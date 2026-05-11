"""Views: todos."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from core.models import (
    ActionItem,
)
from core.forms import (
    ActionItemForm,
)
from core.views._common import _require_manager

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


