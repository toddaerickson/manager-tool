"""Views: delegations."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    Delegation, TeamMember,
)
from core.forms import (
    DelegationForm,
)
from core.services.audit import log_mutation
from core.views._common import _parse_member_filter, _require_manager

# ============================================================
# Phase 5.6 — Delegations
# ============================================================

_DELEGATION_STATUS_LABELS = {
    "active": "Active", "completed": "Completed", "stalled": "Stalled",
}


@login_required
def delegations_list(request):
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    today_iso = date.today().isoformat()
    member_id = _parse_member_filter(request)
    delegations = Delegation.objects.for_manager(mid).select_related("team_member")
    if member_id:
        delegations = delegations.filter(team_member_id=member_id)
    active = delegations.filter(status="active").order_by("check_in_date", "id")
    completed = delegations.exclude(status="active").order_by("-completed_at")[:20]
    members = TeamMember.objects.active_for_manager(mid).order_by("name")
    return render(request, "delegations.html", {
        "active": active,
        "completed": completed,
        "form": DelegationForm(manager_id=mid),
        "members": members,
        "selected_member": member_id,
        "today_iso": today_iso,
        "status_labels": _DELEGATION_STATUS_LABELS,
    })


@login_required
@require_http_methods(["POST"])
def delegations_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = DelegationForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/delegation_form.html", {
            "form": form,
        }, status=422)
    d = form.save(commit=False)
    d.manager_id = manager.id
    if not d.status:
        d.status = "active"
    from django.utils import timezone
    d.created_at = timezone.now()
    d.save()
    log_mutation(manager.id, "create", "Delegation", d.id,
                 f"Delegated to {d.team_member.name if d.team_member else '?'}: {d.task[:60]}")
    return redirect("delegations")


@login_required
def delegations_edit(request, delegation_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    d = get_object_or_404(
        Delegation.objects.for_manager(manager.id).select_related("team_member"),
        pk=delegation_id,
    )
    if request.method == "POST":
        form = DelegationForm(request.POST, instance=d, manager_id=manager.id)
        if form.is_valid():
            from django.utils import timezone
            obj = form.save(commit=False)
            if obj.status == "completed" and not obj.completed_at:
                obj.completed_at = timezone.now()
            obj.save()
            log_mutation(manager.id, "update", "Delegation", obj.id,
                         f"Updated delegation: {obj.task[:60]}")
            return redirect("delegations")
    else:
        form = DelegationForm(instance=d, manager_id=manager.id)
        if d.check_in_date:
            form.initial["check_in_date"] = date.fromisoformat(d.check_in_date)
    return render(request, "delegations_edit.html", {"form": form, "delegation": d})


@login_required
@require_http_methods(["DELETE"])
def delegations_delete(request, delegation_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = Delegation.objects.for_manager(manager.id).filter(pk=delegation_id).delete()
    if deleted == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "delete", "Delegation", delegation_id,
                 "Deleted delegation")
    return HttpResponse(status=200)


