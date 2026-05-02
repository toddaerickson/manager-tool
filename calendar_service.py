"""
Calendar invitation service.
Generates iCalendar (.ics) files and sends them via SMTP email
so recipients can accept and add events to Google Calendar.
"""

import re
import smtplib
import uuid
import os
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr, parseaddr
from email import encoders

from database import get_config


# ---------------------------------------------------------------------------
# Header- and ICS-injection guards (P3.3 / AUDIT M3)
# ---------------------------------------------------------------------------

# Any control character (CR, LF, NUL, vertical tab, etc.) in a header or
# ICS field is suspect — those are the injection vectors. Strip them all.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _strip_control_chars(text):
    """Remove every C0 control character plus DEL. ICS injection (extra
    VEVENT lines) and email-header injection (extra Bcc/Subject) both
    rely on slipping CR/LF into a value."""
    if text is None:
        return ""
    return _CONTROL_CHARS_RE.sub("", str(text))


def _safe_header_text(text, max_len=200):
    """Sanitize a string for use as the *value* of an email header.
    Strips control characters and caps length."""
    return _strip_control_chars(text)[:max_len]


def _ics_cn_value(name):
    """Sanitize a name for use inside a quoted ICS CN parameter (e.g.
    `ORGANIZER;CN="Boss":mailto:boss@x.com`). Must not contain CR, LF,
    other control chars, or a literal double quote — any of which would
    let an attacker break out of the quoted value and forge new ICS
    properties (AUDIT M3)."""
    if not name:
        return ""
    return _strip_control_chars(name).replace('"', "")


def _safe_address_pair(name, address):
    """Build an RFC 5322 address. parseaddr re-parses to drop any embedded
    headers (`name <foo>\\nBcc: x@y`); formataddr quotes the name correctly.
    Falls back to bare-address if name is empty after sanitization."""
    clean_name = _strip_control_chars(name or "")
    # parseaddr ignores anything after a CR/LF, but we strip them already.
    _, clean_addr = parseaddr(_strip_control_chars(address or ""))
    if not clean_addr:
        return ""
    return formataddr((clean_name, clean_addr)) if clean_name else clean_addr


