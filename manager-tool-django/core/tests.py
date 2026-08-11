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
from django.test import override_settings

from coaching.models import CoachSuggestion
from core.models import (
    ActionItem,
    AuditLog,
    CareerConversation,
    Decision,
    Delegation,
    DevelopmentPlan,
    Event,
    Feedback,
    Goal,
    JournalEntry,
    Manager,
    Milestone,
    OneOnOneSession,
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
        Manager.objects.create(
            username="todd", display_name="Todd",
            password_hash="x", email="todd@example.com",
        )
        self._login_as(client, "todd@example.com")
        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        body = resp.content.decode()
        # Phase 4: shell renders before HTMX panel loads. The sidebar
        # brand block deliberately does NOT show the manager's name
        # (removed per operator, 2026-07-14); overview panel placeholder
        # is present (the panel itself is fetched separately).
        assert "Todd" not in body
        assert "overview-panel" in body
        assert "/dashboard/panels/overview/" in body

    def test_overview_panel_returns_per_tenant_data(self, client):
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
        # Team Health shows Todd's 2 members, not m2's
        assert "report A" in body
        assert "report B" in body
        assert "other report" not in body

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

    def test_overview_shows_next_actions(self, client):
        from datetime import date, timedelta
        m = Manager.objects.create(
            username="todd_dash3", display_name="Todd",
            password_hash="x", email="todd_dash3@example.com",
        )
        self._login_as(client, "todd_dash3@example.com")
        # Overdue todo should appear as a next action
        ActionItem.objects.create(
            description="Write quarterly report", manager_id=m.id,
            status="pending",
            due_date=(date.today() - timedelta(days=2)).isoformat(),
        )
        resp = client.get("/dashboard/panels/overview/")
        body = resp.content.decode()
        assert "Write quarterly report" in body
        assert "Next actions" in body

    def test_overview_cross_tenant_isolation(self, client):
        from datetime import date
        Manager.objects.create(
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

    def test_overview_renders_daily_coach_card(self, client):
        from datetime import date
        from coaching.models import CoachSuggestion
        m = Manager.objects.create(
            username="todd_coach", display_name="Todd",
            password_hash="x", email="todd_coach@example.com",
        )
        CoachSuggestion.objects.create(
            manager_id=m.id, suggestion_date=date.today().isoformat(),
            tier="rule", suggestion="SEEDED COACH SUGGESTION",
            dismissed=0,
        )
        self._login_as(client, "todd_coach@example.com")
        resp = client.get("/dashboard/panels/overview/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Daily coach" in body
        assert "SEEDED COACH SUGGESTION" in body

    def test_overview_renders_daily_wisdom(self, client):
        Manager.objects.create(
            username="todd_wisdom", display_name="Todd",
            password_hash="x", email="todd_wisdom@example.com",
        )
        self._login_as(client, "todd_wisdom@example.com")
        resp = client.get("/dashboard/panels/overview/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Today's wisdom" in body
        assert "wisdom.number" not in body  # rendered value, not a template var

    def test_overview_renders_onboarding_checklist(self, client):
        Manager.objects.create(
            username="todd_ob1", display_name="Todd",
            password_hash="x", email="todd_ob1@example.com",
        )
        self._login_as(client, "todd_ob1@example.com")
        resp = client.get("/dashboard/panels/overview/")
        body = resp.content.decode()
        assert "Getting started" in body
        assert "Add your first team member" in body
        assert "Write your first journal entry" in body
        assert "Schedule your first 1:1" in body
        assert "Give feedback to your team" in body

    def test_overview_hides_onboarding_checklist_when_complete(self, client):
        from datetime import date
        m = Manager.objects.create(
            username="todd_ob2", display_name="Todd",
            password_hash="x", email="todd_ob2@example.com",
        )
        member = TeamMember.objects.create(name="Report A", manager_id=m.id)
        JournalEntry.objects.create(
            entry_date=date.today().isoformat(), entry_type="daily",
            content="First entry", manager_id=m.id,
        )
        Event.objects.create(
            title="1:1 with Report A", event_type="one_on_one",
            scheduled_date=date.today().isoformat(), scheduled_time="09:00",
            status="scheduled", manager_id=m.id,
        )
        Feedback.objects.create(
            team_member=member, feedback_type="positive", manager_id=m.id,
        )
        self._login_as(client, "todd_ob2@example.com")
        resp = client.get("/dashboard/panels/overview/")
        body = resp.content.decode()
        assert "Getting started" not in body

    def test_dashboard_coach_dismiss_hides_card_for_the_day(self, client):
        from datetime import date
        from coaching.models import CoachSuggestion
        m = Manager.objects.create(
            username="todd_cdismiss", display_name="Todd",
            password_hash="x", email="todd_cdismiss@example.com",
        )
        CoachSuggestion.objects.create(
            manager_id=m.id, suggestion_date=date.today().isoformat(),
            tier="rule", suggestion="SEEDED COACH SUGGESTION",
            dismissed=0,
        )
        self._login_as(client, "todd_cdismiss@example.com")
        # Card shows before dismissal
        assert "SEEDED COACH SUGGESTION" in client.get(
            "/dashboard/panels/overview/").content.decode()
        # "Got it" dismisses today's suggestion
        resp = client.post("/dashboard/coach/dismiss/")
        assert resp.status_code == 200
        assert CoachSuggestion.objects.for_manager(m.id).filter(
            dismissed=1).count() == 1
        # Card no longer shows for the rest of the day (dismiss honored)
        body = client.get("/dashboard/panels/overview/").content.decode()
        assert "SEEDED COACH SUGGESTION" not in body

    def test_logout_invalidates_session_with_setup(self, client):
        Manager.objects.create(
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

    def test_overview_handles_naive_feedback_timestamp(self, client):
        """Regression: feedback.created_at is TIMESTAMP (no tz) on PG, so
        psycopg2 returns naive datetimes. The overview view subtracts
        `timezone.now()` (aware) from it — naive minus aware was raising
        TypeError before the fix."""
        from datetime import datetime
        from core.models import Feedback
        m = Manager.objects.create(
            username="todd_naive", display_name="Todd",
            password_hash="x", email="todd_naive@example.com",
        )
        tm = TeamMember.objects.create(name="report", manager_id=m.id)
        # Force a naive created_at — mirrors what PG returns for the
        # TIMESTAMP column even though SQLite (used by pytest) normally
        # returns aware values.
        fb = Feedback.objects.create(
            team_member=tm, feedback_type="positive",
            situation="ok", manager_id=m.id,
        )
        Feedback.objects.filter(pk=fb.id).update(
            created_at=datetime(2026, 5, 1, 12, 0, 0),  # naive
        )
        self._login_as(client, "todd_naive@example.com")
        resp = client.get("/dashboard/panels/overview/")
        assert resp.status_code == 200


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
        self._setup(client)  # Todd
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

    def test_purge_writes_system_audit_entries(self):
        """Each hard-delete must leave an AuditLog row with actor="system"
        and the member's name/id captured BEFORE the row was deleted.
        Without this, hard-deletes have no trail (the row that records
        the deletion is gone)."""
        from datetime import timedelta
        from django.core.management import call_command
        from django.utils import timezone
        m = Manager.objects.create(
            username="purge_audit", display_name="P",
            password_hash="x", email="purge_audit@example.com",
        )
        a = TeamMember.objects.create(
            name="Alice", manager_id=m.id,
            deleted_at=timezone.now() - timedelta(days=45),
        )
        b = TeamMember.objects.create(
            name="Bob", manager_id=m.id,
            deleted_at=timezone.now() - timedelta(days=45),
        )
        before = AuditLog.objects.filter(manager_id=m.id).count()
        call_command("purge_deleted_team_members")
        after = AuditLog.objects.filter(manager_id=m.id)
        assert after.count() - before == 2, (
            f"Expected 2 new audit rows for 2 purged members; got {after.count() - before}"
        )
        # All purge audit rows are tagged actor="system"
        purge_rows = after.filter(entity_type="TeamMember", action="delete")
        assert purge_rows.count() == 2
        for row in purge_rows:
            assert row.actor_type == "system", (
                f"Cron-driven delete logged as actor={row.actor_type}; "
                f"should be 'system' to keep /audit/?actor=user clean"
            )
        # Names made it into the summary so the trail is readable.
        summaries = [r.summary for r in purge_rows]
        assert any("Alice" in s for s in summaries)
        assert any("Bob" in s for s in summaries)
        # And the audited entity_ids match the deleted rows.
        audited_ids = sorted(r.entity_id for r in purge_rows)
        assert audited_ids == sorted([a.id, b.id])


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
        from datetime import timedelta
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
        self._setup(client)
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
        self._setup(client)
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

    def test_overdue_is_red_bold_without_text_marker(self, client):
        """Overdue dates render red + bold, not with an '(overdue)'
        suffix (To Do overhaul)."""
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
        assert "(overdue)" not in body
        assert "text-red-600 font-bold" in body

    def test_header_title_checkbox_and_date(self, client):
        """Title line: 'To Do — Manager', Show Delegated? checkbox, and
        today's date as MM/DD/YY. The old caption is gone."""
        from datetime import date
        self._setup(client)
        body = client.get("/todos/").content.decode()
        assert "To Do — Manager" in body
        assert "Show Delegated?" in body
        assert date.today().strftime("%m/%d/%y") in body
        assert "work you're holding" not in body

    def test_due_date_renders_mmddyy(self, client):
        from core.models import ActionItem
        m = self._setup(client)
        ActionItem.objects.create(
            description="DatedThing", manager_id=m.id,
            status="pending", due_date="2099-06-01",
        )
        body = client.get("/todos/").content.decode()
        assert "06/01/99" in body
        assert "2099-06-01" not in body

    def test_rows_link_to_edit_page(self, client):
        from core.models import ActionItem
        m = self._setup(client)
        ai = ActionItem.objects.create(
            description="ClickMe", manager_id=m.id, status="pending",
        )
        body = client.get("/todos/").content.decode()
        assert f"/todos/{ai.id}/edit/" in body


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
    """DELETE /todos/<id>/delete/ — soft delete with a 1-day undo
    window (To Do overhaul)."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_t4", display_name="Todd",
            password_hash="x", email="todd_t4@example.com",
        )
        self._login_as(client, "todd_t4@example.com")
        return m

    def test_delete_soft_deletes_row(self, client):
        from core.models import ActionItem
        m = self._setup(client)
        ai = ActionItem.objects.create(
            description="X", manager_id=m.id, status="pending",
        )
        resp = client.delete(f"/todos/{ai.id}/delete/")
        assert resp.status_code == 200
        ai.refresh_from_db()  # row survives, stamped
        assert ai.deleted_at is not None

    def test_deleted_row_leaves_pending_appears_in_recently_deleted(self, client):
        from core.models import ActionItem
        m = self._setup(client)
        ai = ActionItem.objects.create(
            description="GoneSoon", manager_id=m.id, status="pending",
        )
        client.delete(f"/todos/{ai.id}/delete/")
        body = client.get("/todos/").content.decode()
        assert "Recently deleted" in body
        assert f"/todos/{ai.id}/restore/" in body
        # Not in the pending table anymore
        assert f'id="todo-row-{ai.id}"' not in body

    def test_delete_already_deleted_returns_404(self, client):
        from django.utils import timezone
        from core.models import ActionItem
        m = self._setup(client)
        ai = ActionItem.objects.create(
            description="X", manager_id=m.id, status="pending",
            deleted_at=timezone.now(),
        )
        assert client.delete(f"/todos/{ai.id}/delete/").status_code == 404

    def test_delete_cross_tenant_returns_404(self, client):
        from core.models import ActionItem
        self._setup(client)
        m2 = Manager.objects.create(
            username="other_t4", display_name="Other",
            password_hash="x", email="other_t4@example.com",
        )
        other = ActionItem.objects.create(description="other", manager_id=m2.id)
        resp = client.delete(f"/todos/{other.id}/delete/")
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.deleted_at is None

    def test_restore_within_window(self, client):
        from django.utils import timezone
        from core.models import ActionItem
        m = self._setup(client)
        ai = ActionItem.objects.create(
            description="ComeBack", manager_id=m.id, status="pending",
            deleted_at=timezone.now(),
        )
        resp = client.post(f"/todos/{ai.id}/restore/")
        assert resp.status_code == 200
        ai.refresh_from_db()
        assert ai.deleted_at is None
        body = client.get("/todos/").content.decode()
        assert "ComeBack" in body

    def test_restore_after_window_returns_404(self, client):
        from datetime import timedelta
        from django.utils import timezone
        from core.models import ActionItem
        m = self._setup(client)
        ai = ActionItem.objects.create(
            description="TooLate", manager_id=m.id, status="pending",
            deleted_at=timezone.now() - timedelta(days=2),
        )
        assert client.post(f"/todos/{ai.id}/restore/").status_code == 404

    def test_restore_cross_tenant_returns_404(self, client):
        from django.utils import timezone
        from core.models import ActionItem
        self._setup(client)
        m2 = Manager.objects.create(
            username="other_t4r", display_name="Other",
            password_hash="x", email="other_t4r@example.com",
        )
        other = ActionItem.objects.create(
            description="other", manager_id=m2.id, status="pending",
            deleted_at=timezone.now(),
        )
        assert client.post(f"/todos/{other.id}/restore/").status_code == 404

    def test_expired_rows_purged_on_page_load(self, client):
        from datetime import timedelta
        from django.utils import timezone
        from core.models import ActionItem
        m = self._setup(client)
        expired = ActionItem.objects.create(
            description="Expired", manager_id=m.id, status="pending",
            deleted_at=timezone.now() - timedelta(days=2),
        )
        fresh = ActionItem.objects.create(
            description="Fresh", manager_id=m.id, status="pending",
            deleted_at=timezone.now() - timedelta(hours=1),
        )
        client.get("/todos/")
        assert not ActionItem.objects.filter(pk=expired.id).exists()
        assert ActionItem.objects.filter(pk=fresh.id).exists()

    def test_deleted_excluded_from_completed_and_dashboard(self, client):
        """Soft-deleted rows must vanish everywhere: completed section,
        dashboard next-actions, and search."""
        from datetime import date, timedelta
        from django.utils import timezone
        from core.models import ActionItem
        m = self._setup(client)
        past = (date.today() - timedelta(days=3)).isoformat()
        ActionItem.objects.create(
            description="ZombieOverdue", manager_id=m.id, status="pending",
            due_date=past, deleted_at=timezone.now(),
        )
        zombie_done = ActionItem.objects.create(
            description="ZombieDone", manager_id=m.id, status="completed",
            completed_at=timezone.now(), deleted_at=timezone.now(),
        )
        body = client.get("/todos/").content.decode()
        # Gone from Recently completed (it shows under Recently deleted)
        assert f'id="todo-completed-row-{zombie_done.id}"' not in body
        assert f"/todos/{zombie_done.id}/restore/" in body
        assert "ZombieOverdue" not in client.get("/dashboard/panels/overview/").content.decode()
        assert "ZombieOverdue" not in client.get("/search/?q=Zombie").content.decode()


@pytest.mark.django_db
class TestTodosEdit:
    """GET/POST /todos/<id>/edit/ — full-entry page behind row click."""

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
            username="todd_t5", display_name="Todd",
            password_hash="x", email="todd_t5@example.com",
        )
        self._login_as(client, "todd_t5@example.com")
        ai = ActionItem.objects.create(
            description="Edit me", manager_id=m.id, status="pending",
            due_date="2099-01-15",
        )
        return m, ai

    def test_get_renders_full_entry(self, client):
        m, ai = self._setup(client)
        body = client.get(f"/todos/{ai.id}/edit/").content.decode()
        assert "Edit me" in body
        assert 'name="description"' in body

    def test_post_updates_and_redirects(self, client):
        m, ai = self._setup(client)
        resp = client.post(f"/todos/{ai.id}/edit/", {
            "description": "Edited text",
            "due_date": "2099-02-20",
            "due_time": "",
        })
        assert resp.status_code == 302
        assert resp["Location"] == "/todos/"
        ai.refresh_from_db()
        assert ai.description == "Edited text"
        assert ai.due_date == "2099-02-20"

    def test_cross_tenant_returns_404(self, client):
        from core.models import ActionItem
        self._setup(client)
        m2 = Manager.objects.create(
            username="other_t5", display_name="Other",
            password_hash="x", email="other_t5@example.com",
        )
        other = ActionItem.objects.create(
            description="other", manager_id=m2.id, status="pending",
        )
        assert client.get(f"/todos/{other.id}/edit/").status_code == 404

    def test_soft_deleted_returns_404(self, client):
        from django.utils import timezone
        m, ai = self._setup(client)
        ai.deleted_at = timezone.now()
        ai.save()
        assert client.get(f"/todos/{ai.id}/edit/").status_code == 404


@pytest.mark.django_db
class TestTodosDelegate:
    """GET/POST /todos/<id>/delegate/ — promote a to-do to a Delegation."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        from core.models import ActionItem, TeamMember
        m = Manager.objects.create(
            username="todd_t6", display_name="Todd",
            password_hash="x", email="todd_t6@example.com",
        )
        self._login_as(client, "todd_t6@example.com")
        tm = TeamMember.objects.create(name="Pat Direct", manager_id=m.id)
        ai = ActionItem.objects.create(
            description="Hand this off", manager_id=m.id, status="pending",
            due_date="2099-03-01",
        )
        return m, tm, ai

    def test_get_returns_member_picker(self, client):
        m, tm, ai = self._setup(client)
        body = client.get(f"/todos/{ai.id}/delegate/").content.decode()
        assert f'id="delegate-picker-{ai.id}"' in body
        assert "Pat Direct" in body

    def test_post_creates_delegation_and_removes_todo(self, client):
        from core.models import ActionItem, Delegation
        m, tm, ai = self._setup(client)
        resp = client.post(f"/todos/{ai.id}/delegate/", {
            "team_member_id": tm.id,
        })
        assert resp.status_code == 200
        d = Delegation.objects.for_manager(m.id).get()
        assert d.task == "Hand this off"
        assert d.team_member_id == tm.id
        assert d.check_in_date == "2099-03-01"  # due date carries over
        assert d.status == "active"
        assert not ActionItem.objects.filter(pk=ai.id).exists()

    def test_post_with_other_managers_member_returns_404(self, client):
        """Cannot delegate to another manager's team member — and the
        to-do must survive the failed attempt."""
        from core.models import ActionItem, Delegation, TeamMember
        m, tm, ai = self._setup(client)
        m2 = Manager.objects.create(
            username="other_t6", display_name="Other",
            password_hash="x", email="other_t6@example.com",
        )
        foreign = TeamMember.objects.create(name="Foreign", manager_id=m2.id)
        resp = client.post(f"/todos/{ai.id}/delegate/", {
            "team_member_id": foreign.id,
        })
        assert resp.status_code == 404
        assert ActionItem.objects.filter(pk=ai.id).exists()
        assert Delegation.objects.count() == 0

    def test_post_with_garbage_member_id_returns_400(self, client):
        from core.models import ActionItem
        m, tm, ai = self._setup(client)
        resp = client.post(f"/todos/{ai.id}/delegate/", {
            "team_member_id": "not-a-number",
        })
        assert resp.status_code == 400
        assert ActionItem.objects.filter(pk=ai.id).exists()

    def test_delegate_cross_tenant_todo_returns_404(self, client):
        from core.models import ActionItem
        self._setup(client)
        m2 = Manager.objects.create(
            username="other_t6b", display_name="Other",
            password_hash="x", email="other_t6b@example.com",
        )
        other = ActionItem.objects.create(
            description="other", manager_id=m2.id, status="pending",
        )
        assert client.get(f"/todos/{other.id}/delegate/").status_code == 404

    def test_completed_todo_cannot_be_delegated(self, client):
        m, tm, ai = self._setup(client)
        ai.status = "completed"
        ai.save()
        assert client.get(f"/todos/{ai.id}/delegate/").status_code == 404


@pytest.mark.django_db
class TestTodosStar:
    """POST /todos/<id>/star/ — priority star toggle."""

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
            username="todd_t8", display_name="Todd",
            password_hash="x", email="todd_t8@example.com",
        )
        self._login_as(client, "todd_t8@example.com")
        ai = ActionItem.objects.create(
            description="Starrable", manager_id=m.id, status="pending",
        )
        return m, ai

    def test_toggle_on_and_off(self, client):
        m, ai = self._setup(client)
        assert client.post(f"/todos/{ai.id}/star/").status_code == 200
        ai.refresh_from_db()
        assert ai.starred is True
        client.post(f"/todos/{ai.id}/star/")
        ai.refresh_from_db()
        assert ai.starred is False

    def test_starred_rows_sort_first(self, client):
        from core.models import ActionItem
        m, ai = self._setup(client)  # undated, unstarred
        ActionItem.objects.create(
            description="EarlyDue", manager_id=m.id, status="pending",
            due_date="2099-01-01",
        )
        ActionItem.objects.create(
            description="LateButStarred", manager_id=m.id, status="pending",
            due_date="2099-12-31", starred=True,
        )
        body = client.get("/todos/").content.decode()
        assert body.index("LateButStarred") < body.index("EarlyDue") < body.index("Starrable")

    def test_star_cross_tenant_returns_404(self, client):
        from core.models import ActionItem
        self._setup(client)
        m2 = Manager.objects.create(
            username="other_t8", display_name="Other",
            password_hash="x", email="other_t8@example.com",
        )
        other = ActionItem.objects.create(
            description="other", manager_id=m2.id, status="pending",
        )
        resp = client.post(f"/todos/{other.id}/star/")
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.starred is False

    def test_star_completed_or_deleted_returns_404(self, client):
        from django.utils import timezone
        from core.models import ActionItem
        m, ai = self._setup(client)
        done = ActionItem.objects.create(
            description="done", manager_id=m.id, status="completed",
        )
        gone = ActionItem.objects.create(
            description="gone", manager_id=m.id, status="pending",
            deleted_at=timezone.now(),
        )
        assert client.post(f"/todos/{done.id}/star/").status_code == 404
        assert client.post(f"/todos/{gone.id}/star/").status_code == 404

    def test_star_keeps_delegated_flag(self, client):
        from core.models import Delegation, TeamMember
        m, ai = self._setup(client)
        tm = TeamMember.objects.create(name="Sam", manager_id=m.id)
        Delegation.objects.create(
            manager_id=m.id, team_member=tm, task="StarFlagDeleg",
            status="active",
        )
        body = client.post(f"/todos/{ai.id}/star/?delegated=1").content.decode()
        assert "StarFlagDeleg" in body  # merged view preserved in rebuild

    def test_starred_overdue_first_on_dashboard(self, client):
        from datetime import date, timedelta
        from core.models import ActionItem
        m, ai = self._setup(client)
        past = (date.today() - timedelta(days=2)).isoformat()
        ActionItem.objects.create(
            description="PlainOverdue", manager_id=m.id,
            status="pending", due_date=past,
        )
        ActionItem.objects.create(
            description="StarOverdue", manager_id=m.id,
            status="pending", due_date=past, starred=True,
        )
        body = client.get("/dashboard/panels/overview/").content.decode()
        assert body.index("StarOverdue") < body.index("PlainOverdue")
        assert "★" in body


@pytest.mark.django_db
class TestTodosShowDelegated:
    """?delegated=1 merges active Delegations into the pending table."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        from core.models import Delegation, TeamMember
        m = Manager.objects.create(
            username="todd_t7", display_name="Todd",
            password_hash="x", email="todd_t7@example.com",
        )
        self._login_as(client, "todd_t7@example.com")
        tm = TeamMember.objects.create(name="Sam Direct", manager_id=m.id)
        Delegation.objects.create(
            manager_id=m.id, team_member=tm, task="Delegated work",
            status="active", check_in_date="2099-04-01",
        )
        Delegation.objects.create(
            manager_id=m.id, team_member=tm, task="Finished delegation",
            status="completed",
        )
        return m, tm

    def test_hidden_by_default(self, client):
        self._setup(client)
        body = client.get("/todos/").content.decode()
        assert "Delegated work" not in body

    def test_shown_with_flag_active_only(self, client):
        self._setup(client)
        body = client.get("/todos/?delegated=1").content.decode()
        assert "Delegated work" in body
        assert "Sam Direct" in body
        assert "Finished delegation" not in body  # non-active excluded

    def test_flag_isolates_tenants(self, client):
        from core.models import Delegation, TeamMember
        self._setup(client)
        m2 = Manager.objects.create(
            username="other_t7", display_name="Other",
            password_hash="x", email="other_t7@example.com",
        )
        tm2 = TeamMember.objects.create(name="Foreign", manager_id=m2.id)
        Delegation.objects.create(
            manager_id=m2.id, team_member=tm2, task="Foreign delegated work",
            status="active",
        )
        body = client.get("/todos/?delegated=1").content.decode()
        assert "Foreign delegated work" not in body

    def test_uncomplete_with_flag_keeps_delegations_in_oob_list(self, client):
        """Review fix: the uncomplete button threads ?delegated=1, so
        the OOB #todo-list rebuild keeps the merged delegation rows."""
        from django.utils import timezone
        from core.models import ActionItem
        m, tm = self._setup(client)
        done = ActionItem.objects.create(
            description="FinishedTodo", manager_id=m.id,
            status="completed", completed_at=timezone.now(),
        )
        body = client.post(f"/todos/{done.id}/uncomplete/?delegated=1").content.decode()
        assert "Delegated work" in body  # merged view preserved
        page = client.get("/todos/?delegated=1").content.decode()
        assert f"/todos/{done.id}/complete/?delegated=1" in page  # buttons carry flag

    def test_edit_save_redirect_preserves_flag(self, client):
        from core.models import ActionItem
        m, tm = self._setup(client)
        ai = ActionItem.objects.create(
            description="EditFlag", manager_id=m.id, status="pending",
        )
        resp = client.post(f"/todos/{ai.id}/edit/?delegated=1", {
            "description": "EditFlag2", "due_date": "", "due_time": "",
        })
        assert resp.status_code == 302
        assert resp["Location"] == "/todos/?delegated=1"

    def test_purge_writes_system_audit_entry(self, client):
        from datetime import timedelta
        from django.utils import timezone
        from core.models import ActionItem, AuditLog
        m, tm = self._setup(client)
        expired = ActionItem.objects.create(
            description="PurgeMe", manager_id=m.id, status="pending",
            deleted_at=timezone.now() - timedelta(days=2),
        )
        client.get("/todos/")
        assert not ActionItem.objects.filter(pk=expired.id).exists()
        entry = AuditLog.objects.for_manager(m.id).filter(
            entity_type="ActionItem", entity_id=expired.id, action="delete",
        ).first()
        assert entry is not None and entry.actor_type == "system"

    def test_checkbox_reflects_state(self, client):
        import re
        self._setup(client)

        def is_checked(body):
            m = re.search(r"<input[^>]*type=\"checkbox\"[^>]*>", body)
            assert m, "Show Delegated? checkbox missing"
            # \schecked\s matches the bare attribute but not the
            # `this.checked` in the onchange handler.
            return bool(re.search(r"\schecked\s", m.group(0)))

        assert not is_checked(client.get("/todos/").content.decode())
        assert is_checked(client.get("/todos/?delegated=1").content.decode())


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
        self._setup(client)
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
class TestJournalExport:
    """GET /journal/export/ — CSV download of the manager's journal history."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_jexp", display_name="Todd",
            password_hash="x", email="todd_jexp@example.com",
        )
        self._login_as(client, "todd_jexp@example.com")
        return m

    def test_export_returns_csv_download(self, client):
        m = self._setup(client)
        JournalEntry.objects.create(
            entry_date="2026-05-08", entry_type="daily",
            content="Had a great 1:1, comma, and \"quotes\"",
            mood=4, energy=3, tags="reflection",
            manager_id=m.id,
        )
        resp = client.get("/journal/export/")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        assert "attachment" in resp["Content-Disposition"]
        assert "journal-export-" in resp["Content-Disposition"]
        text = resp.content.decode("utf-8-sig")
        assert "entry_date,entry_type,mood" in text
        assert "2026-05-08" in text
        assert "Had a great 1:1" in text
        # Exports are audited so downloads are traceable (hardening follow-up).
        assert AuditLog.objects.filter(
            manager_id=m.id, action="export", entity_type="JournalEntry",
        ).exists()

    def test_export_is_tenant_scoped(self, client):
        m = self._setup(client)
        m2 = Manager.objects.create(
            username="other_jexp", display_name="Other",
            password_hash="x", email="other_jexp@example.com",
        )
        JournalEntry.objects.create(
            entry_date="2026-05-08", entry_type="daily",
            content="MY SECRET ENTRY", manager_id=m.id,
        )
        JournalEntry.objects.create(
            entry_date="2026-05-09", entry_type="daily",
            content="OTHER SECRET ENTRY", manager_id=m2.id,
        )
        resp = client.get("/journal/export/")
        text = resp.content.decode("utf-8-sig")
        assert "MY SECRET ENTRY" in text
        assert "OTHER SECRET ENTRY" not in text

    def test_export_requires_authenticated_manager(self, client):
        # Anonymous — no manager session.
        resp = client.get("/journal/export/")
        assert resp.status_code in (302, 403)
        # Logged-in but no Manager profile → 403.
        self._login_as(client, "stranger_jexp@example.com")
        resp = client.get("/journal/export/")
        assert resp.status_code == 403


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
class TestJournalAddFormReset:
    """After save, the returned form must be empty (no existing_id, no
    pre-filled content) so the next submit creates a fresh entry, and
    the just-saved entry must include the coaching-polling placeholder."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_jreset", display_name="Todd",
            password_hash="x", email="todd_jreset@example.com",
        )
        self._login_as(client, "todd_jreset@example.com")
        return m

    def test_save_returns_empty_form(self, client):
        self._setup(client)
        resp = client.post("/journal/add/", {
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "Some private thought about delegation",
        })
        assert resp.status_code == 200
        # Form's textarea should not echo the just-saved content.
        body = resp.content.decode()
        form_start = body.find('id="journal-form"')
        form_end = body.find("</form>", form_start)
        form_html = body[form_start:form_end]
        assert "Some private thought about delegation" not in form_html
        # No existing_id hidden input means subsequent submits create new.
        assert 'name="existing_id"' not in form_html
        # Button should say "Save entry", not "Update entry".
        assert "Save entry" in form_html
        assert "Update entry" not in form_html

    def test_save_resets_even_when_updating_existing(self, client):
        m = self._setup(client)
        entry = JournalEntry.objects.create(
            entry_date="2026-05-09", entry_type="daily",
            content="Original", manager_id=m.id,
        )
        resp = client.post("/journal/add/", {
            "existing_id": str(entry.id),
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "Revised content",
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        form_start = body.find('id="journal-form"')
        form_end = body.find("</form>", form_start)
        form_html = body[form_start:form_end]
        assert "Revised content" not in form_html
        assert 'name="existing_id"' not in form_html

    def test_save_includes_coaching_polling_placeholder(self, client):
        self._setup(client)
        resp = client.post("/journal/add/", {
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "Something to coach me on",
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        entry = JournalEntry.objects.first()
        # The polling placeholder targets the just-saved entry.
        assert f"/journal/{entry.id}/coaching/" in body
        assert "Generating coaching response" in body

    def test_save_with_blank_content_no_polling_placeholder(self, client):
        self._setup(client)
        resp = client.post("/journal/add/", {
            "entry_date": "2026-05-09",
            "entry_type": "daily",
            "content": "",
            "mood": "3",
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        # No content => no coaching generation kicked off => no placeholder.
        assert "Generating coaching response" not in body


@pytest.mark.django_db
class TestJournalCoaching:
    """GET /journal/<id>/coaching/ — polling endpoint that returns
    pending placeholder or ready response depending on entry state."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_jcoach", display_name="Todd",
            password_hash="x", email="todd_jcoach@example.com",
        )
        self._login_as(client, "todd_jcoach@example.com")
        return m

    def test_pending_when_coaching_response_empty(self, client):
        m = self._setup(client)
        entry = JournalEntry.objects.create(
            entry_date="2026-05-09", entry_type="daily",
            content="Notes", manager_id=m.id,
        )
        resp = client.get(f"/journal/{entry.id}/coaching/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Generating coaching response" in body
        assert 'hx-trigger="every 2s"' in body
        # Must not include the ready response.
        assert "Coaching response</summary>" not in body

    def test_ready_when_coaching_response_populated(self, client):
        m = self._setup(client)
        entry = JournalEntry.objects.create(
            entry_date="2026-05-09", entry_type="daily",
            content="Notes", manager_id=m.id,
            coaching_response="Try asking what success looks like.",
        )
        resp = client.get(f"/journal/{entry.id}/coaching/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Try asking what success looks like." in body
        # No hx-trigger on the ready partial => polling stops.
        assert "hx-trigger" not in body

    def test_cross_tenant_returns_404(self, client):
        self._setup(client)
        m2 = Manager.objects.create(
            username="other_jcoach", display_name="Other",
            password_hash="x", email="other_jcoach@example.com",
        )
        other_entry = JournalEntry.objects.create(
            entry_date="2026-05-09", entry_type="daily",
            content="Secret", manager_id=m2.id,
            coaching_response="Secret coaching",
        )
        resp = client.get(f"/journal/{other_entry.id}/coaching/")
        assert resp.status_code == 404

    def test_missing_entry_returns_404(self, client):
        self._setup(client)
        resp = client.get("/journal/99999/coaching/")
        assert resp.status_code == 404


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
        Manager.objects.create(
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
        assert resp.status_code == 200  # HTMX partial (D4)
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
        assert resp.status_code == 200  # HTMX partial (D4)
        assert DevelopmentPlan.objects.for_manager(m.id).count() == 1

    def test_update_plan_status(self, client):
        m, tm = self._setup(client)
        plan = DevelopmentPlan.objects.create(
            team_member=tm, title="X", manager_id=m.id, status="active",
        )
        resp = client.post(f"/career/plans/{plan.id}/status/", {"status": "completed"})
        assert resp.status_code == 200  # HTMX partial (D4)
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
        assert resp.status_code == 200  # HTMX partial (D4)
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
        assert resp.status_code == 200  # HTMX partial (D4)
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
        assert resp.status_code == 200  # HTMX partial (D4)
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
        self._setup(client)
        m2 = Manager.objects.create(
            username="other_dc1", display_name="Other",
            password_hash="x", email="other_dc1@example.com",
        )
        d = Decision.objects.create(title="Secret", manager_id=m2.id)
        assert client.get(f"/decisions/{d.id}/edit/").status_code == 404

    def test_cross_tenant_delete_returns_404(self, client):
        self._setup(client)
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
            "category": "observation",
        })
        assert resp.status_code == 302
        ns = RunningNote.objects.for_manager(m.id)
        assert ns.count() == 1
        n = ns.first()
        assert n.content == "Alice did great in the meeting"
        assert n.category == "observation"
        assert n.note_date == "2026-05-09"

    def test_praise_category_rejected_at_form(self, client):
        """Regression for the praise→feedback consolidation (migration
        0010). New praise notes must NOT be creatable via the form —
        praise is now structured Feedback. Without this guard the form
        would re-create the duplicate channel the migration just moved
        users off of."""
        m, tm = self._setup(client)
        resp = client.post("/notes/add/", {
            "team_member": tm.id,
            "note_date": "2026-05-09",
            "content": "shouldn't save",
            "category": "praise",
        })
        # Django ChoiceField rejects unknown values; the view either
        # re-renders the form (200) or stays without redirect.
        assert resp.status_code != 302, (
            "Form accepted praise category — should reject post-migration 0010"
        )
        assert RunningNote.objects.for_manager(m.id).filter(
            category="praise"
        ).count() == 0

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


# ============================================================
# Phase 5.6b — Feedback
# ============================================================


@pytest.mark.django_db
class TestFeedback:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_fb1", display_name="Todd",
            password_hash="x", email="todd_fb1@example.com",
        )
        self._login_as(client, "todd_fb1@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_page_loads(self, client):
        self._setup(client)
        assert client.get("/feedback/").status_code == 200
        assert b"Feedback" in client.get("/feedback/").content

    def test_create_persists(self, client):
        m, tm = self._setup(client)
        resp = client.post("/feedback/add/", {
            "team_member": tm.id,
            "feedback_type": "positive",
            "situation": "In the standup",
            "behavior": "Gave clear status update",
            "impact": "Saved 10 min of follow-up",
        })
        assert resp.status_code == 302
        fbs = Feedback.objects.for_manager(m.id)
        assert fbs.count() == 1
        f = fbs.first()
        assert f.feedback_type == "positive"
        assert f.situation == "In the standup"
        assert f.manager_id == m.id

    def test_missing_team_member_returns_422(self, client):
        self._setup(client)
        resp = client.post("/feedback/add/", {
            "feedback_type": "positive",
            "situation": "In the standup",
        })
        assert resp.status_code == 422

    def test_delete_removes(self, client):
        m, tm = self._setup(client)
        f = Feedback.objects.create(
            team_member=tm, feedback_type="positive",
            manager_id=m.id,
        )
        resp = client.delete(f"/feedback/{f.id}/delete/")
        assert resp.status_code == 200
        assert not Feedback.objects.filter(pk=f.id).exists()

    def test_cross_tenant_delete_returns_404(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_fb1", display_name="Other",
            password_hash="x", email="other_fb1@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        f = Feedback.objects.create(
            team_member=tm2, feedback_type="constructive",
            manager_id=m2.id,
        )
        assert client.delete(f"/feedback/{f.id}/delete/").status_code == 404
        assert Feedback.objects.filter(pk=f.id).exists()

    def test_cross_tenant_list_hidden(self, client):
        m1, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_fb1b", display_name="Other",
            password_hash="x", email="other_fb1b@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        Feedback.objects.create(
            team_member=tm2, feedback_type="positive",
            situation="Secret feedback", manager_id=m2.id,
        )
        resp = client.get("/feedback/")
        assert b"Secret feedback" not in resp.content


# ============================================================
# Phase 5.7 — Settings
# ============================================================


@pytest.mark.django_db
class TestSettings:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_set1", display_name="Todd",
            password_hash="x", email="todd_set1@example.com",
            timezone="America/New_York",
        )
        self._login_as(client, "todd_set1@example.com")
        return m

    def test_page_loads(self, client):
        self._setup(client)
        resp = client.get("/settings/")
        assert resp.status_code == 200
        assert b"Settings" in resp.content
        assert b"Todd" in resp.content

    def test_update_display_name(self, client):
        m = self._setup(client)
        resp = client.post("/settings/", {
            "display_name": "Todd E.",
            "timezone": "America/Chicago",
        })
        assert resp.status_code == 302
        m.refresh_from_db()
        assert m.display_name == "Todd E."
        assert m.timezone == "America/Chicago"

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger_set@example.com")
        resp = client.get("/settings/")
        assert resp.status_code == 403

    def test_shows_account_info(self, client):
        self._setup(client)
        resp = client.get("/settings/")
        assert b"todd_set1@example.com" in resp.content or b"todd_set1" in resp.content


# ============================================================
# Phase 6 — Calendar service tests
# ============================================================


@pytest.mark.django_db
class TestCalendarService:
    """Tests for core/services/calendar.py — ICS generation and M3
    sanitization guards."""

    def _make_event(self):
        m = Manager.objects.create(
            username="cal_mgr", display_name="Cal Manager",
            password_hash="h", email="cal@example.com",
        )
        tm = TeamMember.objects.create(
            name="Cal Report", manager_id=m.id, email="report@example.com",
        )
        e = Event.objects.create(
            manager_id=m.id, title="Weekly 1:1", event_type="one_on_one",
            team_member=tm, scheduled_date="2026-06-01",
            scheduled_time="10:00", duration_minutes=30,
            status="scheduled",
        )
        return m, tm, e

    def test_generate_ics_basic(self):
        from core.services.calendar import generate_ics
        _, _, event = self._make_event()
        ics = generate_ics(event)
        assert "BEGIN:VCALENDAR" in ics
        assert "BEGIN:VEVENT" in ics
        assert "SUMMARY:Weekly 1:1" in ics
        assert "END:VCALENDAR" in ics

    def test_generate_ics_from_dict(self):
        from core.services.calendar import generate_ics
        event_dict = {
            "scheduled_date": "2026-06-01",
            "scheduled_time": "14:00",
            "duration_minutes": 45,
            "title": "Coaching",
            "event_type": "coaching",
            "location": "Room A",
            "agenda": "Discuss goals",
        }
        ics = generate_ics(event_dict)
        assert "SUMMARY:Coaching" in ics
        assert "LOCATION:Room A" in ics

    def test_ics_strips_control_chars(self):
        """M3 guard: control characters in title/agenda are stripped."""
        from core.services.calendar import generate_ics
        event_dict = {
            "scheduled_date": "2026-06-01",
            "scheduled_time": "10:00",
            "title": "Meeting\r\nBcc: evil@attacker.com",
            "event_type": "other",
        }
        ics = generate_ics(event_dict)
        assert "\r\nBcc:" not in ics.replace("\r\n", "|||")
        assert "evil@attacker.com" not in ics or "SUMMARY" in ics

    def test_ics_with_attendees(self):
        from core.services.calendar import generate_ics
        _, _, event = self._make_event()
        ics = generate_ics(
            event,
            organizer_name="Boss", organizer_email="boss@example.com",
            attendee_name="Report", attendee_email="report@example.com",
        )
        assert "ORGANIZER" in ics
        assert "ATTENDEE" in ics
        assert "boss@example.com" in ics
        assert "report@example.com" in ics

    def test_safe_header_text_strips_control_chars(self):
        from core.services.calendar import _safe_header_text
        assert "\n" not in _safe_header_text("Hello\nWorld")
        assert "\r" not in _safe_header_text("Hello\rWorld")
        assert len(_safe_header_text("x" * 300)) == 200

    def test_ics_cn_value_strips_quotes(self):
        from core.services.calendar import _ics_cn_value
        assert '"' not in _ics_cn_value('John "Boss" Smith')

    def test_send_calendar_invite_no_smtp(self):
        """Without SMTP config, send_calendar_invite returns failure."""
        from core.services.calendar import send_calendar_invite
        _, _, event = self._make_event()
        success, msg = send_calendar_invite(
            event, "recipient@example.com",
            manager_id=event.manager_id,
        )
        assert success is False
        assert "SMTP not configured" in msg


# ============================================================
# Phase 6 — Digest service tests
# ============================================================


@pytest.mark.django_db
class TestDigestService:
    """Tests for core/services/digest.py."""

    def _seed(self):
        m = Manager.objects.create(
            username="digest_mgr", display_name="Digest Manager",
            password_hash="h", email="digest@example.com",
        )
        tm = TeamMember.objects.create(
            name="Digest Report", manager_id=m.id,
        )
        Event.objects.create(
            manager_id=m.id, title="Upcoming Event",
            event_type="one_on_one", team_member=tm,
            scheduled_date="2099-01-01", scheduled_time="10:00",
            status="scheduled",
        )
        ActionItem.objects.create(
            manager_id=m.id, description="Overdue task",
            due_date="2020-01-01", status="pending",
        )
        JournalEntry.objects.create(
            manager_id=m.id, entry_date="2099-01-01",
            entry_type="daily", content="Test entry",
        )
        return m

    def test_generate_weekly_digest_html(self):
        from core.services.digest import generate_weekly_digest
        m = self._seed()
        subject, html = generate_weekly_digest(m.id)
        assert "Weekly Digest" in subject
        assert "Digest Manager" in html
        assert "Overdue" in html

    def test_digest_excludes_soft_deleted_todos(self):
        """Review fix (To Do overhaul): soft-deleted to-dos must not be
        emailed as overdue/pending work."""
        from django.utils import timezone
        from core.services.digest import generate_weekly_digest
        m = self._seed()
        ActionItem.objects.create(
            manager_id=m.id, description="ZombieDigestTask",
            due_date="2020-01-01", status="pending",
            deleted_at=timezone.now(),
        )
        _, html = generate_weekly_digest(m.id)
        assert "ZombieDigestTask" not in html
        assert "Overdue task" in html  # the live one still shows

    def test_generate_weekly_digest_empty_manager(self):
        from core.services.digest import generate_weekly_digest
        m = Manager.objects.create(
            username="empty_mgr", display_name="Empty",
            password_hash="h",
        )
        subject, html = generate_weekly_digest(m.id)
        assert "Weekly Digest" in subject
        assert "Empty" in html

    def test_send_weekly_digest_no_smtp(self):
        from core.services.digest import send_weekly_digest
        m = self._seed()
        success, msg = send_weekly_digest(m.id)
        assert success is False
        assert "SMTP not configured" in msg

    def test_weekly_plan_section_included_when_ai_returns_text(self, mocker):
        from core.services.digest import generate_weekly_digest
        m = self._seed()
        mocker.patch(
            "coaching.services.generate_weekly_plan",
            return_value=(
                "1. **Talk to Sarah** — Horstman: weekly 1-on-1s.\n"
                "2. **Close decision #42** — Grove: detect problems early."
            ),
        )
        subject, html = generate_weekly_digest(m.id)
        assert "This week&#x27;s plan" in html or "This week's plan" in html
        assert "<strong>Talk to Sarah</strong>" in html
        assert "<strong>Close decision #42</strong>" in html

    def test_weekly_plan_section_omitted_when_ai_returns_none(self, mocker):
        from core.services.digest import generate_weekly_digest
        m = self._seed()
        mocker.patch(
            "coaching.services.generate_weekly_plan",
            return_value=None,
        )
        subject, html = generate_weekly_digest(m.id)
        assert "This week's plan" not in html
        # Backwards-looking sections still ship.
        assert "Overdue" in html

    def test_weekly_plan_failure_does_not_break_digest(self, mocker):
        from core.services.digest import generate_weekly_digest
        m = self._seed()
        mocker.patch(
            "coaching.services.generate_weekly_plan",
            side_effect=RuntimeError("api down"),
        )
        subject, html = generate_weekly_digest(m.id)
        # Digest still renders without the plan section.
        assert "Weekly Digest" in subject
        assert "Overdue" in html
        assert "This week's plan" not in html


# ============================================================
# Phase 6 — Management commands tests
# ============================================================


@pytest.mark.django_db
class TestSendWeeklyDigestsCommand:
    """Tests for the send_weekly_digests management command."""

    def test_dry_run_no_configured_managers(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("send_weekly_digests", "--dry-run", stdout=out)
        assert "0 manager(s)" in out.getvalue()

    def test_dry_run_with_configured_manager(self):
        from django.core.management import call_command
        from io import StringIO
        from core.models import Config
        m = Manager.objects.create(
            username="cmd_mgr", display_name="Cmd Manager",
            password_hash="h", email="cmd@example.com",
        )
        Config.objects.create(manager_id=m.id, key="smtp_server", value="smtp.example.com")
        out = StringIO()
        call_command("send_weekly_digests", "--dry-run", stdout=out)
        assert "1 manager(s)" in out.getvalue()
        assert "Cmd Manager" in out.getvalue()

    def test_single_manager_no_smtp(self):
        from django.core.management import call_command
        from io import StringIO
        m = Manager.objects.create(
            username="cmd_mgr2", display_name="Cmd Manager 2",
            password_hash="h",
        )
        out = StringIO()
        call_command("send_weekly_digests", "--manager-id", str(m.id), stdout=out)
        assert "0 sent, 1 failed" in out.getvalue()


# ============================================================
# D3 — Audit logging tests
# ============================================================


@pytest.mark.django_db
class TestAuditLogging:
    """Tests for the audit log service and its integration with views."""

    def test_log_mutation_creates_entry(self):
        from core.models import AuditLog
        from core.services.audit import log_mutation
        m = Manager.objects.create(
            username="audit_mgr", display_name="Audit Mgr",
            password_hash="h", email="audit@example.com",
        )
        log_mutation(m.id, "create", "TeamMember", 99, "Added team member: Alice")
        # Filter by entity_type+entity_id to avoid interference from
        # background coaching threads that also write audit entries.
        entries = AuditLog.objects.for_manager(m.id).filter(
            entity_type="TeamMember", entity_id=99,
        )
        assert entries.count() == 1
        entry = entries.first()
        assert entry.action == "create"
        assert "Alice" in entry.summary

    def test_log_mutation_none_manager_skipped(self):
        from core.models import AuditLog
        from core.services.audit import log_mutation
        baseline = AuditLog.objects.count()
        log_mutation(None, "create", "TeamMember", 1, "should not save")
        assert AuditLog.objects.count() == baseline

    def test_log_mutation_caps_summary(self):
        from core.models import AuditLog
        from core.services.audit import log_mutation
        m = Manager.objects.create(
            username="audit_cap", display_name="Cap Mgr",
            password_hash="h",
        )
        log_mutation(m.id, "update", "Goal", 1, "x" * 600)
        entry = AuditLog.objects.for_manager(m.id).filter(
            entity_type="Goal", entity_id=1,
        ).first()
        assert entry is not None
        assert len(entry.summary) == 500

    def test_team_member_add_creates_audit(self, client):
        """Integration: adding a team member via the view creates an audit entry."""
        from django.contrib.auth.models import User
        from core.models import AuditLog
        user = User.objects.create_user("auditor", "auditor@example.com", "pw")
        m = Manager.objects.create(
            username="audit_int", display_name="Audit Int",
            password_hash="h", email="auditor@example.com",
        )
        client.force_login(user)
        client.post("/team/add/", {"name": "New Member"})
        assert AuditLog.objects.for_manager(m.id).filter(
            action="create", entity_type="TeamMember",
        ).exists()

    def test_audit_log_cross_tenant_isolation(self):
        from core.models import AuditLog
        from core.services.audit import log_mutation
        m1 = Manager.objects.create(
            username="audit_m1", display_name="M1",
            password_hash="h",
        )
        m2 = Manager.objects.create(
            username="audit_m2", display_name="M2",
            password_hash="h",
        )
        log_mutation(m1.id, "create", "Goal", 1, "M1's goal")
        log_mutation(m2.id, "create", "Goal", 2, "M2's goal")
        # Filter by entity_type to avoid coaching thread interference
        assert AuditLog.objects.for_manager(m1.id).filter(entity_type="Goal").count() == 1
        assert AuditLog.objects.for_manager(m2.id).filter(entity_type="Goal").count() == 1


# ============================================================
# Reference pages — Analytics, History, Resources
# ============================================================


@pytest.mark.django_db
class TestReferencePages:
    """Basic smoke tests for the three reference pages."""

    def _setup(self, client):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username="ref@example.com", email="ref@example.com", password="x",
        )
        m = Manager.objects.create(
            username="ref_mgr", display_name="Ref Manager",
            password_hash="h", email="ref@example.com",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_analytics_renders(self, client):
        self._setup(client)
        resp = client.get("/analytics/")
        assert resp.status_code == 200
        assert b"Analytics" in resp.content

    def test_analytics_shows_team_count(self, client):
        m, _ = self._setup(client)
        resp = client.get("/analytics/")
        assert b"1" in resp.content  # 1 team member

    def test_analytics_shows_management_score(self, client):
        m, tm = self._setup(client)
        resp = client.get("/analytics/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Management score" in body
        # A fresh account (no feedback/goals/actions) still resolves to a 0
        # score from the cadence + streak components, with a letter grade.
        assert "Grade" in body

    def test_history_renders(self, client):
        self._setup(client)
        resp = client.get("/history/")
        assert resp.status_code == 200
        assert b"History" in resp.content

    def test_history_shows_events(self, client):
        m, tm = self._setup(client)
        Event.objects.create(
            manager_id=m.id, title="Test Meeting",
            event_type="one_on_one", team_member=tm,
            scheduled_date="2026-05-10", scheduled_time="10:00",
            status="scheduled",
        )
        resp = client.get("/history/")
        assert b"Test Meeting" in resp.content

    def test_history_filters_by_member(self, client):
        m, tm = self._setup(client)
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m.id)
        Event.objects.create(
            manager_id=m.id, title="Alice Meeting",
            event_type="one_on_one", team_member=tm,
            scheduled_date="2026-05-10", scheduled_time="10:00",
            status="scheduled",
        )
        Event.objects.create(
            manager_id=m.id, title="Bob Meeting",
            event_type="one_on_one", team_member=tm2,
            scheduled_date="2026-05-10", scheduled_time="11:00",
            status="scheduled",
        )
        resp = client.get(f"/history/?member={tm.id}")
        assert b"Alice Meeting" in resp.content
        assert b"Bob Meeting" not in resp.content

    def test_resources_renders(self, client):
        self._setup(client)
        resp = client.get("/resources/")
        assert resp.status_code == 200
        assert b"Resources" in resp.content
        assert b"wisdom" in resp.content.lower()

    def test_resources_search(self, client):
        self._setup(client)
        resp = client.get("/resources/?q=feedback")
        assert resp.status_code == 200
        assert b"feedback" in resp.content.lower()

    def test_cross_tenant_analytics_403(self, client):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username="stranger@example.com", email="stranger@example.com", password="x",
        )
        client.force_login(u)
        resp = client.get("/analytics/")
        assert resp.status_code == 403

    def test_cross_tenant_history_403(self, client):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username="stranger2@example.com", email="stranger2@example.com", password="x",
        )
        client.force_login(u)
        resp = client.get("/history/")
        assert resp.status_code == 403


# ── One-on-One Meetings ──────────────────────────────────────────────


@pytest.mark.django_db
class TestOneOnOneSessions:
    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=email, email=email, password="x")
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_mtg", display_name="Todd",
            password_hash="x", email="todd_mtg@example.com",
        )
        self._login_as(client, "todd_mtg@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_meetings_list_200(self, client):
        self._setup(client)
        assert client.get("/meetings/").status_code == 200

    def test_meetings_list_requires_login(self, client):
        resp = client.get("/meetings/")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url or "/accounts/google/login/" in resp.url

    def test_new_meeting_page_200(self, client):
        self._setup(client)
        resp = client.get("/meetings/new/")
        assert resp.status_code == 200
        # Renders the shared meeting form (its submit button).
        assert b"Start meeting" in resp.content

    def test_new_meeting_page_requires_login(self, client):
        resp = client.get("/meetings/new/")
        assert resp.status_code == 302
        assert "/accounts/login/" in resp.url or "/accounts/google/login/" in resp.url

    def test_new_meeting_page_is_get_only(self, client):
        self._setup(client)
        # The dedicated page is GET-only; the form POSTs to meetings-add.
        assert client.post("/meetings/new/").status_code == 405

    def test_new_meeting_page_scopes_members_to_manager(self, client):
        _, _ = self._setup(client)  # Todd + Alice
        m2 = Manager.objects.create(
            username="other_new", display_name="Other",
            password_hash="x", email="other_new@example.com",
        )
        TeamMember.objects.create(name="Bob", manager_id=m2.id)
        body = client.get("/meetings/new/").content.decode()
        assert "Alice" in body       # this manager's member is selectable
        assert "Bob" not in body     # another manager's member is not

    def test_new_meeting_page_defaults_date_to_today(self, client):
        from datetime import date as _date
        self._setup(client)
        body = client.get("/meetings/new/").content.decode()
        # Parity with the in-page 'Start new meeting' fold-out, which
        # pre-fills today (one_on_ones_list). The <input type="date">
        # renders value="YYYY-MM-DD".
        assert _date.today().isoformat() in body

    def test_search_filters_by_notes_text(self, client):
        """?q= filters drafts/completed by direct/manager/followup notes."""
        m, tm = self._setup(client)
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-09",
            status="completed", direct_notes="they raised the promotion topic",
        )
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="completed", manager_notes="discussed Q3 hiring plan",
        )
        # Hit on the first session via direct_notes
        body = client.get("/meetings/?q=promotion").content.decode()
        assert "2026-05-09" in body
        assert "2026-05-10" not in body
        # Hit on the second session via manager_notes
        body = client.get("/meetings/?q=hiring").content.decode()
        assert "2026-05-10" in body
        assert "2026-05-09" not in body

    def test_search_does_not_leak_other_managers_notes(self, client):
        """Cross-tenant: ?q= must respect TenantManager scoping."""
        m, tm = self._setup(client)
        m2 = Manager.objects.create(
            username="other_mtg", display_name="Other",
            password_hash="x", email="other_mtg@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        # Manager 2's session contains the search term, but Todd is logged in.
        OneOnOneSession.objects.create(
            manager=m2, team_member=tm2, session_date="2026-05-08",
            status="completed", direct_notes="secret cross-tenant string",
        )
        body = client.get("/meetings/?q=secret").content.decode()
        assert "2026-05-08" not in body
        assert "secret" not in body or "secret cross-tenant" not in body

    def test_create_redirects_to_detail(self, client):
        m, tm = self._setup(client)
        resp = client.post("/meetings/add/", {
            "team_member": tm.id,
            "session_date": "2026-05-10",
        })
        assert resp.status_code == 302
        session = OneOnOneSession.objects.for_manager(m.id).first()
        assert session is not None
        assert session.status == "draft"
        assert session.team_member_id == tm.id
        assert f"/meetings/{session.id}/" in resp.url

    def test_detail_shows_context(self, client):
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        Delegation.objects.create(
            manager_id=m.id, team_member=tm, task="Review deck",
            status="active",
        )
        resp = client.get(f"/meetings/{session.id}/")
        assert resp.status_code == 200
        assert b"Review deck" in resp.content

    def test_prep_mode_offered_when_agenda_empty(self, client):
        """Prep mode: an empty Your Agenda offers a one-click pull of the
        direct's open delegations + action items."""
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft", manager_notes="",
        )
        Delegation.objects.create(
            manager_id=m.id, team_member=tm, task="Review deck",
            status="active",
        )
        resp = client.get(f"/meetings/{session.id}/")
        body = resp.content.decode()
        assert "Prepare agenda from 1 open item" in body
        assert 'id="prep-agenda-data"' in body
        # The open delegation seeds the prep payload.
        assert "Review deck" in body

    def test_prep_mode_hidden_when_agenda_has_content(self, client):
        """Never overwrite: once Your Agenda has notes, the prep button is gone."""
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft", manager_notes="Already drafted my points",
        )
        Delegation.objects.create(
            manager_id=m.id, team_member=tm, task="Review deck",
            status="active",
        )
        resp = client.get(f"/meetings/{session.id}/")
        assert b"Prepare agenda from" not in resp.content

    def test_soft_gate_collapses_your_agenda_when_empty(self, client):
        """Soft gate: with all three text fields empty, Your Agenda and
        Coaching are rendered as closed <details> (no open attr), and the
        nudge points the manager at Their Agenda first."""
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        body = client.get(f"/meetings/{session.id}/").content.decode()
        assert "<details  class=\"group\">" in body or "<details class=\"group\">" in body, \
            "Your Agenda and Coaching <details> should render closed (no open attr)"
        assert "start with theirs" in body  # nudge hint

    def test_soft_gate_opens_when_their_agenda_has_content(self, client):
        """Once Their Agenda is non-empty, Your Agenda + Coaching open."""
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft", direct_notes="They want to discuss promotion path",
        )
        body = client.get(f"/meetings/{session.id}/").content.decode()
        assert "<details open class=\"group\">" in body, \
            "details should be open once direct_notes has content"
        assert "start with theirs" not in body  # hint goes away

    def test_soft_gate_never_hides_existing_notes(self, client):
        """If Your Agenda already has notes, it must render open even if
        Their Agenda is empty — never hide existing content behind a click."""
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft", manager_notes="Pre-existing notes from before",
        )
        body = client.get(f"/meetings/{session.id}/").content.decode()
        assert body.count("<details open class=\"group\">") >= 1, \
            "Your Agenda must be open when manager_notes is non-empty"
        assert "Pre-existing notes from before" in body

    def test_autosave_updates_notes(self, client):
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        resp = client.post(f"/meetings/{session.id}/autosave/", {
            "direct_notes": "They want to discuss promotion",
            "manager_notes": "Review Q2 goals",
            "followup_notes": "Career plan update",
        })
        assert resp.status_code == 200
        session.refresh_from_db()
        assert session.direct_notes == "They want to discuss promotion"
        assert session.manager_notes == "Review Q2 goals"
        assert session.followup_notes == "Career plan update"

    def test_complete_toggles_status(self, client):
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        # Mark complete
        resp = client.post(f"/meetings/{session.id}/complete/")
        assert resp.status_code == 302
        session.refresh_from_db()
        assert session.status == "completed"

        # Reopen
        resp = client.post(f"/meetings/{session.id}/complete/")
        assert resp.status_code == 302
        session.refresh_from_db()
        assert session.status == "draft"

    def test_delete_removes_session(self, client):
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        resp = client.delete(f"/meetings/{session.id}/delete/")
        assert resp.status_code == 200
        assert OneOnOneSession.objects.for_manager(m.id).count() == 0

    def test_add_action_item(self, client):
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        resp = client.post(f"/meetings/{session.id}/action/", {
            "description": "Follow up on training",
        })
        assert resp.status_code == 200
        items = ActionItem.objects.for_manager(m.id).filter(one_on_one_session=session)
        assert items.count() == 1
        assert items.first().description == "Follow up on training"

    def test_add_delegation_from_meeting(self, client):
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        resp = client.post(f"/meetings/{session.id}/action/", {
            "owner": "delegate",
            "description": "Prepare the Q3 deck",
            "due_date": "2026-06-01",
        })
        assert resp.status_code == 200
        deps = Delegation.objects.for_manager(m.id)
        assert deps.count() == 1
        dep = deps.first()
        assert dep.team_member_id == tm.id
        assert dep.task == "Prepare the Q3 deck"
        assert dep.check_in_date == "2026-06-01"
        assert dep.status == "active"
        # A delegation — NOT a to-do.
        assert ActionItem.objects.for_manager(m.id).count() == 0

    def test_add_action_item_defaults_to_mine(self, client):
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        # No owner → defaults to a to-do for the manager.
        resp = client.post(f"/meetings/{session.id}/action/", {
            "description": "Block time for the Q3 review",
        })
        assert resp.status_code == 200
        assert ActionItem.objects.for_manager(m.id).count() == 1
        assert Delegation.objects.for_manager(m.id).count() == 0

    def test_duplicate_date_redirects_to_existing(self, client):
        m, tm = self._setup(client)
        existing = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        resp = client.post("/meetings/add/", {
            "team_member": tm.id,
            "session_date": "2026-05-10",
        })
        assert resp.status_code == 302
        assert f"/meetings/{existing.id}/" in resp.url
        # No new session created
        assert OneOnOneSession.objects.for_manager(m.id).count() == 1

    def test_cross_tenant_detail_404(self, client):
        m, tm = self._setup(client)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-10",
            status="draft",
        )
        # Login as a different user with no manager profile
        from django.contrib.auth import get_user_model
        u2 = get_user_model().objects.create_user(
            username="stranger_mtg@example.com",
            email="stranger_mtg@example.com",
            password="x",
        )
        client.force_login(u2)
        resp = client.get(f"/meetings/{session.id}/")
        # Either 403 (no manager) or 404 (wrong manager)
        assert resp.status_code in (403, 404)


@pytest.mark.django_db
class TestOneOnOneSessionScoping:
    """Cross-manager isolation for OneOnOneSession."""

    def _two_managers(self):
        m1 = Manager.objects.create(
            username="mtg_m1", display_name="M1",
            password_hash="h1", email="mtg_m1@example.com",
        )
        m2 = Manager.objects.create(
            username="mtg_m2", display_name="M2",
            password_hash="h2", email="mtg_m2@example.com",
        )
        return m1, m2

    def test_sessions_isolated_bidirectionally(self):
        m1, m2 = self._two_managers()
        tm1 = TeamMember.objects.create(name="M1 report", manager_id=m1.id)
        tm2 = TeamMember.objects.create(name="M2 report", manager_id=m2.id)

        OneOnOneSession.objects.create(
            manager=m1, team_member=tm1, session_date="2026-05-10",
            status="completed",
        )
        OneOnOneSession.objects.create(
            manager=m2, team_member=tm2, session_date="2026-05-10",
            status="completed",
        )

        m1_rows = OneOnOneSession.objects.for_manager(m1.id)
        m2_rows = OneOnOneSession.objects.for_manager(m2.id)

        assert m1_rows.count() == 1
        assert m2_rows.count() == 1
        assert m1_rows.first().team_member.name == "M1 report"
        assert m2_rows.first().team_member.name == "M2 report"
        assert not m1_rows.filter(team_member__name="M2 report").exists()
        assert not m2_rows.filter(team_member__name="M1 report").exists()


class TestOneOnOneSessionNormalizeTags:
    """Unit tests for OneOnOneSession.normalize_tags."""

    def test_empty_inputs(self):
        assert OneOnOneSession.normalize_tags(None) == ""
        assert OneOnOneSession.normalize_tags("") == ""
        assert OneOnOneSession.normalize_tags("   ") == ""
        assert OneOnOneSession.normalize_tags(",,, , ,") == ""

    def test_lowercase_and_trim(self):
        assert OneOnOneSession.normalize_tags("  Career ") == "career"
        assert OneOnOneSession.normalize_tags("PROJECT-X") == "project-x"

    def test_dedupe_preserves_first_seen_order(self):
        # Case-insensitive duplicates collapse; order of first occurrence kept.
        got = OneOnOneSession.normalize_tags("career, 1on1, Career, project-x, 1ON1")
        assert got == "career,1on1,project-x"

    def test_drops_empty_pieces(self):
        assert OneOnOneSession.normalize_tags("a,, b,,,c") == "a,b,c"

    def test_tags_list_property(self):
        s = OneOnOneSession(tags="career,1on1,project-x")
        assert s.tags_list == ["career", "1on1", "project-x"]
        s2 = OneOnOneSession(tags=None)
        assert s2.tags_list == []
        s3 = OneOnOneSession(tags="")
        assert s3.tags_list == []


@pytest.mark.django_db
class TestOneOnOneSessionTagsListAndFilter:
    """`?tag=foo` filters sessions to those tagged exactly `foo`."""

    def _setup(self, client):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username="todd_tags", display_name="Todd",
            password_hash="x", email="todd_tags@example.com",
        )
        u = get_user_model().objects.create_user(
            username="todd_tags@example.com",
            email="todd_tags@example.com",
            password="x",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        return m, tm

    def test_session_tags_render_as_chips_on_list(self, client):
        m, tm = self._setup(client)
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-20",
            status="completed", tags="career,1on1",
        )
        body = client.get("/meetings/").content.decode()
        assert ">career<" in body
        assert ">1on1<" in body

    def test_tag_filter_exact_element_match_not_substring(self, client):
        """`?tag=foo` must NOT match `foobar` — exact CSV element only."""
        m, tm = self._setup(client)
        # Two sessions: one tagged "career", another "career-growth"
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-20",
            status="completed", tags="career",
            direct_notes="career session text",
        )
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-21",
            status="completed", tags="career-growth",
            direct_notes="growth session text",
        )
        body = client.get("/meetings/?tag=career").content.decode()
        assert "career session text" in body
        assert "growth session text" not in body

    def test_tag_filter_combines_with_member_filter(self, client):
        m, tm = self._setup(client)
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m.id)
        # Alice/career — both filters match
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-20",
            status="completed", tags="career",
            direct_notes="alice career",
        )
        # Bob/career — tag matches but member filter rejects
        OneOnOneSession.objects.create(
            manager=m, team_member=tm2, session_date="2026-05-21",
            status="completed", tags="career",
            direct_notes="bob career",
        )
        body = client.get(
            f"/meetings/?tag=career&member={tm.id}",
        ).content.decode()
        assert "alice career" in body
        assert "bob career" not in body

    def test_all_tags_chip_strip_renders_known_tags(self, client):
        m, tm = self._setup(client)
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-20",
            status="completed", tags="career,1on1",
        )
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-21",
            status="completed", tags="project-x",
        )
        body = client.get("/meetings/").content.decode()
        for t in ("career", "1on1", "project-x"):
            assert f"?tag={t}" in body


