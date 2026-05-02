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
