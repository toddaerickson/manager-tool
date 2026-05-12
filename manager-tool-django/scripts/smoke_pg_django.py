#!/usr/bin/env python3
"""End-to-end smoke test for the Django app against a real Postgres.

Mirrors the production cutover bootstrap path:
  1. Apply Streamlit's fresh-deploy schema (schema_postgres.sql)
  2. Apply the Phase 2 ALTER (migrate_p2_config_to_id_pk.sql) — gives
     `config` the id PK + unique_together shape Django expects
  3. Run Django `migrate --fake-initial` — Django takes ownership; for
     allauth/auth/admin/etc. their tables don't exist yet so they get
     real CREATE TABLE; for core/coaching the tables already exist so
     fake-apply marks them done
  4. Run `makemigrations --dry-run` — must report "No changes detected"
     (the silent column-drift gate)
  5. Use the ORM to:
       a. Create two managers via app helpers (NOT raw SQL — going
          through the ORM is what makes the cross-tenant assertion
          meaningful, mirroring the Streamlit smoke's "no raw seeding"
          rule from round-3 review)
       b. Insert tenant-scoped rows for each
       c. Assert TenantManager.for_manager isolates bidirectionally
       d. Exercise update_or_create on Config (Phase 2 upsert pattern)

Required env: DATABASE_URL must point at a fresh, writable Postgres.
CI uses a postgres:16 service container per job; locally use a docker
container or a throwaway Neon branch (NOT the dev branch, which has
real data).

Exit code 0 only if every step succeeds.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DJANGO_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DJANGO_DIR.parent
SCHEMA_SQL = REPO_ROOT / "schema_postgres.sql"
PHASE2_SQL = REPO_ROOT / "scripts" / "migrate_p2_config_to_id_pk.sql"

# Make `mt` and the apps importable when this script is run as
# `python scripts/smoke_pg_django.py` — Python adds the script's own
# directory (scripts/) to sys.path but not its parent.
sys.path.insert(0, str(DJANGO_DIR))


def _bail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _step(msg: str) -> None:
    print(f"--- {msg} ---", flush=True)


def _setup_env() -> None:
    """Settings-module env vars Django/settings.py expects to find."""
    if not os.environ.get("DATABASE_URL"):
        _bail("DATABASE_URL must be set (point at a fresh Postgres)")
    os.environ.setdefault("DJANGO_SECRET_KEY", "smoke-only-not-secret")
    os.environ.setdefault("MANAGER_TOOL_ENV", "dev")
    os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
    os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "")
    os.environ.setdefault("SENTRY_DSN", "")  # disable Sentry in smoke
    os.environ["DJANGO_SETTINGS_MODULE"] = "mt.settings"


def _apply_sql_file(path: Path, label: str) -> None:
    """Apply a .sql file via psycopg directly. Bypasses Django so we can
    exercise the schema bootstrap before Django knows the DB exists."""
    import psycopg

    _step(f"applying {label}")
    sql = path.read_text()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


def _run_django(*args: str) -> str:
    """Invoke manage.py with the smoke env and return stdout. Bails on
    non-zero exit."""
    cmd = [sys.executable, str(DJANGO_DIR / "manage.py"), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=DJANGO_DIR)
    if proc.returncode != 0:
        _bail(f"manage.py {' '.join(args)} exited {proc.returncode}\n"
              f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
    return proc.stdout


def _exercise_orm() -> None:
    """Use Django ORM in-process to exercise tenant scoping + the Phase 2
    Config upsert. Imports happen inside this function so Django setup
    runs after schema bootstrap."""
    import django
    django.setup()

    from coaching.models import CoachSuggestion
    from core.models import (
        ActionItem, Config, JournalEntry, Manager, OneOnOneSession, TeamMember,
    )

    _step("creating two managers via ORM")
    m1 = Manager.objects.create(
        username="smoke_m1", display_name="Smoke M1",
        password_hash="bcrypt-stub-1", email="m1@smoke.local",
    )
    m2 = Manager.objects.create(
        username="smoke_m2", display_name="Smoke M2",
        password_hash="bcrypt-stub-2", email="m2@smoke.local",
    )

    _step("seeding tenant-scoped rows for each manager")
    TeamMember.objects.create(name="m1 report", manager_id=m1.id)
    TeamMember.objects.create(name="m2 report", manager_id=m2.id)
    JournalEntry.objects.create(
        entry_date="2026-05-01", entry_type="daily",
        content="m1 only", manager_id=m1.id,
    )
    # tier must be 'rule' or 'ai' per schema_postgres.sql CHECK constraint
    CoachSuggestion.objects.create(
        suggestion_date="2026-05-01", tier="rule",
        suggestion="m1 only", manager_id=m1.id,
    )

    _step("asserting bidirectional cross-tenant isolation")
    assert TeamMember.objects.for_manager(m1.id).count() == 1, "m1 sees wrong member count"
    assert TeamMember.objects.for_manager(m2.id).count() == 1, "m2 sees wrong member count"
    assert JournalEntry.objects.for_manager(m1.id).count() == 1
    assert JournalEntry.objects.for_manager(m2.id).count() == 0, \
        "m2 sees m1's journal entry — TenantManager regression"
    assert CoachSuggestion.objects.for_manager(m1.id).count() == 1
    assert CoachSuggestion.objects.for_manager(m2.id).count() == 0, \
        "m2 sees m1's coach suggestion — cross-app TenantManager regression"

    _step("exercising Config.update_or_create (Phase 2 upsert)")
    obj, created = Config.objects.update_or_create(
        manager_id=m1.id, key="theme",
        defaults={"value": "dark"},
    )
    assert created is True, "first update_or_create should insert"
    assert obj.value == "dark"

    obj2, created2 = Config.objects.update_or_create(
        manager_id=m1.id, key="theme",
        defaults={"value": "light"},
    )
    assert created2 is False, "second update_or_create should update existing"
    assert obj2.value == "light"
    assert obj2.id == obj.id, "update_or_create should reuse the same row id"
    assert Config.objects.for_manager(m1.id).count() == 1, \
        "Config row count should still be 1 after update"

    _step("asserting Config unique_together rejects duplicate")
    from django.db import IntegrityError, transaction
    try:
        with transaction.atomic():
            Config.objects.create(
                manager_id=m1.id, key="theme", value="duplicate",
            )
        _bail("creating duplicate (manager_id, key) should have raised IntegrityError")
    except IntegrityError:
        pass

    _step("exercising OneOnOneSession + ActionItem FK")
    tm1 = TeamMember.objects.for_manager(m1.id).first()
    session = OneOnOneSession.objects.create(
        manager=m1, team_member=tm1, session_date="2026-05-10",
        status="draft", direct_notes="Smoke test notes",
    )
    assert OneOnOneSession.objects.for_manager(m1.id).count() == 1
    assert OneOnOneSession.objects.for_manager(m2.id).count() == 0, \
        "m2 sees m1's meeting session — TenantManager regression"

    action = ActionItem.objects.create(
        manager_id=m1.id, one_on_one_session=session,
        description="Smoke action item", status="pending",
    )
    assert action.one_on_one_session_id == session.id
    assert ActionItem.objects.for_manager(m1.id).filter(
        one_on_one_session=session,
    ).count() == 1

    _exercise_recurring_events_no_orphan(m1)
    _exercise_dashboard_naive_timestamp(m1)


def _exercise_dashboard_naive_timestamp(manager) -> None:
    """Regression: feedback.created_at (and most other *_at columns) is
    declared `TIMESTAMP` (no tz) in schema_postgres.sql. psycopg2 returns
    those as naive datetimes; Django's `timezone.now()` is aware. The
    dashboard overview view subtracts the two — naive minus aware was
    raising TypeError ("can't subtract offset-naive and offset-aware
    datetimes") on PG only. SQLite returns aware values for the same
    field, so the pytest suite cannot catch this class of bug."""
    from core.models import Feedback, TeamMember
    from core.views.events import dashboard_overview
    from django.test import RequestFactory

    _step("dashboard overview tolerates naive feedback timestamps (PG TIMESTAMP)")
    tm = TeamMember.objects.for_manager(manager.id).first()
    Feedback.objects.create(
        team_member=tm, feedback_type="positive",
        situation="smoke fb", manager_id=manager.id,
    )

    from django.contrib.auth import get_user_model
    user = get_user_model().objects.create_user(
        username=f"smoke_dashboard_{manager.id}",
        email=manager.email, password="x",
    )
    rf = RequestFactory()
    req = rf.get("/dashboard/panels/overview/")
    # Bypass middleware: @login_required reads request.user;
    # dashboard_overview reads request.manager. Wire both manually.
    req.user = user
    req.manager = manager
    resp = dashboard_overview(req)
    if resp.status_code != 200:
        _bail(
            f"dashboard overview returned {resp.status_code} on PG — "
            f"naive/aware datetime subtraction regression?"
        )


def _exercise_recurring_events_no_orphan(manager) -> None:
    """Phase 5.2b — forced-failure no-orphan assertion.

    The plan + CLAUDE.md: '_materialize_in_txn ... The forced-failure
    no-orphan smoke assertion is the only credible guard against this
    bug class.' Django's transaction.atomic() should give us the same
    guarantee for free. Prove it.

    1. Happy path: create a small recurring series, assert parent + N-1
       children exist with parent_event FK wired correctly.
    2. Failure path: monkey-patch Event.objects.bulk_create to raise
       AFTER the parent INSERT. Assert that the parent INSERT was rolled
       back (post-failure event count == pre-failure count).
    """
    from datetime import date, timedelta

    from core.models import Event
    from core.services.events import create_recurring_events

    _step("create_recurring_events happy path (weekly, 4 children)")
    start = date(2026, 6, 1)  # Monday — deterministic, far from now
    until = start + timedelta(weeks=3)  # 4 occurrences total: parent + 3 children
    parent = create_recurring_events(
        manager_id=manager.id, title="weekly 1:1",
        event_type="one_on_one", start_date=start,
        scheduled_time="10:00", rule="weekly", until_date=until,
    )
    series = Event.objects.for_manager(manager.id).filter(
        recurrence_rule="weekly",
    )
    assert series.count() == 4, f"expected 4 events in series, got {series.count()}"
    children = series.filter(parent_event=parent)
    assert children.count() == 3, f"expected 3 children, got {children.count()}"
    assert all(c.parent_event_id == parent.id for c in children), \
        "children must point at parent via parent_event_id"
    assert series.filter(parent_event__isnull=True).count() == 1, \
        "exactly one row (the parent) should have parent_event NULL"

    _step("forced-failure no-orphan assertion (monkey-patch bulk_create to raise)")
    initial = Event.objects.for_manager(manager.id).count()
    original = Event.objects.bulk_create

    def _boom(*_a, **_kw):
        raise RuntimeError("smoke: forced failure to test transaction rollback")

    Event.objects.bulk_create = _boom
    try:
        try:
            create_recurring_events(
                manager_id=manager.id, title="forced-fail",
                event_type="other", start_date=date(2026, 7, 1),
                scheduled_time="11:00", rule="weekly",
            )
        except RuntimeError:
            pass
        else:
            _bail("forced failure should have raised RuntimeError")
    finally:
        Event.objects.bulk_create = original

    final = Event.objects.for_manager(manager.id).count()
    if final != initial:
        _bail(
            f"NO-ORPHAN FAIL: {final - initial} orphan event row(s) left after "
            f"forced failure (transaction did not roll back)"
        )


def main() -> None:
    _setup_env()
    _apply_sql_file(SCHEMA_SQL, "schema_postgres.sql (Streamlit fresh-deploy)")
    _apply_sql_file(PHASE2_SQL, "migrate_p2_config_to_id_pk.sql (Phase 2 ALTER)")

    _step("running Django migrate --fake-initial")
    _run_django("migrate", "--fake-initial", "--no-input")

    _step("verifying makemigrations --dry-run is clean")
    out = _run_django("makemigrations", "--dry-run")
    if "No changes detected" not in out:
        _bail(f"silent column drift detected:\n{out}")

    _exercise_orm()

    print("\nOK: Django PG smoke passed.")


if __name__ == "__main__":
    main()
