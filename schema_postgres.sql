-- Manager Tool: PostgreSQL schema for Supabase
-- Paste this into Supabase SQL Editor to initialize the database

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
    key TEXT PRIMARY KEY,
    value TEXT
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
    team_member_id INTEGER NOT NULL REFERENCES team_members(id),
    conversation_date TEXT NOT NULL,
    topic TEXT,
    notes TEXT,
    next_steps TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS skills (
    id SERIAL PRIMARY KEY,
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
