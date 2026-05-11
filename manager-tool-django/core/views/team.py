"""Views: team."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from core.models import (
    TeamMember,
)
from core.forms import (
    TeamMemberForm,
)
from core.services.audit import log_mutation
from core.views._common import _require_manager

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
    log_mutation(manager.id, "create", "TeamMember", member.id,
                 f"Added team member: {member.name}")
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
    log_mutation(manager.id, "delete", "TeamMember", member_id,
                 "Soft-deleted team member")
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


