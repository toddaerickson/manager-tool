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
    ActionItem,
    CareerConversation,
    Decision,
    Delegation,
    DevelopmentPlan,
    Event,
    Goal,
    JournalEntry,
    Manager,
    Milestone,
    RunningNote,
    Skill,
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

    def test_anonymous_redirects_to_google_login(self, client):
        """Phase 3: Google-only auth. Anonymous hits go straight to the
        Google OAuth flow, not the email/password form."""
        resp = client.get("/dashboard/")
        assert resp.status_code == 302
        assert "/accounts/google/login/" in resp["Location"]

    def test_logged_in_user_with_no_manager_gets_403(self, client):
        self._login_as(client, "stranger@example.com")
        resp = client.get("/dashboard/")
        assert resp.status_code == 403

    def test_logged_in_user_with_manager_sees_dashboard_shell(self, client):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        body = resp.content.decode()
        # Phase 4: shell renders before HTMX panel loads. Sidebar shows
        # the manager's display name; overview panel placeholder is
        # present (the panel itself is fetched separately).
        assert "Todd" in body
        assert "overview-panel" in body
        assert "/dashboard/panels/overview/" in body

    def test_overview_panel_returns_per_tenant_count(self, client):
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
        resp = client.get("/dashboard/panels/overview/")
        assert resp.status_code == 200
        body = resp.content.decode()
        # Per-tenant count is 2 (Todd's), not 3 (all rows)
        assert ">2<" in body, body

    def test_overview_shows_overdue_count(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_dash2", display_name="Todd",
            password_hash="x", email="todd_dash2@example.com",
        )
        self._login_as(client, "todd_dash2@example.com")
        ActionItem.objects.create(
            description="Overdue task", manager_id=m.id,
            status="pending",
            due_date=(date.today() - timedelta(days=1)).isoformat(),
        )
        ActionItem.objects.create(
            description="Future task", manager_id=m.id,
            status="pending",
            due_date=(date.today() + timedelta(days=3)).isoformat(),
        )
        resp = client.get("/dashboard/panels/overview/")
        body = resp.content.decode()
        assert "Overdue" in body
        assert "Overdue task" in body  # appears in overdue list

    def test_overview_shows_upcoming_events(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_dash3", display_name="Todd",
            password_hash="x", email="todd_dash3@example.com",
        )
        self._login_as(client, "todd_dash3@example.com")
        Event.objects.create(
            title="Weekly sync", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=1)).isoformat(),
            scheduled_time="10:00", status="scheduled",
            manager_id=m.id,
        )
        resp = client.get("/dashboard/panels/overview/")
        body = resp.content.decode()
        assert "Weekly sync" in body

    def test_overview_cross_tenant_isolation(self, client):
        from datetime import date
        m1 = Manager.objects.create(
            username="todd_dash4", display_name="Todd",
            password_hash="x", email="todd_dash4@example.com",
        )
        m2 = Manager.objects.create(
            username="other_dash4", display_name="Other",
            password_hash="x", email="other_dash4@example.com",
        )
        self._login_as(client, "todd_dash4@example.com")
        # Create data for m2 only
        ActionItem.objects.create(
            description="Other's overdue", manager_id=m2.id,
            status="pending", due_date="2020-01-01",
        )
        Event.objects.create(
            title="Other's meeting", event_type="one_on_one",
            scheduled_date=date.today().isoformat(),
            scheduled_time="09:00", status="scheduled",
            manager_id=m2.id,
        )
        resp = client.get("/dashboard/panels/overview/")
        body = resp.content.decode()
        assert "Other's overdue" not in body
        assert "Other's meeting" not in body

    def test_logout_invalidates_session_with_setup(self, client):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        # Authenticated dashboard works
        assert client.get("/dashboard/").status_code == 200
        # Force-logout via the test client
        client.logout()
        # Now blocked
        resp = client.get("/dashboard/")
        assert resp.status_code == 302
        assert "/accounts/google/login/" in resp["Location"]


# ============================================================
# Phase 5.1: Team Members CRUD
# ============================================================


