"""Views: reference."""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from core.models import (
    ActionItem, AuditLog, Decision, Delegation, Event, Feedback, Goal, JournalEntry, RunningNote, TeamMember,
)
from core.services.journal import journal_streak as _journal_streak
from core.views._common import _require_manager

# ============================================================
# Reference pages — Analytics, History, Audit log, Resources
# ============================================================


@login_required
def analytics(request):
    """Aggregate stats: meeting cadence, feedback ratios, action items,
    goal progress. Server-rendered tables — no JS charts."""
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    today_iso = date.today().isoformat()

    # Team count
    members = TeamMember.objects.active_for_manager(mid).order_by("name")
    team_count = members.count()

    # Meeting cadence per member — single annotated query (#4)
    from django.db.models import Max, Count, Q
    cadence_qs = (
        Event.objects.for_manager(mid)
        .filter(
            status__in=["completed", "scheduled"],
            scheduled_date__lte=today_iso,  # past events only (#13)
        )
        .values("team_member_id")
        .annotate(last_date=Max("scheduled_date"))
    )
    cadence_map = {r["team_member_id"]: r["last_date"] for r in cadence_qs}
    meeting_cadence = []
    for m in members:
        last_date = cadence_map.get(m.id)
        if last_date:
            try:
                days = (date.today() - date.fromisoformat(last_date)).days
            except ValueError:
                days = None
            meeting_cadence.append({"name": m.name, "last_date": last_date, "days_ago": days})
        else:
            meeting_cadence.append({"name": m.name, "last_date": None, "days_ago": None})

    # Feedback ratios per member — single annotated query (#4)
    fb_qs = (
        Feedback.objects.for_manager(mid)
        .values("team_member_id", "team_member__name")
        .annotate(
            positive=Count("id", filter=Q(feedback_type="positive")),
            constructive=Count("id", filter=Q(feedback_type="constructive")),
        )
    )
    feedback_ratios = []
    for row in fb_qs:
        total = row["positive"] + row["constructive"]
        if total > 0:
            feedback_ratios.append({
                "name": row["team_member__name"],
                "positive": row["positive"],
                "constructive": row["constructive"],
                "ratio_pct": int(row["positive"] / total * 100),
            })

    # Action item stats
    all_actions = ActionItem.objects.active_for_manager(mid)
    action_total = all_actions.count()
    action_completed = all_actions.filter(status="completed").count()
    action_pending = all_actions.filter(status="pending").count()
    action_overdue = all_actions.filter(
        status="pending", due_date__lt=today_iso,
    ).count()
    completion_pct = int(action_completed / action_total * 100) if action_total else 0

    # Goal stats by status
    goals = Goal.objects.for_manager(mid)
    goal_stats = {}
    for status in ["active", "completed", "paused", "cancelled"]:
        c = goals.filter(status=status).count()
        if c > 0:
            goal_stats[status] = c

    # Personal anti-pattern synthesis (The Ghost / Micromanager / Buddy / Scorekeeper)
    from core.services.anti_patterns import detect_anti_patterns
    anti_patterns = detect_anti_patterns(meeting_cadence, feedback_ratios)

    # Totals
    total_events = Event.objects.for_manager(mid).count()
    total_feedback = Feedback.objects.for_manager(mid).count()
    streak = _journal_streak(mid, today_iso)

    # Management score — composite 0-100 from the metrics above. Each
    # component only contributes when the manager has data for it.
    if feedback_ratios:
        feedback_ratio_avg = sum(
            f["ratio_pct"] for f in feedback_ratios
        ) / len(feedback_ratios)
    else:
        feedback_ratio_avg = None
    if team_count:
        on_track = sum(
            1 for m in meeting_cadence
            if m["days_ago"] is not None and m["days_ago"] <= 14
        )
        cadence_coverage_pct = round(on_track / team_count * 100)
    else:
        cadence_coverage_pct = None
    goal_total = sum(goal_stats.values()) if goal_stats else 0
    goal_completed = goal_stats.get("completed", 0)
    goal_completion_pct = round(goal_completed / goal_total * 100) if goal_total else None
    action_completion_pct = completion_pct if action_total else None

    from core.services.management_score import compute_management_score
    ms = compute_management_score({
        "feedback": feedback_ratio_avg,
        "cadence": cadence_coverage_pct,
        "streak": streak,
        "goals": goal_completion_pct,
        "actions": action_completion_pct,
    })

    return render(request, "analytics.html", {
        "team_count": team_count,
        "total_events": total_events,
        "total_feedback": total_feedback,
        "streak": streak,
        "meeting_cadence": meeting_cadence,
        "feedback_ratios": feedback_ratios,
        "anti_patterns": anti_patterns,
        "ms": ms,
        "action_total": action_total,
        "action_completed": action_completed,
        "action_pending": action_pending,
        "action_overdue": action_overdue,
        "completion_pct": completion_pct,
        "goal_stats": goal_stats,
    })


