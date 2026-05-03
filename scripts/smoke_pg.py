#!/usr/bin/env python3
"""End-to-end smoke test against a real PostgreSQL backend.

Mirrors the production bootstrap path: applies schema_postgres.sql, then
calls init_db() (which runs the migration ledger), then exercises the
auth + session flows that have historically broken on the SQLite→PG port
(connection vs. cursor API, TIMESTAMP vs. TEXT comparisons).

Exit code is 0 only if every step succeeds. Intended to run in CI as a
guard against shipping PG-incompatible changes — the existing pytest
suite is SQLite-only and does not exercise psycopg2's row types.

Required env: DATABASE_URL must point at a fresh, writable PG database.
The script creates a `smoketest` manager and assumes nothing else owns
that username; CI uses a per-job fresh database, so collisions don't arise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _count_events_for(db, manager_id: int) -> int:
    """Count event rows scoped to manager_id. Used by the forced-failure
    no-orphan assertion to verify _materialize_in_txn rolled back."""
    with db._connect() as conn:
        cur = db._exec(conn,
            "SELECT COUNT(*) AS n FROM events WHERE manager_id = ?",
            (manager_id,))
        row = cur.fetchone()
    if row is None:
        return 0
    return row["n"] if isinstance(row, dict) else row[0]


def _seed_second_manager(db, soon: str) -> int:
    """Seed manager B via the same app helpers manager A used. Going through
    add_action_item / add_delegation / add_goal (NOT raw SQL) is what makes
    the cross-tenant assertion meaningful — a missing manager_id predicate
    would surface here, not in a raw-SQL seed that bypasses scope."""
    mid_b = db.create_manager(
        username="smoketest_b",
        display_name="Smoke Test B",
        password="smoke-test-password-456",
        email="smoke_b@test.local",
    )
    if mid_b is None:
        raise RuntimeError("create_manager returned None for second manager")
    member_b = db.add_team_member(
        name="Smoke Member B", email="member_b@test.local", role="IC",
        manager_id=mid_b)
    db.add_action_item("B's task", due_date=soon, manager_id=mid_b)
    db.add_delegation("B's deleg", team_member_id=member_b,
                      outcome_expected="B's deleg outcome (smoke)",
                      check_in_date=soon, manager_id=mid_b)
    db.add_goal(member_b, "Q2 2026", "B's goal",
                target_date=soon, manager_id=mid_b)
    return mid_b


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("error: DATABASE_URL must be set", file=sys.stderr)
        return 1

    import psycopg2

    import database as db

    db._USE_PG = None
    db._INIT_DB_DONE = False

    schema_sql = (REPO_ROOT / "schema_postgres.sql").read_text()
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()
    print("[ok] schema_postgres.sql applied")

    db.init_db(force=True)
    print("[ok] init_db (migration runner) succeeded")

    # Migration idempotency — re-running must be a no-op. Catches a regression
    # where any migration entry forgets the IF NOT EXISTS / column-existence
    # guard and would re-attempt the ALTER on the second run.
    db.init_db(force=True)
    print("[ok] init_db second run (idempotency)")

    mid = db.create_manager(
        username="smoketest",
        display_name="Smoke Test",
        password="smoke-test-password-123",
        email="smoke@test.local",
    )
    if mid is None:
        print("error: create_manager returned None", file=sys.stderr)
        return 1
    print(f"[ok] create_manager → id={mid}")

    auth = db.authenticate_manager("smoketest", "smoke-test-password-123")
    if not auth or auth["id"] != mid:
        print(f"error: authenticate_manager mismatch: {auth}", file=sys.stderr)
        return 1
    print("[ok] authenticate_manager")

    token = db.create_session(mid, ttl_seconds=60, user_agent_hash="ua-hash")
    if not token:
        print("error: create_session returned empty token", file=sys.stderr)
        return 1
    print(f"[ok] create_session → token len={len(token)}")

    got = db.validate_session(token, user_agent_hash="ua-hash")
    if got != mid:
        print(f"error: validate_session returned {got}, expected {mid}",
              file=sys.stderr)
        return 1
    print(f"[ok] validate_session → manager_id={got}")

    db.record_failed_login("nonexistent-user")
    locked = db.get_lockout_until("nonexistent-user")
    print(f"[ok] record_failed_login + get_lockout_until → {locked}")

    member_id = db.add_team_member(
        name="Smoke Member", email="member@test.local", role="IC", manager_id=mid)
    if not member_id:
        print("error: add_team_member returned None", file=sys.stderr)
        return 1
    db.add_feedback(
        team_member_id=member_id, feedback_type="positive",
        situation="s", behavior="b", impact="i", manager_id=mid)

    timeline = db.get_member_timeline(member_id, manager_id=mid)
    print(f"[ok] get_member_timeline → {len(timeline)} row(s)")

    prep = db.get_pre_meeting_prep(member_id, manager_id=mid)
    print(f"[ok] get_pre_meeting_prep → keys={sorted((prep or {}).keys())}")

    trends = db.get_manager_activity_trends(weeks=12, manager_id=mid)
    print(f"[ok] get_manager_activity_trends → {len(trends)} week(s)")

    # -- Upcoming aggregator + cross-tenant leak guard ----------------------
    # Seeds a second manager via app helpers (NOT raw SQL) so the cross-tenant
    # check exercises the real predicate path on PG. Catches PG-only predicate
    # bugs (e.g. text-vs-date coercion drift) that SQLite-side pytest cannot.
    from datetime import date, timedelta
    soon = (date.today() + timedelta(days=2)).isoformat()
    past = (date.today() - timedelta(days=5)).isoformat()

    db.add_action_item("A's task", due_date=soon, manager_id=mid)
    db.add_delegation("A's deleg", team_member_id=member_id,
                      outcome_expected="A's deleg outcome (smoke)",
                      check_in_date=soon, manager_id=mid)
    db.add_goal(member_id, "Q2 2026", "A's goal",
                target_date=soon, manager_id=mid)
    db.add_action_item("A's overdue task", due_date=past, manager_id=mid)

    a_upcoming = db.get_upcoming_aggregate(manager_id=mid)
    a_overdue = db.get_overdue_aggregate(manager_id=mid)
    print(f"[ok] get_upcoming_aggregate (A) → {len(a_upcoming)} row(s)")
    print(f"[ok] get_overdue_aggregate (A) → {len(a_overdue)} row(s)")

    mid_b = _seed_second_manager(db, soon)

    a_upcoming_after_b = db.get_upcoming_aggregate(manager_id=mid)
    a_titles = {r["title"] for r in a_upcoming_after_b}
    if "B's task" in a_titles or "B's deleg" in a_titles or "B's goal" in a_titles:
        print(f"error: manager A leaked manager B rows: {a_titles}",
              file=sys.stderr)
        return 1
    print("[ok] cross-tenant: manager A sees no manager-B rows")

    b_upcoming = db.get_upcoming_aggregate(manager_id=mid_b)
    b_titles = {r["title"] for r in b_upcoming}
    if "A's task" in b_titles or "A's deleg" in b_titles or "A's goal" in b_titles:
        print(f"error: manager B leaked manager A rows: {b_titles}",
              file=sys.stderr)
        return 1
    if "B's task" not in b_titles:
        print(f"error: bidirectional check failed — B sees own rows: {b_titles}",
              file=sys.stderr)
        return 1
    print("[ok] cross-tenant: manager B sees own rows, no manager-A rows")

    # -- Recurring events end-to-end (PR 4) --------------------------------
    # Materializes a monthly-on-31st series that exercises _add_months_anchored
    # clamp behavior on real PG, then forces a synthetic mid-materialization
    # failure to assert _materialize_in_txn doesn't leave an orphan parent.
    pid_monthly = db.create_recurring_events(
        title="Month-end review (smoke)",
        event_type="quarterly_review",
        start_date=date(2026, 1, 31),
        scheduled_time="14:00",
        rule="monthly",
        manager_id=mid,
    )
    print(f"[ok] create_recurring_events (monthly-on-31st) → parent #{pid_monthly}")

    # Forced-failure no-orphan assertion: NULL title violates NOT NULL on
    # the child INSERT. _materialize_in_txn must roll back the parent.
    parent_count_before = _count_events_for(db, mid)
    parent_sql = ("INSERT INTO events (title, event_type, scheduled_date, "
                  "scheduled_time, manager_id) VALUES (?, ?, ?, ?, ?)")
    parent_params = ("Should not commit", "one_on_one", "2026-12-31", "10:00", mid)
    children_sql = ("INSERT INTO events (title, event_type, scheduled_date, "
                    "scheduled_time, manager_id, parent_event_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)")
    bad_children = [(None, "one_on_one", "2026-12-31", "10:00", mid)]
    with db._connect() as conn:
        try:
            db._materialize_in_txn(conn, parent_sql, parent_params,
                                   children_sql, bad_children)
        except Exception:
            pass
        else:
            print("error: forced-failure materialize did not raise",
                  file=sys.stderr)
            return 1
    parent_count_after = _count_events_for(db, mid)
    if parent_count_after != parent_count_before:
        print(f"error: orphan parent landed — count went from "
              f"{parent_count_before} to {parent_count_after}",
              file=sys.stderr)
        return 1
    print("[ok] forced-failure no-orphan: parent rolled back correctly")

    # -- 1:1 sessions end-to-end (PR `db/one-on-one-sessions`) -------------
    # Round-trip via app helpers (NOT raw SQL), so the cross-tenant check
    # exercises the real predicate path on real PG. Round-3 caught raw-SQL
    # seeding as tautological for predicate verification.
    today_iso = date.today().isoformat()
    sid_a = db.create_one_on_one_session(
        manager_id=mid, team_member_id=member_id,
        session_date=today_iso,
        direct_notes="A's direct notes (smoke)",
        manager_notes="A's manager notes (smoke)",
        followup_notes="A's followup (smoke)")
    print(f"[ok] create_one_on_one_session (A) → id={sid_a}")

    # UPSERT idempotency on the same (manager, member, date) tuple.
    sid_a_again = db.create_one_on_one_session(
        manager_id=mid, team_member_id=member_id,
        session_date=today_iso,
        direct_notes="A's direct notes (smoke v2)")
    if sid_a_again != sid_a:
        print(f"error: UPSERT created new row {sid_a_again} instead of "
              f"updating {sid_a}", file=sys.stderr)
        return 1
    a_session = db.get_one_on_one_session(sid_a, manager_id=mid)
    if (a_session or {}).get("direct_notes") != "A's direct notes (smoke v2)":
        print(f"error: UPSERT did not update row contents: "
              f"{a_session}", file=sys.stderr)
        return 1
    print("[ok] UPSERT idempotency: same tuple updates in place")

    # Manager B (already seeded) gets their own session.
    member_b_rows = db.list_team_members(manager_id=mid_b)
    if not member_b_rows:
        print("error: manager B has no team members; smoke seed broken",
              file=sys.stderr)
        return 1
    member_b = member_b_rows[0]["id"]
    sid_b = db.create_one_on_one_session(
        manager_id=mid_b, team_member_id=member_b,
        session_date=today_iso, direct_notes="B's notes (smoke)")
    print(f"[ok] create_one_on_one_session (B) → id={sid_b}")

    # Cross-tenant: manager A cannot read B's session even with the right id.
    if db.get_one_on_one_session(sid_b, manager_id=mid) is not None:
        print(f"error: manager A read manager B's session {sid_b}",
              file=sys.stderr)
        return 1
    if db.get_one_on_one_session(sid_a, manager_id=mid_b) is not None:
        print(f"error: manager B read manager A's session {sid_a}",
              file=sys.stderr)
        return 1
    a_sessions = db.list_one_on_one_sessions(manager_id=mid)
    a_notes = {(r.get("direct_notes") or "") for r in a_sessions}
    if "B's notes (smoke)" in a_notes:
        print(f"error: manager A's list_one_on_one_sessions leaked B's row: "
              f"{a_notes}", file=sys.stderr)
        return 1
    print("[ok] cross-tenant: 1:1 sessions are bidirectionally isolated")

    # event_id consistency check: binding A's session to an event that
    # belongs to a different member must raise.
    other_member = db.add_team_member("Other Smoke Member", manager_id=mid)
    other_event = db.create_event(
        "Other 1:1", "one_on_one", today_iso, "11:00",
        team_member_id=other_member, manager_id=mid)
    try:
        db.create_one_on_one_session(
            manager_id=mid, team_member_id=member_id,
            session_date="2026-12-15", event_id=other_event)
    except ValueError:
        print("[ok] event_id consistency check: refused mismatched member")
    else:
        print("error: create_one_on_one_session accepted event_id whose "
              "team_member_id does not match the session's", file=sys.stderr)
        return 1

    # Lock contract: backdate created_at to >LOCK_WINDOW ago, attempt
    # update, confirm PermissionError. Mirrors the SQLite test on real PG.
    with db._connect() as conn:
        db._exec(conn,
            "UPDATE one_on_one_sessions "
            "SET created_at = NOW() - INTERVAL '25 hours' "
            "WHERE id = ?",
            (sid_a,))
        db._commit(conn)
    try:
        db.update_one_on_one_session(
            sid_a, manager_id=mid, direct_notes="too late")
    except PermissionError:
        print("[ok] 24h lock: helper raises PermissionError after window")
    else:
        print("error: update_one_on_one_session did not raise after 25h",
              file=sys.stderr)
        return 1

    db.revoke_session(token)
    after_revoke = db.validate_session(token, user_agent_hash="ua-hash")
    if after_revoke is not None:
        print(f"error: validate_session returned {after_revoke} after revoke",
              file=sys.stderr)
        return 1
    print("[ok] revoke_session")

    print("\nALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
