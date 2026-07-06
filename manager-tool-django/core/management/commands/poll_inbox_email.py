"""Poll a Gmail mailbox over IMAP and land new messages in the Inbox.

Roadmap PR 5 (email-in capture). Wired to a Render Cron every 15
minutes; structure mirrors send_weekly_digests (--dry-run, per-manager
loop). All stdlib: imaplib + email. Host/port are hardcoded to Gmail —
the capture mailbox decision is Gmail-only (M365 retired IMAP basic
auth), so there is deliberately no config plumbing for a value that
can never vary.

Safety properties (each has a test):
- Sender allowlist: only mail whose From: address is in
  `inbox_allowed_senders` (default: the manager_email config) creates
  an item. Everything else is marked seen and dropped — the mailbox
  address is not a secret, the allowlist is the gate. (From: spoofing
  is an ACCEPTED personal-tool risk per the roadmap decision record;
  a subject token is the escalation path if it ever matters.)
- Poison-message isolation: every message is processed inside its own
  try/except. A malformed email logs, lands as a VISIBLE
  InboxItem(status='failed') with a raw excerpt, and the loop
  continues — one bad email can never block the queue. Failed items
  keep their Message-ID when one exists so a re-fetched poison
  message dedupes instead of duplicating.
- Dedupe: get_or_create on (manager_id, message_id) — re-fetching a
  message that carries a Message-ID (flag loss, overlapping runs)
  cannot create duplicates. Messages with no Message-ID header have
  nothing to dedupe on and are simply created.
- \\Seen only after the DB write: messages are fetched with BODY.PEEK[]
  (which does NOT implicitly set \\Seen), and the flag is stored only
  after the item is committed, so a crash mid-run re-presents the
  message on the next run instead of losing it. A store() failure
  after a successful capture is logged and absorbed (the item is
  already committed; dedupe absorbs the re-fetch) — it must never be
  mistaken for a poison message.
- Blast-radius bounds: a connection-level fetch failure stops that
  mailbox's batch (unseen mail retries next run) without inventing
  failed items, and any unexpected per-manager crash is caught in
  handle() so one manager's failure can't skip the managers after it.
"""

import imaplib
import logging
from datetime import timezone as dt_timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Config, InboxItem, Manager
from core.services.config import get_config

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

SUBJECT_MAX = 500
BODY_MAX = 20_000
RAW_EXCERPT_MAX = 2_000
# Bound the work the HTML stripper can be handed — a pathological
# multi-megabyte HTML body should not be fully tokenized every poll.
HTML_SRC_MAX = 200_000


class _HTMLToText(HTMLParser):
    """Minimal, hostile-input-safe HTML→text: drops script/style
    contents, breaks lines on block-level closes and <br>. The digest's
    regex stripper was written for trusted self-authored HTML; email
    bodies are untrusted, so this uses the real stdlib parser.
    convert_charrefs=True unescapes entities into handle_data for us.

    `keep_all=True` disables the script/style drop — used as the
    second pass for documents with an UNCLOSED <script>/<style>, where
    HTMLParser's CDATA mode would otherwise classify the entire rest
    of the document as script content and silently discard it. Ugly
    visible text beats silently losing the tail of a captured note
    (templates escape everything, so keeping it is XSS-safe).
    """

    _BLOCK_ENDS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4",
                   "blockquote", "table"}

    def __init__(self, keep_all=False):
        super().__init__(convert_charrefs=True)
        self._chunks = []
        self._skip_depth = 0
        self._keep_all = keep_all
        if keep_all:
            # Disable CDATA mode entirely: with an UNCLOSED <script>,
            # goahead() hits `if self.cdata_elem: break` and withholds
            # the buffered tail forever — no handler ever sees it. With
            # CDATA off, script content parses as normal markup and
            # every text node reaches handle_data. (Checked via `self`,
            # so an instance attribute overrides the class tuple.)
            self.CDATA_CONTENT_ELEMENTS = ()

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style") and not self._keep_all:
            self._skip_depth += 1
        elif tag == "br":
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and not self._keep_all:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK_ENDS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._chunks.append(data)

    @property
    def unclosed_skip(self):
        return self._skip_depth > 0

    def text(self):
        raw = "".join(self._chunks)
        # Collapse runs of blank lines left behind by nested blocks.
        lines = [ln.strip() for ln in raw.splitlines()]
        out, blank = [], False
        for ln in lines:
            if ln:
                out.append(ln)
                blank = False
            elif not blank:
                out.append("")
                blank = True
        return "\n".join(out).strip()


def _strip_html(html_src):
    parser = _HTMLToText()
    parser.feed(html_src)
    parser.close()
    if parser.unclosed_skip:
        # An unclosed <script>/<style> put the parser in CDATA mode and
        # swallowed everything after it. Re-run keeping all text so the
        # tail of the message stays VISIBLE rather than silently lost.
        logger.warning(
            "HTML body has an unclosed script/style tag; keeping raw "
            "text so no content is silently dropped"
        )
        parser = _HTMLToText(keep_all=True)
        parser.feed(html_src)
        parser.close()
    return parser.text()


