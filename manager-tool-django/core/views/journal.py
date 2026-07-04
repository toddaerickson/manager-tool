"""Views: journal."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import (
    JournalEntry,
)
from core.forms import (
    JournalEntryForm,
)
from core.services.audit import log_mutation
from core.services.journal import journal_streak as _journal_streak
from core.views._common import _require_manager

import logging as _logging
_logger = _logging.getLogger(__name__)

# ============================================================
# Phase 5.4 — Journal entries
# ============================================================

_MOOD_EMOJI = {1: "\U0001f62b", 2: "\U0001f614", 3: "\U0001f610", 4: "\U0001f60a", 5: "\U0001f525"}
_ENERGY_EMOJI = {1: "\U0001f62a", 2: "\U0001f615", 3: "\U0001f610", 4: "\U0001f4aa", 5: "\u26a1"}


@login_required
def journal_list(request):
    """Journal main page: today's entry form (pre-filled if exists) plus
    recent history. Mirrors Streamlit's page_journal Today + History tabs
    but in a single page — the form is always visible at top, history
    scrolls below.

    If an entry already exists for today, the form pre-fills so the user
    can edit in-place (update on save, not duplicate)."""
    manager, err = _require_manager(request)
    if err:
        return err
    today_iso = date.today().isoformat()
    existing = (
        JournalEntry.objects.for_manager(manager.id)
        .filter(entry_date=today_iso, entry_type="daily")
        .first()
    )
    if existing:
        form = JournalEntryForm(instance=existing)
        # Re-populate the date field as a date object for the widget.
        form.initial["entry_date"] = date.fromisoformat(existing.entry_date)
        if existing.mood is not None:
            form.initial["mood"] = str(existing.mood)
        if existing.energy is not None:
            form.initial["energy"] = str(existing.energy)
    else:
        form = JournalEntryForm(initial={
            "entry_date": date.today(),
            "entry_type": "daily",
        })
    entries = (
        JournalEntry.objects.for_manager(manager.id)
        .order_by("-entry_date", "-created_at")[:30]
    )
    # Compute journal streak (consecutive days ending today).
    streak = _journal_streak(manager.id, today_iso)
    return render(request, "journal.html", {
        "form": form,
        "entries": entries,
        "today_iso": today_iso,
        "existing_id": existing.id if existing else None,
        "streak": streak,
        "mood_emoji": _MOOD_EMOJI,
        "energy_emoji": _ENERGY_EMOJI,
    })



@login_required
@require_http_methods(["POST"])
def journal_add(request):
    """HTMX endpoint: create or update a journal entry. If an entry_id
    is passed (hidden field), update that entry instead of creating.

    On success, returns the updated history list + cleared/refreshed form
    via OOB swap. On error, returns the form with validation errors."""
    manager, err = _require_manager(request)
    if err:
        return err
    existing_id = request.POST.get("existing_id")
    instance = None
    if existing_id:
        instance = (
            JournalEntry.objects.for_manager(manager.id)
            .filter(pk=existing_id)
            .first()
        )
    form = JournalEntryForm(request.POST, instance=instance)
    if not form.is_valid():
        return render(request, "_partials/journal_form.html", {
            "form": form,
            "existing_id": existing_id,
        }, status=422)
    entry = form.save(commit=False)
    entry.manager_id = manager.id
    from django.utils import timezone
    if not instance:
        entry.created_at = timezone.now()
    entry.updated_at = timezone.now()
    entry.save()
    action = "update" if instance else "create"
    log_mutation(manager.id, action, "JournalEntry", entry.id,
                 f"Journal ({entry.entry_type}): {(entry.content or '')[:60]}")
    # Generate coaching response in a background thread so the
    # save returns instantly (finding #1 from /review-as).
    # COACHING_ENABLED is False under settings_test: the daemon thread
    # opens its own connection to the shared in-memory test DB and its
    # writes commit outside the test transaction, so a spawned thread can
    # leak a CoachingResponse row past rollback into an unrelated test.
    # Disabling the spawn keeps tests isolated; the generation logic is
    # covered directly by coaching/tests.py.
    from django.conf import settings
    if (entry.content and entry.content.strip()
            and getattr(settings, "COACHING_ENABLED", True)):
        import threading

        def _generate_coaching(entry_id, manager_id, content, entry_type):
            try:
                from coaching.services import get_coaching_response
                coaching = get_coaching_response(
                    content, manager_id,
                    context_type=entry_type or "journal",
                )
                if coaching:
                    JournalEntry.objects.filter(pk=entry_id).update(
                        coaching_response=coaching,
                    )
                    # actor="system": this runs in a background thread
                    # after the user's request returns, so it's a
                    # Claude-driven write, not an operator action.
                    log_mutation(manager_id, "create", "CoachingResponse",
                                entry_id, f"AI coaching for journal entry {entry_id}",
                                actor="system")
            except Exception:
                _logger.exception("Coaching generation failed for entry %d", entry_id)

        t = threading.Thread(
            target=_generate_coaching,
            args=(entry.id, manager.id, entry.content, entry.entry_type),
            daemon=True,
        )
        t.start()
    # Return a fresh empty form + refreshed history via OOB. The form
    # always resets after save so the user gets a clean slate; subsequent
    # submits create new entries. In-place editing is available via the
    # Edit link on each entry in the Recent entries list.
    today_iso = date.today().isoformat()
    new_form = JournalEntryForm(initial={
        "entry_date": date.today(),
        "entry_type": "daily",
    })
    entries = (
        JournalEntry.objects.for_manager(manager.id)
        .order_by("-entry_date", "-created_at")[:30]
    )
    return render(request, "_partials/journal_list_after_add.html", {
        "form": new_form,
        "entries": entries,
        "today_iso": today_iso,
        "existing_id": None,
        "just_saved_id": entry.id,
        "streak": _journal_streak(manager.id, today_iso),
        "mood_emoji": _MOOD_EMOJI,
        "energy_emoji": _ENERGY_EMOJI,
    })


@login_required
def journal_coaching(request, entry_id: int):
    """HTMX polling endpoint: returns the coaching response partial
    once the background generation has populated it, or a pending
    placeholder that triggers itself again every 2 seconds.

    Polling stops automatically when the swapped-in fragment lacks
    hx-trigger (the 'ready' partial)."""
    manager, err = _require_manager(request)
    if err:
        return err
    entry = (
        JournalEntry.objects.for_manager(manager.id)
        .filter(pk=entry_id)
        .first()
    )
    if not entry:
        return HttpResponse(status=404)
    if entry.coaching_response:
        return render(request, "_partials/journal_coaching_ready.html", {
            "coaching_response": entry.coaching_response,
        })
    return render(request, "_partials/journal_coaching_pending.html", {
        "entry": entry,
    })


@login_required
def journal_edit(request, entry_id: int):
    """Edit a past journal entry. GET shows the form pre-filled;
    POST saves and redirects back to /journal/."""
    manager, err = _require_manager(request)
    if err:
        return err
    entry = get_object_or_404(
        JournalEntry.objects.for_manager(manager.id),
        pk=entry_id,
    )
    if request.method == "POST":
        form = JournalEntryForm(request.POST, instance=entry)
        if form.is_valid():
            from django.utils import timezone
            obj = form.save(commit=False)
            obj.updated_at = timezone.now()
            obj.save()
            return redirect("journal")
    else:
        form = JournalEntryForm(instance=entry)
        form.initial["entry_date"] = date.fromisoformat(entry.entry_date)
        if entry.mood is not None:
            form.initial["mood"] = str(entry.mood)
        if entry.energy is not None:
            form.initial["energy"] = str(entry.energy)
    return render(request, "journal_edit.html", {"form": form, "entry": entry})


