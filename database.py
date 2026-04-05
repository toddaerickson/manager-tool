"""
Database layer for Manager Task Generator.
Uses SQLite for persistent local storage of events, team members,
action items, feedback, goals, and configuration.
"""

import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manager_data.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initialize all database tables."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS team_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            role TEXT,
            start_date TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            FOREIGN KEY (team_member_id) REFERENCES team_members(id)
        );

        CREATE TABLE IF NOT EXISTS action_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER,
            description TEXT NOT NULL,
            assignee TEXT,
            due_date TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN
                ('pending', 'in_progress', 'completed')),
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (event_id) REFERENCES events(id)
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

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            name TEXT,
            picture TEXT,
            last_login TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


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
