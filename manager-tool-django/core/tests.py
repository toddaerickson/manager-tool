"""Tests for the core app.

TestCrossManagerScoping is the Django ORM port of
tests/test_database.py::TestCrossManagerScoping (audit C1 / P1.2). The
Streamlit version exercises every db.* helper to prove cross-manager
access is rejected. This Django version asserts the structural property
those helpers depended on: TenantManager.for_manager(X) returns ZERO
of manager Y's rows, and vice versa, on every tenant-scoped model.

Per-helper / per-view cross-tenant tests come later (Phase 5, alongside
each page port).
"""

import pytest

from coaching.models import CoachSuggestion
from core.models import (
    Decision,
    Event,
    JournalEntry,
    Manager,
    TeamMember,
)


@pytest.mark.django_db
class TestCrossManagerScoping:
    """Audit C1 parity — TenantManager.for_manager isolates rows
    bidirectionally on every tenant-scoped model."""

    def _two_managers(self):
        m1 = Manager.objects.create(
            username="scope_m1", display_name="M1",
            password_hash="hash1", email="m1@example.com",
        )
        m2 = Manager.objects.create(
            username="scope_m2", display_name="M2",
            password_hash="hash2", email="m2@example.com",
        )
        return m1, m2

    def test_team_members_isolated_bidirectionally(self):
        m1, m2 = self._two_managers()
        TeamMember.objects.create(name="M1's report", manager_id=m1.id)
        TeamMember.objects.create(name="M2's report", manager_id=m2.id)

        m1_rows = TeamMember.objects.for_manager(m1.id)
        m2_rows = TeamMember.objects.for_manager(m2.id)

        assert m1_rows.count() == 1
        assert m2_rows.count() == 1
        assert m1_rows.first().name == "M1's report"
        assert m2_rows.first().name == "M2's report"
        # Critical: neither manager can see the other's row through
        # the tenant manager — the audit C1 guarantee.
        assert not m1_rows.filter(name="M2's report").exists()
        assert not m2_rows.filter(name="M1's report").exists()

    def test_events_isolated(self):
        m1, m2 = self._two_managers()
        Event.objects.create(
            title="M1 meet", event_type="one_on_one",
            scheduled_date="2026-05-10", scheduled_time="10:00",
            manager_id=m1.id,
        )
        assert Event.objects.for_manager(m1.id).count() == 1
        assert Event.objects.for_manager(m2.id).count() == 0

    def test_journal_entries_isolated(self):
        m1, m2 = self._two_managers()
        JournalEntry.objects.create(
            entry_date="2026-05-01", entry_type="daily",
            content="m1 only", manager_id=m1.id,
        )
        assert JournalEntry.objects.for_manager(m1.id).count() == 1
        assert JournalEntry.objects.for_manager(m2.id).count() == 0

    def test_decisions_isolated(self):
        m1, m2 = self._two_managers()
        Decision.objects.create(title="m1 only", manager_id=m1.id)
        assert Decision.objects.for_manager(m1.id).count() == 1
        assert Decision.objects.for_manager(m2.id).count() == 0

    def test_coaching_app_uses_same_tenant_manager(self):
        """Cross-app sanity check: coaching's CoachSuggestion imports
        TenantManager from core and behaves the same way."""
        m1, m2 = self._two_managers()
        CoachSuggestion.objects.create(
            suggestion_date="2026-05-01", tier="weekly",
            suggestion="m1 only", manager_id=m1.id,
        )
        assert CoachSuggestion.objects.for_manager(m1.id).count() == 1
        assert CoachSuggestion.objects.for_manager(m2.id).count() == 0

    def test_for_manager_rejects_none(self):
        """Required by TenantManager — None must fail loud, not silently
        scan the whole table."""
        with pytest.raises(ValueError, match="requires a manager_id"):
            TeamMember.objects.for_manager(None)
