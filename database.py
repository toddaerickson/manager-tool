"""
Database layer for Manager Tool.
Dual-mode: PostgreSQL (Supabase) in production, SQLite for local dev.
Set DATABASE_URL env var or Streamlit secret to use PostgreSQL.
"""

import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manager_data.db")

# ---------------------------------------------------------------------------
# Dual-mode connection: PostgreSQL (production) / SQLite (local dev)
# ---------------------------------------------------------------------------

_USE_PG = None


def _detect_pg():
    """Detect if we should use PostgreSQL."""
    global _USE_PG
    if _USE_PG is not None:
        return _USE_PG
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        try:
            import streamlit as st
            url = st.secrets.get("DATABASE_URL", "")
        except Exception:
            url = ""
    _USE_PG = bool(url)
    return _USE_PG


def _get_pg_url():
    """Get PostgreSQL connection URL."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        try:
            import streamlit as st
            url = st.secrets.get("DATABASE_URL", "")
        except Exception:
            pass
    return url


def _q(sql):
    """Convert ? placeholders to %s for PostgreSQL."""
    if _detect_pg():
        return sql.replace("?", "%s")
    return sql


def get_connection():
    if _detect_pg():
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(_get_pg_url(), cursor_factory=RealDictCursor)
            conn.autocommit = True
            return conn
        except Exception:
            # Fall back to SQLite if PostgreSQL connection fails
            global _USE_PG
            _USE_PG = False
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _exec(conn, sql, params=None):
    """Execute SQL with automatic placeholder conversion."""
    cur = conn.cursor()
    if params:
        cur.execute(_q(sql), params)
    else:
        cur.execute(_q(sql))
    return cur


def _exec_returning_id(conn, sql, params=None):
    """Execute INSERT and return the new row ID. Handles PG vs SQLite."""
    if _detect_pg():
        cur = conn.cursor()
        cur.execute(_q(sql) + " RETURNING id", params or ())
        row = cur.fetchone()
        return row["id"] if row else None
    else:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
        return cur.lastrowid


def _fetchone(conn, sql, params=None):
    """Fetch one row as dict."""
    cur = _exec(conn, sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def _fetchall(conn, sql, params=None):
    """Fetch all rows as list of dicts."""
    cur = _exec(conn, sql, params)
    return [dict(r) for r in cur.fetchall()]


def _commit(conn):
    """Commit for SQLite (PostgreSQL uses autocommit)."""
    if not _detect_pg():
        conn.commit()


# ---------------------------------------------------------------------------
# SQL dialect helpers — generate correct SQL for SQLite or PostgreSQL
# ---------------------------------------------------------------------------

def _sql_now():
    """SQL expression for current timestamp."""
    return "NOW()" if _detect_pg() else "datetime('now')"


def _sql_current_date():
    """SQL expression for current date."""
    return "CURRENT_DATE" if _detect_pg() else "date('now')"


def _sql_days_since(date_col):
    """SQL expression: integer days between now and a date column."""
    if _detect_pg():
        return f"EXTRACT(DAY FROM NOW() - {date_col}::timestamp)::int"
    return f"CAST(julianday('now') - julianday({date_col}) AS INTEGER)"


def _sql_date_offset(days_param):
    """SQL expression: date N days ago. Pass the parameter placeholder."""
    if _detect_pg():
        return f"CURRENT_DATE - ({days_param} || ' days')::interval"
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


def init_db():
    """Initialize database tables (SQLite only — PostgreSQL uses schema_postgres.sql)."""
    if _detect_pg():
        return  # Tables created via Supabase SQL editor
    # Migrate: if old schema has NOT NULL manager_id, drop and recreate
    if os.path.exists(DB_PATH):
        try:
            conn = sqlite3.connect(DB_PATH)
            info = conn.execute("PRAGMA table_info(journal_entries)").fetchall()
            for col in info:
                if col[1] == "manager_id" and col[3] == 1:  # notnull=1
                    conn.close()
                    os.remove(DB_PATH)
                    break
            else:
                conn.close()
        except Exception:
            pass
    conn = get_connection()
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
            team_member_id INTEGER NOT NULL,
            event_id INTEGER,
            feedback_type TEXT NOT NULL CHECK(feedback_type IN
                ('positive', 'constructive')),
            situation TEXT,
            behavior TEXT,
            impact TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id),
            FOREIGN KEY (event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_member_id INTEGER NOT NULL,
            quarter TEXT NOT NULL,
            description TEXT NOT NULL,
            key_results TEXT,
            status TEXT DEFAULT 'not_started' CHECK(status IN
                ('not_started', 'in_progress', 'met', 'exceeded',
                 'partially_met', 'not_met')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
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
            team_member_id INTEGER NOT NULL,
            conversation_date TEXT NOT NULL,
            topic TEXT,
            notes TEXT,
            next_steps TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_member_id INTEGER NOT NULL,
            skill_name TEXT NOT NULL,
            proficiency TEXT DEFAULT 'developing'
                CHECK(proficiency IN ('learning', 'developing', 'proficient', 'expert')),
            is_strength INTEGER DEFAULT 0,
            is_growth_area INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS development_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_member_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            target_date TEXT,
            status TEXT DEFAULT 'active'
                CHECK(status IN ('active', 'completed', 'paused')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            target_date TEXT,
            completed INTEGER DEFAULT 0,
            completed_at TEXT,
            FOREIGN KEY (plan_id) REFERENCES development_plans(id)
        );
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Manager Profiles & Authentication
# ---------------------------------------------------------------------------

def create_manager(username, display_name, password, email=None,
                   work_schedule=None, timezone=None):
    """Create a new manager account. Returns manager_id."""
    import hashlib
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO managers (username, display_name, email, password_hash, "
            "work_schedule, timezone) VALUES (?, ?, ?, ?, ?, ?)",
            (username.lower().strip(), display_name, email, pw_hash,
             work_schedule, timezone),
        )
        conn.commit()
        mid = cursor.lastrowid
    except Exception:
        conn.close()
        return None
    conn.close()
    return mid


def authenticate_manager(username, password):
    """Verify credentials. Returns manager dict or None."""
    import hashlib
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM managers WHERE username = ? AND password_hash = ?",
        (username.lower().strip(), pw_hash),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_manager(manager_id):
    """Get manager profile by ID."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM managers WHERE id = ?", (manager_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_manager(manager_id, **kwargs):
    """Update manager profile fields."""
    allowed = {"display_name", "email", "work_schedule", "timezone"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    conn.execute(
        f"UPDATE managers SET {set_clause} WHERE id = ?",
        (*fields.values(), manager_id),
    )
    conn.commit()
    conn.close()


def update_manager_password(manager_id, new_password):
    """Change manager password."""
    import hashlib
    pw_hash = hashlib.sha256(new_password.encode()).hexdigest()
    conn = get_connection()
    conn.execute(
        "UPDATE managers SET password_hash = ?, updated_at = ? WHERE id = ?",
        (pw_hash, datetime.now().isoformat(), manager_id),
    )
    conn.commit()
    conn.close()


def manager_exists(username):
    """Check if a username is taken."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM managers WHERE username = ?",
        (username.lower().strip(),),
    ).fetchone()
    conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def set_config(key, value):
    conn = get_connection()
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_config(key, default=None):
    conn = get_connection()
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def upsert_user(google_id, email, name=None, picture=None):
    """Insert or update a user record on login."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO users (google_id, email, name, picture, last_login) "
        "VALUES (?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(google_id) DO UPDATE SET "
        "email = excluded.email, name = excluded.name, "
        "picture = excluded.picture, last_login = datetime('now')",
        (google_id, email, name, picture),
    )
    conn.commit()
    conn.close()


def get_user_by_google_id(google_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE google_id = ?", (google_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users ORDER BY last_login DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_config():
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM config ORDER BY key").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Team Members
# ---------------------------------------------------------------------------

def add_team_member(name, email=None, role=None, start_date=None, notes=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO team_members (name, email, role, start_date, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, email, role, start_date, notes),
    )
    member_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return member_id


def update_team_member(member_id, **kwargs):
    conn = get_connection()
    allowed = {"name", "email", "role", "start_date", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        conn.close()
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [datetime.now().isoformat(), member_id]
    conn.execute(
        f"UPDATE team_members SET {sets}, updated_at = ? WHERE id = ?", values
    )
    conn.commit()
    conn.close()


def get_team_member(member_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM team_members WHERE id = ?", (member_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_team_member_by_name(name):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM team_members WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_team_members():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM team_members ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_team_member(member_id):
    conn = get_connection()
    conn.execute("DELETE FROM team_members WHERE id = ?", (member_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def create_event(title, event_type, scheduled_date, scheduled_time,
                 team_member_id=None, duration_minutes=30,
                 location=None, agenda=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO events (title, event_type, team_member_id, scheduled_date, "
        "scheduled_time, duration_minutes, location, agenda) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (title, event_type, team_member_id, scheduled_date,
         scheduled_time, duration_minutes, location, agenda),
    )
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return event_id


def update_event(event_id, **kwargs):
    conn = get_connection()
    allowed = {
        "title", "event_type", "team_member_id", "scheduled_date",
        "scheduled_time", "duration_minutes", "location", "agenda",
        "status", "notes", "calendar_invite_sent",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        conn.close()
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [datetime.now().isoformat(), event_id]
    conn.execute(
        f"UPDATE events SET {sets}, updated_at = ? WHERE id = ?", values
    )
    conn.commit()
    conn.close()


def complete_event(event_id, notes=None):
    update_event(event_id, status="completed", notes=notes)


def cancel_event(event_id):
    update_event(event_id, status="cancelled")


def get_event(event_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT e.*, tm.name AS participant_name, tm.email AS participant_email "
        "FROM events e "
        "LEFT JOIN team_members tm ON e.team_member_id = tm.id "
        "WHERE e.id = ?",
        (event_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_events(event_type=None, status=None, team_member_id=None,
                from_date=None, to_date=None, limit=50):
    conn = get_connection()
    query = (
        "SELECT e.*, tm.name AS participant_name, tm.email AS participant_email "
        "FROM events e "
        "LEFT JOIN team_members tm ON e.team_member_id = tm.id WHERE 1=1"
    )
    params = []

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

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_upcoming_events(days=7):
    today = datetime.now().strftime("%Y-%m-%d")
    future = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    return list_events(status="scheduled", from_date=today, to_date=future)


def get_event_history(team_member_id, limit=20):
    return list_events(team_member_id=team_member_id, status="completed", limit=limit)


# ---------------------------------------------------------------------------
# Action Items
# ---------------------------------------------------------------------------

def add_action_item(description, event_id=None, assignee=None, due_date=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO action_items (event_id, description, assignee, due_date) "
        "VALUES (?, ?, ?, ?)",
        (event_id, description, assignee, due_date),
    )
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return item_id


def complete_action_item(item_id):
    conn = get_connection()
    conn.execute(
        "UPDATE action_items SET status = 'completed', completed_at = ? WHERE id = ?",
        (datetime.now().isoformat(), item_id),
    )
    conn.commit()
    conn.close()


def update_action_item_status(item_id, status):
    conn = get_connection()
    completed_at = datetime.now().isoformat() if status == "completed" else None
    conn.execute(
        "UPDATE action_items SET status = ?, completed_at = ? WHERE id = ?",
        (status, completed_at, item_id),
    )
    conn.commit()
    conn.close()


def list_action_items(event_id=None, status=None, assignee=None):
    conn = get_connection()
    query = (
        "SELECT ai.*, e.title AS event_title "
        "FROM action_items ai "
        "LEFT JOIN events e ON ai.event_id = e.id WHERE 1=1"
    )
    params = []
    if event_id:
        query += " AND ai.event_id = ?"
        params.append(event_id)
    if status:
        query += " AND ai.status = ?"
        params.append(status)
    if assignee:
        query += " AND LOWER(ai.assignee) = LOWER(?)"
        params.append(assignee)
    query += " ORDER BY ai.due_date, ai.created_at"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_action_items():
    return list_action_items(status="pending") + list_action_items(status="in_progress")


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def add_feedback(team_member_id, feedback_type, situation=None,
                 behavior=None, impact=None, event_id=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO feedback (team_member_id, event_id, feedback_type, "
        "situation, behavior, impact) VALUES (?, ?, ?, ?, ?, ?)",
        (team_member_id, event_id, feedback_type, situation, behavior, impact),
    )
    feedback_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return feedback_id


def list_feedback(team_member_id=None, feedback_type=None):
    conn = get_connection()
    query = (
        "SELECT f.*, tm.name AS member_name "
        "FROM feedback f "
        "JOIN team_members tm ON f.team_member_id = tm.id WHERE 1=1"
    )
    params = []
    if team_member_id:
        query += " AND f.team_member_id = ?"
        params.append(team_member_id)
    if feedback_type:
        query += " AND f.feedback_type = ?"
        params.append(feedback_type)
    query += " ORDER BY f.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def add_goal(team_member_id, quarter, description, key_results=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO goals (team_member_id, quarter, description, key_results) "
        "VALUES (?, ?, ?, ?)",
        (team_member_id, quarter, description, key_results),
    )
    goal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return goal_id


def update_goal(goal_id, **kwargs):
    conn = get_connection()
    allowed = {"description", "key_results", "status", "quarter"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        conn.close()
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [datetime.now().isoformat(), goal_id]
    conn.execute(f"UPDATE goals SET {sets}, updated_at = ? WHERE id = ?", values)
    conn.commit()
    conn.close()


def list_goals(team_member_id=None, quarter=None, status=None):
    conn = get_connection()
    query = (
        "SELECT g.*, tm.name AS member_name "
        "FROM goals g "
        "JOIN team_members tm ON g.team_member_id = tm.id WHERE 1=1"
    )
    params = []
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
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Reports / Aggregations
# ---------------------------------------------------------------------------

def get_weekly_summary():
    """Get a summary of activity for the current week."""
    today = datetime.now()
    monday = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    sunday = (today + timedelta(days=6 - today.weekday())).strftime("%Y-%m-%d")

    conn = get_connection()
    summary = {}

    summary["upcoming_events"] = list_events(
        status="scheduled", from_date=monday, to_date=sunday
    )
    summary["completed_events"] = list_events(
        status="completed", from_date=monday, to_date=sunday
    )
    summary["pending_actions"] = get_pending_action_items()

    overdue = conn.execute(
        "SELECT * FROM action_items WHERE status != 'completed' "
        "AND due_date < ? ORDER BY due_date",
        (today.strftime("%Y-%m-%d"),),
    ).fetchall()
    summary["overdue_actions"] = [dict(r) for r in overdue]

    conn.close()
    return summary


def get_member_summary(team_member_id):
    """Get a full activity summary for a single team member."""
    member = get_team_member(team_member_id)
    if not member:
        return None

    conn = get_connection()
    summary = {"member": member}

    summary["recent_events"] = list_events(
        team_member_id=team_member_id, limit=10
    )
    summary["feedback"] = list_feedback(team_member_id=team_member_id)
    summary["goals"] = list_goals(team_member_id=team_member_id)

    actions = conn.execute(
        "SELECT ai.* FROM action_items ai "
        "JOIN events e ON ai.event_id = e.id "
        "WHERE e.team_member_id = ? ORDER BY ai.created_at DESC LIMIT 20",
        (team_member_id,),
    ).fetchall()
    summary["action_items"] = [dict(r) for r in actions]

    conn.close()
    return summary


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

def add_journal_entry(entry_date, entry_type="daily", content=None,
                      mood=None, energy=None, private_notes=None, tags=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO journal_entries "
        "(entry_date, entry_type, content, mood, energy, private_notes, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (entry_date, entry_type, content, mood, energy, private_notes, tags),
    )
    conn.commit()
    entry_id = cursor.lastrowid
    conn.close()
    return entry_id


def update_journal_entry(entry_id, **kwargs):
    allowed = {"content", "mood", "energy", "private_notes", "tags", "entry_type"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    conn.execute(
        f"UPDATE journal_entries SET {set_clause} WHERE id = ?",
        (*fields.values(), entry_id),
    )
    conn.commit()
    conn.close()


def get_journal_entry_by_date(entry_date, entry_type="daily"):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM journal_entries WHERE entry_date = ? AND entry_type = ?",
        (entry_date, entry_type),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_journal_entries(entry_type=None, limit=30):
    conn = get_connection()
    query = "SELECT * FROM journal_entries WHERE 1=1"
    params = []
    if entry_type:
        query += " AND entry_type = ?"
        params.append(entry_type)
    query += " ORDER BY entry_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_journal_streak():
    """Count consecutive days with a journal entry ending today."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT entry_date FROM journal_entries "
        "ORDER BY entry_date DESC LIMIT 365"
    ).fetchall()
    conn.close()
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

def save_self_assessment(week_date, scores_dict):
    """Save or replace self-assessment scores for a week.
    scores_dict: {dimension_name: score}"""
    conn = get_connection()
    _exec(conn, "DELETE FROM self_assessments WHERE week_date = ?", (week_date,))
    for dim, score in scores_dict.items():
        conn.execute(
            "INSERT INTO self_assessments (week_date, dimension, score) "
            "VALUES (?, ?, ?)",
            (week_date, dim, score),
        )
    conn.commit()
    conn.close()


def get_self_assessment_trends(weeks=12):
    conn = get_connection()
    rows = conn.execute(
        "SELECT week_date, dimension, score FROM self_assessments "
        f"WHERE week_date >= {_sql_date_offset('?')} "
        "ORDER BY week_date, dimension",
        (str(-weeks * 7),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_self_assessment():
    conn = get_connection()
    rows = conn.execute(
        "SELECT dimension, score FROM self_assessments "
        "WHERE week_date = (SELECT MAX(week_date) FROM self_assessments)"
    ).fetchall()
    conn.close()
    return {r["dimension"]: r["score"] for r in rows}


# ---------------------------------------------------------------------------
# Nudges
# ---------------------------------------------------------------------------

def get_time_since_last_event_per_member():
    conn = get_connection()
    days_expr = _sql_days_since("MAX(e.scheduled_date)")
    rows = _fetchall(conn, f"""
        SELECT tm.id AS member_id, tm.name AS member_name,
               MAX(e.scheduled_date) AS last_meeting_date,
               {days_expr} AS days_since
        FROM team_members tm
        LEFT JOIN events e ON e.team_member_id = tm.id AND e.status = 'completed'
        GROUP BY tm.id, tm.name
        ORDER BY days_since DESC
    """)
    conn.close()
    return rows


def get_stale_feedback_members(days=21):
    conn = get_connection()
    days_expr = _sql_days_since("MAX(f.created_at)")
    rows = _fetchall(conn, f"""
        SELECT tm.id AS member_id, tm.name AS member_name,
               MAX(f.created_at) AS last_feedback_date,
               {days_expr} AS days_since
        FROM team_members tm
        LEFT JOIN feedback f ON f.team_member_id = tm.id
        GROUP BY tm.id, tm.name
        HAVING MAX(f.created_at) IS NULL
           OR {days_expr} > ?
        ORDER BY days_since DESC
    """, (days,))
    conn.close()
    return rows


def get_overdue_action_count():
    conn = get_connection()
    row = _fetchone(conn,
        f"SELECT COUNT(*) AS cnt FROM action_items "
        f"WHERE status != 'completed' AND due_date < {_sql_current_date()} "
        f"AND due_date IS NOT NULL")
    conn.close()
    return row["cnt"] if row else 0


def get_nudges():
    """Aggregate all nudges, sorted by severity."""
    nudges = []
    for m in get_time_since_last_event_per_member():
        days = m["days_since"]
        if days is None:
            nudges.append({
                "type": "meeting", "severity": "critical",
                "message": f"You have never had a recorded meeting with {m['member_name']}.",
                "member_id": m["member_id"],
            })
        elif days > 21:
            nudges.append({
                "type": "meeting", "severity": "critical",
                "message": f"It's been {days} days since your last meeting with {m['member_name']}.",
                "member_id": m["member_id"],
            })
        elif days > 14:
            nudges.append({
                "type": "meeting", "severity": "warning",
                "message": f"It's been {days} days since your last meeting with {m['member_name']}.",
                "member_id": m["member_id"],
            })

    overdue = get_overdue_action_count()
    if overdue > 0:
        nudges.append({
            "type": "action", "severity": "warning",
            "message": f"{overdue} overdue action item(s) need attention.",
            "member_id": None,
        })

    for m in get_stale_feedback_members(days=21):
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
            days=datetime.now().date().weekday())).isoformat(), "weekly")
    if not last_weekly:
        # Check if ANY weekly entry in last 7 days
        recent_weekly = list_journal_entries(entry_type="weekly", limit=1)
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

def get_meetings_per_member_per_month(months=6):
    conn = get_connection()
    month_expr = _sql_month("e.scheduled_date")
    date_offset = _sql_date_offset("?")
    rows = _fetchall(conn, f"""
        SELECT tm.name AS member_name,
               {month_expr} AS month,
               COUNT(*) AS meeting_count
        FROM events e
        JOIN team_members tm ON e.team_member_id = tm.id
        WHERE e.status = 'completed'
          AND e.scheduled_date >= {date_offset}
        GROUP BY tm.id, tm.name, {month_expr}
        ORDER BY month, tm.name
    """, (str(-months * 30),))
    conn.close()
    return rows


def get_feedback_ratios():
    conn = get_connection()
    rows = conn.execute("""
        SELECT tm.name AS member_name,
               SUM(CASE WHEN f.feedback_type = 'positive' THEN 1 ELSE 0 END)
                   AS positive_count,
               SUM(CASE WHEN f.feedback_type = 'constructive' THEN 1 ELSE 0 END)
                   AS constructive_count,
               COUNT(*) AS total_count
        FROM feedback f
        JOIN team_members tm ON f.team_member_id = tm.id
        GROUP BY tm.id
        ORDER BY tm.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_goal_completion_rates():
    conn = get_connection()
    rows = conn.execute("""
        SELECT tm.name AS member_name, g.status, COUNT(*) AS cnt
        FROM goals g
        JOIN team_members tm ON g.team_member_id = tm.id
        GROUP BY tm.id, g.status
        ORDER BY tm.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_action_stats():
    conn = get_connection()
    cd = _sql_current_date()
    row = _fetchone(conn, f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN status != 'completed' THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN status != 'completed' AND due_date < {cd}
                        AND due_date IS NOT NULL THEN 1 ELSE 0 END) AS overdue
        FROM action_items
    """)
    conn.close()
    return row if row else {"total": 0, "completed": 0, "pending": 0, "overdue": 0}


def get_manager_activity_trends(weeks=12):
    conn = get_connection()
    wk = _sql_week
    dt = _sql_date_offset("?")
    rows = _fetchall(conn, f"""
        SELECT week, SUM(events) AS events, SUM(feedback) AS feedback,
               SUM(actions) AS actions
        FROM (
            SELECT {wk('scheduled_date')} AS week,
                   COUNT(*) AS events, 0 AS feedback, 0 AS actions
            FROM events WHERE status = 'completed'
              AND scheduled_date >= {dt}
            GROUP BY {wk('scheduled_date')}
            UNION ALL
            SELECT {wk('created_at')} AS week,
                   0, COUNT(*), 0
            FROM feedback
            WHERE created_at >= {dt}
            GROUP BY {wk('created_at')}
            UNION ALL
            SELECT {wk('created_at')} AS week,
                   0, 0, COUNT(*)
            FROM action_items
            WHERE created_at >= {dt}
            GROUP BY {wk('created_at')}
        ) sub
        GROUP BY week ORDER BY week
    """, (str(-weeks * 7), str(-weeks * 7), str(-weeks * 7)))
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Member Timeline & Pre-Meeting Prep
# ---------------------------------------------------------------------------

def get_member_timeline(member_id, limit=50):
    conn = get_connection()
    left10 = _sql_left("created_at", 10)
    rows = _fetchall(conn, f"""
        SELECT date, type, summary, detail, source_id FROM (
            SELECT scheduled_date AS date, 'event' AS type,
                   title AS summary, notes AS detail, id AS source_id
            FROM events WHERE team_member_id = ?
            UNION ALL
            SELECT {left10} AS date,
                   feedback_type || '_feedback' AS type,
                   COALESCE(situation, '') AS summary,
                   COALESCE(behavior, '') || ' → ' || COALESCE(impact, '') AS detail,
                   id AS source_id
            FROM feedback WHERE team_member_id = ?
            UNION ALL
            SELECT {left10} AS date, 'goal' AS type,
                   description AS summary, status AS detail, id AS source_id
            FROM goals WHERE team_member_id = ?
            UNION ALL
            SELECT conversation_date AS date, 'career' AS type,
                   COALESCE(topic, 'Career conversation') AS summary,
                   notes AS detail, id AS source_id
            FROM career_conversations WHERE team_member_id = ?
        ) timeline
        ORDER BY date DESC LIMIT ?
    """, (member_id, member_id, member_id, member_id, limit))
    conn.close()
    return rows


def get_pre_meeting_prep(member_id):
    conn = get_connection()
    member = conn.execute(
        "SELECT * FROM team_members WHERE id = ?", (member_id,)
    ).fetchone()
    if not member:
        conn.close()
        return None

    prep = {"member": dict(member)}

    # Last meeting
    last_evt = conn.execute(
        "SELECT scheduled_date FROM events "
        "WHERE team_member_id = ? AND status = 'completed' "
        "ORDER BY scheduled_date DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    if last_evt:
        prep["last_meeting_date"] = last_evt["scheduled_date"]
        prep["days_since_meeting"] = (
            datetime.now().date() - datetime.fromisoformat(last_evt["scheduled_date"]).date()
        ).days
    else:
        prep["last_meeting_date"] = None
        prep["days_since_meeting"] = None

    # Last feedback
    last_fb = conn.execute(
        "SELECT created_at FROM feedback WHERE team_member_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (member_id,),
    ).fetchone()
    if last_fb:
        prep["last_feedback_date"] = last_fb["created_at"][:10]
        prep["days_since_feedback"] = (
            datetime.now().date() - datetime.fromisoformat(last_fb["created_at"][:10]).date()
        ).days
    else:
        prep["last_feedback_date"] = None
        prep["days_since_feedback"] = None

    # Feedback ratio
    ratios = conn.execute(
        "SELECT feedback_type, COUNT(*) AS cnt FROM feedback "
        "WHERE team_member_id = ? GROUP BY feedback_type",
        (member_id,),
    ).fetchall()
    prep["positive_count"] = 0
    prep["constructive_count"] = 0
    for r in ratios:
        if r["feedback_type"] == "positive":
            prep["positive_count"] = r["cnt"]
        else:
            prep["constructive_count"] = r["cnt"]

    # Pending actions (match by member name through events)
    name = member["name"]
    prep["pending_actions"] = conn.execute(
        "SELECT COUNT(*) AS cnt FROM action_items "
        "WHERE status != 'completed' AND ("
        "  LOWER(assignee) = LOWER(?) "
        "  OR event_id IN (SELECT id FROM events WHERE team_member_id = ?)"
        ")",
        (name, member_id),
    ).fetchone()["cnt"]

    # Active goals
    goals = conn.execute(
        "SELECT quarter, description, status FROM goals "
        "WHERE team_member_id = ? AND status IN ('not_started', 'in_progress') "
        "ORDER BY quarter DESC",
        (member_id,),
    ).fetchall()
    prep["active_goals"] = [dict(g) for g in goals]

    # Recent feedback
    recent_fb = conn.execute(
        "SELECT feedback_type, situation, behavior, impact, created_at "
        "FROM feedback WHERE team_member_id = ? "
        "ORDER BY created_at DESC LIMIT 3",
        (member_id,),
    ).fetchall()
    prep["recent_feedback"] = [dict(f) for f in recent_fb]

    conn.close()
    return prep


# ---------------------------------------------------------------------------
# Career Development
# ---------------------------------------------------------------------------

def add_career_conversation(team_member_id, conversation_date,
                            topic=None, notes=None, next_steps=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO career_conversations "
        "(team_member_id, conversation_date, topic, notes, next_steps) "
        "VALUES (?, ?, ?, ?, ?)",
        (team_member_id, conversation_date, topic, notes, next_steps),
    )
    conn.commit()
    cid = cursor.lastrowid
    conn.close()
    return cid


def list_career_conversations(team_member_id, limit=20):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM career_conversations WHERE team_member_id = ? "
        "ORDER BY conversation_date DESC LIMIT ?",
        (team_member_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_skill(team_member_id, skill_name, proficiency="developing",
              is_strength=0, is_growth_area=0, notes=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO skills "
        "(team_member_id, skill_name, proficiency, is_strength, is_growth_area, notes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (team_member_id, skill_name, proficiency, is_strength, is_growth_area, notes),
    )
    conn.commit()
    sid = cursor.lastrowid
    conn.close()
    return sid


def update_skill(skill_id, **kwargs):
    allowed = {"skill_name", "proficiency", "is_strength", "is_growth_area", "notes"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    conn.execute(
        f"UPDATE skills SET {set_clause} WHERE id = ?",
        (*fields.values(), skill_id),
    )
    conn.commit()
    conn.close()


def list_skills(team_member_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM skills WHERE team_member_id = ? ORDER BY skill_name",
        (team_member_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_skill(skill_id):
    conn = get_connection()
    conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
    conn.commit()
    conn.close()


def add_development_plan(team_member_id, title, description=None, target_date=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO development_plans "
        "(team_member_id, title, description, target_date) VALUES (?, ?, ?, ?)",
        (team_member_id, title, description, target_date),
    )
    conn.commit()
    pid = cursor.lastrowid
    conn.close()
    return pid


def update_development_plan(plan_id, **kwargs):
    allowed = {"title", "description", "target_date", "status"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_connection()
    conn.execute(
        f"UPDATE development_plans SET {set_clause} WHERE id = ?",
        (*fields.values(), plan_id),
    )
    conn.commit()
    conn.close()


def list_development_plans(team_member_id, status=None):
    conn = get_connection()
    query = "SELECT * FROM development_plans WHERE team_member_id = ?"
    params = [team_member_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_milestone(plan_id, description, target_date=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO milestones (plan_id, description, target_date) VALUES (?, ?, ?)",
        (plan_id, description, target_date),
    )
    conn.commit()
    mid = cursor.lastrowid
    conn.close()
    return mid


def complete_milestone(milestone_id):
    conn = get_connection()
    conn.execute(
        "UPDATE milestones SET completed = 1, completed_at = ? WHERE id = ?",
        (datetime.now().isoformat(), milestone_id),
    )
    conn.commit()
    conn.close()


def list_milestones(plan_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM milestones WHERE plan_id = ? ORDER BY id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
