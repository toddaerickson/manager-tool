from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import EventForm, TeamMemberForm
from .models import Event, TeamMember
from .services.events import create_recurring_events


def hello(request):
    """Public landing page. Shows a sign-in link if anonymous, otherwise
    a link to the dashboard."""
    if request.user.is_authenticated:
        body = (
            f"Hello {request.user.email or 'authenticated user'} — "
            f"go to /dashboard/ to see your team.\n"
        )
    else:
        body = (
            "Manager Tool — Django scaffold.\n"
            "Sign in: /accounts/google/login/\n"
        )
    return HttpResponse(body, content_type="text/plain")


def sentry_debug(request):
    """Trigger a deliberate exception so Sentry can prove it captures errors.

    Phase 1 → 2 gate: a hit on this URL must show up in the Sentry dashboard
    within 60 seconds.
    """
    raise ZeroDivisionError("sentry-debug: deliberate test exception")


@login_required
def dashboard(request):
    """Phase 3 → 4: template-rendered dashboard with a Tailwind sidebar
    layout. The overview panel loads via HTMX (see dashboard.html)."""
    if request.manager is None:
        return HttpResponseForbidden(
            f"No manager profile is linked to {request.user.email}. "
            "Ask an administrator to create one."
        )
    return render(request, "dashboard.html")


def _require_manager(request):
    """Returns (manager, None) if OK; (None, response) to short-circuit."""
    if request.manager is None:
        return None, HttpResponseForbidden(
            f"No manager profile is linked to {request.user.email}."
        )
    return request.manager, None


@login_required
def team_members_list(request):
    """Phase 5.1 — Team Members list + Add form. HTMX target is the
    member-list partial; the form posts to /team/add/.

    Active members shown in the main list; soft-deleted members within
    the 30-day undo window appear in a "Recently deleted" section with
    a restore button.
    """
    manager, err = _require_manager(request)
    if err:
        return err
    members = TeamMember.objects.active_for_manager(manager.id).order_by("name")
    deleted = TeamMember.objects.recently_deleted_for_manager(manager.id)
    return render(request, "team_members.html", {
        "members": members,
        "deleted_members": deleted,
        "form": TeamMemberForm(),
    })


@login_required
@require_http_methods(["POST"])
def team_members_add(request):
    """HTMX endpoint: validate + create + return updated list partial.
    On error, returns the form fragment with errors so HTMX swaps it back
    in. Both responses target #member-list (form re-render handles itself)."""
    manager, err = _require_manager(request)
    if err:
        return err
    form = TeamMemberForm(request.POST)
    if not form.is_valid():
        return render(
            request, "_partials/team_member_form.html",
            {"form": form}, status=422,
        )
    member = form.save(commit=False)
    member.manager_id = manager.id
    member.save()
    members = TeamMember.objects.active_for_manager(manager.id).order_by("name")
    # Return BOTH the cleared form (oob swap) and the updated list.
    return render(request, "_partials/team_member_list_after_add.html", {
        "members": members,
        "form": TeamMemberForm(),
    })


@login_required
@require_http_methods(["DELETE"])
def team_members_delete(request, member_id: int):
    """HTMX soft-delete: stamps deleted_at = now() if the row belongs
    to this manager. Returns the updated "Recently deleted" panel via
    hx-swap-oob and lets the row swap out (HTMX target is the row).

    Cross-tenant attempts return 404 — audit C1 "looks like the row
    doesn't exist" pattern rather than 403 (which leaks existence).

    Hard-delete after the 30-day undo window happens via the
    `purge_deleted_team_members` management command (Phase 6 wires
    Render Cron).
    """
    from django.utils import timezone
    manager, err = _require_manager(request)
    if err:
        return err
    updated = (
        TeamMember.objects
        .active_for_manager(manager.id)
        .filter(pk=member_id)
        .update(deleted_at=timezone.now())
    )
    if updated == 0:
        return HttpResponse(status=404)
    return render(request, "_partials/team_member_row_deleted.html", {
        "deleted_members": TeamMember.objects.recently_deleted_for_manager(manager.id),
    })


