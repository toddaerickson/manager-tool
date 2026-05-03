"""
Database layer for Manager Tool.
Dual-mode: PostgreSQL (Neon) in production, SQLite for local dev.
Set DATABASE_URL env var or Streamlit secret to use PostgreSQL.
"""

from __future__ import annotations

import calendar
import sqlite3
import os
import re
import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("manager_tool.database")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manager_data.db")

# ---------------------------------------------------------------------------
# Sensitive config encryption
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = {"anthropic_api_key", "smtp_password", "google_client_secret"}
_ENC_PREFIX = "enc:"


class EncryptionUnavailableError(RuntimeError):
    """Raised when sensitive config cannot be encrypted or decrypted."""


def _get_fernet():
    """Get a Fernet instance for encrypting/decrypting sensitive config.
    Returns None only if `cryptography` is not installed.

    Production policy (AUDIT M9 / P5): when MANAGER_TOOL_ENV=prod the
    CONFIG_ENCRYPTION_KEY env var is REQUIRED. Auto-generating a key into
    a file next to database.py is convenient for dev laptops but disastrous
    in prod (a tarball of the deploy contains both the key and the
    encrypted DB rows). Outside prod we still auto-generate, but we
    chmod 600 the file immediately so it's not world-readable."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.error(
            "cryptography library is not installed; sensitive config cannot be protected"
        )
        return None

    key = os.environ.get("CONFIG_ENCRYPTION_KEY")
    if not key:
        if _is_production():
            raise EncryptionUnavailableError(
                "MANAGER_TOOL_ENV=prod requires CONFIG_ENCRYPTION_KEY to be "
                "set as an environment variable. Refusing to auto-generate "
                "a key file in production."
            )
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".encryption_key")
        if os.path.exists(key_path):
            with open(key_path, "r") as f:
                key = f.read().strip()
            # Tighten perms even if the file pre-dates this fix — a tar of
            # the source tree shouldn't expose the key.
            try:
                os.chmod(key_path, 0o600)
            except OSError as e:
                logger.warning("Could not chmod %s to 0600: %s", key_path, e)
        else:
            key = Fernet.generate_key().decode()
            with open(key_path, "w") as f:
                f.write(key)
            try:
                os.chmod(key_path, 0o600)
            except OSError as e:
                logger.warning("Could not chmod %s to 0600: %s", key_path, e)
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt_value(value):
    """Encrypt a sensitive string value. Raises EncryptionUnavailableError if
    encryption is not available (refuses to write plaintext for sensitive keys)."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        raise EncryptionUnavailableError(
            "Cannot store sensitive configuration: cryptography is not installed. "
            "Install it via `pip install cryptography`."
        )
    return _ENC_PREFIX + f.encrypt(value.encode()).decode()


def _decrypt_value(value):
    """Decrypt a sensitive string value. Raises EncryptionUnavailableError on
    any decryption failure (refuses to return ciphertext as plaintext)."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    f = _get_fernet()
    if f is None:
        raise EncryptionUnavailableError(
            "Cannot decrypt sensitive configuration: cryptography is not installed."
        )
    try:
        return f.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except Exception as e:
        logger.exception("Failed to decrypt sensitive config value")
        raise EncryptionUnavailableError(
            "Failed to decrypt sensitive config value "
            "(encryption key may have changed or value is corrupt)."
        ) from e

# ---------------------------------------------------------------------------
# Dual-mode connection: PostgreSQL (production) / SQLite (local dev)
# ---------------------------------------------------------------------------

_USE_PG = None


_DB_URL_CRED_RE = re.compile(
    r"(postgres(?:ql)?://)[^/@\s]*@",
    re.IGNORECASE,
)


def _redact_db_credentials(text: str) -> str:
    """Scrub user:password@ from any postgres URL embedded in `text`.
    psycopg2 sometimes echoes the full DSN in its exception messages,
    which would leak the credential into our UI banner (AUDIT M8)."""
    if not text:
        return text
    return _DB_URL_CRED_RE.sub(r"\1***@", text)


def _read_streamlit_secret(key: str, default: str = "") -> str:
    """Best-effort read from Streamlit's secrets.toml — empty string when
    Streamlit isn't installed or no secrets file exists. Logs a debug
    message on unexpected failures so a misconfigured secrets store
    isn't completely silent (P5 / AUDIT M6)."""
    try:
        import streamlit as st  # noqa: F401 — needed for st.secrets attr
    except ImportError:
        return default
    try:
        return st.secrets.get(key, default)
    except FileNotFoundError:
        # Common case in non-Streamlit contexts (CLI, tests) — no secrets
        # file present. Don't warn.
        return default
    except Exception as e:
        logger.debug("Streamlit secrets unavailable for %s: %s", key, e)
        return default


def _detect_pg():
    """Detect if we should use PostgreSQL."""
    global _USE_PG
    if _USE_PG is not None:
        return _USE_PG
    url = os.environ.get("DATABASE_URL", "") or _read_streamlit_secret("DATABASE_URL")
    _USE_PG = bool(url)
    return _USE_PG


def _get_pg_url():
    """Get PostgreSQL connection URL."""
    return os.environ.get("DATABASE_URL", "") or _read_streamlit_secret("DATABASE_URL")


def _q(sql):
    """Convert ? placeholders to %s for PostgreSQL."""
    if _detect_pg():
        return sql.replace("?", "%s")
    return sql


_PG_FAILED = False
_PG_ERROR = ""


def pg_connection_failed() -> tuple[bool, str]:
    """Return (failed: bool, error_msg: str) for UI status display."""
    return _PG_FAILED, _PG_ERROR


class DatabaseUnavailableError(RuntimeError):
    """Raised in production when PostgreSQL is unreachable. The SQLite
    fallback is intentionally disabled in prod so writes do not silently
    land in ephemeral storage that subsequent restarts ignore."""


def _is_production() -> bool:
    """A deploy is production when MANAGER_TOOL_ENV is set to 'prod'.

    Used to enforce stricter handling of secrets (e.g. CONFIG_ENCRYPTION_KEY
    must be supplied via env, not auto-generated to a key file). The choice
    of database backend itself is no longer gated on this — see
    get_connection()."""
    return os.environ.get("MANAGER_TOOL_ENV", "").strip().lower() == "prod"


def get_connection():
    """Get a database connection.

    Backend selection is determined entirely by DATABASE_URL:
      - DATABASE_URL set    → PostgreSQL via psycopg2; failure raises
                              DatabaseUnavailableError (no SQLite fallback).
      - DATABASE_URL unset  → local SQLite at DB_PATH (dev / tests / CI).

    Earlier versions silently fell back to SQLite when DATABASE_URL was set
    but Postgres was unreachable, gated on MANAGER_TOOL_ENV=prod. That
    behaviour is removed: a configured-but-unreachable Postgres always
    fails loud, regardless of environment. Silent fallback split data
    between the SQLite file and the real database — writes during the
    outage were orphaned the moment Postgres returned, and the SQLite
    file was queried as if it held authoritative state.

    Uses direct psycopg2 connections rather than a pool because Neon's
    serverless proxy handles pooling and scales compute to zero after idle
    periods — app-side pools would hold stale connections."""
    global _PG_FAILED, _PG_ERROR
    if _detect_pg():
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(_get_pg_url(), cursor_factory=RealDictCursor)
            conn.autocommit = True
            _PG_FAILED = False
            _PG_ERROR = ""
            return conn
        except Exception as e:
            _PG_FAILED = True
            # Scrub any user:password@ embedded in psycopg2's error message
            # before we store it for UI display (AUDIT M8).
            _PG_ERROR = _redact_db_credentials(str(e))
            logger.exception(
                "PostgreSQL connection failed; DATABASE_URL is set so "
                "refusing to silently fall back to SQLite")
            raise DatabaseUnavailableError(
                "PostgreSQL is unreachable. The SQLite fallback is "
                "intentionally disabled whenever DATABASE_URL is set — "
                "silently switching backends would orphan any writes "
                "made during the outage. Investigate connectivity before "
                f"continuing. Original error: {_PG_ERROR}"
            ) from e
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _connect():
    """Context manager for database connections.
    Closes connections on exit. Protects against connection leaks on exceptions."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _exec(conn, sql, params=None):
    """Execute SQL with automatic placeholder conversion."""
    cur = conn.cursor()
    if params:
        cur.execute(_q(sql), params)
    else:
        cur.execute(_q(sql))
    return cur


def _exec_returning_id(conn, sql, params=None):
    """Execute INSERT and return the new row ID. Handles PG vs SQLite.

    The Postgres path appends ` RETURNING id`. Strip any trailing
    whitespace and semicolon from the caller's SQL first so the
    concatenation produces a syntactically valid statement (AUDIT L11)."""
    if _detect_pg():
        cur = conn.cursor()
        cleaned_sql = _q(sql).rstrip().rstrip(";").rstrip()
        cur.execute(cleaned_sql + " RETURNING id", params or ())
        row = cur.fetchone()
        return row["id"] if row else None
    else:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
        return cur.lastrowid


def _normalize_row(row) -> dict:
    """Convert psycopg2's datetime/date values to ISO strings so callers see
    the same string-typed shape they get from SQLite. Without this, code
    that does `row["created_at"][:10]` or `row["expires_at"] <= some_iso`
    works on SQLite and silently breaks on Postgres."""
    out = dict(row)
    for k, v in out.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, date):
            out[k] = v.isoformat()
    return out


def _fetchone(conn, sql, params=None):
    """Fetch one row as dict."""
    cur = _exec(conn, sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    return _normalize_row(row)


def _fetchall(conn, sql, params=None):
    """Fetch all rows as list of dicts."""
    cur = _exec(conn, sql, params)
    return [_normalize_row(r) for r in cur.fetchall()]


def _commit(conn):
    """Commit for SQLite (PostgreSQL uses autocommit)."""
    if not _detect_pg():
        conn.commit()


def _to_datetime(value):
    """Normalize a TIMESTAMP column value to a datetime.

    psycopg2 returns datetime objects for TIMESTAMP columns, while SQLite
    stores them as ISO-formatted strings. Code that compares or parses
    these values must coerce both shapes; otherwise PG hits a TypeError
    (datetime vs str) where SQLite quietly works."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# SQL dialect helpers — generate correct SQL for SQLite or PostgreSQL
# ---------------------------------------------------------------------------

def _sql_now():
    """SQL expression for current timestamp."""
    return "NOW()" if _detect_pg() else "datetime('now')"


def _sql_current_date():
    """SQL expression for current date (as text, for comparison with TEXT date columns)."""
    return "CURRENT_DATE::text" if _detect_pg() else "date('now')"


def _sql_days_since(date_col):
    """SQL expression: integer days between now and a date column (TEXT stored as YYYY-MM-DD)."""
    if _detect_pg():
        return f"EXTRACT(DAY FROM NOW() - {date_col}::date)::int"
    return f"CAST(julianday('now') - julianday({date_col}) AS INTEGER)"


def _sql_date_offset(days_param):
    """SQL expression: date N days ago (as text, for comparison with TEXT date columns)."""
    if _detect_pg():
        return f"(CURRENT_DATE - ({days_param} || ' days')::interval)::date::text"
    return f"date('now', {days_param} || ' days')"


def _sql_month(date_col):
    """SQL expression: extract YYYY-MM from a date column."""
    if _detect_pg():
        return f"TO_CHAR({date_col}::date, 'YYYY-MM')"
    return f"strftime('%Y-%m', {date_col})"


def _sql_week(date_col):
    """SQL expression: extract YYYY-WW from a date column."""
    if _detect_pg():
        return f"TO_CHAR({date_col}::date, 'IYYY-IW')"
    return f"strftime('%Y-%W', {date_col})"


def _sql_left(col, n):
    """SQL expression: left N characters of a string."""
    if _detect_pg():
        return f"LEFT({col}, {n})"
    return f"substr({col}, 1, {n})"


def _sql_date_of_timestamp(col):
    """SQL expression: extract the date portion of a TIMESTAMP column as a
    'YYYY-MM-DD' text value. PG's LEFT() rejects TIMESTAMP arguments, so
    string-slicing a timestamp the SQLite way (substr) needs an explicit
    date cast on Postgres."""
    if _detect_pg():
        return f"({col})::date::text"
    return f"substr({col}, 1, 10)"


# ---------------------------------------------------------------------------
# Recurrence + transactional materialization helpers (PR 4)
# ---------------------------------------------------------------------------
#
# These exist so the Schedule Event form can write a parent + N children
# atomically across both backends. The naïve "loop _exec_returning_id" approach
# is broken on both:
#   - PG sets conn.autocommit = True (database.py:227); per-statement transactions
#     mean conn.rollback() after a failed children-insert does NOT undo the parent.
#   - SQLite path of _exec_returning_id calls conn.commit() (database.py:288);
#     the parent commits before children even start.
# Round-3 plan review caught this; the helpers below bypass both paths.

_MATERIALIZE_MAX_CHILDREN = 32  # Hard cap. Form's natural max is 12 (weekly).


def _materialize_in_txn(conn, parent_sql, parent_params,
                       children_sql, children_rows):
    """Insert one parent + N children atomically and return the parent's id.

    PG: explicit BEGIN/COMMIT/ROLLBACK on the cursor — bypasses the
        connection's autocommit=True so the two INSERTs share a transaction.
    SQLite: sqlite3 connections start in "deferred" transaction mode; we
        avoid _exec_returning_id (which auto-commits) by going to the raw
        cursor and using cur.lastrowid. _commit / rollback are issued on
        the connection at the end.

    Both paths land both INSERTs or neither — no orphan parents.

    Children rows are tuples WITHOUT the parent_event_id slot; this helper
    appends parent_id as the last column of each tuple before executemany.
    children_sql must therefore have placeholders for that trailing column.

    Caps len(children_rows) at _MATERIALIZE_MAX_CHILDREN — defense-in-depth
    against a crafted POST attempting to fan out further than the form's
    natural ceiling."""
    if len(children_rows) > _MATERIALIZE_MAX_CHILDREN:
        raise ValueError(
            f"refusing to materialize {len(children_rows)} children "
            f"(cap={_MATERIALIZE_MAX_CHILDREN})")

    cur = conn.cursor()
    is_pg = _detect_pg()
    if is_pg:
        cur.execute("BEGIN")
    try:
        if is_pg:
            cleaned = _q(parent_sql).rstrip().rstrip(";").rstrip()
            cur.execute(cleaned + " RETURNING id", parent_params)
            row = cur.fetchone()
            parent_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cur.execute(parent_sql, parent_params)
            parent_id = cur.lastrowid
        full_rows = [(*row, parent_id) for row in children_rows]
        if full_rows:
            cur.executemany(_q(children_sql), full_rows)
        if is_pg:
            cur.execute("COMMIT")
        else:
            conn.commit()
        return parent_id
    except Exception:
        if is_pg:
            cur.execute("ROLLBACK")
        else:
            conn.rollback()
        raise