@pytest.mark.django_db
class TestOneOnOneSessionTagsAutosave:
    """Autosave normalizes tags input and clears them when blanked."""

    def _setup(self, client):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username="todd_tags_as", display_name="Todd",
            password_hash="x", email="todd_tags_as@example.com",
        )
        u = get_user_model().objects.create_user(
            username="todd_tags_as@example.com",
            email="todd_tags_as@example.com",
            password="x",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        session = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-20",
            status="draft",
        )
        return m, session

    def test_autosave_normalizes_tags(self, client):
        m, s = self._setup(client)
        resp = client.post(f"/meetings/{s.id}/autosave/", {
            "direct_notes": "",
            "manager_notes": "",
            "followup_notes": "",
            "tags": "  Career , 1on1, career , project-X, ,",
        })
        assert resp.status_code == 200
        s.refresh_from_db()
        assert s.tags == "career,1on1,project-x"

    def test_autosave_clearing_tags_stores_none(self, client):
        m, s = self._setup(client)
        s.tags = "career"
        s.save()
        resp = client.post(f"/meetings/{s.id}/autosave/", {
            "direct_notes": "",
            "manager_notes": "",
            "followup_notes": "",
            "tags": "",
        })
        assert resp.status_code == 200
        s.refresh_from_db()
        assert s.tags is None