@pytest.mark.django_db
class TestTeamMembersList:
    """Phase 5.1: GET /team/ — per-tenant list, anon redirect, no-manager 403."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="testpw",
        )
        client.force_login(u)
        return u

    def test_anonymous_redirects_to_google_login(self, client):
        resp = client.get("/team/")
        assert resp.status_code == 302
        assert "/accounts/google/login/" in resp["Location"]

    def test_logged_in_no_manager_yields_403(self, client):
        self._login_as(client, "stranger@example.com")
        assert client.get("/team/").status_code == 403

    def test_lists_only_own_members(self, client):
        m1 = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        m2 = Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        TeamMember.objects.create(name="Todd report", manager_id=m1.id)
        TeamMember.objects.create(name="Other report", manager_id=m2.id)

        self._login_as(client, "todd@example.com")
        resp = client.get("/team/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Todd report" in body
        assert "Other report" not in body, "cross-tenant leak"


@pytest.mark.django_db
class TestTeamMembersAdd:
    """Phase 5.1: POST /team/add/ — HTMX endpoint."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="testpw",
        )
        client.force_login(u)
        return u

    def test_get_not_allowed(self, client):
        Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        assert client.get("/team/add/").status_code == 405

    def test_create_persists_with_correct_manager_id(self, client):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        resp = client.post("/team/add/", {
            "name": "New Hire",
            "email": "new@example.com",
            "role": "PM",
            "start_date": "2026-05-10",
            "notes": "from PR test",
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "New Hire" in body  # appears in returned list partial

        # Verify DB: row created, manager_id set, start_date is ISO string
        members = TeamMember.objects.for_manager(m.id)
        assert members.count() == 1
        nh = members.first()
        assert nh.name == "New Hire"
        assert nh.start_date == "2026-05-10"  # TextField, not DateField

    def test_validation_error_returns_form_with_422(self, client):
        Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        # Missing required name
        resp = client.post("/team/add/", {"email": "x@example.com"})
        assert resp.status_code == 422
        body = resp.content.decode()
        # Form re-rendered with error
        assert "form" in body.lower()
        assert TeamMember.objects.count() == 0

    def test_other_managers_data_unaffected(self, client):
        m1 = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        m2 = Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        TeamMember.objects.create(name="Other existing", manager_id=m2.id)

        self._login_as(client, "todd@example.com")
        client.post("/team/add/", {"name": "Todd new"})

        assert TeamMember.objects.for_manager(m1.id).count() == 1
        assert TeamMember.objects.for_manager(m2.id).count() == 1
        assert TeamMember.objects.for_manager(m1.id).first().name == "Todd new"
        assert TeamMember.objects.for_manager(m2.id).first().name == "Other existing"

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger@example.com")
        resp = client.post("/team/add/", {"name": "X"})
        assert resp.status_code == 403


@pytest.mark.django_db
class TestTeamMembersSoftDelete:
    """Phase 5.1b: DELETE /team/<id>/delete/ — soft-delete + 30-day undo."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        member = TeamMember.objects.create(name="Doomed", manager_id=m.id)
        self._login_as(client, "todd@example.com")
        return m, member

    def test_delete_sets_deleted_at_not_remove_row(self, client):
        m, member = self._setup(client)
        resp = client.delete(f"/team/{member.id}/delete/")
        assert resp.status_code == 200
        member.refresh_from_db()
        assert member.deleted_at is not None
        # Row physically still there
        assert TeamMember.objects.filter(pk=member.id).exists()
        # But not in active list
        assert TeamMember.objects.active_for_manager(m.id).count() == 0
        # Visible in deleted list
        assert TeamMember.objects.recently_deleted_for_manager(m.id).count() == 1

    def test_delete_cross_tenant_returns_404(self, client):
        m, member = self._setup(client)  # Todd is logged in
        # Try to delete a member belonging to another manager
        m2 = Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        other_member = TeamMember.objects.create(name="Other's", manager_id=m2.id)
        resp = client.delete(f"/team/{other_member.id}/delete/")
        assert resp.status_code == 404
        # Other's row untouched
        other_member.refresh_from_db()
        assert other_member.deleted_at is None

    def test_delete_already_deleted_returns_404(self, client):
        """Idempotency guard: deleting a soft-deleted row returns 404
        rather than re-stamping deleted_at."""
        m, member = self._setup(client)
        client.delete(f"/team/{member.id}/delete/")
        resp = client.delete(f"/team/{member.id}/delete/")
        assert resp.status_code == 404

    def test_active_list_excludes_soft_deleted(self, client):
        m, member = self._setup(client)
        # Add a second active member
        TeamMember.objects.create(name="Active", manager_id=m.id)
        client.delete(f"/team/{member.id}/delete/")
        resp = client.get("/team/")
        body = resp.content.decode()
        assert "Active" in body
        assert "Doomed" not in body or "Recently deleted" in body
        # The deleted name appears only in the Recently deleted section
        # (not in the main table). Quick structural check:
        deleted_idx = body.find("Recently deleted")
        doomed_idx = body.find("Doomed")
        assert deleted_idx > 0 and doomed_idx > deleted_idx, "Doomed should be in deleted section"


@pytest.mark.django_db
class TestTeamMembersRestore:
    """Phase 5.1b: POST /team/<id>/restore/ — undo within 30 days."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        return m

    def test_restore_clears_deleted_at(self, client):
        m = self._setup(client)
        member = TeamMember.objects.create(name="X", manager_id=m.id)
        client.delete(f"/team/{member.id}/delete/")
        resp = client.post(f"/team/{member.id}/restore/")
        assert resp.status_code == 200
        member.refresh_from_db()
        assert member.deleted_at is None
        assert TeamMember.objects.active_for_manager(m.id).count() == 1

    def test_restore_outside_window_returns_404(self, client):
        """Soft-deletes older than 30 days are no longer restorable
        (undo window expired). Force the deleted_at into the past."""
        from django.utils import timezone
        from datetime import timedelta
        m = self._setup(client)
        member = TeamMember.objects.create(
            name="ExpiredUndo", manager_id=m.id,
            deleted_at=timezone.now() - timedelta(days=31),
        )
        resp = client.post(f"/team/{member.id}/restore/")
        assert resp.status_code == 404
        member.refresh_from_db()
        assert member.deleted_at is not None  # not restored

    def test_restore_cross_tenant_returns_404(self, client):
        from django.utils import timezone
        m = self._setup(client)  # Todd
        m2 = Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        other_member = TeamMember.objects.create(
            name="Other's", manager_id=m2.id,
            deleted_at=timezone.now(),
        )
        resp = client.post(f"/team/{other_member.id}/restore/")
        assert resp.status_code == 404
        other_member.refresh_from_db()
        assert other_member.deleted_at is not None  # untouched


@pytest.mark.django_db
class TestPurgeDeletedTeamMembers:
    """Phase 5.1b: management command hard-deletes soft-deleted rows
    older than the undo window."""

    def test_purges_only_rows_past_window(self):
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        # 1 active, 1 recently deleted (within window), 1 expired
        active = TeamMember.objects.create(name="Active", manager_id=m.id)
        recent = TeamMember.objects.create(
            name="Recent", manager_id=m.id,
            deleted_at=timezone.now() - timedelta(days=5),
        )
        expired = TeamMember.objects.create(
            name="Expired", manager_id=m.id,
            deleted_at=timezone.now() - timedelta(days=45),
        )
        call_command("purge_deleted_team_members")
        # Active and recent survive; expired is gone
        assert TeamMember.objects.filter(pk=active.id).exists()
        assert TeamMember.objects.filter(pk=recent.id).exists()
        assert not TeamMember.objects.filter(pk=expired.id).exists()

    def test_dry_run_deletes_nothing(self):
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone
        m = Manager.objects.create(
            username="dry_runner", display_name="Dry",
            password_hash="x", email="dry@example.com",
        )
        expired = TeamMember.objects.create(
            name="Expired", manager_id=m.id,
            deleted_at=timezone.now() - timedelta(days=45),
        )
        call_command("purge_deleted_team_members", "--dry-run")
        assert TeamMember.objects.filter(pk=expired.id).exists()


# ============================================================
# Phase 5.2a: Events — list, schedule one-off, cancel, complete
# ============================================================


@pytest.mark.django_db
class TestEventsUpcoming:
    """GET /events/ — only this manager's scheduled events from today on,
    grouped by date."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        return m, date.today(), date.today() + timedelta(days=1)

    def test_anonymous_redirects_to_google_login(self, client):
        resp = client.get("/events/")
        assert resp.status_code == 302
        assert "/accounts/google/login/" in resp["Location"]

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger@example.com")
        assert client.get("/events/").status_code == 403

    def test_empty_state(self, client):
        m, today, tomorrow = self._setup(client)
        resp = client.get("/events/")
        assert resp.status_code == 200
        assert "Nothing scheduled" in resp.content.decode()

    def test_only_own_scheduled_future_events_shown(self, client):
        from datetime import date, timedelta
        m, today, tomorrow = self._setup(client)
        m2 = Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        # Mine, today, scheduled — should appear
        Event.objects.create(
            manager_id=m.id, title="Mine today", event_type="one_on_one",
            scheduled_date=today.isoformat(), scheduled_time="10:00",
            status="scheduled",
        )
        # Mine, yesterday — should NOT appear (past)
        Event.objects.create(
            manager_id=m.id, title="Mine past", event_type="one_on_one",
            scheduled_date=(today - timedelta(days=1)).isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        # Mine, tomorrow, cancelled — should NOT appear (status filter)
        Event.objects.create(
            manager_id=m.id, title="Mine cancelled", event_type="one_on_one",
            scheduled_date=tomorrow.isoformat(), scheduled_time="10:00",
            status="cancelled",
        )
        # Other manager's, tomorrow — should NOT appear (cross-tenant)
        Event.objects.create(
            manager_id=m2.id, title="Other's", event_type="one_on_one",
            scheduled_date=tomorrow.isoformat(), scheduled_time="10:00",
            status="scheduled",
        )
        resp = client.get("/events/")
        body = resp.content.decode()
        assert "Mine today" in body
        assert "Mine past" not in body
        assert "Mine cancelled" not in body
        assert "Other&#x27;s" not in body and "Other's" not in body

    def test_today_label_used_for_today(self, client):
        from datetime import date
        m, today, tomorrow = self._setup(client)
        Event.objects.create(
            manager_id=m.id, title="X", event_type="one_on_one",
            scheduled_date=today.isoformat(), scheduled_time="09:00",
            status="scheduled",
        )
        body = client.get("/events/").content.decode()
        assert ">Today<" in body


@pytest.mark.django_db
class TestEventsSchedule:
    """GET/POST /events/schedule/ — create a one-off event."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        return m

    def test_get_renders_form(self, client):
        self._setup(client)
        resp = client.get("/events/schedule/")
        assert resp.status_code == 200
        assert "Schedule event" in resp.content.decode()

    def test_post_creates_event_with_manager_id_and_redirects(self, client):
        from datetime import date, timedelta
        m = self._setup(client)
        resp = client.post("/events/schedule/", {
            "event_type": "one_on_one",
            "title": "1:1 with someone",
            "scheduled_date": (date.today() + timedelta(days=2)).isoformat(),
            "scheduled_time": "14:30",
            "duration_minutes": "30",
        })
        assert resp.status_code == 302  # redirect to /events/
        assert resp["Location"].endswith("/events/")

        ev = Event.objects.for_manager(m.id).get()
        assert ev.title == "1:1 with someone"
        assert ev.scheduled_time == "14:30"  # TextField, formatted
        assert ev.status == "scheduled"
        assert ev.manager_id == m.id

    def test_blank_title_uses_default_from_event_type(self, client):
        from datetime import date, timedelta
        m = self._setup(client)
        client.post("/events/schedule/", {
            "event_type": "coaching",
            "title": "",
            "scheduled_date": (date.today() + timedelta(days=1)).isoformat(),
            "scheduled_time": "11:00",
            "duration_minutes": "30",
        })
        ev = Event.objects.for_manager(m.id).get()
        assert ev.title == "Coaching"  # from EVENT_TYPE_CHOICES label

    def test_invalid_event_type_rejected(self, client):
        from datetime import date, timedelta
        m = self._setup(client)
        resp = client.post("/events/schedule/", {
            "event_type": "not_a_real_type",
            "scheduled_date": (date.today() + timedelta(days=1)).isoformat(),
            "scheduled_time": "11:00",
            "duration_minutes": "30",
        })
        assert resp.status_code == 200  # form re-renders
        assert Event.objects.count() == 0

    def test_time_dropdown_shows_friendly_am_pm_labels(self, client):
        self._setup(client)
        body = client.get("/events/schedule/").content.decode()
        # 30-min increments, 12-hour display format
        assert "9:00 AM" in body
        assert "12:00 PM" in body  # noon edge case
        assert "1:30 PM" in body   # post-noon edge case
        assert "9:00 PM" in body
        # Stored value is 24-hour HH:MM
        assert 'value="09:00"' in body
        assert 'value="12:00"' in body
        assert 'value="13:30"' in body

    def test_team_member_dropdown_scoped_to_this_manager(self, client):
        m = self._setup(client)
        m2 = Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        TeamMember.objects.create(name="Mine", manager_id=m.id)
        TeamMember.objects.create(name="Theirs", manager_id=m2.id)
        body = client.get("/events/schedule/").content.decode()
        assert "Mine" in body
        assert "Theirs" not in body, "cross-tenant team_member leak in dropdown"


@pytest.mark.django_db
class TestEventsComplete:
    """POST /events/<id>/complete/ — HTMX status transition.
    Cancel was removed (Phase 6) — functionally redundant with Delete
    once D2's source-of-truth contract puts Outlook in charge of *when*."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        ev = Event.objects.create(
            manager_id=m.id, title="X", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=1)).isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        return m, ev

    def test_complete_sets_status_completed(self, client):
        m, ev = self._setup(client)
        resp = client.post(f"/events/{ev.id}/complete/")
        assert resp.status_code == 200
        ev.refresh_from_db()
        assert ev.status == "completed"

    def test_complete_cross_tenant_returns_404(self, client):
        m, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other", display_name="Other",
            password_hash="x", email="other@example.com",
        )
        from datetime import date
        other = Event.objects.create(
            manager_id=m2.id, title="Other", event_type="other",
            scheduled_date=date.today().isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        resp = client.post(f"/events/{other.id}/complete/")
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.status == "scheduled"

    def test_get_not_allowed_on_complete(self, client):
        m, ev = self._setup(client)
        assert client.get(f"/events/{ev.id}/complete/").status_code == 405

    def test_cancel_url_is_removed(self, client):
        m, ev = self._setup(client)
        # Phase 6: cancel feature removed. URL should no longer exist.
        assert client.post(f"/events/{ev.id}/cancel/").status_code == 404


# ============================================================
# Dedupe fix — events_delete + near-duplicate check
# ============================================================


@pytest.mark.django_db
class TestEventsDelete:
    """DELETE /events/<id>/delete/ — hard delete (different from cancel)."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        from datetime import date
        m = Manager.objects.create(
            username="todd_del", display_name="Todd",
            password_hash="x", email="todd_del@example.com",
        )
        self._login_as(client, "todd_del@example.com")
        ev = Event.objects.create(
            manager_id=m.id, title="dupe", event_type="quarterly_review",
            scheduled_date=date(2026, 5, 29).isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        return m, ev

    def test_delete_removes_row(self, client):
        m, ev = self._setup(client)
        resp = client.delete(f"/events/{ev.id}/delete/")
        assert resp.status_code == 200
        assert not Event.objects.filter(pk=ev.id).exists()

    def test_delete_completed_event_works(self, client):
        """Cancel only works on status='scheduled' — Delete works on
        any status (used to clean up dupes regardless of state)."""
        m, ev = self._setup(client)
        ev.status = "completed"
        ev.save()
        resp = client.delete(f"/events/{ev.id}/delete/")
        assert resp.status_code == 200

    def test_delete_cross_tenant_returns_404(self, client):
        from datetime import date
        m, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_del", display_name="Other",
            password_hash="x", email="other_del@example.com",
        )
        other = Event.objects.create(
            manager_id=m2.id, title="other", event_type="other",
            scheduled_date=date.today().isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        resp = client.delete(f"/events/{other.id}/delete/")
        assert resp.status_code == 404
        assert Event.objects.filter(pk=other.id).exists()

    def test_delete_parent_leaves_children(self, client):
        """Recurring-series ON DELETE SET NULL: deleting the parent must
        not cascade — children survive (their parent_event_id becomes
        NULL via the FK constraint on PG; on SQLite the FK clause is
        omitted at create time so the column simply stays referencing a
        no-longer-existing id, but the children remain)."""
        from datetime import date
        from core.services.events import create_recurring_events
        m, _ = self._setup(client)  # _setup creates one extra event we'll ignore
        parent = create_recurring_events(
            manager_id=m.id, title="series",
            event_type="one_on_one", start_date=date(2026, 7, 1),
            scheduled_time="10:00", rule="weekly",
            until_date=date(2026, 7, 22),  # 4 occurrences
        )
        children_ids = list(
            Event.objects.filter(parent_event=parent).values_list("id", flat=True)
        )
        assert len(children_ids) == 3

        resp = client.delete(f"/events/{parent.id}/delete/")
        assert resp.status_code == 200
        assert not Event.objects.filter(pk=parent.id).exists()
        # All 3 children survive
        assert Event.objects.filter(id__in=children_ids).count() == 3

    def test_get_and_post_not_allowed(self, client):
        m, ev = self._setup(client)
        assert client.get(f"/events/{ev.id}/delete/").status_code == 405
        assert client.post(f"/events/{ev.id}/delete/").status_code == 405


@pytest.mark.django_db
class TestEventsScheduleNearDuplicateCheck:
    """events_schedule rejects near-duplicate POSTs (within 30s) silently
    with a redirect — defense against double-click + refresh-resubmit."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_dup", display_name="Todd",
            password_hash="x", email="todd_dup@example.com",
        )
        self._login_as(client, "todd_dup@example.com")
        return m

    def _payload(self, **overrides):
        from datetime import date, timedelta
        base = {
            "event_type": "quarterly_review",
            "title": "Quarterly Review: PG",
            "scheduled_date": (date.today() + timedelta(days=7)).isoformat(),
            "scheduled_time": "10:00",
            "duration_minutes": "30",
            "recurrence_rule": "",
        }
        base.update(overrides)
        return base

    def test_first_post_creates_event(self, client):
        m = self._setup(client)
        client.post("/events/schedule/", self._payload())
        assert Event.objects.for_manager(m.id).count() == 1

    def test_second_identical_post_within_window_is_silent_dedupe(self, client):
        """The bug the user reported: double-clicking Schedule produced
        duplicate events. The dedupe check rejects the second submit
        without creating, returning a normal redirect."""
        m = self._setup(client)
        client.post("/events/schedule/", self._payload())
        resp = client.post("/events/schedule/", self._payload())
        assert resp.status_code == 302
        assert resp["Location"].endswith("/events/")
        assert Event.objects.for_manager(m.id).count() == 1, (
            "second identical POST within window should NOT create another row"
        )

    def test_different_title_creates_separate_event(self, client):
        """Dedupe must NOT block legitimate different events at the
        same slot."""
        m = self._setup(client)
        client.post("/events/schedule/", self._payload(title="A"))
        client.post("/events/schedule/", self._payload(title="B"))
        assert Event.objects.for_manager(m.id).count() == 2

    def test_different_time_creates_separate_event(self, client):
        m = self._setup(client)
        client.post("/events/schedule/", self._payload(scheduled_time="10:00"))
        client.post("/events/schedule/", self._payload(scheduled_time="11:00"))
        assert Event.objects.for_manager(m.id).count() == 2

    def test_outside_window_creates_separate_event(self, client):
        """Past-the-window resubmits succeed — user genuinely meant to
        schedule the same slot again (rare, but allowed)."""
        from datetime import timedelta
        from django.utils import timezone
        m = self._setup(client)
        client.post("/events/schedule/", self._payload())
        # Force the existing row's created_at to be > 30s ago
        Event.objects.for_manager(m.id).update(
            created_at=timezone.now() - timedelta(minutes=5),
        )
        client.post("/events/schedule/", self._payload())
        assert Event.objects.for_manager(m.id).count() == 2

    def test_dedupe_applies_to_recurring_too(self, client):
        """The bug the user actually saw was on a recurring quarterly.
        Dedupe check runs BEFORE the rule branch, so the second submit
        of an identical recurring series is also blocked."""
        from datetime import date, timedelta
        m = self._setup(client)
        start = date.today() + timedelta(days=7)
        payload = self._payload(
            scheduled_date=start.isoformat(),
            recurrence_rule="quarterly",
            until_date=(start + timedelta(days=365)).isoformat(),
        )
        client.post("/events/schedule/", payload)
        first_count = Event.objects.for_manager(m.id).count()
        assert first_count > 1  # series created

        client.post("/events/schedule/", payload)
        # No additional events; the second submit was deduped at the parent
        assert Event.objects.for_manager(m.id).count() == first_count

    def test_cross_tenant_dedupe_not_triggered(self, client):
        """Manager B's identical event must NOT trigger Manager A's
        dedupe (and vice versa) — for_manager scoping inside the check."""
        from datetime import date, timedelta
        m = self._setup(client)  # Todd
        m2 = Manager.objects.create(
            username="other_dup", display_name="Other",
            password_hash="x", email="other_dup@example.com",
        )
        # Other manager already has an identical event (created just now)
        Event.objects.create(
            manager_id=m2.id, title="Quarterly Review: PG",
            event_type="quarterly_review",
            scheduled_date=(date.today() + timedelta(days=7)).isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        # Todd posts the same thing — must succeed (not dedupe across tenants)
        client.post("/events/schedule/", self._payload())
        assert Event.objects.for_manager(m.id).count() == 1
        assert Event.objects.for_manager(m2.id).count() == 1


# ============================================================
# Phase 5.2b — recurring events
# ============================================================


class TestAddMonthsAnchored:
    """Pure-Python helper. Algo A (anchored), NOT Algo B (drift)."""

    def test_zero_months_is_identity(self):
        from datetime import date
        from core.services.events import add_months_anchored
        assert add_months_anchored(date(2026, 5, 15), 0) == date(2026, 5, 15)

    def test_jan_31_plus_one_clamps_to_feb_28(self):
        from datetime import date
        from core.services.events import add_months_anchored
        assert add_months_anchored(date(2026, 1, 31), 1) == date(2026, 2, 28)

    def test_jan_31_plus_two_anchors_back_to_mar_31(self):
        """Algo A: anchor preserved (+2 from start, not +1 from clamped)."""
        from datetime import date
        from core.services.events import add_months_anchored
        assert add_months_anchored(date(2026, 1, 31), 2) == date(2026, 3, 31)

    def test_leap_year_feb_29_handling(self):
        from datetime import date
        from core.services.events import add_months_anchored
        assert add_months_anchored(date(2028, 1, 31), 1) == date(2028, 2, 29)

    def test_year_rollover(self):
        from datetime import date
        from core.services.events import add_months_anchored
        assert add_months_anchored(date(2026, 12, 15), 1) == date(2027, 1, 15)
        assert add_months_anchored(date(2026, 12, 15), 14) == date(2028, 2, 15)


class TestExpandRecurrenceDates:
    def test_weekly_default_count_is_12(self):
        from datetime import date
        from core.services.events import expand_recurrence_dates
        dates = expand_recurrence_dates(date(2026, 6, 1), "weekly", until=None)
        assert len(dates) == 12

    def test_monthly_default_count_is_12(self):
        from datetime import date
        from core.services.events import expand_recurrence_dates
        dates = expand_recurrence_dates(date(2026, 6, 1), "monthly", until=None)
        assert len(dates) == 12

    def test_quarterly_default_count_is_8(self):
        from datetime import date
        from core.services.events import expand_recurrence_dates
        dates = expand_recurrence_dates(date(2026, 6, 1), "quarterly", until=None)
        assert len(dates) == 8

    def test_until_caps_count(self):
        from datetime import date, timedelta
        from core.services.events import expand_recurrence_dates
        start = date(2026, 6, 1)
        until = start + timedelta(weeks=3)
        dates = expand_recurrence_dates(start, "weekly", until=until)
        assert len(dates) == 4
        assert dates[-1] == start + timedelta(weeks=3)

    def test_unknown_rule_raises(self):
        from datetime import date
        from core.services.events import expand_recurrence_dates
        with pytest.raises(ValueError, match="unknown recurrence rule"):
            expand_recurrence_dates(date(2026, 6, 1), "yearly")


@pytest.mark.django_db
class TestCreateRecurringEvents:
    """Service-layer: atomic materialization. The forced-failure no-orphan
    case mirrors smoke_pg_django.py's check; this version uses SQLite
    in-memory but the transaction.atomic guarantee is the same."""

    def _manager(self):
        return Manager.objects.create(
            username="todd_recur", display_name="Todd",
            password_hash="x", email="todd_recur@example.com",
        )

    def test_happy_path_creates_parent_plus_children(self):
        from datetime import date, timedelta
        from core.services.events import create_recurring_events
        m = self._manager()
        start = date(2026, 6, 1)
        until = start + timedelta(weeks=3)
        parent = create_recurring_events(
            manager_id=m.id, title="weekly 1:1",
            event_type="one_on_one", start_date=start,
            scheduled_time="10:00", rule="weekly", until_date=until,
        )
        in_series = Event.objects.for_manager(m.id).filter(
            recurrence_rule="weekly",
        )
        assert in_series.count() == 4
        assert in_series.filter(parent_event__isnull=True).count() == 1
        assert in_series.filter(parent_event=parent).count() == 3

    def test_forced_failure_rolls_back_parent(self):
        """No-orphan: bulk_create raises → parent Event.create rolls back."""
        from datetime import date
        from core.services.events import create_recurring_events
        m = self._manager()
        before = Event.objects.for_manager(m.id).count()
        original = Event.objects.bulk_create

        def _boom(*_a, **_kw):
            raise RuntimeError("test forced failure")

        Event.objects.bulk_create = _boom
        try:
            with pytest.raises(RuntimeError, match="test forced failure"):
                create_recurring_events(
                    manager_id=m.id, title="X", event_type="other",
                    start_date=date(2026, 6, 1),
                    scheduled_time="10:00", rule="weekly",
                )
        finally:
            Event.objects.bulk_create = original

        after = Event.objects.for_manager(m.id).count()
        assert after == before, (
            f"NO-ORPHAN FAIL: {after - before} orphan rows after rollback"
        )

    def test_rejects_string_start_date(self):
        from core.services.events import create_recurring_events
        m = self._manager()
        with pytest.raises(TypeError, match="start_date must be a date"):
            create_recurring_events(
                manager_id=m.id, title="X", event_type="other",
                start_date="2026-06-01",  # string, not date
                scheduled_time="10:00", rule="weekly",
            )

    def test_rejects_until_before_start(self):
        from datetime import date
        from core.services.events import create_recurring_events
        m = self._manager()
        with pytest.raises(ValueError, match="until_date must be >= start_date"):
            create_recurring_events(
                manager_id=m.id, title="X", event_type="other",
                start_date=date(2026, 6, 5),
                until_date=date(2026, 6, 1),
                scheduled_time="10:00", rule="weekly",
            )

    def test_rejects_missing_manager_id(self):
        from datetime import date
        from core.services.events import create_recurring_events
        with pytest.raises(ValueError, match="manager_id required"):
            create_recurring_events(
                manager_id=None, title="X", event_type="other",
                start_date=date(2026, 6, 1),
                scheduled_time="10:00", rule="weekly",
            )

    def test_cross_tenant_isolation(self):
        from datetime import date
        from core.services.events import create_recurring_events
        m1 = self._manager()
        m2 = Manager.objects.create(
            username="other_recur", display_name="Other",
            password_hash="x", email="other_recur@example.com",
        )
        create_recurring_events(
            manager_id=m1.id, title="m1", event_type="one_on_one",
            start_date=date(2026, 6, 1), scheduled_time="10:00",
            rule="weekly", until_date=date(2026, 6, 22),
        )
        assert Event.objects.for_manager(m1.id).count() == 4
        assert Event.objects.for_manager(m2.id).count() == 0


@pytest.mark.django_db
class TestEventsScheduleRecurringForm:
    """View-level: POST with rule routes through create_recurring_events."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def test_post_with_rule_creates_series(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_view_r", display_name="Todd",
            password_hash="x", email="todd_view_r@example.com",
        )
        self._login_as(client, "todd_view_r@example.com")
        start = date.today() + timedelta(days=7)
        until = start + timedelta(weeks=2)
        resp = client.post("/events/schedule/", {
            "event_type": "one_on_one",
            "title": "",
            "scheduled_date": start.isoformat(),
            "scheduled_time": "10:00",
            "duration_minutes": "30",
            "recurrence_rule": "weekly",
            "until_date": until.isoformat(),
        })
        assert resp.status_code == 302
        assert Event.objects.for_manager(m.id).filter(
            recurrence_rule="weekly",
        ).count() == 3

    def test_post_until_before_start_returns_form(self, client):
        from datetime import date, timedelta
        Manager.objects.create(
            username="todd_view_r2", display_name="Todd",
            password_hash="x", email="todd_view_r2@example.com",
        )
        self._login_as(client, "todd_view_r2@example.com")
        start = date.today() + timedelta(days=7)
        resp = client.post("/events/schedule/", {
            "event_type": "one_on_one",
            "scheduled_date": start.isoformat(),
            "scheduled_time": "10:00",
            "duration_minutes": "30",
            "recurrence_rule": "weekly",
            "until_date": (start - timedelta(days=1)).isoformat(),
        })
        assert resp.status_code == 200
        assert Event.objects.count() == 0

    def test_post_blank_rule_creates_one_off(self, client):
        """Blank rule must NOT route through the recurring service
        (which would error on the empty rule)."""
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_view_r3", display_name="Todd",
            password_hash="x", email="todd_view_r3@example.com",
        )
        self._login_as(client, "todd_view_r3@example.com")
        client.post("/events/schedule/", {
            "event_type": "one_on_one",
            "scheduled_date": (date.today() + timedelta(days=1)).isoformat(),
            "scheduled_time": "11:00",
            "duration_minutes": "30",
            "recurrence_rule": "",
        })
        events = Event.objects.for_manager(m.id)
        assert events.count() == 1
        assert events.first().recurrence_rule in (None, "")


# ============================================================
# Phase 6 D1+D2 — events_detail + events_edit
# ============================================================


@pytest.mark.django_db
class TestEventsDetail:
    """GET /events/<id>/ — canonical URL for the Outlook-link contract."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_d1", display_name="Todd",
            password_hash="x", email="todd_d1@example.com",
        )
        self._login_as(client, "todd_d1@example.com")
        ev = Event.objects.create(
            manager_id=m.id, title="My Quarterly", event_type="quarterly_review",
            scheduled_date=(date.today() + timedelta(days=7)).isoformat(),
            scheduled_time="10:00", status="scheduled",
            agenda="Discuss roadmap", location="Zoom",
        )
        return m, ev

    def test_anonymous_redirects(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="x", display_name="X", password_hash="x",
            email="x@example.com",
        )
        ev = Event.objects.create(
            manager_id=m.id, title="X", event_type="other",
            scheduled_date=(date.today() + timedelta(days=1)).isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        resp = client.get(f"/events/{ev.id}/")
        assert resp.status_code == 302
        assert "/accounts/google/login/" in resp["Location"]

    def test_renders_event_with_copy_link_button(self, client):
        m, ev = self._setup(client)
        resp = client.get(f"/events/{ev.id}/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "My Quarterly" in body
        assert "Discuss roadmap" in body
        # The copy-link button contains the absolute URL for Outlook paste
        assert "Copy link for Outlook" in body
        assert f"/events/{ev.id}/" in body

    def test_cross_tenant_returns_404(self, client):
        from datetime import date
        m, _ = self._setup(client)  # Todd
        m2 = Manager.objects.create(
            username="other_d1", display_name="Other",
            password_hash="x", email="other_d1@example.com",
        )
        other = Event.objects.create(
            manager_id=m2.id, title="Other", event_type="other",
            scheduled_date=date.today().isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        resp = client.get(f"/events/{other.id}/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestEventsEdit:
    """GET/POST /events/<id>/edit/ — D1 resolution per D2 contract."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_d2", display_name="Todd",
            password_hash="x", email="todd_d2@example.com",
        )
        self._login_as(client, "todd_d2@example.com")
        ev = Event.objects.create(
            manager_id=m.id, title="orig title", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=7)).isoformat(),
            scheduled_time="10:00", status="scheduled",
            duration_minutes=30, location="orig location",
            agenda="orig agenda",
        )
        return m, ev

    def test_get_renders_form_with_warning_when_recurring(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_d2r", display_name="Todd",
            password_hash="x", email="todd_d2r@example.com",
        )
        self._login_as(client, "todd_d2r@example.com")
        # Series child
        parent = Event.objects.create(
            manager_id=m.id, title="series", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=1)).isoformat(),
            scheduled_time="10:00", status="scheduled",
            recurrence_rule="weekly",
        )
        child = Event.objects.create(
            manager_id=m.id, title="series", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=8)).isoformat(),
            scheduled_time="10:00", status="scheduled",
            recurrence_rule="weekly", parent_event=parent,
        )
        body = client.get(f"/events/{child.id}/edit/").content.decode()
        assert "recurring series" in body
        assert "siblings are unaffected" in body

    def test_post_updates_title_agenda_etc(self, client):
        from datetime import date, timedelta
        m, ev = self._setup(client)
        resp = client.post(f"/events/{ev.id}/edit/", {
            "event_type": ev.event_type,
            "title": "new title",
            "scheduled_date": (date.today() + timedelta(days=7)).isoformat(),
            "scheduled_time": ev.scheduled_time,
            "duration_minutes": "45",
            "location": "new location",
            "agenda": "new agenda",
        })
        assert resp.status_code == 302
        assert resp["Location"].endswith(f"/events/{ev.id}/")
        ev.refresh_from_db()
        assert ev.title == "new title"
        assert ev.agenda == "new agenda"
        assert ev.location == "new location"
        assert ev.duration_minutes == 45

    def test_post_updates_date_and_time(self, client):
        """Per D2 contract: date/time IS editable (with the warning),
        not immutable."""
        from datetime import date, timedelta
        m, ev = self._setup(client)
        new_date = (date.today() + timedelta(days=14)).isoformat()
        client.post(f"/events/{ev.id}/edit/", {
            "event_type": ev.event_type,
            "title": ev.title,
            "scheduled_date": new_date,
            "scheduled_time": "14:30",
            "duration_minutes": str(ev.duration_minutes),
            "location": ev.location,
            "agenda": ev.agenda,
        })
        ev.refresh_from_db()
        assert ev.scheduled_date == new_date
        assert ev.scheduled_time == "14:30"

    def test_edit_does_not_propagate_to_recurring_siblings(self, client):
        """CLAUDE.md: editing one occurrence does NOT propagate to
        siblings. The edit view operates on the single row."""
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_d2s", display_name="Todd",
            password_hash="x", email="todd_d2s@example.com",
        )
        self._login_as(client, "todd_d2s@example.com")
        parent = Event.objects.create(
            manager_id=m.id, title="series", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=1)).isoformat(),
            scheduled_time="10:00", status="scheduled",
            recurrence_rule="weekly",
        )
        child_a = Event.objects.create(
            manager_id=m.id, title="series", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=8)).isoformat(),
            scheduled_time="10:00", status="scheduled",
            recurrence_rule="weekly", parent_event=parent,
        )
        child_b = Event.objects.create(
            manager_id=m.id, title="series", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=15)).isoformat(),
            scheduled_time="10:00", status="scheduled",
            recurrence_rule="weekly", parent_event=parent,
        )

        # Edit child_a's title to something different
        client.post(f"/events/{child_a.id}/edit/", {
            "event_type": "one_on_one",
            "title": "child_a only",
            "scheduled_date": child_a.scheduled_date,
            "scheduled_time": child_a.scheduled_time,
            "duration_minutes": "30",
        })

        child_a.refresh_from_db()
        child_b.refresh_from_db()
        parent.refresh_from_db()
        assert child_a.title == "child_a only"
        assert child_b.title == "series"  # unchanged
        assert parent.title == "series"   # unchanged

    def test_cross_tenant_returns_404(self, client):
        from datetime import date
        m, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_d2", display_name="Other",
            password_hash="x", email="other_d2@example.com",
        )
        other = Event.objects.create(
            manager_id=m2.id, title="Other", event_type="other",
            scheduled_date=date.today().isoformat(),
            scheduled_time="10:00", status="scheduled",
        )
        assert client.get(f"/events/{other.id}/edit/").status_code == 404
        assert client.post(f"/events/{other.id}/edit/", {}).status_code == 404