def _decode_subject(msg):
    raw = msg.get("Subject", "")
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))[:SUBJECT_MAX]
    except Exception:
        # RFC 2047 garbage in the header — keep the raw form rather
        # than poisoning the whole message over a subject line.
        logger.warning("Undecodable Subject header; storing raw form")
        return raw[:SUBJECT_MAX]


def _sender_of(msg):
    """Lowercased From: address, robust to unquoted commas in display
    names ('Erickson, Todd <t@x.com>' makes parseaddr return ('','')).
    getaddresses splits the comma-list; take the first real address.
    No address at all → '' → the caller rejects (fail closed)."""
    for _name, addr in getaddresses([msg.get("From", "")]):
        if "@" in addr:
            return addr.strip().lower()
    return ""


def _extract_body(msg):
    """Prefer the first text/plain part; fall back to stripped
    text/html. Charset comes from the part header — an unknown charset
    raises (LookupError) and is handled by the caller's poison path so
    the failure is VISIBLE in the inbox, not silently mangled."""
    plain, html_part = None, None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():  # attachment, not body
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = part
        elif ctype == "text/html" and html_part is None:
            html_part = part
    part = plain if plain is not None else html_part
    if part is None:
        return ""
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    text = payload.decode(charset, errors="replace")
    if part is html_part:
        text = _strip_html(text[:HTML_SRC_MAX])
    return text[:BODY_MAX]


def _received_at(msg):
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        return timezone.now()
    if dt.tzinfo is None:  # RFC allows tz-less dates; assume UTC
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def _allowlist_for(manager_id):
    """Lowercased set of allowed sender addresses. Falls back to the
    manager_email config so a fresh setup works with zero extra
    fields. Empty set == reject everything (fail closed)."""
    raw = get_config("inbox_allowed_senders", manager_id, default="") or ""
    addrs = {a.strip().lower() for a in raw.replace("\n", ",").split(",")
             if a.strip()}
    if not addrs:
        fallback = get_config("manager_email", manager_id, default="") or ""
        if fallback.strip():
            addrs = {fallback.strip().lower()}
    return addrs


def _stamp_last_poll(manager_id, outcome):
    """Write the last-poll timestamp+outcome shown on the Settings
    page. Direct upsert, NOT set_config: this changes every 15 minutes
    and set_config would write ~96 AuditLog rows/day of pure noise."""
    Config.objects.update_or_create(
        manager_id=manager_id,
        key="inbox_last_poll",
        defaults={"value": f"{timezone.now().isoformat()} — {outcome}"},
    )


