"""Composite management score — a single 0-100 signal synthesized from the
analytics the app already computes (feedback ratio, meeting cadence, journal
streak, goal and action completion). Pure function: no DB access, fully
unit-testable.

Each component is normalized to 0-100 and only contributes when the manager
has data for it (value is not None), so a fresh account isn't unfairly
penalized for things it hasn't tried yet. The weighted average is reweighted
over whichever components are present.
"""

# Component weights (sum to 1.0 when all present).
_WEIGHTS = {
    "feedback": 0.30,
    "cadence": 0.25,
    "streak": 0.15,
    "goals": 0.15,
    "actions": 0.15,
}

# A journal streak of 14+ consecutive days scores full marks; beyond that the
# marginal benefit flattens (capped so 30 days isn't wildly better than 14).
_STREAK_TARGET_DAYS = 14


def _grade(score):
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    if score >= 30:
        return "D"
    return "F"


def compute_management_score(stats):
    """Return a dict with the composite score, letter grade, and per-component
    sub-scores.

    `stats` accepts any of these keys (None means "no data", so that component
    is skipped):
      - feedback: average positive-ratio % across team members (0-100)
      - cadence:  % of team members with a 1:1 within the last 14 days (0-100)
      - streak:   current journal streak in days
      - goals:    goal completion % (0-100)
      - actions:  action-item completion % (0-100)

    Returns {"score": int|None, "grade": str|None, "subscores": {key: 0-100}}.
    score/grade are None when there is no data at all."""
    subscores = {}
    for key in _WEIGHTS:
        value = stats.get(key)
        if value is None:
            continue
        if key == "streak":
            value = min(value, _STREAK_TARGET_DAYS) / _STREAK_TARGET_DAYS * 100
        subscores[key] = max(0, min(100, round(value)))

    if not subscores:
        return {"score": None, "grade": None, "subscores": {}}

    total_weight = sum(_WEIGHTS[k] for k in subscores)
    score = round(
        sum(subscores[k] * _WEIGHTS[k] for k in subscores) / total_weight
    )
    return {"score": score, "grade": _grade(score), "subscores": subscores}
