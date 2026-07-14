"""Views: unified cross-model search (roadmap PR 3).

One page answering "where did I write that down?" — case-insensitive
substring search across every content-bearing model, grouped by type,
capped per model. Hits deep-link to the item's edit/detail page where
one exists (meetings, journal, decisions, delegations, goals, events);
models without per-item routes (feedback, to-dos, career, team, notes)
link to their list page.

icontains on purpose: PG SearchVector would break the SQLite test
suite, and at single-user scale (thousands of rows) full scans are
milliseconds. Every queryset goes through for_manager (directly, or
via active_for_manager for soft-delete-aware TeamMember) — tenant
isolation is non-negotiable here because this view touches every
model at once.
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse

from core.models import (
    ActionItem,
    CareerConversation,
    Decision,
    Delegation,
    Event,
    Feedback,
    Goal,
    JournalEntry,
    OneOnOneSession,
    RunningNote,
    TeamMember,
)
from core.views._common import _require_manager

PER_MODEL_CAP = 20
MIN_QUERY_LEN = 2
SNIPPET_WIDTH = 160


def _snippet(q, *fields):
    """A ~160-char excerpt from the first field that matches q (window
    opens ~a third before the match so trailing context dominates),
    else the first non-empty field, truncated."""
    fields = [f for f in fields if f]
    if not fields:
        return ""
    text = fields[0]
    for f in fields:
        if q.lower() in f.lower():
            text = f
            break
    i = text.lower().find(q.lower())
    if i < 0:
        return text[:SNIPPET_WIDTH] + ("…" if len(text) > SNIPPET_WIDTH else "")
    start = max(0, i - SNIPPET_WIDTH // 3)
    end = start + SNIPPET_WIDTH
    return (
        ("…" if start > 0 else "")
        + text[start:end]
        + ("…" if end < len(text) else "")
    )


@login_required
def search_page(request):
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    q = request.GET.get("q", "").strip()

    groups = []
    if len(q) >= MIN_QUERY_LEN:
        groups = [g for g in _build_groups(mid, q) if g["items"]]

    total = sum(len(g["items"]) for g in groups)
    return render(request, "search.html", {
        "q": q,
        "groups": groups,
        "total": total,
        "min_len": MIN_QUERY_LEN,
        "cap": PER_MODEL_CAP,
    })


def _build_groups(mid, q):
    """One group dict per model: {label, items:[{title, snippet, url,
    date}]}. Newest rows first (-id: every table's ids are insert-
    ordered; avoids depending on per-model date column shapes)."""

    def hits(qs):
        return list(qs.order_by("-id")[:PER_MODEL_CAP])

    groups = []

    sessions = hits(
        OneOnOneSession.objects.for_manager(mid)
        .select_related("team_member")
        .filter(
            Q(direct_notes__icontains=q) | Q(manager_notes__icontains=q)
            | Q(followup_notes__icontains=q) | Q(tags__icontains=q)
        )
    )
    groups.append({"label": "Meetings", "items": [{
        "title": f"1:1 with {s.team_member.name}" if s.team_member else "1:1",
        "snippet": _snippet(q, s.direct_notes, s.manager_notes,
                            s.followup_notes, s.tags),
        "url": reverse("meetings-detail", args=[s.id]),
        "date": s.session_date,
    } for s in sessions]})

    entries = hits(
        JournalEntry.objects.for_manager(mid).filter(
            Q(content__icontains=q) | Q(private_notes__icontains=q)
            | Q(tags__icontains=q)
        )
    )
    groups.append({"label": "Journal", "items": [{
        "title": f"Journal — {e.entry_date}",
        "snippet": _snippet(q, e.content, e.private_notes, e.tags),
        "url": reverse("journal-edit", args=[e.id]),
        "date": e.entry_date,
    } for e in entries]})

    notes = hits(
        RunningNote.objects.for_manager(mid)
        .select_related("team_member")
        .filter(Q(content__icontains=q) | Q(category__icontains=q))
    )
    groups.append({"label": "Notes", "items": [{
        "title": (f"Note — {n.team_member.name}" if n.team_member
                  else "Note — Broadcast"),
        "snippet": _snippet(q, n.content, n.category),
        "url": reverse("notes") + (
            f"?member={n.team_member_id}" if n.team_member_id else ""
        ),
        "date": n.note_date,
    } for n in notes]})

    decisions = hits(
        Decision.objects.for_manager(mid).filter(
            Q(title__icontains=q) | Q(context__icontains=q)
            | Q(alternatives__icontains=q) | Q(rationale__icontains=q)
            | Q(expected_outcome__icontains=q)
            | Q(actual_outcome__icontains=q)
        )
    )
    groups.append({"label": "Decisions", "items": [{
        "title": d.title or "Decision",
        "snippet": _snippet(q, d.context, d.rationale, d.alternatives,
                            d.expected_outcome, d.actual_outcome, d.title),
        "url": reverse("decisions-edit", args=[d.id]),
        "date": d.review_date,
    } for d in decisions]})

    feedback = hits(
        Feedback.objects.for_manager(mid)
        .select_related("team_member")
        .filter(
            Q(situation__icontains=q) | Q(behavior__icontains=q)
            | Q(impact__icontains=q)
        )
    )
    groups.append({"label": "Feedback", "items": [{
        "title": (f"{(f.feedback_type or 'Feedback').title()} — "
                  f"{f.team_member.name}" if f.team_member
                  else (f.feedback_type or "Feedback").title()),
        "snippet": _snippet(q, f.situation, f.behavior, f.impact),
        "url": reverse("feedback"),
        "date": None,
    } for f in feedback]})

    delegations = hits(
        Delegation.objects.for_manager(mid)
        .select_related("team_member")
        .filter(
            Q(task__icontains=q) | Q(outcome_expected__icontains=q)
            | Q(notes__icontains=q)
        )
    )
    groups.append({"label": "Delegations", "items": [{
        "title": (f"{(d.task or 'Delegation')[:60]} — {d.team_member.name}"
                  if d.team_member else (d.task or "Delegation")[:60]),
        "snippet": _snippet(q, d.task, d.outcome_expected, d.notes),
        "url": reverse("delegations-edit", args=[d.id]),
        "date": d.check_in_date,
    } for d in delegations]})

    todos = hits(
        ActionItem.objects.active_for_manager(mid).filter(
            Q(description__icontains=q) | Q(assignee__icontains=q)
        )
    )
    groups.append({"label": "To Do", "items": [{
        "title": (t.description or "To-do")[:80],
        "snippet": _snippet(q, t.description, t.assignee),
        "url": reverse("todos"),
        "date": t.due_date,
    } for t in todos]})

    goals = hits(
        Goal.objects.for_manager(mid)
        .select_related("team_member")
        .filter(
            Q(description__icontains=q) | Q(key_results__icontains=q)
            | Q(quarter__icontains=q)
        )
    )
    groups.append({"label": "Goals", "items": [{
        "title": (f"{g.quarter or 'Goal'} — {g.team_member.name}"
                  if g.team_member else (g.quarter or "Goal")),
        "snippet": _snippet(q, g.description, g.key_results, g.quarter),
        "url": reverse("goals-edit", args=[g.id]),
        "date": g.target_date,
    } for g in goals]})

    convos = hits(
        CareerConversation.objects.for_manager(mid)
        .select_related("team_member")
        .filter(
            Q(topic__icontains=q) | Q(notes__icontains=q)
            | Q(next_steps__icontains=q)
        )
    )
    groups.append({"label": "Career", "items": [{
        "title": (f"{c.topic or 'Conversation'} — {c.team_member.name}"
                  if c.team_member else (c.topic or "Conversation")),
        "snippet": _snippet(q, c.notes, c.next_steps, c.topic),
        "url": reverse("career-dev"),
        "date": c.conversation_date,
    } for c in convos]})

    members = hits(
        TeamMember.objects.active_for_manager(mid).filter(
            Q(name__icontains=q) | Q(role__icontains=q)
            | Q(notes__icontains=q) | Q(email__icontains=q)
        )
    )
    groups.append({"label": "Team", "items": [{
        "title": m.name,
        "snippet": _snippet(q, m.role, m.notes, m.email, m.name),
        "url": reverse("team"),
        "date": m.start_date,
    } for m in members]})

    events = hits(
        Event.objects.for_manager(mid).filter(
            Q(title__icontains=q) | Q(agenda__icontains=q)
            | Q(notes__icontains=q) | Q(location__icontains=q)
        )
    )
    groups.append({"label": "Events", "items": [{
        "title": e.title or "Event",
        "snippet": _snippet(q, e.agenda, e.notes, e.location, e.title),
        "url": reverse("events-detail", args=[e.id]),
        "date": e.scheduled_date,
    } for e in events]})

    return groups
