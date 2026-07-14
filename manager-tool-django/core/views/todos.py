"""Views: todos."""

from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import (
    ActionItem,
    ActionItemManager,
    Delegation,
    TeamMember,
)
from core.forms import (
    ActionItemForm,
)
from core.services.audit import log_mutation
from core.views._common import _require_manager

# ============================================================
# Phase 5.3 — Action Items / "To Do"
# ============================================================


def _show_delegated(request) -> bool:
    """The "Show Delegated?" header checkbox state. Threaded through
    every HTMX endpoint as a `?delegated=1` query param so list
    rebuilds keep rendering the merged view."""
    return request.GET.get("delegated") == "1"


def _pending_rows(manager_id: int, show_delegated: bool) -> list:
    """Normalized row dicts for the pending table.

    Always the manager's own pending to-dos; with show_delegated, active
    Delegations are merged in (due = check-in date) so the one list
    shows everything in flight. Sorted in Python — dated items first in
    date order — because SQL NULL ordering differs between SQLite and
    PG.
    """
    today_iso = date.today().isoformat()
    flag_qs = "?delegated=1" if show_delegated else ""
    rows = []
    pending = (
        ActionItem.objects.active_for_manager(manager_id)
        .filter(status="pending")
    )
    for t in pending:
        rows.append({
            "kind": "todo",
            "id": t.id,
            "description": t.description,
            "due_date": t.due_date,
            "due_time": t.due_time,
            "overdue": bool(t.due_date and t.due_date < today_iso),
            "member_name": None,
            "edit_url": reverse("todos-edit", args=[t.id]) + flag_qs,
        })
    if show_delegated:
        delegations = (
            Delegation.objects.for_manager(manager_id)
            .filter(status="active")
            .select_related("team_member")
        )
        for d in delegations:
            rows.append({
                "kind": "delegation",
                "id": d.id,
                "description": d.task,
                "due_date": d.check_in_date,
                "due_time": None,
                "overdue": bool(d.check_in_date and d.check_in_date < today_iso),
                "member_name": d.team_member.name if d.team_member else "—",
                "edit_url": reverse("delegations-edit", args=[d.id]),
            })
    rows.sort(key=lambda r: (
        r["due_date"] is None, r["due_date"] or "",
        r["due_time"] is None, r["due_time"] or "",
        r["id"],
    ))
    return rows


def _completed(manager_id: int):
    return (
        ActionItem.objects.active_for_manager(manager_id)
        .filter(status="completed")
        .order_by("-completed_at")[:20]
    )


def _recently_deleted(manager_id: int):
    return ActionItem.objects.recently_deleted_for_manager(manager_id)


def _list_context(manager_id: int, show_delegated: bool) -> dict:
    return {
        "rows": _pending_rows(manager_id, show_delegated),
        "show_delegated": show_delegated,
    }