def _add_months_anchored(start: date, n: int) -> date:
    """Return start + n months, clamped to last day if the target month
    is shorter. Iterates from `start` (Algo A) so the cadence stays
    anchored:  Jan 31 + 1mo = Feb 28, + 2mo = Mar 31 (anchor preserved).
    NOT Algo B (advance from previous-clamped) which drifts permanently:
       Jan 31 + 1mo = Feb 28, + 1mo + 1mo = Mar 28 (anchor lost)."""
    if n == 0:
        return start
    y, m = start.year, start.month - 1 + n
    y += m // 12
    m = m % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(start.day, last_day))


# ---------------------------------------------------------------------------
# 1:1 session lock contract
# ---------------------------------------------------------------------------
#
# A 1:1 session record is editable for exactly LOCK_WINDOW after `created_at`,
# then immutable. The lock is computed on read from `created_at` (no
# `locked_at` column) — it's a deterministic function of an existing column
# and denormalizing would invite drift on backfilled imports. Composes
# cleanly with future Django (`@property`).

LOCK_WINDOW = timedelta(hours=24)


def is_session_locked(created_at, now=None) -> bool:
    """True when a 1:1 session can no longer be edited.

    `now` is injectable so the lock contract is unit-testable without
    `freezegun`. Default: server clock in UTC.

    Boundary contract: `>=` — a session created exactly LOCK_WINDOW ago
    is locked. Off-by-one risk on the boundary is real on PG (microsecond
    precision) vs SQLite (second precision); tests cover both sides.

    `created_at` may be a `datetime` (PG psycopg2 with TIMESTAMPTZ) or an
    ISO string (post-`_normalize_row` SQLite path); coerce to datetime.
    Naive datetimes are interpreted as server UTC."""
    if not created_at:
        return False
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(tz=timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - created_at) >= LOCK_WINDOW


# ---------------------------------------------------------------------------
# Migration runner (P2.1)
#
# A small homegrown migration system: each entry in `_MIGRATIONS` is a
# (id, fn) pair. Every startup, `_run_migrations(conn)` reads
# `schema_migrations` and applies any whose id is not yet recorded. Every
# migration function is itself idempotent (it inspects schema before mutating)
# so it's safe even if the ledger row is missing or hand-cleared. Works for
# SQLite and Postgres uniformly via _detect_pg() + helpers.
# ---------------------------------------------------------------------------