@login_required
def history(request):
    """Cross-entity timeline: events, feedback, journal, delegations,
    decisions, goals, and notes in one chronological list."""
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    member_raw = request.GET.get("member", "")
    member_id = None
    if member_raw:
        try:
            member_id = int(member_raw)
        except ValueError:
            member_id = None  # ignore bad input, show unfiltered
    members = TeamMember.objects.active_for_manager(mid).order_by("name")

    timeline = []

    # Events
    events_qs = Event.objects.for_manager(mid).select_related("team_member")
    if member_id:
        events_qs = events_qs.filter(team_member_id=member_id)
    for e in events_qs.order_by("-scheduled_date")[:50]:
        timeline.append({
            "date": e.scheduled_date,
            "type": "event",
            "title": e.title,
            "detail": f"{e.scheduled_time} · {e.status or 'scheduled'}",
            "member": e.team_member.name if e.team_member else None,
        })

    # Feedback
    fb_qs = Feedback.objects.for_manager(mid).select_related("team_member")
    if member_id:
        fb_qs = fb_qs.filter(team_member_id=member_id)
    for f in fb_qs.order_by("-created_at")[:50]:
        created = f.created_at.strftime("%Y-%m-%d") if f.created_at else "?"
        timeline.append({
            "date": created,
            "type": "feedback",
            "title": f"{f.feedback_type.capitalize()} feedback",
            "detail": (f.situation or "")[:80],
            "member": f.team_member.name if f.team_member else None,
        })

    # Journal entries (not member-filterable)
    if not member_id:
        for j in JournalEntry.objects.for_manager(mid).order_by("-entry_date")[:30]:
            timeline.append({
                "date": j.entry_date,
                "type": "journal",
                "title": f"Journal ({j.entry_type})",
                "detail": (j.content or "")[:80],
                "member": None,
            })

    # Delegations
    del_qs = Delegation.objects.for_manager(mid).select_related("team_member")
    if member_id:
        del_qs = del_qs.filter(team_member_id=member_id)
    for d in del_qs.order_by("-created_at")[:30]:
        created = d.created_at.strftime("%Y-%m-%d") if d.created_at else "?"
        timeline.append({
            "date": created,
            "type": "delegation",
            "title": d.task[:60],
            "detail": f"Status: {d.status or 'active'}",
            "member": d.team_member.name if d.team_member else None,
        })

    # Decisions (not member-filterable)
    if not member_id:
        for d in Decision.objects.for_manager(mid).order_by("-created_at")[:20]:
            created = d.created_at.strftime("%Y-%m-%d") if d.created_at else "?"
            timeline.append({
                "date": created,
                "type": "decision",
                "title": d.title[:60],
                "detail": f"Status: {d.status or 'active'}",
                "member": None,
            })

    # Goals
    goal_qs = Goal.objects.for_manager(mid).select_related("team_member")
    if member_id:
        goal_qs = goal_qs.filter(team_member_id=member_id)
    for g in goal_qs.order_by("-created_at")[:20]:
        created = g.created_at.strftime("%Y-%m-%d") if g.created_at else "?"
        timeline.append({
            "date": created,
            "type": "goal",
            "title": g.description[:60],
            "detail": f"Status: {g.status or 'active'}",
            "member": g.team_member.name if g.team_member else None,
        })

    # Running notes
    note_qs = RunningNote.objects.for_manager(mid).select_related("team_member")
    if member_id:
        note_qs = note_qs.filter(team_member_id=member_id)
    for n in note_qs.order_by("-note_date")[:30]:
        timeline.append({
            "date": n.note_date,
            "type": "note",
            "title": n.content[:60],
            "detail": n.category or "",
            "member": n.team_member.name if n.team_member else None,
        })

    # Sort by date descending
    timeline.sort(key=lambda x: x["date"] or "", reverse=True)

    # Paginate (50 per page)
    PAGE_SIZE = 50
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    total = len(timeline)
    start = (page - 1) * PAGE_SIZE
    timeline = timeline[start:start + PAGE_SIZE]
    has_next = start + PAGE_SIZE < total
    has_prev = page > 1

    return render(request, "history.html", {
        "timeline": timeline,
        "members": members,
        "selected_member": member_id,
        "page": page,
        "has_next": has_next,
        "has_prev": has_prev,
    })


