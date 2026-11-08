"""Replace people's names with opaque tokens before text goes to Claude.

What it covers: every TeamMember of the manager (active and soft-deleted),
plus the manager's KnownPerson list (former directs tombstoned at purge,
and anyone else the manager adds: boss, peers, clients). Each person's
name and nicknames expand into variants (full name, every part, runs of
parts, hyphen/underscore/dot joins, initial+surname, name inside an
email local part). Matching ignores accents and curly apostrophes.

Tokens look like ⟦P3⟧: they cannot occur in normal text, so restoring
them cannot corrupt "1:1" or "Direct-12". Numbering is shuffled per
request with `secrets`, so the same person is not the same token across
calls (no cross-request linkage on the provider side). The map lives
only in memory for one call.

Pseudonymised text is still personal data: context (role, dates, events)
can identify people, and misspellings, initials and unlisted nicknames
are not caught. This reduces exposure; it does not anonymise.
"""

import contextvars
import re
import secrets
import unicodedata
from pathlib import Path

TOKEN_RE = re.compile(r"⟦P\d+⟧")

# Joining words in multi-part names; never matched on their own.
_PARTICLES = frozenset({
    "de", "la", "le", "del", "della", "der", "den", "van", "von", "da",
    "di", "du", "dos", "das", "do", "el", "bin", "ibn", "bint", "st",
    "mc", "jr", "sr", "ii", "iii", "iv",
})
_SEP = r"[\s_.\-]+"
_LEFT = r"(?<![^\W_])"    # not preceded by a letter/digit (underscore counts as a break)
_RIGHT = r"(?![^\W_])"    # not followed by a letter/digit
_APOSTROPHES = {"’": "'", "‘": "'", "ʼ": "'", "`": "'"}


def _load_common_words():
    path = Path(__file__).resolve().parent / "data" / "name_words.txt"
    words = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            words.update(w.lower() for w in line.split())
    return frozenset(words)


COMMON_WORDS = _load_common_words()


def _fold_char(ch):
    ch = _APOSTROPHES.get(ch, ch)
    decomposed = unicodedata.normalize("NFKD", ch)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    return base or ch


def fold(text):
    """Accent-strip `text`, keeping case. Returns (folded, index_map)
    where index_map[i] is the index in `text` that folded[i] came from."""
    out, index_map = [], []
    for i, ch in enumerate(text):
        f = _fold_char(ch)
        out.append(f)
        index_map.extend([i] * len(f))
    return "".join(out), index_map


def _atoms(name):
    folded, _ = fold(name or "")
    return [a for a in re.split(r"[\s_.\-]+", folded.strip()) if a]


def _variants(name):
    """Yield (atom_tuple, ambiguous) for one name or nickname."""
    atoms = _atoms(name)
    seen = set()
    for i in range(len(atoms)):
        for j in range(i + 1, len(atoms) + 1):
            run = tuple(atoms[i:j])
            if all(a.lower() in _PARTICLES for a in run):
                continue
            if len(run) == 1 and len(run[0]) < 2:
                continue
            key = tuple(a.lower() for a in run)
            if key in seen:
                continue
            seen.add(key)
            ambiguous = len(run) == 1 and run[0].lower() in COMMON_WORDS
            yield run, ambiguous
    real = [a for a in atoms if a.lower() not in _PARTICLES]
    if len(real) >= 2:
        first, last = real[0], real[-1]
        for joined in (first[0] + last, first + last):
            if len(joined) >= 4 and (joined.lower(),) not in seen:
                seen.add((joined.lower(),))
                yield (joined,), False


def _pattern(run, ambiguous):
    if ambiguous:
        # Capitalised only: "Will" yes, "will"/"WILL" no.
        word = run[0]
        body = re.escape(word[0].upper()) + re.escape(word[1:].lower())
        return re.compile(_LEFT + body + _RIGHT)
    body = _SEP.join(re.escape(a) for a in run)
    return re.compile(_LEFT + body + _RIGHT, re.IGNORECASE)


class Pseudonymizer:
    """One per outbound request. `people` is a list of
    (display_name, [names_and_aliases...])."""

    def __init__(self, people):
        slots = list(range(1, len(people) + 1))
        secrets.SystemRandom().shuffle(slots)
        self._real_by_token = {}
        self._patterns = []   # (regex, token, specificity)
        for (display, names), slot in zip(people, slots):
            token = f"⟦P{slot}⟧"
            self._real_by_token[token] = display
            for name in names:
                for run, ambiguous in _variants(name):
                    spec = sum(len(a) for a in run) + len(run)
                    self._patterns.append((_pattern(run, ambiguous), token, spec))

    def __len__(self):
        return len(self._real_by_token)

    def redact(self, text, escape_tokens=True):
        """Replace names with tokens. `escape_tokens` (the default, for
        raw user text) first turns any literal ⟦ ⟧ into [ ] so a token
        typed into a note, or planted in a forwarded email, cannot be
        restored into someone's real name. The backstop pass over an
        already-tokenised message passes False so it keeps our tokens."""
        if not text:
            return text or ""
        text = str(text)
        if escape_tokens:
            text = text.replace("⟦", "[").replace("⟧", "]")
        if not self._patterns:
            return text
        folded, index_map = fold(text)
        spans = []
        for regex, token, spec in self._patterns:
            for m in regex.finditer(folded):
                if m.end() > m.start():
                    start = index_map[m.start()]
                    end = index_map[m.end() - 1] + 1
                    spans.append((start, end, spec, token))
        if not spans:
            return text
        # Longest/most specific first, then leftmost; drop overlaps.
        spans.sort(key=lambda s: (-(s[1] - s[0]), -s[2], s[0]))
        taken, chosen = [], []
        for start, end, _spec, token in spans:
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue
            taken.append((start, end))
            chosen.append((start, end, token))
        chosen.sort()
        out, pos = [], 0
        for start, end, token in chosen:
            out.append(text[pos:start])
            out.append(token)
            pos = end
        out.append(text[pos:])
        return "".join(out)

    def restore(self, text):
        if not text:
            return text
        return TOKEN_RE.sub(lambda m: self._real_by_token.get(m.group(0), m.group(0)), text)


_active = contextvars.ContextVar("active_pseudonymizer", default=None)


def active():
    """The Pseudonymizer for the AI request being built, or None."""
    return _active.get()


def activate(p):
    return _active.set(p)


def deactivate(token):
    _active.reset(token)


def people_for_manager(manager_id):
    """Everyone whose name must not leave: all TeamMembers (including
    soft-deleted) and every KnownPerson row for this manager."""
    from core.models import KnownPerson, TeamMember

    people = []
    for tm in TeamMember.objects.for_manager(manager_id).only("name", "aliases"):
        people.append((tm.name, [tm.name, *split_aliases(tm.aliases)]))
    for kp in KnownPerson.objects.for_manager(manager_id):
        people.append((kp.name, [kp.name, *split_aliases(kp.aliases)]))
    return people


def split_aliases(raw):
    return [a.strip() for a in (raw or "").split(",") if a.strip()]