def _table_columns(conn, table: str) -> list[str]:
    """Return list of column names for a table on either dialect."""
    if _detect_pg():
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s",
            (table,),
        )
        return [r["column_name"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [c[1] for c in cur.fetchall()]


def _migration_journal_coaching_response(conn) -> None:
    """Add coaching_response column to journal_entries if missing."""
    cols = _table_columns(conn, "journal_entries")
    if "coaching_response" in cols:
        return
    _exec(conn, "ALTER TABLE journal_entries ADD COLUMN coaching_response TEXT")
    _commit(conn)


def _migration_orphan_table_manager_id(conn) -> None:
    """P1.1: add manager_id to feedback / goals / career_conversations / skills /
    development_plans / milestones; backfill from parent."""
    for table in ("feedback", "goals", "career_conversations", "skills",
                  "development_plans", "milestones"):
        cols = _table_columns(conn, table)
        if "manager_id" not in cols:
            _exec(conn, f"ALTER TABLE {table} ADD COLUMN manager_id INTEGER")
            _commit(conn)
    # Backfill (idempotent; cheap once all rows are populated)
    for table in ("feedback", "goals", "career_conversations", "skills",
                  "development_plans"):
        _exec(conn,
            f"UPDATE {table} SET manager_id = ("
            "SELECT manager_id FROM team_members WHERE team_members.id = "
            f"{table}.team_member_id"
            ") WHERE manager_id IS NULL"
        )
    _exec(conn,
        "UPDATE milestones SET manager_id = ("
        "SELECT manager_id FROM development_plans "
        "WHERE development_plans.id = milestones.plan_id"
        ") WHERE manager_id IS NULL"
    )
    _commit(conn)


def _migration_partition_config_table(conn) -> None:
    """P1.3: replace single-key PK on config with composite (manager_id, key).
    Reassign legacy rows: system keys → SYSTEM_MANAGER_ID, others → sole manager
    if exactly one exists, else SYSTEM_MANAGER_ID."""
    cols = _table_columns(conn, "config")
    if "manager_id" in cols:
        return

    cur = _exec(conn, "SELECT id FROM managers")
    rows = cur.fetchall()
    if len(rows) == 1:
        first = rows[0]
        sole_mid = first["id"] if isinstance(first, dict) else first[0]
    else:
        sole_mid = SYSTEM_MANAGER_ID

    _exec(conn,
        "CREATE TABLE config_new ("
        "  manager_id INTEGER NOT NULL DEFAULT 0,"
        "  key TEXT NOT NULL,"
        "  value TEXT,"
        "  PRIMARY KEY (manager_id, key))"
    )
    cur = _exec(conn, "SELECT key, value FROM config")
    old_rows = cur.fetchall()
    for r in old_rows:
        k = r["key"] if isinstance(r, dict) else r[0]
        v = r["value"] if isinstance(r, dict) else r[1]
        target_mid = SYSTEM_MANAGER_ID if _is_system_key(k) else sole_mid
        _exec(conn,
            "INSERT INTO config_new (manager_id, key, value) VALUES (?, ?, ?)",
            (target_mid, k, v))
    _exec(conn, "DROP TABLE config")
    _exec(conn, "ALTER TABLE config_new RENAME TO config")
    _commit(conn)
    logger.info("config table partitioned by manager_id "
                "(%d rows reassigned; sole_mid=%s)", len(old_rows), sole_mid)


def _migration_sole_manager_backfill(conn) -> None:
    """One-shot backfill: assign orphaned rows in scoped tables to the sole
    manager. Only runs when exactly one manager exists; otherwise no-op."""
    cur = _exec(conn, "SELECT id FROM managers")
    rows = cur.fetchall()
    if len(rows) != 1:
        return
    first = rows[0]
    mid = first["id"] if isinstance(first, dict) else first[0]
    for table in ("team_members", "events", "action_items",
                  "journal_entries", "self_assessments"):
        _exec(conn,
            f"UPDATE {table} SET manager_id = ? WHERE manager_id IS NULL",
            (mid,))
    _commit(conn)


def _migration_save_uniqueness_constraints(conn) -> None:
    """P2.6: add UNIQUE indexes that let save_self_assessment and
    save_coach_suggestion use INSERT ... ON CONFLICT DO UPDATE atomically
    instead of the race-prone DELETE-then-INSERT pattern under autocommit.

    Dedup pre-existing duplicates first (keep the highest-id row per group),
    otherwise the unique index creation fails."""
    # coach_suggestions: one row per (manager_id, suggestion_date, tier)
    _exec(conn,
        "DELETE FROM coach_suggestions "
        "WHERE id NOT IN ("
        "  SELECT MAX(id) FROM coach_suggestions "
        "  GROUP BY manager_id, suggestion_date, tier"
        ")"
    )
    _exec(conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_suggestions_mid_date_tier "
        "ON coach_suggestions (manager_id, suggestion_date, tier)"
    )
    # self_assessments: one row per (manager_id, week_date, dimension)
    _exec(conn,
        "DELETE FROM self_assessments "
        "WHERE id NOT IN ("
        "  SELECT MAX(id) FROM self_assessments "
        "  GROUP BY manager_id, week_date, dimension"
        ")"
    )
    _exec(conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_self_assessments_mid_week_dim "
        "ON self_assessments (manager_id, week_date, dimension)"
    )
    _commit(conn)


def _migration_sessions_and_login_attempts(conn) -> None:
    """P2.3: add server-side sessions + persistent login_attempts tables for
    deployments that pre-date schema_postgres.sql carrying these tables."""
    # sessions
    if _detect_pg():
        _exec(conn,
            "CREATE TABLE IF NOT EXISTS sessions ("
            "  id TEXT PRIMARY KEY,"
            "  manager_id INTEGER NOT NULL REFERENCES managers(id),"
            "  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            "  last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            "  expires_at TIMESTAMP NOT NULL,"
            "  user_agent_hash TEXT)"
        )
        _exec(conn,
            "CREATE TABLE IF NOT EXISTS login_attempts ("
            "  username TEXT PRIMARY KEY,"
            "  failed_count INTEGER NOT NULL DEFAULT 0,"
            "  last_attempt_at TIMESTAMP NOT NULL,"
            "  locked_until TIMESTAMP)"
        )
    else:
        _exec(conn,
            "CREATE TABLE IF NOT EXISTS sessions ("
            "  id TEXT PRIMARY KEY,"
            "  manager_id INTEGER NOT NULL,"
            "  created_at TEXT DEFAULT (datetime('now')),"
            "  last_seen TEXT DEFAULT (datetime('now')),"
            "  expires_at TEXT NOT NULL,"
            "  user_agent_hash TEXT,"
            "  FOREIGN KEY (manager_id) REFERENCES managers(id))"
        )
        _exec(conn,
            "CREATE TABLE IF NOT EXISTS login_attempts ("
            "  username TEXT PRIMARY KEY,"
            "  failed_count INTEGER NOT NULL DEFAULT 0,"
            "  last_attempt_at TEXT NOT NULL,"
            "  locked_until TEXT)"
        )
    _commit(conn)


def _migration_goals_target_date(conn) -> None:
    """P-Upcoming.A: add goals.target_date so the Upcoming aggregator can
    surface goals approaching their deadline. Existing rows stay NULL and
    don't appear in Upcoming until a deadline is set. Idempotent via
    `_table_columns` check + `CREATE INDEX IF NOT EXISTS`."""
    cols = _table_columns(conn, "goals")
    if "target_date" not in cols:
        _exec(conn, "ALTER TABLE goals ADD COLUMN target_date TEXT")
        _commit(conn)
    _exec(conn,
        "CREATE INDEX IF NOT EXISTS ix_goals_manager_target "
        "ON goals (manager_id, target_date)")
    _commit(conn)


def _migration_events_recurrence(conn) -> None:
    """PR 4: recurring events. Adds three columns to `events`:
      - recurrence_rule TEXT NULL
          One of 'weekly' / 'monthly' / 'quarterly' / NULL.
      - parent_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL
          Self-referential FK for joinable lineage between a recurring
          parent and its materialized children. Round-3 plan review caught
          that without ON DELETE SET NULL, deleting a parent fails with a
          confusing FK error; SET NULL makes children survive standalone.
      - recurrence_warned_at TEXT NULL
          ISO timestamp set when the expiry banner has been shown for a
          series so the dashboard doesn't spam warnings on every render.

    Plus two indexes:
      - ix_events_parent (parent_event_id) — speeds FK SET-NULL cascades
        and 'find children of parent X' queries.
      - ix_events_manager_parent (manager_id, parent_event_id) WHERE
        parent_event_id IS NOT NULL — composite for the expiry-warning
        query in next_step_for. The single-column ix_events_parent does
        NOT cover `WHERE manager_id=? AND parent_event_id IS NOT NULL`.
        On SQLite, partial-index WHERE is dropped (full composite still
        works as a B-tree)."""
    cols = _table_columns(conn, "events")
    if "recurrence_rule" not in cols:
        _exec(conn, "ALTER TABLE events ADD COLUMN recurrence_rule TEXT")
        _commit(conn)
    if "parent_event_id" not in cols:
        if _detect_pg():
            _exec(conn,
                "ALTER TABLE events ADD COLUMN parent_event_id INTEGER "
                "REFERENCES events(id) ON DELETE SET NULL")
        else:
            # SQLite cannot ALTER TABLE ADD COLUMN with a REFERENCES clause
            # against a populated table; the FK is non-load-bearing for the
            # contract (children are independent at creation time) and SQLite
            # FK enforcement is off by default in this codebase. Add the
            # column without the constraint.
            _exec(conn,
                "ALTER TABLE events ADD COLUMN parent_event_id INTEGER")
        _commit(conn)
    if "recurrence_warned_at" not in cols:
        _exec(conn, "ALTER TABLE events ADD COLUMN recurrence_warned_at TEXT")
        _commit(conn)
    _exec(conn,
        "CREATE INDEX IF NOT EXISTS ix_events_parent "
        "ON events (parent_event_id)")
    if _detect_pg():
        _exec(conn,
            "CREATE INDEX IF NOT EXISTS ix_events_manager_parent "
            "ON events (manager_id, parent_event_id) "
            "WHERE parent_event_id IS NOT NULL")
    else:
        _exec(conn,
            "CREATE INDEX IF NOT EXISTS ix_events_manager_parent "
            "ON events (manager_id, parent_event_id)")
    _commit(conn)


def _migration_one_on_one_sessions(conn) -> None:
    """PR `db/one-on-one-sessions`: add `one_on_one_sessions` table for the
    1:1 Meeting page. Schema dual-written across schema_postgres.sql and the
    SQLite block in this file; this migration brings existing deploys forward.

    Idempotent via `CREATE TABLE IF NOT EXISTS`. The UNIQUE constraint on
    `(manager_id, team_member_id, session_date)` is part of the table DDL
    so repeat saves UPSERT instead of duplicating. The lock contract is
    computed on read from `created_at` (no `locked_at` column)."""
    if _detect_pg():
        _exec(conn,
            "CREATE TABLE IF NOT EXISTS one_on_one_sessions ("
            "  id SERIAL PRIMARY KEY,"
            "  manager_id INTEGER NOT NULL REFERENCES managers(id),"
            "  team_member_id INTEGER NOT NULL REFERENCES team_members(id),"
            "  event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,"
            "  session_date TEXT NOT NULL,"
            "  direct_notes TEXT,"
            "  manager_notes TEXT,"
            "  followup_notes TEXT,"
            "  created_at TIMESTAMPTZ DEFAULT NOW(),"
            "  updated_at TIMESTAMPTZ DEFAULT NOW(),"
            "  UNIQUE (manager_id, team_member_id, session_date)"
            ")")
    else:
        _exec(conn,
            "CREATE TABLE IF NOT EXISTS one_on_one_sessions ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  manager_id INTEGER NOT NULL,"
            "  team_member_id INTEGER NOT NULL,"
            "  event_id INTEGER,"
            "  session_date TEXT NOT NULL,"
            "  direct_notes TEXT,"
            "  manager_notes TEXT,"
            "  followup_notes TEXT,"
            "  created_at TEXT DEFAULT (datetime('now')),"
            "  updated_at TEXT DEFAULT (datetime('now')),"
            "  FOREIGN KEY (manager_id) REFERENCES managers(id),"
            "  FOREIGN KEY (team_member_id) REFERENCES team_members(id),"
            "  FOREIGN KEY (event_id) REFERENCES events(id),"
            "  UNIQUE (manager_id, team_member_id, session_date)"
            ")")
    _exec(conn,
        "CREATE INDEX IF NOT EXISTS ix_one_on_one_sessions_member_date "
        "ON one_on_one_sessions (team_member_id, session_date DESC)")
    _exec(conn,
        "CREATE INDEX IF NOT EXISTS ix_one_on_one_sessions_manager "
        "ON one_on_one_sessions (manager_id)")
    _commit(conn)


def _migration_hot_path_indexes(conn) -> None:
    """P4.1: btree indexes on hot WHERE columns (AUDIT M5). Idempotent via
    `IF NOT EXISTS`. Operators on Postgres prod should consider running
    `CREATE INDEX CONCURRENTLY` manually for zero-downtime, but this
    migration is safe to run even on small/medium tables."""
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_events_manager_date_status "
        "ON events (manager_id, scheduled_date, status)",

        "CREATE INDEX IF NOT EXISTS ix_action_items_manager_status_due "
        "ON action_items (manager_id, status, due_date)",

        "CREATE INDEX IF NOT EXISTS ix_journal_entries_manager_date "
        "ON journal_entries (manager_id, entry_date)",

        "CREATE INDEX IF NOT EXISTS ix_feedback_member_created "
        "ON feedback (team_member_id, created_at)",

        "CREATE INDEX IF NOT EXISTS ix_team_members_manager "
        "ON team_members (manager_id)",

        "CREATE INDEX IF NOT EXISTS ix_running_notes_member_date "
        "ON running_notes (team_member_id, note_date)",

        "CREATE INDEX IF NOT EXISTS ix_delegations_manager_status_checkin "
        "ON delegations (manager_id, status, check_in_date)",

        "CREATE INDEX IF NOT EXISTS ix_coach_suggestions_manager_date "
        "ON coach_suggestions (manager_id, suggestion_date)",
    ]
    for sql in statements:
        _exec(conn, sql)
    _commit(conn)


_MIGRATIONS: list[tuple[str, Any]] = [
    ("0001_journal_coaching_response", _migration_journal_coaching_response),
    ("0002_orphan_table_manager_id", _migration_orphan_table_manager_id),
    ("0003_partition_config_table", _migration_partition_config_table),
    ("0004_sole_manager_backfill", _migration_sole_manager_backfill),
    ("0005_sessions_and_login_attempts", _migration_sessions_and_login_attempts),
    ("0006_save_uniqueness_constraints", _migration_save_uniqueness_constraints),
    ("0007_hot_path_indexes", _migration_hot_path_indexes),
    ("0008_goals_target_date", _migration_goals_target_date),
    ("0009_events_recurrence", _migration_events_recurrence),
    ("0010_one_on_one_sessions", _migration_one_on_one_sessions),
]


def _ensure_schema_migrations_table(conn) -> None:
    """Make sure schema_migrations exists (Postgres bootstraps via
    schema_postgres.sql; this handles older deploys that haven't run it
    against the latest schema yet)."""
    if _detect_pg():
        _exec(conn,
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  id TEXT PRIMARY KEY,"
            "  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
    else:
        _exec(conn,
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  id TEXT PRIMARY KEY,"
            "  applied_at TEXT DEFAULT (datetime('now')))"
        )
    _commit(conn)


def _run_migrations(conn) -> list[str]:
    """Apply any migrations not yet recorded in schema_migrations.
    Returns the list of migration ids applied this run."""
    _ensure_schema_migrations_table(conn)
    cur = _exec(conn, "SELECT id FROM schema_migrations")
    applied = {r["id"] if isinstance(r, dict) else r[0] for r in cur.fetchall()}

    newly_applied: list[str] = []
    for mid, fn in _MIGRATIONS:
        if mid in applied:
            continue
        try:
            fn(conn)
        except Exception as e:
            logger.exception("Migration %s failed: %s", mid, e)
            raise
        _exec(conn,
            "INSERT INTO schema_migrations (id) VALUES (?)", (mid,))
        _commit(conn)
        newly_applied.append(mid)
        logger.info("Applied migration %s", mid)
    return newly_applied


_INIT_DB_DONE = False


def init_db(*, force: bool = False):
    """Initialize database tables and apply pending migrations.

    SQLite: creates tables if missing, then runs migrations.
    PostgreSQL: assumes schema_postgres.sql ran at bootstrap; only runs
    migrations to bring an older deploy forward.

    Streamlit re-runs the script top-to-bottom on every interaction. Without
    a guard, every render re-executes init_db's PRAGMA checks and migration-
    ledger reads, which is wasted work. We skip after the first successful
    call per process (P4.2 / AUDIT M4). Pass force=True to override (used
    by tests that monkeypatch DB_PATH per case)."""
    global _INIT_DB_DONE
    if _INIT_DB_DONE and not force:
        return
    if _detect_pg():
        with _connect() as conn:
            _run_migrations(conn)
        _INIT_DB_DONE = True
        return
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            info = conn.execute("PRAGMA table_info(journal_entries)").fetchall()
            conn.close()
            for col in info:
                if col[1] == "manager_id" and col[3] == 1:  # notnull=1
                    raise RuntimeError(
                        f"Detected legacy schema in {DB_PATH}: "
                        "journal_entries.manager_id is NOT NULL. "
                        "Refusing to auto-migrate (would destroy data). "
                        "Back up the file, then run a manual migration to make manager_id nullable."
                    )
        except sqlite3.Error as e:
            logger.warning("Schema migration check failed: %s", e)
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS managers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            email TEXT,
            password_hash TEXT NOT NULL,
            work_schedule TEXT DEFAULT '{"days": ["Mon","Tue","Wed","Thu","Fri"], "start": "09:00", "end": "17:00"}',
            timezone TEXT DEFAULT 'America/New_York',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            manager_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            last_seen TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            user_agent_hash TEXT,
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            username TEXT PRIMARY KEY,
            failed_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TEXT NOT NULL,
            locked_until TEXT
        );

        CREATE TABLE IF NOT EXISTS team_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            name TEXT NOT NULL,
            email TEXT,
            role TEXT,
            start_date TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            title TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN
                ('check_in', 'coaching', 'one_on_one', 'quarterly_review', 'other')),
            team_member_id INTEGER,
            scheduled_date TEXT NOT NULL,
            scheduled_time TEXT NOT NULL,
            duration_minutes INTEGER DEFAULT 30,
            location TEXT,
            agenda TEXT,
            status TEXT DEFAULT 'scheduled' CHECK(status IN
                ('scheduled', 'completed', 'cancelled', 'rescheduled')),
            notes TEXT,
            calendar_invite_sent INTEGER DEFAULT 0,
            recurrence_rule TEXT,
            parent_event_id INTEGER,
            recurrence_warned_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id),
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        );

        CREATE TABLE IF NOT EXISTS action_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            event_id INTEGER,
            description TEXT NOT NULL,
            assignee TEXT,
            due_date TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN
                ('pending', 'in_progress', 'completed')),
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (event_id) REFERENCES events(id),
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            team_member_id INTEGER NOT NULL,
            event_id INTEGER,
            feedback_type TEXT NOT NULL CHECK(feedback_type IN
                ('positive', 'constructive')),
            situation TEXT,
            behavior TEXT,
            impact TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id),
            FOREIGN KEY (event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            team_member_id INTEGER NOT NULL,
            quarter TEXT NOT NULL,
            description TEXT NOT NULL,
            key_results TEXT,
            target_date TEXT,
            status TEXT DEFAULT 'not_started' CHECK(status IN
                ('not_started', 'in_progress', 'met', 'exceeded',
                 'partially_met', 'not_met')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS config (
            manager_id INTEGER NOT NULL DEFAULT 0,
            key TEXT NOT NULL,
            value TEXT,
            PRIMARY KEY (manager_id, key)
        );

        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            entry_date TEXT NOT NULL,
            entry_type TEXT NOT NULL DEFAULT 'daily'
                CHECK(entry_type IN ('daily', 'weekly', 'reflection')),
            content TEXT,
            mood INTEGER CHECK(mood BETWEEN 1 AND 5),
            energy INTEGER CHECK(energy BETWEEN 1 AND 5),
            private_notes TEXT,
            tags TEXT,
            coaching_response TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        );

        CREATE TABLE IF NOT EXISTS self_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            week_date TEXT NOT NULL,
            dimension TEXT NOT NULL,
            score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        );

        CREATE TABLE IF NOT EXISTS career_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            team_member_id INTEGER NOT NULL,
            conversation_date TEXT NOT NULL,
            topic TEXT,
            notes TEXT,
            next_steps TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            team_member_id INTEGER NOT NULL,
            skill_name TEXT NOT NULL,
            proficiency TEXT DEFAULT 'developing'
                CHECK(proficiency IN ('learning', 'developing', 'proficient', 'expert')),
            is_strength INTEGER DEFAULT 0,
            is_growth_area INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS development_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            team_member_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            target_date TEXT,
            status TEXT DEFAULT 'active'
                CHECK(status IN ('active', 'completed', 'paused')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            plan_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            target_date TEXT,
            completed INTEGER DEFAULT 0,
            completed_at TEXT,
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (plan_id) REFERENCES development_plans(id)
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            name TEXT,
            picture TEXT,
            last_login TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS delegations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            team_member_id INTEGER,
            task TEXT NOT NULL,
            outcome_expected TEXT,
            autonomy_level TEXT DEFAULT 'guided' CHECK(autonomy_level IN
                ('directed', 'guided', 'autonomous')),
            check_in_date TEXT,
            status TEXT DEFAULT 'active' CHECK(status IN
                ('active', 'completed', 'revoked', 'stalled')),
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS running_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            team_member_id INTEGER NOT NULL,
            note_date TEXT NOT NULL DEFAULT (date('now')),
            content TEXT NOT NULL,
            category TEXT DEFAULT 'general' CHECK(category IN
                ('general', 'meeting_prep', 'observation', 'follow_up', 'praise')),
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS one_on_one_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER NOT NULL,
            team_member_id INTEGER NOT NULL,
            event_id INTEGER,
            session_date TEXT NOT NULL,
            direct_notes TEXT,
            manager_notes TEXT,
            followup_notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id),
            FOREIGN KEY (event_id) REFERENCES events(id),
            UNIQUE (manager_id, team_member_id, session_date)
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            title TEXT NOT NULL,
            context TEXT,
            alternatives TEXT,
            rationale TEXT,
            expected_outcome TEXT,
            review_date TEXT,
            status TEXT DEFAULT 'active' CHECK(status IN
                ('active', 'validated', 'revised', 'reversed')),
            actual_outcome TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        );

        CREATE TABLE IF NOT EXISTS coach_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            suggestion_date TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'rule' CHECK(tier IN ('rule', 'ai')),
            suggestion TEXT NOT NULL,
            action_page TEXT,
            dismissed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_suggestions_mid_date_tier
            ON coach_suggestions (manager_id, suggestion_date, tier);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_self_assessments_mid_week_dim
            ON self_assessments (manager_id, week_date, dimension);

        CREATE INDEX IF NOT EXISTS ix_events_manager_date_status
            ON events (manager_id, scheduled_date, status);
        CREATE INDEX IF NOT EXISTS ix_action_items_manager_status_due
            ON action_items (manager_id, status, due_date);
        CREATE INDEX IF NOT EXISTS ix_journal_entries_manager_date
            ON journal_entries (manager_id, entry_date);
        CREATE INDEX IF NOT EXISTS ix_feedback_member_created
            ON feedback (team_member_id, created_at);
        CREATE INDEX IF NOT EXISTS ix_team_members_manager
            ON team_members (manager_id);
        CREATE INDEX IF NOT EXISTS ix_running_notes_member_date
            ON running_notes (team_member_id, note_date);
        CREATE INDEX IF NOT EXISTS ix_delegations_manager_status_checkin
            ON delegations (manager_id, status, check_in_date);
        CREATE INDEX IF NOT EXISTS ix_coach_suggestions_manager_date
            ON coach_suggestions (manager_id, suggestion_date);
        CREATE INDEX IF NOT EXISTS ix_goals_manager_target
            ON goals (manager_id, target_date);
        CREATE INDEX IF NOT EXISTS ix_events_parent
            ON events (parent_event_id);
        CREATE INDEX IF NOT EXISTS ix_events_manager_parent
            ON events (manager_id, parent_event_id);
        CREATE INDEX IF NOT EXISTS ix_one_on_one_sessions_member_date
            ON one_on_one_sessions (team_member_id, session_date DESC);
        CREATE INDEX IF NOT EXISTS ix_one_on_one_sessions_manager
            ON one_on_one_sessions (manager_id);
        """)
        conn.commit()

        _run_migrations(conn)

    _INIT_DB_DONE = True


# ---------------------------------------------------------------------------
# Manager Profiles & Authentication
# ---------------------------------------------------------------------------

def _hash_password(password):
    """Hash a password using bcrypt."""
    import bcrypt
    # Pin rounds=12 explicitly (AUDIT L2). Default has shifted across
    # bcrypt versions; pinning makes hash cost deterministic across
    # deploys and avoids silent CPU-cost drift on library upgrades.
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_password(password, stored_hash):
    """Verify password against stored hash. Supports bcrypt and legacy SHA-256."""
    import bcrypt
    # Legacy SHA-256 detection: exactly 64 hex characters
    if len(stored_hash) == 64:
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest() == stored_hash
    # bcrypt verification
    try:
        return bcrypt.checkpw(password.encode(), stored_hash.encode())
    except Exception as e:
        logger.error("Password verification error: %s", e)
        return False


def create_manager(username: str, display_name: str, password: str, email: str | None = None,
                   work_schedule: str | None = None, timezone: str | None = None) -> int | None:
    """Create a new manager account. Returns manager_id, or None on failure
    (e.g., username already taken, DB error)."""
    pw_hash = _hash_password(password)
    try:
        with _connect() as conn:
            mid = _exec_returning_id(
                conn,
                "INSERT INTO managers (username, display_name, email, password_hash, "
                "work_schedule, timezone) VALUES (?, ?, ?, ?, ?, ?)",
                (username.lower().strip(), display_name, email, pw_hash,
                 work_schedule, timezone),
            )
            _commit(conn)
            return mid
    except Exception as e:
        logger.error("Failed to create manager '%s': %s", username, e)
        return None


def authenticate_manager(username: str, password: str) -> dict[str, Any] | None:
    """Verify credentials. Returns manager dict or None.
    Automatically migrates legacy SHA-256 hashes to bcrypt on successful login."""
    with _connect() as conn:
        row = _fetchone(
            conn,
            "SELECT * FROM managers WHERE username = ?",
            (username.lower().strip(),),
        )
        if not row:
            return None
        if not _verify_password(password, row["password_hash"]):
            return None
        # Migrate legacy SHA-256 hash to bcrypt on successful login
        if len(row["password_hash"]) == 64:
            new_hash = _hash_password(password)
            _exec(conn, "UPDATE managers SET password_hash = ? WHERE id = ?",
                  (new_hash, row["id"]))
            _commit(conn)
    return row


def get_manager(manager_id: int) -> dict[str, Any] | None:
    """Get manager profile by ID."""
    with _connect() as conn:
        row = _fetchone(conn, "SELECT * FROM managers WHERE id = ?", (manager_id,))
    return row


def update_manager(manager_id: int, **kwargs: Any) -> None:
    """Update manager profile fields."""
    allowed = {"display_name", "email", "work_schedule", "timezone"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        _exec(conn, f"UPDATE managers SET {set_clause} WHERE id = ?",
              (*fields.values(), manager_id))
        _commit(conn)


class IncorrectPasswordError(ValueError):
    """Raised when a password change is attempted with the wrong current password."""


def update_manager_password(manager_id: int, old_password: str, new_password: str) -> None:
    """Change manager password after verifying the current one.

    Raises IncorrectPasswordError if old_password does not match, or if the
    manager does not exist. This guard prevents account takeover from any code
    path that exposes update_manager_password with a caller-supplied manager_id.
    """
    with _connect() as conn:
        row = _fetchone(conn, "SELECT password_hash FROM managers WHERE id = ?", (manager_id,))
        if not row or not _verify_password(old_password, row["password_hash"]):
            raise IncorrectPasswordError("Current password is incorrect")
        pw_hash = _hash_password(new_password)
        _exec(conn, "UPDATE managers SET password_hash = ?, updated_at = ? WHERE id = ?",
              (pw_hash, datetime.now().isoformat(), manager_id))
        _commit(conn)


def manager_exists(username: str) -> bool:
    """Check if a username is taken."""
    with _connect() as conn:
        row = _fetchone(conn, "SELECT id FROM managers WHERE username = ?",
                        (username.lower().strip(),))
    return row is not None


# ---------------------------------------------------------------------------
# Sessions & rate-limiting (P2.3 / AUDIT H2 + H3)
# ---------------------------------------------------------------------------

import secrets
import hashlib

SESSION_DEFAULT_TTL_SECONDS = 12 * 3600  # 12 hours
LOGIN_FAIL_THRESHOLD = 5
LOGIN_LOCKOUT_BASE_SECONDS = 60  # First lockout window after threshold
LOGIN_LOCKOUT_MAX_SECONDS = 24 * 3600  # Cap lockout at 24 hours


def hash_user_agent(user_agent: str | None) -> str | None:
    """Stable hash for binding sessions to the originating User-Agent string."""
    if not user_agent:
        return None
    return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()


def create_session(manager_id: int, ttl_seconds: int = SESSION_DEFAULT_TTL_SECONDS,
                   user_agent_hash: str | None = None) -> str:
    """Create a server-side session and return the opaque token to store
    client-side. Token is 32 bytes of cryptographic randomness, URL-safe."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(seconds=ttl_seconds)).isoformat()
    with _connect() as conn:
        _exec(conn,
              "INSERT INTO sessions (id, manager_id, expires_at, user_agent_hash) "
              "VALUES (?, ?, ?, ?)",
              (token, manager_id, expires_at, user_agent_hash))
        _commit(conn)
    return token


