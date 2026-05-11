"""Views: decisions."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    Decision,
)
from core.forms import (
    DecisionForm,
)
from core.services.audit import log_mutation
from core.views._common import _require_manager

# ============================================================
# Phase 5.6 — Decisions
# ============================================================

_DECISION_STATUS_LABELS = {
    "active": "Active", "validated": "Validated",
    "revised": "Revised", "reversed": "Reversed",
}


@login_required
def decisions_list(request):
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    today_iso = date.today().isoformat()
    decisions = Decision.objects.for_manager(mid).order_by("-created_at")[:50]
    return render(request, "decisions.html", {
        "decisions": decisions,
        "form": DecisionForm(),
        "today_iso": today_iso,
        "status_labels": _DECISION_STATUS_LABELS,
    })


@login_required
@require_http_methods(["POST"])
def decisions_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = DecisionForm(request.POST)
    if not form.is_valid():
        return render(request, "_partials/decision_form.html", {
            "form": form,
        }, status=422)
    d = form.save(commit=False)
    d.manager_id = manager.id
    if not d.status:
        d.status = "active"
    from django.utils import timezone
    d.created_at = timezone.now()
    d.save()
    log_mutation(manager.id, "create", "Decision", d.id,
                 f"Decision: {d.title[:60]}")
    return redirect("decisions")


@login_required
def decisions_edit(request, decision_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    d = get_object_or_404(
        Decision.objects.for_manager(manager.id), pk=decision_id,
    )
    if request.method == "POST":
        form = DecisionForm(request.POST, instance=d)
        if form.is_valid():
            from django.utils import timezone
            obj = form.save(commit=False)
            obj.updated_at = timezone.now()
            obj.save()
            log_mutation(manager.id, "update", "Decision", obj.id,
                         f"Updated decision: {obj.title[:60]}")
            return redirect("decisions")
    else:
        form = DecisionForm(instance=d)
        if d.review_date:
            form.initial["review_date"] = date.fromisoformat(d.review_date)
    return render(request, "decisions_edit.html", {"form": form, "decision": d})


@login_required
@require_http_methods(["DELETE"])
def decisions_delete(request, decision_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = Decision.objects.for_manager(manager.id).filter(pk=decision_id).delete()
    if deleted == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "delete", "Decision", decision_id,
                 "Deleted decision")
    return HttpResponse(status=200)


