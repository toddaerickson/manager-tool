"""Views: goals."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    Goal, TeamMember,
)
from core.forms import (
    GOAL_STATUS_CHOICES,
    GoalForm,
)
from core.services.audit import log_mutation
from core.views._common import _parse_member_filter, _require_manager

# ============================================================
# Phase 5.5 — Goals
# ============================================================


_GOAL_STATUS_LABELS = dict(GoalForm.declared_fields["status"].choices)


@login_required
def goals_list(request):
    """Goals list with add form. Filterable by team member."""
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    member_id = _parse_member_filter(request)
    goals = Goal.objects.for_manager(mid).select_related("team_member")
    if member_id:
        goals = goals.filter(team_member_id=member_id)
    goals = goals.order_by("-created_at")
    members = TeamMember.objects.active_for_manager(mid).order_by("name")
    return render(request, "goals.html", {
        "goals": goals,
        "form": GoalForm(manager_id=mid),
        "members": members,
        "selected_member": member_id,
        "status_labels": _GOAL_STATUS_LABELS,
    })


@login_required
@require_http_methods(["POST"])
def goals_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = GoalForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/goal_form.html", {
            "form": form,
        }, status=422)
    goal = form.save(commit=False)
    goal.manager_id = manager.id
    from django.utils import timezone
    goal.created_at = timezone.now()
    goal.save()
    log_mutation(manager.id, "create", "Goal", goal.id,
                 f"Goal for {goal.team_member.name}: {goal.description[:60]}")
    goals = Goal.objects.for_manager(manager.id).select_related("team_member").order_by("-created_at")
    return render(request, "_partials/goal_list_after_add.html", {
        "goals": goals,
        "form": GoalForm(manager_id=manager.id),
        "status_labels": _GOAL_STATUS_LABELS,
    })


@login_required
def goals_edit(request, goal_id: int):
    """Edit a goal — GET shows form, POST saves and redirects."""
    manager, err = _require_manager(request)
    if err:
        return err
    goal = get_object_or_404(
        Goal.objects.for_manager(manager.id).select_related("team_member"),
        pk=goal_id,
    )
    if request.method == "POST":
        form = GoalForm(request.POST, instance=goal, manager_id=manager.id)
        if form.is_valid():
            from django.utils import timezone
            obj = form.save(commit=False)
            obj.updated_at = timezone.now()
            obj.save()
            log_mutation(manager.id, "update", "Goal", obj.id,
                         f"Updated goal: {obj.description[:60]}")
            return redirect("goals")
    else:
        form = GoalForm(instance=goal, manager_id=manager.id)
        if goal.target_date:
            form.initial["target_date"] = date.fromisoformat(goal.target_date)
    return render(request, "goals_edit.html", {"form": form, "goal": goal})


@login_required
@require_http_methods(["DELETE"])
def goals_delete(request, goal_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = Goal.objects.for_manager(manager.id).filter(pk=goal_id).delete()
    if deleted == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "delete", "Goal", goal_id, "Deleted goal")
    return HttpResponse(status=200)


@login_required
@require_http_methods(["POST"])
def goal_status(request, goal_id: int):
    """HTMX: quick status change — set a goal's state without opening the
    edit form (UI review follow-up). Re-renders the goal list so the badge
    and quick-status dropdown stay in sync."""
    manager, err = _require_manager(request)
    if err:
        return err
    new_status = request.POST.get("status", "")
    valid = {c for c, _ in GOAL_STATUS_CHOICES}
    if new_status not in valid:
        return HttpResponse(status=400)
    updated = (
        Goal.objects.for_manager(manager.id)
        .filter(pk=goal_id)
        .update(status=new_status)
    )
    if updated == 0:
        return HttpResponse(status=404)

    member_id = _parse_member_filter(request)
    goals = Goal.objects.for_manager(manager.id).select_related("team_member")
    if member_id:
        goals = goals.filter(team_member_id=member_id)
    goals = goals.order_by("-created_at")
    return render(request, "_partials/goal_list.html", {
        "goals": goals,
        "status_labels": _GOAL_STATUS_LABELS,
    })


