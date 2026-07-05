"""Views: Inbox capture triage queue (roadmap PR 4).

Frictionless capture, deferred filing: the quick-add box (sidebar, every
page) and later the email poller drop thoughts here; the /inbox/ page
converts each into the right record with one click.

Triage is double-tap safe: a CAS-style UPDATE ... WHERE status='pending'
claims the item inside a transaction BEFORE the target row is created,
so two overlapping POSTs (the slow-mobile re-tap) can't file it twice.
"""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import (
    ActionItem,
    Decision,
    InboxItem,
    JournalEntry,
    RunningNote,
    TeamMember,
)
from core.services.audit import log_mutation
from core.views._common import _require_manager


def _pending(mid):
    return (
        InboxItem.objects.for_manager(mid)
        .filter(status__in=["pending", "failed"])
        .order_by("-created_at")
    )


def _page_context(manager):
    return {
        "items": _pending(manager.id),
        "members": TeamMember.objects.active_for_manager(manager.id)
        .order_by("name"),
    }


@login_required
def inbox_list(request):
    manager, err = _require_manager(request)
    if err:
        return err
    return render(request, "inbox.html", _page_context(manager))


@login_required
def inbox_badge(request):
    """htmx lazy fragment for the sidebar count — deliberately NOT a
    context processor (a sitewide per-render query for a badge)."""
    manager, err = _require_manager(request)
    if err:
        return err
    count = _pending(manager.id).count()
    return render(request, "_partials/inbox_badge.html", {"count": count})


@login_required
@require_http_methods(["POST"])
def inbox_quick_add(request):
    manager, err = _require_manager(request)
    if err:
        return err
    body = request.POST.get("body", "").strip()
    if not body:
        return render(request, "_partials/inbox_quick_toast.html", {
            "error": "Nothing to capture.",
        }, status=422)
    item = InboxItem.objects.create(
        manager_id=manager.id, source="quick", body=body,
        received_at=timezone.now(),
    )
    log_mutation(manager.id, "create", "InboxItem", item.id,
                 f"Inbox capture: {body[:60]}")
    return render(request, "_partials/inbox_quick_toast.html", {
        "captured": body,
    })


TRIAGE_TARGETS = {"journal", "todo", "note", "decision", "dismiss"}


def _text(item):
    """Subject + body joined for filing into a single text field."""
    if item.subject:
        return f"{item.subject}\n\n{item.body}".strip()
    return item.body


@login_required
@require_http_methods(["POST"])
def inbox_triage(request, item_id: int):
    """One-click convert: target= journal|todo|note|decision|dismiss.
    Returns the refreshed rows partial (htmx swaps the list)."""
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    target = request.POST.get("target", "")

    # Validate BEFORE claiming: an invalid action must never take the
    # item out of the queue.
    if target not in TRIAGE_TARGETS:
        return render(request, "_partials/inbox_rows.html",
                      _page_context(manager), status=422)

    # Resolve the note's member before opening the transaction (its own
    # tenant-scoped read).
    member = None
    if target == "note":
        member_id = request.POST.get("member", "")
        if member_id:
            member = (
                TeamMember.objects.active_for_manager(mid)
                .filter(pk=member_id)
                .first()
            )

    # Claim + create + stamp in ONE transaction: if the target-row
    # create raises, the CAS claim rolls back too, so the item returns
    # to the queue (visible, retryable) instead of a stuck "triaged"
    # row with no target. audit_entity is set only on the create path.
    audit = None
    with transaction.atomic():
        claimed = (
            InboxItem.objects.for_manager(mid)
            .filter(pk=item_id, status__in=["pending", "failed"])
            .update(status="triaged" if target != "dismiss" else "dismissed")
        )
        if claimed != 1:
            # Already triaged/dismissed (double tap) or not yours —
            # idempotent no-op from the UI's view.
            return render(request, "_partials/inbox_rows.html",
                          _page_context(manager))
        item = InboxItem.objects.for_manager(mid).get(pk=item_id)

        if target == "dismiss":
            log_mutation(mid, "update", "InboxItem", item.id,
                         f"Inbox dismissed: {item.body[:60]}")
            return render(request, "_partials/inbox_rows.html",
                          _page_context(manager))

        text = _text(item)
        today = date.today().isoformat()
        if target == "journal":
            created = JournalEntry.objects.create(
                manager_id=mid, entry_date=today, content=text,
                created_at=timezone.now(),
            )
            entity = "JournalEntry"
        elif target == "todo":
            created = ActionItem.objects.create(
                manager_id=mid, description=text, status="pending",
                created_at=timezone.now(),
            )
            entity = "ActionItem"
        elif target == "note":
            created = RunningNote.objects.create(
                manager_id=mid, team_member=member, note_date=today,
                content=text, created_at=timezone.now(),
            )
            entity = "RunningNote"
        else:  # decision
            title = (item.subject or item.body.splitlines()[0])[:80]
            created = Decision.objects.create(
                manager_id=mid, title=title, context=item.body,
                status="active", created_at=timezone.now(),
            )
            entity = "Decision"

        item.triaged_entity_type = entity
        item.triaged_entity_id = created.id
        item.save(update_fields=["triaged_entity_type", "triaged_entity_id"])
        audit = (entity, created.id, text[:60])

    # Audit outside the txn (log_mutation is a separate write; the
    # triage itself is already durably committed).
    log_mutation(mid, "create", audit[0], audit[1],
                 f"Triaged from inbox: {audit[2]}")
    return render(request, "_partials/inbox_rows.html",
                  _page_context(manager))