# ============================================================
# Phase 5.3 — Action items / To Do
# ============================================================


@pytest.mark.django_db
class TestTodosList:
    """GET /todos/ — pending + completed sections, overdue indicator."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_t1", display_name="Todd",
            password_hash="x", email="todd_t1@example.com",
        )
        self._login_as(client, "todd_t1@example.com")
        return m

    def test_anonymous_redirects(self, client):
        resp = client.get("/todos/")
        assert resp.status_code == 302
        assert "/accounts/google/login/" in resp["Location"]

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger@example.com")
        assert client.get("/todos/").status_code == 403

    def test_empty_state(self, client):
        m = self._setup(client)
        body = client.get("/todos/").content.decode()
        assert "caught up" in body.lower()

    def test_lists_only_own_pending(self, client):
        from core.models import ActionItem
        m1 = self._setup(client)
        m2 = Manager.objects.create(
            username="other_t1", display_name="Other",
            password_hash="x", email="other_t1@example.com",
        )
        ActionItem.objects.create(description="Mine pending", manager_id=m1.id, status="pending")
        ActionItem.objects.create(description="Other pending", manager_id=m2.id, status="pending")
        body = client.get("/todos/").content.decode()
        assert "Mine pending" in body
        assert "Other pending" not in body

    def test_completed_appears_in_recently_completed(self, client):
        from datetime import timedelta
        from django.utils import timezone
        from core.models import ActionItem
        m = self._setup(client)
        ActionItem.objects.create(
            description="DoneOne", manager_id=m.id,
            status="completed", completed_at=timezone.now() - timedelta(hours=1),
        )
        body = client.get("/todos/").content.decode()
        assert "Recently completed" in body
        assert "DoneOne" in body

    def test_overdue_marker(self, client):
        from datetime import timedelta, date
        from core.models import ActionItem
        m = self._setup(client)
        past = (date.today() - timedelta(days=2)).isoformat()
        ActionItem.objects.create(
            description="LateThing", manager_id=m.id,
            status="pending", due_date=past,
        )
        body = client.get("/todos/").content.decode()
        assert "LateThing" in body
        assert "overdue" in body.lower()


@pytest.mark.django_db
class TestTodosAdd:
    """POST /todos/add/ — HTMX endpoint."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_t2", display_name="Todd",
            password_hash="x", email="todd_t2@example.com",
        )
        self._login_as(client, "todd_t2@example.com")
        return m

    def test_get_not_allowed(self, client):
        self._setup(client)
        assert client.get("/todos/add/").status_code == 405

    def test_create_persists_with_correct_manager_id(self, client):
        from core.models import ActionItem
        m = self._setup(client)
        resp = client.post("/todos/add/", {
            "description": "Send the report",
            "due_date": "2026-06-01",
            "due_time": "15:00",
        })
        assert resp.status_code == 200
        items = ActionItem.objects.for_manager(m.id)
        assert items.count() == 1
        i = items.first()
        assert i.description == "Send the report"
        assert i.due_date == "2026-06-01"  # iso text
        assert i.due_time == "15:00"
        assert i.status == "pending"
        assert i.manager_id == m.id

    def test_create_with_no_due_time_stores_null(self, client):
        from core.models import ActionItem
        m = self._setup(client)
        client.post("/todos/add/", {
            "description": "No specific time",
            "due_date": "2026-06-02",
            "due_time": "",  # "(no time)" sentinel
        })
        i = ActionItem.objects.for_manager(m.id).first()
        assert i.due_time is None

    def test_assignee_field_is_not_in_form(self, client):
        """Phase 5.3.1 — assignee was removed; ensure the form HTML
        no longer contains an assignee input."""
        self._setup(client)
        body = client.get("/todos/").content.decode()
        assert 'name="assignee"' not in body
        # The Due time field IS present
        assert 'name="due_time"' in body

    def test_blank_description_is_validation_error(self, client):
        from core.models import ActionItem
        self._setup(client)
        resp = client.post("/todos/add/", {"description": ""})
        assert resp.status_code == 422
        assert ActionItem.objects.count() == 0

    def test_other_managers_data_unaffected(self, client):
        from core.models import ActionItem
        m1 = self._setup(client)
        m2 = Manager.objects.create(
            username="other_t2", display_name="Other",
            password_hash="x", email="other_t2@example.com",
        )
        ActionItem.objects.create(description="Other had this", manager_id=m2.id, status="pending")
        client.post("/todos/add/", {"description": "Mine"})
        assert ActionItem.objects.for_manager(m1.id).count() == 1
        assert ActionItem.objects.for_manager(m2.id).count() == 1

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger_t@example.com")
        resp = client.post("/todos/add/", {"description": "X"})
        assert resp.status_code == 403


