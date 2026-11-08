"""Replace people's names with opaque tokens before text goes to Claude.

What it covers: every TeamMember of the manager (active and soft-deleted),
plus the manager's KnownPerson list (former directs tombstoned at purge,
and anyone else the manager adds: boss, peers, clients). Each person's
name and nicknames expand into variants (full name, every part, runs of
parts, hyphen/underscore/dot joins, initial+surname, name inside an
email local part). Matching ignores accents and curly apostrophes.

Tokens look like ⟦P3⟧ (one person) or ⟦A1⟧ (a name part several
people share; restored to the words originally written). They cannot
occur in normal text, so restoring them cannot corrupt "1:1" or
"Direct-12". Numbering is shuffled per request with `secrets`, so the
same person is not the same token across calls (no cross-request
linkage on the provider side). Only tokens emitted in this request are
restored. The map lives only in memory for one call.

Known gaps (documented, not fixed): lowercase/ALL-CAPS uses of names
that are common words ("will", "WILL"); names inside unspaced CJK text;
tokens the model rewrites ("[P3]") stay visible as written.

Pseudonymised text is still personal data: context (role, dates, events)
can identify people, and misspellings, initials and unlisted nicknames
are not caught. This reduces exposure; it does not anonymise.
"""

import contextvars
import re
import secrets
import unicodedata
from pathlib import Path

TOKEN_RE = re.compile(r"⟦[PA]\d+⟧")

# Joining words in multi-part names; never matched on their own.
_PARTICLES = frozenset({
    "de", "la", "le", "del", "della", "der", "den", "van", "von", "da",
    "di", "du", "dos", "das", "do", "el", "bin", "ibn", "bint", "st",
    "mc", "jr", "sr", "ii", "iii", "iv",
})
# Parts may be joined by whitespace, "_", "-", or a "." that is NOT
# followed by whitespace ("sarah.chen" yes; "June. Park" is two sentences).
_SEP = r"(?:[\s_\-]|\.(?!\s))+"
_LEFT = r"(?<![^\W_])"    # not preceded by a letter/digit (underscore counts as a break)
_RIGHT = r"(?![^\W_])"    # not followed by a letter/digit
_APOSTROPHES = {"’": "'", "‘": "'", "ʼ": "'", "`": "'"}
# Letters NFKD does not decompose into a base letter.
_EXTRA_FOLDS = {
    "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D",
    "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D", "ı": "i", "ħ": "h", "Ħ": "H",
}

# Pattern kinds
_ANY_CASE = "any"      # matched case-insensitively
_CAPITALISED = "cap"   # matched only as "Will" (common word or particle)
_HANDLE = "handle"     # "schen", "sarahchen": only touching "@" (email/handle)


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
    if ch in _EXTRA_FOLDS:
        return _EXTRA_FOLDS[ch]
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
    """Yield (atom_tuple, kind) for one name or nickname."""
    atoms = _atoms(name)
    n = len(atoms)
    seen = set()
    for i in range(n):
        for j in range(i + 1, n + 1):
            run = tuple(atoms[i:j])
            if len(run) == 1 and len(run[0]) < 2:
                continue
            kind = _ANY_CASE
            if all(a.lower() in _PARTICLES for a in run):
                # "de la" alone is never a name, but "Le" in "Thanh Le",
                # "Van" in "Van Nguyen", or a nickname "Di" is: a single
                # particle at either end (or the whole name) still counts,
                # capitalised only because it is also an ordinary word.
                if not (len(run) == 1 and (i == 0 or j == n)):
                    continue
                kind = _CAPITALISED
            elif len(run) == 1 and run[0].lower() in COMMON_WORDS:
                kind = _CAPITALISED
            key = tuple(a.lower() for a in run)
            if key in seen:
                continue
            seen.add(key)
            yield run, kind
    real = [a for a in atoms if a.lower() not in _PARTICLES]
    if len(real) >= 2:
        first, last = real[0], real[-1]
        for joined in (first[0] + last, first + last):
            if len(joined) >= 4:
                yield (joined,), _HANDLE


