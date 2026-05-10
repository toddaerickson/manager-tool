from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from django.shortcuts import get_object_or_404

from .forms import ActionItemForm, EventEditForm, EventForm, JournalEntryForm, TeamMemberForm
from .models import ActionItem, Event, JournalEntry, TeamMember
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


# ============================================================
# Phase 5.3 — Action Items / "To Do"
# ============================================================


@login_required
def todos_list(request):
    """Pending action items (status='pending') with overdue indicator,
    plus a collapsible Completed section. Mirrors Streamlit
    page_action_items but minus the data_editor and "promote to
    delegation" expander (delegations come in their own Phase 5 PR)."""
    manager, err = _require_manager(request)
    if err:
        return err
    today_iso = date.today().isoformat()
    pending = (
        ActionItem.objects.for_manager(manager.id)
        .filter(status="pending")
        .order_by("due_date", "id")
    )
    completed = (
        ActionItem.objects.for_manager(manager.id)
        .filter(status="completed")
        .order_by("-completed_at")[:20]
    )
    return render(request, "todos.html", {
        "pending": pending,
        "completed": completed,
        "today_iso": today_iso,
        "form": ActionItemForm(manager_id=manager.id),
    })


@login_required
@require_http_methods(["POST"])
def todos_add(request):
    """HTMX endpoint: create + return updated pending list partial.
    On error, returns the form fragment with errors."""
    manager, err = _require_manager(request)
    if err:
        return err
    form = ActionItemForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/todo_form.html", {
            "form": form,
        }, status=422)
    item = form.save(commit=False)
    item.manager_id = manager.id
    item.status = "pending"
    from django.utils import timezone
    item.created_at = timezone.now()
    item.save()
    pending = (
        ActionItem.objects.for_manager(manager.id)
        .filter(status="pending")
        .order_by("due_date", "id")
    )
    return render(request, "_partials/todo_list_after_add.html", {
        "pending": pending,
        "today_iso": date.today().isoformat(),
        "form": ActionItemForm(manager_id=manager.id),
    })


@login_required
@require_http_methods(["POST"])
def todos_complete(request, todo_id: int):
    """HTMX: set status='completed' + completed_at=now. Row swaps out
    of pending list. The Completed section refreshes via oob swap."""
    manager, err = _require_manager(request)
    if err:
        return err
    from django.utils import timezone
    updated = (
        ActionItem.objects.for_manager(manager.id)
        .filter(pk=todo_id, status="pending")
        .update(status="completed", completed_at=timezone.now())
    )
    if updated == 0:
        return HttpResponse(status=404)
    completed = (
        ActionItem.objects.for_manager(manager.id)
        .filter(status="completed")
        .order_by("-completed_at")[:20]
    )
    return render(request, "_partials/todo_row_completed.html", {
        "completed": completed,
    })


@login_required
@require_http_methods(["POST"])
def todos_uncomplete(request, todo_id: int):
    """HTMX: revert status='completed' → 'pending'; clears completed_at.
    Returns updated pending + completed lists via oob."""
    manager, err = _require_manager(request)
    if err:
        return err
    updated = (
        ActionItem.objects.for_manager(manager.id)
        .filter(pk=todo_id, status="completed")
        .update(status="pending", completed_at=None)
    )
    if updated == 0:
        return HttpResponse(status=404)
    pending = (
        ActionItem.objects.for_manager(manager.id)
        .filter(status="pending")
        .order_by("due_date", "id")
    )
    completed = (
        ActionItem.objects.for_manager(manager.id)
        .filter(status="completed")
        .order_by("-completed_at")[:20]
    )
    return render(request, "_partials/todo_row_uncompleted.html", {
        "pending": pending,
        "completed": completed,
        "today_iso": date.today().isoformat(),
    })


@login_required
@require_http_methods(["DELETE"])
def todos_delete(request, todo_id: int):
    """Hard-delete an action item. With confirmation in the UI."""
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = (
        ActionItem.objects.for_manager(manager.id).filter(pk=todo_id).delete()
    )
    if deleted == 0:
        return HttpResponse(status=404)
    return HttpResponse(status=200)


# ============================================================
# Phase 5.4 — Journal entries
# ============================================================

_MOOD_EMOJI = {1: "\U0001f62b", 2: "\U0001f614", 3: "\U0001f610", 4: "\U0001f60a", 5: "\U0001f525"}
_ENERGY_EMOJI = {1: "\U0001f62a", 2: "\U0001f615", 3: "\U0001f610", 4: "\U0001f4aa", 5: "\u26a1"}


