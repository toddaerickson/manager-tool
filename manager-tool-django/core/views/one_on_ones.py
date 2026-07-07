"""Views: one-on-one meetings."""

import logging
import re
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.forms import MeetingActionItemForm, OneOnOneSessionForm
from core.models import ActionItem, OneOnOneSession, TeamMember
from core.services.audit import log_mutation
from core.views._common import (
    _parse_member_filter,
    _require_manager,
    get_member_context,
)

logger = logging.getLogger(__name__)


def _all_meeting_tags_for_manager(mid: int) -> list[str]:
    """Distinct tag values used across this manager's meetings, sorted."""
    bag: set[str] = set()
    rows = (
        OneOnOneSession.objects.for_manager(mid)
        .exclude(tags__isnull=True)
        .exclude(tags="")
        .values_list("tags", flat=True)
    )
    for row in rows:
        for t in row.split(","):
            if t:
                bag.add(t)
    return sorted(bag)


# ============================================================
# One-on-One Meetings — 10/10/10 structured meeting recorder
# ============================================================


@login_required
def one_on_ones_list(request):
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    member_id = _parse_member_filter(request)
    search_query = request.GET.get("q", "").strip()
    tag_query = request.GET.get("tag", "").strip().lower()
    today_iso = date.today().isoformat()

    sessions = OneOnOneSession.objects.for_manager(mid).select_related(
        "team_member", "event",
    )
    if member_id:
        sessions = sessions.filter(team_member_id=member_id)
    if search_query:
        from django.db.models import Q
        sessions = sessions.filter(
            Q(direct_notes__icontains=search_query)
            | Q(manager_notes__icontains=search_query)
            | Q(followup_notes__icontains=search_query)
        )
    if tag_query:
        # Match the tag as a whole CSV element on both PG (~*) and SQLite
        # (Django registers a Python re-backed REGEXP). Stored tags are
        # already normalized to lowercase and trimmed.
        pattern = r"(^|,)" + re.escape(tag_query) + r"(,|$)"
        sessions = sessions.filter(tags__iregex=pattern)

    # Draft sessions first, then by date descending
    drafts = sessions.filter(status="draft").order_by("-session_date")
    completed = sessions.filter(status="completed").order_by("-session_date")[:50]

    members = TeamMember.objects.active_for_manager(mid).order_by("name")

    # Cadence health: days since last completed meeting per active member
    cadence = []
    for m in members:
        last = (
            OneOnOneSession.objects.for_manager(mid)
            .filter(team_member=m, status="completed")
            .order_by("-session_date")
            .values_list("session_date", flat=True)
            .first()
        )
        if last:
            days = (date.today() - date.fromisoformat(last)).days
        else:
            days = None  # never met
        cadence.append({"member": m, "days_since": days})

    form = OneOnOneSessionForm(manager_id=mid)
    form.initial["session_date"] = date.today()

    return render(request, "meetings.html", {
        "drafts": drafts,
        "completed": completed,
        "form": form,
        "members": members,
        "selected_member": member_id,
        "search_query": search_query,
        "selected_tag": tag_query,
        "all_tags": _all_meeting_tags_for_manager(mid),
        "cadence": cadence,
        "today_iso": today_iso,
    })


@login_required
@require_http_methods(["GET"])
def one_on_ones_new(request):
    """Dedicated 'start a new 1:1' page.

    Renders the same form the Meetings page folds out, on its own route
    so the sidebar can link straight to it. The form POSTs to
    meetings-add, which creates the draft session and redirects into the
    10/10/10 recorder — identical to the in-page 'Start new meeting'.
    """
    manager, err = _require_manager(request)
    if err:
        return err
    form = OneOnOneSessionForm(manager_id=manager.id)
    form.initial["session_date"] = date.today()
    return render(request, "meetings_new.html", {"form": form})


@login_required
@require_http_methods(["POST"])
def one_on_ones_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = OneOnOneSessionForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/meeting_form.html", {
            "form": form,
        }, status=422)
    session = form.save(commit=False)
    session.manager = manager
    session.status = "draft"
    now = timezone.now()
    session.created_at = now
    session.updated_at = now
    from django.db import transaction
    try:
        with transaction.atomic():
            session.save()
    except IntegrityError:
        # Unique constraint: one session per member per date
        existing = OneOnOneSession.objects.for_manager(manager.id).filter(
            team_member=session.team_member,
            session_date=session.session_date,
        ).first()
        if existing:
            return redirect("meetings-detail", session_id=existing.id)
        raise
    log_mutation(manager.id, "create", "OneOnOneSession", session.id,
                 f"Meeting with {session.team_member.name} on {session.session_date}")
    return redirect("meetings-detail", session_id=session.id)