@login_required
@require_http_methods(["POST"])
def team_members_restore(request, member_id: int):
    """HTMX restore: clears deleted_at if within the 30-day window.
    Returns the updated active list (oob) and the updated deleted panel."""
    manager, err = _require_manager(request)
    if err:
        return err
    updated = (
        TeamMember.objects
        .recently_deleted_for_manager(manager.id)
        .filter(pk=member_id)
        .update(deleted_at=None)
    )
    if updated == 0:
        return HttpResponse(status=404)
    return render(request, "_partials/team_member_row_restored.html", {
        "members": TeamMember.objects.active_for_manager(manager.id).order_by("name"),
        "deleted_members": TeamMember.objects.recently_deleted_for_manager(manager.id),
    })


# ============================================================
# Phase 5.2a — Events (one-off; recurring comes in 5.2b)
# ============================================================

# Lex order of YYYY-MM-DD matches chronological — see CLAUDE.md
# "Date semantics". Compute bounds in Python and bind as TEXT.

_TODAY_HEADER_ISO = None  # set per-request via date.today().isoformat()


def _date_label(iso: str, today_iso: str, tomorrow_iso: str) -> str:
    if iso == today_iso:
        return "Today"
    if iso == tomorrow_iso:
        return "Tomorrow"
    try:
        return date.fromisoformat(iso).strftime("%a %b %-d")
    except ValueError:
        return iso


def _group_events_by_date(events, today_iso, tomorrow_iso):
    """Returns [(date_label, [events])] in chronological order."""
    by_date = {}
    for ev in events:
        by_date.setdefault(ev.scheduled_date, []).append(ev)
    return [
        (_date_label(d, today_iso, tomorrow_iso), by_date[d])
        for d in sorted(by_date.keys())
    ]


@login_required
def events_upcoming(request):
    """Phase 5.2a — Upcoming events (status='scheduled', date >= today)
    grouped by date. Mirrors Streamlit's page_upcoming_events but only
    the events portion (todos / check-ins / goals come in their own
    Phase 5 sub-PRs)."""
    manager, err = _require_manager(request)
    if err:
        return err
    today_iso = date.today().isoformat()
    tomorrow_iso = (date.today() + timedelta(days=1)).isoformat()
    events = (
        Event.objects.for_manager(manager.id)
        .filter(status="scheduled", scheduled_date__gte=today_iso)
        .select_related("team_member")
        .order_by("scheduled_date", "scheduled_time")
    )
    return render(request, "events_upcoming.html", {
        "groups": _group_events_by_date(events, today_iso, tomorrow_iso),
        "any_events": events.exists(),
    })


_NEAR_DUP_WINDOW_SECONDS = 30


def _recently_created_dup(*, manager_id, scheduled_date, scheduled_time,
                          title, team_member):
    """Defense against double-submit: returns True if an event with the
    same (manager, date, time, title, team_member) was created within
    the last _NEAR_DUP_WINDOW_SECONDS. The window is short enough that
    intentional resubmits (user changed their mind, hits Schedule again
    for the same slot) succeed; long enough to catch double-clicks and
    refresh-resubmits.

    For recurring series we only check the parent's row — if the parent
    is a near-dup the whole series is a near-dup."""
    from datetime import timedelta
    from django.utils import timezone
    cutoff = timezone.now() - timedelta(seconds=_NEAR_DUP_WINDOW_SECONDS)
    return Event.objects.for_manager(manager_id).filter(
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        title=title,
        team_member=team_member,
        created_at__gte=cutoff,
    ).exists()


