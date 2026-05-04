"""Recurring-event materialization (Phase 5.2b).

Ports the Streamlit `_add_months_anchored`, `_expand_recurrence_dates`,
and `create_recurring_events` from `database.py`. Same algorithm
(server-controlled max counts, end-of-month anchoring), Django-flavored
transaction (`transaction.atomic`) instead of Streamlit's hand-rolled
`_materialize_in_txn` (which existed to bypass psycopg2 autocommit and
SQLite cursor pitfalls — none of those concerns apply through the
Django ORM).

The "no orphan" guarantee — if any child INSERT fails, the parent INSERT
must roll back too — is what `_materialize_in_txn` was load-bearing for
in Streamlit. Django's `transaction.atomic()` gives us the same
guarantee for free; the smoke job's forced-failure assertion is the
only credible guard against the bug class regressing.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from django.db import transaction

from core.models import Event

# Server-controlled — the form doesn't pass a count.
RECURRENCE_COUNTS = {"weekly": 12, "monthly": 12, "quarterly": 8}

# Hard cap on materialized children. Defense in depth (the rule-derived
# counts above already cap at 12), but if a future rule is added with a
# higher count, the cap blocks runaway materialization.
MATERIALIZE_MAX_CHILDREN = 32


def add_months_anchored(start: date, n: int) -> date:
    """Return start + n months, clamped to last day if the target month
    is shorter. Iterates from start (Algo A) so the cadence stays
    anchored: Jan 31 + 1mo = Feb 28, + 2mo = Mar 31 (anchor preserved).
    NOT Algo B (advance from previous-clamped) which drifts permanently:
       Jan 31 + 1mo = Feb 28, + 1mo + 1mo = Mar 28 (anchor lost)."""
    if n == 0:
        return start
    y = start.year
    m = start.month - 1 + n
    y += m // 12
    m = m % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(start.day, last_day))


def expand_recurrence_dates(
    start: date, rule: str, until: date | None = None,
) -> list[date]:
    """Generate dates from `start` for the given rule. Stops at the rule's
    max count OR at `until` (inclusive), whichever is sooner. `start` is
    the parent's date and is the first element."""
    if rule not in RECURRENCE_COUNTS:
        raise ValueError(f"unknown recurrence rule: {rule}")
    max_count = RECURRENCE_COUNTS[rule]
    dates = [start]
    for i in range(1, max_count):
        if rule == "weekly":
            d = start + timedelta(weeks=i)
        elif rule == "monthly":
            d = add_months_anchored(start, i)
        else:  # quarterly
            d = add_months_anchored(start, 3 * i)
        if until and d > until:
            break
        dates.append(d)
    return dates


@transaction.atomic
def create_recurring_events(
    *,
    manager_id: int,
    title: str,
    event_type: str,
    start_date: date,
    scheduled_time: str,
    rule: str,
    until_date: date | None = None,
    team_member=None,
    duration_minutes: int = 30,
    location: str | None = None,
    agenda: str | None = None,
) -> Event:
    """Create the parent event + N concrete child rows atomically.
    Returns the parent.

    On any exception inside this function the entire transaction rolls
    back — no orphan children. This is asserted by smoke_pg_django.py's
    forced-failure check.

    `start_date` and `until_date` MUST be `date` instances (not iso
    strings); a stringly-typed callsite gets caught here, not silently
    passed through to `add_months_anchored` where it would TypeError
    on the day arithmetic.
    """
    if manager_id is None:
        raise ValueError("manager_id required (no implicit cross-tenant)")
    if not isinstance(start_date, date):
        raise TypeError("start_date must be a date instance")
    if until_date is not None:
        if not isinstance(until_date, date):
            raise TypeError("until_date must be a date instance")
        if until_date < start_date:
            raise ValueError("until_date must be >= start_date")

    dates = expand_recurrence_dates(start_date, rule, until_date)
    if len(dates) < 1:
        raise ValueError("recurrence produced zero dates")
    if len(dates) - 1 > MATERIALIZE_MAX_CHILDREN:
        raise ValueError(
            f"too many materializations "
            f"({len(dates) - 1} > cap {MATERIALIZE_MAX_CHILDREN})"
        )

    common = dict(
        title=title,
        event_type=event_type,
        manager_id=manager_id,
        scheduled_time=scheduled_time,
        duration_minutes=duration_minutes,
        location=location,
        agenda=agenda,
        recurrence_rule=rule,
        team_member=team_member,
        status="scheduled",
    )
    parent = Event.objects.create(
        scheduled_date=dates[0].isoformat(), **common,
    )
    children = [
        Event(
            scheduled_date=d.isoformat(),
            parent_event=parent,
            **common,
        )
        for d in dates[1:]
    ]
    if children:
        Event.objects.bulk_create(children)
    return parent
