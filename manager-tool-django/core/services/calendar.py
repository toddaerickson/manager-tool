"""Calendar invitation service (Django port of calendar_service.py).

Generates iCalendar (.ics) content and sends RFC 5545 SMTP calendar
invites. Preserves the M3 sanitization guards (control-char stripping,
ICS-escape, header-injection prevention) from the Streamlit version.

Phase 6: used by the 1-on-1 invite flow (Option C from D2 contract).
"""

import logging
import re
import uuid
from datetime import datetime, timedelta
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from core.services.email import get_smtp_settings, send_smtp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Header- and ICS-injection guards (P3.3 / AUDIT M3)
# ---------------------------------------------------------------------------

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _strip_control_chars(text):
    """Remove every C0 control character plus DEL."""
    if text is None:
        return ""
    return _CONTROL_CHARS_RE.sub("", str(text))


def _safe_header_text(text, max_len=200):
    """Sanitize a string for use as an email header value."""
    return _strip_control_chars(text)[:max_len]


def _ics_cn_value(name):
    """Sanitize a name for use inside a quoted ICS CN parameter.
    Must not contain CR, LF, other control chars, or a literal
    double quote (AUDIT M3)."""
    if not name:
        return ""
    return _strip_control_chars(name).replace('"', "")


def _safe_address_pair(name, address):
    """Build an RFC 5322 address with injection protection."""
    clean_name = _strip_control_chars(name or "")
    _, clean_addr = parseaddr(_strip_control_chars(address or ""))
    if not clean_addr:
        return ""
    return formataddr((clean_name, clean_addr)) if clean_name else clean_addr


def _ics_escape(text):
    """RFC 5545 TEXT escape with control-char stripping (AUDIT M3)."""
    if not text:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;")
    s = s.replace(",", "\\,")
    s = s.replace("\n", "\\n")
    return _CONTROL_CHARS_RE.sub("", s)


# ---------------------------------------------------------------------------
# ICS generation
# ---------------------------------------------------------------------------

EVENT_TYPE_LABELS = {
    "check_in": "Weekly Check-In",
    "coaching": "Coaching Session",
    "one_on_one": "1-on-1 Meeting",
    "quarterly_review": "Quarterly Review",
    "other": "Meeting",
}

# App recurrence rule -> RFC 5545 FREQ clause (roadmap PR 10). COUNT
# comes from RECURRENCE_COUNTS at call time — single source of truth
# with the materializer; never hardcode counts here.
_RRULE_FREQ = {
    "weekly": "FREQ=WEEKLY",
    "monthly": "FREQ=MONTHLY",
    "quarterly": "FREQ=MONTHLY;INTERVAL=3",
}


def rrule_for_rule(rule):
    """RFC 5545 RRULE value for an app recurrence rule, or None when the
    rule is unknown (callers fall back to a single-occurrence invite).

    Documented caveat: RFC 5545 FREQ=MONTHLY on day 29-31 SKIPS months
    without that day, while the app's materializer clamps to month-end
    (add_months_anchored). The app's Event rows stay the source of
    truth; the invite is a convenience mirror."""
    from core.services.events import RECURRENCE_COUNTS

    freq = _RRULE_FREQ.get(rule)
    if freq is None:
        return None
    return f"{freq};COUNT={RECURRENCE_COUNTS[rule]}"


