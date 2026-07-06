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
  address is not a secret, the allowlist is the gate.
- Poison-message isolation: every message is processed inside its own
  try/except. A malformed email logs, lands as a VISIBLE
  InboxItem(status='failed') with a raw excerpt, is marked seen, and
  the loop continues — one bad email can never block the queue.
- Dedupe: get_or_create on (manager_id, message_id) — re-fetching a
  message (flag loss, overlapping runs) cannot create duplicates.
- \\Seen only after the DB write: messages are fetched with BODY.PEEK[]
  (which does NOT implicitly set \\Seen), and the flag is stored only
  after the item is committed, so a crash mid-run re-presents the
  message on the next run instead of losing it.
"""

import imaplib
import logging
from datetime import timezone as dt_timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
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


class _HTMLToText(HTMLParser):
    """Minimal, hostile-input-safe HTML→text: drops script/style
    contents, breaks lines on block-level closes and <br>. The digest's
    regex stripper was written for trusted self-authored HTML; email
    bodies are untrusted, so this uses the real stdlib parser.
    convert_charrefs=True unescapes entities into handle_data for us.
    """

    _BLOCK_ENDS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4",
                   "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag == "br":
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK_ENDS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._chunks.append(data)

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
        text = _strip_html(text)
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
            self._poll_manager(manager, dry_run=opts["dry_run"])

    # ------------------------------------------------------------------
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
            # the DB write commits.
            _typ, data = conn.fetch(num, "(BODY.PEEK[])")
            raw = data[0][1] if data and data[0] else b""
            try:
                msg = message_from_bytes(raw)
                sender = parseaddr(msg.get("From", ""))[1].strip().lower()
                if not sender or sender not in allow:
                    counts["rejected"] += 1
                    conn.store(num, "+FLAGS", "\\Seen")
                    continue

                message_id = (msg.get("Message-ID") or "").strip() or None
                fields = {
                    "source": "email",
                    "subject": _decode_subject(msg),
                    "body": _extract_body(msg),
                    "from_address": sender,
                    "received_at": _received_at(msg),
                    "status": "pending",
                }
                if message_id:
                    _item, created = InboxItem.objects.get_or_create(
                        manager_id=manager.id,
                        message_id=message_id,
                        defaults=fields,
                    )
                else:
                    # No Message-ID header: nothing to dedupe on.
                    InboxItem.objects.create(
                        manager_id=manager.id, **fields,
                    )
                    created = True
                counts["created" if created else "duplicate"] += 1
                # Flag only after the row is committed (autocommit) so a
                # crash between fetch and write re-presents the message.
                conn.store(num, "+FLAGS", "\\Seen")
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
                InboxItem.objects.create(
                    manager_id=manager.id,
                    source="email",
                    subject="(unparseable email)",
                    body=excerpt or "(empty message)",
                    status="failed",
                )
                counts["failed"] += 1
                conn.store(num, "+FLAGS", "\\Seen")

        return counts