def _pattern(run, kind):
    if kind == _CAPITALISED:
        word = run[0]
        body = re.escape(word[0].upper()) + re.escape(word[1:].lower())
        return re.compile(_LEFT + body + _RIGHT)
    body = _SEP.join(re.escape(a) for a in run)
    if kind == _HANDLE:
        # Only as an email local part ("schen@") or a handle ("@schen"):
        # bare "james" for "Jane Ames" must not swallow a real word/name.
        return re.compile(
            _LEFT + r"(?:(?<=@)" + body + "|" + body + r"(?=@))" + _RIGHT,
            re.IGNORECASE,
        )
    return re.compile(_LEFT + body + _RIGHT, re.IGNORECASE)


_KIND_RANK = {_ANY_CASE: 0, _CAPITALISED: 0, _HANDLE: 1}


class Pseudonymizer:
    """One per outbound request. `people` is a list of
    (display_name, [names_and_aliases...]).

    A variant owned by exactly one person maps to that person's ⟦Pn⟧.
    A variant shared by several people ("Sarah" for Sarah Lee and Sarah
    Kim) maps to a shared ⟦An⟧ that restores to the words originally
    written, so the reply never pins a bare "Sarah" on the wrong Sarah.
    `restore` only replaces tokens this instance emitted, so a token the
    model copies or invents is never turned into some other real person.
    """

    def __init__(self, people):
        slots = list(range(1, len(people) + 1))
        secrets.SystemRandom().shuffle(slots)
        self._display_by_token = {}
        owners = {}   # (kind, atoms_lower) -> {token}
        runs = {}     # (kind, atoms_lower) -> run
        for (display, names), slot in zip(people, slots):
            token = f"⟦P{slot}⟧"
            self._display_by_token[token] = display
            for name in names:
                for run, kind in _variants(name):
                    key = (kind, tuple(a.lower() for a in run))
                    owners.setdefault(key, set()).add(token)
                    runs.setdefault(key, run)
        self._patterns = []   # (regex, owner_token_or_None, specificity, rank)
        for key, tokens in owners.items():
            kind = key[0]
            run = runs[key]
            spec = sum(len(a) for a in run) + len(run)
            owner = next(iter(tokens)) if len(tokens) == 1 else None
            self._patterns.append((_pattern(run, kind), owner, spec, _KIND_RANK[kind]))
        self._emitted = {}        # token -> text restore() puts back
        self._shared_by_surface = {}

    def __len__(self):
        return len(self._display_by_token)

    def _shared_token(self, surface):
        key = fold(surface)[0].lower()
        token = self._shared_by_surface.get(key)
        if token is None:
            token = f"⟦A{len(self._shared_by_surface) + 1}⟧"
            self._shared_by_surface[key] = token
            self._emitted[token] = surface
        return token

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
        for regex, owner, spec, rank in self._patterns:
            for m in regex.finditer(folded):
                if m.end() > m.start():
                    start = index_map[m.start()]
                    end = index_map[m.end() - 1] + 1
                    spans.append((start, end, spec, rank, owner))
        if not spans:
            return text
        # Longest span first, then most specific, then plain name over
        # handle form, then leftmost; drop overlaps.
        spans.sort(key=lambda s: (-(s[1] - s[0]), -s[2], s[3], s[0]))
        taken, chosen = [], []
        for start, end, _spec, _rank, owner in spans:
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue
            taken.append((start, end))
            chosen.append((start, end, owner))
        chosen.sort()
        out, pos = [], 0
        for start, end, owner in chosen:
            out.append(text[pos:start])
            if owner is None:
                out.append(self._shared_token(text[start:end]))
            else:
                self._emitted[owner] = self._display_by_token[owner]
                out.append(owner)
            pos = end
        out.append(text[pos:])
        return "".join(out)

    def restore(self, text):
        if not text:
            return text
        return TOKEN_RE.sub(lambda m: self._emitted.get(m.group(0), m.group(0)), text)


def trim_partial_token(text):
    """Drop a token cut in half by a length cap ("...⟦P1"), so the model
    never sees a dangling fragment it might 'complete' into a guess."""
    i = text.rfind("⟦")
    if i != -1 and "⟧" not in text[i:]:
        return text[:i]
    return text


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