@login_required
def todos_list(request):
    """Pending to-dos (+ optionally active delegations), a collapsible
    Recently completed section, and a Recently deleted section (1-day
    undo window)."""
    manager, err = _require_manager(request)
    if err:
        return err
    # Opportunistic purge: hard-delete rows whose 1-day undo window has
    # expired. Mirrors the purge_deleted_team_members cron, including the
    # per-row actor="system" audit entry — the loop only runs when a row
    # actually expired, so the page-load cost is one usually-empty query.
    cutoff = timezone.now() - timedelta(days=ActionItemManager.UNDO_WINDOW_DAYS)
    expired = ActionItem.objects.for_manager(manager.id).filter(
        deleted_at__lt=cutoff,
    )
    for t in expired:
        log_mutation(manager.id, "delete", "ActionItem", t.id,
                     f"Purged expired deleted to-do: {t.description[:60]}",
                     actor="system")
    expired.delete()
    show_delegated = _show_delegated(request)
    return render(request, "todos.html", {
        **_list_context(manager.id, show_delegated),
        "today_iso": date.today().isoformat(),
        "completed": _completed(manager.id),
        "deleted": _recently_deleted(manager.id),
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
    show_delegated = _show_delegated(request)
    form = ActionItemForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/todo_form.html", {
            "form": form,
            "show_delegated": show_delegated,
        }, status=422)
    item = form.save(commit=False)
    item.manager_id = manager.id
    item.status = "pending"
    item.created_at = timezone.now()
    item.save()
    return render(request, "_partials/todo_list_after_add.html", {
        **_list_context(manager.id, show_delegated),
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
    updated = (
        ActionItem.objects.active_for_manager(manager.id)
        .filter(pk=todo_id, status="pending")
        .update(status="completed", completed_at=timezone.now())
    )
    if updated == 0:
        return HttpResponse(status=404)
    return render(request, "_partials/todo_row_completed.html", {
        "completed": _completed(manager.id),
        "show_delegated": _show_delegated(request),
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
        ActionItem.objects.active_for_manager(manager.id)
        .filter(pk=todo_id, status="completed")
        .update(status="pending", completed_at=None)
    )
    if updated == 0:
        return HttpResponse(status=404)
    return render(request, "_partials/todo_row_uncompleted.html", {
        **_list_context(manager.id, _show_delegated(request)),
        "completed": _completed(manager.id),
    })


@login_required
@require_http_methods(["DELETE"])
def todos_delete(request, todo_id: int):
    """HTMX: soft-delete (deleted_at=now). The row swaps out of the
    pending list; the Recently deleted section refreshes via oob and
    offers Restore for 1 day, after which the purge in todos_list
    hard-deletes the row."""
    manager, err = _require_manager(request)
    if err:
        return err
    updated = (
        ActionItem.objects.active_for_manager(manager.id)
        .filter(pk=todo_id)
        .update(deleted_at=timezone.now())
    )
    if updated == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "delete", "ActionItem", todo_id,
                 "Deleted to-do (recoverable for 1 day)")
    return render(request, "_partials/todo_row_deleted.html", {
        "deleted": _recently_deleted(manager.id),
        "show_delegated": _show_delegated(request),
    })


@login_required
@require_http_methods(["POST"])
def todos_restore(request, todo_id: int):
    """HTMX: clear deleted_at within the 1-day undo window. Returns the
    updated Recently deleted list + oob pending list."""
    manager, err = _require_manager(request)
    if err:
        return err
    updated = (
        ActionItem.objects.recently_deleted_for_manager(manager.id)
        .filter(pk=todo_id)
        .update(deleted_at=None)
    )
    if updated == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "update", "ActionItem", todo_id,
                 "Restored deleted to-do")
    return render(request, "_partials/todo_row_restored.html", {
        **_list_context(manager.id, _show_delegated(request)),
        "deleted": _recently_deleted(manager.id),
    })


@login_required
def todos_edit(request, todo_id: int):
    """Full-entry page — clicking a row (or its Edit icon) lands here."""
    manager, err = _require_manager(request)
    if err:
        return err
    show_delegated = _show_delegated(request)
    flag_qs = "?delegated=1" if show_delegated else ""
    t = get_object_or_404(
        ActionItem.objects.active_for_manager(manager.id),
        pk=todo_id,
    )
    if request.method == "POST":
        form = ActionItemForm(request.POST, instance=t, manager_id=manager.id)
        if form.is_valid():
            obj = form.save()
            log_mutation(manager.id, "update", "ActionItem", obj.id,
                         f"Updated to-do: {obj.description[:60]}")
            return redirect(reverse("todos") + flag_qs)
    else:
        form = ActionItemForm(instance=t, manager_id=manager.id)
        if t.due_date:
            form.initial["due_date"] = date.fromisoformat(t.due_date)
    return render(request, "todos_edit.html", {
        "form": form, "todo": t, "show_delegated": show_delegated,
    })


@login_required
@require_http_methods(["GET", "POST"])
def todos_delegate(request, todo_id: int):
    """Promote a to-do into a Delegation.

    GET returns an inline member-picker row (HTMX, inserted after the
    to-do's row). POST creates the Delegation (task = description,
    check-in = due date) and removes the to-do atomically — the work
    now lives in Delegations, single source of truth — then returns the
    rebuilt pending list.
    """
    manager, err = _require_manager(request)
    if err:
        return err
    show_delegated = _show_delegated(request)
    t = get_object_or_404(
        ActionItem.objects.active_for_manager(manager.id),
        pk=todo_id, status="pending",
    )
    if request.method == "GET":
        members = TeamMember.objects.active_for_manager(manager.id).order_by("name")
        return render(request, "_partials/todo_delegate_row.html", {
            "t": t,
            "members": members,
            "show_delegated": show_delegated,
        })
    try:
        member_id = int(request.POST.get("team_member_id") or "")
    except ValueError:
        return HttpResponse(status=400)
    member = get_object_or_404(
        TeamMember.objects.active_for_manager(manager.id),
        pk=member_id,
    )
    with transaction.atomic():
        d = Delegation.objects.create(
            manager_id=manager.id,
            team_member=member,
            task=t.description,
            autonomy_level="guided",
            check_in_date=t.due_date,
            status="active",
            notes="Promoted from To Do",
            created_at=timezone.now(),
        )
        t.delete()
    log_mutation(manager.id, "create", "Delegation", d.id,
                 f"Promoted to-do to delegation for {member.name}: {d.task[:60]}")
    return render(request, "_partials/todo_list.html",
                  _list_context(manager.id, show_delegated))