def validate_session(token: str, user_agent_hash: str | None = None) -> int | None:
    """Validate a session token and refresh last_seen. Returns manager_id, or
    None if the token is missing, expired, or its UA hash mismatches."""
    if not token:
        return None
    now = datetime.now()
    with _connect() as conn:
        row = _fetchone(conn,
                        "SELECT manager_id, expires_at, user_agent_hash "
                        "FROM sessions WHERE id = ?",
                        (token,))
        if not row:
            return None
        if _to_datetime(row["expires_at"]) <= now:
            # Expired: clean up and reject.
            _exec(conn, "DELETE FROM sessions WHERE id = ?", (token,))
            _commit(conn)
            return None
        if row["user_agent_hash"] is not None and user_agent_hash is not None \
                and row["user_agent_hash"] != user_agent_hash:
            return None
        _exec(conn,
              "UPDATE sessions SET last_seen = ? WHERE id = ?",
              (now.isoformat(), token))
        _commit(conn)
        return row["manager_id"]


def revoke_session(token: str) -> None:
    """Delete a session row. Idempotent — silently no-ops on unknown token."""
    if not token:
        return
    with _connect() as conn:
        _exec(conn, "DELETE FROM sessions WHERE id = ?", (token,))
        _commit(conn)


def cleanup_expired_sessions() -> int:
    """Delete all expired sessions. Returns count removed."""
    now_iso = datetime.now().isoformat()
    with _connect() as conn:
        cur = _exec(conn,
                    "DELETE FROM sessions WHERE expires_at <= ?",
                    (now_iso,))
        _commit(conn)
        return cur.rowcount if hasattr(cur, "rowcount") else 0


def _lockout_seconds_for(failed_count: int) -> int:
    """Exponential backoff: no lockout below threshold; otherwise
    `BASE * 2^(over_threshold)` seconds, capped at MAX."""
    over = failed_count - LOGIN_FAIL_THRESHOLD
    if over < 0:
        return 0
    return min(LOGIN_LOCKOUT_BASE_SECONDS * (2 ** over), LOGIN_LOCKOUT_MAX_SECONDS)


def get_lockout_until(username: str) -> datetime | None:
    """If the username is currently locked out, return the unlock time.
    Otherwise return None."""
    if not username:
        return None
    uname = username.lower().strip()
    with _connect() as conn:
        row = _fetchone(conn,
                        "SELECT locked_until FROM login_attempts WHERE username = ?",
                        (uname,))
    if not row or not row["locked_until"]:
        return None
    locked_until = _to_datetime(row["locked_until"])
    return locked_until if locked_until > datetime.now() else None


def record_failed_login(username: str) -> int:
    """Increment the failed-login counter for username and (re)compute the
    lockout window. Returns the updated failed_count."""
    if not username:
        return 0
    uname = username.lower().strip()
    now = datetime.now()
    with _connect() as conn:
        row = _fetchone(conn,
                        "SELECT failed_count FROM login_attempts WHERE username = ?",
                        (uname,))
        new_count = (row["failed_count"] if row else 0) + 1
        lockout_secs = _lockout_seconds_for(new_count)
        locked_until_iso = (now + timedelta(seconds=lockout_secs)).isoformat() \
            if lockout_secs else None
        _exec(conn,
              "INSERT INTO login_attempts (username, failed_count, last_attempt_at, locked_until) "
              "VALUES (?, ?, ?, ?) "
              "ON CONFLICT(username) DO UPDATE SET "
              "  failed_count = excluded.failed_count, "
              "  last_attempt_at = excluded.last_attempt_at, "
              "  locked_until = excluded.locked_until",
              (uname, new_count, now.isoformat(), locked_until_iso))
        _commit(conn)
    return new_count


def clear_failed_logins(username: str) -> None:
    """Reset the failed-login counter on successful authentication."""
    if not username:
        return
    uname = username.lower().strip()
    with _connect() as conn:
        _exec(conn, "DELETE FROM login_attempts WHERE username = ?", (uname,))
        _commit(conn)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Sentinel manager_id for system-wide / non-tenant config (OAuth provider
# settings, allowlists, internal migration markers).
SYSTEM_MANAGER_ID = 0

# Keys that are intrinsically system-wide (the deployment owns them, not any
# individual tenant). Anything else is tenant-scoped.
_SYSTEM_KEYS = {
    "google_client_id",
    "google_client_secret",
    "oauth_redirect_uri",
    "allowed_emails",
    "allowed_domain",
    "_migration_backfill_done",
    "_migration_config_partitioned",
}


def _is_system_key(key: str) -> bool:
    """A key is system-wide if it's in _SYSTEM_KEYS or starts with an underscore."""
    return key in _SYSTEM_KEYS or key.startswith("_")


def set_config(key: str, value: str, manager_id: int) -> None:
    """Store a config value scoped to manager_id. Pass SYSTEM_MANAGER_ID for
    deployment-wide config."""
    stored = _encrypt_value(value) if key in _SENSITIVE_KEYS else value
    with _connect() as conn:
        _exec(conn,
              "INSERT INTO config (manager_id, key, value) VALUES (?, ?, ?) "
              "ON CONFLICT(manager_id, key) DO UPDATE SET value = excluded.value",
              (manager_id, key, stored))
        _commit(conn)


def get_config(key: str, manager_id: int, default: str | None = None) -> str | None:
    """Read a config value scoped to manager_id. Pass SYSTEM_MANAGER_ID for
    deployment-wide config."""
    with _connect() as conn:
        row = _fetchone(conn,
                        "SELECT value FROM config WHERE manager_id = ? AND key = ?",
                        (manager_id, key))
    if not row:
        return default
    raw = row["value"]
    return _decrypt_value(raw) if key in _SENSITIVE_KEYS else raw


def upsert_user(google_id, email, name=None, picture=None):
    """Insert or update a user record on login."""
    with _connect() as conn:
        now = _sql_now()
        _exec(conn,
              f"INSERT INTO users (google_id, email, name, picture, last_login) "
              f"VALUES (?, ?, ?, ?, {now}) "
              f"ON CONFLICT(google_id) DO UPDATE SET "
              f"email = excluded.email, name = excluded.name, "
              f"picture = excluded.picture, last_login = {now}",
              (google_id, email, name, picture))
        _commit(conn)


def get_user_by_google_id(google_id):
    with _connect() as conn:
        row = _fetchone(conn, "SELECT * FROM users WHERE google_id = ?", (google_id,))
    return row


def list_users():
    with _connect() as conn:
        rows = _fetchall(conn, "SELECT * FROM users ORDER BY last_login DESC")
    return rows


def get_all_config(manager_id: int) -> dict[str, str]:
    """Return all config rows scoped to manager_id. Pass SYSTEM_MANAGER_ID for
    deployment-wide rows."""
    with _connect() as conn:
        rows = _fetchall(conn,
                         "SELECT key, value FROM config WHERE manager_id = ? ORDER BY key",
                         (manager_id,))
    return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Team Members
# ---------------------------------------------------------------------------

def add_team_member(name: str, email: str | None = None, role: str | None = None,
                    start_date: str | None = None, notes: str | None = None,
                    manager_id: int | None = None) -> int | None:
    with _connect() as conn:
        member_id = _exec_returning_id(
            conn,
            "INSERT INTO team_members (name, email, role, start_date, notes, manager_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, email, role, start_date, notes, manager_id),
        )
        _commit(conn)
    return member_id


def update_team_member(member_id: int, manager_id: int | None = None, **kwargs: Any) -> None:
    with _connect() as conn:
        allowed = {"name", "email", "role", "start_date", "notes"}
        fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), member_id]
        sql = f"UPDATE team_members SET {sets}, updated_at = ? WHERE id = ?"
        if manager_id is not None:
            sql += " AND manager_id = ?"
            values.append(manager_id)
        _exec(conn, sql, values)
        _commit(conn)


def get_team_member(member_id: int, manager_id: int | None = None) -> dict[str, Any] | None:
    with _connect() as conn:
        sql = "SELECT * FROM team_members WHERE id = ?"
        params = [member_id]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        row = _fetchone(conn, sql, params)
    return row


def get_team_member_by_name(name: str, manager_id: int | None = None) -> dict[str, Any] | None:
    with _connect() as conn:
        sql = "SELECT * FROM team_members WHERE LOWER(name) = LOWER(?)"
        params = [name]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        row = _fetchone(conn, sql, params)
    return row


def list_team_members(manager_id: int | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        sql = "SELECT * FROM team_members"
        params = []
        if manager_id is not None:
            sql += " WHERE manager_id = ?"
            params.append(manager_id)
        sql += " ORDER BY name"
        rows = _fetchall(conn, sql, params or None)
    return rows


def delete_team_member(member_id: int, manager_id: int | None = None) -> None:
    with _connect() as conn:
        sql = "DELETE FROM team_members WHERE id = ?"
        params = [member_id]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        _exec(conn, sql, params)
        _commit(conn)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def create_event(title: str, event_type: str, scheduled_date: str, scheduled_time: str,
                 team_member_id: int | None = None, duration_minutes: int = 30,
                 location: str | None = None, agenda: str | None = None,
                 manager_id: int | None = None) -> int | None:
    with _connect() as conn:
        event_id = _exec_returning_id(
            conn,
            "INSERT INTO events (title, event_type, team_member_id, scheduled_date, "
            "scheduled_time, duration_minutes, location, agenda, manager_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, event_type, team_member_id, scheduled_date,
             scheduled_time, duration_minutes, location, agenda, manager_id),
        )
        _commit(conn)
    return event_id


_RECURRENCE_COUNTS = {"weekly": 12, "monthly": 12, "quarterly": 8}


def _expand_recurrence_dates(start: date, rule: str,
                             until: date | None = None) -> list[date]:
    """Generate dates from `start` for the given rule. Stops at the rule's
    max count (12 / 12 / 8) OR at `until` (inclusive), whichever is sooner.
    `start` is the parent's date and is the first element of the returned
    list. With `until` is None, the full max-count is generated."""
    if rule not in _RECURRENCE_COUNTS:
        raise ValueError(f"unknown recurrence rule: {rule}")
    max_count = _RECURRENCE_COUNTS[rule]
    dates = [start]
    for i in range(1, max_count):
        if rule == "weekly":
            d = start + timedelta(weeks=i)
        elif rule == "monthly":
            d = _add_months_anchored(start, i)
        else:  # quarterly
            d = _add_months_anchored(start, 3 * i)
        if until and d > until:
            break
        dates.append(d)
    return dates


def create_recurring_events(title: str, event_type: str,
                            start_date: date, scheduled_time: str,
                            rule: str,
                            until_date: date | None = None,
                            team_member_id: int | None = None,
                            duration_minutes: int = 30,
                            location: str | None = None,
                            agenda: str | None = None,
                            manager_id: int | None = None) -> int:
    """Create a parent event + N concrete child rows atomically.
    Returns the parent's id.

    `rule` is one of 'weekly' / 'monthly' / 'quarterly'. The count is
    server-controlled (12 / 12 / 8) — the form doesn't pass a count.
    `start_date` MUST be a `date` instance (not iso string) — the form
    submit branch enforces this so a stringly-typed callsite is caught
    by isinstance, not silently passed through to _add_months_anchored
    where it would TypeError.

    Children are independent rows with status='scheduled' and
    parent_event_id pointing back at the parent. Editing or completing
    a single child does not propagate; deleting the parent leaves
    children with parent_event_id=NULL (FK ON DELETE SET NULL on PG;
    SQLite drops the FK clause and the column simply stays set to the
    no-longer-existing parent's id — children survive either way)."""
    if rule not in _RECURRENCE_COUNTS:
        raise ValueError(f"unknown recurrence rule: {rule}")
    if not isinstance(start_date, date):
        raise TypeError("start_date must be a date instance")
    if until_date is not None:
        if not isinstance(until_date, date):
            raise TypeError("until_date must be a date instance")
        if until_date < start_date:
            raise ValueError("until_date must be >= start_date")

    dates = _expand_recurrence_dates(start_date, rule, until_date)
    if len(dates) < 1:
        raise ValueError("recurrence produced zero dates")
    parent_iso = dates[0].isoformat()
    child_isos = [d.isoformat() for d in dates[1:]]

    parent_sql = (
        "INSERT INTO events (title, event_type, team_member_id, scheduled_date, "
        "scheduled_time, duration_minutes, location, agenda, manager_id, "
        "recurrence_rule) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    parent_params = (title, event_type, team_member_id, parent_iso,
                     scheduled_time, duration_minutes, location, agenda,
                     manager_id, rule)

    # children_sql expects parent_event_id as the LAST column —
    # _materialize_in_txn appends it to each tuple before executemany.
    children_sql = (
        "INSERT INTO events (title, event_type, team_member_id, scheduled_date, "
        "scheduled_time, duration_minutes, location, agenda, manager_id, "
        "recurrence_rule, parent_event_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    children_rows = [
        (title, event_type, team_member_id, d_iso, scheduled_time,
         duration_minutes, location, agenda, manager_id, rule)
        for d_iso in child_isos
    ]

    with _connect() as conn:
        return _materialize_in_txn(conn, parent_sql, parent_params,
                                   children_sql, children_rows)