@pytest.mark.django_db
class TestOneOnOneSessionTagsCrossTenant:
    """`?tag=foo` and the tag chip strip must respect TenantManager scoping."""

    def test_tag_filter_does_not_leak_other_tenant(self, client):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username="todd_xt", display_name="Todd",
            password_hash="x", email="todd_xt@example.com",
        )
        m2 = Manager.objects.create(
            username="other_xt", display_name="Other",
            password_hash="x", email="other_xt@example.com",
        )
        u = get_user_model().objects.create_user(
            username="todd_xt@example.com",
            email="todd_xt@example.com",
            password="x",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        # m2 has a session with tag "secret-tag" — Todd's filter must not see it.
        OneOnOneSession.objects.create(
            manager=m2, team_member=tm2, session_date="2026-05-20",
            status="completed", tags="secret-tag",
            direct_notes="secret cross-tenant string",
        )
        # Todd's session — empty tags, harmless.
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-05-21",
            status="completed",
        )
        body = client.get("/meetings/?tag=secret-tag").content.decode()
        assert "secret cross-tenant string" not in body
        # The cross-tenant tag must not appear in Todd's chip strip either.
        assert "?tag=secret-tag" not in body


# ============================================================
# Phase 6 — Settings: encryption, config CRUD, settings page
# ============================================================


class TestEncryption:
    """Fernet round-trip and prefix handling for sensitive config."""

    def test_round_trip(self):
        from core.services.encryption import encrypt_value, decrypt_value
        plain = "sk-ant-test-roundtrip-value"
        encrypted = encrypt_value(plain)
        assert encrypted.startswith("enc:")
        assert plain not in encrypted
        assert decrypt_value(encrypted) == plain

    def test_empty_string_passes_through(self):
        from core.services.encryption import encrypt_value, decrypt_value
        assert encrypt_value("") == ""
        assert decrypt_value("") == ""
        assert encrypt_value(None) is None

    def test_plaintext_decrypt_returns_unchanged(self):
        # Legacy / migration path: values without 'enc:' prefix should
        # return as-is so we don't break existing plaintext Config rows.
        from core.services.encryption import decrypt_value
        assert decrypt_value("plaintext-value") == "plaintext-value"

    def test_decrypt_garbage_raises(self):
        from core.services.encryption import (
            EncryptionUnavailableError, decrypt_value,
        )
        with pytest.raises(EncryptionUnavailableError):
            decrypt_value("enc:not-real-ciphertext")

    def test_is_encrypted(self):
        from core.services.encryption import encrypt_value, is_encrypted
        assert is_encrypted(encrypt_value("hello"))
        assert not is_encrypted("plain")
        assert not is_encrypted("")
        assert not is_encrypted(None)


@pytest.mark.django_db
class TestConfigService:
    """get_config / set_config / get_all_config with encryption."""

    def _mgr(self):
        return Manager.objects.create(
            username="cfg_mgr", display_name="Cfg",
            password_hash="x", email="cfg@example.com",
        )

    def test_set_then_get_nonsensitive(self):
        from core.services.config import get_config, set_config
        m = self._mgr()
        set_config("manager_name", m.id, "Alice")
        assert get_config("manager_name", m.id) == "Alice"

    def test_set_then_get_sensitive_round_trips(self):
        from core.models import Config
        from core.services.config import get_config, set_config
        m = self._mgr()
        set_config("anthropic_api_key", m.id, "sk-ant-test-secret")
        # Retrieved value is decrypted plaintext.
        assert get_config("anthropic_api_key", m.id) == "sk-ant-test-secret"
        # Stored value on disk is encrypted (ciphertext, not plain).
        row = Config.objects.get(manager_id=m.id, key="anthropic_api_key")
        assert row.value.startswith("enc:")
        assert "sk-ant-test-secret" not in row.value

    def test_set_empty_clears_value(self):
        from core.services.config import get_config, set_config
        m = self._mgr()
        set_config("smtp_user", m.id, "old@example.com")
        set_config("smtp_user", m.id, "")
        assert get_config("smtp_user", m.id, default="fallback") == "fallback"

    def test_get_missing_returns_default(self):
        from core.services.config import get_config
        m = self._mgr()
        assert get_config("nonexistent", m.id) is None
        assert get_config("nonexistent", m.id, default="x") == "x"

    def test_get_all_masks_sensitive(self):
        from core.services.config import get_all_config, set_config
        m = self._mgr()
        set_config("manager_name", m.id, "Alice")
        set_config("anthropic_api_key", m.id, "sk-ant-test-mask")
        set_config("smtp_password", m.id, "app-pass-test")
        all_cfg = get_all_config(m.id)
        assert all_cfg["manager_name"] == "Alice"
        assert all_cfg["anthropic_api_key"] == "********"
        assert all_cfg["smtp_password"] == "********"

    def test_upsert_overwrites(self):
        from core.services.config import get_config, set_config
        m = self._mgr()
        set_config("manager_name", m.id, "First")
        set_config("manager_name", m.id, "Second")
        assert get_config("manager_name", m.id) == "Second"

    def test_cross_tenant_isolation(self):
        from core.services.config import get_config, set_config
        m1 = self._mgr()
        m2 = Manager.objects.create(
            username="cfg_m2", display_name="Cfg2",
            password_hash="x", email="cfg2@example.com",
        )
        set_config("anthropic_api_key", m1.id, "sk-ant-m1")
        set_config("anthropic_api_key", m2.id, "sk-ant-m2")
        assert get_config("anthropic_api_key", m1.id) == "sk-ant-m1"
        assert get_config("anthropic_api_key", m2.id) == "sk-ant-m2"
        # Each manager only sees their own keys.
        from core.services.config import get_all_config
        assert "anthropic_api_key" in get_all_config(m1.id)

    # --- Audit-log coverage --------------------------------------------
    # The credential leak that prompted this work would have been
    # investigable in hindsight if Config changes had been audited.

    def test_set_audits_nonsensitive_change(self):
        from core.services.config import set_config
        m = self._mgr()
        # Count Config rows specifically, not all audit rows: a background
        # coaching thread from an earlier test can leave a CoachingResponse
        # row on this manager_id in the shared in-memory test DB, which
        # would inflate an unfiltered `before` and flake this assertion.
        before = AuditLog.objects.filter(
            manager_id=m.id, entity_type="Config",
        ).count()
        set_config("manager_name", m.id, "Alice")
        rows = AuditLog.objects.filter(
            manager_id=m.id, entity_type="Config",
        )
        assert rows.count() - before == 1
        r = rows.first()
        assert r.action == "update"
        assert r.actor_type == "user"
        # Non-sensitive values are rendered in the summary.
        assert "manager_name" in r.summary
        assert "Alice" in r.summary

    def test_set_audits_sensitive_change_without_value(self):
        """Sensitive keys must never expose the secret value in the audit
        summary. Anyone reading /audit/ should be able to see "key set"
        but not the key itself."""
        from core.services.config import set_config
        m = self._mgr()
        set_config("anthropic_api_key", m.id, "sk-ant-real-secret-VALUE")
        r = AuditLog.objects.filter(
            manager_id=m.id, entity_type="Config",
        ).first()
        assert r is not None
        assert "anthropic_api_key" in r.summary
        # The secret must NOT appear in plaintext in the summary.
        assert "sk-ant-real-secret-VALUE" not in r.summary
        assert "VALUE" not in r.summary
        # Transition is recorded as "set".
        assert "set" in r.summary

    def test_set_audits_clearing_sensitive(self):
        from core.services.config import set_config
        m = self._mgr()
        set_config("anthropic_api_key", m.id, "sk-ant-initial")
        before = AuditLog.objects.filter(
            manager_id=m.id, entity_type="Config",
        ).count()
        set_config("anthropic_api_key", m.id, "")
        rows = AuditLog.objects.filter(
            manager_id=m.id, entity_type="Config",
        )
        assert rows.count() - before == 1
        # Latest row is the clear.
        latest = rows.order_by("-created_at").first()
        assert "cleared" in latest.summary

    def test_unchanged_value_does_not_audit(self):
        """settings_page submits all fields on every save, even unchanged
        ones. Without change detection, every save would write N audit
        rows. Setting the same value twice must be a single audit."""
        from core.services.config import set_config
        m = self._mgr()
        set_config("manager_name", m.id, "Alice")
        before = AuditLog.objects.filter(manager_id=m.id).count()
        set_config("manager_name", m.id, "Alice")  # same value
        set_config("manager_name", m.id, "Alice")  # again
        assert AuditLog.objects.filter(manager_id=m.id).count() == before

    def test_actor_kwarg_propagates(self):
        """If a background job ever calls set_config, it can pass
        actor='system' so /audit/?actor=system stays useful."""
        from core.services.config import set_config
        m = self._mgr()
        set_config("manager_name", m.id, "FromCron", actor="system")
        r = AuditLog.objects.filter(
            manager_id=m.id, entity_type="Config",
        ).order_by("-created_at").first()
        assert r.actor_type == "system"


@pytest.mark.django_db
class TestSettingsPage:
    """GET/POST /settings/ — Manager profile + Config table fields."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_s", display_name="Todd",
            password_hash="x", email="todd_s@example.com",
        )
        self._login_as(client, "todd_s@example.com")
        return m

    def test_get_renders_form(self, client):
        self._setup(client)
        resp = client.get("/settings/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Anthropic API Key" in body
        assert "Email &amp; SMTP" in body or "Email & SMTP" in body
        assert "Send weekly digest" in body

    def test_get_prefills_non_sensitive_config(self, client):
        from core.services.config import set_config
        m = self._setup(client)
        set_config("smtp_server", m.id, "smtp.example.com")
        set_config("manager_email", m.id, "todd@example.com")
        resp = client.get("/settings/")
        body = resp.content.decode()
        assert "smtp.example.com" in body
        assert "todd@example.com" in body

    def test_get_never_echoes_anthropic_key(self, client):
        from core.services.config import set_config
        m = self._setup(client)
        set_config("anthropic_api_key", m.id, "sk-ant-test-secret-marker")
        resp = client.get("/settings/")
        body = resp.content.decode()
        assert "sk-ant-test-secret-marker" not in body

    def test_post_saves_profile_and_config(self, client):
        from core.services.config import get_config
        m = self._setup(client)
        resp = client.post("/settings/", {
            "display_name": "Todd Erickson",
            "timezone": "America/New_York",
            "anthropic_api_key": "sk-ant-test-new-key",
            "manager_name": "Todd",
            "manager_email": "todd@example.com",
            "smtp_server": "smtp.gmail.com",
            "smtp_port": "587",
            "smtp_user": "todd@gmail.com",
            "smtp_password": "app-pass-test-12345",
        })
        assert resp.status_code == 302
        assert resp.url == "/settings/"
        m.refresh_from_db()
        assert m.display_name == "Todd Erickson"
        assert get_config("anthropic_api_key", m.id) == "sk-ant-test-new-key"
        assert get_config("smtp_server", m.id) == "smtp.gmail.com"
        assert get_config("smtp_password", m.id) == "app-pass-test-12345"

    def test_post_blank_anthropic_key_preserves_existing(self, client):
        from core.services.config import get_config, set_config
        m = self._setup(client)
        set_config("anthropic_api_key", m.id, "sk-ant-test-original")
        client.post("/settings/", {
            "display_name": "Todd",
            "timezone": "UTC",
            "anthropic_api_key": "",  # blank — should keep existing
            "manager_name": "",
            "manager_email": "",
            "smtp_server": "",
            "smtp_port": "",
            "smtp_user": "",
            "smtp_password": "",
        })
        assert get_config("anthropic_api_key", m.id) == "sk-ant-test-original"

    def test_post_blank_smtp_password_preserves_existing(self, client):
        from core.services.config import get_config, set_config
        m = self._setup(client)
        set_config("smtp_password", m.id, "original-pass")
        client.post("/settings/", {
            "display_name": "Todd",
            "timezone": "UTC",
            "anthropic_api_key": "",
            "manager_name": "",
            "manager_email": "",
            "smtp_server": "",
            "smtp_port": "",
            "smtp_user": "",
            "smtp_password": "",
        })
        assert get_config("smtp_password", m.id) == "original-pass"

    def test_post_invalid_port_rejected(self, client):
        self._setup(client)
        resp = client.post("/settings/", {
            "display_name": "Todd",
            "timezone": "UTC",
            "anthropic_api_key": "",
            "manager_name": "",
            "manager_email": "",
            "smtp_server": "",
            "smtp_port": "not-a-number",
            "smtp_user": "",
            "smtp_password": "",
        })
        assert resp.status_code == 200  # re-renders form with errors
        assert b"Port must be numeric" in resp.content

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger_s@example.com")
        assert client.get("/settings/").status_code == 403


@pytest.mark.django_db
class TestSettingsSendDigest:
    """POST /settings/send-digest/ — on-demand weekly-digest sender."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_sd", display_name="Todd",
            password_hash="x", email="todd_sd@example.com",
        )
        self._login_as(client, "todd_sd@example.com")
        return m

    def test_get_not_allowed(self, client):
        self._setup(client)
        assert client.get("/settings/send-digest/").status_code == 405

    def test_post_calls_send_weekly_digest(self, client, mocker):
        m = self._setup(client)
        mocked = mocker.patch(
            "core.views.settings_views.send_weekly_digest",
            return_value=(True, "Digest sent."),
        )
        resp = client.post("/settings/send-digest/")
        assert resp.status_code == 302
        assert resp.url == "/settings/"
        mocked.assert_called_once_with(m.id)

    def test_post_propagates_failure_message(self, client, mocker):
        self._setup(client)
        mocker.patch(
            "core.views.settings_views.send_weekly_digest",
            return_value=(False, "SMTP not configured for this manager."),
        )
        resp = client.post("/settings/send-digest/", follow=True)
        assert resp.status_code == 200
        assert b"SMTP not configured" in resp.content

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger_sd@example.com")
        assert client.post("/settings/send-digest/").status_code == 403