def generate_ics(event, organizer_name=None, organizer_email=None,
                 attendee_name=None, attendee_email=None):
    uid = f"{uuid.uuid4()}@manager-tool"
    date_str = event.get("scheduled_date", "")
    time_str = event.get("scheduled_time", "00:00")
    try:
        dt_start = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        dt_start = datetime.now()
    dt_end = dt_start + timedelta(minutes=event.get("duration_minutes", 30))

    fmt = "%Y%m%dT%H%M%S"
    dtstart = dt_start.strftime(fmt)
    dtend = dt_end.strftime(fmt)
    dtstamp = datetime.utcnow().strftime(fmt) + "Z"

    summary = _ics_escape(event.get("title", "Manager Meeting"))
    location = _ics_escape(event.get("location", ""))

    description_parts = []
    event_type_labels = {
        "check_in": "Weekly Check-In", "coaching": "Coaching Session",
        "one_on_one": "1-on-1 Meeting", "quarterly_review": "Quarterly Review",
        "other": "Meeting",
    }
    description_parts.append(
        f"Type: {event_type_labels.get(event.get('event_type', ''), 'Meeting')}"
    )
    if event.get("agenda"):
        description_parts.append(f"\\nAgenda:\\n{_ics_escape(event['agenda'])}")
    if event.get("notes"):
        description_parts.append(f"\\nNotes:\\n{_ics_escape(event['notes'])}")
    description = "\\n".join(description_parts)

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Manager Task Generator//EN",
        "METHOD:REQUEST", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}", f"DTEND:{dtend}", f"SUMMARY:{summary}",
    ]
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
        lines.append(f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE{att_cn}:mailto:{clean_att_email}")
    if organizer_email:
        clean_org_email = _strip_control_chars(organizer_email)
        clean_org_name = _ics_cn_value(organizer_name)
        org_cn2 = f';CN="{clean_org_name}"' if clean_org_name else ""
        lines.append(f"ATTENDEE;PARTSTAT=ACCEPTED{org_cn2}:mailto:{clean_org_email}")
    lines += [
        "STATUS:CONFIRMED", "SEQUENCE:0",
        "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY",
        "DESCRIPTION:Reminder", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines)


def save_ics_file(ics_content, filename=None):
    export_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ics_exports")
    os.makedirs(export_dir, exist_ok=True)
    if not filename:
        filename = f"event_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ics"
    if not filename.endswith(".ics"):
        filename += ".ics"
    filepath = os.path.join(export_dir, filename)
    with open(filepath, "w") as f:
        f.write(ics_content)
    return filepath


def send_calendar_invite(event, recipient_email, recipient_name=None, manager_id=None):
    if manager_id is None:
        manager_id = event.get("manager_id") if isinstance(event, dict) else None
    smtp_server = get_config("smtp_server", manager_id=manager_id)
    smtp_port = get_config("smtp_port", manager_id=manager_id, default="587")
    smtp_user = get_config("smtp_user", manager_id=manager_id)
    smtp_password = get_config("smtp_password", manager_id=manager_id)
    manager_name = get_config("manager_name", manager_id=manager_id, default="Manager")
    manager_email = get_config("manager_email", manager_id=manager_id)

    if not all([smtp_server, smtp_user, smtp_password, manager_email]):
        return (False, "SMTP not configured. Run: python manager_tool.py config setup")

    ics_content = generate_ics(event, organizer_name=manager_name,
        organizer_email=manager_email, attendee_name=recipient_name,
        attendee_email=recipient_email)

    safe_title = "".join(
        c if c.isalnum() or c in " -_" else "" for c in event.get("title", "event")
    ).strip().replace(" ", "_")
    save_ics_file(ics_content, f"{safe_title}_{event['scheduled_date']}.ics")

    msg = MIMEMultipart("mixed")
    # Build address headers via formataddr after stripping control chars —
    # raw f-strings allowed CRLF injection forging extra Bcc/Subject lines
    # (AUDIT M3).
    msg["From"] = _safe_address_pair(manager_name, manager_email)
    msg["To"] = _safe_address_pair(recipient_name, recipient_email)
    msg["Subject"] = Header(
        _safe_header_text(f"Calendar Invite: {event.get('title', 'Meeting')}"),
        "utf-8",
    )

    event_type_labels = {
        "check_in": "Weekly Check-In", "coaching": "Coaching Session",
        "one_on_one": "1-on-1 Meeting", "quarterly_review": "Quarterly Review",
        "other": "Meeting",
    }
    body = (
        f"You're invited to: {event.get('title', 'Meeting')}\n\n"
        f"Type: {event_type_labels.get(event.get('event_type', ''), 'Meeting')}\n"
        f"Date: {event['scheduled_date']}\nTime: {event['scheduled_time']}\n"
        f"Duration: {event.get('duration_minutes', 30)} minutes\n"
    )
    if event.get("location"):
        body += f"Location: {event['location']}\n"
    if event.get("agenda"):
        body += f"\nAgenda:\n{event['agenda']}\n"
    body += "\nPlease open the attached .ics file or accept this invitation to add it to your Google Calendar."
    msg.attach(MIMEText(body, "plain"))

    cal_part = MIMEBase("text", "calendar", method="REQUEST")
    cal_part.set_payload(ics_content.encode("utf-8"))
    encoders.encode_base64(cal_part)
    cal_part.add_header("Content-Disposition", "attachment", filename="invite.ics")
    msg.attach(cal_part)

    try:
        port = int(smtp_port) if smtp_port and str(smtp_port).isdigit() else 587
        server = smtplib.SMTP(smtp_server, port)
        server.ehlo(); server.starttls(); server.ehlo()
        server.login(smtp_user, smtp_password)
        server.sendmail(manager_email, [recipient_email], msg.as_string())
        server.quit()
        return (True, f"Invitation sent to {recipient_email}")
    except smtplib.SMTPAuthenticationError:
        return (False, "SMTP authentication failed. For Gmail, use an App Password: https://myaccount.google.com/apppasswords")
    except smtplib.SMTPException as e:
        return (False, f"SMTP error: {e}")
    except Exception as e:
        return (False, f"Failed to send email: {e}")


def send_invite_to_self(event, manager_id=None):
    if manager_id is None:
        manager_id = event.get("manager_id") if isinstance(event, dict) else None
    manager_email = get_config("manager_email", manager_id=manager_id)
    manager_name = get_config("manager_name", manager_id=manager_id, default="Manager")
    if not manager_email:
        return (False, "Manager email not configured. Run: python manager_tool.py config setup")
    return send_calendar_invite(event, manager_email, manager_name, manager_id=manager_id)


def _ics_escape(text):
    """RFC 5545 TEXT escape, with control-char stripping.

    The previous implementation escaped \\, ;, , and \\n but left \\r and
    other control characters intact, allowing CRLF injection that forges
    new VEVENT lines or alters ATTENDEE/ORGANIZER properties (AUDIT M3).

    Order matters: backslash first (so we don't double-escape later),
    then the structural separators, then convert real newlines to \\n
    *before* stripping control chars (otherwise legitimate multi-line
    agendas would lose all their line breaks). Finally, every other
    control character (incl. \\r, NUL, vertical tab) is stripped — none of
    them have legitimate use in an ICS TEXT value.
    """
    if not text:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;")
    s = s.replace(",", "\\,")
    s = s.replace("\n", "\\n")
    # Strip every remaining control character. \n is gone (escaped above);
    # \r and friends are dangerous — drop them.
    return _CONTROL_CHARS_RE.sub("", s)


# ---------------------------------------------------------------------------
# Weekly email digest
# ---------------------------------------------------------------------------

def generate_weekly_digest(manager_id=None):
    """Generate an HTML email body summarizing the manager's week.
    Returns (subject: str, html_body: str)."""
    import database as db

    manager = db.get_manager(manager_id) if manager_id else None
    name = manager["display_name"] if manager else "Manager"
    summary = db.get_weekly_summary(manager_id=manager_id)
    nudges = db.get_nudges(manager_id=manager_id)
    streak = db.get_journal_streak(manager_id=manager_id)

    upcoming = summary.get("upcoming_events", [])
    completed = summary.get("completed_events", [])
    pending = summary.get("pending_actions", [])
    overdue = summary.get("overdue_actions", [])

    subject = f"Manager Tool Weekly Digest — {datetime.now().strftime('%b %d, %Y')}"

    sections = []
    sections.append(f"<h2>Weekly Digest for {name}</h2>")
    sections.append(f"<p><strong>Journal streak:</strong> {streak} day{'s' if streak != 1 else ''}</p>")

    # Nudges
    if nudges:
        sections.append("<h3>Nudges</h3><ul>")
        for n in nudges:
            icon = {"critical": "&#x1F6A8;", "warning": "&#x26A0;", "info": "&#x2139;"}.get(
                n["severity"], "")
            sections.append(f"<li>{icon} {n['message']}</li>")
        sections.append("</ul>")

    # Upcoming events
    if upcoming:
        sections.append(f"<h3>Upcoming Events ({len(upcoming)})</h3><ul>")
        for e in upcoming[:10]:
            sections.append(
                f"<li><strong>{e.get('title', 'Event')}</strong> — "
                f"{e['scheduled_date']} at {e['scheduled_time']}"
                f"{' with ' + e['participant_name'] if e.get('participant_name') else ''}</li>")
        sections.append("</ul>")

    # Completed this week
    if completed:
        sections.append(f"<h3>Completed This Week ({len(completed)})</h3><ul>")
        for e in completed[:10]:
            sections.append(f"<li>{e.get('title', 'Event')} — {e['scheduled_date']}</li>")
        sections.append("</ul>")

    # Overdue actions
    if overdue:
        sections.append(f"<h3 style='color:#cc0000'>Overdue Action Items ({len(overdue)})</h3><ul>")
        for a in overdue:
            sections.append(
                f"<li><strong>{a['description']}</strong>"
                f" — due {a.get('due_date', 'N/A')}"
                f"{', assigned to ' + a['assignee'] if a.get('assignee') else ''}</li>")
        sections.append("</ul>")

    # Pending actions
    if pending:
        sections.append(f"<h3>Pending Actions ({len(pending)})</h3><ul>")
        for a in pending[:10]:
            sections.append(f"<li>{a['description']}"
                           f"{' (due ' + a['due_date'] + ')' if a.get('due_date') else ''}</li>")
        sections.append("</ul>")

    sections.append("<hr><p style='color:#888;font-size:0.85em'>"
                    "Sent by Manager Tool. Open the app to take action.</p>")

    html_body = "\n".join(sections)
    return subject, html_body


def send_weekly_digest(manager_id):
    """Send the weekly digest email to the configured manager email.
    Returns (success: bool, message: str)."""
    smtp_server = get_config("smtp_server", manager_id=manager_id)
    smtp_port = get_config("smtp_port", manager_id=manager_id, default="587")
    smtp_user = get_config("smtp_user", manager_id=manager_id)
    smtp_password = get_config("smtp_password", manager_id=manager_id)
    manager_email = get_config("manager_email", manager_id=manager_id)
    manager_name = get_config("manager_name", manager_id=manager_id, default="Manager")

    if not all([smtp_server, smtp_user, smtp_password, manager_email]):
        return (False, "SMTP not configured. Set up email in Settings > Configuration.")

    subject, html_body = generate_weekly_digest(manager_id)

    msg = MIMEMultipart("alternative")
    msg["From"] = _safe_address_pair(manager_name, manager_email)
    msg["To"] = _safe_address_pair("", manager_email)
    msg["Subject"] = Header(_safe_header_text(subject), "utf-8")

    # Plain text fallback
    import re
    plain_text = re.sub(r"<[^>]+>", "", html_body).strip()
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        port = int(smtp_port) if smtp_port and str(smtp_port).isdigit() else 587
        server = smtplib.SMTP(smtp_server, port)
        server.ehlo(); server.starttls(); server.ehlo()
        server.login(smtp_user, smtp_password)
        server.sendmail(manager_email, [manager_email], msg.as_string())
        server.quit()
        return (True, f"Weekly digest sent to {manager_email}")
    except smtplib.SMTPAuthenticationError:
        return (False, "SMTP authentication failed. Check your App Password.")
    except Exception as e:
        return (False, f"Failed to send digest: {e}")
