"""
Calendar invitation service.
Generates iCalendar (.ics) files and sends them via SMTP email
so recipients can accept and add events to Google Calendar.
"""

import smtplib
import uuid
import os
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

from database import get_config


def generate_ics(event, organizer_name=None, organizer_email=None,
                 attendee_name=None, attendee_email=None):
    uid = f"{uuid.uuid4()}@manager-tool"
    date_str = event["scheduled_date"]
    time_str = event["scheduled_time"]
    dt_start = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
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
        org_cn = f';CN="{organizer_name}"' if organizer_name else ""
        lines.append(f"ORGANIZER{org_cn}:mailto:{organizer_email}")
    if attendee_email:
        att_cn = f';CN="{attendee_name}"' if attendee_name else ""
        lines.append(f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE{att_cn}:mailto:{attendee_email}")
    if organizer_email:
        org_cn2 = f';CN="{organizer_name}"' if organizer_name else ""
        lines.append(f"ATTENDEE;PARTSTAT=ACCEPTED{org_cn2}:mailto:{organizer_email}")
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


def send_calendar_invite(event, recipient_email, recipient_name=None):
    smtp_server = get_config("smtp_server")
    smtp_port = get_config("smtp_port", "587")
    smtp_user = get_config("smtp_user")
    smtp_password = get_config("smtp_password")
    manager_name = get_config("manager_name", "Manager")
    manager_email = get_config("manager_email")

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
    msg["From"] = f"{manager_name} <{manager_email}>"
    msg["To"] = f"{recipient_name} <{recipient_email}>" if recipient_name else recipient_email
    msg["Subject"] = f"Calendar Invite: {event.get('title', 'Meeting')}"

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
        server = smtplib.SMTP(smtp_server, int(smtp_port))
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


def send_invite_to_self(event):
    manager_email = get_config("manager_email")
    manager_name = get_config("manager_name", "Manager")
    if not manager_email:
        return (False, "Manager email not configured. Run: python manager_tool.py config setup")
    return send_calendar_invite(event, manager_email, manager_name)


def _ics_escape(text):
    if not text:
        return ""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


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


def send_weekly_digest(manager_id=None):
    """Send the weekly digest email to the configured manager email.
    Returns (success: bool, message: str)."""
    smtp_server = get_config("smtp_server")
    smtp_port = get_config("smtp_port", "587")
    smtp_user = get_config("smtp_user")
    smtp_password = get_config("smtp_password")
    manager_email = get_config("manager_email")
    manager_name = get_config("manager_name", "Manager")

    if not all([smtp_server, smtp_user, smtp_password, manager_email]):
        return (False, "SMTP not configured. Set up email in Settings > Configuration.")

    subject, html_body = generate_weekly_digest(manager_id)

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{manager_name} <{manager_email}>"
    msg["To"] = manager_email
    msg["Subject"] = subject

    # Plain text fallback
    import re
    plain_text = re.sub(r"<[^>]+>", "", html_body).strip()
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        server = smtplib.SMTP(smtp_server, int(smtp_port))
        server.ehlo(); server.starttls(); server.ehlo()
        server.login(smtp_user, smtp_password)
        server.sendmail(manager_email, [manager_email], msg.as_string())
        server.quit()
        return (True, f"Weekly digest sent to {manager_email}")
    except smtplib.SMTPAuthenticationError:
        return (False, "SMTP authentication failed. Check your App Password.")
    except Exception as e:
        return (False, f"Failed to send digest: {e}")