@pytest.mark.django_db
class TestHealthEndpoint:
    """/health is public and reports the deployed git SHA so a deploy
    can be confirmed exactly (the gap /verify-deploy left)."""

    def test_health_is_public_and_ok(self, client):
        resp = client.get("/health/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "git_sha" in data

    def test_health_reports_render_git_commit(self, client, monkeypatch):
        monkeypatch.setenv("RENDER_GIT_COMMIT", "abc1234")
        resp = client.get("/health/")
        assert resp.json()["git_sha"] == "abc1234"

    def test_health_reports_db_ok(self, client):
        assert client.get("/health/").json()["db"] == "ok"

    def test_health_returns_503_when_db_unreachable(self, client, mocker):
        """PR-1 hardening: a process that boots but can't reach the DB
        must NOT report healthy — Render's health check would keep
        routing traffic to a service that 500s on every real page."""
        conn = mocker.patch("core.views._common.connection")
        conn.cursor.side_effect = Exception("db down")
        resp = client.get("/health/")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "error"
        assert data["db"] == "unreachable"
        # git_sha still reported so the broken deploy is identifiable
        assert "git_sha" in data


@pytest.mark.django_db
class TestLanding:
    """The public `/` page is the unauthenticated marketing/sign-in surface.
    Authenticated users skip it and go straight to /dashboard/."""

    def test_anonymous_sees_designed_landing(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.content.decode()
        # New designed page, not the old plaintext scaffold
        assert "Django scaffold" not in body
        assert "Sign in with Google" in body
        assert "/accounts/google/login/" in body
        # Uses the design system tokens (Fraunces display font + teal accent)
        assert "Fraunces" in body
        assert "bg-accent-700" in body

    def test_deploy_sha_renders_in_footer_when_set(self, client, monkeypatch):
        monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdef1234567")
        body = client.get("/").content.decode()
        # Footer shows the 7-char short SHA
        assert "deploy abcdef1" in body

    def test_deploy_sha_hidden_when_unknown(self, client):
        # No env var set → footer shouldn't show "deploy unknown"
        body = client.get("/").content.decode()
        assert "deploy unknown" not in body

    def test_authenticated_user_redirects_to_dashboard(self, client):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username="landing_redir", email="landing_redir@example.com", password="x",
        )
        client.force_login(u)
        resp = client.get("/")
        assert resp.status_code == 302
        assert resp.url.endswith("/dashboard/")


@pytest.mark.django_db
class TestAccountSignupClosed:
    """The app is Google-OAuth-only. /accounts/signup/ must not allow
    creating a User row — otherwise the email-iexact bridge middleware
    is an account-takeover surface."""

    def test_signup_get_redirects_to_google_login(self, client):
        resp = client.get("/accounts/signup/")
        # The URL-level RedirectView short-circuits before allauth renders.
        assert resp.status_code == 302
        assert "/accounts/google/login/" in resp["Location"]

    def test_signup_post_does_not_create_user(self, client):
        """Even if a future URL change accidentally re-exposes the form,
        the ClosedSignupAdapter blocks signup at the framework level."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        before = User.objects.count()
        resp = client.post("/accounts/signup/", {
            "email": "attacker@example.com",
            "password1": "ThisIs1StrongPassword!",
        })
        # Either 302 (URL redirect) or 403 (adapter rejection) is acceptable.
        # What is NOT acceptable: a 200 success and a new User row.
        assert resp.status_code in (302, 403, 404)
        assert User.objects.count() == before, \
            "Signup should be closed — no new User should have been created."


@pytest.mark.django_db
class TestEventFormTenantScoping:
    """EventForm.clean_team_member must reject foreign-tenant team_member ids
    posted directly to /events/schedule/."""

    def _setup(self, client, username="evt_tenant"):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username=username, display_name="Mgr",
            password_hash="x", email=f"{username}@example.com",
        )
        u = get_user_model().objects.create_user(
            username=f"{username}@example.com",
            email=f"{username}@example.com", password="x",
        )
        client.force_login(u)
        return m

    def test_cross_tenant_team_member_id_rejected(self, client):
        from core.models import Event
        m1 = self._setup(client, "evt_attacker")
        m2 = Manager.objects.create(
            username="evt_victim", display_name="Victim",
            password_hash="x", email="evt_victim@example.com",
        )
        # Victim's team member — should never become reachable from m1.
        victim_member = TeamMember.objects.create(name="Victim Direct", manager_id=m2.id)

        before = Event.objects.for_manager(m1.id).count()
        resp = client.post("/events/schedule/", {
            "team_member": str(victim_member.id),
            "event_type": "one_on_one",
            "title": "attacker meeting",
            "scheduled_date": "2026-06-01",
            "scheduled_time": "10:00",
            "duration_minutes": 30,
        })
        # The form should either re-render with an error (200) or redirect
        # without writing the event. Critical: no Event with the victim's
        # team_member should land in m1's tenant.
        after_attacker = Event.objects.for_manager(m1.id).count()
        assert after_attacker == before, \
            f"Cross-tenant write: attacker tenant now has {after_attacker - before} extra event(s)"
        # And the victim's tenant must also be unchanged.
        assert Event.objects.for_manager(m2.id).count() == 0
        # Sanity: response should not be a redirect to a successfully-created
        # event detail page.
        assert "events/" not in (resp.url if resp.status_code == 302 else "")


class TestRedactDbCredentials:
    """`core.utils.redact_db_credentials` must scrub `user:password@`
    from any Postgres DSN before the text reaches a log line, error
    page, or Sentry event. Sentry's default scrubber catches query
    params but not Postgres userinfo (SECURITY_PARITY.md M8).
    """

    def test_redacts_basic_userinfo(self):
        from core.utils import redact_db_credentials
        text = "connection failed: postgres://alice:s3cret@db.example.com:5432/app"
        out = redact_db_credentials(text)
        assert "alice" not in out
        assert "s3cret" not in out
        assert "postgres://***@db.example.com:5432/app" in out

    def test_redacts_postgresql_scheme(self):
        from core.utils import redact_db_credentials
        text = "DSN: postgresql://u:p@host/db"
        assert redact_db_credentials(text) == "DSN: postgresql://***@host/db"

    def test_case_insensitive_scheme(self):
        from core.utils import redact_db_credentials
        assert "POSTGRES://***@" in redact_db_credentials("POSTGRES://u:p@h/d")

    def test_handles_multiple_dsns_in_one_string(self):
        from core.utils import redact_db_credentials
        text = "primary postgres://a:b@h1/db and replica postgresql://c:d@h2/db"
        out = redact_db_credentials(text)
        assert "a:b" not in out and "c:d" not in out
        assert out.count("***@") == 2

    def test_passes_through_text_without_dsn(self):
        from core.utils import redact_db_credentials
        assert redact_db_credentials("nothing sensitive here") == "nothing sensitive here"

    def test_handles_empty_input(self):
        from core.utils import redact_db_credentials
        assert redact_db_credentials("") == ""
        assert redact_db_credentials(None) is None


class TestSentryBeforeSend:
    """`sentry_before_send` walks the Sentry event dict and pipes every
    exception value, top-level message, and breadcrumb message through
    `redact_db_credentials`. Wired via `before_send=` in mt/settings.py
    when SENTRY_DSN is set."""

    def test_redacts_exception_value(self):
        from core.utils import sentry_before_send
        event = {
            "exception": {
                "values": [{"type": "OperationalError",
                            "value": "could not connect to postgres://u:p@host/db"}]
            }
        }
        out = sentry_before_send(event, None)
        assert "u:p" not in out["exception"]["values"][0]["value"]
        assert "***@host" in out["exception"]["values"][0]["value"]

    def test_redacts_top_level_message(self):
        from core.utils import sentry_before_send
        event = {"message": "init: postgresql://alice:secret@db/app failed"}
        assert "alice:secret" not in sentry_before_send(event, None)["message"]

    def test_redacts_breadcrumb_messages(self):
        from core.utils import sentry_before_send
        event = {
            "breadcrumbs": {
                "values": [
                    {"message": "connecting postgres://x:y@h/d"},
                    {"message": "no creds here"},
                ]
            }
        }
        out = sentry_before_send(event, None)
        crumbs = out["breadcrumbs"]["values"]
        assert "x:y" not in crumbs[0]["message"]
        assert crumbs[1]["message"] == "no creds here"

    def test_handles_empty_event(self):
        from core.utils import sentry_before_send
        # Must not raise on a sparse event dict.
        out = sentry_before_send({}, None)
        assert out == {}

    def test_never_drops_the_event_on_internal_error(self):
        from core.utils import sentry_before_send
        # Deliberately malformed shape — the hook should swallow rather
        # than let its own bug suppress the real error report.
        out = sentry_before_send({"exception": "not-a-dict"}, None)
        assert out is not None


class TestPraiseToFeedbackMigration:
    """Hard regression for migration 0010.

    The migration is irreversible (reverse raises NotImplementedError) so
    a bug shipped without a test would destroy data on prod. Rather than
    use MigrationExecutor (which can't roll back over an irreversible
    operation cleanly), we load the migration module and call its forward
    function directly against the current schema, seeded with fixtures
    matching the three documented input cases:

    1. praise + team_member -> new Feedback row, source RunningNote deleted
    2. praise + no team_member (broadcast) -> RunningNote preserved
    3. non-praise -> RunningNote untouched
    """

    @staticmethod
    def _load_migration():
        # File name starts with a digit so `import` doesn't work.
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent
            / "migrations" / "0010_migrate_praise_notes_to_feedback.py"
        )
        spec = importlib.util.spec_from_file_location(
            "mig_0010", str(path),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.django_db
    def test_forward_migrates_praise_with_member_only(self):
        from datetime import datetime, timezone as dt_tz
        from django.apps import apps as global_apps

        from core.models import Feedback, Manager, RunningNote, TeamMember

        mgr = Manager.objects.create(
            username="mig_test", display_name="M",
            password_hash="x", email="mig_test@example.com",
        )
        member = TeamMember.objects.create(name="A", manager_id=mgr.id)

        # Seed: one praise+member, one broadcast-praise, one non-praise.
        RunningNote.objects.create(
            manager_id=mgr.id, team_member_id=member.id,
            note_date="2026-05-01", category="praise",
            content="nice debugging on the migration",
            created_at=datetime(2026, 5, 1, 12, 0, tzinfo=dt_tz.utc),
        )
        RunningNote.objects.create(
            manager_id=mgr.id, team_member_id=None,
            note_date="2026-05-02", category="praise",
            content="team-wide shout-out",
            created_at=datetime(2026, 5, 2, 12, 0, tzinfo=dt_tz.utc),
        )
        RunningNote.objects.create(
            manager_id=mgr.id, team_member_id=member.id,
            note_date="2026-05-03", category="concern",
            content="behind on the metric",
            created_at=datetime(2026, 5, 3, 12, 0, tzinfo=dt_tz.utc),
        )

        # Invoke the migration's forward function directly. schema_editor
        # is unused by this RunPython callable (no DDL — pure data move).
        mod = self._load_migration()
        mod.migrate_praise_to_feedback(global_apps, schema_editor=None)

        # 1. praise + team_member -> became a positive Feedback row.
        feedback = Feedback.objects.filter(
            manager_id=mgr.id, team_member_id=member.id,
        )
        assert feedback.count() == 1
        f = feedback.first()
        assert f.feedback_type == "positive"
        assert "nice debugging on the migration" in (f.behavior or "")
        assert "migrated from running note" in (f.situation or "")

        # 1b. Source RunningNote was deleted.
        praise_with_member = RunningNote.objects.filter(
            manager_id=mgr.id, category="praise", team_member_id=member.id,
        )
        assert praise_with_member.count() == 0

        # 2. Broadcast praise (team_member_id NULL) - preserved.
        broadcast = RunningNote.objects.filter(
            manager_id=mgr.id, category="praise", team_member_id__isnull=True,
        )
        assert broadcast.count() == 1
        assert "team-wide shout-out" in (broadcast.first().content or "")

        # 3. Non-praise - untouched.
        concern = RunningNote.objects.filter(
            manager_id=mgr.id, category="concern",
        )
        assert concern.count() == 1
        assert "behind on the metric" in (concern.first().content or "")

    @pytest.mark.django_db
    def test_forward_is_noop_with_no_praise_rows(self):
        """Re-running the migration on a DB with no praise rows must be a
        clean no-op — protects against the prod deploy where most managers
        will have zero praise rows and the migration runs once on empty
        input."""
        from core.models import Feedback, Manager, RunningNote, TeamMember
        from django.apps import apps as global_apps

        mgr = Manager.objects.create(
            username="noop_test", display_name="N",
            password_hash="x", email="noop_test@example.com",
        )
        TeamMember.objects.create(name="B", manager_id=mgr.id)

        before_feedback = Feedback.objects.filter(manager_id=mgr.id).count()
        before_notes = RunningNote.objects.filter(manager_id=mgr.id).count()

        mod = self._load_migration()
        mod.migrate_praise_to_feedback(global_apps, schema_editor=None)

        assert Feedback.objects.filter(manager_id=mgr.id).count() == before_feedback
        assert RunningNote.objects.filter(manager_id=mgr.id).count() == before_notes

    @pytest.mark.django_db
    def test_reverse_raises_not_implemented(self):
        """Reverse must hard-block — silently re-adding "praise" rows
        from Feedback would create duplicates and lose ordering."""
        from django.apps import apps as global_apps

        mod = self._load_migration()
        with pytest.raises(NotImplementedError):
            mod.reverse_unsupported(global_apps, schema_editor=None)


class TestSentryInitHardening:
    """Regression for the 2026-05-25 23:48 UTC purge-deleted-members crash.

    With SENTRY_DSN unset or set to a malformed value, mt.settings must
    import cleanly so Django can boot. Pre-hardening, sentry_sdk.init
    raised BadDsn on a "PASTE_VALUE_HERE" placeholder, which bubbled out
    of settings.py and crashed every gunicorn worker / cron run.
    """

    @staticmethod
    def _import_settings(extra_env):
        """Spawn a clean subprocess that imports mt.settings with the
        given env vars set. Returns (returncode, stderr_text)."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        django_root = Path(__file__).resolve().parent.parent
        # Mandatory baseline so settings.py doesn't fail on something
        # else (missing SECRET_KEY, missing env file, etc.).
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "DJANGO_SETTINGS_MODULE": "mt.settings",
            "DJANGO_SECRET_KEY": "test-secret-not-real",
            "MANAGER_TOOL_ENV": "dev",
            "DATABASE_URL": "sqlite:///:memory:",
        }
        env.update(extra_env)
        result = subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            env=env,
            cwd=str(django_root),
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode, (result.stderr or "")

    def test_malformed_dsn_does_not_crash_settings(self):
        # Exactly the value that crashed purge-deleted-members on
        # 2026-05-25 — the placeholder text I put in the cron env vars
        # before the user pasted real values.
        rc, stderr = self._import_settings({"SENTRY_DSN": "PASTE_VALUE_HERE"})
        assert rc == 0, (
            f"Django failed to import with a malformed SENTRY_DSN. "
            f"This is the regression — settings.py must catch the "
            f"BadDsn rather than let it propagate. stderr:\n{stderr}"
        )
        # Settings should log a warning rather than re-raising.
        assert (
            "Sentry init failed" in stderr
            or "BadDsn" not in stderr
        ), f"Expected the warn-log path. stderr:\n{stderr}"

    def test_empty_dsn_does_not_attempt_init(self):
        # Default behavior — no Sentry, no warning, no crash.
        rc, stderr = self._import_settings({"SENTRY_DSN": ""})
        assert rc == 0
        assert "Sentry init failed" not in stderr


@pytest.mark.django_db
class TestAuditLogView:
    """`/audit/` is a read-only browser over HR-sensitive AuditLog rows.
    Cross-tenant leakage here would be a compliance incident, so tenant
    isolation is the load-bearing assertion.
    """

    def _login_manager(self, client, username):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username=username, display_name=username.title(),
            password_hash="x", email=f"{username}@example.com",
        )
        u = get_user_model().objects.create_user(
            username=f"{username}@example.com",
            email=f"{username}@example.com", password="x",
        )
        client.force_login(u)
        return m

    def _make_audit(self, mgr, **kw):
        defaults = dict(
            manager_id=mgr.id, action="create",
            entity_type="Feedback", entity_id=1,
            summary="seeded", actor_type="user",
        )
        defaults.update(kw)
        return AuditLog.objects.create(**defaults)

    def test_returns_200_for_logged_in_manager(self, client):
        self._login_manager(client, "audit_a")
        resp = client.get("/audit/")
        assert resp.status_code == 200
        assert b"Audit log" in resp.content

    def test_lists_only_this_managers_rows(self, client):
        m1 = self._login_manager(client, "audit_b")
        m2 = Manager.objects.create(
            username="audit_b_victim", display_name="V",
            password_hash="x", email="audit_b_victim@example.com",
        )
        self._make_audit(m1, summary="m1 row")
        self._make_audit(m2, summary="m2 SECRET row")
        resp = client.get("/audit/")
        assert resp.status_code == 200
        assert b"m1 row" in resp.content
        assert b"SECRET" not in resp.content, (
            "Cross-tenant leak: m2's audit row appeared on m1's page"
        )

    def test_entity_filter_scopes_results(self, client):
        m = self._login_manager(client, "audit_c")
        self._make_audit(m, entity_type="Feedback", summary="fb row")
        self._make_audit(m, entity_type="Delegation", summary="del row")
        resp = client.get("/audit/?entity=Feedback")
        assert b"fb row" in resp.content
        assert b"del row" not in resp.content

    def test_actor_filter_only_accepts_known_values(self, client):
        m = self._login_manager(client, "audit_d")
        self._make_audit(m, actor_type="user", summary="user row")
        self._make_audit(m, actor_type="system", summary="system row")
        # Valid filter
        resp = client.get("/audit/?actor=system")
        assert b"system row" in resp.content
        assert b"user row" not in resp.content
        # Garbage filter — must not be passed to ORM (skipped silently
        # since the view whitelists actor in {"user","system"}).
        resp = client.get("/audit/?actor=__INJECT__")
        assert resp.status_code == 200
        assert b"user row" in resp.content
        assert b"system row" in resp.content

    def test_pagination_caps_page_size(self, client):
        m = self._login_manager(client, "audit_e")
        # 51 rows — one over PAGE_SIZE=50
        for i in range(51):
            self._make_audit(m, entity_id=i, summary=f"row-{i}")
        resp = client.get("/audit/")
        # Page 1 must show 50; page 2 the overflow.
        assert b"row-50" in resp.content or b"row-0" in resp.content
        # has_next must be truthy on page 1
        assert b"Next" in resp.content

    def test_entity_types_dropdown_is_tenant_scoped(self, client):
        """Dropdown options leak schema if not filtered per-manager.
        Specifically: if manager A only ever logs Feedback writes but
        manager B has logged Goal writes, A's dropdown must not show Goal."""
        m1 = self._login_manager(client, "audit_f")
        m2 = Manager.objects.create(
            username="audit_f_other", display_name="O",
            password_hash="x", email="audit_f_other@example.com",
        )
        self._make_audit(m1, entity_type="Feedback")
        self._make_audit(m2, entity_type="VerySensitiveModel")
        resp = client.get("/audit/")
        assert b"Feedback" in resp.content
        assert b"VerySensitiveModel" not in resp.content


# ============================================================
# Per-form cross-tenant team_member rejection (audit gap #1)
#
# Every form whose Meta.fields includes "team_member" must reject a
# foreign-tenant team_member_id. The pattern is uniform: __init__
# narrows the field's queryset to TeamMember.objects.active_for_manager
# (or .for_manager) of the constructing manager_id; Django's
# ModelChoiceField.to_python then rejects PKs not in that queryset.
#
# EventForm is covered by TestEventFormTenantScoping (view-level).
# This class covers the other 9 forms at the form-construction layer
# — a regression that drops the queryset scoping in any __init__ would
# fail here without needing per-form view scaffolding.
# ============================================================


@pytest.mark.django_db
class TestCrossTenantTeamMemberInForms:
    """One test per form-with-team_member-field. Each:
       1. Creates two managers + a TeamMember owned by manager 2.
       2. Constructs the form with manager_id=manager 1 and a payload
          whose team_member references manager 2's row.
       3. Asserts the form is invalid and that the team_member field
          is the source of the error."""

    @staticmethod
    def _two_managers():
        m1 = Manager.objects.create(
            username="form_m1", display_name="M1",
            password_hash="x", email="form_m1@example.com",
        )
        m2 = Manager.objects.create(
            username="form_m2", display_name="M2",
            password_hash="x", email="form_m2@example.com",
        )
        return m1, m2

    @staticmethod
    def _victim_member(m2):
        return TeamMember.objects.create(name="Victim", manager_id=m2.id)

    def _assert_team_member_rejected(self, form):
        assert not form.is_valid(), (
            "Form accepted a foreign-tenant team_member_id — "
            "queryset scoping in __init__ has regressed."
        )
        assert "team_member" in form.errors, (
            f"Form rejected the submission but the error wasn't on "
            f"team_member (got: {dict(form.errors)})"
        )

    def test_event_edit_form_rejects_cross_tenant_member(self):
        from core.forms import EventEditForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        ev = Event.objects.create(
            manager_id=m1.id, title="X", event_type="one_on_one",
            scheduled_date="2026-06-01", scheduled_time="10:00",
            status="scheduled",
        )
        form = EventEditForm(
            data={
                "title": "edited",
                "event_type": "one_on_one",
                "scheduled_date": "2026-06-02",
                "scheduled_time": "10:00",
                "duration_minutes": 30,
                "team_member": str(victim.id),
            },
            instance=ev,
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)

    def test_goal_form_rejects_cross_tenant_member(self):
        from core.forms import GoalForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        form = GoalForm(
            data={
                "team_member": str(victim.id),
                "quarter": "Q2 2026",
                "description": "x",
                "key_results": "",
                "status": "not_started",
            },
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)

    def test_skill_form_rejects_cross_tenant_member(self):
        from core.forms import SkillForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        form = SkillForm(
            data={
                "team_member": str(victim.id),
                "skill_name": "Public speaking",
                "proficiency": "",
            },
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)

    def test_development_plan_form_rejects_cross_tenant_member(self):
        from core.forms import DevelopmentPlanForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        form = DevelopmentPlanForm(
            data={
                "team_member": str(victim.id),
                "title": "Plan",
                "description": "",
                "status": "active",
            },
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)

    def test_career_conversation_form_rejects_cross_tenant_member(self):
        from core.forms import CareerConversationForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        form = CareerConversationForm(
            data={
                "team_member": str(victim.id),
                "conversation_date": "2026-06-01",
                "topic": "Career path",
                "notes": "",
                "next_steps": "",
            },
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)

    def test_delegation_form_rejects_cross_tenant_member(self):
        """DelegationForm.team_member is OPTIONAL (None == no assignee).
        That permissiveness must NOT extend to *foreign-tenant* members —
        a non-empty value still has to belong to the constructing manager."""
        from core.forms import DelegationForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        form = DelegationForm(
            data={
                "team_member": str(victim.id),
                "task": "ship it",
                "outcome_expected": "shipped",
                "autonomy_level": "guided",
                "status": "active",
            },
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)

    def test_running_note_form_rejects_cross_tenant_member(self):
        """RunningNote.team_member is OPTIONAL (broadcast sentinel). Same
        guard as DelegationForm: empty is fine, foreign-tenant id is not."""
        from core.forms import RunningNoteForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        form = RunningNoteForm(
            data={
                "team_member": str(victim.id),
                "note_date": "2026-06-01",
                "content": "obs",
                "category": "general",
            },
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)

    def test_feedback_form_rejects_cross_tenant_member(self):
        from core.forms import FeedbackForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        form = FeedbackForm(
            data={
                "team_member": str(victim.id),
                "feedback_type": "positive",
                "situation": "s",
                "behavior": "b",
                "impact": "i",
            },
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)

    def test_one_on_one_session_form_rejects_cross_tenant_member(self):
        from core.forms import OneOnOneSessionForm
        m1, m2 = self._two_managers()
        victim = self._victim_member(m2)
        form = OneOnOneSessionForm(
            data={
                "team_member": str(victim.id),
                "session_date": "2026-06-01",
            },
            manager_id=m1.id,
        )
        self._assert_team_member_rejected(form)


# ============================================================
# XSS parity with the (now-legacy) Streamlit suite (audit gap #5)
#
# Django templates auto-escape by default, but any `{{ x|safe }}`,
# `{% autoescape off %}`, or unwrapped `mark_safe()` in a view would
# silently undo that protection on the page rendering it. These tests
# pin the contract in two layers:
#   1. Static scan — no |safe / autoescape off / safeseq in any template.
#   2. Live render — seed each main page's user-controlled text fields
#      with a <script> payload, GET the page, assert the literal payload
#      doesn't reach the browser. Renders also prove auto-escaping is
#      actually in effect (the escaped form does appear).
# ============================================================


def _project_template_files():
    """Every .html file in the project's own template dir(s), sourced from
    settings.TEMPLATES DIRS rather than a hardcoded path so a moved dir
    tracks automatically. Asserts the scan matched at least one file, so a
    renamed/relocated root fails loud here instead of letting the template
    lints below pass vacuously (rglob over a missing dir yields nothing).
    Third-party app templates (allauth, under .venv via APP_DIRS) are
    intentionally out of scope — only first-party markup is linted."""
    from pathlib import Path
    from django.conf import settings

    files = []
    for root in settings.TEMPLATES[0]["DIRS"]:
        files.extend(Path(root).rglob("*.html"))
    assert files, (
        "Template lint found no files to scan — the configured template "
        "dir appears to have moved. Update settings.TEMPLATES DIRS."
    )
    return files


@pytest.mark.django_db
class TestNoUnsafeTemplateMarkers:
    """Cheap, fast regression guard. The whole-template scan catches
    `|safe`, `{% autoescape off %}`, `|safeseq`, and `mark_safe(` —
    each of which would defeat auto-escaping on the field it's applied
    to. If a template legitimately needs to render trusted HTML, this
    test will fail and the writer must justify it (and probably add an
    allowlist entry here)."""

    FORBIDDEN_MARKERS = (
        "|safe",            # The most common foot-gun.
        "|safeseq",         # Same risk class.
        "{% autoescape off %}",
        "mark_safe(",       # Should not appear inside template files at all.
    )

    def test_no_unsafe_markers_in_templates(self):
        from django.conf import settings
        offenders = []
        for path in _project_template_files():
            text = path.read_text(encoding="utf-8")
            for marker in self.FORBIDDEN_MARKERS:
                if marker in text:
                    rel = path.relative_to(settings.BASE_DIR)
                    offenders.append(f"{rel}: {marker!r}")
        assert not offenders, (
            "Template auto-escape bypasses detected — each of these would "
            "let user-controlled text render as live HTML:\n  "
            + "\n  ".join(offenders)
        )