def generate_ics(event, organizer_name=None, organizer_email=None,
                 attendee_name=None, attendee_email=None, rrule=None):
    """Generate an RFC 5545 iCalendar string for a single event.

    `event` can be an Event model instance or a dict with keys:
    scheduled_date, scheduled_time, duration_minutes, title,
    event_type, location, agenda, notes.

    `rrule`, when set (e.g. "FREQ=WEEKLY;COUNT=12"), emits one
    recurring VEVENT instead of a single occurrence.
    """
    uid = f"{uuid.uuid4()}@manager-tool"

    # Support both model instances and dicts
    def _get(field, default=""):
        if isinstance(event, dict):
            return event.get(field, default)
        return getattr(event, field, default) or default

    date_str = _get("scheduled_date", "")
    time_str = _get("scheduled_time", "00:00")
    try:
        dt_start = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        dt_start = datetime.now()
    dt_end = dt_start + timedelta(minutes=int(_get("duration_minutes", 30) or 30))

    fmt = "%Y%m%dT%H%M%S"
    dtstart = dt_start.strftime(fmt)
    dtend = dt_end.strftime(fmt)
    dtstamp = datetime.utcnow().strftime(fmt) + "Z"

    summary = _ics_escape(_get("title", "Manager Meeting"))
    location = _ics_escape(_get("location", ""))

    description_parts = []
    description_parts.append(
        f"Type: {EVENT_TYPE_LABELS.get(_get('event_type', ''), 'Meeting')}"
    )
    if _get("agenda"):
        description_parts.append(f"\\nAgenda:\\n{_ics_escape(_get('agenda'))}")
    if _get("notes"):
        description_parts.append(f"\\nNotes:\\n{_ics_escape(_get('notes'))}")
    description = "\\n".join(description_parts)

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Manager Task Generator//EN",
        "METHOD:REQUEST", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}", f"DTEND:{dtend}", f"SUMMARY:{summary}",
    ]
    if rrule:
        # rrule is server-built (rrule_for_rule), never user text, so no
        # escaping — but strip control chars as defense in depth.
        lines.append(f"RRULE:{_strip_control_chars(rrule)}")
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    if organizer_email:
        clean_org_email = _strip_control_chars(organizer_email)
        clean_org_name = _ics_cn_value(organizer_name)
        org_cn = f';CN="{clean_org_name}"' if clean_org_name else ""
        lines.append(f"ORGANIZER{org_cn}:mailto:{clean_org_email}")
    if attendee_email:
        clean_att_email = _strip_control_chars(attendee_email)
        clean_att_name = _ics_cn_value(attendee_name)
        att_cn = f';CN="{clean_att_name}"' if clean_att_name else ""
        lines.append(
            f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE{att_cn}"
            f":mailto:{clean_att_email}"
        )
    if organizer_email:
        clean_org_email = _strip_control_chars(organizer_email)
        clean_org_name = _ics_cn_value(organizer_name)
        org_cn2 = f';CN="{clean_org_name}"' if clean_org_name else ""
        lines.append(
            f"ATTENDEE;PARTSTAT=ACCEPTED{org_cn2}:mailto:{clean_org_email}"
        )
    lines += [
        "STATUS:CONFIRMED", "SEQUENCE:0",
        "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY",
        "DESCRIPTION:Reminder", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines)


def send_calendar_invite(event, recipient_email, recipient_name=None,
                         manager_id=None, rrule=None):
    """Send an RFC 5545 calendar invite for an event via SMTP.

    `rrule`, when set, sends ONE recurring invite (series parents —
    roadmap PR 10) instead of a single occurrence.

    Returns (success: bool, message: str).
    """
    if manager_id is None:
        if isinstance(event, dict):
            manager_id = event.get("manager_id")
        else:
            manager_id = getattr(event, "manager_id", None)

    smtp_cfg = get_smtp_settings(manager_id)
    if smtp_cfg is None:
        return False, "SMTP not configured. Set up email in Settings."

    ics_content = generate_ics(
        event,
        organizer_name=smtp_cfg["name"],
        organizer_email=smtp_cfg["email"],
        attendee_name=recipient_name,
        attendee_email=recipient_email,
        rrule=rrule,
    )

    msg = MIMEMultipart("mixed")
    msg["From"] = _safe_address_pair(smtp_cfg["name"], smtp_cfg["email"])
    msg["To"] = _safe_address_pair(recipient_name, recipient_email)

    title = event.get("title", "Meeting") if isinstance(event, dict) else (event.title or "Meeting")
    msg["Subject"] = Header(
        _safe_header_text(f"Calendar Invite: {title}"), "utf-8",
    )

    event_type = event.get("event_type", "") if isinstance(event, dict) else (event.event_type or "")
    body = (
        f"You're invited to: {title}\n\n"
        f"Type: {EVENT_TYPE_LABELS.get(event_type, 'Meeting')}\n"
    )
    scheduled_date = event.get("scheduled_date", "") if isinstance(event, dict) else event.scheduled_date
    scheduled_time = event.get("scheduled_time", "") if isinstance(event, dict) else event.scheduled_time
    duration = event.get("duration_minutes", 30) if isinstance(event, dict) else event.duration_minutes
    body += f"Date: {scheduled_date}\nTime: {scheduled_time}\n"
    body += f"Duration: {duration or 30} minutes\n"
    if rrule:
        body += f"Recurring: {rrule}\n"
    location = event.get("location", "") if isinstance(event, dict) else (event.location or "")
    if location:
        body += f"Location: {location}\n"
    agenda = event.get("agenda", "") if isinstance(event, dict) else (event.agenda or "")
    if agenda:
        body += f"\nAgenda:\n{agenda}\n"
    body += (
        "\nPlease open the attached .ics file or accept this "
        "invitation to add it to your calendar."
    )
    msg.attach(MIMEText(body, "plain"))

    cal_part = MIMEBase("text", "calendar", method="REQUEST")
    cal_part.set_payload(ics_content.encode("utf-8"))
    encoders.encode_base64(cal_part)
    cal_part.add_header(
        "Content-Disposition", "attachment", filename="invite.ics",
    )
    msg.attach(cal_part)

    return send_smtp(smtp_cfg, msg)
