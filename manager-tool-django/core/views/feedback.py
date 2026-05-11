"""Views: feedback."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    Feedback, TeamMember,
)
from core.forms import (
    FeedbackForm,
)
from core.services.audit import log_mutation
from core.views._common import _parse_member_filter, _require_manager

# ============================================================
# Phase 5.6b — Feedback
# ============================================================


@login_required
def feedback_list(request):
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    member_id = _parse_member_filter(request)
    fb = Feedback.objects.for_manager(mid).select_related("team_member")
    if member_id:
        fb = fb.filter(team_member_id=member_id)
    fb = fb.order_by("-created_at")[:50]
    members = TeamMember.objects.active_for_manager(mid).order_by("name")
    return render(request, "feedback.html", {
        "feedback_list": fb,
        "form": FeedbackForm(manager_id=mid),
        "members": members,
        "selected_member": member_id,
    })


@login_required
@require_http_methods(["POST"])
def feedback_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = FeedbackForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/feedback_form.html", {
            "form": form,
        }, status=422)
    fb = form.save(commit=False)
    fb.manager_id = manager.id
    from django.utils import timezone
    fb.created_at = timezone.now()
    fb.save()
    log_mutation(manager.id, "create", "Feedback", fb.id,
                 f"{fb.feedback_type} feedback for {fb.team_member.name}")
    return redirect("feedback")


@login_required
@require_http_methods(["DELETE"])
def feedback_delete(request, feedback_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = Feedback.objects.for_manager(manager.id).filter(pk=feedback_id).delete()
    if deleted == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "delete", "Feedback", feedback_id,
                 "Deleted feedback entry")
    return HttpResponse(status=200)