@pytest.mark.django_db
class TestTodosCompleteUncomplete:
    """POST /todos/<id>/complete/ + /todos/<id>/uncomplete/"""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        from core.models import ActionItem
        m = Manager.objects.create(
            username="todd_t3", display_name="Todd",
            password_hash="x", email="todd_t3@example.com",
        )
        self._login_as(client, "todd_t3@example.com")
        ai = ActionItem.objects.create(
            description="x", manager_id=m.id, status="pending",
        )
        return m, ai

    def test_complete_sets_status_and_completed_at(self, client):
        m, ai = self._setup(client)
        resp = client.post(f"/todos/{ai.id}/complete/")
        assert resp.status_code == 200
        ai.refresh_from_db()
        assert ai.status == "completed"
        assert ai.completed_at is not None

    def test_complete_already_completed_returns_404(self, client):
        m, ai = self._setup(client)
        ai.status = "completed"
        ai.save()
        resp = client.post(f"/todos/{ai.id}/complete/")
        assert resp.status_code == 404

    def test_complete_cross_tenant_returns_404(self, client):
        from core.models import ActionItem
        m, _ = self._setup(client)  # Todd
        m2 = Manager.objects.create(
            username="other_t3", display_name="Other",
            password_hash="x", email="other_t3@example.com",
        )
        other = ActionItem.objects.create(
            description="other", manager_id=m2.id, status="pending",
        )
        resp = client.post(f"/todos/{other.id}/complete/")
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.status == "pending"

    def test_uncomplete_reverts_to_pending(self, client):
        from django.utils import timezone
        m, ai = self._setup(client)
        ai.status = "completed"
        ai.completed_at = timezone.now()
        ai.save()
        resp = client.post(f"/todos/{ai.id}/uncomplete/")
        assert resp.status_code == 200
        ai.refresh_from_db()
        assert ai.status == "pending"
        assert ai.completed_at is None

    def test_uncomplete_already_pending_returns_404(self, client):
        m, ai = self._setup(client)  # ai is already pending
        resp = client.post(f"/todos/{ai.id}/uncomplete/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestTodosDelete:
    """DELETE /todos/<id>/delete/ — hard delete."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def test_delete_removes_row(self, client):
        from core.models import ActionItem
        m = Manager.objects.create(
            username="todd_t4", display_name="Todd",
            password_hash="x", email="todd_t4@example.com",
        )
        self._login_as(client, "todd_t4@example.com")
        ai = ActionItem.objects.create(description="X", manager_id=m.id)
        resp = client.delete(f"/todos/{ai.id}/delete/")
        assert resp.status_code == 200
        assert not ActionItem.objects.filter(pk=ai.id).exists()

    def test_delete_cross_tenant_returns_404(self, client):
        from core.models import ActionItem
        m1 = Manager.objects.create(
            username="todd_t4b", display_name="Todd",
            password_hash="x", email="todd_t4b@example.com",
        )
        self._login_as(client, "todd_t4b@example.com")
        m2 = Manager.objects.create(
            username="other_t4", display_name="Other",
            password_hash="x", email="other_t4@example.com",
        )
        other = ActionItem.objects.create(description="other", manager_id=m2.id)
        resp = client.delete(f"/todos/{other.id}/delete/")
        assert resp.status_code == 404
        assert ActionItem.objects.filter(pk=other.id).exists()


# ============================================================
# Phase 5.4 — Journal entries
# ============================================================


@pytest.mark.django_db
class TestJournalList:
    """GET /journal/ — main journal page with today's form + history."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_j1", display_name="Todd",
            password_hash="x", email="todd_j1@example.com",
        )
        self._login_as(client, "todd_j1@example.com")
        return m

    def test_page_loads_with_empty_form(self, client):
        self._setup(client)
        resp = client.get("/journal/")
        assert resp.status_code == 200
        assert b"Manager Journal" in resp.content

    def test_page_shows_existing_entries(self, client):
        m = self._setup(client)
        JournalEntry.objects.create(
            entry_date="2026-05-08", entry_type="daily",
            content="Had a great 1:1", manager_id=m.id,
        )
        resp = client.get("/journal/")
        assert resp.status_code == 200
        assert b"Had a great 1:1" in resp.content

    def test_today_entry_prefills_form(self, client):
        from datetime import date
        m = self._setup(client)
        today = date.today().isoformat()
        JournalEntry.objects.create(
            entry_date=today, entry_type="daily",
            content="Morning thoughts", mood=4, energy=3,
            manager_id=m.id,
        )
        resp = client.get("/journal/")
        assert resp.status_code == 200
        assert b"Morning thoughts" in resp.content
        # Form should show "Edit today's entry" not "Write today's entry"
        assert b"Edit today" in resp.content

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger_j@example.com")
        resp = client.get("/journal/")
        assert resp.status_code == 403

    def test_cross_manager_entries_not_visible(self, client):
        m1 = self._setup(client)
        m2 = Manager.objects.create(
            username="other_j1", display_name="Other",
            password_hash="x", email="other_j1@example.com",
        )
        JournalEntry.objects.create(
            entry_date="2026-05-08", entry_type="daily",
            content="Secret thoughts", manager_id=m2.id,
        )
        resp = client.get("/journal/")
        assert b"Secret thoughts" not in resp.content