class TestNoMultilineInlineComments:
    """Django's `{# ... #}` comment syntax is single-line only. A comment
    that spans lines is not recognized by the template engine and renders
    as literal visible text (in <head> the browser hoists it to the top of
    <body>). Multi-line comments must use {% comment %}...{% endcomment %}."""

    @staticmethod
    def _line_has_unclosed_comment(line):
        # Walk EVERY `{#` on the line — each must be closed by a later `#}`
        # on the same line. Checking only the first opener would miss a
        # line like `{# ok #} ... {# spilled` where the first comment's
        # `#}` masks a second, still-open opener.
        idx = 0
        while (opener := line.find("{#", idx)) != -1:
            closer = line.find("#}", opener + 2)
            if closer == -1:
                return True
            idx = closer + 2
        return False

    def test_every_inline_comment_closes_on_its_own_line(self):
        from django.conf import settings
        offenders = []
        for path in _project_template_files():
            lines = path.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, 1):
                if self._line_has_unclosed_comment(line):
                    rel = path.relative_to(settings.BASE_DIR)
                    offenders.append(f"{rel}:{lineno}")
        assert not offenders, (
            "Multi-line {# #} comments detected — Django only strips these "
            "when opened and closed on the same line; these render as "
            "visible page text. Use {% comment %} blocks instead:\n  "
            + "\n  ".join(offenders)
        )


@pytest.mark.django_db
class TestPageRenderEscapesUserContent:
    """Live-render tests. For each main page that surfaces user-typed
    text, seed a `<script>` payload and assert the response body never
    contains the live, unescaped substring `<script>alert("xss")</script>`.

    The payload chosen is distinctive enough that a substring search is
    unambiguous. We also assert the *escaped* form is present so a
    template that simply drops the field entirely (and trivially
    "passes" the no-live-script check) still fails."""

    PAYLOAD = '<script>alert("xss")</script>'
    ESCAPED_FRAGMENT = "&lt;script&gt;"  # html.escape(PAYLOAD)[:14]

    def _login(self, client, username="xss_mgr"):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username=username, display_name="Mgr",
            password_hash="x", email=f"{username}@example.com",
        )
        u = get_user_model().objects.create_user(
            username=f"{username}@example.com",
            email=f"{username}@example.com", password="x",
        )
        client.force_login(u)
        return m

    def _assert_payload_escaped(self, response, expect_escaped=True):
        body = response.content.decode(errors="replace")
        assert self.PAYLOAD not in body, (
            "Live <script> payload reached the rendered page — a template "
            "is rendering user-controlled text without auto-escape "
            "(check for |safe / autoescape off / mark_safe)."
        )
        if expect_escaped:
            assert self.ESCAPED_FRAGMENT in body, (
                "Payload didn't reach the page in any form — the seeded "
                "value isn't being rendered, so this test isn't actually "
                "proving anything. Adjust the seed/path."
            )

    def test_team_members_page_escapes_member_name(self, client):
        m = self._login(client, "xss_team")
        TeamMember.objects.create(name=self.PAYLOAD, manager_id=m.id)
        self._assert_payload_escaped(client.get("/team/"))

    def test_decisions_page_escapes_title_and_context(self, client):
        m = self._login(client, "xss_dec")
        Decision.objects.create(
            manager_id=m.id,
            title=self.PAYLOAD,
            context="ctx", alternatives="alt",
            rationale="r", expected_outcome="x",
            status="active",
        )
        self._assert_payload_escaped(client.get("/decisions/"))

    def test_journal_page_escapes_entry_content(self, client):
        m = self._login(client, "xss_jrn")
        JournalEntry.objects.create(
            manager_id=m.id,
            entry_date="2026-06-01",
            entry_type="daily",
            content=self.PAYLOAD,
        )
        self._assert_payload_escaped(client.get("/journal/"))

    def test_feedback_page_escapes_sbi_fields(self, client):
        """Situation / behavior / impact are the highest-volume free-text
        fields in the app — they're rendered on the feedback list and
        also flow into the weekly digest. Pin escape on the list view."""
        m = self._login(client, "xss_fb")
        tm = TeamMember.objects.create(name="Direct", manager_id=m.id)
        Feedback.objects.create(
            manager_id=m.id, team_member=tm,
            feedback_type="positive",
            situation=self.PAYLOAD,
            behavior="b", impact="i",
        )
        self._assert_payload_escaped(client.get("/feedback/"))

    def test_notes_page_escapes_note_content(self, client):
        m = self._login(client, "xss_notes")
        tm = TeamMember.objects.create(name="Direct", manager_id=m.id)
        RunningNote.objects.create(
            manager_id=m.id, team_member=tm,
            note_date="2026-06-01",
            content=self.PAYLOAD,
            category="general",
        )
        self._assert_payload_escaped(client.get("/notes/"))

    def test_goals_page_escapes_description(self, client):
        m = self._login(client, "xss_goals")
        tm = TeamMember.objects.create(name="Direct", manager_id=m.id)
        Goal.objects.create(
            manager_id=m.id, team_member=tm,
            quarter="Q2 2026",
            description=self.PAYLOAD,
            key_results="",
            status="not_started",
        )
        self._assert_payload_escaped(client.get("/goals/"))

    def test_delegations_page_escapes_task(self, client):
        m = self._login(client, "xss_del")
        Delegation.objects.create(
            manager_id=m.id,
            task=self.PAYLOAD,
            outcome_expected="o",
            autonomy_level="guided",
            status="active",
        )
        self._assert_payload_escaped(client.get("/delegations/"))


# ============================================================
# events_send_invite view tests (audit gap #2)
#
# POST /events/<id>/invite/ — sends an RFC 5545 calendar invite via
# SMTP and writes an audit log entry. The view has three side effects:
#   1. Sets events.calendar_invite_sent = 1 on success.
#   2. Writes an AuditLog row (action=create, entity=CalendarInvite).
#   3. Calls send_calendar_invite which opens an SMTP connection.
#
# Before these tests existed only the service-layer
# (test_send_calendar_invite_no_smtp) was covered. The view's
# cross-tenant guard, missing-email branch, no-audit-on-failure
# invariant, and HTTP-method guard were all untested.
#
# SMTP is mocked via unittest.mock.patch on
# `core.services.calendar.send_calendar_invite` — that's where the
# view's inline `from core.services.calendar import ...` resolves at
# call time, so the patch takes effect there.
# ============================================================


@pytest.mark.django_db
class TestEventsSendInvite:

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client, username="invite_mgr", login=True,
               member_email="direct@example.com"):
        m = Manager.objects.create(
            username=username, display_name="Mgr",
            password_hash="x", email=f"{username}@example.com",
        )
        if login:
            self._login_as(client, f"{username}@example.com")
        tm = TeamMember.objects.create(
            name="Direct", manager_id=m.id, email=member_email,
        )
        ev = Event.objects.create(
            manager_id=m.id, title="1:1", event_type="one_on_one",
            team_member=tm, scheduled_date="2026-06-01",
            scheduled_time="10:00", status="scheduled",
            calendar_invite_sent=0,
        )
        return m, tm, ev

    def test_get_not_allowed(self, client):
        m, _, ev = self._setup(client)
        resp = client.get(f"/events/{ev.id}/invite/")
        assert resp.status_code == 405

    def test_anonymous_post_redirects_to_login(self, client):
        m, _, ev = self._setup(client, login=False)
        resp = client.post(f"/events/{ev.id}/invite/")
        # @login_required → 302 to login; the route should NOT execute.
        assert resp.status_code == 302
        ev.refresh_from_db()
        assert ev.calendar_invite_sent == 0
        assert AuditLog.objects.filter(entity_type="CalendarInvite").count() == 0

    def test_logged_in_user_with_no_manager_blocked(self, client):
        """A logged-in user whose email matches no Manager row is rejected
        by _require_manager. The view must NOT mutate or audit.

        _require_manager returns 403 OR redirects to onboarding depending
        on configuration — assert it's not a 200, and that no side effect
        landed."""
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username="orphan@example.com",
            email="orphan@example.com", password="x",
        )
        # Need an event to address, but it must belong to *someone*; this
        # test exercises the "logged in user has no Manager row" branch.
        m_owner = Manager.objects.create(
            username="ev_owner", display_name="Owner",
            password_hash="x", email="ev_owner@example.com",
        )
        tm = TeamMember.objects.create(
            name="D", manager_id=m_owner.id, email="d@example.com",
        )
        ev = Event.objects.create(
            manager_id=m_owner.id, title="X", event_type="one_on_one",
            team_member=tm, scheduled_date="2026-06-01",
            scheduled_time="10:00", status="scheduled",
            calendar_invite_sent=0,
        )
        client.force_login(u)
        resp = client.post(f"/events/{ev.id}/invite/")
        # The view should not succeed.
        assert resp.status_code != 200 or "invite_success" not in resp.context
        ev.refresh_from_db()
        assert ev.calendar_invite_sent in (0, None), \
            "No-manager request must not flip the sent flag"
        assert AuditLog.objects.filter(entity_type="CalendarInvite").count() == 0

    def test_cross_tenant_event_returns_404(self, client):
        """Manager A POSTs to an event owned by manager B. The
        get_object_or_404 on for_manager(A.id) must reject."""
        m_a, _, _ = self._setup(client, "invite_a")
        m_b = Manager.objects.create(
            username="invite_b", display_name="B",
            password_hash="x", email="invite_b@example.com",
        )
        tm_b = TeamMember.objects.create(
            name="B Direct", manager_id=m_b.id, email="b_direct@example.com",
        )
        victim_ev = Event.objects.create(
            manager_id=m_b.id, title="B's meeting", event_type="one_on_one",
            team_member=tm_b, scheduled_date="2026-06-01",
            scheduled_time="10:00", status="scheduled",
            calendar_invite_sent=0,
        )
        resp = client.post(f"/events/{victim_ev.id}/invite/")
        assert resp.status_code == 404
        # No mutation on victim's row.
        victim_ev.refresh_from_db()
        assert victim_ev.calendar_invite_sent == 0
        # No audit row in either tenant.
        assert AuditLog.objects.for_manager(m_a.id).filter(
            entity_type="CalendarInvite"
        ).count() == 0
        assert AuditLog.objects.for_manager(m_b.id).filter(
            entity_type="CalendarInvite"
        ).count() == 0

    def test_event_without_team_member_returns_error_no_smtp(self, client):
        """If the event has no team_member at all (e.g. all-team event),
        we must NOT attempt SMTP and NOT write an audit row."""
        from unittest.mock import patch
        m = Manager.objects.create(
            username="invite_nomem", display_name="M",
            password_hash="x", email="invite_nomem@example.com",
        )
        self._login_as(client, "invite_nomem@example.com")
        ev = Event.objects.create(
            manager_id=m.id, title="All-hands", event_type="other",
            team_member=None,
            scheduled_date="2026-06-01", scheduled_time="10:00",
            status="scheduled", calendar_invite_sent=0,
        )
        with patch("core.services.calendar.send_calendar_invite") as mock_send:
            resp = client.post(f"/events/{ev.id}/invite/")
        assert resp.status_code == 200
        assert mock_send.call_count == 0, \
            "send_calendar_invite must not be reached when team_member is None"
        body = resp.content.decode()
        assert "No email address" in body
        ev.refresh_from_db()
        assert ev.calendar_invite_sent == 0
        assert AuditLog.objects.filter(entity_type="CalendarInvite").count() == 0

    def test_team_member_without_email_returns_error_no_smtp(self, client):
        from unittest.mock import patch
        m, tm, ev = self._setup(client, "invite_noemail", member_email=None)
        with patch("core.services.calendar.send_calendar_invite") as mock_send:
            resp = client.post(f"/events/{ev.id}/invite/")
        assert resp.status_code == 200
        assert mock_send.call_count == 0, (
            "send_calendar_invite must not be reached when team_member "
            "has no email — empty email is a no-op, not an SMTP error."
        )
        body = resp.content.decode()
        assert "No email address" in body
        ev.refresh_from_db()
        assert ev.calendar_invite_sent == 0
        assert AuditLog.objects.filter(entity_type="CalendarInvite").count() == 0

    def test_send_success_sets_flag_and_writes_audit(self, client):
        from unittest.mock import patch
        m, tm, ev = self._setup(client, "invite_ok")
        with patch(
            "core.services.calendar.send_calendar_invite",
            return_value=(True, "Email sent"),
        ) as mock_send:
            resp = client.post(f"/events/{ev.id}/invite/")
        assert resp.status_code == 200
        assert mock_send.call_count == 1
        # Mock was called with (event, recipient_email, recipient_name, manager_id=...)
        args, kwargs = mock_send.call_args
        assert args[0].id == ev.id, "wrong event passed to invite sender"
        assert args[1] == tm.email
        assert kwargs.get("manager_id") == m.id
        ev.refresh_from_db()
        assert ev.calendar_invite_sent == 1, \
            "Successful send must flip calendar_invite_sent to 1"
        # Audit row exists, scoped to this manager, with the right shape.
        logs = list(AuditLog.objects.for_manager(m.id).filter(
            entity_type="CalendarInvite"
        ))
        assert len(logs) == 1
        log = logs[0]
        assert log.action == "create"
        assert log.entity_id == ev.id
        assert tm.email in (log.summary or "")

    def test_send_failure_no_flag_no_audit(self, client):
        """When send_calendar_invite returns (False, msg), the view must
        NOT flip calendar_invite_sent and MUST NOT write an audit row.
        Auditing a failed send would corrupt the trail."""
        from unittest.mock import patch
        m, tm, ev = self._setup(client, "invite_fail")
        with patch(
            "core.services.calendar.send_calendar_invite",
            return_value=(False, "SMTP authentication failed"),
        ):
            resp = client.post(f"/events/{ev.id}/invite/")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "SMTP" in body or "auth" in body.lower(), \
            "Failure message should surface in the rendered detail page"
        ev.refresh_from_db()
        assert ev.calendar_invite_sent == 0, \
            "Failed send must NOT flip calendar_invite_sent"
        assert AuditLog.objects.for_manager(m.id).filter(
            entity_type="CalendarInvite"
        ).count() == 0, "Failed send must NOT write an audit row"

    def test_audit_row_uses_acting_manager_id_not_event_owner(self, client):
        """Edge case: even though the view's get_object_or_404 already
        scopes to the acting manager (so event.manager_id == acting
        manager.id always), pin the audit row's manager_id to the
        acting manager explicitly. Regression guard for log_mutation
        being called with the wrong manager_id (e.g. event.manager_id
        on a SET NULL'd recurring child)."""
        from unittest.mock import patch
        m, tm, ev = self._setup(client, "invite_owner")
        with patch(
            "core.services.calendar.send_calendar_invite",
            return_value=(True, "Email sent"),
        ):
            client.post(f"/events/{ev.id}/invite/")
        log = AuditLog.objects.filter(entity_type="CalendarInvite").get()
        assert log.manager_id == m.id


@pytest.mark.django_db
class TestGetConfigFailLoud:
    """PR-1 review finding: a rotated/missing CONFIG_ENCRYPTION_KEY must
    be LOUD (ERROR log), not indistinguishable from 'never configured'.
    Previously get_config's blanket `except Exception: return default`
    made both cases return None silently — the render.yaml:76-79
    silent-cron bug class."""

    def _manager(self):
        return Manager.objects.create(
            username="cfg_loud", display_name="CfgLoud",
            password_hash="h", email="cfg_loud@example.com",
        )

    def test_decrypt_failure_returns_default_and_logs_error(self, caplog):
        import logging as _logging

        from core.models import Config
        from core.services.config import get_config

        m = self._manager()
        # A sensitive key whose stored value is NOT valid Fernet
        # ciphertext — what prod rows look like after a key rotation.
        Config.objects.create(
            manager_id=m.id, key="smtp_password", value="enc:not-real-fernet",
        )
        with caplog.at_level(_logging.ERROR, logger="core.services.config"):
            assert get_config("smtp_password", m.id, default=None) is None
        messages = [r.getMessage() for r in caplog.records
                    if r.name == "core.services.config"]
        assert any(
            "failed to decrypt" in msg and "smtp_password" in msg
            for msg in messages
        ), f"expected a loud decrypt-failure ERROR, got: {messages}"

    def test_malformed_encryption_key_returns_default_and_logs_error(
        self, caplog, monkeypatch,
    ):
        """Review finding (PR #121): a present-but-INVALID key (bad
        paste during rotation) makes Fernet() raise a bare ValueError.
        decrypt_value must wrap it as EncryptionUnavailableError so
        get_config fails loud + returns default instead of crashing
        the caller — same class as the Sentry BadDsn incident."""
        import logging as _logging

        from core.models import Config
        from core.services.config import get_config

        m = self._manager()
        Config.objects.create(
            manager_id=m.id, key="smtp_password", value="enc:whatever",
        )
        monkeypatch.setenv("CONFIG_ENCRYPTION_KEY", "not-a-valid-fernet-key")
        with caplog.at_level(_logging.ERROR, logger="core.services.config"):
            assert get_config("smtp_password", m.id, default=None) is None
        messages = [r.getMessage() for r in caplog.records
                    if r.name == "core.services.config"]
        assert any(
            "failed to decrypt" in msg and "smtp_password" in msg
            for msg in messages
        ), f"malformed key must fail loud, got: {messages}"

    def test_missing_row_returns_default_without_error_log(self, caplog):
        import logging as _logging

        from core.services.config import get_config

        m = self._manager()
        with caplog.at_level(_logging.ERROR):
            assert get_config("smtp_password", m.id, default="fb") == "fb"
        errors = [r for r in caplog.records if r.levelno >= _logging.ERROR]
        assert not errors, "genuinely-unconfigured must stay quiet"

    def test_valid_encrypted_value_still_roundtrips(self):
        from core.services.config import get_config, set_config

        m = self._manager()
        set_config("smtp_password", m.id, "s3cret")
        assert get_config("smtp_password", m.id) == "s3cret"


class TestNoSilentExcepts:
    """Ported from legacy/tests/test_no_silent_excepts.py (AUDIT M6):
    a production except block whose body is just `pass` is the
    canonical silent failure — every except must log, assign a
    fallback, or re-raise. Scans core/, coaching/, and mt/; tests and
    migrations are excluded, as is scripts/ (the PG smoke harness is
    test code whose expected-exception asserts legitimately pass)."""

    def test_no_silent_except_pass(self):
        import ast
        from pathlib import Path

        django_root = Path(__file__).resolve().parent.parent
        offenders = []
        for pkg in ("core", "coaching", "mt"):
            for path in sorted((django_root / pkg).rglob("*.py")):
                if "migrations" in path.parts:
                    continue
                if path.name in ("tests.py", "settings_test.py"):
                    continue
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.ExceptHandler)
                        and len(node.body) == 1
                        and isinstance(node.body[0], ast.Pass)
                    ):
                        label = ("bare" if node.type is None
                                 else ast.unparse(node.type))
                        offenders.append(
                            f"{path.relative_to(django_root)}:"
                            f"{node.lineno} ({label})"
                        )
        assert not offenders, (
            "Silent `except: pass` blocks found — every except must "
            f"log, assign a fallback, or re-raise: {offenders}"
        )


class TestCompiledCssCoverage:
    """Roadmap PR 2 guard: Tailwind is a compiled artifact now (the Play
    CDN compiled classes at runtime). Every class token used in
    templates or Python-emitted markup must exist in static/css/tw.css,
    or it silently renders unstyled. Failing here means: rebuild —
    TAILWINDCSS_VERSION=v3.4.17 tailwindcss -c tailwind.config.js
    -i static/src/input.css -o static/css/tw.css --minify"""

    def test_every_used_class_exists_in_compiled_css(self):
        import glob as _glob
        import re as _re
        from pathlib import Path

        django_root = Path(__file__).resolve().parent.parent
        css = (django_root / "static/css/tw.css").read_text(encoding="utf-8")

        # Templates via the shared helper (review finding: hand-rolled
        # globs reintroduce the hardcoded-path / vacuous-pass / encoding
        # defects _project_template_files() was built to prevent).
        tokens = set()
        for path in _project_template_files():
            for m in _re.findall(
                r'class="([^"]*)"', path.read_text(encoding="utf-8"),
            ):
                m = _re.sub(r"{%[^%]*%}|{{[^}]*}}|{#[^#]*#}", " ", m)
                tokens.update(m.split())
        # Python files that emit markup with Tailwind classes — glob the
        # same trees as tailwind.config.js `content` so this guard can't
        # drift as files are added. tests.py excluded: assertion strings
        # aren't emitted markup.
        py_files = []
        for pkg in ("core", "coaching"):
            py_files += _glob.glob(str(django_root / pkg / "**" / "*.py"),
                                   recursive=True)
        assert py_files, "py-file scan matched nothing — glob roots moved?"
        for f in sorted(py_files):
            if Path(f).name == "tests.py":
                continue
            src = Path(f).read_text(encoding="utf-8")
            for m in _re.findall(
                r'["\']class["\']\s*:\s*["\']([^"\']*)["\']', src,
            ):
                tokens.update(m.split())
            for m in _re.findall(r'class=\\?"([^"\\]*)\\?"', src):
                tokens.update(m.split())

        missing = []
        for t in sorted(tokens):
            if not t or t.startswith("{") or t.endswith("}"):
                continue
            sel = t
            for ch in ":./[]#%":
                sel = sel.replace(ch, "\\" + ch)
            # Delimiter-aware match (review finding: plain substring
            # containment false-passes prefix classes — bare "shadow"
            # would match inside ".shadow-lg" despite having no rule).
            if not _re.search(
                _re.escape("." + sel) + r"(?![-\w])", css,
            ):
                missing.append(t)
        assert not missing, (
            "Class tokens missing from compiled static/css/tw.css — "
            f"rebuild it (see docstring): {missing}"
        )


@pytest.mark.django_db
class TestUnifiedSearch:
    """Roadmap PR 3: /search/?q= sweeps every content model via
    for_manager and groups hits. Deep-link assertions cover the three
    per-item-route archetypes (detail page, edit page x2); list-page
    links are covered by the group-presence assertions. Cross-tenant
    isolation matters double here — this is the one view that touches
    every model at once."""

    TOKEN = "zebrafish"

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="testpw",
        )
        client.force_login(u)
        return u

    def _manager(self, slug):
        return Manager.objects.create(
            username=f"search_{slug}", display_name=slug.title(),
            password_hash="h", email=f"search_{slug}@example.com",
        )

    def _seed_all_models(self, m, token):
        from core.models import (
            ActionItem, CareerConversation, Decision, Delegation, Event,
            Feedback, Goal, JournalEntry, OneOnOneSession, RunningNote,
            TeamMember,
        )
        tm = TeamMember.objects.create(
            manager_id=m.id, name=f"Pat {token}", role="Engineer",
        )
        OneOnOneSession.objects.create(
            manager_id=m.id, team_member=tm, session_date="2026-07-01",
            direct_notes=f"discussed {token} rollout", status="completed",
        )
        JournalEntry.objects.create(
            manager_id=m.id, entry_date="2026-07-01",
            content=f"thinking about {token} strategy",
        )
        RunningNote.objects.create(
            manager_id=m.id, team_member=tm, note_date="2026-07-01",
            content=f"{token} status update",
        )
        Decision.objects.create(
            manager_id=m.id, title=f"Adopt {token}", context="ctx",
        )
        Feedback.objects.create(
            manager_id=m.id, team_member=tm, feedback_type="praise",
            situation=f"handled the {token} incident",
        )
        Delegation.objects.create(
            manager_id=m.id, team_member=tm, task=f"own the {token} runbook",
            status="active",
        )
        ActionItem.objects.create(
            manager_id=m.id, description=f"review {token} PRs",
            status="pending",
        )
        Goal.objects.create(
            manager_id=m.id, team_member=tm, quarter="Q3 2026",
            description=f"ship {token} v1",
        )
        CareerConversation.objects.create(
            manager_id=m.id, team_member=tm,
            conversation_date="2026-07-01", topic=f"{token} growth path",
        )
        Event.objects.create(
            manager_id=m.id, title=f"{token} kickoff",
            event_type="meeting", scheduled_date="2026-07-10",
            scheduled_time="10:00", status="scheduled",
        )

    EXPECTED_GROUPS = (
        "Meetings", "Journal", "Notes", "Decisions", "Feedback",
        "Delegations", "To Do", "Goals", "Career", "Team", "Events",
    )

    def test_hits_every_model_grouped_with_deep_links(self, client):
        m = self._manager("a")
        self._login_as(client, m.email)
        self._seed_all_models(m, self.TOKEN)
        resp = client.get(f"/search/?q={self.TOKEN}")
        assert resp.status_code == 200
        body = resp.content.decode()
        for label in self.EXPECTED_GROUPS:
            assert label in body, f"missing group: {label}"
        # Deep links to per-item pages, not just list pages
        from core.models import Decision, JournalEntry, OneOnOneSession
        s = OneOnOneSession.objects.for_manager(m.id).get()
        e = JournalEntry.objects.for_manager(m.id).get()
        d = Decision.objects.for_manager(m.id).get()
        assert f"/meetings/{s.id}/" in body
        assert f"/journal/{e.id}/edit/" in body
        assert f"/decisions/{d.id}/edit/" in body

    def test_cross_tenant_returns_zero_hits(self, client):
        owner = self._manager("owner")
        intruder = self._manager("intruder")
        self._seed_all_models(owner, self.TOKEN)
        self._login_as(client, intruder.email)
        resp = client.get(f"/search/?q={self.TOKEN}")
        assert resp.status_code == 200
        body = resp.content.decode()
        # The query echoes back in the input box (value="{{ q }}") — the
        # isolation signal is that none of the OWNER'S CONTENT leaks.
        assert "No matches" in body
        for owner_content in (
            f"discussed {self.TOKEN}", f"Pat {self.TOKEN}",
            f"Adopt {self.TOKEN}", f"{self.TOKEN} kickoff",
            f"thinking about {self.TOKEN}",
        ):
            assert owner_content not in body

    def test_short_query_prompts_for_more(self, client):
        m = self._manager("short")
        self._login_as(client, m.email)
        resp = client.get("/search/?q=z")
        assert resp.status_code == 200
        assert b"at least 2 characters" in resp.content

    def test_no_manager_yields_403(self, client):
        self._login_as(client, "stranger_search@example.com")
        assert client.get("/search/?q=anything").status_code == 403

    def test_per_model_cap_limits_each_group(self, client):
        from core.models import ActionItem
        m = self._manager("capped")
        self._login_as(client, m.email)
        for i in range(25):
            ActionItem.objects.create(
                manager_id=m.id, status="pending",
                description=f"{self.TOKEN} capitem {i}",
            )
        body = client.get(f"/search/?q={self.TOKEN}").content.decode()
        assert "(20)" in body, "group badge must show the capped count"
        # Newest-first (-id): items 24..5 render, 4..0 fall past the cap.
        assert "capitem 24" in body
        assert "capitem 5" in body
        assert "capitem 4 " not in body and "capitem 4<" not in body
        assert "capitem 0 " not in body and "capitem 0<" not in body


