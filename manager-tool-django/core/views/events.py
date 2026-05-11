"""Views: events."""

from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    ActionItem, Event, TeamMember,
)
from core.forms import (
    EventEditForm, EventForm,
)
from core.services.audit import log_mutation
from core.services.events import create_recurring_events
from core.services.journal import journal_streak as _journal_streak
from core.views._common import _require_manager

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
def events_detail(request, event_id: int):
    """Phase 6 (D2 link contract) — minimal event-detail page. The
    canonical URL the user pastes into their Outlook invite. Shows
    title / date / time / participant / agenda / status. Phase 6+
    fleshes this out with notes, action items, coaching pane.

    Cross-tenant returns 404 (audit C1)."""
    manager, err = _require_manager(request)
    if err:
        return err
    ev = get_object_or_404(
        Event.objects.for_manager(manager.id).select_related("team_member"),
        pk=event_id,
    )
    return render(request, "events_detail.html", {"ev": ev})


@login_required
def events_edit(request, event_id: int):
    """Phase 6 (D1 resolution) — edit any event field. Title / agenda /
    location / duration / notes are MT's domain and edit cleanly.
    scheduled_date and scheduled_time are editable but Outlook isn't
    notified — the template shows a warning banner.

    Per CLAUDE.md: editing one occurrence in a recurring series does
    NOT propagate to siblings. The edit form operates on the single
    row regardless of parent_event_id."""
    manager, err = _require_manager(request)
    if err:
        return err
    ev = get_object_or_404(
        Event.objects.for_manager(manager.id),
        pk=event_id,
    )
    if request.method == "POST":
        form = EventEditForm(request.POST, instance=ev, manager_id=manager.id)
        if form.is_valid():
            form.save()
            return redirect("events-detail", event_id=ev.id)
    else:
        form = EventEditForm(instance=ev, manager_id=manager.id)
    return render(request, "events_edit.html", {"form": form, "ev": ev})


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
@require_http_methods(["POST"])
def events_send_invite(request, event_id: int):
    """D2 Option C — send an RFC 5545 calendar invite for a 1-on-1 event.

    Only works when the event has a team_member with an email address
    and the manager has SMTP configured."""
    manager, err = _require_manager(request)
    if err:
        return err
    ev = get_object_or_404(
        Event.objects.for_manager(manager.id).select_related("team_member"),
        pk=event_id,
    )
    if not ev.team_member or not ev.team_member.email:
        return render(request, "events_detail.html", {
            "ev": ev,
            "invite_error": "No email address for this team member.",
        })
    from core.services.calendar import send_calendar_invite
    success, message = send_calendar_invite(
        ev, ev.team_member.email, ev.team_member.name,
        manager_id=manager.id,
    )
    if success:
        Event.objects.filter(pk=ev.id).update(calendar_invite_sent=1)
        ev.refresh_from_db()
        log_mutation(manager.id, "create", "CalendarInvite", ev.id,
                     f"Sent invite to {ev.team_member.email} for '{ev.title}'")
    return render(request, "events_detail.html", {
        "ev": ev,
        "invite_success": message if success else None,
        "invite_error": message if not success else None,
    })


@login_required
def dashboard_overview(request):
    """HTMX partial — returns the overview panel HTML fragment.

    Mirrors the Streamlit `_dashboard_bundle` pattern (one cached call per
    manager_id) but with lazy loading so the page shell renders before
    any DB work happens.

    Panels use data from completed Phase 5 pages: team members, events,
    todos, journal. Coaching/AI panels deferred to Phase 6.
    """
    if request.manager is None:
        return HttpResponseForbidden("No manager profile.")
    mid = request.manager.id
    today_iso = date.today().isoformat()
    week_end_iso = (date.today() + timedelta(days=7)).isoformat()

    # Quick stats
    team_count = TeamMember.objects.active_for_manager(mid).count()
    upcoming_events = (
        Event.objects.for_manager(mid)
        .filter(status="scheduled", scheduled_date__gte=today_iso)
    )
    upcoming_count = upcoming_events.filter(
        scheduled_date__lt=week_end_iso,
    ).count()
    pending_todos = (
        ActionItem.objects.for_manager(mid)
        .filter(status="pending")
    )
    pending_count = pending_todos.count()
    overdue_todos = pending_todos.filter(
        due_date__isnull=False, due_date__lt=today_iso,
    )
    overdue_count = overdue_todos.count()
    streak = _journal_streak(mid, today_iso)

    # Lists for detail sections
    next_events = (
        upcoming_events
        .select_related("team_member")
        .order_by("scheduled_date", "scheduled_time")[:5]
    )
    overdue_list = overdue_todos.order_by("due_date")[:5]

    ctx = {
        "team_member_count": team_count,
        "upcoming_count": upcoming_count,
        "pending_count": pending_count,
        "overdue_count": overdue_count,
        "streak": streak,
        "next_events": next_events,
        "overdue_list": overdue_list,
        "today_iso": today_iso,
    }
    return render(request, "_partials/dashboard_overview.html", ctx)


