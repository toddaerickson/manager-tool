-- Manager Tool: PostgreSQL schema (Neon / any standard PostgreSQL provider)
-- Run via: psql "$DATABASE_URL" -f schema_postgres.sql

CREATE TABLE IF NOT EXISTS managers (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    work_schedule TEXT DEFAULT '{"days": ["Mon","Tue","Wed","Thu","Fri"], "start": "09:00", "end": "17:00"}',
    timezone TEXT DEFAULT 'America/New_York',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Migration ledger (P2.1). Records which sequenced migrations have been applied
-- so they don't re-run on startup.
CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Server-side sessions (P2.3 / AUDIT H3). Token-based session validation
-- replaces the per-tab st.session_state-only auth.
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    manager_id INTEGER NOT NULL REFERENCES managers(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    user_agent_hash TEXT
);

-- Persistent failed-login counters (P2.3 / AUDIT H2). Keyed by username so
-- attempts can't be reset by opening a new tab.
CREATE TABLE IF NOT EXISTS login_attempts (
    username TEXT PRIMARY KEY,
    failed_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMP NOT NULL,
    locked_until TIMESTAMP
);

CREATE TABLE IF NOT EXISTS team_members (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER,
    name TEXT NOT NULL,
    email TEXT,
    role TEXT,
    start_date TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER,
    title TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN
        ('check_in', 'coaching', 'one_on_one', 'quarterly_review', 'other')),
    team_member_id INTEGER REFERENCES team_members(id),
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    duration_minutes INTEGER DEFAULT 30,
    location TEXT,
    agenda TEXT,
    status TEXT DEFAULT 'scheduled' CHECK(status IN
        ('scheduled', 'completed', 'cancelled', 'rescheduled')),
    notes TEXT,
    calendar_invite_sent INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS action_items (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER,
    event_id INTEGER REFERENCES events(id),
    description TEXT NOT NULL,
    assignee TEXT,
    due_date TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN
        ('pending', 'in_progress', 'completed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(id),
    team_member_id INTEGER NOT NULL REFERENCES team_members(id),
    event_id INTEGER REFERENCES events(id),
    feedback_type TEXT NOT NULL CHECK(feedback_type IN
        ('positive', 'constructive')),
    situation TEXT,
    behavior TEXT,
    impact TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS goals (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(id),
    team_member_id INTEGER NOT NULL REFERENCES team_members(id),
    quarter TEXT NOT NULL,
    description TEXT NOT NULL,
    key_results TEXT,
    status TEXT DEFAULT 'not_started' CHECK(status IN
        ('not_started', 'in_progress', 'met', 'exceeded',
         'partially_met', 'not_met')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS config (
    manager_id INTEGER NOT NULL DEFAULT 0,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (manager_id, key)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id SERIAL PRIMARY KEY,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS self_assessments (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER,
    week_date TEXT NOT NULL,
    dimension TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS career_conversations (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(id),
    team_member_id INTEGER NOT NULL REFERENCES team_members(id),
    conversation_date TEXT NOT NULL,
    topic TEXT,
    notes TEXT,
    next_steps TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS skills (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(id),
    team_member_id INTEGER NOT NULL REFERENCES team_members(id),
    skill_name TEXT NOT NULL,
    proficiency TEXT DEFAULT 'developing'
        CHECK(proficiency IN ('learning', 'developing', 'proficient', 'expert')),
    is_strength INTEGER DEFAULT 0,
    is_growth_area INTEGER DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS development_plans (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(id),
    team_member_id INTEGER NOT NULL REFERENCES team_members(id),
    title TEXT NOT NULL,
    description TEXT,
    target_date TEXT,
    status TEXT DEFAULT 'active'
        CHECK(status IN ('active', 'completed', 'paused')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS milestones (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER REFERENCES managers(id),
    plan_id INTEGER NOT NULL REFERENCES development_plans(id),
    description TEXT NOT NULL,
    target_date TEXT,
    completed INTEGER DEFAULT 0,
    completed_at TIMESTAMP
);

-- Users table for Google OAuth (used by auth.py)
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    google_id TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    picture TEXT,
    last_login TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Delegation tracker
CREATE TABLE IF NOT EXISTS delegations (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER,
    team_member_id INTEGER REFERENCES team_members(id),
    task TEXT NOT NULL,
    outcome_expected TEXT,
    autonomy_level TEXT DEFAULT 'guided' CHECK(autonomy_level IN
        ('directed', 'guided', 'autonomous')),
    check_in_date TEXT,
    status TEXT DEFAULT 'active' CHECK(status IN
        ('active', 'completed', 'revoked', 'stalled')),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Running 1:1 notes (persistent per-member notes across meetings)
CREATE TABLE IF NOT EXISTS running_notes (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER,
    team_member_id INTEGER NOT NULL REFERENCES team_members(id),
    note_date TEXT NOT NULL DEFAULT CURRENT_DATE,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general' CHECK(category IN
        ('general', 'meeting_prep', 'observation', 'follow_up', 'praise')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Decision log / decision journal
CREATE TABLE IF NOT EXISTS decisions (
    id SERIAL PRIMARY KEY,
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Daily coach suggestions (cached, one per manager per day)
CREATE TABLE IF NOT EXISTS coach_suggestions (
    id SERIAL PRIMARY KEY,
    manager_id INTEGER,
    suggestion_date TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'rule' CHECK(tier IN ('rule', 'ai')),
    suggestion TEXT NOT NULL,
    action_page TEXT,
    dismissed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_coach_suggestions_mid_date_tier
    ON coach_suggestions (manager_id, suggestion_date, tier);
CREATE UNIQUE INDEX IF NOT EXISTS ux_self_assessments_mid_week_dim
    ON self_assessments (manager_id, week_date, dimension);

-- Hot-path indexes (P4.1 / AUDIT M5).
-- Note: in production, prefer `CREATE INDEX CONCURRENTLY` to avoid locking
-- the table during the build. The runner-applied form below uses plain
-- CREATE INDEX IF NOT EXISTS because `CONCURRENTLY` can't run inside a
-- transaction; the migration ledger wraps each migration in one. Run the
-- CONCURRENTLY variants manually for zero-downtime.
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