@pytest.mark.django_db
class TestInbox:
    """Roadmap PR 4: capture triage queue. Quick-add lands pending
    items; each triages into exactly one target record (CAS-guarded
    against double submits); badge counts pending; tenant-isolated."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="testpw",
        )
        client.force_login(u)
        return u

    def _manager(self, slug):
        return Manager.objects.create(
            username=f"inbox_{slug}", display_name=slug.title(),
            password_hash="h", email=f"inbox_{slug}@example.com",
        )

    def _item(self, m, body="captured thought", **kw):
        from core.models import InboxItem
        kw.setdefault("source", "quick")
        return InboxItem.objects.create(manager_id=m.id, body=body, **kw)

    def test_quick_add_creates_pending_item_and_audit_row(self, client):
        from core.models import InboxItem
        m = self._manager("qa")
        self._login_as(client, m.email)
        resp = client.post("/inbox/quick/", {"body": "call the auditor"})
        assert resp.status_code == 200
        item = InboxItem.objects.for_manager(m.id).get()
        assert item.status == "pending" and item.source == "quick"
        assert AuditLog.objects.for_manager(m.id).filter(
            entity_type="InboxItem", entity_id=item.id,
        ).count() == 1

    def test_quick_add_empty_body_shows_toast_no_item(self, client):
        # 200 (not 422) so htmx renders the toast; no item created.
        from core.models import InboxItem
        m = self._manager("empty")
        self._login_as(client, m.email)
        resp = client.post("/inbox/quick/", {"body": "  "})
        assert resp.status_code == 200
        assert b"Nothing to capture" in resp.content
        assert InboxItem.objects.for_manager(m.id).count() == 0

    def test_triage_to_journal(self, client):
        m = self._manager("tj")
        self._login_as(client, m.email)
        item = self._item(m, subject="Idea", body="rotate demo duty")
        resp = client.post(f"/inbox/{item.id}/triage/", {"target": "journal"})
        assert resp.status_code == 200
        entry = JournalEntry.objects.for_manager(m.id).get()
        assert "rotate demo duty" in entry.content
        assert "Idea" in entry.content
        # entry_type must be a valid choice, not the "" default that
        # .create() would silently insert (blank type breaks the UI).
        assert entry.entry_type == "daily"
        item.refresh_from_db()
        assert item.status == "triaged"
        assert item.triaged_entity_type == "JournalEntry"
        assert item.triaged_entity_id == entry.id
        assert AuditLog.objects.for_manager(m.id).filter(
            entity_type="JournalEntry", entity_id=entry.id,
        ).exists()

    def test_triage_to_todo_and_decision(self, client):
        from core.models import Decision
        m = self._manager("td")
        self._login_as(client, m.email)
        a = self._item(m, body="book skip-levels")
        b = self._item(m, subject="Adopt trunk-based dev", body="ctx here")
        client.post(f"/inbox/{a.id}/triage/", {"target": "todo"})
        client.post(f"/inbox/{b.id}/triage/", {"target": "decision"})
        todo = ActionItem.objects.for_manager(m.id).get()
        assert "book skip-levels" in todo.description
        assert todo.status == "pending"
        d = Decision.objects.for_manager(m.id).get()
        assert d.title == "Adopt trunk-based dev"
        assert d.context == "ctx here"

    def test_triage_to_note_with_member(self, client):
        from core.models import RunningNote
        m = self._manager("tn")
        self._login_as(client, m.email)
        tm = TeamMember.objects.create(manager_id=m.id, name="Pat")
        item = self._item(m, body="wants Q4 stretch role")
        client.post(f"/inbox/{item.id}/triage/",
                    {"target": "note", "member": str(tm.id)})
        note = RunningNote.objects.for_manager(m.id).get()
        assert note.team_member_id == tm.id
        assert "stretch role" in note.content

    def test_dismiss(self, client):
        m = self._manager("dis")
        self._login_as(client, m.email)
        item = self._item(m)
        client.post(f"/inbox/{item.id}/triage/", {"target": "dismiss"})
        item.refresh_from_db()
        assert item.status == "dismissed"
        assert JournalEntry.objects.for_manager(m.id).count() == 0

    def test_double_submit_files_exactly_once(self, client):
        """The mobile double-tap: two POSTs race for one item; the CAS
        claim must let exactly one create a target row."""
        m = self._manager("race")
        self._login_as(client, m.email)
        item = self._item(m, body="only once")
        r1 = client.post(f"/inbox/{item.id}/triage/", {"target": "journal"})
        r2 = client.post(f"/inbox/{item.id}/triage/", {"target": "journal"})
        assert r1.status_code == 200 and r2.status_code == 200
        assert JournalEntry.objects.for_manager(m.id).count() == 1

    def test_unknown_target_never_claims(self, client):
        m = self._manager("unk")
        self._login_as(client, m.email)
        item = self._item(m)
        assert client.post(
            f"/inbox/{item.id}/triage/", {"target": "bogus"},
        ).status_code == 422
        item.refresh_from_db()
        assert item.status == "pending", "invalid action must not claim"

    def test_create_failure_rolls_back_claim(self, client, mocker):
        """If the target-row create raises, the CAS claim rolls back so
        the item returns to the queue (no stuck 'triaged' with no
        target). create is mocked to raise, so this proves the STATUS
        rollback specifically."""
        m = self._manager("rollback")
        self._login_as(client, m.email)
        item = self._item(m, body="must survive a failed file")
        mocker.patch(
            "core.views.inbox.JournalEntry.objects.create",
            side_effect=RuntimeError("boom"),
        )
        try:
            client.post(f"/inbox/{item.id}/triage/", {"target": "journal"})
        except RuntimeError:
            pass  # the view lets it 500; the point is the DB state
        item.refresh_from_db()
        assert item.status == "pending", "claim must roll back on create failure"

    def test_stamp_failure_rolls_back_the_created_child(self, client, mocker):
        """The real no-orphan proof: the JournalEntry create SUCCEEDS,
        then the stamp save() fails — the whole transaction must roll
        back, leaving BOTH the item pending AND no orphan JournalEntry.
        (The create-mock test above can't prove this; nothing is ever
        inserted there.)"""
        from core.models import InboxItem
        m = self._manager("orphan")
        self._login_as(client, m.email)
        item = self._item(m, body="no orphan allowed")
        # Fail the post-create stamp (item.save with update_fields).
        real_save = InboxItem.save

        def boom(self_obj, *a, **k):
            if k.get("update_fields") == [
                "triaged_entity_type", "triaged_entity_id",
            ]:
                raise RuntimeError("stamp boom")
            return real_save(self_obj, *a, **k)

        mocker.patch.object(InboxItem, "save", autospec=True, side_effect=boom)
        try:
            client.post(f"/inbox/{item.id}/triage/", {"target": "journal"})
        except RuntimeError:
            pass
        item.refresh_from_db()
        assert item.status == "pending", "claim must roll back"
        assert JournalEntry.objects.for_manager(m.id).count() == 0, \
            "the created JournalEntry must NOT persist as an orphan"

    def test_cross_tenant_triage_is_a_noop(self, client):
        owner = self._manager("owner2")
        intruder = self._manager("intr2")
        item = self._item(owner, body="not yours")
        self._login_as(client, intruder.email)
        client.post(f"/inbox/{item.id}/triage/", {"target": "journal"})
        item.refresh_from_db()
        assert item.status == "pending", "intruder must not claim the item"
        assert JournalEntry.objects.for_manager(intruder.id).count() == 0
        assert JournalEntry.objects.for_manager(owner.id).count() == 0

    def test_retriage_different_target_after_claim_is_noop(self, client):
        """The CAS filter (status IN pending,failed) blocks a SECOND,
        different triage action on an already-claimed item — not just a
        same-target replay."""
        m = self._manager("retriage")
        self._login_as(client, m.email)
        item = self._item(m, body="file me once")
        client.post(f"/inbox/{item.id}/triage/", {"target": "journal"})
        client.post(f"/inbox/{item.id}/triage/", {"target": "todo"})
        assert JournalEntry.objects.for_manager(m.id).count() == 1
        from core.models import ActionItem as _AI
        assert _AI.objects.for_manager(m.id).count() == 0, \
            "second (different) triage must not file the already-claimed item"

    def test_note_with_cross_tenant_member_falls_back_to_broadcast(self, client):
        """Raw-POST defense: a member_id belonging to ANOTHER manager
        must resolve to broadcast (None), never attach cross-tenant."""
        from core.models import RunningNote
        owner = self._manager("noteowner")
        other = self._manager("noteother")
        other_member = TeamMember.objects.create(
            manager_id=other.id, name="Not Yours",
        )
        self._login_as(client, owner.email)
        item = self._item(owner, body="attach me somewhere")
        client.post(f"/inbox/{item.id}/triage/",
                    {"target": "note", "member": str(other_member.id)})
        note = RunningNote.objects.for_manager(owner.id).get()
        assert note.team_member_id is None, "must not attach cross-tenant member"

    def test_note_with_nonnumeric_member_does_not_500(self, client):
        from core.models import RunningNote
        m = self._manager("junkmember")
        self._login_as(client, m.email)
        item = self._item(m, body="junk member id")
        resp = client.post(f"/inbox/{item.id}/triage/",
                           {"target": "note", "member": "abc"})
        assert resp.status_code == 200
        assert RunningNote.objects.for_manager(m.id).get().team_member_id is None

    def test_decision_from_empty_body_does_not_crash(self, client):
        """The Decision title falls back to 'Decision' when body has no
        non-blank line (quick-add blocks this, but a seeded/email item
        could hit it — must not IndexError into a 500)."""
        from core.models import Decision
        m = self._manager("emptybody")
        self._login_as(client, m.email)
        item = self._item(m, body="   \n  \n")  # whitespace-only
        resp = client.post(f"/inbox/{item.id}/triage/", {"target": "decision"})
        assert resp.status_code == 200
        assert Decision.objects.for_manager(m.id).get().title == "Decision"

    def test_badge_counts_pending_and_failed_not_dismissed(self, client):
        m = self._manager("badge")
        self._login_as(client, m.email)
        self._item(m)                                   # pending
        self._item(m, status="failed", source="email")  # failed counts too
        self._item(m, status="dismissed")               # excluded
        self._item(m, status="triaged")                 # excluded
        body = client.get("/inbox/badge/").content.decode()
        assert ">2<" in body, "badge must count pending + failed, not the rest"

    def test_inbox_page_lists_pending_and_failed(self, client):
        m = self._manager("page")
        self._login_as(client, m.email)
        self._item(m, body="pending one")
        self._item(m, body="broken email", status="failed",
                   source="email", from_address="x@y.z")
        body = client.get("/inbox/").content.decode()
        assert "pending one" in body
        assert "broken email" in body and "failed email" in body

    def test_page_orders_newest_first(self, client):
        m = self._manager("order")
        self._login_as(client, m.email)
        self._item(m, body="older capture")
        self._item(m, body="newer capture")
        body = client.get("/inbox/").content.decode()
        assert body.index("newer capture") < body.index("older capture"), \
            "newest item must render first (-created_at, -id)"

    def test_page_escapes_subject_and_body(self, client):
        """Parity with TestPageRenderEscapesUserContent — untrusted
        capture text must be auto-escaped, not rendered as live markup."""
        m = self._manager("xss")
        self._login_as(client, m.email)
        self._item(m, subject="<script>alert('s')</script>",
                   body="<img src=x onerror=alert('b')>")
        body = client.get("/inbox/").content.decode()
        assert "<script>alert('s')</script>" not in body
        assert "&lt;script&gt;" in body
        assert "<img src=x onerror" not in body

    def test_all_four_routes_403_without_manager(self, client):
        self._login_as(client, "stranger_inbox@example.com")
        assert client.get("/inbox/").status_code == 403
        assert client.get("/inbox/badge/").status_code == 403
        assert client.post("/inbox/quick/", {"body": "x"}).status_code == 403
        assert client.post("/inbox/1/triage/", {"target": "journal"}).status_code == 403

    def test_page_renders_density_affordances(self, client):
        """The design-pass fixes reach rendered output: 44px tap-floor
        on triage buttons and the Show more/less body disclosure."""
        m = self._manager("dens")
        self._login_as(client, m.email)
        self._item(m, body="x " * 400)  # long body -> clamp + expand
        body = client.get("/inbox/").content.decode()
        assert "min-h-11" in body, "triage buttons missing 44px tap floor"
        assert "line-clamp-4" in body and "Show more" in body
        assert 'aria-label="Team member for note"' in body

    def test_quick_add_response_oob_refreshes_badge(self, client):
        """The quick-add toast carries an hx-swap-oob badge so the sidebar
        count reflects the just-captured item without a full page reload."""
        m = self._manager("qaoob")
        self._login_as(client, m.email)
        body = client.post(
            "/inbox/quick/", {"body": "fresh thought"},
        ).content.decode()
        assert 'hx-swap-oob="true"' in body
        assert 'id="inbox-badge"' in body
        assert ">1<" in body, "OOB badge must show the new pending count"

    def test_empty_quick_add_does_not_oob_refresh(self, client):
        """Nothing captured -> nothing changed -> no stray badge swap."""
        m = self._manager("noref")
        self._login_as(client, m.email)
        body = client.post("/inbox/quick/", {"body": "   "}).content.decode()
        assert "hx-swap-oob" not in body

    def test_triage_response_oob_refreshes_badge(self, client):
        """Filing an item drops the pending count; the rows response OOB
        refreshes the badge to the remaining count."""
        m = self._manager("troob")
        self._login_as(client, m.email)
        self._item(m, body="keep me pending")
        victim = self._item(m, body="file me")
        body = client.post(
            f"/inbox/{victim.id}/triage/", {"target": "journal"},
        ).content.decode()
        assert 'hx-swap-oob="true"' in body
        assert ">1<" in body, "OOB badge must show remaining count after triage"

    def test_dismiss_response_oob_refreshes_badge(self, client):
        """Dismiss also changes the count, so it OOB-refreshes too."""
        m = self._manager("disoob")
        self._login_as(client, m.email)
        only = self._item(m, body="dismiss me")
        body = client.post(
            f"/inbox/{only.id}/triage/", {"target": "dismiss"},
        ).content.decode()
        assert 'hx-swap-oob="true"' in body
        # Count is now zero -> the hidden-span variant, not a visible pill.
        assert 'id="inbox-badge" hx-swap-oob="true" class="hidden"' in body

    def test_full_page_has_exactly_one_inbox_badge(self, client):
        """The rows partial's OOB badge is gated OFF on the full page, so
        the sidebar's id="inbox-badge" is never duplicated (a duplicate id
        is invalid and would break the badge's swap target)."""
        m = self._manager("dupid")
        self._login_as(client, m.email)
        self._item(m, body="one")
        body = client.get("/inbox/").content.decode()
        assert body.count('id="inbox-badge"') == 1
        assert "hx-swap-oob" not in body, "full page must carry no OOB badge"


@pytest.mark.django_db
class TestInboxEmailPoll:
    """Roadmap PR 5: poll_inbox_email cron. All IMAP traffic is mocked
    (imaplib.IMAP4_SSL patched); the safety properties under test are
    the sender allowlist, run-twice dedupe on Message-ID, HTML-only
    body stripping, poison-message isolation (a malformed email lands
    as a VISIBLE failed item and never blocks the queue), the dry-run
    no-op, and the last-poll outcome stamp."""

    class _FakeIMAP:
        """Stands in for imaplib.IMAP4_SSL. `mailbox` is a list of raw
        message bytes; `seen` is a shared index-set so \\Seen state
        survives across command runs within one test (like a real
        mailbox would)."""

        mailbox = []
        seen = set()

        def __init__(self, host, port):
            self.host, self.port = host, port

        def login(self, user, password):
            return ("OK", [b"Logged in"])

        def select(self, box):
            return ("OK", [b"1"])

        def search(self, charset, criterion):
            assert criterion == "UNSEEN"
            unseen = [
                str(i + 1).encode()
                for i in range(len(self.mailbox))
                if i not in self.seen
            ]
            return ("OK", [b" ".join(unseen)])

        def fetch(self, num, spec):
            # BODY.PEEK[] must NOT implicitly mark seen — mirror that.
            assert "PEEK" in spec, "poller must fetch with BODY.PEEK[]"
            raw = self.mailbox[int(num) - 1]
            return ("OK", [(b"1 (BODY[] {%d}" % len(raw), raw), b")"])

        def store(self, num, flags, value):
            assert flags == "+FLAGS" and value == "\\Seen"
            self.seen.add(int(num) - 1)
            return ("OK", [b""])

        def logout(self):
            return ("BYE", [b""])

    # ------------------------------------------------------------------
    def _manager(self, slug):
        return Manager.objects.create(
            username=f"poll_{slug}", display_name=slug.title(),
            password_hash="h", email=f"poll_{slug}@example.com",
        )

    def _configure(self, m, allowed=None):
        from core.services.config import set_config
        set_config("inbox_imap_user", m.id, "capture@gmail.com")
        set_config("inbox_imap_password", m.id, "gmail-app-pass")
        if allowed is not None:
            set_config("inbox_allowed_senders", m.id, allowed)
        else:
            set_config("manager_email", m.id, "todd@example.com")

    def _raw(self, from_addr="todd@example.com", subject="Test subject",
             body="hello from email", message_id="<m1@example.com>",
             charset="utf-8", html=None):
        lines = [
            f"From: Todd <{from_addr}>",
            f"Subject: {subject}",
            "Date: Mon, 06 Jul 2026 09:00:00 -0400",
        ]
        if message_id:
            lines.append(f"Message-ID: {message_id}")
        if html is not None:
            lines.append(f'Content-Type: text/html; charset="{charset}"')
            payload = html
        else:
            lines.append(f'Content-Type: text/plain; charset="{charset}"')
            payload = body
        return ("\r\n".join(lines) + "\r\n\r\n" + payload).encode()

    def _run(self, mailbox, *, seen=None, dry_run=False):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        fake = self._FakeIMAP
        fake.mailbox = mailbox
        fake.seen = seen if seen is not None else set()
        out = StringIO()
        with patch("imaplib.IMAP4_SSL", fake):
            kwargs = {"stdout": out}
            if dry_run:
                kwargs["dry_run"] = True
            call_command("poll_inbox_email", **kwargs)
        return out.getvalue(), fake.seen

    # ------------------------------------------------------------------
    def test_allowed_sender_creates_pending_item(self):
        from core.models import InboxItem
        m = self._manager("ok")
        self._configure(m)
        _out, seen = self._run([self._raw()])
        item = InboxItem.objects.for_manager(m.id).get()
        assert item.source == "email" and item.status == "pending"
        assert item.subject == "Test subject"
        assert item.body == "hello from email"
        assert item.from_address == "todd@example.com"
        assert item.message_id == "<m1@example.com>"
        assert item.received_at is not None
        assert seen == {0}, "message must be marked seen after commit"

    def test_disallowed_sender_rejected_no_item(self):
        from core.models import InboxItem
        m = self._manager("spam")
        self._configure(m)
        _out, seen = self._run([self._raw(from_addr="attacker@evil.com")])
        assert InboxItem.objects.for_manager(m.id).count() == 0
        assert seen == {0}, "rejected mail is still marked seen (dropped)"

    def test_allowlist_config_overrides_manager_email(self):
        from core.models import InboxItem
        m = self._manager("allow")
        self._configure(m, allowed="second@work.com")
        # manager_email is NOT in the explicit allowlist -> rejected.
        from core.services.config import set_config
        set_config("manager_email", m.id, "todd@example.com")
        self._run([
            self._raw(from_addr="todd@example.com",
                      message_id="<rej@example.com>"),
            self._raw(from_addr="second@work.com",
                      message_id="<acc@example.com>"),
        ])
        items = InboxItem.objects.for_manager(m.id)
        assert items.count() == 1
        assert items.get().from_address == "second@work.com"

    def test_run_twice_dedupes_on_message_id(self):
        from core.models import InboxItem
        m = self._manager("dupe")
        self._configure(m)
        mailbox = [self._raw()]
        _out, seen = self._run(mailbox)
        # Simulate \Seen flag loss (or an overlapping run): clear the
        # flag so the same message is re-presented, and poll again.
        _out2, _seen2 = self._run(mailbox, seen=set())
        assert InboxItem.objects.for_manager(m.id).count() == 1
        assert "1 duplicate" in _out2

    def test_html_only_body_is_stripped(self):
        from core.models import InboxItem
        m = self._manager("html")
        self._configure(m)
        html = ("<html><head><style>p{color:red}</style></head><body>"
                "<p>Line one &amp; more</p><script>alert(1)</script>"
                "<div>Line two</div></body></html>")
        self._run([self._raw(html=html)])
        body = InboxItem.objects.for_manager(m.id).get().body
        assert "Line one & more" in body, "entities must be unescaped"
        assert "Line two" in body
        assert "<" not in body, "no tags may survive"
        assert "alert(1)" not in body, "script contents must be dropped"
        assert "color:red" not in body, "style contents must be dropped"

    def test_poison_message_lands_failed_and_queue_continues(self):
        from core.models import InboxItem
        m = self._manager("poison")
        self._configure(m)
        # Unknown charset -> LookupError inside body extraction: a
        # genuine parse failure, not a mocked one.
        poison = self._raw(charset="totally-bogus-charset",
                           message_id="<bad@example.com>")
        good = self._raw(subject="After the poison",
                         message_id="<good@example.com>")
        _out, seen = self._run([poison, good])
        items = InboxItem.objects.for_manager(m.id)
        failed = items.get(status="failed")
        assert failed.subject == "(unparseable email)"
        assert failed.body, "failed item must carry a raw excerpt"
        ok = items.get(status="pending")
        assert ok.subject == "After the poison"
        assert seen == {0, 1}, "both messages consumed; queue not blocked"

    def test_dry_run_writes_nothing(self):
        from core.models import Config, InboxItem
        m = self._manager("dry")
        self._configure(m)
        out, seen = self._run([self._raw()], dry_run=True)
        assert "DRY-RUN" in out and "1 unseen" in out
        assert InboxItem.objects.for_manager(m.id).count() == 0
        assert seen == set(), "dry-run must not mark anything seen"
        assert not Config.objects.filter(
            manager_id=m.id, key="inbox_last_poll",
        ).exists(), "dry-run must not stamp the last-poll outcome"

    def test_last_poll_outcome_stamped(self):
        from core.models import Config
        m = self._manager("stamp")
        self._configure(m)
        self._run([self._raw()])
        stamp = Config.objects.get(manager_id=m.id, key="inbox_last_poll")
        assert "ok: 1 new" in stamp.value

    def test_unconfigured_manager_is_skipped(self):
        from core.models import InboxItem
        m = self._manager("skip")  # no inbox_imap_user config at all
        _out, _seen = self._run([self._raw()])
        assert InboxItem.objects.for_manager(m.id).count() == 0

    # ---- review-round regression tests (PR #135 code review) ----

    def test_unclosed_script_tag_does_not_swallow_body_tail(self):
        """HTMLParser's CDATA mode treats everything after an unclosed
        <script> as script content; the first-pass stripper would drop
        the entire tail SILENTLY (status stays pending — invisible
        loss). The keep-all fallback must preserve the tail."""
        from core.models import InboxItem
        m = self._manager("cdata")
        self._configure(m)
        html = ("<div>Intro text</div><script>var x=1;"
                "<div>Tail that must NOT be lost</div>")
        self._run([self._raw(html=html)])
        body = InboxItem.objects.for_manager(m.id).get().body
        assert "Intro text" in body
        assert "Tail that must NOT be lost" in body

    def test_comma_display_name_sender_still_matches_allowlist(self):
        """parseaddr returns ('','') for an unquoted comma display name
        ('Erickson, Todd <t@x>'), which would silently DROP legit mail.
        _sender_of must recover the real address via getaddresses."""
        from core.models import InboxItem
        m = self._manager("comma")
        self._configure(m)
        raw = self._raw()
        raw = raw.replace(
            b"From: Todd <todd@example.com>",
            b"From: Erickson, Todd <todd@example.com>",
        )
        self._run([raw])
        item = InboxItem.objects.for_manager(m.id).get()
        assert item.from_address == "todd@example.com"
        assert item.status == "pending"

    def test_poison_refetch_dedupes_on_message_id(self):
        """A poison message keeps its Message-ID, so a re-fetch after
        flag loss dedupes into ONE failed row instead of duplicating."""
        from core.models import InboxItem
        m = self._manager("repoison")
        self._configure(m)
        poison = self._raw(charset="totally-bogus-charset",
                           message_id="<poison@example.com>")
        self._run([poison])
        self._run([poison], seen=set())  # simulate \Seen flag loss
        assert InboxItem.objects.for_manager(m.id).filter(
            status="failed",
        ).count() == 1

    def test_enabled_but_passwordless_manager_stamps_error(self):
        """inbox_imap_user set but password missing (or undecryptable
        after a CONFIG_ENCRYPTION_KEY rotation): the Settings last-poll
        stamp must show the error, not freeze on a stale healthy value."""
        from core.models import Config, InboxItem
        from core.services.config import set_config
        m = self._manager("nopass")
        set_config("inbox_imap_user", m.id, "capture@gmail.com")
        # no password on purpose
        self._run([self._raw()])
        stamp = Config.objects.get(manager_id=m.id, key="inbox_last_poll")
        assert stamp.value and "error:" in stamp.value
        assert InboxItem.objects.for_manager(m.id).count() == 0

    def test_settings_blank_imap_password_keeps_existing(self, client):
        """The keep-if-blank secret pattern must cover the new field: a
        settings save with a blank password preserves the stored one."""
        from django.contrib.auth import get_user_model

        from core.services.config import get_config
        m = self._manager("keep")
        u = get_user_model().objects.create_user(
            username=m.email, email=m.email, password="testpw",
        )
        client.force_login(u)
        base = {"display_name": "Todd", "timezone": ""}
        r1 = client.post("/settings/", {
            **base, "inbox_imap_user": "capture@gmail.com",
            "inbox_imap_password": "first-secret",
        })
        assert r1.status_code == 302
        assert get_config("inbox_imap_password", m.id) == "first-secret"
        r2 = client.post("/settings/", {
            **base, "inbox_imap_user": "capture@gmail.com",
            "inbox_imap_password": "",
        })
        assert r2.status_code == 302
        assert get_config("inbox_imap_password", m.id) == "first-secret", \
            "blank submission must keep the existing password"


