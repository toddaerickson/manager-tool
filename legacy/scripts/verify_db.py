#!/usr/bin/env python3
"""Database smoke test — verifies live connectivity + expected schema.

Uses the same DATABASE_URL resolution as the app (env var or Streamlit secrets).
Exits 0 on success, nonzero on failure. Safe to wire into CI.

Usage:
    python scripts/verify_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402

EXPECTED_TABLES = {
    "action_items", "career_conversations", "coach_suggestions", "config",
    "decisions", "delegations", "development_plans", "events", "feedback",
    "goals", "journal_entries", "managers", "milestones", "running_notes",
    "self_assessments", "skills", "team_members", "users",
}


def main() -> int:
    mode = "Neon PostgreSQL" if database._detect_pg() else "SQLite"
    conn = database.get_connection()
    try:
        cur = conn.cursor()

        cur.execute("SELECT 1 AS ok")
        row = cur.fetchone()
        if row["ok"] != 1:
            print(f"FAIL ({mode}): SELECT 1 returned {row!r}")
            return 1

        if database._detect_pg():
            cur.execute("SELECT tablename AS name FROM pg_tables WHERE schemaname = 'public'")
        else:
            cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        found = {r["name"] for r in cur.fetchall()}
    finally:
        conn.close()

    missing = EXPECTED_TABLES - found
    if missing:
        print(f"FAIL ({mode}): missing tables {sorted(missing)}")
        return 1

    print(f"OK ({mode}): {len(EXPECTED_TABLES)} expected tables present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
