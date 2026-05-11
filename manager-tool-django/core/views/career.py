"""Views: career."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    CareerConversation, DevelopmentPlan, Milestone, Skill, TeamMember,
)
from core.forms import (
    CareerConversationForm, DevelopmentPlanForm, MilestoneForm, SkillForm,
)
from core.services.audit import log_mutation
from core.views._common import _parse_member_filter, _require_manager

# ============================================================
# Phase 5.5 — Career Development (Skills, Dev Plans, Conversations)
# ============================================================


def _career_dev_partial(request, manager):
    """Build context and return the career dev content partial (D4)."""
    mid = manager.id
    skills = Skill.objects.for_manager(mid).select_related("team_member").order_by("team_member__name", "skill_name")
    plans = DevelopmentPlan.objects.for_manager(mid).select_related("team_member").order_by("-created_at")
    convos = CareerConversation.objects.for_manager(mid).select_related("team_member").order_by("-conversation_date")[:20]
    plans = list(plans)
    plan_ids = [p.id for p in plans]
    milestones_by_plan = {}
    if plan_ids:
        for ms in Milestone.objects.for_manager(mid).filter(plan_id__in=plan_ids).order_by("id"):
            milestones_by_plan.setdefault(ms.plan_id, []).append(ms)
    for p in plans:
        p.milestones_list = milestones_by_plan.get(p.id, [])
    return render(request, "_partials/career_dev_content.html", {
        "skills": skills,
        "plans": plans,
        "convos": convos,
        "skill_form": SkillForm(manager_id=mid),
        "plan_form": DevelopmentPlanForm(manager_id=mid),
        "convo_form": CareerConversationForm(manager_id=mid),
    })


@login_required
def career_dev(request):
    """Career development page — skills, development plans with
    milestones, and career conversations. All scoped to the selected
    team member (or all members if none selected)."""
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    member_id = _parse_member_filter(request)
    members = TeamMember.objects.active_for_manager(mid).order_by("name")

    skills = Skill.objects.for_manager(mid).select_related("team_member")
    plans = DevelopmentPlan.objects.for_manager(mid).select_related("team_member")
    convos = CareerConversation.objects.for_manager(mid).select_related("team_member")
    if member_id:
        skills = skills.filter(team_member_id=member_id)
        plans = plans.filter(team_member_id=member_id)
        convos = convos.filter(team_member_id=member_id)
    skills = skills.order_by("team_member__name", "skill_name")
    plans = plans.order_by("-created_at")
    convos = convos.order_by("-conversation_date")[:20]

    # Batch-load milestones for visible plans (avoids N+1).
    # Attach directly to plan objects so the template does a simple
    # {% for ms in p.milestones_list %} without O(plans*milestones).
    plans = list(plans)
    plan_ids = [p.id for p in plans]
    milestones_by_plan = {}
    if plan_ids:
        for ms in Milestone.objects.for_manager(mid).filter(plan_id__in=plan_ids).order_by("id"):
            milestones_by_plan.setdefault(ms.plan_id, []).append(ms)
    for p in plans:
        p.milestones_list = milestones_by_plan.get(p.id, [])

    return render(request, "career_dev.html", {
        "skills": skills,
        "plans": plans,
        "convos": convos,
        "members": members,
        "selected_member": member_id,
        "skill_form": SkillForm(manager_id=mid),
        "plan_form": DevelopmentPlanForm(manager_id=mid),
        "convo_form": CareerConversationForm(manager_id=mid),
    })


@login_required
@require_http_methods(["POST"])
def skills_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = SkillForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/skill_form.html", {
            "skill_form": form,
        }, status=422)
    skill = form.save(commit=False)
    skill.manager_id = manager.id
    from django.utils import timezone
    skill.created_at = timezone.now()
    skill.save()
    log_mutation(manager.id, "create", "Skill", skill.id,
                 f"Skill for {skill.team_member.name}: {skill.skill_name}")
    return _career_dev_partial(request, manager)


@login_required
@require_http_methods(["DELETE"])
def skills_delete(request, skill_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = Skill.objects.for_manager(manager.id).filter(pk=skill_id).delete()
    if deleted == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "delete", "Skill", skill_id, "Deleted skill")
    return _career_dev_partial(request, manager)


@login_required
@require_http_methods(["POST"])
def plans_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = DevelopmentPlanForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/plan_form.html", {
            "plan_form": form,
        }, status=422)
    plan = form.save(commit=False)
    plan.manager_id = manager.id
    from django.utils import timezone
    plan.created_at = timezone.now()
    plan.save()
    log_mutation(manager.id, "create", "DevelopmentPlan", plan.id,
                 f"Plan for {plan.team_member.name}: {plan.title[:60]}")
    return _career_dev_partial(request, manager)


@login_required
@require_http_methods(["POST"])
def plans_update_status(request, plan_id: int):
    """HTMX: update plan status (active/completed/paused)."""
    manager, err = _require_manager(request)
    if err:
        return err
    new_status = request.POST.get("status")
    if new_status not in ("active", "completed", "paused"):
        return HttpResponse(status=400)
    from django.utils import timezone
    updated = (
        DevelopmentPlan.objects.for_manager(manager.id)
        .filter(pk=plan_id)
        .update(status=new_status, updated_at=timezone.now())
    )
    if updated == 0:
        return HttpResponse(status=404)
    return _career_dev_partial(request, manager)


@login_required
@require_http_methods(["POST"])
def milestones_add(request, plan_id: int):
    """Add a milestone to a development plan."""
    manager, err = _require_manager(request)
    if err:
        return err
    plan = get_object_or_404(
        DevelopmentPlan.objects.for_manager(manager.id), pk=plan_id,
    )
    form = MilestoneForm(request.POST)
    if not form.is_valid():
        return redirect("career-dev")
    ms = form.save(commit=False)
    ms.plan = plan
    ms.manager_id = manager.id
    ms.completed = 0
    ms.save()
    log_mutation(manager.id, "create", "Milestone", ms.id,
                 f"Milestone: {ms.description[:60]}")
    return _career_dev_partial(request, manager)


@login_required
@require_http_methods(["POST"])
def milestones_complete(request, milestone_id: int):
    """Mark a milestone as completed."""
    manager, err = _require_manager(request)
    if err:
        return err
    from django.utils import timezone
    updated = (
        Milestone.objects.for_manager(manager.id)
        .filter(pk=milestone_id, completed=0)
        .update(completed=1, completed_at=timezone.now())
    )
    if updated == 0:
        return HttpResponse(status=404)
    return _career_dev_partial(request, manager)


@login_required
@require_http_methods(["POST"])
def convos_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = CareerConversationForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/convo_form.html", {
            "convo_form": form,
        }, status=422)
    convo = form.save(commit=False)
    convo.manager_id = manager.id
    from django.utils import timezone
    convo.created_at = timezone.now()
    convo.save()
    log_mutation(manager.id, "create", "CareerConversation", convo.id,
                 f"Career convo with {convo.team_member.name}")
    return _career_dev_partial(request, manager)