@login_required
def journal_list(request):
    """Journal main page: today's entry form (pre-filled if exists) plus
    recent history. Mirrors Streamlit's page_journal Today + History tabs
    but in a single page — the form is always visible at top, history
    scrolls below.

    If an entry already exists for today, the form pre-fills so the user
    can edit in-place (update on save, not duplicate)."""
    manager, err = _require_manager(request)
    if err:
        return err
    today_iso = date.today().isoformat()
    existing = (
        JournalEntry.objects.for_manager(manager.id)
        .filter(entry_date=today_iso, entry_type="daily")
        .first()
    )
    if existing:
        form = JournalEntryForm(instance=existing)
        # Re-populate the date field as a date object for the widget.
        form.initial["entry_date"] = date.fromisoformat(existing.entry_date)
        if existing.mood is not None:
            form.initial["mood"] = str(existing.mood)
        if existing.energy is not None:
            form.initial["energy"] = str(existing.energy)
    else:
        form = JournalEntryForm(initial={
            "entry_date": date.today(),
            "entry_type": "daily",
        })
    entries = (
        JournalEntry.objects.for_manager(manager.id)
        .order_by("-entry_date", "-created_at")[:30]
    )
    # Compute journal streak (consecutive days ending today).
    streak = _journal_streak(manager.id, today_iso)
    return render(request, "journal.html", {
        "form": form,
        "entries": entries,
        "today_iso": today_iso,
        "existing_id": existing.id if existing else None,
        "streak": streak,
        "mood_emoji": _MOOD_EMOJI,
        "energy_emoji": _ENERGY_EMOJI,
    })


def _journal_streak(manager_id: int, today_iso: str) -> int:
    """Count consecutive days with a journal entry ending on today_iso.
    Returns 0 if no entry today."""
    dates = set(
        JournalEntry.objects.for_manager(manager_id)
        .values_list("entry_date", flat=True)
    )
    if today_iso not in dates:
        return 0
    streak = 0
    d = date.fromisoformat(today_iso)
    while d.isoformat() in dates:
        streak += 1
        d -= timedelta(days=1)
    return streak


@login_required
@require_http_methods(["POST"])
def journal_add(request):
    """HTMX endpoint: create or update a journal entry. If an entry_id
    is passed (hidden field), update that entry instead of creating.

    On success, returns the updated history list + cleared/refreshed form
    via OOB swap. On error, returns the form with validation errors."""
    manager, err = _require_manager(request)
    if err:
        return err
    existing_id = request.POST.get("existing_id")
    instance = None
    if existing_id:
        instance = (
            JournalEntry.objects.for_manager(manager.id)
            .filter(pk=existing_id)
            .first()
        )
    form = JournalEntryForm(request.POST, instance=instance)
    if not form.is_valid():
        return render(request, "_partials/journal_form.html", {
            "form": form,
            "existing_id": existing_id,
        }, status=422)
    entry = form.save(commit=False)
    entry.manager_id = manager.id
    from django.utils import timezone
    if not instance:
        entry.created_at = timezone.now()
    entry.updated_at = timezone.now()
    entry.save()
    # Return refreshed form + history via OOB.
    today_iso = date.today().isoformat()
    new_form = JournalEntryForm(instance=entry)
    new_form.initial["entry_date"] = date.fromisoformat(entry.entry_date)
    if entry.mood is not None:
        new_form.initial["mood"] = str(entry.mood)
    if entry.energy is not None:
        new_form.initial["energy"] = str(entry.energy)
    entries = (
        JournalEntry.objects.for_manager(manager.id)
        .order_by("-entry_date", "-created_at")[:30]
    )
    return render(request, "_partials/journal_list_after_add.html", {
        "form": new_form,
        "entries": entries,
        "today_iso": today_iso,
        "existing_id": entry.id,
        "streak": _journal_streak(manager.id, today_iso),
        "mood_emoji": _MOOD_EMOJI,
        "energy_emoji": _ENERGY_EMOJI,
    })


@login_required
def journal_edit(request, entry_id: int):
    """Edit a past journal entry. GET shows the form pre-filled;
    POST saves and redirects back to /journal/."""
    manager, err = _require_manager(request)
    if err:
        return err
    entry = get_object_or_404(
        JournalEntry.objects.for_manager(manager.id),
        pk=entry_id,
    )
    if request.method == "POST":
        form = JournalEntryForm(request.POST, instance=entry)
        if form.is_valid():
            from django.utils import timezone
            obj = form.save(commit=False)
            obj.updated_at = timezone.now()
            obj.save()
            return redirect("journal")
    else:
        form = JournalEntryForm(instance=entry)
        form.initial["entry_date"] = date.fromisoformat(entry.entry_date)
        if entry.mood is not None:
            form.initial["mood"] = str(entry.mood)
        if entry.energy is not None:
            form.initial["energy"] = str(entry.energy)
    return render(request, "journal_edit.html", {"form": form, "entry": entry})
