"""Tenant-scoped data export — serialize a manager's full data as a JSON blob.

`Config` is deliberately excluded: it holds secrets (Anthropic key, SMTP/IMAP
passwords) that must never leave the app. `AuditLog` and `InboxItem` are also
omitted (operational / transient, not user-authored content).
"""

from datetime import date, datetime

from core.models import (
    ActionItem, CareerConversation, Decision, Delegation, DevelopmentPlan,
    Event, Feedback, Goal, JournalEntry, Milestone, OneOnOneSession,
    RunningNote, SelfAssessment, Skill, TeamMember,
)

# (JSON key, model). Ordered for readability of the archive.
_TABLES = [
    ("team_members", TeamMember),
    ("events", Event),
    ("action_items", ActionItem),
    ("one_on_one_sessions", OneOnOneSession),
    ("delegations", Delegation),
    ("feedback", Feedback),
    ("goals", Goal),
    ("decisions", Decision),
    ("journal_entries", JournalEntry),
    ("running_notes", RunningNote),
    ("career_convos", CareerConversation),
    ("skills", Skill),
    ("development_plans", DevelopmentPlan),
    ("milestones", Milestone),
    ("self_assessments", SelfAssessment),
]


def _to_primitive(value):
    """Convert datetime/date to ISO strings; leave everything else as-is so the
    resulting dict is JSON-serializable."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _serialize(queryset):
    rows = []
    for inst in queryset.iterator():
        row = {}
        for field in inst._meta.concrete_fields:
            row[field.name] = _to_primitive(getattr(inst, field.attname))
        rows.append(row)
    return rows


def build_export_payload(manager):
    """Return a JSON-serializable dict of the manager's profile + all their
    tenant-scoped content, keyed by table name."""
    payload = {
        "manager": {
            "id": manager.id,
            "username": manager.username,
            "display_name": manager.display_name,
            "email": manager.email,
        },
    }
    for key, model in _TABLES:
        payload[key] = _serialize(
            model.objects.for_manager(manager.id).order_by("id")
        )
    return payload
