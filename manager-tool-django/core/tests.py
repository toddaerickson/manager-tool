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


# ============================================================
# Phase 3: bridge from request.user (allauth) to request.manager
# ============================================================


@pytest.mark.django_db
class TestManagerBridge:
    """Phase 3 → 4 gate — ManagerBridgeMiddleware attaches the right
    Manager row (or None) to the request based on request.user.email.
    """

    def _user(self, email):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )

    def _request(self, user):
        from django.test import RequestFactory
        rf = RequestFactory()
        request = rf.get("/dashboard/")
        request.user = user
        return request

    def _bridge(self, request):
        from core.middleware import ManagerBridgeMiddleware
        mw = ManagerBridgeMiddleware(get_response=lambda r: None)
        mw(request)
        return request

    def test_unauth_user_yields_none(self):
        from django.contrib.auth.models import AnonymousUser
        request = self._request(AnonymousUser())
        self._bridge(request)
        assert request.manager is None

    def test_auth_user_with_no_matching_manager_yields_none(self):
        Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        request = self._request(self._user("nobody@example.com"))
        self._bridge(request)
        assert request.manager is None

    def test_auth_user_with_matching_manager_attaches_it(self):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        request = self._request(self._user("todd@example.com"))
        self._bridge(request)
        assert request.manager is not None
        assert request.manager.id == m.id

    def test_email_match_is_case_insensitive(self):
        """Google sometimes normalizes case; managers.email is whatever
        was typed. Match must be case-insensitive."""
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="Todd@Example.COM",
        )
        request = self._request(self._user("todd@example.com"))
        self._bridge(request)
        assert request.manager is not None
        assert request.manager.id == m.id

    def test_user_with_no_email_yields_none(self):
        """Defensive — if Google somehow returns a user with no email
        (shouldn't happen with our scopes), don't 500."""
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username="noemail", email="", password="x",
        )
        request = self._request(u)
        self._bridge(request)
        assert request.manager is None


@pytest.mark.django_db
class TestDashboardView:
    """Phase 3 → 4 gate — `request.manager.id` correctly scopes the
    dashboard's per-tenant query, and logout invalidates the session."""

    def _login_as(self, client, email, manager=None):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="testpw",
        )
        client.force_login(u)
        return u

    def test_anonymous_redirects_to_login(self, client):
        resp = client.get("/dashboard/")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]

    def test_logged_in_user_with_no_manager_gets_403(self, client):
        self._login_as(client, "stranger@example.com")
        resp = client.get("/dashboard/")
        assert resp.status_code == 403

    def test_logged_in_user_with_manager_sees_their_member_count(self, client):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        TeamMember.objects.create(name="report A", manager_id=m.id)
        TeamMember.objects.create(name="report B", manager_id=m.id)
        # Another manager's data — must not bleed in
        m2 = Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        TeamMember.objects.create(name="other report", manager_id=m2.id)

        self._login_as(client, "todd@example.com")
        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Team members: 2" in body, body
        assert "id=" + str(m.id) in body

    def test_logout_invalidates_session(self, client):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        # Authenticated dashboard works
        assert client.get("/dashboard/").status_code == 200
        # Force-logout via the test client (mirrors what /accounts/logout/ does)
        client.logout()
        # Now blocked
        resp = client.get("/dashboard/")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp["Location"]