@pytest.mark.django_db
class TestJournalAdd:
    """POST /journal/add/ — create or update a journal entry."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_j2", display_name="Todd",
            password_hash="x", email="todd_j2@example.com",
        )
        self._login_as(client, "todd_j2@example.com")
        return m

    def test_get_not_allowed(self, client):
        self._setup(client)
        assert client.get("/journal/add/").status_code == 405

    def test_create_persists_with_correct_manager_id(self, client):
        m = self._setup(client)
        resp = client.post("/journal/add/", {
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "Delegated the Q3 report",
            "mood": "4",
            "energy": "3",
            "tags": "delegation, empowerment",
        })
        assert resp.status_code == 200
        entries = JournalEntry.objects.for_manager(m.id)
        assert entries.count() == 1
        e = entries.first()
        assert e.entry_date == "2026-05-09"
        assert e.entry_type == "daily"
        assert e.content == "Delegated the Q3 report"
        assert e.mood == 4
        assert e.energy == 3
        assert e.tags == "delegation, empowerment"
        assert e.manager_id == m.id
        assert e.created_at is not None

    def test_create_with_no_mood_energy_stores_null(self, client):
        m = self._setup(client)
        client.post("/journal/add/", {
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "Quick note",
            "mood": "",
            "energy": "",
        })
        e = JournalEntry.objects.for_manager(m.id).first()
        assert e.mood is None
        assert e.energy is None

    def test_update_existing_entry(self, client):
        m = self._setup(client)
        entry = JournalEntry.objects.create(
            entry_date="2026-05-09", entry_type="daily",
            content="Original", manager_id=m.id,
        )
        resp = client.post("/journal/add/", {
            "existing_id": str(entry.id),
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "Updated thoughts",
            "mood": "5",
            "energy": "4",
        })
        assert resp.status_code == 200
        entry.refresh_from_db()
        assert entry.content == "Updated thoughts"
        assert entry.mood == 5
        assert entry.energy == 4
        assert entry.updated_at is not None
        # No duplicate created
        assert JournalEntry.objects.for_manager(m.id).count() == 1

    def test_update_cross_tenant_ignored(self, client):
        """Passing another manager's entry_id should create a new entry
        for the logged-in manager, not update the other manager's."""
        m1 = self._setup(client)
        m2 = Manager.objects.create(
            username="other_j2", display_name="Other",
            password_hash="x", email="other_j2@example.com",
        )
        other_entry = JournalEntry.objects.create(
            entry_date="2026-05-09", entry_type="daily",
            content="Other's secret", manager_id=m2.id,
        )
        resp = client.post("/journal/add/", {
            "existing_id": str(other_entry.id),
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "My entry",
        })
        assert resp.status_code == 200
        other_entry.refresh_from_db()
        assert other_entry.content == "Other's secret"  # unchanged
        # A new entry was created for m1
        assert JournalEntry.objects.for_manager(m1.id).count() == 1

    def test_missing_date_is_validation_error(self, client):
        self._setup(client)
        resp = client.post("/journal/add/", {
            "entry_type": "daily",
            "content": "No date",
        })
        assert resp.status_code == 422
        assert JournalEntry.objects.count() == 0

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger_j2@example.com")
        resp = client.post("/journal/add/", {
            "entry_date": "2026-05-09",
            "entry_type": "daily",
        })
        assert resp.status_code == 403

    def test_other_managers_data_unaffected(self, client):
        m1 = self._setup(client)
        m2 = Manager.objects.create(
            username="other_j2b", display_name="Other",
            password_hash="x", email="other_j2b@example.com",
        )
        JournalEntry.objects.create(
            entry_date="2026-05-08", entry_type="daily",
            content="Other's entry", manager_id=m2.id,
        )
        client.post("/journal/add/", {
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "Mine",
        })
        assert JournalEntry.objects.for_manager(m1.id).count() == 1
        assert JournalEntry.objects.for_manager(m2.id).count() == 1


