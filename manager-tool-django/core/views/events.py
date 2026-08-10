"""Views: events."""

import logging
from datetime import date, timedelta, timezone as _dt_tz

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    ActionItem, Event, OneOnOneSession, TeamMember,
)
from core.forms import (
    EventEditForm, EventForm,
)
from core.services.audit import log_mutation
from core.services.events import create_recurring_events
from core.services.journal import journal_streak as _journal_streak
from core.views._common import _require_manager

logger = logging.getLogger(__name__)

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
    # For one_on_one events, check if a meeting session is linked
    meeting_session = None
    if ev.event_type == "one_on_one":
        meeting_session = (
            OneOnOneSession.objects.for_manager(manager.id)
            .filter(event=ev)
            .first()
        )
    return render(request, "events_detail.html", {
        "ev": ev,
        "meeting_session": meeting_session,
    })


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
    from core.services.calendar import rrule_for_rule, send_calendar_invite
    # Series parent (recurrence_rule set, no parent of its own) → ONE
    # recurring RRULE invite covering the ACTUAL series (roadmap PR 10;
    # COUNT = parent + children in the DB, so an until_date-capped
    # series never over-invites). An orphaned child — parent deleted,
    # SET_NULL left recurrence_rule behind — has no children, so
    # rrule_for_rule's count<2 guard degrades it to a single invite
    # (review finding). Children and one-offs keep single invites.
    rrule = None
    child_ids = []
    if ev.recurrence_rule and ev.parent_event_id is None:
        child_ids = list(
            Event.objects.for_manager(manager.id)
            .filter(parent_event=ev)
            .values_list("id", flat=True)
        )
        rrule = rrule_for_rule(ev.recurrence_rule, 1 + len(child_ids))
        if rrule is None and child_ids:
            logger.warning(
                "Event %s has children but recurrence_rule %r has no "
                "RRULE mapping — sending a single-occurrence invite",
                ev.id, ev.recurrence_rule,
            )
    success, message = send_calendar_invite(
        ev, ev.team_member.email, ev.team_member.name,
        manager_id=manager.id, rrule=rrule,
    )
    if success:
        Event.objects.filter(pk=ev.id).update(calendar_invite_sent=1)
        if rrule and child_ids:
            # The parent's RRULE invite already covers every child date;
            # stamp the children so their pages show the sent state
            # instead of a button that would double-book the recipient
            # (review finding).
            Event.objects.for_manager(manager.id).filter(
                id__in=child_ids,
            ).update(calendar_invite_sent=1)
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
    """HTMX partial — actionable dashboard with three sections:
    1. Next Actions — prioritized queue of things to do right now
    2. Team Health — per-direct status indicators
    3. Weekly Recap — what happened in the last 7 days
    """
    from django.db.models import Max, Count
    from core.models import (
        Decision, Delegation, Feedback, Goal, JournalEntry,
    )

    if request.manager is None:
        return HttpResponseForbidden("No manager profile.")
    mid = request.manager.id
    today = date.today()
    today_iso = today.isoformat()
    week_ago_iso = (today - timedelta(days=7)).isoformat()

    # Daily coach (Tier-1 rule / Tier-2 AI) — cached per day, dismissable.
    from coaching.services import get_daily_suggestion
    coach = get_daily_suggestion(mid)

    # Today's wisdom (deterministic per calendar day) — small footer note.
    from coaching.services import get_daily_wisdom
    wisdom = get_daily_wisdom()

    members = list(TeamMember.objects.active_for_manager(mid).order_by("name"))

    # ── Next Actions ─────────────────────────────────────────
    actions = []

    # Overdue to-dos
    overdue_todos = (
        ActionItem.objects.active_for_manager(mid)
        .filter(status="pending", due_date__isnull=False, due_date__lt=today_iso)
        .order_by("-starred", "due_date")[:5]
    )
    for t in overdue_todos:
        actions.append({
            "urgency": 1, "icon": "!",
            "text": f"{'★ ' if t.starred else ''}Overdue: {t.description[:50]}",
            "detail": f"Due {t.due_date}",
            "url": "/todos/",
        })

    # Overdue delegation check-ins
    overdue_delegations = (
        Delegation.objects.for_manager(mid)
        .filter(status="active", check_in_date__isnull=False, check_in_date__lt=today_iso)
        .select_related("team_member")[:5]
    )
    for d in overdue_delegations:
        name = d.team_member.name if d.team_member else "unassigned"
        actions.append({
            "urgency": 2, "icon": "!",
            "text": f"Delegation check-in overdue: {d.task[:40]}",
            "detail": f"{name} — due {d.check_in_date}",
            "url": f"/delegations/{d.id}/edit/",
        })

    # Decisions due for review
    decisions_due = (
        Decision.objects.for_manager(mid)
        .filter(status="active", review_date__isnull=False, review_date__lte=today_iso)
        [:5]
    )
    for d in decisions_due:
        actions.append({
            "urgency": 3, "icon": "?",
            "text": f"Decision due for review: {d.title[:40]}",
            "detail": f"Review date {d.review_date}",
            "url": f"/decisions/{d.id}/edit/",
        })

    # Meeting cadence — members with no meeting in 14+ days
    cadence_qs = (
        Event.objects.for_manager(mid)
        .filter(status__in=["completed", "scheduled"], scheduled_date__lte=today_iso)
        .values("team_member_id")
        .annotate(last_date=Max("scheduled_date"))
    )
    cadence_map = {r["team_member_id"]: r["last_date"] for r in cadence_qs}
    for m in members:
        last = cadence_map.get(m.id)
        if last:
            days = (today - date.fromisoformat(last)).days
            if days >= 14:
                actions.append({
                    "urgency": 4, "icon": "~",
                    "text": f"1:1 with {m.name} overdue ({days} days)",
                    "detail": f"Last meeting {last}",
                    "url": "/events/schedule/",
                })
        else:
            actions.append({
                "urgency": 4, "icon": "~",
                "text": f"Never met with {m.name}",
                "detail": "Schedule a first 1:1",
                "url": "/events/schedule/",
            })

    # Feedback staleness — members with no feedback in 30+ days
    fb_qs = (
        Feedback.objects.for_manager(mid)
        .values("team_member_id")
        .annotate(last_at=Max("created_at"))
    )
    fb_map = {r["team_member_id"]: r["last_at"] for r in fb_qs}
    for m in members:
        last_fb = fb_map.get(m.id)
        if last_fb:
            from django.utils import timezone as _tz
            # feedback.created_at is TIMESTAMP (no tz) in PG, so psycopg2
            # returns naive datetimes. _tz.now() is aware. Coerce stored
            # values to UTC — Render's server clock is UTC.
            if last_fb.tzinfo is None:
                last_fb = last_fb.replace(tzinfo=_dt_tz.utc)
            days_fb = (_tz.now() - last_fb).days
            if days_fb >= 30:
                actions.append({
                    "urgency": 5, "icon": "~",
                    "text": f"No feedback for {m.name} in {days_fb} days",
                    "detail": "Consider giving positive or constructive feedback",
                    "url": f"/feedback/?member={m.id}",
                })
        else:
            actions.append({
                "urgency": 5, "icon": "~",
                "text": f"No feedback recorded for {m.name}",
                "detail": "Start with positive feedback",
                "url": f"/feedback/?member={m.id}",
            })

    # Journal streak
    streak = _journal_streak(mid, today_iso)
    if streak == 0:
        actions.append({
            "urgency": 6, "icon": "+",
            "text": "Write today's journal entry",
            "detail": "Keep your streak going",
            "url": "/journal/",
        })

    actions.sort(key=lambda a: a["urgency"])

    # ── Team Health ──────────────────────────────────────────
    # Per-member: days since last meeting, days since last feedback,
    # open goals count, active delegations count
    goal_counts = dict(
        Goal.objects.for_manager(mid)
        .values("team_member_id")
        .annotate(c=Count("id"))
        .values_list("team_member_id", "c")
    )
    del_counts = dict(
        Delegation.objects.for_manager(mid)
        .filter(status="active")
        .values("team_member_id")
        .annotate(c=Count("id"))
        .values_list("team_member_id", "c")
    )
    team_health = []
    for m in members:
        last_meeting = cadence_map.get(m.id)
        meeting_days = None
        if last_meeting:
            meeting_days = (today - date.fromisoformat(last_meeting)).days

        last_fb = fb_map.get(m.id)
        fb_days = None
        if last_fb:
            from django.utils import timezone as _tz
            if last_fb.tzinfo is None:
                last_fb = last_fb.replace(tzinfo=_dt_tz.utc)
            fb_days = (_tz.now() - last_fb).days

        team_health.append({
            "name": m.name,
            "id": m.id,
            "meeting_days": meeting_days,
            "meeting_status": "red" if meeting_days is None or meeting_days > 14 else ("yellow" if meeting_days > 7 else "green"),
            "fb_days": fb_days,
            "fb_status": "red" if fb_days is None or fb_days > 30 else ("yellow" if fb_days > 14 else "green"),
            "goals": goal_counts.get(m.id, 0),
            "delegations": del_counts.get(m.id, 0),
        })

    # Compute a Python-side aware UTC cutoff for the TIMESTAMP-column
    # filters below (created_at/completed_at/updated_at). Using `__date__gte`
    # on a naive TIMESTAMP under USE_TZ + non-UTC TIME_ZONE applies an
    # AT TIME ZONE cast that buckets UTC-stored values into the wrong day
    # around the boundary. SQLite tests don't see it; PG does. See the
    # adversarial review finding #4.
    from django.utils import timezone as _tz_recap
    week_ago_dt = _tz_recap.now() - timedelta(days=7)

    # ── Weekly Recap ─────────────────────────────────────────
    recap = {
        "meetings": Event.objects.for_manager(mid).filter(
            status__in=["completed", "scheduled"],
            scheduled_date__gte=week_ago_iso, scheduled_date__lte=today_iso,
        ).count(),
        "feedback_positive": Feedback.objects.for_manager(mid).filter(
            created_at__gte=week_ago_dt, feedback_type="positive",
        ).count(),
        "feedback_constructive": Feedback.objects.for_manager(mid).filter(
            created_at__gte=week_ago_dt, feedback_type="constructive",
        ).count(),
        "journal_entries": JournalEntry.objects.for_manager(mid).filter(
            entry_date__gte=week_ago_iso,
        ).count(),
        "delegations_completed": Delegation.objects.for_manager(mid).filter(
            status="completed", completed_at__gte=week_ago_dt,
        ).count(),
        "decisions_reviewed": Decision.objects.for_manager(mid).filter(
            status__in=["validated", "revised", "reversed"],
            updated_at__gte=week_ago_dt,
        ).count(),
    }
    recap["feedback_total"] = recap["feedback_positive"] + recap["feedback_constructive"]

    ctx = {
        "actions": actions,
        "team_health": team_health,
        "recap": recap,
        "streak": streak,
        "today_iso": today_iso,
        "coach": coach,
        "wisdom": wisdom,
    }
    return render(request, "_partials/dashboard_overview.html", ctx)


@login_required
@require_http_methods(["POST"])
def dashboard_coach_dismiss(request):
    """"Got it" — dismiss today's daily-coach suggestion. Returns an empty
    200 so the HTMX target swaps the card out of the DOM."""
    manager, err = _require_manager(request)
    if err:
        return err
    from coaching.models import CoachSuggestion
    CoachSuggestion.objects.for_manager(manager.id).filter(
        suggestion_date=date.today().isoformat(), dismissed=0,
    ).update(dismissed=1)
    return HttpResponse("")


