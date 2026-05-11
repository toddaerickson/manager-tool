"""Audit logging service (D3 — HR data mutation tracking).

Provides a single `log_mutation` function that views call after any
create/update/delete on HR-sensitive models. The log is immutable and
append-only — entries are never updated or deleted.

Usage in views:
    from core.services.audit import log_mutation
    log_mutation(manager.id, "create", "Feedback", fb.id,
                 f"Positive feedback for {fb.team_member.name}")
"""

import logging

from core.models import AuditLog

logger = logging.getLogger(__name__)

# Models considered HR-sensitive (D3 scope from /review-as audit PR #67)
HR_SENSITIVE_MODELS = {
    "TeamMember", "Feedback", "Goal", "CareerConversation",
    "DevelopmentPlan", "Milestone", "Skill", "Delegation",
    "Decision", "RunningNote",
}


def log_mutation(manager_id, action, entity_type, entity_id, summary=""):
    """Append an immutable audit log entry.

    Args:
        manager_id: the manager who performed the action
        action: "create", "update", or "delete"
        entity_type: model class name (e.g. "Feedback")
        entity_id: primary key of the affected row
        summary: human-readable description of the change
    """
    if manager_id is None:
        logger.warning("audit: skipping log with manager_id=None")
        return

    try:
        AuditLog.objects.create(
            manager_id=manager_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary[:500],  # cap summary length
        )
    except Exception:
        # Audit log is fire-and-forget — a DB error here must not
        # surface as a 500 after the mutation has already committed.
        logger.exception("audit: failed to log %s on %s(%s)", action, entity_type, entity_id)
