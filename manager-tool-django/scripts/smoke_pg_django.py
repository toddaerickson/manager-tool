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
    from core.models import Config, JournalEntry, Manager, TeamMember

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
    CoachSuggestion.objects.create(
        suggestion_date="2026-05-01", tier="weekly",
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
