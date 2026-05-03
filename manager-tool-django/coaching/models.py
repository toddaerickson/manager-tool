"""Models owned by the coaching app — currently just CoachSuggestions.

The bulk of the coaching workflow is in services (calendar_service.py
and coaching.py port to coaching/services/ in Phase 6); the only data
model that lives here is the per-manager suggestion ledger.
"""

from django.db import models

from core.managers import TenantManager


class CoachSuggestion(models.Model):
    manager_id = models.IntegerField(blank=True, null=True, db_index=True)
    suggestion_date = models.TextField()
    tier = models.TextField()
    suggestion = models.TextField()
    action_page = models.TextField(blank=True, null=True)
    dismissed = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    objects = TenantManager()

    class Meta:
        db_table = "coach_suggestions"
        unique_together = (("manager_id", "suggestion_date", "tier"),)
        indexes = [
            models.Index(
                fields=["manager_id", "suggestion_date"],
                name="ix_coach_sugg_mgr_date",
            ),
        ]
