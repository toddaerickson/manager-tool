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
            manager_id INTEGER NOT NULL DEFAULT 0,
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
            manager_id INTEGER NOT NULL DEFAULT 0,
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
            manager_id INTEGER NOT NULL DEFAULT 0,
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
            manager_id INTEGER NOT NULL DEFAULT 0,
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
            manager_id INTEGER NOT NULL DEFAULT 0,
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
    conn.execute("DELETE FROM self_assessments WHERE week_date = ?", (week_date,))
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
        "WHERE week_date >= date('now', ? || ' days') "
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
    rows = conn.execute("""
        SELECT tm.id AS member_id, tm.name AS member_name,
               MAX(e.scheduled_date) AS last_meeting_date,
               CAST(julianday('now') - julianday(MAX(e.scheduled_date)) AS INTEGER)
                   AS days_since
        FROM team_members tm
        LEFT JOIN events e ON e.team_member_id = tm.id AND e.status = 'completed'
        GROUP BY tm.id
        ORDER BY days_since DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stale_feedback_members(days=21):
    conn = get_connection()
    rows = conn.execute("""
        SELECT tm.id AS member_id, tm.name AS member_name,
               MAX(f.created_at) AS last_feedback_date,
               CAST(julianday('now') - julianday(MAX(f.created_at)) AS INTEGER)
                   AS days_since
        FROM team_members tm
        LEFT JOIN feedback f ON f.team_member_id = tm.id
        GROUP BY tm.id
        HAVING last_feedback_date IS NULL
           OR CAST(julianday('now') - julianday(MAX(f.created_at)) AS INTEGER) > ?
        ORDER BY days_since DESC
    """, (days,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_overdue_action_count():
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM action_items "
        "WHERE status != 'completed' AND due_date < date('now') AND due_date IS NOT NULL"
    ).fetchone()
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
    rows = conn.execute("""
        SELECT tm.name AS member_name,
               strftime('%%Y-%%m', e.scheduled_date) AS month,
               COUNT(*) AS meeting_count
        FROM events e
        JOIN team_members tm ON e.team_member_id = tm.id
        WHERE e.status = 'completed'
          AND e.scheduled_date >= date('now', ? || ' months')
        GROUP BY tm.id, month
        ORDER BY month, tm.name
    """, (str(-months),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    row = conn.execute("""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN status != 'completed' THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN status != 'completed' AND due_date < date('now')
                        AND due_date IS NOT NULL THEN 1 ELSE 0 END) AS overdue
        FROM action_items
    """).fetchone()
    conn.close()
    return dict(row) if row else {"total": 0, "completed": 0, "pending": 0, "overdue": 0}


def get_manager_activity_trends(weeks=12):
    conn = get_connection()
    rows = conn.execute("""
        SELECT week, SUM(events) AS events, SUM(feedback) AS feedback,
               SUM(actions) AS actions
        FROM (
            SELECT strftime('%%Y-%%W', scheduled_date) AS week,
                   COUNT(*) AS events, 0 AS feedback, 0 AS actions
            FROM events WHERE status = 'completed'
              AND scheduled_date >= date('now', ? || ' days')
            GROUP BY week
            UNION ALL
            SELECT strftime('%%Y-%%W', created_at) AS week,
                   0, COUNT(*), 0
            FROM feedback
            WHERE created_at >= date('now', ? || ' days')
            GROUP BY week
            UNION ALL
            SELECT strftime('%%Y-%%W', created_at) AS week,
                   0, 0, COUNT(*)
            FROM action_items
            WHERE created_at >= date('now', ? || ' days')
            GROUP BY week
        )
        GROUP BY week ORDER BY week
    """, (str(-weeks * 7), str(-weeks * 7), str(-weeks * 7))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Member Timeline & Pre-Meeting Prep
# ---------------------------------------------------------------------------

def get_member_timeline(member_id, limit=50):
    conn = get_connection()
    rows = conn.execute("""
        SELECT date, type, summary, detail, source_id FROM (
            SELECT scheduled_date AS date, 'event' AS type,
                   title AS summary, notes AS detail, id AS source_id
            FROM events WHERE team_member_id = ?
            UNION ALL
            SELECT substr(created_at, 1, 10) AS date,
                   feedback_type || '_feedback' AS type,
                   COALESCE(situation, '') AS summary,
                   COALESCE(behavior, '') || ' → ' || COALESCE(impact, '') AS detail,
                   id AS source_id
            FROM feedback WHERE team_member_id = ?
            UNION ALL
            SELECT substr(created_at, 1, 10) AS date, 'goal' AS type,
                   description AS summary, status AS detail, id AS source_id
            FROM goals WHERE team_member_id = ?
            UNION ALL
            SELECT conversation_date AS date, 'career' AS type,
                   COALESCE(topic, 'Career conversation') AS summary,
                   notes AS detail, id AS source_id
            FROM career_conversations WHERE team_member_id = ?
        )
        ORDER BY date DESC LIMIT ?
    """, (member_id, member_id, member_id, member_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