class Command(BaseCommand):
    help = "Poll the configured Gmail mailbox over IMAP into the Inbox."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Connect and report the unseen count without writing "
                 "items or marking anything seen.",
        )
        parser.add_argument(
            "--manager-id",
            type=int,
            help="Poll a single manager's mailbox (for testing).",
        )

    def handle(self, *args, **opts):
        if opts["manager_id"]:
            managers = Manager.objects.filter(id=opts["manager_id"])
        else:
            configured_ids = (
                Config.objects.filter(key="inbox_imap_user")
                .exclude(value__isnull=True)
                .exclude(value="")
                .values_list("manager_id", flat=True)
            )
            managers = Manager.objects.filter(id__in=configured_ids)

        for manager in managers:
            try:
                self._poll_manager(manager, dry_run=opts["dry_run"])
            except Exception:
                # Blast-radius bound: an unexpected failure for one
                # manager (e.g. a transient DB error) must not skip the
                # managers after it in the run.
                logger.exception("Inbox poll crashed for manager %d",
                                 manager.id)
                self.stdout.write(self.style.ERROR(
                    f"Manager {manager.id}: poll crashed (see logs); "
                    "continuing with remaining managers"
                ))

    # ------------------------------------------------------------------
    def _mark_seen(self, conn, num, manager_id):
        """Guarded \\Seen store. A failure here after a successful
        capture must NOT look like a poison message — the item is
        already committed and dedupe absorbs the re-fetch."""
        try:
            conn.store(num, "+FLAGS", "\\Seen")
        except (imaplib.IMAP4.error, OSError):
            logger.warning(
                "Could not mark message seen for manager %d (connection "
                "dropped?) — item already committed; dedupe will absorb "
                "the re-fetch", manager_id,
            )

    def _poll_manager(self, manager, *, dry_run):
        user = get_config("inbox_imap_user", manager.id)
        password = get_config("inbox_imap_password", manager.id)
        if not user or not password:
            # get_config already ERROR-logs a set-but-undecryptable key
            # (rotated CONFIG_ENCRYPTION_KEY); this covers plain not-set.
            self.stdout.write(self.style.WARNING(
                f"Manager {manager.id}: IMAP user/password not configured "
                "— skipping"
            ))
            if user and not dry_run:
                # Email-in was ENABLED (user set) but can't run — the
                # Settings page must not freeze on a stale healthy
                # stamp while polls silently skip.
                _stamp_last_poll(
                    manager.id,
                    "error: IMAP password missing or undecryptable "
                    "(set it in Settings; if it was set, check "
                    "CONFIG_ENCRYPTION_KEY)",
                )
            return

        try:
            conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            try:
                conn.login(user, password)
                conn.select("INBOX")
                _typ, data = conn.search(None, "UNSEEN")
                nums = data[0].split() if data and data[0] else []

                if dry_run:
                    self.stdout.write(
                        f"DRY-RUN: manager {manager.id}: {len(nums)} unseen "
                        "message(s); no items written, no flags changed"
                    )
                    return

                counts = self._process_messages(conn, manager, nums)
                outcome = (
                    f"ok: {counts['created']} new, "
                    f"{counts['duplicate']} duplicate, "
                    f"{counts['rejected']} rejected, "
                    f"{counts['failed']} failed"
                )
                self.stdout.write(self.style.SUCCESS(
                    f"Manager {manager.id}: {outcome}"
                ))
            finally:
                try:
                    conn.logout()
                except (imaplib.IMAP4.error, OSError):
                    logger.warning("IMAP logout failed for manager %d "
                                   "(poll already completed)", manager.id)
        except (imaplib.IMAP4.error, OSError) as exc:
            # Connection/auth-level failure: loud in logs AND on the
            # settings page via the outcome stamp — never a silent no-op.
            logger.error("Inbox poll failed for manager %d: %s",
                         manager.id, exc)
            self.stdout.write(self.style.ERROR(
                f"Manager {manager.id}: poll failed: {exc}"
            ))
            if not dry_run:
                _stamp_last_poll(manager.id, f"error: {exc}")
            return

        _stamp_last_poll(manager.id, outcome)

    # ------------------------------------------------------------------
    def _process_messages(self, conn, manager, nums):
        allow = _allowlist_for(manager.id)
        if not allow:
            logger.warning(
                "Manager %d has no inbox_allowed_senders and no "
                "manager_email — rejecting all inbound mail", manager.id,
            )
        counts = {"created": 0, "duplicate": 0, "rejected": 0, "failed": 0}

        for num in nums:
            # BODY.PEEK[] so nothing is implicitly marked \Seen before
            # the DB write commits. A fetch failure is a CONNECTION
            # problem, not a message problem: stop this mailbox's batch
            # (unseen mail retries next run) without inventing failed
            # items.
            try:
                _typ, data = conn.fetch(num, "(BODY.PEEK[])")
            except (imaplib.IMAP4.error, OSError) as exc:
                logger.error(
                    "IMAP fetch failed mid-batch for manager %d (%s) — "
                    "stopping this run; unseen messages retry next poll",
                    manager.id, exc,
                )
                break
            raw = data[0][1] if data and data[0] else b""

            message_id = None
            try:
                msg = message_from_bytes(raw)
                # Extract the dedupe key BEFORE body parsing so the
                # poison path below can reuse it — a re-fetched poison
                # message must dedupe, not duplicate.
                message_id = (msg.get("Message-ID") or "").strip() or None
                sender = _sender_of(msg)
                if not sender or sender not in allow:
                    counts["rejected"] += 1
                else:
                    fields = {
                        "source": "email",
                        "subject": _decode_subject(msg),
                        "body": _extract_body(msg),
                        "from_address": sender,
                        "received_at": _received_at(msg),
                        "status": "pending",
                    }
                    created = self._store_item(manager.id, message_id,
                                               fields)
                    counts["created" if created else "duplicate"] += 1
            except Exception:
                # Poison-message isolation: the failure must be VISIBLE
                # in the inbox UI, not just in Sentry, and must never
                # block the rest of the queue.
                logger.exception(
                    "Unparseable inbound email for manager %d", manager.id,
                )
                excerpt = raw[:RAW_EXCERPT_MAX].decode(
                    "latin-1", errors="replace",
                )
                self._store_item(manager.id, message_id, {
                    "source": "email",
                    "subject": "(unparseable email)",
                    "body": excerpt or "(empty message)",
                    "status": "failed",
                })
                counts["failed"] += 1

            # Flag only after the row is committed (autocommit) so a
            # crash between fetch and write re-presents the message.
            # Guarded: a store failure here is a connection hiccup, not
            # a poison message.
            self._mark_seen(conn, num, manager.id)

        return counts

    @staticmethod
    def _store_item(manager_id, message_id, fields):
        """Create an InboxItem, deduped on (manager_id, message_id)
        when a Message-ID exists (matches uq_inbox_manager_message_id).
        Returns True if a new row was created."""
        if message_id:
            _item, created = InboxItem.objects.get_or_create(
                manager_id=manager_id,
                message_id=message_id,
                defaults=fields,
            )
            return created
        InboxItem.objects.create(manager_id=manager_id, **fields)
        return True
