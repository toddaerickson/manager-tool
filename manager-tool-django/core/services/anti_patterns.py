"""Personal management anti-pattern detection.

Ports the Streamlit-era `templates.py::detect_anti_patterns` to Django.
Synthesizes the analytics the app already computes — meeting cadence,
feedback ratios — into named behavioral anti-patterns (the "identity
hook" alerts: "You're showing signs of The Ghost"). Each pattern carries
evidence, a concrete suggestion, and a piece of wisdom so it reads as a
coach's observation, not a verdict.

Pure functions: callers pass the same shapes the analytics view already
builds, so this module has no ORM/DB dependency of its own.
"""

import random

# Static wisdom fallbacks. The full 620-idea library is loaded from
# coaching.services where available; these keep the detector independent
# of the wisdom file being present.
_MEETING_WISDOM = [
    "Grove: 'Reports are self-discipline, not communication. The weekly one-on-one is the primary point of contact.'",
    "Horstman: 'Weekly 30-minute one-on-ones are the single most important management behavior.'",
    "Buckingham: 'Catch your people doing something right — the catalyst speeds the reaction between talent and goals.'",
]
_FEEDBACK_WISDOM = [
    "Dellanna: 'Every time an employee achieves an objective but doesn't get rewarded, motivation dies.'",
    "Grove: 'Performance reviews are the highest-leverage activity a manager performs.'",
    "Grove: 'The most important thing a manager can do is assess performance, not potential.'",
]


def _wisdom_for(section: str) -> str:
    entries = _MEETING_WISDOM if section == "MEETING" else _FEEDBACK_WISDOM
    return random.choice(entries)


def detect_anti_patterns(meeting_cadence, feedback_ratios):
    """Return a list of {pattern, evidence, suggestion, wisdom} dicts.

    `meeting_cadence`: list of {name, last_date, days_ago} (days_ago None
    means never met). `feedback_ratios`: list of {name, positive,
    constructive} counts. Both match what `core.views.reference.analytics`
    already builds.
    """
    patterns = []

    # The Ghost — not meeting enough
    if meeting_cadence:
        for m in meeting_cadence:
            days = m.get("days_ago")
            name = m.get("name", "a team member")
            if days is not None and days > 21:
                patterns.append({
                    "pattern": "The Ghost",
                    "evidence": f"It's been {days} days since you met with {name}.",
                    "suggestion": "Block dedicated time. Your team is your primary job.",
                    "wisdom": _wisdom_for("MEETING"),
                })
                break
            if days is None:
                patterns.append({
                    "pattern": "The Ghost",
                    "evidence": f"You've never had a recorded meeting with {name}.",
                    "suggestion": "Schedule your first 1-on-1 this week.",
                    "wisdom": _wisdom_for("MEETING"),
                })
                break

    # The Micromanager / The Buddy / The Scorekeeper — from feedback ratios
    if feedback_ratios:
        total_pos = sum(r.get("positive") or 0 for r in feedback_ratios)
        total_con = sum(r.get("constructive") or 0 for r in feedback_ratios)
        total = total_pos + total_con
        if total > 3 and (total_con / max(total, 1)) > 0.8:
            pct = int(total_con / max(total, 1) * 100)
            patterns.append({
                "pattern": "The Micromanager",
                "evidence": f"Your feedback is {pct}% constructive across all members.",
                "suggestion": "What is your team doing well? Catch them doing something right.",
                "wisdom": _wisdom_for("FEEDBACK"),
            })
        if total > 0 and total_con == 0:
            patterns.append({
                "pattern": "The Buddy",
                "evidence": "You haven't given any constructive feedback recently.",
                "suggestion": "Caring means telling the truth. Kindness is not avoidance.",
                "wisdom": _wisdom_for("FEEDBACK"),
            })
        for r in feedback_ratios:
            if (r.get("constructive") or 0) > 3 and (r.get("positive") or 0) == 0:
                patterns.append({
                    "pattern": "The Scorekeeper",
                    "evidence": f"{r.get('name', 'Someone')} has received only "
                                "constructive feedback and zero positive.",
                    "suggestion": "Address issues once, then move forward. "
                                  "What are their strengths?",
                    "wisdom": _wisdom_for("FEEDBACK"),
                })
                break

    return patterns