def find_expiring_recurring_series(*, manager_id: int,
                                   lead_days: int = 14,
                                   grace_days: int = 7) -> dict[str, Any] | None:
    """Return the soonest-expiring recurring series whose latest child is
    within `lead_days` and which hasn't been warned about in the last
    `grace_days`. None if no series qualifies.

    The expiry-warning is the canonical Next Step branch added in PR 4 —
    a weekly 1:1 silently stops after 12 materialized occurrences (~3
    months) without this. Multiplicity contract: surface ONE banner per
    dashboard render (LIMIT 1, ordered by soonest expiry); next render
    surfaces the next-soonest after the user acts on the first.

    `recurrence_warned_at` is read via MIN across the series — if any
    child is recently stamped, the whole series counts as 'warned.'"""
    assert manager_id is not None, "manager_id required (no implicit cross-tenant)"
    today = date.today().isoformat()
    horizon = (date.today() + timedelta(days=lead_days)).isoformat()
    grace_cutoff = (datetime.now() - timedelta(days=grace_days)).isoformat()

    sql = """
        SELECT
            e.parent_event_id AS series_id,
            MIN(tm.name) AS member_name,
            MIN(e.recurrence_rule) AS recurrence_rule,
            MIN(e.title) AS title,
            MIN(e.scheduled_time) AS scheduled_time,
            MIN(e.duration_minutes) AS duration_minutes,
            MIN(e.location) AS location,
            MIN(e.event_type) AS event_type,
            MIN(e.team_member_id) AS team_member_id,
            MAX(e.scheduled_date) AS latest_date
        FROM events e
        LEFT JOIN team_members tm ON e.team_member_id = tm.id
        WHERE e.manager_id = ?
          AND e.parent_event_id IS NOT NULL
          AND e.status = 'scheduled'
        GROUP BY e.parent_event_id
        HAVING MAX(e.scheduled_date) BETWEEN ? AND ?
           AND (MIN(COALESCE(e.recurrence_warned_at, '')) = ''
                OR MIN(COALESCE(e.recurrence_warned_at, '')) < ?)
        ORDER BY MAX(e.scheduled_date) ASC
        LIMIT 1
    """
    with _connect() as conn:
        return _fetchone(conn, sql,
                         (manager_id, today, horizon, grace_cutoff))


def get_recurring_series_template(*, manager_id: int,
                                  series_id: int) -> dict[str, Any] | None:
    """Return prefill data for extending a recurring series. Same shape as
    find_expiring_recurring_series but keyed by an explicit series_id, used
    by the Schedule form when the user clicks an expiry warning.

    Aggregates with MIN/MAX so a missing parent (FK SET NULL) doesn't break
    the lookup — children carry the same title/time/etc. so any of them
    suffices to populate the form. Returns None when no children exist."""
    assert manager_id is not None, "manager_id required (no implicit cross-tenant)"
    sql = """
        SELECT
            MIN(e.title) AS title,
            MIN(e.event_type) AS event_type,
            MIN(e.team_member_id) AS team_member_id,
            MIN(e.scheduled_time) AS scheduled_time,
            MIN(e.duration_minutes) AS duration_minutes,
            MIN(e.location) AS location,
            MIN(e.recurrence_rule) AS recurrence_rule,
            MAX(e.scheduled_date) AS latest_date
        FROM events e
        WHERE e.manager_id = ?
          AND (e.id = ? OR e.parent_event_id = ?)
    """
    with _connect() as conn:
        row = _fetchone(conn, sql, (manager_id, series_id, series_id))
    if not row or row.get("title") is None:
        return None
    return row


def stamp_recurrence_warning(*, manager_id: int, series_id: int) -> None:
    """Set recurrence_warned_at = NOW() on every child of the given series.
    Stamping every child (rather than just the parent) means the warning
    state survives the parent being FK SET NULL'd via ON DELETE."""
    assert manager_id is not None, "manager_id required (no implicit cross-tenant)"
    now_iso = datetime.now().isoformat()
    with _connect() as conn:
        _exec(conn,
            "UPDATE events SET recurrence_warned_at = ? "
            "WHERE manager_id = ? AND parent_event_id = ?",
            (now_iso, manager_id, series_id))
        _commit(conn)


def update_event(event_id: int, manager_id: int, **kwargs) -> None:
    with _connect() as conn:
        allowed = {
            "title", "event_type", "team_member_id", "scheduled_date",
            "scheduled_time", "duration_minutes", "location", "agenda",
            "status", "notes", "calendar_invite_sent",
        }
        fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), event_id, manager_id]
        _exec(conn,
              f"UPDATE events SET {sets}, updated_at = ? WHERE id = ? AND manager_id = ?",
              values)
        _commit(conn)


def complete_event(event_id: int, manager_id: int, notes: str | None = None) -> None:
    update_event(event_id, manager_id, status="completed", notes=notes)


def cancel_event(event_id: int, manager_id: int) -> None:
    update_event(event_id, manager_id, status="cancelled")


