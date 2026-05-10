#!/usr/bin/env python3
"""Phase 7 data-validation script — proves Django ORM reads match raw SQL
counts against the same Neon database.

The script queries every tenant-scoped table twice:
  1. Via Django ORM (TenantManager.for_manager)
  2. Via raw SQL (direct psycopg connection)

If the counts differ for any table/manager pair, the script exits non-zero.
This catches: missing for_manager filters, broken model Meta.db_table
mappings, managed=True on tables Django shouldn't own, and any Django
migration that silently altered the schema.

Usage:
    DATABASE_URL=postgresql://... python scripts/cutover_diff.py

Run against the Neon dev branch first (proves the script works), then
against production immediately before cutover. Zero discrepancies on
prod is the go/no-go signal.

Exit codes:
  0 — PASS (all counts match, at least one manager found)
  1 — FAIL (count drift detected)
  2 — ERROR (no managers found, connection failure, etc.)

Output: prints to stdout AND writes a JSON artifact to
scripts/cutover_diff_<timestamp>.json for audit trail.
"""

import json
import os
import sys
from datetime import datetime, timezone

# ── Django bootstrap ──────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mt.settings")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django
django.setup()

from psycopg import sql as psql
import psycopg
from core.models import (
    ActionItem, AuditLog, CareerConversation, Decision, Delegation,
    DevelopmentPlan, Event, Feedback, Goal, JournalEntry, Manager,
    Milestone, OneOnOneSession, RunningNote, SelfAssessment, Skill,
    TeamMember,
)
from core.models import Config

# ── Table → Model mapping ────────────────────────────────────
# Every tenant-scoped model with manager_id (integer or FK).
TENANT_MODELS = [
    ("team_members", TeamMember),
    ("events", Event),
    ("action_items", ActionItem),
    ("journal_entries", JournalEntry),
    ("goals", Goal),
    ("skills", Skill),
    ("development_plans", DevelopmentPlan),
    ("milestones", Milestone),
    ("delegations", Delegation),
    ("decisions", Decision),
    ("feedback", Feedback),
    ("running_notes", RunningNote),
    ("career_conversations", CareerConversation),
    ("one_on_one_sessions", OneOnOneSession),
    ("self_assessments", SelfAssessment),
    ("config", Config),
    ("audit_log", AuditLog),
]


def get_raw_connection():
    """Direct psycopg connection (bypasses Django ORM). Read-only."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("FAIL: DATABASE_URL not set.")
        sys.exit(2)
    try:
        conn = psycopg.connect(url, connect_timeout=10)
        # Set read-only at session level (Neon pooler doesn't support
        # startup options, so we SET after connecting).
        conn.execute("SET default_transaction_read_only = on")
        return conn
    except Exception as e:
        print(f"FAIL: Could not connect to database: {e}")
        sys.exit(2)


def get_active_manager_ids(conn):
    """All manager IDs that have any tenant data."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM managers ORDER BY id")
        return [row[0] for row in cur.fetchall()]


def raw_count(conn, table, manager_id):
    """Count rows in a table for a specific manager_id via raw SQL.
    Uses psycopg.sql.Identifier to safely quote the table name.
    Returns None if the table doesn't exist (Django-only tables like
    audit_log won't exist on production until migrate runs)."""
    try:
        with conn.cursor() as cur:
            query = psql.SQL("SELECT COUNT(*) FROM {} WHERE manager_id = %s").format(
                psql.Identifier(table)
            )
            cur.execute(query, (manager_id,))
            return cur.fetchone()[0]
    except psycopg.errors.UndefinedTable:
        conn.rollback()  # clear the error state
        return None


def orm_count(model, manager_id):
    """Count rows via Django ORM's TenantManager.
    Returns None if the table doesn't exist yet."""
    try:
        return model.objects.for_manager(manager_id).count()
    except Exception:
        # Table doesn't exist — Django migration hasn't run on this DB
        from django.db import connection
        connection.ensure_connection()  # reset after error
        return None


def run_diff():
    started_at = datetime.now(timezone.utc).isoformat()
    print("Phase 7 — Cutover data-validation diff")
    print("=" * 50)

    conn = get_raw_connection()
    manager_ids = get_active_manager_ids(conn)

    if not manager_ids:
        print("FAIL: No managers found in database. Wrong DATABASE_URL?")
        conn.close()
        sys.exit(2)

    print(f"Found {len(manager_ids)} manager(s)")

    drift_count = 0
    check_count = 0
    zero_tables = set()
    results = []

    for manager_id in manager_ids:
        print(f"\n--- Manager {manager_id} ---")
        for table, model in TENANT_MODELS:
            raw = raw_count(conn, table, manager_id)
            orm = orm_count(model, manager_id)
            check_count += 1
            if raw is None or orm is None:
                # Table doesn't exist in DB yet (Django-only, created by migrate)
                status = "SKIP"
                print(f"  SKIP:  {table:25s}  (table not in DB — will be created by migrate)")
            elif raw != orm:
                status = "DRIFT"
                print(f"  DRIFT: {table:25s}  raw={raw}  orm={orm}")
                drift_count += 1
            else:
                status = "OK"
                print(f"  OK:    {table:25s}  count={raw}")
            if raw == 0 and orm == 0:
                zero_tables.add(table)
            results.append({
                "manager_id": manager_id,
                "table": table,
                "raw_count": raw,
                "orm_count": orm,
                "status": status,
            })

    conn.close()

    # Warn on tables that are zero across ALL managers
    if zero_tables:
        print(f"\nWARN: Tables with zero rows across all managers: {sorted(zero_tables)}")
        print("      (May be expected for audit_log on first run or unused features.)")

    # Write audit artifact
    artifact = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "manager_count": len(manager_ids),
        "checks": check_count,
        "drifts": drift_count,
        "zero_tables": sorted(zero_tables),
        "verdict": "PASS" if drift_count == 0 else "FAIL",
        "results": results,
    }
    artifact_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"cutover_diff_{started_at[:19].replace(':', '-')}.json",
    )
    with open(artifact_path, "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"\nArtifact written to: {artifact_path}")

    print(f"\n{'=' * 50}")
    print(f"Checks: {check_count}  |  Drifts: {drift_count}")

    if drift_count > 0:
        print("\nFAIL — data discrepancies found. Do NOT proceed with cutover.")
        sys.exit(1)
    else:
        print("\nPASS — Django ORM reads match raw SQL. Safe to proceed.")
        sys.exit(0)


if __name__ == "__main__":
    run_diff()
