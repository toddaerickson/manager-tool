"""Weekly digest email service (Django port).

Generates and sends an HTML email summarizing the manager's week:
upcoming events, completed events, overdue to-dos, pending delegations,
journal streak, and decisions due for review.

Uses per-manager SMTP config from the Config table (same as
calendar invites). Preserves M3 sanitization from calendar service.
"""

import logging
import re
from datetime import date, datetime, timedelta
from email.header import Header
from html import escape as h
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from core.models import (
    ActionItem, Config, Decision, Delegation, Event, JournalEntry,
    Manager, TeamMember,
)
from core.services.calendar import _safe_address_pair, _safe_header_text
from core.services.email import get_smtp_settings, send_smtp
from core.services.journal import journal_streak

logger = logging.getLogger(__name__)


def generate_weekly_digest(manager_id):
    """Generate an HTML email body summarizing the manager's week.

    Returns (subject: str, html_body: str).
    """
    manager = Manager.objects.filter(id=manager_id).first()
    name = manager.display_name if manager else "Manager"
    today = date.today()
    today_iso = today.isoformat()
    week_ago_iso = (today - timedelta(days=7)).isoformat()
    week_ahead_iso = (today + timedelta(days=7)).isoformat()

    # Upcoming events (next 7 days)
    upcoming = (
        Event.objects.for_manager(manager_id)
        .filter(status="scheduled", scheduled_date__gte=today_iso,
                scheduled_date__lt=week_ahead_iso)
        .select_related("team_member")
        .order_by("scheduled_date", "scheduled_time")[:10]
    )

    # Completed this week
    completed = (
        Event.objects.for_manager(manager_id)
        .filter(status="completed", scheduled_date__gte=week_ago_iso,
                scheduled_date__lte=today_iso)
        .order_by("-scheduled_date")[:10]
    )

    # Overdue action items
    overdue = (
        ActionItem.objects.for_manager(manager_id)
        .filter(status="pending", due_date__lt=today_iso)
        .order_by("due_date")[:10]
    )

    # Pending action items
    pending = (
        ActionItem.objects.for_manager(manager_id)
        .filter(status="pending")
        .order_by("due_date")[:10]
    )

    # Overdue delegations
    overdue_dels = (
        Delegation.objects.for_manager(manager_id)
        .filter(status="active", check_in_date__lt=today_iso)
        .select_related("team_member")[:5]
    )

    # Decisions due
    decisions_due = (
        Decision.objects.for_manager(manager_id)
        .filter(status="active", review_date__lte=today_iso)[:5]
    )

    streak = journal_streak(manager_id)

    subject = f"Manager Tool Weekly Digest \u2014 {today.strftime('%b %d, %Y')}"

    sections = []
    sections.append(f"<h2>Weekly Digest for {h(name)}</h2>")
    sections.append(
        f"<p><strong>Journal streak:</strong> "
        f"{streak} day{'s' if streak != 1 else ''}</p>"
    )

    # AI-generated forward-looking plan (top section). Silently omitted
    # when no Anthropic API key is configured \u2014 backwards-looking
    # sections below still ship.
    try:
        from coaching.services import (
            generate_weekly_plan, render_weekly_plan_html,
        )
        plan_text = generate_weekly_plan(manager_id)
        if plan_text:
            plan_html = render_weekly_plan_html(plan_text)
            if plan_html:
                sections.append("<h3>This week's plan</h3>")
                sections.append(plan_html)
    except Exception:
        logger.exception("Weekly plan generation failed; digest will skip section")

    # Upcoming events
    if upcoming:
        sections.append(f"<h3>Upcoming Events ({upcoming.count()})</h3><ul>")
        for e in upcoming:
            member = f" with {h(e.team_member.name)}" if e.team_member else ""
            sections.append(
                f"<li><strong>{h(e.title)}</strong> &mdash; "
                f"{h(e.scheduled_date)} at {h(e.scheduled_time)}{member}</li>"
            )
        sections.append("</ul>")

    # Completed this week
    if completed:
        sections.append(f"<h3>Completed This Week ({completed.count()})</h3><ul>")
        for e in completed:
            sections.append(f"<li>{h(e.title)} &mdash; {h(e.scheduled_date)}</li>")
        sections.append("</ul>")

    # Overdue action items
    if overdue:
        sections.append(
            f"<h3 style='color:#cc0000'>Overdue Action Items "
            f"({overdue.count()})</h3><ul>"
        )
        for a in overdue:
            sections.append(
                f"<li><strong>{h(a.description)}</strong> "
                f"&mdash; due {h(a.due_date or 'N/A')}</li>"
            )
        sections.append("</ul>")

    # Overdue delegations
    if overdue_dels:
        sections.append(
            f"<h3 style='color:#cc0000'>Overdue Delegations "
            f"({overdue_dels.count()})</h3><ul>"
        )
        for d in overdue_dels:
            member = h(d.team_member.name) if d.team_member else "?"
            sections.append(
                f"<li><strong>{h(d.task[:80])}</strong> &mdash; "
                f"{member}, check-in was {h(d.check_in_date or '?')}</li>"
            )
        sections.append("</ul>")

    # Decisions due for review
    if decisions_due:
        sections.append(
            f"<h3>Decisions Due for Review ({decisions_due.count()})</h3><ul>"
        )
        for d in decisions_due:
            sections.append(
                f"<li>{h(d.title)} &mdash; review by {h(d.review_date or '?')}</li>"
            )
        sections.append("</ul>")

    # Pending action items
    if pending:
        sections.append(f"<h3>Pending Actions ({pending.count()})</h3><ul>")
        for a in pending[:10]:
            due = f" (due {h(a.due_date)})" if a.due_date else ""
            sections.append(f"<li>{h(a.description)}{due}</li>")
        sections.append("</ul>")

    sections.append(
        "<hr><p style='color:#888;font-size:0.85em'>"
        "Sent by Manager Tool. Open the app to take action.</p>"
    )

    html_body = "\n".join(sections)
    return subject, html_body


def send_weekly_digest(manager_id):
    """Send the weekly digest email to the configured manager email.

    Returns (success: bool, message: str).
    """
    smtp_cfg = get_smtp_settings(manager_id)
    if smtp_cfg is None:
        return False, "SMTP not configured for this manager."

    subject, html_body = generate_weekly_digest(manager_id)

    msg = MIMEMultipart("alternative")
    msg["From"] = _safe_address_pair(smtp_cfg["name"], smtp_cfg["email"])
    msg["To"] = _safe_address_pair("", smtp_cfg["email"])
    msg["Subject"] = Header(_safe_header_text(subject), "utf-8")

    # Plain text fallback
    plain_text = re.sub(r"<[^>]+>", "", html_body).strip()
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    return send_smtp(smtp_cfg, msg)