@pytest.mark.django_db
class TestJournalEdit:
    """GET/POST /journal/<id>/edit/ — edit a past journal entry."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_j3", display_name="Todd",
            password_hash="x", email="todd_j3@example.com",
        )
        self._login_as(client, "todd_j3@example.com")
        entry = JournalEntry.objects.create(
            entry_date="2026-05-05", entry_type="daily",
            content="Old thoughts", mood=3, energy=2,
            manager_id=m.id,
        )
        return m, entry

    def test_get_loads_form_with_existing_data(self, client):
        _, entry = self._setup(client)
        resp = client.get(f"/journal/{entry.id}/edit/")
        assert resp.status_code == 200
        assert b"Old thoughts" in resp.content

    def test_post_updates_entry_and_redirects(self, client):
        _, entry = self._setup(client)
        resp = client.post(f"/journal/{entry.id}/edit/", {
            "entry_date": "2026-05-05",
            "entry_type": "daily",
            "content": "Revised thoughts",
            "mood": "5",
            "energy": "4",
        })
        assert resp.status_code == 302
        assert resp.url == "/journal/"
        entry.refresh_from_db()
        assert entry.content == "Revised thoughts"
        assert entry.mood == 5

    def test_cross_tenant_edit_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_j3", display_name="Other",
            password_hash="x", email="other_j3@example.com",
        )
        other_entry = JournalEntry.objects.create(
            entry_date="2026-05-05", entry_type="daily",
            content="Secret", manager_id=m2.id,
        )
        resp = client.get(f"/journal/{other_entry.id}/edit/")
        assert resp.status_code == 404

    def test_edit_post_cross_tenant_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_j3b", display_name="Other",
            password_hash="x", email="other_j3b@example.com",
        )
        other_entry = JournalEntry.objects.create(
            entry_date="2026-05-05", entry_type="daily",
            content="Secret", manager_id=m2.id,
        )
        resp = client.post(f"/journal/{other_entry.id}/edit/", {
            "entry_date": "2026-05-05",
            "entry_type": "daily",
            "content": "Hacked",
        })
        assert resp.status_code == 404
        other_entry.refresh_from_db()
        assert other_entry.content == "Secret"


@pytest.mark.django_db
class TestJournalStreak:
    """Streak calculation in journal_list view."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_j4", display_name="Todd",
            password_hash="x", email="todd_j4@example.com",
        )
        self._login_as(client, "todd_j4@example.com")
        return m

    def test_no_entries_shows_no_streak(self, client):
        self._setup(client)
        resp = client.get("/journal/")
        assert b"-day streak" not in resp.content

    def test_today_entry_shows_1_day_streak(self, client):
        from datetime import date
        m = self._setup(client)
        JournalEntry.objects.create(
            entry_date=date.today().isoformat(), entry_type="daily",
            manager_id=m.id,
        )
        resp = client.get("/journal/")
        assert b"1-day streak" in resp.content

    def test_consecutive_days_counted(self, client):
        from datetime import date, timedelta
        m = self._setup(client)
        today = date.today()
        for i in range(3):
            JournalEntry.objects.create(
                entry_date=(today - timedelta(days=i)).isoformat(),
                entry_type="daily", manager_id=m.id,
            )
        resp = client.get("/journal/")
        assert b"3-day streak" in resp.content

    def test_gap_breaks_streak(self, client):
        from datetime import date, timedelta
        m = self._setup(client)
        today = date.today()
        JournalEntry.objects.create(
            entry_date=today.isoformat(), entry_type="daily",
            manager_id=m.id,
        )
        # Skip yesterday, add day before yesterday
        JournalEntry.objects.create(
            entry_date=(today - timedelta(days=2)).isoformat(),
            entry_type="daily", manager_id=m.id,
        )
        resp = client.get("/journal/")
        assert b"1-day streak" in resp.content