class TestPwaManifest:
    """Roadmap PR 7: installable PWA — manifest + icons + head links.
    No service worker in v1 (deliberate). The PNG checks parse magic
    bytes + IHDR with the stdlib so no image library is needed."""

    @staticmethod
    def _static(rel):
        from django.conf import settings
        return settings.BASE_DIR / "static" / rel

    def test_manifest_is_valid_and_standalone(self):
        import json
        manifest = json.loads(
            self._static("manifest.webmanifest").read_text(encoding="utf-8")
        )
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == "/"
        assert manifest["name"] == "Manager Tool"
        sizes = {i["sizes"] for i in manifest["icons"]}
        assert sizes == {"192x192", "512x512"}
        for icon in manifest["icons"]:
            assert icon["type"] == "image/png"
            assert "maskable" in icon["purpose"]

    def test_icons_exist_and_dimensions_match(self):
        import struct
        expected = {
            "icons/icon-192.png": 192,
            "icons/icon-512.png": 512,
            "icons/apple-touch-icon.png": 180,
        }
        for rel, size in expected.items():
            data = self._static(rel).read_bytes()
            assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{rel}: not a PNG"
            # IHDR is always the first chunk: length(4) type(4) W(4) H(4)
            w, h = struct.unpack(">II", data[16:24])
            assert (w, h) == (size, size), f"{rel}: {w}x{h} != {size}"

    def test_manifest_icon_srcs_point_at_real_files(self):
        import json
        manifest = json.loads(
            self._static("manifest.webmanifest").read_text(encoding="utf-8")
        )
        for icon in manifest["icons"]:
            rel = icon["src"].removeprefix("/static/")
            assert self._static(rel).exists(), f"{icon['src']} missing"

    @pytest.mark.django_db
    def test_app_shell_head_carries_install_surface(self, client):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username="pwa_mgr", display_name="P", password_hash="h",
            email="pwa_mgr@example.com",
        )
        u = get_user_model().objects.create_user(
            username=m.email, email=m.email, password="testpw",
        )
        client.force_login(u)
        body = client.get("/dashboard/").content.decode()
        assert 'rel="manifest"' in body
        # Light chrome — the sidebar/page are white (To Do overhaul PR)
        assert 'name="theme-color" content="#ffffff"' in body
        assert 'rel="apple-touch-icon"' in body

    @pytest.mark.django_db
    def test_landing_head_carries_install_surface(self, client):
        body = client.get("/").content.decode()
        assert 'rel="manifest"' in body
        assert 'rel="apple-touch-icon"' in body


@pytest.mark.django_db
class TestPrepBrief:
    """Roadmap PR 8: pre-1:1 AI prep brief. The poll's state machine is
    the safety property under test — a dead generation thread must
    surface as an explicit failed/retry state after 60s, never an
    eternal "Generating…" spinner. COACHING_ENABLED=False in tests, so
    the generate endpoint never spawns the real thread."""

    def _setup(self, client, slug):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username=f"prep_{slug}", display_name=slug.title(),
            password_hash="h", email=f"prep_{slug}@example.com",
        )
        u = get_user_model().objects.create_user(
            username=m.email, email=m.email, password="testpw",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(name="Sarah", manager_id=m.id)
        s = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-07-06",
            status="draft",
        )
        return m, tm, s

    def test_idle_state_shows_generate_button(self, client):
        _m, _tm, s = self._setup(client, "idle")
        body = client.get(f"/meetings/{s.id}/prep-brief/").content.decode()
        assert "Generate AI prep brief" in body
        assert "every 2s" not in body, "idle must not poll"

    def test_generate_sets_requested_at_and_returns_pending(self, client):
        m, _tm, s = self._setup(client, "gen")
        body = client.post(
            f"/meetings/{s.id}/prep-brief/generate/",
        ).content.decode()
        assert "Generating prep brief" in body
        assert "every 2s" in body, "pending must poll"
        s.refresh_from_db()
        assert s.prep_brief_requested_at is not None
        assert s.prep_brief is None

    def test_poll_within_timeout_stays_pending(self, client):
        from django.utils import timezone
        _m, _tm, s = self._setup(client, "pend")
        s.prep_brief_requested_at = timezone.now()
        s.save(update_fields=["prep_brief_requested_at"])
        body = client.get(f"/meetings/{s.id}/prep-brief/").content.decode()
        assert "Generating prep brief" in body and "every 2s" in body

    def test_poll_after_timeout_is_terminal_failed_state(self, client):
        """THE review-mandated property: a dead thread (worker restart
        mid-generation) flips to failed/retry — polling STOPS."""
        from datetime import timedelta

        from django.utils import timezone
        _m, _tm, s = self._setup(client, "dead")
        s.prep_brief_requested_at = timezone.now() - timedelta(seconds=61)
        s.save(update_fields=["prep_brief_requested_at"])
        body = client.get(f"/meetings/{s.id}/prep-brief/").content.decode()
        assert "failed or timed out" in body
        assert "Retry" in body
        assert "every 2s" not in body, "failed state must NOT keep polling"

    def test_poll_with_brief_renders_ready_and_stops(self, client):
        from django.utils import timezone
        _m, _tm, s = self._setup(client, "ready")
        s.prep_brief = "**Since last time**\n- shipped the migration"
        s.prep_brief_requested_at = timezone.now()
        s.save(update_fields=["prep_brief", "prep_brief_requested_at"])
        body = client.get(f"/meetings/{s.id}/prep-brief/").content.decode()
        assert "shipped the migration" in body
        assert "Regenerate" in body
        assert "every 2s" not in body, "ready state must NOT keep polling"

    def test_regenerate_clears_stale_brief(self, client):
        _m, _tm, s = self._setup(client, "regen")
        s.prep_brief = "old brief"
        s.save(update_fields=["prep_brief"])
        client.post(f"/meetings/{s.id}/prep-brief/generate/")
        s.refresh_from_db()
        assert s.prep_brief is None, "regenerate must clear the old brief"

    def test_cross_tenant_session_404s(self, client):
        from django.contrib.auth import get_user_model
        _m, _tm, s = self._setup(client, "victim")
        other = Manager.objects.create(
            username="prep_intruder", display_name="I",
            password_hash="h", email="prep_intruder@example.com",
        )
        u2 = get_user_model().objects.create_user(
            username=other.email, email=other.email, password="testpw",
        )
        client.force_login(u2)
        assert client.get(f"/meetings/{s.id}/prep-brief/").status_code == 404
        assert client.post(
            f"/meetings/{s.id}/prep-brief/generate/",
        ).status_code == 404

    def test_detail_page_includes_brief_section(self, client):
        _m, _tm, s = self._setup(client, "detail")
        body = client.get(f"/meetings/{s.id}/").content.decode()
        assert 'id="prep-brief"' in body
        assert "Generate AI prep brief" in body

    # ---- review-round regression tests (PR #139 code review) ----

    def test_autosave_does_not_clobber_fresh_brief(self, client):
        """The review's data-loss race: autosave fetched the row before
        the background thread wrote the brief, then a full-row save()
        wrote the stale None back. update_fields on autosave makes the
        clobber impossible even with a stale instance."""
        _m, _tm, s = self._setup(client, "clobber")
        # Simulate the interleaving: the autosave request would have
        # fetched `s` already; the thread then commits a brief.
        OneOnOneSession.objects.filter(pk=s.id).update(
            prep_brief="fresh brief from thread",
        )
        resp = client.post(f"/meetings/{s.id}/autosave/", {
            "direct_notes": "their agenda item",
            "manager_notes": "", "followup_notes": "",
        })
        assert resp.status_code == 200
        s.refresh_from_db()
        assert s.direct_notes == "their agenda item"
        assert s.prep_brief == "fresh brief from thread", \
            "autosave must never write back a stale prep_brief"

    def test_complete_does_not_clobber_fresh_brief(self, client):
        _m, _tm, s = self._setup(client, "compclob")
        OneOnOneSession.objects.filter(pk=s.id).update(
            prep_brief="fresh brief from thread",
        )
        client.post(f"/meetings/{s.id}/complete/")
        s.refresh_from_db()
        assert s.status == "completed"
        assert s.prep_brief == "fresh brief from thread"

    def test_stale_thread_write_is_discarded_by_stamp_guard(self, client):
        """Pin the CAS contract: a thread carrying an OLD requested_at
        stamp must not overwrite the row after a re-stamp (Regenerate).
        Exercises the exact guarded queryset the thread runs, including
        the datetime-equality round-trip on the DB."""
        from datetime import timedelta

        from django.utils import timezone
        _m, _tm, s = self._setup(client, "stale")
        old_stamp = timezone.now() - timedelta(seconds=30)
        new_stamp = timezone.now()
        OneOnOneSession.objects.filter(pk=s.id).update(
            prep_brief_requested_at=new_stamp,
        )
        updated = OneOnOneSession.objects.filter(
            pk=s.id, prep_brief_requested_at=old_stamp,
        ).update(prep_brief="STALE RESULT")
        assert updated == 0
        s.refresh_from_db()
        assert s.prep_brief is None
        # ...and the CURRENT stamp's write lands.
        updated = OneOnOneSession.objects.filter(
            pk=s.id, prep_brief_requested_at=new_stamp,
        ).update(prep_brief="current result")
        assert updated == 1


@pytest.mark.django_db
class TestDraftSBI:
    """Roadmap PR 9: SBI drafting assist. The endpoint turns rough notes
    into editable S/B/I form initials — the AI must NEVER write to the
    DB, and every degraded path (no key, API error, unparseable output)
    must land the notes in Behavior with a visible caveat."""

    def _setup(self, client, slug):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username=f"sbi_{slug}", display_name=slug.title(),
            password_hash="h", email=f"sbi_{slug}@example.com",
        )
        u = get_user_model().objects.create_user(
            username=m.email, email=m.email, password="testpw",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(name="Sarah", manager_id=m.id)
        return m, tm

    def _mock_ai(self, mocker, text):
        fake = mocker.Mock()
        fake.messages.create.return_value = mocker.Mock(
            content=[mocker.Mock(text=text)],
        )
        mocker.patch("coaching.services._get_client", return_value=fake)
        return fake

    def test_draft_populates_three_fields(self, client, mocker):
        _m, tm = self._setup(client, "happy")
        fake = self._mock_ai(
            mocker,
            "SITUATION: At Monday standup.\n"
            "BEHAVIOR: Interrupted the designer twice.\n"
            "IMPACT: The team lost the thread.",
        )
        resp = client.post("/feedback/draft-sbi/", {
            "notes": "sarah kept interrupting in standup",
            "team_member": tm.id, "feedback_type": "constructive",
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "At Monday standup." in body
        assert "Interrupted the designer twice." in body
        assert "The team lost the thread." in body
        # member + type picks survive the swap
        assert f'value="{tm.id}" selected' in body
        assert 'value="constructive" selected' in body
        # notes went through the injection-wrapping path with the name
        prompt = fake.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "<user_input>" in prompt and "Sarah" in prompt

    def test_empty_notes_is_422(self, client):
        self._setup(client, "empty")
        resp = client.post("/feedback/draft-sbi/", {"notes": "   "})
        assert resp.status_code == 422
        assert "rough notes" in resp.content.decode()

    def test_no_api_key_falls_back_to_behavior(self, client, mocker):
        self._setup(client, "nokey")
        mocker.patch("coaching.services._get_client", return_value=None)
        resp = client.post("/feedback/draft-sbi/", {
            "notes": "raw unstructured note",
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "raw unstructured note" in body
        assert "no API key" in body

    def test_unparseable_output_dumps_to_behavior(self, client, mocker):
        self._setup(client, "prose")
        self._mock_ai(mocker, "Some feedback prose with no labels at all.")
        body = client.post("/feedback/draft-sbi/", {
            "notes": "whatever",
        }).content.decode()
        assert "Some feedback prose with no labels at all." in body
        assert "placed in Behavior" in body

    def test_api_error_degrades_with_visible_note(self, client, mocker):
        self._setup(client, "apierr")
        fake = mocker.Mock()
        fake.messages.create.side_effect = RuntimeError("boom")
        mocker.patch("coaching.services._get_client", return_value=fake)
        resp = client.post("/feedback/draft-sbi/", {
            "notes": "note that must survive",
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "note that must survive" in body
        assert "API error" in body

    def test_draft_never_writes_feedback_row(self, client, mocker):
        _m, tm = self._setup(client, "nodb")
        self._mock_ai(mocker, "SITUATION: x\nBEHAVIOR: y\nIMPACT: z")
        client.post("/feedback/draft-sbi/", {
            "notes": "n", "team_member": tm.id,
        })
        assert Feedback.objects.filter(manager_id=_m.id).count() == 0

    def test_cross_tenant_member_id_is_dropped(self, client, mocker):
        self._setup(client, "victim2")
        intruder_m = Manager.objects.create(
            username="sbi_intruder", display_name="I",
            password_hash="h", email="sbi_intruder@example.com",
        )
        other_tm = TeamMember.objects.create(
            name="OtherTenantMember", manager_id=intruder_m.id,
        )
        fake = self._mock_ai(mocker, "SITUATION: x\nBEHAVIOR: y\nIMPACT: z")
        body = client.post("/feedback/draft-sbi/", {
            "notes": "n", "team_member": other_tm.id,
        }).content.decode()
        assert "OtherTenantMember" not in body
        prompt = fake.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "OtherTenantMember" not in prompt

    def test_feedback_page_has_assist_block(self, client):
        self._setup(client, "page")
        body = client.get("/feedback/").content.decode()
        assert "Draft S/B/I" in body
        assert 'id="sbi-notes"' in body
        assert 'id="feedback-form-wrap"' in body

    # ---- parser unit tests ----

    def test_parse_bold_and_case_insensitive_headers(self):
        from coaching.services import parse_sbi_sections
        parsed, ok = parse_sbi_sections(
            "**Situation:** at the review\nbehavior: spoke over Jim\nIMPACT: morale dip",
        )
        assert ok
        assert parsed["situation"] == "at the review"
        assert parsed["behavior"] == "spoke over Jim"
        assert parsed["impact"] == "morale dip"

    def test_parse_multiline_sections(self):
        from coaching.services import parse_sbi_sections
        parsed, ok = parse_sbi_sections(
            "SITUATION: line one\nline two\nBEHAVIOR: b\nIMPACT: i",
        )
        assert ok
        assert parsed["situation"] == "line one\nline two"

    def test_parse_no_headers_falls_to_behavior(self):
        from coaching.services import parse_sbi_sections
        parsed, ok = parse_sbi_sections("just prose")
        assert not ok
        assert parsed["behavior"] == "just prose"
        assert parsed["situation"] == "" and parsed["impact"] == ""


@pytest.mark.django_db
class TestQuarterlyReview:
    """Roadmap PR 9: quarterly review draft grounded in the member's
    recorded quarter. Sparse member must yield an explicit
    'not enough data' message; generation never writes to the DB;
    saving is the explicit convos_add round-trip."""

    QUARTER = "Q3 2026"

    def _setup(self, client, slug):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username=f"qr_{slug}", display_name=slug.title(),
            password_hash="h", email=f"qr_{slug}@example.com",
        )
        u = get_user_model().objects.create_user(
            username=m.email, email=m.email, password="testpw",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(name="Devon", manager_id=m.id)
        return m, tm

    def _seed_quarter(self, m, tm):
        from datetime import datetime

        from django.utils import timezone as djtz
        in_q = djtz.make_aware(datetime(2026, 7, 5, 12, 0))
        Goal.objects.create(
            team_member=tm, manager_id=m.id, quarter=self.QUARTER,
            description="Ship the pricing migration", status="active",
        )
        Feedback.objects.create(
            team_member=tm, manager_id=m.id, feedback_type="positive",
            behavior="Unblocked the data team", impact="Saved the sprint",
            created_at=in_q,
        )
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-07-02",
            status="completed", manager_notes="Discussed scope creep",
        )
        CareerConversation.objects.create(
            team_member=tm, manager_id=m.id, conversation_date="2026-07-03",
            topic="Tech lead track", created_at=in_q,
        )
        Delegation.objects.create(
            team_member=tm, manager_id=m.id, task="Own the vendor eval",
            status="active", created_at=in_q,
        )

    def _mock_ai(self, mocker, text):
        fake = mocker.Mock()
        fake.messages.create.return_value = mocker.Mock(
            content=[mocker.Mock(text=text)],
        )
        mocker.patch("coaching.services._get_client", return_value=fake)
        return fake

    # ---- quarter_bounds unit tests ----

    def test_quarter_bounds_all_quarters(self):
        from coaching.services import quarter_bounds
        assert quarter_bounds("Q1 2026") == ("2026-01-01", "2026-03-31")
        assert quarter_bounds("Q2 2026") == ("2026-04-01", "2026-06-30")
        assert quarter_bounds("Q3 2026") == ("2026-07-01", "2026-09-30")
        assert quarter_bounds("Q4 2026") == ("2026-10-01", "2026-12-31")

    def test_quarter_bounds_rejects_garbage(self):
        from coaching.services import quarter_bounds
        for bad in ("", "Q5 2026", "2026-Q3", "third quarter", None):
            assert quarter_bounds(bad) is None

    # ---- gather grounding ----

    def test_gather_includes_only_quarter_data(self, client):
        from datetime import datetime

        from django.utils import timezone as djtz

        from coaching.services import _gather_quarter_data
        m, tm = self._setup(client, "gather")
        self._seed_quarter(m, tm)
        # Out-of-quarter noise: wrong goal quarter, Q2 feedback,
        # Q2 session, Q4 convo.
        Goal.objects.create(
            team_member=tm, manager_id=m.id, quarter="Q2 2026",
            description="Old quarter goal", status="met",
        )
        Feedback.objects.create(
            team_member=tm, manager_id=m.id, feedback_type="constructive",
            behavior="Stale feedback",
            created_at=djtz.make_aware(datetime(2026, 6, 30, 23, 0)),
        )
        OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-06-30",
            status="completed", manager_notes="Last quarter session",
        )
        CareerConversation.objects.create(
            team_member=tm, manager_id=m.id,
            conversation_date="2026-10-01", topic="Next quarter convo",
        )
        data = _gather_quarter_data(tm, m.id, self.QUARTER)
        flat = "\n".join(
            data["goals"] + data["feedback"] + data["sessions"]
            + data["convos"] + data["delegations"],
        )
        assert "Ship the pricing migration" in flat
        assert "Unblocked the data team" in flat
        assert "Discussed scope creep" in flat
        assert "Tech lead track" in flat
        assert "Own the vendor eval" in flat
        assert "Old quarter goal" not in flat
        assert "Stale feedback" not in flat
        assert "Last quarter session" not in flat
        assert "Next quarter convo" not in flat

    def test_gather_excludes_other_tenants_rows(self, client):
        """Even a corrupted row pointing another manager's feedback at
        MY member must not leak into my gather (for_manager guard)."""
        from datetime import datetime

        from django.utils import timezone as djtz

        from coaching.services import _gather_quarter_data
        m, tm = self._setup(client, "iso")
        other = Manager.objects.create(
            username="qr_other", display_name="O",
            password_hash="h", email="qr_other@example.com",
        )
        Feedback.objects.create(
            team_member=tm, manager_id=other.id, feedback_type="positive",
            behavior="LEAKED CROSS TENANT",
            created_at=djtz.make_aware(datetime(2026, 7, 5, 12, 0)),
        )
        data = _gather_quarter_data(tm, m.id, self.QUARTER)
        assert not any("LEAKED" in f for f in data["feedback"])

    # ---- endpoint behavior ----

    def test_endpoint_renders_draft_and_save_form(self, client, mocker):
        m, tm = self._setup(client, "happy")
        self._seed_quarter(m, tm)
        fake = self._mock_ai(mocker, "**Summary**\nSolid quarter for Devon.")
        resp = client.post("/career/quarterly-review/", {
            "team_member": tm.id, "quarter": self.QUARTER,
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Solid quarter for Devon." in body
        assert "Save as career conversation" in body
        assert f'value="{self.QUARTER} review draft"' in body
        # grounded prompt: quarter metadata outside tags, data inside
        prompt = fake.messages.create.call_args.kwargs["messages"][0]["content"]
        assert f"QUARTER: {self.QUARTER}" in prompt
        assert "<user_input>" in prompt
        assert "Ship the pricing migration" in prompt

    def test_sparse_member_gets_explicit_message(self, client):
        _m, tm = self._setup(client, "sparse")
        resp = client.post("/career/quarterly-review/", {
            "team_member": tm.id, "quarter": self.QUARTER,
        })
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Not enough data" in body
        assert "Save as career conversation" not in body

    def test_no_api_key_fallback_lists_raw_record(self, client, mocker):
        m, tm = self._setup(client, "nokey")
        self._seed_quarter(m, tm)
        mocker.patch("coaching.services._get_client", return_value=None)
        body = client.post("/career/quarterly-review/", {
            "team_member": tm.id, "quarter": self.QUARTER,
        }).content.decode()
        assert "AI draft unavailable" in body
        assert "Ship the pricing migration" in body

    def test_api_error_falls_back_with_note(self, client, mocker):
        m, tm = self._setup(client, "apierr")
        self._seed_quarter(m, tm)
        fake = mocker.Mock()
        fake.messages.create.side_effect = RuntimeError("boom")
        mocker.patch("coaching.services._get_client", return_value=fake)
        body = client.post("/career/quarterly-review/", {
            "team_member": tm.id, "quarter": self.QUARTER,
        }).content.decode()
        assert "API error" in body
        assert "Ship the pricing migration" in body

    def test_invalid_quarter_is_422(self, client):
        _m, tm = self._setup(client, "badq")
        resp = client.post("/career/quarterly-review/", {
            "team_member": tm.id, "quarter": "2026-Q3",
        })
        assert resp.status_code == 422
        assert "Q3 2026" in resp.content.decode()

    def test_missing_member_is_422(self, client):
        self._setup(client, "nomem")
        resp = client.post("/career/quarterly-review/", {
            "quarter": self.QUARTER,
        })
        assert resp.status_code == 422
        assert "team member" in resp.content.decode()

    def test_cross_tenant_member_404s(self, client):
        self._setup(client, "victim")
        other = Manager.objects.create(
            username="qr_intruder", display_name="I",
            password_hash="h", email="qr_intruder@example.com",
        )
        other_tm = TeamMember.objects.create(
            name="NotYours", manager_id=other.id,
        )
        resp = client.post("/career/quarterly-review/", {
            "team_member": other_tm.id, "quarter": self.QUARTER,
        })
        assert resp.status_code == 404

    def test_generate_never_writes_db(self, client, mocker):
        m, tm = self._setup(client, "nodb")
        self._seed_quarter(m, tm)
        self._mock_ai(mocker, "**Summary**\nFine.")
        before = CareerConversation.objects.filter(manager_id=m.id).count()
        client.post("/career/quarterly-review/", {
            "team_member": tm.id, "quarter": self.QUARTER,
        })
        after = CareerConversation.objects.filter(manager_id=m.id).count()
        assert after == before

    def test_save_roundtrip_creates_convo(self, client):
        """The save form's exact field contract against convos_add."""
        m, tm = self._setup(client, "save")
        resp = client.post("/career/convos/add/", {
            "team_member": tm.id, "conversation_date": "2026-07-06",
            "topic": f"{self.QUARTER} review draft",
            "notes": "**Summary**\nSolid quarter.",
        })
        assert resp.status_code == 200
        convo = CareerConversation.objects.for_manager(m.id).get(
            topic=f"{self.QUARTER} review draft",
        )
        assert "Solid quarter." in convo.notes

    def test_career_page_has_review_panel(self, client):
        self._setup(client, "page")
        body = client.get("/career/").content.decode()
        assert "Quarterly Review Draft" in body
        assert 'name="quarter"' in body
        assert 'id="quarterly-review-result"' in body


@pytest.mark.django_db
class TestRruleInvites:
    """Roadmap PR 10: a recurring-series PARENT sends ONE invite with an
    RFC 5545 RRULE (counts sourced from RECURRENCE_COUNTS); children and
    one-off events keep the single-occurrence invite."""

    def _login(self, client, slug):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username=f"rr_{slug}", display_name="M",
            password_hash="x", email=f"rr_{slug}@example.com",
        )
        u = get_user_model().objects.create_user(
            username=m.email, email=m.email, password="x",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(
            name="Direct", manager_id=m.id, email="direct@example.com",
        )
        return m, tm

    def _event(self, m, tm, **kw):
        defaults = dict(
            manager_id=m.id, title="1:1", event_type="one_on_one",
            team_member=tm, scheduled_date="2026-07-06",
            scheduled_time="10:00", status="scheduled",
            calendar_invite_sent=0,
        )
        defaults.update(kw)
        return Event.objects.create(**defaults)

    def _series(self, m, tm, rule="weekly", **kw):
        from datetime import date as _date

        from core.services.events import create_recurring_events
        return create_recurring_events(
            manager_id=m.id, title="1:1", event_type="one_on_one",
            start_date=_date(2026, 7, 6), scheduled_time="10:00",
            rule=rule, team_member=tm, **kw,
        )

    # ---- rrule_for_rule unit tests ----

    def test_rrule_strings_use_actual_count(self):
        from core.services.calendar import rrule_for_rule
        assert rrule_for_rule("weekly", 12) == "FREQ=WEEKLY;COUNT=12"
        assert rrule_for_rule("monthly", 12) == "FREQ=MONTHLY;COUNT=12"
        assert rrule_for_rule("quarterly", 8) == \
            "FREQ=MONTHLY;INTERVAL=3;COUNT=8"
        # COUNT mirrors the actual series size, not the rule's max —
        # an until_date-capped series must not over-invite.
        assert rrule_for_rule("weekly", 3) == "FREQ=WEEKLY;COUNT=3"

    def test_rrule_unknown_rule_or_singleton_is_none(self):
        from core.services.calendar import rrule_for_rule
        assert rrule_for_rule("daily", 12) is None
        assert rrule_for_rule("", 12) is None
        assert rrule_for_rule(None, 12) is None
        # A one-row "series" (orphaned child, or until-capped to the
        # start date) degrades to a single-occurrence invite.
        assert rrule_for_rule("weekly", 1) is None
        assert rrule_for_rule("weekly", 0) is None

    def test_rrule_freq_keys_track_recurrence_counts(self):
        """Drift guard (review finding): a rule added to the
        materializer without an RRULE mapping — or vice versa — must
        fail CI, not silently downgrade series invites."""
        from core.services.calendar import _RRULE_FREQ
        from core.services.events import RECURRENCE_COUNTS
        assert set(_RRULE_FREQ) == set(RECURRENCE_COUNTS)

    # ---- generate_ics ----

    def test_ics_includes_rrule_line_when_given(self):
        from core.services.calendar import generate_ics
        ics = generate_ics({
            "scheduled_date": "2026-07-06", "scheduled_time": "10:00",
            "duration_minutes": 30, "title": "Weekly 1:1",
            "event_type": "one_on_one",
        }, rrule="FREQ=WEEKLY;COUNT=12")
        assert "RRULE:FREQ=WEEKLY;COUNT=12\r\n" in ics
        assert ics.count("BEGIN:VEVENT") == 1, \
            "recurring invite must still be ONE VEVENT"

    def test_ics_has_no_rrule_by_default(self):
        from core.services.calendar import generate_ics
        ics = generate_ics({
            "scheduled_date": "2026-07-06", "scheduled_time": "10:00",
            "duration_minutes": 30, "title": "One-off",
            "event_type": "one_on_one",
        })
        assert "RRULE" not in ics

    # ---- view: series detection ----

    def test_series_parent_sends_rrule_invite(self, client):
        from unittest.mock import patch
        m, tm = self._login(client, "parent")
        parent = self._series(m, tm)  # weekly → 1 parent + 11 children
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(True, "sent")) as mock_send:
            client.post(f"/events/{parent.id}/invite/")
        assert mock_send.call_count == 1
        assert mock_send.call_args.kwargs["rrule"] == "FREQ=WEEKLY;COUNT=12"

    def test_until_capped_series_counts_actual_rows(self, client):
        """Review finding: COUNT must mirror the materialized rows —
        a series capped by until_date must not over-invite."""
        from datetime import date as _date
        from unittest.mock import patch
        m, tm = self._login(client, "capped")
        parent = self._series(m, tm, until_date=_date(2026, 7, 20))
        assert Event.objects.for_manager(m.id).filter(
            parent_event=parent).count() == 2  # 7/13 + 7/20
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(True, "sent")) as mock_send:
            client.post(f"/events/{parent.id}/invite/")
        assert mock_send.call_args.kwargs["rrule"] == "FREQ=WEEKLY;COUNT=3"

    def test_orphaned_child_degrades_to_single_invite(self, client):
        """Review finding: deleting a parent SET_NULLs the children but
        leaves recurrence_rule — an orphaned child must NOT be treated
        as a series parent and re-invite the whole series."""
        from unittest.mock import patch
        m, tm = self._login(client, "orphan")
        parent = self._series(m, tm)
        child = Event.objects.for_manager(m.id).filter(
            parent_event=parent).order_by("scheduled_date").first()
        Event.objects.for_manager(m.id).filter(pk=parent.id).delete()
        child.refresh_from_db()
        assert child.parent_event_id is None, "SET_NULL premise"
        assert child.recurrence_rule == "weekly", "rule survives delete"
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(True, "sent")) as mock_send:
            client.post(f"/events/{child.id}/invite/")
        assert mock_send.call_args.kwargs["rrule"] is None, \
            "orphaned child must send a single-occurrence invite"

    def test_parent_invite_stamps_children_sent(self, client):
        """Review finding: the parent's RRULE invite covers every child
        date — children must show the sent state so their pages don't
        offer a double-booking invite button."""
        from unittest.mock import patch
        m, tm = self._login(client, "stamp")
        parent = self._series(m, tm)
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(True, "sent")):
            client.post(f"/events/{parent.id}/invite/")
        kids = Event.objects.for_manager(m.id).filter(parent_event=parent)
        assert kids.count() == 11
        assert not kids.exclude(calendar_invite_sent=1).exists(), \
            "every child must be stamped calendar_invite_sent"

    def test_failed_parent_invite_stamps_nothing(self, client):
        from unittest.mock import patch
        m, tm = self._login(client, "failstamp")
        parent = self._series(m, tm)
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(False, "SMTP not configured")):
            client.post(f"/events/{parent.id}/invite/")
        parent.refresh_from_db()
        assert parent.calendar_invite_sent in (0, None)
        assert not Event.objects.for_manager(m.id).filter(
            parent_event=parent, calendar_invite_sent=1).exists()

    def test_child_event_sends_single_invite(self, client):
        from unittest.mock import patch
        m, tm = self._login(client, "child")
        parent = self._event(m, tm, recurrence_rule="weekly")
        child = self._event(
            m, tm, recurrence_rule="weekly", parent_event=parent,
            scheduled_date="2026-07-13",
        )
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(True, "sent")) as mock_send:
            client.post(f"/events/{child.id}/invite/")
        assert mock_send.call_args.kwargs["rrule"] is None, \
            "children must NOT get an RRULE (double-booking with the parent)"

    def test_non_recurring_event_sends_single_invite(self, client):
        from unittest.mock import patch
        m, tm = self._login(client, "oneoff")
        ev = self._event(m, tm)
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(True, "sent")) as mock_send:
            client.post(f"/events/{ev.id}/invite/")
        assert mock_send.call_args.kwargs["rrule"] is None

    def test_quarterly_parent_maps_to_monthly_interval_3(self, client):
        from unittest.mock import patch
        m, tm = self._login(client, "quarterly")
        parent = self._series(m, tm, rule="quarterly")  # 8 dates
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(True, "sent")) as mock_send:
            client.post(f"/events/{parent.id}/invite/")
        assert mock_send.call_args.kwargs["rrule"] == \
            "FREQ=MONTHLY;INTERVAL=3;COUNT=8"

    def test_childless_flagged_event_sends_single_invite(self, client):
        """An event with recurrence_rule but no materialized children
        (never a real series, or data drift) must not claim COUNT=12."""
        from unittest.mock import patch
        m, tm = self._login(client, "bare")
        ev = self._event(m, tm, recurrence_rule="weekly")
        with patch("core.services.calendar.send_calendar_invite",
                   return_value=(True, "sent")) as mock_send:
            client.post(f"/events/{ev.id}/invite/")
        assert mock_send.call_args.kwargs["rrule"] is None