@login_required
def audit_log(request):
    """Read-only audit trail of HR-sensitive mutations.

    Paginated (50/page). Optional ?entity= and ?actor= filters. Manager-
    scoped via TenantManager; no edit affordances anywhere on the page.
    Complements migration 0009 which added the actor_type column so
    operator vs background-job writes can be distinguished.
    """
    manager, err = _require_manager(request)
    if err:
        return err
    mid = manager.id
    qs = AuditLog.objects.for_manager(mid)

    entity = request.GET.get("entity", "").strip()
    if entity:
        qs = qs.filter(entity_type=entity)
    actor = request.GET.get("actor", "").strip()
    if actor in ("user", "system"):
        qs = qs.filter(actor_type=actor)

    qs = qs.order_by("-created_at")

    PAGE_SIZE = 50
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    total = qs.count()
    start = (page - 1) * PAGE_SIZE
    rows = list(qs[start:start + PAGE_SIZE])
    has_next = start + PAGE_SIZE < total
    has_prev = page > 1

    # Entity-type filter dropdown — distinct values THIS manager has logged
    # (avoids leaking the schema of unrelated tenants via the dropdown).
    entity_types = list(
        AuditLog.objects.for_manager(mid)
        .values_list("entity_type", flat=True)
        .distinct()
        .order_by("entity_type")
    )

    return render(request, "audit_log.html", {
        "rows": rows,
        "entity": entity,
        "actor": actor,
        "entity_types": entity_types,
        "page": page,
        "has_next": has_next,
        "has_prev": has_prev,
        "total": total,
    })


@login_required
def resources(request):
    """Wisdom library browser — 620 management ideas from 23 books.
    Searchable by keyword, browsable by book/section."""
    manager, err = _require_manager(request)
    if err:
        return err
    from coaching.services import _load_wisdom, get_daily_wisdom, _WISDOM_SECTIONS

    entries = _load_wisdom()
    daily = get_daily_wisdom()
    query = request.GET.get("q", "").strip()
    section_filter = request.GET.get("section", "").strip()

    # Count sections
    sections = {}
    if _WISDOM_SECTIONS:
        for sec, indices in _WISDOM_SECTIONS.items():
            sections[sec] = len(indices)

    results = []
    if section_filter:
        # Exact section match (#15)
        for e in entries:
            if e["section"] == section_filter:
                results.append(e)
        query = ""  # don't show search UI state for section browse
    elif query:
        q_lower = query.lower()
        for e in entries:
            if q_lower in e["text"].lower() or q_lower in e["section"].lower():
                results.append(e)
    else:
        results = []  # Show section index instead

    return render(request, "resources.html", {
        "daily": daily,
        "query": query,
        "section_filter": section_filter,
        "results": results,
        "sections": sections,
        "wisdom_count": len(entries),
        "section_count": len(sections),
    })