# ============================================================
# Phase 5.5 — Goals
# ============================================================


@pytest.mark.django_db
class TestGoalsList:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_g1", display_name="Todd",
            password_hash="x", email="todd_g1@example.com",
        )
        self._login_as(client, "todd_g1@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_page_loads(self, client):
        self._setup(client)
        resp = client.get("/goals/")
        assert resp.status_code == 200
        assert b"Goals" in resp.content

    def test_shows_goals_for_manager(self, client):
        m, tm = self._setup(client)
        Goal.objects.create(
            team_member=tm, quarter="Q2 2026", description="Ship feature",
            manager_id=m.id, status="in_progress",
        )
        resp = client.get("/goals/")
        assert b"Ship feature" in resp.content

    def test_cross_tenant_goals_hidden(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_g1", display_name="Other",
            password_hash="x", email="other_g1@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        Goal.objects.create(
            team_member=tm2, quarter="Q2 2026", description="Secret goal",
            manager_id=m2.id,
        )
        resp = client.get("/goals/")
        assert b"Secret goal" not in resp.content


@pytest.mark.django_db
class TestGoalsAdd:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_g2", display_name="Todd",
            password_hash="x", email="todd_g2@example.com",
        )
        self._login_as(client, "todd_g2@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_create_persists(self, client):
        m, tm = self._setup(client)
        resp = client.post("/goals/add/", {
            "team_member": tm.id,
            "quarter": "Q2 2026",
            "description": "Improve code review",
            "key_results": "Review 3 PRs/week",
            "status": "not_started",
        })
        assert resp.status_code == 200
        goals = Goal.objects.for_manager(m.id)
        assert goals.count() == 1
        g = goals.first()
        assert g.description == "Improve code review"
        assert g.manager_id == m.id

    def test_missing_description_returns_422(self, client):
        m, tm = self._setup(client)
        resp = client.post("/goals/add/", {
            "team_member": tm.id,
            "quarter": "Q2 2026",
            "status": "not_started",
        })
        assert resp.status_code == 422

    def test_get_not_allowed(self, client):
        self._setup(client)
        assert client.get("/goals/add/").status_code == 405


@pytest.mark.django_db
class TestGoalsEdit:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_g3", display_name="Todd",
            password_hash="x", email="todd_g3@example.com",
        )
        self._login_as(client, "todd_g3@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        g = Goal.objects.create(
            team_member=tm, quarter="Q2 2026", description="Original",
            manager_id=m.id, status="not_started",
        )
        return m, tm, g

    def test_get_loads_form(self, client):
        _, _, g = self._setup(client)
        resp = client.get(f"/goals/{g.id}/edit/")
        assert resp.status_code == 200
        assert b"Original" in resp.content

    def test_post_updates_and_redirects(self, client):
        _, tm, g = self._setup(client)
        resp = client.post(f"/goals/{g.id}/edit/", {
            "team_member": tm.id,
            "quarter": "Q2 2026",
            "description": "Updated goal",
            "status": "met",
        })
        assert resp.status_code == 302
        g.refresh_from_db()
        assert g.description == "Updated goal"
        assert g.status == "met"

    def test_cross_tenant_returns_404(self, client):
        m1, _, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_g3", display_name="Other",
            password_hash="x", email="other_g3@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        other = Goal.objects.create(
            team_member=tm2, quarter="Q1", description="Secret",
            manager_id=m2.id,
        )
        assert client.get(f"/goals/{other.id}/edit/").status_code == 404


@pytest.mark.django_db
class TestGoalsDelete:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def test_delete_removes_row(self, client):
        m = Manager.objects.create(
            username="todd_g4", display_name="Todd",
            password_hash="x", email="todd_g4@example.com",
        )
        self._login_as(client, "todd_g4@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        g = Goal.objects.create(
            team_member=tm, quarter="Q1", description="X",
            manager_id=m.id,
        )
        resp = client.delete(f"/goals/{g.id}/delete/")
        assert resp.status_code == 200
        assert not Goal.objects.filter(pk=g.id).exists()

    def test_cross_tenant_returns_404(self, client):
        m1 = Manager.objects.create(
            username="todd_g4b", display_name="Todd",
            password_hash="x", email="todd_g4b@example.com",
        )
        self._login_as(client, "todd_g4b@example.com")
        m2 = Manager.objects.create(
            username="other_g4", display_name="Other",
            password_hash="x", email="other_g4@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        other = Goal.objects.create(
            team_member=tm2, quarter="Q1", description="X",
            manager_id=m2.id,
        )
        assert client.delete(f"/goals/{other.id}/delete/").status_code == 404
        assert Goal.objects.filter(pk=other.id).exists()


# ============================================================
# Phase 5.5 — Career Development
# ============================================================


@pytest.mark.django_db
class TestCareerDevPage:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_c1", display_name="Todd",
            password_hash="x", email="todd_c1@example.com",
        )
        self._login_as(client, "todd_c1@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_page_loads(self, client):
        self._setup(client)
        resp = client.get("/career/")
        assert resp.status_code == 200
        assert b"Career Development" in resp.content

    def test_shows_skills(self, client):
        m, tm = self._setup(client)
        Skill.objects.create(
            team_member=tm, skill_name="Public speaking",
            proficiency="developing", manager_id=m.id,
        )
        resp = client.get("/career/")
        assert b"Public speaking" in resp.content

    def test_shows_plans_with_milestones(self, client):
        m, tm = self._setup(client)
        plan = DevelopmentPlan.objects.create(
            team_member=tm, title="Leadership track",
            manager_id=m.id, status="active",
        )
        Milestone.objects.create(
            plan=plan, description="Complete course",
            manager_id=m.id, completed=0,
        )
        resp = client.get("/career/")
        assert b"Leadership track" in resp.content
        assert b"Complete course" in resp.content

    def test_cross_tenant_data_hidden(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_c1", display_name="Other",
            password_hash="x", email="other_c1@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        Skill.objects.create(
            team_member=tm2, skill_name="Secret skill",
            manager_id=m2.id,
        )
        resp = client.get("/career/")
        assert b"Secret skill" not in resp.content


@pytest.mark.django_db
class TestSkillsAdd:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_s1", display_name="Todd",
            password_hash="x", email="todd_s1@example.com",
        )
        self._login_as(client, "todd_s1@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_create_persists(self, client):
        m, tm = self._setup(client)
        resp = client.post("/career/skills/add/", {
            "team_member": tm.id,
            "skill_name": "Python",
            "proficiency": "proficient",
            "is_strength": "on",
        })
        assert resp.status_code == 302
        skills = Skill.objects.for_manager(m.id)
        assert skills.count() == 1
        s = skills.first()
        assert s.skill_name == "Python"
        assert s.is_strength == 1
        assert s.is_growth_area == 0

    def test_delete_removes(self, client):
        m, tm = self._setup(client)
        s = Skill.objects.create(
            team_member=tm, skill_name="X", manager_id=m.id,
        )
        resp = client.delete(f"/career/skills/{s.id}/delete/")
        assert resp.status_code == 200
        assert not Skill.objects.filter(pk=s.id).exists()

    def test_cross_tenant_delete_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_s1", display_name="Other",
            password_hash="x", email="other_s1@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        s = Skill.objects.create(
            team_member=tm2, skill_name="Secret", manager_id=m2.id,
        )
        resp = client.delete(f"/career/skills/{s.id}/delete/")
        assert resp.status_code == 404
        assert Skill.objects.filter(pk=s.id).exists()


@pytest.mark.django_db
class TestPlansAndMilestones:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_p1", display_name="Todd",
            password_hash="x", email="todd_p1@example.com",
        )
        self._login_as(client, "todd_p1@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_create_plan(self, client):
        m, tm = self._setup(client)
        resp = client.post("/career/plans/add/", {
            "team_member": tm.id,
            "title": "Leadership",
            "status": "active",
        })
        assert resp.status_code == 302
        assert DevelopmentPlan.objects.for_manager(m.id).count() == 1

    def test_update_plan_status(self, client):
        m, tm = self._setup(client)
        plan = DevelopmentPlan.objects.create(
            team_member=tm, title="X", manager_id=m.id, status="active",
        )
        resp = client.post(f"/career/plans/{plan.id}/status/", {"status": "completed"})
        assert resp.status_code == 302
        plan.refresh_from_db()
        assert plan.status == "completed"

    def test_add_milestone(self, client):
        m, tm = self._setup(client)
        plan = DevelopmentPlan.objects.create(
            team_member=tm, title="X", manager_id=m.id, status="active",
        )
        resp = client.post(f"/career/plans/{plan.id}/milestones/add/", {
            "description": "Read book",
        })
        assert resp.status_code == 302
        assert Milestone.objects.for_manager(m.id).count() == 1
        ms = Milestone.objects.first()
        assert ms.completed == 0

    def test_complete_milestone(self, client):
        m, tm = self._setup(client)
        plan = DevelopmentPlan.objects.create(
            team_member=tm, title="X", manager_id=m.id, status="active",
        )
        ms = Milestone.objects.create(
            plan=plan, description="Read book",
            manager_id=m.id, completed=0,
        )
        resp = client.post(f"/career/milestones/{ms.id}/complete/")
        assert resp.status_code == 302
        ms.refresh_from_db()
        assert ms.completed == 1
        assert ms.completed_at is not None

    def test_cross_tenant_plan_status_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_p1", display_name="Other",
            password_hash="x", email="other_p1@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        plan = DevelopmentPlan.objects.create(
            team_member=tm2, title="X", manager_id=m2.id, status="active",
        )
        resp = client.post(f"/career/plans/{plan.id}/status/", {"status": "completed"})
        assert resp.status_code == 404
        plan.refresh_from_db()
        assert plan.status == "active"

    def test_cross_tenant_milestone_add_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_p1b", display_name="Other",
            password_hash="x", email="other_p1b@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        plan = DevelopmentPlan.objects.create(
            team_member=tm2, title="X", manager_id=m2.id, status="active",
        )
        resp = client.post(f"/career/plans/{plan.id}/milestones/add/", {
            "description": "Hacked milestone",
        })
        assert resp.status_code == 404
        assert Milestone.objects.count() == 0

    def test_cross_tenant_milestone_complete_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_p1c", display_name="Other",
            password_hash="x", email="other_p1c@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        plan = DevelopmentPlan.objects.create(
            team_member=tm2, title="X", manager_id=m2.id, status="active",
        )
        ms = Milestone.objects.create(
            plan=plan, description="Other's milestone",
            manager_id=m2.id, completed=0,
        )
        resp = client.post(f"/career/milestones/{ms.id}/complete/")
        assert resp.status_code == 404
        ms.refresh_from_db()
        assert ms.completed == 0


