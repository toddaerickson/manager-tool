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
        assert "id=" + str(m.id) in body

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
class TestEventsCancelComplete:
    """POST /events/<id>/cancel/ + /events/<id>/complete/ — HTMX status
    transitions, cross-tenant rejection."""

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

    def test_cancel_sets_status_cancelled(self, client):
        m, ev = self._setup(client)
        resp = client.post(f"/events/{ev.id}/cancel/")
        assert resp.status_code == 200
        ev.refresh_from_db()
        assert ev.status == "cancelled"

    def test_cancel_already_non_scheduled_returns_404(self, client):
        m, ev = self._setup(client)
        ev.status = "completed"
        ev.save()
        resp = client.post(f"/events/{ev.id}/cancel/")
        assert resp.status_code == 404

    def test_cancel_cross_tenant_returns_404(self, client):
        m, _ = self._setup(client)  # Todd
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
        resp = client.post(f"/events/{other.id}/cancel/")
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.status == "scheduled"  # untouched

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

    def test_get_not_allowed_on_status_endpoints(self, client):
        m, ev = self._setup(client)
        assert client.get(f"/events/{ev.id}/cancel/").status_code == 405
        assert client.get(f"/events/{ev.id}/complete/").status_code == 405