def get_event(event_id: int, manager_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = _fetchone(
            conn,
            "SELECT e.*, tm.name AS participant_name, tm.email AS participant_email "
            "FROM events e "
            "LEFT JOIN team_members tm ON e.team_member_id = tm.id "
            "WHERE e.id = ? AND e.manager_id = ?",
            (event_id, manager_id),
        )
    return row


def list_events(event_type: str | None = None, status: str | None = None,
                team_member_id: int | None = None, from_date: str | None = None,
                to_date: str | None = None, limit: int = 50,
                manager_id: int | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        query = (
            "SELECT e.*, tm.name AS participant_name, tm.email AS participant_email "
            "FROM events e "
            "LEFT JOIN team_members tm ON e.team_member_id = tm.id WHERE 1=1"
        )
        params = []

        if manager_id is not None:
            query += " AND e.manager_id = ?"
            params.append(manager_id)
        if event_type:
            query += " AND e.event_type = ?"
            params.append(event_type)
        if status:
            query += " AND e.status = ?"
            params.append(status)
        if team_member_id:
            query += " AND e.team_member_id = ?"
            params.append(team_member_id)
        if from_date:
            query += " AND e.scheduled_date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND e.scheduled_date <= ?"
            params.append(to_date)

        query += " ORDER BY e.scheduled_date, e.scheduled_time LIMIT ?"
        params.append(limit)

        cur = _exec(conn, query, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def get_upcoming_events(days: int = 7, manager_id: int | None = None) -> list[dict[str, Any]]:
    today = datetime.now().strftime("%Y-%m-%d")
    future = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return list_events(status="scheduled", from_date=today, to_date=future,
                       manager_id=manager_id)


# ---------------------------------------------------------------------------
# Upcoming / Overdue aggregator — events + action_items + delegations + goals
# ---------------------------------------------------------------------------
#
# Date bounds are computed in Python and bound as TEXT params, never inlined
# via _sql_current_date(). On Postgres, _sql_current_date() returns a `date`
# type and `text BETWEEN date AND date` triggers the same UndefinedFunction
# class that bit get_member_timeline + get_manager_activity_trends. All four
# date columns are TEXT YYYY-MM-DD; lex order == chronological order on both
# backends, so `text BETWEEN ? AND ?` with string params works on PG and SQLite.

_UPCOMING_AGGREGATE_SQL = """
    SELECT 'event' AS type, e.scheduled_date AS due_date,
           e.scheduled_time AS time_str, e.title AS title,
           tm.name AS member_name, 'Schedule' AS link_page, e.id AS link_id
    FROM events e
    LEFT JOIN team_members tm ON e.team_member_id = tm.id
    WHERE e.manager_id = ?
      AND e.scheduled_date BETWEEN ? AND ?
      AND e.status = 'scheduled'
    UNION ALL
    SELECT 'todo' AS type, ai.due_date AS due_date,
           NULL AS time_str, ai.description AS title,
           NULL AS member_name, 'Actions' AS link_page, ai.id AS link_id
    FROM action_items ai
    WHERE ai.manager_id = ?
      AND ai.status IN ('pending', 'in_progress')
      AND ai.due_date BETWEEN ? AND ?
    UNION ALL
    SELECT 'check-in' AS type, d.check_in_date AS due_date,
           NULL AS time_str, d.task AS title,
           tm.name AS member_name, 'Delegations' AS link_page, d.id AS link_id
    FROM delegations d
    LEFT JOIN team_members tm ON d.team_member_id = tm.id
    WHERE d.manager_id = ?
      AND d.status = 'active'
      AND d.check_in_date BETWEEN ? AND ?
    UNION ALL
    SELECT 'goal' AS type, g.target_date AS due_date,
           NULL AS time_str, g.description AS title,
           tm.name AS member_name, 'Goals' AS link_page, g.id AS link_id
    FROM goals g
    LEFT JOIN team_members tm ON g.team_member_id = tm.id
    WHERE g.manager_id = ?
      AND g.status IN ('not_started', 'in_progress')
      AND g.target_date BETWEEN ? AND ?
    ORDER BY due_date, time_str
"""


def get_upcoming_aggregate(*, manager_id: int,
                           days: int = 7) -> list[dict[str, Any]]:
    """Items due within `days` days (today through today+days inclusive)
    across events, action_items, delegations, and goals.

    `manager_id` is keyword-only and asserted non-None: an unauthenticated
    `_mid()` returning None must fail loud rather than silently returning
    zero rows that read as 'nothing upcoming.' Goals filter is the
    positive form ('not_started', 'in_progress') — `partially_met` and
    `not_met` are terminal historical states and must not appear here."""
    assert manager_id is not None, "manager_id required (no implicit cross-tenant)"
    today = date.today().isoformat()
    horizon = (date.today() + timedelta(days=days)).isoformat()
    params = (manager_id, today, horizon,
              manager_id, today, horizon,
              manager_id, today, horizon,
              manager_id, today, horizon)
    with _connect() as conn:
        return _fetchall(conn, _UPCOMING_AGGREGATE_SQL, params)


def get_overdue_aggregate(*, manager_id: int,
                          lookback_days: int = 90) -> list[dict[str, Any]]:
    """Past-due items in the same four streams. Lookback is capped so a
    user with old completed-but-never-marked events doesn't scan years
    of history; 90 days is enough to surface anything actionable.

    Same goals-status contract as get_upcoming_aggregate — terminal states
    (met / exceeded / partially_met / not_met) are excluded so an overdue
    goal that the user already gave up on doesn't keep appearing."""
    assert manager_id is not None, "manager_id required (no implicit cross-tenant)"
    earliest = (date.today() - timedelta(days=lookback_days)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    params = (manager_id, earliest, yesterday,
              manager_id, earliest, yesterday,
              manager_id, earliest, yesterday,
              manager_id, earliest, yesterday)
    with _connect() as conn:
        return _fetchall(conn, _UPCOMING_AGGREGATE_SQL, params)


def get_event_history(team_member_id, limit=20, manager_id=None):
    return list_events(team_member_id=team_member_id, status="completed",
                       limit=limit, manager_id=manager_id)


# ---------------------------------------------------------------------------
# Action Items
# ---------------------------------------------------------------------------

def add_action_item(description: str, event_id: int | None = None,
                    assignee: str | None = None, due_date: str | None = None,
                    manager_id: int | None = None) -> int | None:
    with _connect() as conn:
        item_id = _exec_returning_id(
            conn,
            "INSERT INTO action_items (event_id, description, assignee, due_date, manager_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, description, assignee, due_date, manager_id),
        )
        _commit(conn)
    return item_id


def complete_action_item(item_id: int, manager_id: int) -> None:
    with _connect() as conn:
        _exec(conn,
              "UPDATE action_items SET status = 'completed', completed_at = ? "
              "WHERE id = ? AND manager_id = ?",
              (datetime.now().isoformat(), item_id, manager_id))
        _commit(conn)


def update_action_item_status(item_id: int, status: str, manager_id: int) -> None:
    with _connect() as conn:
        completed_at = datetime.now().isoformat() if status == "completed" else None
        _exec(conn,
              "UPDATE action_items SET status = ?, completed_at = ? "
              "WHERE id = ? AND manager_id = ?",
              (status, completed_at, item_id, manager_id))
        _commit(conn)


def list_action_items(event_id=None, status=None, assignee=None, manager_id=None):
    """List action items. `status` may be a string (single value) or an
    iterable of strings (matched via IN). Used by get_pending_action_items
    to fold two queries into one (P4.2 / AUDIT M4)."""
    with _connect() as conn:
        query = (
            "SELECT ai.*, e.title AS event_title "
            "FROM action_items ai "
            "LEFT JOIN events e ON ai.event_id = e.id WHERE 1=1"
        )
        params = []
        if manager_id is not None:
            query += " AND ai.manager_id = ?"
            params.append(manager_id)
        if event_id:
            query += " AND ai.event_id = ?"
            params.append(event_id)
        if status:
            if isinstance(status, (list, tuple, set)):
                statuses = list(status)
                if statuses:
                    placeholders = ",".join("?" for _ in statuses)
                    query += f" AND ai.status IN ({placeholders})"
                    params.extend(statuses)
            else:
                query += " AND ai.status = ?"
                params.append(status)
        if assignee:
            query += " AND LOWER(ai.assignee) = LOWER(?)"
            params.append(assignee)
        query += " ORDER BY ai.due_date, ai.created_at"
        cur = _exec(conn, query, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def get_pending_action_items(manager_id: int | None = None) -> list[dict[str, Any]]:
    """Single-query fetch of pending+in_progress action items (was two
    separate queries; P4.2 / AUDIT M4)."""
    return list_action_items(
        status=("pending", "in_progress"), manager_id=manager_id)


def delete_action_item(item_id: int, manager_id: int) -> None:
    """Delete an action item owned by manager_id."""
    with _connect() as conn:
        _exec(conn, "DELETE FROM action_items WHERE id = ? AND manager_id = ?",
              (item_id, manager_id))
        _commit(conn)


def update_action_item(item_id: int, manager_id: int, **kwargs) -> None:
    """Update action item fields owned by manager_id."""
    allowed = {"description", "assignee", "due_date", "status"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    if fields.get("status") == "completed":
        fields["completed_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        _exec(conn, f"UPDATE action_items SET {sets} WHERE id = ? AND manager_id = ?",
              (*fields.values(), item_id, manager_id))
        _commit(conn)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def _resolve_manager_id_from_member(conn, team_member_id):
    """Look up manager_id from a team_member id. Used to populate manager_id
    on inserts into child tables when the caller does not supply it."""
    if team_member_id is None:
        return None
    row = _fetchone(conn,
                    "SELECT manager_id FROM team_members WHERE id = ?",
                    (team_member_id,))
    return row["manager_id"] if row else None


def add_feedback(team_member_id: int, feedback_type: str, situation: str | None = None,
                 behavior: str | None = None, impact: str | None = None,
                 event_id: int | None = None,
                 manager_id: int | None = None) -> int | None:
    with _connect() as conn:
        if manager_id is None:
            manager_id = _resolve_manager_id_from_member(conn, team_member_id)
        feedback_id = _exec_returning_id(
            conn,
            "INSERT INTO feedback (manager_id, team_member_id, event_id, feedback_type, "
            "situation, behavior, impact) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (manager_id, team_member_id, event_id, feedback_type, situation, behavior, impact),
        )
        _commit(conn)
    return feedback_id


def list_feedback(manager_id: int, team_member_id: int | None = None,
                  feedback_type: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        query = (
            "SELECT f.*, tm.name AS member_name "
            "FROM feedback f "
            "JOIN team_members tm ON f.team_member_id = tm.id "
            "WHERE f.manager_id = ?"
        )
        params = [manager_id]
        if team_member_id:
            query += " AND f.team_member_id = ?"
            params.append(team_member_id)
        if feedback_type:
            query += " AND f.feedback_type = ?"
            params.append(feedback_type)
        query += " ORDER BY f.created_at DESC"
        cur = _exec(conn, query, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def update_feedback(feedback_id: int, manager_id: int, **kwargs) -> None:
    """Update feedback fields owned by manager_id."""
    allowed = {"feedback_type", "situation", "behavior", "impact"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        _exec(conn, f"UPDATE feedback SET {sets} WHERE id = ? AND manager_id = ?",
              (*fields.values(), feedback_id, manager_id))
        _commit(conn)


def delete_feedback(feedback_id: int, manager_id: int) -> None:
    """Delete a feedback record owned by manager_id."""
    with _connect() as conn:
        _exec(conn, "DELETE FROM feedback WHERE id = ? AND manager_id = ?",
              (feedback_id, manager_id))
        _commit(conn)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def add_goal(team_member_id: int, quarter: str, description: str,
             key_results: str | None = None,
             target_date: str | None = None,
             manager_id: int | None = None) -> int | None:
    if target_date:
        # Defense-in-depth — st.date_input always emits a YYYY-MM-DD string
        # via .isoformat() in our form, but the writer is also reachable from
        # tests / scripts. A malformed value here would silently break the
        # Upcoming aggregator's `BETWEEN ? AND ?` lex-comparison.
        date.fromisoformat(target_date)
    with _connect() as conn:
        if manager_id is None:
            manager_id = _resolve_manager_id_from_member(conn, team_member_id)
        goal_id = _exec_returning_id(
            conn,
            "INSERT INTO goals (manager_id, team_member_id, quarter, description, "
            "key_results, target_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (manager_id, team_member_id, quarter, description, key_results,
             target_date),
        )
        _commit(conn)
    return goal_id


def update_goal(goal_id: int, manager_id: int, **kwargs) -> None:
    with _connect() as conn:
        allowed = {"description", "key_results", "status", "quarter", "target_date"}
        if kwargs.get("target_date"):
            date.fromisoformat(kwargs["target_date"])
        fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [datetime.now().isoformat(), goal_id, manager_id]
        _exec(conn,
              f"UPDATE goals SET {sets}, updated_at = ? WHERE id = ? AND manager_id = ?",
              values)
        _commit(conn)


def list_goals(manager_id: int, team_member_id: int | None = None,
               quarter: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        query = (
            "SELECT g.*, tm.name AS member_name "
            "FROM goals g "
            "JOIN team_members tm ON g.team_member_id = tm.id "
            "WHERE g.manager_id = ?"
        )
        params = [manager_id]
        if team_member_id:
            query += " AND g.team_member_id = ?"
            params.append(team_member_id)
        if quarter:
            query += " AND g.quarter = ?"
            params.append(quarter)
        if status:
            query += " AND g.status = ?"
            params.append(status)
        query += " ORDER BY g.quarter DESC, g.created_at"
        cur = _exec(conn, query, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def delete_goal(goal_id: int, manager_id: int) -> None:
    """Delete a goal record owned by manager_id."""
    with _connect() as conn:
        _exec(conn, "DELETE FROM goals WHERE id = ? AND manager_id = ?",
              (goal_id, manager_id))
        _commit(conn)


# ---------------------------------------------------------------------------
# Reports / Aggregations
# ---------------------------------------------------------------------------

def get_weekly_summary(manager_id: int | None = None) -> dict[str, list]:
    """Get a summary of activity for the current week."""
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    sunday = (today + timedelta(days=6 - today.weekday())).strftime("%Y-%m-%d")

    summary = {}

    summary["upcoming_events"] = list_events(
        status="scheduled", from_date=monday, to_date=sunday,
        manager_id=manager_id
    )
    summary["completed_events"] = list_events(
        status="completed", from_date=monday, to_date=sunday,
        manager_id=manager_id
    )
    summary["pending_actions"] = get_pending_action_items(manager_id=manager_id)

    with _connect() as conn:
        sql = ("SELECT * FROM action_items WHERE status != 'completed' "
               "AND due_date < ? AND due_date IS NOT NULL")
        params = [today.strftime("%Y-%m-%d")]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        sql += " ORDER BY due_date"
        overdue = _fetchall(conn, sql, params)
        summary["overdue_actions"] = overdue

    return summary


def get_member_summary(team_member_id: int, manager_id: int) -> dict | None:
    """Get a full activity summary for a team member owned by manager_id."""
    member = get_team_member(team_member_id)
    if not member or member.get("manager_id") != manager_id:
        return None

    with _connect() as conn:
        summary = {"member": member}

        summary["recent_events"] = list_events(
            team_member_id=team_member_id, limit=10, manager_id=manager_id,
        )
        summary["feedback"] = list_feedback(manager_id=manager_id, team_member_id=team_member_id)
        summary["goals"] = list_goals(manager_id=manager_id, team_member_id=team_member_id)

        actions = _fetchall(
            conn,
            "SELECT ai.* FROM action_items ai "
            "JOIN events e ON ai.event_id = e.id "
            "WHERE e.team_member_id = ? AND ai.manager_id = ? "
            "ORDER BY ai.created_at DESC LIMIT 20",
            (team_member_id, manager_id),
        )
        summary["action_items"] = actions

    return summary


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def add_journal_entry(entry_date: str, entry_type: str = "daily", content: str | None = None,
                      mood: int | None = None, energy: int | None = None,
                      private_notes: str | None = None, tags: str | None = None,
                      manager_id: int | None = None) -> int | None:
    with _connect() as conn:
        entry_id = _exec_returning_id(
            conn,
            "INSERT INTO journal_entries "
            "(entry_date, entry_type, content, mood, energy, private_notes, tags, manager_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_date, entry_type, content, mood, energy, private_notes, tags, manager_id),
        )
        _commit(conn)
    return entry_id


def update_journal_entry(entry_id: int, manager_id: int, **kwargs) -> None:
    allowed = {"content", "mood", "energy", "private_notes", "tags", "entry_type",
                "coaching_response"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        _exec(conn,
              f"UPDATE journal_entries SET {set_clause} WHERE id = ? AND manager_id = ?",
              (*fields.values(), entry_id, manager_id))
        _commit(conn)


def get_journal_entry_by_date(entry_date: str, entry_type: str = "daily",
                              manager_id: int | None = None) -> dict[str, Any] | None:
    with _connect() as conn:
        sql = "SELECT * FROM journal_entries WHERE entry_date = ? AND entry_type = ?"
        params = [entry_date, entry_type]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        row = _fetchone(conn, sql, params)
    return row


def list_journal_entries(entry_type: str | None = None, limit: int = 30,
                         manager_id: int | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        query = "SELECT * FROM journal_entries WHERE 1=1"
        params = []
        if manager_id is not None:
            query += " AND manager_id = ?"
            params.append(manager_id)
        if entry_type:
            query += " AND entry_type = ?"
            params.append(entry_type)
        query += " ORDER BY entry_date DESC LIMIT ?"
        params.append(limit)
        cur = _exec(conn, query, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def get_journal_streak(manager_id: int | None = None) -> int:
    """Count consecutive days with a journal entry ending today."""
    with _connect() as conn:
        sql = "SELECT DISTINCT entry_date FROM journal_entries"
        params = []
        if manager_id is not None:
            sql += " WHERE manager_id = ?"
            params.append(manager_id)
        sql += " ORDER BY entry_date DESC LIMIT 365"
        rows = _fetchall(conn, sql, params or None)
    if not rows:
        return 0
    dates = [r["entry_date"] for r in rows]
    streak = 0
    check = datetime.now().date()
    for d in dates:
        if d == check.isoformat():
            streak += 1
            check -= timedelta(days=1)
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# Self-Assessment
# ---------------------------------------------------------------------------

def save_self_assessment(week_date, scores_dict, manager_id=None):
    """Save or replace self-assessment scores for a week.
    scores_dict: {dimension_name: score}

    Uses INSERT ... ON CONFLICT DO UPDATE under the (manager_id, week_date,
    dimension) unique index so concurrent saves cannot produce duplicate or
    missing rows under autocommit (regression for AUDIT H6)."""
    with _connect() as conn:
        for dim, score in scores_dict.items():
            _exec(conn,
                  "INSERT INTO self_assessments "
                  "(manager_id, week_date, dimension, score) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(manager_id, week_date, dimension) "
                  "DO UPDATE SET score = excluded.score",
                  (manager_id, week_date, dim, score))
        _commit(conn)


def get_self_assessment_trends(weeks=12, manager_id=None):
    with _connect() as conn:
        sql = ("SELECT week_date, dimension, score FROM self_assessments "
               f"WHERE week_date >= {_sql_date_offset('?')}")
        params = [str(-weeks * 7)]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        sql += " ORDER BY week_date, dimension"
        rows = _fetchall(conn, sql, params)
    return rows


def get_latest_self_assessment(manager_id=None):
    with _connect() as conn:
        subquery = "SELECT MAX(week_date) FROM self_assessments"
        if manager_id is not None:
            subquery += " WHERE manager_id = ?"
        sql = ("SELECT dimension, score FROM self_assessments "
               f"WHERE week_date = ({subquery})")
        params = []
        if manager_id is not None:
            params.append(manager_id)
            sql += " AND manager_id = ?"
            params.append(manager_id)
        rows = _fetchall(conn, sql, params or None)
    return {r["dimension"]: r["score"] for r in rows}


# ---------------------------------------------------------------------------
# Nudges
# ---------------------------------------------------------------------------

def get_time_since_last_event_per_member(manager_id=None):
    with _connect() as conn:
        days_expr = _sql_days_since("MAX(e.scheduled_date)")
        sql = f"""
            SELECT tm.id AS member_id, tm.name AS member_name,
                   MAX(e.scheduled_date) AS last_meeting_date,
                   {days_expr} AS days_since
            FROM team_members tm
            LEFT JOIN events e ON e.team_member_id = tm.id AND e.status = 'completed'
        """
        params = []
        if manager_id is not None:
            sql += " WHERE tm.manager_id = ?"
            params.append(manager_id)
        sql += " GROUP BY tm.id, tm.name ORDER BY days_since DESC"
        rows = _fetchall(conn, sql, params or None)
    return rows


# Nudge thresholds (AUDIT L9). Promoted from inline magic numbers so the
# dashboard's "you haven't met X in N days" semantics live in one place.
MEETING_CRITICAL_DAYS = 21
MEETING_WARNING_DAYS = 14
STALE_FEEDBACK_DAYS = 21


def get_stale_feedback_members(days=STALE_FEEDBACK_DAYS, manager_id=None):
    with _connect() as conn:
        days_expr = _sql_days_since("MAX(f.created_at)")
        sql = f"""
            SELECT tm.id AS member_id, tm.name AS member_name,
                   MAX(f.created_at) AS last_feedback_date,
                   {days_expr} AS days_since
            FROM team_members tm
            LEFT JOIN feedback f ON f.team_member_id = tm.id
        """
        params = []
        if manager_id is not None:
            sql += " WHERE tm.manager_id = ?"
            params.append(manager_id)
        sql += f"""
            GROUP BY tm.id, tm.name
            HAVING MAX(f.created_at) IS NULL
               OR {days_expr} > ?
            ORDER BY days_since DESC
        """
        params.append(days)
        rows = _fetchall(conn, sql, params)
    return rows


def get_overdue_action_count(manager_id=None):
    with _connect() as conn:
        sql = (f"SELECT COUNT(*) AS cnt FROM action_items "
               f"WHERE status != 'completed' AND due_date < {_sql_current_date()} "
               f"AND due_date IS NOT NULL")
        params = []
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        row = _fetchone(conn, sql, params or None)
    return row["cnt"] if row else 0


def get_nudges(manager_id: int | None = None) -> list[dict[str, Any]]:
    """Aggregate all nudges, sorted by severity."""
    nudges = []
    for m in get_time_since_last_event_per_member(manager_id=manager_id):
        days = m["days_since"]
        if days is None:
            nudges.append({
                "type": "meeting", "severity": "critical",
                "message": f"You have never had a recorded meeting with {m['member_name']}.",
                "member_id": m["member_id"],
            })
        elif days > MEETING_CRITICAL_DAYS:
            nudges.append({
                "type": "meeting", "severity": "critical",
                "message": f"It's been {days} days since your last meeting with {m['member_name']}.",
                "member_id": m["member_id"],
            })
        elif days > MEETING_WARNING_DAYS:
            nudges.append({
                "type": "meeting", "severity": "warning",
                "message": f"It's been {days} days since your last meeting with {m['member_name']}.",
                "member_id": m["member_id"],
            })

    overdue = get_overdue_action_count(manager_id=manager_id)
    if overdue > 0:
        nudges.append({
            "type": "action", "severity": "warning",
            "message": f"{overdue} overdue action item(s) need attention.",
            "member_id": None,
        })

    for m in get_stale_feedback_members(days=STALE_FEEDBACK_DAYS, manager_id=manager_id):
        days = m["days_since"]
        label = f"{days} days" if days else "ever"
        nudges.append({
            "type": "feedback", "severity": "info",
            "message": f"No feedback recorded for {m['member_name']} in {label}.",
            "member_id": m["member_id"],
        })

    # Weekly reflection self-binding nudge
    last_weekly = get_journal_entry_by_date(
        (datetime.now().date() - timedelta(
            days=datetime.now().date().weekday())).isoformat(), "weekly",
        manager_id=manager_id)
    if not last_weekly:
        # Check if ANY weekly entry in last 7 days
        recent_weekly = list_journal_entries(entry_type="weekly", limit=1,
                                            manager_id=manager_id)
        if not recent_weekly or (
            recent_weekly and recent_weekly[0]["entry_date"] <
            (datetime.now().date() - timedelta(days=7)).isoformat()
        ):
            nudges.append({
                "type": "reflection", "severity": "info",
                "message": "Time for your weekly reflection. "
                           "How did you show up as a manager this week?",
                "member_id": None,
            })

    order = {"critical": 0, "warning": 1, "info": 2}
    nudges.sort(key=lambda n: order.get(n["severity"], 3))
    return nudges


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def get_meetings_per_member_per_month(months=6, manager_id=None):
    with _connect() as conn:
        month_expr = _sql_month("e.scheduled_date")
        date_offset = _sql_date_offset("?")
        sql = f"""
            SELECT tm.name AS member_name,
                   {month_expr} AS month,
                   COUNT(*) AS meeting_count
            FROM events e
            JOIN team_members tm ON e.team_member_id = tm.id
            WHERE e.status = 'completed'
              AND e.scheduled_date >= {date_offset}
        """
        params = [str(-months * 30)]
        if manager_id is not None:
            sql += " AND e.manager_id = ?"
            params.append(manager_id)
        sql += f" GROUP BY tm.id, tm.name, {month_expr} ORDER BY month, tm.name"
        rows = _fetchall(conn, sql, params)
    return rows


def get_feedback_ratios(manager_id: int | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        sql = """
            SELECT tm.name AS member_name,
                   SUM(CASE WHEN f.feedback_type = 'positive' THEN 1 ELSE 0 END)
                       AS positive_count,
                   SUM(CASE WHEN f.feedback_type = 'constructive' THEN 1 ELSE 0 END)
                       AS constructive_count,
                   COUNT(*) AS total_count
            FROM feedback f
            JOIN team_members tm ON f.team_member_id = tm.id
        """
        params = []
        if manager_id is not None:
            sql += " WHERE tm.manager_id = ?"
            params.append(manager_id)
        sql += " GROUP BY tm.id ORDER BY tm.name"
        rows = _fetchall(conn, sql, params or None)
    return rows


def get_goal_completion_rates(manager_id=None):
    with _connect() as conn:
        sql = """
            SELECT tm.name AS member_name, g.status, COUNT(*) AS cnt
            FROM goals g
            JOIN team_members tm ON g.team_member_id = tm.id
        """
        params = []
        if manager_id is not None:
            sql += " WHERE tm.manager_id = ?"
            params.append(manager_id)
        sql += " GROUP BY tm.id, g.status ORDER BY tm.name"
        rows = _fetchall(conn, sql, params or None)
    return rows


def get_action_stats(manager_id: int | None = None) -> dict[str, int]:
    with _connect() as conn:
        cd = _sql_current_date()
        sql = f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status != 'completed' THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN status != 'completed' AND due_date < {cd}
                            AND due_date IS NOT NULL THEN 1 ELSE 0 END) AS overdue
            FROM action_items
        """
        params = []
        if manager_id is not None:
            sql += " WHERE manager_id = ?"
            params.append(manager_id)
        row = _fetchone(conn, sql, params or None)
    return row if row else {"total": 0, "completed": 0, "pending": 0, "overdue": 0}


def get_manager_activity_trends(weeks=12, manager_id=None):
    with _connect() as conn:
        wk = _sql_week
        dt = _sql_date_offset("?")
        # feedback.created_at and action_items.created_at are TIMESTAMP on PG,
        # so a direct comparison against `dt` (text on PG) raises
        # "operator does not exist: timestamp >= text". Render them as date
        # strings to match `dt`'s type. SQLite stores these as ISO text
        # already, so the helper is a no-op-shaped substr() there.
        ca_date = _sql_date_of_timestamp("created_at")
        evt_filter = " AND manager_id = ?" if manager_id is not None else ""
        ai_filter = " AND manager_id = ?" if manager_id is not None else ""
        # feedback doesn't have manager_id directly; filter via team_members join
        fb_filter = (" AND team_member_id IN "
                     "(SELECT id FROM team_members WHERE manager_id = ?)"
                     if manager_id is not None else "")
        sql = f"""
            SELECT week, SUM(events) AS events, SUM(feedback) AS feedback,
                   SUM(actions) AS actions
            FROM (
                SELECT {wk('scheduled_date')} AS week,
                       COUNT(*) AS events, 0 AS feedback, 0 AS actions
                FROM events WHERE status = 'completed'
                  AND scheduled_date >= {dt}{evt_filter}
                GROUP BY {wk('scheduled_date')}
                UNION ALL
                SELECT {wk('created_at')} AS week,
                       0, COUNT(*), 0
                FROM feedback
                WHERE {ca_date} >= {dt}{fb_filter}
                GROUP BY {wk('created_at')}
                UNION ALL
                SELECT {wk('created_at')} AS week,
                       0, 0, COUNT(*)
                FROM action_items
                WHERE {ca_date} >= {dt}{ai_filter}
                GROUP BY {wk('created_at')}
            ) sub
            GROUP BY week ORDER BY week
        """
        params = [str(-weeks * 7)]
        if manager_id is not None:
            params.append(manager_id)
        params.append(str(-weeks * 7))
        if manager_id is not None:
            params.append(manager_id)
        params.append(str(-weeks * 7))
        if manager_id is not None:
            params.append(manager_id)
        rows = _fetchall(conn, sql, params)
    return rows


# ---------------------------------------------------------------------------
# Member Timeline & Pre-Meeting Prep
# ---------------------------------------------------------------------------

def get_member_timeline(member_id: int, manager_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        left10 = _sql_date_of_timestamp("created_at")
        rows = _fetchall(conn, f"""
            SELECT date, type, summary, detail, source_id FROM (
                SELECT scheduled_date AS date, 'event' AS type,
                       title AS summary, notes AS detail, id AS source_id
                FROM events WHERE team_member_id = ? AND manager_id = ?
                UNION ALL
                SELECT {left10} AS date,
                       feedback_type || '_feedback' AS type,
                       COALESCE(situation, '') AS summary,
                       COALESCE(behavior, '') || ' → ' || COALESCE(impact, '') AS detail,
                       id AS source_id
                FROM feedback WHERE team_member_id = ? AND manager_id = ?
                UNION ALL
                SELECT {left10} AS date, 'goal' AS type,
                       description AS summary, status AS detail, id AS source_id
                FROM goals WHERE team_member_id = ? AND manager_id = ?
                UNION ALL
                SELECT conversation_date AS date, 'career' AS type,
                       COALESCE(topic, 'Career conversation') AS summary,
                       notes AS detail, id AS source_id
                FROM career_conversations WHERE team_member_id = ? AND manager_id = ?
                UNION ALL
                SELECT session_date AS date, 'one_on_one' AS type,
                       'One-on-one' AS summary,
                       COALESCE(direct_notes, '') AS detail, id AS source_id
                FROM one_on_one_sessions
                WHERE team_member_id = ? AND manager_id = ?
            ) timeline
            ORDER BY date DESC LIMIT ?
        """, (member_id, manager_id, member_id, manager_id,
              member_id, manager_id, member_id, manager_id,
              member_id, manager_id, limit))
    return rows


def get_pre_meeting_prep(member_id: int, manager_id: int) -> dict | None:
    with _connect() as conn:
        member = _fetchone(conn,
                           "SELECT * FROM team_members WHERE id = ? AND manager_id = ?",
                           (member_id, manager_id))
        if not member:
            return None

        prep = {"member": member}

        # Last meeting
        last_evt = _fetchone(conn,
            "SELECT scheduled_date FROM events "
            "WHERE team_member_id = ? AND manager_id = ? AND status = 'completed' "
            "ORDER BY scheduled_date DESC LIMIT 1",
            (member_id, manager_id))
        if last_evt:
            prep["last_meeting_date"] = last_evt["scheduled_date"]
            prep["days_since_meeting"] = (
                datetime.now().date() - datetime.fromisoformat(last_evt["scheduled_date"]).date()
            ).days
        else:
            prep["last_meeting_date"] = None
            prep["days_since_meeting"] = None

        # Last feedback
        last_fb = _fetchone(conn,
            "SELECT created_at FROM feedback "
            "WHERE team_member_id = ? AND manager_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (member_id, manager_id))
        if last_fb:
            fb_created = _to_datetime(last_fb["created_at"])
            prep["last_feedback_date"] = fb_created.date().isoformat()
            prep["days_since_feedback"] = (datetime.now().date() - fb_created.date()).days
        else:
            prep["last_feedback_date"] = None
            prep["days_since_feedback"] = None

        # Feedback ratio
        ratios = _fetchall(conn,
            "SELECT feedback_type, COUNT(*) AS cnt FROM feedback "
            "WHERE team_member_id = ? AND manager_id = ? GROUP BY feedback_type",
            (member_id, manager_id))
        prep["positive_count"] = 0
        prep["constructive_count"] = 0
        for r in ratios:
            if r["feedback_type"] == "positive":
                prep["positive_count"] = r["cnt"]
            else:
                prep["constructive_count"] = r["cnt"]

        # Pending actions (match by member name through events)
        name = member["name"]
        pending_row = _fetchone(conn,
            "SELECT COUNT(*) AS cnt FROM action_items "
            "WHERE manager_id = ? AND status != 'completed' AND ("
            "  LOWER(assignee) = LOWER(?) "
            "  OR event_id IN (SELECT id FROM events "
            "                  WHERE team_member_id = ? AND manager_id = ?)"
            ")",
            (manager_id, name, member_id, manager_id))
        prep["pending_actions"] = pending_row["cnt"] if pending_row else 0

        # Active goals
        prep["active_goals"] = _fetchall(conn,
            "SELECT quarter, description, status FROM goals "
            "WHERE team_member_id = ? AND manager_id = ? "
            "AND status IN ('not_started', 'in_progress') "
            "ORDER BY quarter DESC",
            (member_id, manager_id))

        # Recent feedback
        prep["recent_feedback"] = _fetchall(conn,
            "SELECT feedback_type, situation, behavior, impact, created_at "
            "FROM feedback WHERE team_member_id = ? AND manager_id = ? "
            "ORDER BY created_at DESC LIMIT 3",
            (member_id, manager_id))

    return prep


# ---------------------------------------------------------------------------
# Career Development
# ---------------------------------------------------------------------------

def add_career_conversation(team_member_id, conversation_date,
                            topic=None, notes=None, next_steps=None,
                            manager_id=None):
    with _connect() as conn:
        if manager_id is None:
            manager_id = _resolve_manager_id_from_member(conn, team_member_id)
        cid = _exec_returning_id(
            conn,
            "INSERT INTO career_conversations "
            "(manager_id, team_member_id, conversation_date, topic, notes, next_steps) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (manager_id, team_member_id, conversation_date, topic, notes, next_steps),
        )
        _commit(conn)
    return cid


def list_career_conversations(team_member_id: int, manager_id: int,
                              limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = _fetchall(conn,
            "SELECT * FROM career_conversations "
            "WHERE team_member_id = ? AND manager_id = ? "
            "ORDER BY conversation_date DESC LIMIT ?",
            (team_member_id, manager_id, limit))
    return rows


def add_skill(team_member_id, skill_name, proficiency="developing",
              is_strength=0, is_growth_area=0, notes=None,
              manager_id=None):
    with _connect() as conn:
        if manager_id is None:
            manager_id = _resolve_manager_id_from_member(conn, team_member_id)
        sid = _exec_returning_id(
            conn,
            "INSERT INTO skills "
            "(manager_id, team_member_id, skill_name, proficiency, is_strength, is_growth_area, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (manager_id, team_member_id, skill_name, proficiency, is_strength, is_growth_area, notes),
        )
        _commit(conn)
    return sid


def update_skill(skill_id: int, manager_id: int, **kwargs) -> None:
    allowed = {"skill_name", "proficiency", "is_strength", "is_growth_area", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        _exec(conn,
              f"UPDATE skills SET {set_clause} WHERE id = ? AND manager_id = ?",
              (*fields.values(), skill_id, manager_id))
        _commit(conn)


def list_skills(team_member_id: int, manager_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = _fetchall(conn,
            "SELECT * FROM skills WHERE team_member_id = ? AND manager_id = ? "
            "ORDER BY skill_name",
            (team_member_id, manager_id))
    return rows


def delete_skill(skill_id: int, manager_id: int) -> None:
    with _connect() as conn:
        _exec(conn, "DELETE FROM skills WHERE id = ? AND manager_id = ?",
              (skill_id, manager_id))
        _commit(conn)


def add_development_plan(team_member_id, title, description=None, target_date=None,
                         manager_id=None):
    with _connect() as conn:
        if manager_id is None:
            manager_id = _resolve_manager_id_from_member(conn, team_member_id)
        pid = _exec_returning_id(
            conn,
            "INSERT INTO development_plans "
            "(manager_id, team_member_id, title, description, target_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (manager_id, team_member_id, title, description, target_date),
        )
        _commit(conn)
    return pid


def update_development_plan(plan_id: int, manager_id: int, **kwargs) -> None:
    allowed = {"title", "description", "target_date", "status"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        _exec(conn,
              f"UPDATE development_plans SET {set_clause} WHERE id = ? AND manager_id = ?",
              (*fields.values(), plan_id, manager_id))
        _commit(conn)


def list_development_plans(team_member_id: int, manager_id: int,
                           status: str | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        query = "SELECT * FROM development_plans WHERE team_member_id = ? AND manager_id = ?"
        params = [team_member_id, manager_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        cur = _exec(conn, query, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def add_milestone(plan_id, description, target_date=None, manager_id=None):
    with _connect() as conn:
        if manager_id is None and plan_id is not None:
            row = _fetchone(conn,
                            "SELECT manager_id FROM development_plans WHERE id = ?",
                            (plan_id,))
            manager_id = row["manager_id"] if row else None
        mid = _exec_returning_id(
            conn,
            "INSERT INTO milestones (manager_id, plan_id, description, target_date) "
            "VALUES (?, ?, ?, ?)",
            (manager_id, plan_id, description, target_date),
        )
        _commit(conn)
    return mid


def complete_milestone(milestone_id: int, manager_id: int) -> None:
    with _connect() as conn:
        _exec(conn,
              "UPDATE milestones SET completed = 1, completed_at = ? "
              "WHERE id = ? AND manager_id = ?",
              (datetime.now().isoformat(), milestone_id, manager_id))
        _commit(conn)


def list_milestones(plan_id: int, manager_id: int) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = _fetchall(conn,
            "SELECT * FROM milestones WHERE plan_id = ? AND manager_id = ? ORDER BY id",
            (plan_id, manager_id))
    return rows


def list_milestones_for_plans(plan_ids, manager_id: int) -> dict[int, list[dict[str, Any]]]:
    """Batch fetch: one query, returns {plan_id: [milestone_rows]}.
    Replaces the N+1 loop at web_app.py page_career_development that
    called list_milestones once per plan (P4.2 / AUDIT M4)."""
    plan_ids = list(plan_ids)
    if not plan_ids:
        return {}
    placeholders = ",".join("?" for _ in plan_ids)
    with _connect() as conn:
        rows = _fetchall(conn,
            f"SELECT * FROM milestones "
            f"WHERE plan_id IN ({placeholders}) AND manager_id = ? "
            f"ORDER BY plan_id, id",
            (*plan_ids, manager_id))
    out: dict[int, list[dict[str, Any]]] = {pid: [] for pid in plan_ids}
    for r in rows:
        out.setdefault(r["plan_id"], []).append(r)
    return out


# ---------------------------------------------------------------------------
# Delegation Tracker
# ---------------------------------------------------------------------------

def add_delegation(task: str, team_member_id: int | None = None,
                   outcome_expected: str | None = None,
                   autonomy_level: str = "guided",
                   check_in_date: str | None = None,
                   notes: str | None = None,
                   manager_id: int | None = None) -> int | None:
    """Create a new delegation record."""
    with _connect() as conn:
        did = _exec_returning_id(
            conn,
            "INSERT INTO delegations (manager_id, team_member_id, task, "
            "outcome_expected, autonomy_level, check_in_date, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (manager_id, team_member_id, task, outcome_expected,
             autonomy_level, check_in_date, notes),
        )
        _commit(conn)
    return did


def list_delegations(manager_id: int | None = None,
                     team_member_id: int | None = None,
                     status: str | None = None) -> list[dict[str, Any]]:
    """List delegations with optional filters."""
    with _connect() as conn:
        query = (
            "SELECT d.*, tm.name AS member_name "
            "FROM delegations d "
            "LEFT JOIN team_members tm ON d.team_member_id = tm.id WHERE 1=1"
        )
        params = []
        if manager_id is not None:
            query += " AND d.manager_id = ?"
            params.append(manager_id)
        if team_member_id is not None:
            query += " AND d.team_member_id = ?"
            params.append(team_member_id)
        if status:
            query += " AND d.status = ?"
            params.append(status)
        query += " ORDER BY d.check_in_date, d.created_at DESC"
        cur = _exec(conn, query, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def update_delegation(delegation_id: int, manager_id: int, **kwargs: Any) -> None:
    """Update delegation fields owned by manager_id."""
    allowed = {"task", "outcome_expected", "autonomy_level", "check_in_date",
               "notes", "status"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    if fields.get("status") == "completed":
        fields["completed_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        _exec(conn, f"UPDATE delegations SET {sets} WHERE id = ? AND manager_id = ?",
              (*fields.values(), delegation_id, manager_id))
        _commit(conn)


def delete_delegation(delegation_id: int, manager_id: int) -> None:
    with _connect() as conn:
        _exec(conn, "DELETE FROM delegations WHERE id = ? AND manager_id = ?",
              (delegation_id, manager_id))
        _commit(conn)


def get_active_delegations_count(manager_id: int | None = None) -> int:
    """Count active delegations for nudge/dashboard display."""
    with _connect() as conn:
        sql = "SELECT COUNT(*) AS cnt FROM delegations WHERE status = 'active'"
        params = []
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        row = _fetchone(conn, sql, params or None)
    return row["cnt"] if row else 0


def get_overdue_delegations(manager_id: int | None = None) -> list[dict[str, Any]]:
    """Get delegations past their check-in date."""
    with _connect() as conn:
        cd = _sql_current_date()
        sql = (f"SELECT d.*, tm.name AS member_name "
               f"FROM delegations d "
               f"LEFT JOIN team_members tm ON d.team_member_id = tm.id "
               f"WHERE d.status = 'active' AND d.check_in_date < {cd} "
               f"AND d.check_in_date IS NOT NULL")
        params = []
        if manager_id is not None:
            sql += " AND d.manager_id = ?"
            params.append(manager_id)
        sql += " ORDER BY d.check_in_date"
        rows = _fetchall(conn, sql, params or None)
    return rows


# ---------------------------------------------------------------------------
# Running 1:1 Notes (persistent per-member notes across meetings)
# ---------------------------------------------------------------------------

def add_running_note(team_member_id: int, content: str,
                     category: str = "general",
                     note_date: str | None = None,
                     manager_id: int | None = None) -> int | None:
    """Add a running note for a team member."""
    if not note_date:
        note_date = datetime.now().date().isoformat()
    with _connect() as conn:
        nid = _exec_returning_id(
            conn,
            "INSERT INTO running_notes (manager_id, team_member_id, note_date, "
            "content, category) VALUES (?, ?, ?, ?, ?)",
            (manager_id, team_member_id, note_date, content, category),
        )
        _commit(conn)
    return nid


def list_running_notes(team_member_id: int,
                       manager_id: int | None = None,
                       limit: int = 50) -> list[dict[str, Any]]:
    """Get running notes for a team member, newest first."""
    with _connect() as conn:
        sql = "SELECT * FROM running_notes WHERE team_member_id = ?"
        params: list = [team_member_id]
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        sql += " ORDER BY note_date DESC, created_at DESC LIMIT ?"
        params.append(limit)
        cur = _exec(conn, sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def delete_running_note(note_id: int, manager_id: int) -> None:
    with _connect() as conn:
        _exec(conn, "DELETE FROM running_notes WHERE id = ? AND manager_id = ?",
              (note_id, manager_id))
        _commit(conn)


# ---------------------------------------------------------------------------
# 1:1 Meeting Sessions (the keystone artifact of a weekly 1:1)
# ---------------------------------------------------------------------------
#
# Editable for `LOCK_WINDOW` after `created_at`; thereafter immutable.
# UPSERT on (manager_id, team_member_id, session_date) so a double-click
# on Save updates the existing row rather than creating a duplicate.
#
# `manager_id` is keyword-only and validated with `if x is None: raise
# ValueError(...)` rather than `assert` — `assert` is stripped under
# `python -O`, which would silently disable the cross-tenant guard in
# production. New code must not propagate the existing assert pattern.

def create_one_on_one_session(*, manager_id: int, team_member_id: int,
                              session_date: str,
                              direct_notes: str = "",
                              manager_notes: str = "",
                              followup_notes: str = "",
                              event_id: int | None = None) -> int:
    """Insert a 1:1 session (or UPSERT if one already exists for this
    manager/member/date). Returns the row id.

    When `event_id` is supplied, asserts `event.team_member_id` matches
    the session's `team_member_id` to catch the case where the user
    started a 1:1 from one member's page while a stale event from a
    different member was the latest in cache."""
    if manager_id is None:
        raise ValueError("manager_id required (no implicit cross-tenant)")
    if team_member_id is None:
        raise ValueError("team_member_id required")
    if not session_date:
        raise ValueError("session_date required")

    with _connect() as conn:
        if event_id is not None:
            ev = _fetchone(conn,
                "SELECT manager_id, team_member_id FROM events WHERE id = ?",
                (event_id,))
            if ev is None:
                raise ValueError(f"event {event_id} does not exist")
            if ev["manager_id"] != manager_id:
                raise ValueError(
                    f"event {event_id} belongs to a different manager")
            if ev["team_member_id"] != team_member_id:
                raise ValueError(
                    f"event {event_id} is for team_member {ev['team_member_id']}, "
                    f"not {team_member_id} — refuse to bind a session to a "
                    f"different member's event")

        cur = _exec(conn,
            "INSERT INTO one_on_one_sessions "
            "(manager_id, team_member_id, event_id, session_date, "
            " direct_notes, manager_notes, followup_notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(manager_id, team_member_id, session_date) "
            "DO UPDATE SET "
            "  direct_notes = excluded.direct_notes, "
            "  manager_notes = excluded.manager_notes, "
            "  followup_notes = excluded.followup_notes, "
            "  event_id = COALESCE(excluded.event_id, one_on_one_sessions.event_id), "
            "  updated_at = " + _sql_now(),
            (manager_id, team_member_id, event_id, session_date,
             direct_notes, manager_notes, followup_notes))
        _commit(conn)

        # The INSERT may have returned no row from cur on UPSERT; fetch the
        # canonical id by the unique tuple. This works on both backends.
        row = _fetchone(conn,
            "SELECT id FROM one_on_one_sessions "
            "WHERE manager_id = ? AND team_member_id = ? AND session_date = ?",
            (manager_id, team_member_id, session_date))
    return row["id"]


def get_one_on_one_session(session_id: int, *, manager_id: int) -> dict | None:
    """Fetch a 1:1 session scoped to manager. Returns None for unknown id
    or cross-tenant access."""
    if manager_id is None:
        raise ValueError("manager_id required (no implicit cross-tenant)")
    with _connect() as conn:
        return _fetchone(conn,
            "SELECT * FROM one_on_one_sessions "
            "WHERE id = ? AND manager_id = ?",
            (session_id, manager_id))


def update_one_on_one_session(session_id: int, *, manager_id: int,
                              **kwargs) -> None:
    """Update an editable 1:1 session. Raises PermissionError if the row's
    `created_at` is older than LOCK_WINDOW. Allowed fields:
    direct_notes, manager_notes, followup_notes."""
    if manager_id is None:
        raise ValueError("manager_id required (no implicit cross-tenant)")

    allowed = {"direct_notes", "manager_notes", "followup_notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return

    with _connect() as conn:
        row = _fetchone(conn,
            "SELECT id, created_at FROM one_on_one_sessions "
            "WHERE id = ? AND manager_id = ?",
            (session_id, manager_id))
        if row is None:
            raise ValueError(
                f"session {session_id} not found for this manager")
        if is_session_locked(row["created_at"]):
            raise PermissionError(
                f"session {session_id} is locked (created_at "
                f"{row['created_at']} + {LOCK_WINDOW} has elapsed); "
                f"record is immutable")

        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [session_id, manager_id]
        _exec(conn,
            f"UPDATE one_on_one_sessions SET {sets}, updated_at = {_sql_now()} "
            "WHERE id = ? AND manager_id = ?",
            values)
        _commit(conn)


def list_one_on_one_sessions(*, manager_id: int,
                             team_member_id: int | None = None,
                             limit: int = 20) -> list[dict]:
    """Past sessions ordered session_date DESC, id DESC.
    Optionally filtered to a single member."""
    if manager_id is None:
        raise ValueError("manager_id required (no implicit cross-tenant)")

    sql = "SELECT * FROM one_on_one_sessions WHERE manager_id = ?"
    params: list[Any] = [manager_id]
    if team_member_id is not None:
        sql += " AND team_member_id = ?"
        params.append(team_member_id)
    sql += " ORDER BY session_date DESC, id DESC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        return _fetchall(conn, sql, params)


def get_most_recent_one_on_one(*, manager_id: int,
                               team_member_id: int) -> dict | None:
    """The most recent 1:1 session for this member (any status). Used by
    the live-meeting page to render a carry-over banner from prior
    `followup_notes`."""
    if manager_id is None:
        raise ValueError("manager_id required (no implicit cross-tenant)")
    if team_member_id is None:
        raise ValueError("team_member_id required")
    with _connect() as conn:
        return _fetchone(conn,
            "SELECT * FROM one_on_one_sessions "
            "WHERE manager_id = ? AND team_member_id = ? "
            "ORDER BY session_date DESC, id DESC LIMIT 1",
            (manager_id, team_member_id))


# ---------------------------------------------------------------------------
# Decision Log / Decision Journal
# ---------------------------------------------------------------------------

def add_decision(title: str, context: str | None = None,
                 alternatives: str | None = None,
                 rationale: str | None = None,
                 expected_outcome: str | None = None,
                 review_date: str | None = None,
                 manager_id: int | None = None) -> int | None:
    """Record a decision in the decision log."""
    with _connect() as conn:
        did = _exec_returning_id(
            conn,
            "INSERT INTO decisions (manager_id, title, context, alternatives, "
            "rationale, expected_outcome, review_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (manager_id, title, context, alternatives, rationale,
             expected_outcome, review_date),
        )
        _commit(conn)
    return did


def list_decisions(manager_id: int | None = None,
                   status: str | None = None,
                   limit: int = 50) -> list[dict[str, Any]]:
    """List decisions, newest first."""
    with _connect() as conn:
        sql = "SELECT * FROM decisions WHERE 1=1"
        params: list = []
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur = _exec(conn, sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def update_decision(decision_id: int, manager_id: int, **kwargs: Any) -> None:
    """Update a decision owned by manager_id."""
    allowed = {"title", "context", "alternatives", "rationale",
               "expected_outcome", "review_date", "status", "actual_outcome"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _connect() as conn:
        _exec(conn, f"UPDATE decisions SET {sets} WHERE id = ? AND manager_id = ?",
              (*fields.values(), decision_id, manager_id))
        _commit(conn)


def delete_decision(decision_id: int, manager_id: int) -> None:
    with _connect() as conn:
        _exec(conn, "DELETE FROM decisions WHERE id = ? AND manager_id = ?",
              (decision_id, manager_id))
        _commit(conn)


def get_decisions_due_for_review(manager_id: int | None = None) -> list[dict[str, Any]]:
    """Get decisions where review_date has passed and status is still active."""
    with _connect() as conn:
        cd = _sql_current_date()
        sql = (f"SELECT * FROM decisions WHERE status = 'active' "
               f"AND review_date <= {cd} AND review_date IS NOT NULL")
        params: list = []
        if manager_id is not None:
            sql += " AND manager_id = ?"
            params.append(manager_id)
        sql += " ORDER BY review_date"
        rows = _fetchall(conn, sql, params or None)
    return rows


# ---------------------------------------------------------------------------
# Coach Suggestions (daily cached suggestions)
# ---------------------------------------------------------------------------

def save_coach_suggestion(manager_id: int, suggestion: str,
                          tier: str = "rule",
                          action_page: str | None = None,
                          suggestion_date: str | None = None) -> int | None:
    """Save a coach suggestion, replacing any existing one for the same
    (manager_id, suggestion_date, tier).

    Uses INSERT ... ON CONFLICT DO UPDATE under the unique index so concurrent
    saves cannot produce duplicate or missing rows under autocommit
    (regression for AUDIT H6)."""
    if not suggestion_date:
        suggestion_date = datetime.now().date().isoformat()
    with _connect() as conn:
        sid = _exec_returning_id(
            conn,
            "INSERT INTO coach_suggestions "
            "(manager_id, suggestion_date, tier, suggestion, action_page) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(manager_id, suggestion_date, tier) "
            "DO UPDATE SET suggestion = excluded.suggestion, "
            "              action_page = excluded.action_page, "
            "              dismissed = 0",
            (manager_id, suggestion_date, tier, suggestion, action_page),
        )
        _commit(conn)
    return sid


def get_todays_suggestion(manager_id: int) -> dict[str, Any] | None:
    """Get today's active (non-dismissed) suggestion. Prefers AI tier over rule."""
    today = datetime.now().date().isoformat()
    with _connect() as conn:
        # Try AI tier first, then rule
        row = _fetchone(
            conn,
            "SELECT * FROM coach_suggestions "
            "WHERE manager_id = ? AND suggestion_date = ? AND dismissed = 0 "
            "ORDER BY CASE tier WHEN 'ai' THEN 0 ELSE 1 END LIMIT 1",
            (manager_id, today),
        )
    return row


def dismiss_todays_suggestion(manager_id: int) -> None:
    """Dismiss all of today's suggestions for this manager."""
    today = datetime.now().date().isoformat()
    with _connect() as conn:
        _exec(conn,
              "UPDATE coach_suggestions SET dismissed = 1 "
              "WHERE manager_id = ? AND suggestion_date = ?",
              (manager_id, today))
        _commit(conn)


def get_recent_journal_content(manager_id: int, days: int = 7) -> list[dict[str, Any]]:
    """Get recent journal entries for AI context building."""
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    with _connect() as conn:
        rows = _fetchall(
            conn,
            "SELECT entry_date, entry_type, content, mood, energy "
            "FROM journal_entries WHERE manager_id = ? AND entry_date >= ? "
            "ORDER BY entry_date DESC",
            (manager_id, cutoff),
        )
    return rows
