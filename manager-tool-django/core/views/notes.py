"""Views: notes."""


from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    RunningNote, TeamMember,
)
from core.forms import (
    RunningNoteForm,
)
from core.services.audit import log_mutation
from core.views._common import _parse_member_filter, _require_manager

# ============================================================
# Phase 5.6 — Running Notes (1:1 Notes)
# ============================================================

_NOTE_CATEGORY_LABELS = {
    "general": "General", "meeting_prep": "Meeting prep",
    "observation": "Observation", "follow_up": "Follow-up",
    # "praise" label retained for legacy rows: migration 0010 converted
    # most existing praise running-notes to Feedback, but broadcast
    # praise (team_member NULL) was preserved as-is. New praise rows
    # are blocked at the form layer (see NOTE_CATEGORY_CHOICES in
    # core/forms.py); this label keeps any pre-migration rows readable.
    "praise": "Praise",
}


@login_required
def notes_list(request):
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    member_id = _parse_member_filter(request)
    notes = RunningNote.objects.for_manager(mid).select_related("team_member")
    if member_id:
        # Show member's notes + broadcast notes
        from django.db.models import Q
        notes = notes.filter(Q(team_member_id=member_id) | Q(team_member__isnull=True))
    notes = notes.order_by("-note_date", "-created_at")[:50]
    members = TeamMember.objects.active_for_manager(mid).order_by("name")
    return render(request, "notes.html", {
        "notes": notes,
        "form": RunningNoteForm(manager_id=mid),
        "members": members,
        "selected_member": member_id,
        "category_labels": _NOTE_CATEGORY_LABELS,
    })


@login_required
@require_http_methods(["POST"])
def notes_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    form = RunningNoteForm(request.POST, manager_id=manager.id)
    if not form.is_valid():
        return render(request, "_partials/note_form.html", {
            "form": form,
        }, status=422)
    note = form.save(commit=False)
    note.manager_id = manager.id
    from django.utils import timezone
    note.created_at = timezone.now()
    note.save()
    log_mutation(manager.id, "create", "RunningNote", note.id,
                 f"Note: {note.content[:60]}")
    return redirect("notes")


@login_required
@require_http_methods(["DELETE"])
def notes_delete(request, note_id: int):
    manager, err = _require_manager(request)
    if err:
        return err
    deleted, _ = RunningNote.objects.for_manager(manager.id).filter(pk=note_id).delete()
    if deleted == 0:
        return HttpResponse(status=404)
    log_mutation(manager.id, "delete", "RunningNote", note_id,
                 "Deleted running note")
    return HttpResponse(status=200)