@login_required
def one_on_ones_detail(request, session_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    session = get_object_or_404(
        OneOnOneSession.objects.for_manager(manager.id).select_related(
            "team_member", "event",
        ),
        pk=session_id,
    )
    form = OneOnOneSessionForm(instance=session, manager_id=manager.id)
    if session.session_date:
        try:
            form.initial["session_date"] = date.fromisoformat(session.session_date)
        except ValueError:
            logger.warning(
                "OneOnOneSession %s has unparseable session_date %r; "
                "leaving form initial unset",
                session.pk, session.session_date,
            )

    context = get_member_context(manager.id, session.team_member)
    action_items = ActionItem.objects.for_manager(manager.id).filter(
        one_on_one_session=session,
    ).order_by("-created_at")
    action_form = MeetingActionItemForm()

    # Prep mode: a draft agenda built from this direct's open delegations
    # and carried-over action items, so "Your Agenda" starts as a checklist
    # instead of a blank box. The template only offers it when manager_notes
    # is empty, and it never overwrites — the manager clicks to pull it in.
    prep_lines = []
    for d in context["open_delegations"]:
        line = f"- {d.task}"
        if d.check_in_date:
            line += f" (check-in {d.check_in_date})"
        prep_lines.append(line)
    for a in context["open_actions"]:
        line = f"- {a.description}"
        if a.due_date:
            line += f" (due {a.due_date})"
        prep_lines.append(line)
    prep_agenda = "\n".join(prep_lines)

    return render(request, "meetings_detail.html", {
        "session": session,
        "form": form,
        "action_items": action_items,
        "action_form": action_form,
        "prep_agenda": prep_agenda,
        "prep_count": len(prep_lines),
        "prep_brief_state": _prep_brief_state(session),
        **context,
    })


@login_required
@require_http_methods(["POST"])
def one_on_ones_autosave(request, session_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    session = get_object_or_404(
        OneOnOneSession.objects.for_manager(manager.id),
        pk=session_id,
    )
    changed = False
    for field in ("direct_notes", "manager_notes", "followup_notes"):
        new_val = request.POST.get(field, "")
        old_val = getattr(session, field) or ""
        if new_val != old_val:
            setattr(session, field, new_val)
            changed = True

    if "tags" in request.POST:
        new_tags = OneOnOneSession.normalize_tags(request.POST["tags"]) or None
        if new_tags != session.tags:
            session.tags = new_tags
            changed = True

    if "actual_duration_minutes" in request.POST:
        new_duration = OneOnOneSession.normalize_duration(
            request.POST["actual_duration_minutes"],
            session.actual_duration_minutes,
        )
        if new_duration != session.actual_duration_minutes:
            session.actual_duration_minutes = new_duration
            changed = True

    # Also update event if sent
    event_id = request.POST.get("event")
    if event_id:
        try:
            event_id = int(event_id)
        except ValueError:
            event_id = None
    else:
        event_id = None
    if session.event_id != event_id:
        session.event_id = event_id
        changed = True

    if changed:
        session.updated_at = timezone.now()
        # Explicit update_fields: a full-row save() would write back the
        # STALE prep_brief this request fetched at start, silently
        # erasing a brief the background thread committed mid-request
        # (PR 8 review finding — autosave fires every 2s while the ~10s
        # generation runs).
        session.save(update_fields=[
            "direct_notes", "manager_notes", "followup_notes",
            "tags", "event", "actual_duration_minutes", "updated_at",
        ])

    now_str = timezone.localtime().strftime("%I:%M %p").lstrip("0")
    return render(request, "_partials/meeting_save_indicator.html", {
        "time": now_str,
        "changed": changed,
    })


@login_required
@require_http_methods(["POST"])
def one_on_ones_complete(request, session_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    session = get_object_or_404(
        OneOnOneSession.objects.for_manager(manager.id),
        pk=session_id,
    )
    old_status = session.status
    if old_status == "completed":
        session.status = "draft"
    else:
        session.status = "completed"
    session.updated_at = timezone.now()
    # update_fields for the same reason as autosave: never write back a
    # stale prep_brief fetched before a background generation finished.
    session.save(update_fields=["status", "updated_at"])
    log_mutation(manager.id, "update", "OneOnOneSession", session.id,
                 f"Status: {old_status} → {session.status}")
    return redirect("meetings-detail", session_id=session.id)


@login_required
@require_http_methods(["DELETE"])
def one_on_ones_delete(request, session_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = (
        OneOnOneSession.objects.for_manager(manager.id)
        .filter(pk=session_id)
        .delete()
    )
    if deleted == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "delete", "OneOnOneSession", session_id,
                 "Deleted meeting session")
    return HttpResponse(status=200)


@login_required
@require_http_methods(["POST"])
def one_on_ones_add_action(request, session_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    session = get_object_or_404(
        OneOnOneSession.objects.for_manager(manager.id),
        pk=session_id,
    )
    form = MeetingActionItemForm(request.POST)
    if form.is_valid():
        due = form.cleaned_data.get("due_date")
        item = ActionItem.objects.create(
            manager_id=manager.id,
            one_on_one_session=session,
            event=session.event,
            description=form.cleaned_data["description"],
            due_date=due.isoformat() if due else None,
            status="pending",
            created_at=timezone.now(),
        )
        log_mutation(manager.id, "create", "ActionItem", item.id,
                     f"Action from meeting: {item.description[:60]}")

    action_items = ActionItem.objects.for_manager(manager.id).filter(
        one_on_one_session=session,
    ).order_by("-created_at")
    action_form = MeetingActionItemForm()
    return render(request, "_partials/meeting_action_items.html", {
        "session": session,
        "action_items": action_items,
        "action_form": action_form,
    })


# ---------------------------------------------------------------------------
# Pre-1:1 AI prep brief (roadmap PR 8)
# ---------------------------------------------------------------------------

# Poll timeout: after this many seconds without a result, the pending
# partial flips to an explicit "failed — retry" state. A worker restart
# mid-generation is EXPECTED under gthread (deploys every merge), so a
# dead thread must surface as a retry, never an eternal spinner.
PREP_BRIEF_TIMEOUT_SECONDS = 60


def _prep_brief_state(session):
    """ready | pending | failed | idle — single source of truth for
    which branch of the prep_brief partial renders."""
    if session.prep_brief:
        return "ready"
    if not session.prep_brief_requested_at:
        return "idle"
    age = (timezone.now() - session.prep_brief_requested_at).total_seconds()
    return "pending" if age < PREP_BRIEF_TIMEOUT_SECONDS else "failed"


def _render_prep_brief(request, session, status=200):
    return render(request, "_partials/prep_brief.html", {
        "session": session,
        "state": _prep_brief_state(session),
    }, status=status)


@login_required
def one_on_ones_prep_brief(request, session_id: int):
    """HTMX poll endpoint. The pending branch re-polls every 2s; the
    ready/failed/idle branches carry no hx-trigger, so polling stops
    by construction (same idiom as journal_coaching)."""
    manager, err = _require_manager(request)
    if err:
        return err
    session = get_object_or_404(
        OneOnOneSession.objects.for_manager(manager.id), pk=session_id,
    )
    return _render_prep_brief(request, session)


@login_required
@require_http_methods(["POST"])
def one_on_ones_prep_brief_generate(request, session_id: int):
    """Kick off brief generation in a background thread (the API call
    takes ~10s; the request returns immediately with the pending
    partial). COACHING_ENABLED=False under settings_test — the daemon
    thread would leak writes past the test transaction, same rationale
    as the journal coaching spawn."""
    manager, err = _require_manager(request)
    if err:
        return err
    session = get_object_or_404(
        OneOnOneSession.objects.for_manager(manager.id), pk=session_id,
    )

    stamp = timezone.now()
    session.prep_brief = None
    session.prep_brief_requested_at = stamp
    session.save(update_fields=["prep_brief", "prep_brief_requested_at"])

    from django.conf import settings
    if getattr(settings, "COACHING_ENABLED", True):
        import threading

        def _generate(session_id, manager_id, stamp):
            try:
                from coaching.services import generate_prep_brief
                brief = generate_prep_brief(session_id, manager_id)
                if brief:
                    # CAS-style guard (the #128 double-tap doctrine): the
                    # write lands only if requested_at still carries THIS
                    # request's stamp. A Regenerate/Retry click re-stamps
                    # the row, so a slower, earlier thread's late result
                    # is discarded instead of overwriting the newer brief
                    # with stale content.
                    updated = OneOnOneSession.objects.filter(
                        pk=session_id, prep_brief_requested_at=stamp,
                    ).update(prep_brief=brief)
                    if updated:
                        log_mutation(manager_id, "update", "OneOnOneSession",
                                     session_id, "AI prep brief generated",
                                     actor="system")
                    else:
                        logger.info(
                            "Stale prep-brief result discarded for session "
                            "%d (regenerated meanwhile)", session_id,
                        )
                else:
                    # Falsy-but-no-exception (empty AI response): the 60s
                    # timeout will surface failed/retry — log so the
                    # cause is findable (review finding: parity with the
                    # exception path).
                    logger.warning(
                        "Prep brief generation returned empty for "
                        "session %d", session_id,
                    )
            except Exception:
                # Logged and DROPPED on purpose: prep_brief stays NULL,
                # so the poll's 60s timeout renders the failed/retry
                # state — the visible error surface for this path.
                logger.exception(
                    "Prep brief generation failed for session %d", session_id,
                )

        threading.Thread(
            target=_generate, args=(session.id, manager.id, stamp),
            daemon=True,
        ).start()

    return _render_prep_brief(request, session)