@login_required
def events_schedule(request):
    """Phase 5.2b — branches on recurrence_rule:
      - blank → single Event.create (one-off)
      - weekly/monthly/quarterly → create_recurring_events service
        (parent + N children, atomic via transaction.atomic; the
        no-orphan guarantee is asserted by smoke_pg_django.py)
    Both paths redirect to /events/ on success.

    Pre-create dedupe: if an identical event was created within the
    last _NEAR_DUP_WINDOW_SECONDS, treat the request as a duplicate
    submit and redirect WITHOUT creating. Defense against double-click
    and browser-refresh-resubmit. Per-row Delete in /events/ cleans up
    older duplicates the user wants to remove."""
    manager, err = _require_manager(request)
    if err:
        return err
    if request.method == "POST":
        form = EventForm(request.POST, manager_id=manager.id)
        if form.is_valid():
            rule = form.cleaned_data.get("recurrence_rule") or ""

            if _recently_created_dup(
                manager_id=manager.id,
                scheduled_date=form.cleaned_data["scheduled_date"],
                scheduled_time=form.cleaned_data["scheduled_time"],
                title=form.cleaned_data["title"],
                team_member=form.cleaned_data.get("team_member"),
            ):
                # Silent dedupe — the user already got their event; no
                # error message needed (would be confusing if it was a
                # genuine refresh on slow network).
                return redirect("events-upcoming")

            if rule:
                # cleaned_data["scheduled_date"] is iso string after
                # clean_scheduled_date; the service expects a date.
                sched_d = date.fromisoformat(form.cleaned_data["scheduled_date"])
                until = form.cleaned_data.get("until_date")
                try:
                    create_recurring_events(
                        manager_id=manager.id,
                        title=form.cleaned_data["title"],
                        event_type=form.cleaned_data["event_type"],
                        start_date=sched_d,
                        scheduled_time=form.cleaned_data["scheduled_time"],
                        rule=rule,
                        until_date=until,
                        team_member=form.cleaned_data.get("team_member"),
                        duration_minutes=form.cleaned_data.get("duration_minutes") or 30,
                        location=form.cleaned_data.get("location"),
                        agenda=form.cleaned_data.get("agenda"),
                    )
                except (TypeError, ValueError) as e:
                    form.add_error(None, str(e))
                else:
                    return redirect("events-upcoming")
            else:
                from django.utils import timezone
                ev = form.save(commit=False)
                ev.manager_id = manager.id
                ev.status = "scheduled"
                # Populate created_at explicitly. Schema gives PG a
                # DB-level DEFAULT CURRENT_TIMESTAMP, but Django models
                # don't know that, so SQLite (and any code path that
                # reads back the value before re-fetching) sees NULL.
                # Setting here makes the dedupe check backend-agnostic.
                ev.created_at = timezone.now()
                ev.save()
                return redirect("events-upcoming")
    else:
        form = EventForm(manager_id=manager.id)
    return render(request, "events_schedule.html", {"form": form})


@login_required
@require_http_methods(["POST"])
def events_cancel(request, event_id: int):
    """HTMX: set status='cancelled' if currently scheduled. Returns an
    empty 200 — HTMX hx-target removes the row via outerHTML swap.
    Cross-tenant or already-non-scheduled returns 404 (audit C1
    pattern)."""
    manager, err = _require_manager(request)
    if err:
        return err
    updated = (
        Event.objects.for_manager(manager.id)
        .filter(pk=event_id, status="scheduled")
        .update(status="cancelled")
    )
    if updated == 0:
        return HttpResponse(status=404)
    return HttpResponse(status=200)


@login_required
@require_http_methods(["POST"])
def events_complete(request, event_id: int):
    """HTMX: set status='completed'. Notes-on-complete UI deferred to
    a follow-up sub-PR; this PR does status-only."""
    manager, err = _require_manager(request)
    if err:
        return err
    updated = (
        Event.objects.for_manager(manager.id)
        .filter(pk=event_id, status="scheduled")
        .update(status="completed")
    )
    if updated == 0:
        return HttpResponse(status=404)
    return HttpResponse(status=200)


@login_required
@require_http_methods(["DELETE"])
def events_delete(request, event_id: int):
    """Hard-delete an event row. Different from `events_cancel` which
    sets status='cancelled' and keeps the row for history. Use Delete
    for cleaning up duplicates / mistakes you don't want recorded.

    On a recurring-series PARENT, deleting the parent leaves children
    with parent_event_id NULL (FK ON DELETE SET NULL); siblings of a
    deleted CHILD are unaffected.

    Cross-tenant attempts return 404 (audit C1 pattern)."""
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = (
        Event.objects.for_manager(manager.id).filter(pk=event_id).delete()
    )
    if deleted == 0:
        return HttpResponse(status=404)
    return HttpResponse(status=200)


@login_required
def dashboard_overview(request):
    """HTMX partial — returns the overview panel HTML fragment.

    Mirrors the Streamlit `_dashboard_bundle` pattern (one cached call per
    manager_id) but with lazy loading so the page shell renders before
    any DB work happens.
    """
    if request.manager is None:
        return HttpResponseForbidden("No manager profile.")
    ctx = {
        "team_member_count": TeamMember.objects.for_manager(request.manager.id).count(),
    }
    return render(request, "_partials/dashboard_overview.html", ctx)