@pytest.mark.django_db
class TestConvosAdd:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_cv1", display_name="Todd",
            password_hash="x", email="todd_cv1@example.com",
        )
        self._login_as(client, "todd_cv1@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_create_persists(self, client):
        m, tm = self._setup(client)
        resp = client.post("/career/convos/add/", {
            "team_member": tm.id,
            "conversation_date": "2026-05-09",
            "topic": "Career path",
            "notes": "Discussed IC vs management",
            "next_steps": "Shadow a team lead",
        })
        assert resp.status_code == 302
        convos = CareerConversation.objects.for_manager(m.id)
        assert convos.count() == 1
        c = convos.first()
        assert c.topic == "Career path"
        assert c.conversation_date == "2026-05-09"


# ============================================================
# Phase 5.6 — Delegations
# ============================================================


@pytest.mark.django_db
class TestDelegations:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_d1", display_name="Todd",
            password_hash="x", email="todd_d1@example.com",
        )
        self._login_as(client, "todd_d1@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_page_loads(self, client):
        self._setup(client)
        assert client.get("/delegations/").status_code == 200

    def test_create_persists(self, client):
        m, tm = self._setup(client)
        resp = client.post("/delegations/add/", {
            "task": "Write the report",
            "team_member": tm.id,
            "outcome_expected": "Draft by Friday",
            "autonomy_level": "guided",
            "status": "active",
        })
        assert resp.status_code == 302
        ds = Delegation.objects.for_manager(m.id)
        assert ds.count() == 1
        assert ds.first().task == "Write the report"
        assert ds.first().manager_id == m.id

    def test_edit_updates(self, client):
        m, tm = self._setup(client)
        d = Delegation.objects.create(
            task="Original", manager_id=m.id, status="active",
            team_member=tm,
        )
        resp = client.post(f"/delegations/{d.id}/edit/", {
            "task": "Updated",
            "team_member": tm.id,
            "autonomy_level": "autonomous",
            "status": "completed",
        })
        assert resp.status_code == 302
        d.refresh_from_db()
        assert d.task == "Updated"
        assert d.status == "completed"
        assert d.completed_at is not None

    def test_delete_removes(self, client):
        m, tm = self._setup(client)
        d = Delegation.objects.create(
            task="X", manager_id=m.id, team_member=tm, status="active",
        )
        resp = client.delete(f"/delegations/{d.id}/delete/")
        assert resp.status_code == 200
        assert not Delegation.objects.filter(pk=d.id).exists()

    def test_cross_tenant_edit_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_d1", display_name="Other",
            password_hash="x", email="other_d1@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        d = Delegation.objects.create(
            task="Secret", manager_id=m2.id, team_member=tm2, status="active",
        )
        assert client.get(f"/delegations/{d.id}/edit/").status_code == 404

    def test_cross_tenant_delete_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_d1b", display_name="Other",
            password_hash="x", email="other_d1b@example.com",
        )
        d = Delegation.objects.create(
            task="Secret", manager_id=m2.id, status="active",
        )
        assert client.delete(f"/delegations/{d.id}/delete/").status_code == 404
        assert Delegation.objects.filter(pk=d.id).exists()

    def test_cross_tenant_list_hidden(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_d1c", display_name="Other",
            password_hash="x", email="other_d1c@example.com",
        )
        Delegation.objects.create(
            task="Secret task", manager_id=m2.id, status="active",
        )
        resp = client.get("/delegations/")
        assert b"Secret task" not in resp.content


# ============================================================
# Phase 5.6 — Decisions
# ============================================================


@pytest.mark.django_db
class TestDecisions:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_dc1", display_name="Todd",
            password_hash="x", email="todd_dc1@example.com",
        )
        self._login_as(client, "todd_dc1@example.com")
        return m

    def test_page_loads(self, client):
        self._setup(client)
        assert client.get("/decisions/").status_code == 200

    def test_create_persists(self, client):
        m = self._setup(client)
        resp = client.post("/decisions/add/", {
            "title": "Switch to Django",
            "rationale": "Better maintainability",
            "status": "active",
        })
        assert resp.status_code == 302
        ds = Decision.objects.for_manager(m.id)
        assert ds.count() == 1
        assert ds.first().title == "Switch to Django"

    def test_edit_updates_with_outcome(self, client):
        m = self._setup(client)
        d = Decision.objects.create(
            title="Original", manager_id=m.id, status="active",
        )
        resp = client.post(f"/decisions/{d.id}/edit/", {
            "title": "Original",
            "status": "validated",
            "actual_outcome": "Shipped faster than expected",
        })
        assert resp.status_code == 302
        d.refresh_from_db()
        assert d.status == "validated"
        assert d.actual_outcome == "Shipped faster than expected"

    def test_delete_removes(self, client):
        m = self._setup(client)
        d = Decision.objects.create(title="X", manager_id=m.id)
        resp = client.delete(f"/decisions/{d.id}/delete/")
        assert resp.status_code == 200
        assert not Decision.objects.filter(pk=d.id).exists()

    def test_cross_tenant_edit_returns_404(self, client):
        m1 = self._setup(client)
        m2 = Manager.objects.create(
            username="other_dc1", display_name="Other",
            password_hash="x", email="other_dc1@example.com",
        )
        d = Decision.objects.create(title="Secret", manager_id=m2.id)
        assert client.get(f"/decisions/{d.id}/edit/").status_code == 404

    def test_cross_tenant_delete_returns_404(self, client):
        m1 = self._setup(client)
        m2 = Manager.objects.create(
            username="other_dc1b", display_name="Other",
            password_hash="x", email="other_dc1b@example.com",
        )
        d = Decision.objects.create(title="Secret", manager_id=m2.id)
        assert client.delete(f"/decisions/{d.id}/delete/").status_code == 404
        assert Decision.objects.filter(pk=d.id).exists()


# ============================================================
# Phase 5.6 — Running Notes (1:1 Notes)
# ============================================================


@pytest.mark.django_db
class TestNotes:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_n1", display_name="Todd",
            password_hash="x", email="todd_n1@example.com",
        )
        self._login_as(client, "todd_n1@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_page_loads(self, client):
        self._setup(client)
        assert client.get("/notes/").status_code == 200

    def test_create_persists(self, client):
        m, tm = self._setup(client)
        resp = client.post("/notes/add/", {
            "team_member": tm.id,
            "note_date": "2026-05-09",
            "content": "Alice did great in the meeting",
            "category": "praise",
        })
        assert resp.status_code == 302
        ns = RunningNote.objects.for_manager(m.id)
        assert ns.count() == 1
        n = ns.first()
        assert n.content == "Alice did great in the meeting"
        assert n.category == "praise"
        assert n.note_date == "2026-05-09"

    def test_broadcast_note_has_null_member(self, client):
        m, _ = self._setup(client)
        resp = client.post("/notes/add/", {
            "note_date": "2026-05-09",
            "content": "Team-wide update",
            "category": "general",
        })
        assert resp.status_code == 302
        n = RunningNote.objects.for_manager(m.id).first()
        assert n.team_member is None

    def test_delete_removes(self, client):
        m, tm = self._setup(client)
        n = RunningNote.objects.create(
            team_member=tm, note_date="2026-05-09",
            content="X", manager_id=m.id,
        )
        resp = client.delete(f"/notes/{n.id}/delete/")
        assert resp.status_code == 200
        assert not RunningNote.objects.filter(pk=n.id).exists()

    def test_cross_tenant_delete_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_n1", display_name="Other",
            password_hash="x", email="other_n1@example.com",
        )
        n = RunningNote.objects.create(
            note_date="2026-05-09", content="Secret",
            manager_id=m2.id,
        )
        assert client.delete(f"/notes/{n.id}/delete/").status_code == 404
        assert RunningNote.objects.filter(pk=n.id).exists()

    def test_cross_tenant_notes_hidden(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_n1b", display_name="Other",
            password_hash="x", email="other_n1b@example.com",
        )
        RunningNote.objects.create(
            note_date="2026-05-09", content="Secret observation",
            manager_id=m2.id,
        )
        resp = client.get("/notes/")
        assert b"Secret observation" not in resp.content

    def test_member_filter_includes_broadcasts(self, client):
        m, tm = self._setup(client)
        RunningNote.objects.create(
            team_member=tm, note_date="2026-05-09",
            content="Alice specific", manager_id=m.id,
        )
        RunningNote.objects.create(
            note_date="2026-05-09", content="Broadcast note",
            manager_id=m.id,
        )
        resp = client.get(f"/notes/?member={tm.id}")
        assert b"Alice specific" in resp.content
        assert b"Broadcast note" in resp.content