@pytest.mark.django_db
class TestActualDuration:
    """Roadmap PR 10: nullable actual_duration_minutes on
    OneOnOneSession, autosaved from the meeting detail page. Garbage
    must never silently null a stored value, and the autosave must keep
    the PR 8 update_fields discipline (no prep_brief clobber)."""

    def _setup(self, client, slug):
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username=f"dur_{slug}", display_name="M",
            password_hash="x", email=f"dur_{slug}@example.com",
        )
        u = get_user_model().objects.create_user(
            username=m.email, email=m.email, password="x",
        )
        client.force_login(u)
        tm = TeamMember.objects.create(name="Direct", manager_id=m.id)
        s = OneOnOneSession.objects.create(
            manager=m, team_member=tm, session_date="2026-07-06",
            status="draft",
        )
        return m, tm, s

    def _autosave(self, client, s, duration):
        return client.post(f"/meetings/{s.id}/autosave/", {
            "direct_notes": "", "manager_notes": "", "followup_notes": "",
            "actual_duration_minutes": duration,
        })

    def test_autosave_sets_duration(self, client):
        _m, _tm, s = self._setup(client, "set")
        resp = self._autosave(client, s, "25")
        assert resp.status_code == 200
        s.refresh_from_db()
        assert s.actual_duration_minutes == 25

    def test_autosave_empty_clears_duration(self, client):
        _m, _tm, s = self._setup(client, "clear")
        OneOnOneSession.objects.filter(pk=s.id).update(
            actual_duration_minutes=30,
        )
        self._autosave(client, s, "")
        s.refresh_from_db()
        assert s.actual_duration_minutes is None

    def test_autosave_garbage_keeps_stored_value(self, client):
        _m, _tm, s = self._setup(client, "garbage")
        OneOnOneSession.objects.filter(pk=s.id).update(
            actual_duration_minutes=30,
        )
        self._autosave(client, s, "not-a-number")
        s.refresh_from_db()
        assert s.actual_duration_minutes == 30, \
            "garbage input must never silently null a recorded duration"

    def test_autosave_clamps_to_sane_bounds(self, client):
        _m, _tm, s = self._setup(client, "clamp")
        self._autosave(client, s, "99999")
        s.refresh_from_db()
        assert s.actual_duration_minutes == 1440
        self._autosave(client, s, "-5")
        s.refresh_from_db()
        assert s.actual_duration_minutes == 0

    def test_autosave_accepts_decimal_input(self, client):
        """Review finding: <input type=number> submits '45.5' (typed or
        pasted); it must round down, not be discarded as garbage."""
        _m, _tm, s = self._setup(client, "decimal")
        self._autosave(client, s, "45.5")
        s.refresh_from_db()
        assert s.actual_duration_minutes == 45

    def test_normalize_duration_edge_cases(self):
        """Model-level normalizer (review finding: one rule set for
        every write path, sibling of normalize_tags)."""
        nd = OneOnOneSession.normalize_duration
        assert nd("", 30) is None
        assert nd("  ", 30) is None
        assert nd(None, 30) is None
        assert nd("25", 30) == 25
        assert nd("45.5", 30) == 45
        assert nd("nan", 30) == 30
        assert nd("inf", 30) == 30
        assert nd("1e999", 30) == 30
        assert nd("junk", 30) == 30
        assert nd("junk", None) is None

    def test_autosave_without_field_leaves_duration_alone(self, client):
        """Requests that don't carry the field (older cached pages mid-
        deploy) must not touch the stored value."""
        _m, _tm, s = self._setup(client, "absent")
        OneOnOneSession.objects.filter(pk=s.id).update(
            actual_duration_minutes=45,
        )
        client.post(f"/meetings/{s.id}/autosave/", {
            "direct_notes": "note", "manager_notes": "",
            "followup_notes": "",
        })
        s.refresh_from_db()
        assert s.actual_duration_minutes == 45

    def test_duration_autosave_does_not_clobber_fresh_brief(self, client):
        """PR 8 regression guard extended to the new field: the autosave
        that writes duration must not write back a stale prep_brief."""
        _m, _tm, s = self._setup(client, "clobber")
        OneOnOneSession.objects.filter(pk=s.id).update(
            prep_brief="fresh brief from thread",
        )
        self._autosave(client, s, "40")
        s.refresh_from_db()
        assert s.actual_duration_minutes == 40
        assert s.prep_brief == "fresh brief from thread"

    def test_detail_page_renders_duration_input(self, client):
        _m, _tm, s = self._setup(client, "page")
        OneOnOneSession.objects.filter(pk=s.id).update(
            actual_duration_minutes=25,
        )
        body = client.get(f"/meetings/{s.id}/").content.decode()
        assert 'name="actual_duration_minutes"' in body
        assert 'value="25"' in body
        assert "Actual duration" in body
@pytest.mark.django_db
class TestAntiPatternDetector:
    """Unit tests for core.services.anti_patterns.detect_anti_patterns
    (pure function — no DB), plus one analytics-view integration check."""

    def test_clean_no_patterns(self):
        from core.services.anti_patterns import detect_anti_patterns
        cadence = [{"name": "Alice", "last_date": "2026-08-01", "days_ago": 3}]
        ratios = [{"name": "Alice", "positive": 4, "constructive": 1}]
        assert detect_anti_patterns(cadence, ratios) == []

    def test_ghost_overdue(self):
        from core.services.anti_patterns import detect_anti_patterns
        cadence = [{"name": "Alice", "last_date": "2026-07-01", "days_ago": 40}]
        pats = detect_anti_patterns(cadence, [])
        assert len(pats) == 1
        assert pats[0]["pattern"] == "The Ghost"
        assert "40 days" in pats[0]["evidence"]

    def test_ghost_never_met(self):
        from core.services.anti_patterns import detect_anti_patterns
        cadence = [{"name": "Alice", "last_date": None, "days_ago": None}]
        pats = detect_anti_patterns(cadence, [])
        assert pats and pats[0]["pattern"] == "The Ghost"
        assert "never had a recorded meeting" in pats[0]["evidence"]

    def test_micromanager(self):
        from core.services.anti_patterns import detect_anti_patterns
        ratios = [{"name": "Alice", "positive": 0, "constructive": 5}]
        pats = detect_anti_patterns([], ratios)
        assert "The Micromanager" in {p["pattern"] for p in pats}

    def test_buddy(self):
        from core.services.anti_patterns import detect_anti_patterns
        ratios = [{"name": "Alice", "positive": 3, "constructive": 0}]
        pats = detect_anti_patterns([], ratios)
        assert "The Buddy" in {p["pattern"] for p in pats}

    def test_scorekeeper(self):
        from core.services.anti_patterns import detect_anti_patterns
        ratios = [
            {"name": "Alice", "positive": 2, "constructive": 1},
            {"name": "Bob", "positive": 0, "constructive": 5},
        ]
        pats = detect_anti_patterns([], ratios)
        assert "The Scorekeeper" in {p["pattern"] for p in pats}

    def test_analytics_view_surfaces_anti_patterns(self, client):
        from datetime import date, timedelta
        from django.contrib.auth import get_user_model
        m = Manager.objects.create(
            username="ap_mgr", display_name="AP", password_hash="x",
            email="ap@example.com",
        )
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        # An event 25 days ago (>21) should surface "The Ghost".
        Event.objects.create(
            manager_id=m.id, title="Old 1:1", event_type="one_on_one",
            team_member=tm,
            scheduled_date=(date.today() - timedelta(days=25)).isoformat(),
            scheduled_time="09:00", status="completed",
        )
        u = get_user_model().objects.create_user(
            username="ap@example.com", email="ap@example.com", password="x",
        )
        client.force_login(u)
        resp = client.get("/analytics/")
        assert resp.status_code == 200
        assert b"Anti-patterns" in resp.content
        assert b"The Ghost" in resp.content


class TestManagementScore:
    """Unit tests for core.services.management_score.compute_management_score.
    Pure function — no DB access, no django_db marker needed."""

    def test_all_components_high_yields_high_grade(self):
        from core.services.management_score import compute_management_score
        res = compute_management_score({
            "feedback": 90, "cadence": 100, "streak": 14,
            "goals": 100, "actions": 100,
        })
        assert res["score"] >= 90
        assert res["grade"] == "A"
        assert res["subscores"] == {
            "feedback": 90, "cadence": 100, "streak": 100,
            "goals": 100, "actions": 100,
        }

    def test_no_data_returns_none(self):
        from core.services.management_score import compute_management_score
        res = compute_management_score({})
        assert res["score"] is None
        assert res["grade"] is None
        assert res["subscores"] == {}

    def test_partial_data_reweights(self):
        from core.services.management_score import compute_management_score
        # Only feedback present (weight 0.30) → score equals the feedback value.
        res = compute_management_score({"feedback": 40})
        assert res["score"] == 40
        assert res["subscores"] == {"feedback": 40}

    def test_streak_capped_at_target(self):
        from core.services.management_score import compute_management_score
        # 7 days → 50% of the 14-day target; 30 days → capped at 100%.
        assert compute_management_score({"streak": 7})["subscores"]["streak"] == 50
        assert compute_management_score({"streak": 30})["subscores"]["streak"] == 100

    def test_values_clamped_to_0_100(self):
        from core.services.management_score import compute_management_score
        res = compute_management_score({"feedback": 120, "cadence": -5})
        assert res["subscores"]["feedback"] == 100
        assert res["subscores"]["cadence"] == 0

    def test_grade_bands(self):
        from core.services.management_score import compute_management_score
        for score, expected in [(90, "A"), (75, "B"), (60, "C"), (40, "D"), (20, "F")]:
            res = compute_management_score({"feedback": score})
            assert res["score"] == score
            assert res["grade"] == expected


@pytest.mark.django_db
class TestDataExport:
    """GET /export/ — JSON archive of the manager's tenant-scoped data."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_exp", display_name="Todd",
            password_hash="x", email="todd_exp@example.com",
        )
        self._login_as(client, "todd_exp@example.com")
        return m

    def test_export_returns_json_archive(self, client):
        import json
        m = self._setup(client)
        TeamMember.objects.create(name="Alice", manager_id=m.id)
        JournalEntry.objects.create(
            entry_date="2026-05-08", entry_type="daily",
            content="hello", manager_id=m.id,
        )
        resp = client.get("/export/")
        assert resp.status_code == 200
        assert "application/json" in resp["Content-Type"]
        assert "attachment" in resp["Content-Disposition"]
        assert "manager-data-" in resp["Content-Disposition"]
        data = json.loads(resp.content)
        assert data["manager"]["email"] == "todd_exp@example.com"
        assert data["team_members"][0]["name"] == "Alice"
        assert data["journal_entries"][0]["content"] == "hello"
        assert data["events"] == []
        # Exports are audited so downloads are traceable (hardening follow-up).
        assert AuditLog.objects.filter(
            manager_id=m.id, action="export", entity_type="DataExport",
        ).exists()

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "test-export-ratelimit"}})
    def test_export_is_rate_limited(self, client):
        from core.views.settings_views import EXPORT_RATE_LIMIT
        self._setup(client)
        statuses = [
            client.get("/export/").status_code
            for _ in range(EXPORT_RATE_LIMIT + 1)
        ]
        assert statuses[:EXPORT_RATE_LIMIT] == [200] * EXPORT_RATE_LIMIT
        assert statuses[-1] == 429

    def test_export_is_tenant_scoped(self, client):
        import json
        m = self._setup(client)
        m2 = Manager.objects.create(
            username="other_exp", display_name="Other",
            password_hash="x", email="other_exp@example.com",
        )
        TeamMember.objects.create(name="MINE", manager_id=m.id)
        TeamMember.objects.create(name="THEIRS", manager_id=m2.id)
        resp = client.get("/export/")
        data = json.loads(resp.content)
        names = [r["name"] for r in data["team_members"]]
        assert "MINE" in names
        assert "THEIRS" not in names

    def test_export_omits_config_secrets(self, client):
        import json
        m = self._setup(client)
        from core.models import Config
        Config.objects.create(
            manager_id=m.id, key="anthropic_api_key", value="sk-super-secret",
        )
        resp = client.get("/export/")
        data = json.loads(resp.content)
        raw = resp.content.decode()
        assert "sk-super-secret" not in raw
        assert "config" not in data

    def test_export_requires_authenticated_manager(self, client):
        resp = client.get("/export/")
        assert resp.status_code in (302, 403)
        self._login_as(client, "stranger_exp@example.com")
        resp = client.get("/export/")
        assert resp.status_code == 403


@pytest.mark.django_db
class TestJournalDelete:
    """DELETE /journal/<id>/delete/ — remove a journal entry (UI review follow-up)."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_jdel", display_name="Todd",
            password_hash="x", email="todd_jdel@example.com",
        )
        self._login_as(client, "todd_jdel@example.com")
        entry = JournalEntry.objects.create(
            entry_date="2026-05-05", entry_type="daily",
            content="To delete", manager_id=m.id,
        )
        return m, entry

    def test_delete_removes_entry(self, client):
        m, entry = self._setup(client)
        resp = client.delete(f"/journal/{entry.id}/delete/")
        assert resp.status_code == 200
        assert JournalEntry.objects.for_manager(m.id).count() == 0

    def test_delete_cross_tenant_returns_404(self, client):
        m, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_jdel", display_name="Other",
            password_hash="x", email="other_jdel@example.com",
        )
        other = JournalEntry.objects.create(
            entry_date="2026-05-06", entry_type="daily",
            content="Theirs", manager_id=m2.id,
        )
        assert client.delete(f"/journal/{other.id}/delete/").status_code == 404
        assert JournalEntry.objects.for_manager(m2.id).count() == 1


@pytest.mark.django_db
class TestNotesEdit:
    """GET/POST /notes/<id>/edit/ — edit a running note (UI review follow-up)."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_ned", display_name="Todd",
            password_hash="x", email="todd_ned@example.com",
        )
        self._login_as(client, "todd_ned@example.com")
        note = RunningNote.objects.create(
            manager_id=m.id, note_date="2026-05-09",
            content="Original", category="general",
        )
        return m, note

    def test_edit_loads_form(self, client):
        _, note = self._setup(client)
        resp = client.get(f"/notes/{note.id}/edit/")
        assert resp.status_code == 200
        assert b"Original" in resp.content

    def test_edit_updates_note(self, client):
        m, note = self._setup(client)
        resp = client.post(f"/notes/{note.id}/edit/", {
            "note_date": "2026-05-09",
            "content": "Edited content",
            "category": "follow_up",
        })
        assert resp.status_code == 302
        note.refresh_from_db()
        assert note.content == "Edited content"
        assert note.category == "follow_up"


@pytest.mark.django_db
class TestFeedbackEdit:
    """GET/POST /feedback/<id>/edit/ — edit a feedback record."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_fed", display_name="Todd",
            password_hash="x", email="todd_fed@example.com",
        )
        self._login_as(client, "todd_fed@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        fb = Feedback.objects.create(
            team_member=tm, feedback_type="positive",
            situation="S1", manager_id=m.id,
        )
        return m, fb

    def test_edit_updates_feedback(self, client):
        m, fb = self._setup(client)
        resp = client.post(f"/feedback/{fb.id}/edit/", {
            "team_member": fb.team_member_id, "feedback_type": "constructive",
            "situation": "S2", "behavior": "B", "impact": "I",
        })
        assert resp.status_code == 302
        fb.refresh_from_db()
        assert fb.feedback_type == "constructive"
        assert fb.situation == "S2"


@pytest.mark.django_db
class TestTeamMembersEdit:
    """GET/POST /team/<id>/edit/ — edit a team member profile."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_ted", display_name="Todd",
            password_hash="x", email="todd_ted@example.com",
        )
        self._login_as(client, "todd_ted@example.com")
        member = TeamMember.objects.create(name="Alice", role="Eng", manager_id=m.id)
        return m, member

    def test_edit_updates_member(self, client):
        m, member = self._setup(client)
        resp = client.post(f"/team/{member.id}/edit/", {
            "name": "Alicia", "email": "alicia@example.com", "role": "Staff Eng",
        })
        assert resp.status_code == 302
        member.refresh_from_db()
        assert member.name == "Alicia"
        assert member.role == "Staff Eng"

    def test_edit_cross_tenant_returns_404(self, client):
        m, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_ted", display_name="Other",
            password_hash="x", email="other_ted@example.com",
        )
        other = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        assert client.post(f"/team/{other.id}/edit/", {"name": "Hacked"}).status_code == 404


@pytest.mark.django_db
class TestEventsUncomplete:
    """POST /events/<id>/uncomplete/ — undo Complete (reopen symmetry)."""

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
            username="todd_evu", display_name="Todd",
            password_hash="x", email="todd_evu@example.com",
        )
        self._login_as(client, "todd_evu@example.com")
        ev = Event.objects.create(
            manager_id=m.id, title="X", event_type="one_on_one",
            scheduled_date=(date.today() + timedelta(days=1)).isoformat(),
            scheduled_time="10:00", status="completed",
        )
        return m, ev

    def test_uncomplete_returns_to_scheduled(self, client):
        m, ev = self._setup(client)
        resp = client.post(f"/events/{ev.id}/uncomplete/")
        assert resp.status_code == 200
        ev.refresh_from_db()
        assert ev.status == "scheduled"

    def test_uncomplete_on_scheduled_returns_404(self, client):
        m, ev = self._setup(client)
        ev.status = "scheduled"
        ev.save(update_fields=["status"])
        assert client.post(f"/events/{ev.id}/uncomplete/").status_code == 404


@pytest.mark.django_db
class TestGoalStatus:
    """POST /goals/<id>/status/ — quick status change without the edit form."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_gst", display_name="Todd",
            password_hash="x", email="todd_gst@example.com",
        )
        self._login_as(client, "todd_gst@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        goal = Goal.objects.create(
            team_member=tm, quarter="Q2 2026", description="Ship X",
            status="not_started", manager_id=m.id,
        )
        return m, goal

    def test_status_updates_and_returns_list(self, client):
        m, goal = self._setup(client)
        resp = client.post(f"/goals/{goal.id}/status/", {"status": "in_progress"})
        assert resp.status_code == 200
        goal.refresh_from_db()
        assert goal.status == "in_progress"
        assert b"Ship X" in resp.content

    def test_invalid_status_returns_400(self, client):
        m, goal = self._setup(client)
        assert client.post(f"/goals/{goal.id}/status/", {"status": "bogus"}).status_code == 400

    def test_cross_tenant_returns_404(self, client):
        m, _ = self._setup(client)
        m2 = Manager.objects.create(
            username="other_gst", display_name="Other",
            password_hash="x", email="other_gst@example.com",
        )
        tm2 = TeamMember.objects.create(name="Bob", manager_id=m2.id)
        other = Goal.objects.create(
            team_member=tm2, quarter="Q2 2026", description="Other", manager_id=m2.id,
        )
        assert client.post(f"/goals/{other.id}/status/", {"status": "met"}).status_code == 404


@pytest.mark.django_db
class TestDelegationsComplete:
    """POST /delegations/<id>/complete/ — lightweight check-in."""

    def _login_as(self, client, email):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(
            username=email, email=email, password="x",
        )
        client.force_login(u)
        return u

    def _setup(self, client):
        m = Manager.objects.create(
            username="todd_dcm", display_name="Todd",
            password_hash="x", email="todd_dcm@example.com",
        )
        self._login_as(client, "todd_dcm@example.com")
        tm = TeamMember.objects.create(name="Alice", manager_id=m.id)
        dep = Delegation.objects.create(
            manager_id=m.id, team_member=tm, task="Do X", status="active",
        )
        return m, dep

    def test_complete_marks_completed(self, client):
        m, dep = self._setup(client)
        resp = client.post(f"/delegations/{dep.id}/complete/")
        assert resp.status_code == 200
        dep.refresh_from_db()
        assert dep.status == "completed"
        assert dep.completed_at is not None

    def test_complete_on_completed_returns_404(self, client):
        m, dep = self._setup(client)
        dep.status = "completed"
        dep.save(update_fields=["status"])
        assert client.post(f"/delegations/{dep.id}/complete/").status_code == 404


@pytest.mark.django_db
class TestEventsCompleteSeries:
    """POST /events/<id>/complete-series/ — complete a whole recurring series."""

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
            username="todd_ecs", display_name="Todd",
            password_hash="x", email="todd_ecs@example.com",
        )
        self._login_as(client, "todd_ecs@example.com")
        parent = Event.objects.create(
            manager_id=m.id, title="Weekly", event_type="one_on_one",
            scheduled_date=date.today().isoformat(), scheduled_time="10:00",
            status="scheduled", recurrence_rule="weekly",
        )
        child = Event.objects.create(
            manager_id=m.id, title="Weekly", event_type="one_on_one",
            scheduled_date="2099-01-01", scheduled_time="10:00",
            status="scheduled", parent_event=parent,
        )
        return m, parent, child

    def test_completes_parent_and_children(self, client):
        m, parent, child = self._setup(client)
        resp = client.post(f"/events/{parent.id}/complete-series/")
        assert resp.status_code == 200
        parent.refresh_from_db()
        child.refresh_from_db()
        assert parent.status == "completed"
        assert child.status == "completed"

    def test_child_is_not_series_parent_returns_400(self, client):
        m, parent, child = self._setup(client)
        assert client.post(f"/events/{child.id}/complete-series/").status_code == 400


class TestBackupCommandHelpers:
    """Unit tests for core.management.commands.backup_db pure helpers.
    No DB / no live pg_dump needed."""

    def test_backup_filename_format_and_sortability(self):
        from datetime import datetime
        from core.management.commands.backup_db import backup_filename
        a = backup_filename(datetime(2026, 5, 1, 3, 0, 0))
        b = backup_filename(datetime(2026, 5, 2, 3, 0, 0))
        assert a.startswith("manager-tool-")
        assert a.endswith(".sql.gz")
        assert a < b  # lexical order == chronological order

    def test_prune_keeps_most_recent(self, tmp_path):
        from core.management.commands.backup_db import prune_old_backups
        (tmp_path / "manager-tool-20260501-030000.sql.gz").write_text("a")
        (tmp_path / "manager-tool-20260502-030000.sql.gz").write_text("b")
        (tmp_path / "manager-tool-20260503-030000.sql.gz").write_text("c")
        deleted = prune_old_backups(tmp_path, keep=2)
        remaining = sorted(p.name for p in tmp_path.iterdir())
        assert deleted == [str(tmp_path / "manager-tool-20260501-030000.sql.gz")]
        assert remaining == [
            "manager-tool-20260502-030000.sql.gz",
            "manager-tool-20260503-030000.sql.gz",
        ]

    def test_pg_env_parses_url_without_leaking_password(self):
        from core.management.commands.backup_db import pg_env_from_url
        env = pg_env_from_url(
            "postgresql://user:pw@db.example.com:5433/mydb?sslmode=verify-full",
        )
        assert env["PGHOST"] == "db.example.com"
        assert env["PGPORT"] == "5433"
        assert env["PGUSER"] == "user"
        assert env["PGPASSWORD"] == "pw"
        assert env["PGDATABASE"] == "mydb"
        assert env["PGSSLMODE"] == "verify-full"
        # The password must live in env, never in argv (process-list safe).
        cmd = ["pg_dump", "--no-owner", "--no-privileges"]
        assert "pw" not in cmd
        assert all("pw" not in arg for arg in cmd)

    def test_command_has_dry_run_and_targets(self):
        from django.core.management import call_command
        import io
        out = io.StringIO()
        call_command("backup_db", "--dry-run", "--dir", "/tmp/does-not-exist", stdout=out)
        assert "DRY-RUN" in out.getvalue()

