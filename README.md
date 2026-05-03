# Manager Tool

A private management coaching journal and team management platform that makes you a better manager. Not another HR tool — a personal thinking partner that connects your daily reflections to your team data, a 620-idea management wisdom library, and AI-powered coaching grounded in 23 books.

## What Makes This Different

| Generic HR Tool | This Tool |
|---|---|
| Built for HR to monitor managers | Built for the manager to develop themselves |
| Empty forms | Coaching provocations + wisdom at the right moment |
| Public by default | Private by default |
| Tracks compliance | Detects your personal anti-patterns |
| Separates learning from workflow | Embeds 620 ideas INTO the workflow |
| Passive dashboard | Active daily coach: "Here's what to do right now" |
| 10-minute sessions | 30-second sessions |

## Core Features

### Next Step + Daily Coach (two surfaces, both at the top of Dashboard)

**Next Step** — a single imperative one-liner above the Coach card. Sourced from the rule engine; one click goes to the relevant page (overdue 1:1 → Schedule, expiring recurring series → Schedule with prefill, stale feedback → Feedback, etc.). Branch priority: overdue > recurring-series-expiry > delegation-due > event-soon > baseline.

**Daily Coach** — a richer reflective suggestion below Next Step:
- **Tier 1 (instant)**: Rule-based priority engine — mood-aware, streak-protecting, names specific people and situations
- **Tier 2 (AI-enhanced)**: Claude synthesizes your last 7 days of journal entries, team meeting cadence, overdue delegations, and pending decisions into one actionable prompt
- Cached daily. Dismiss with "Got it" to skip for the day
- Mood-sensitive: if yesterday was tough, it leads with support before tasks

### Journal (the keystone habit)
- Zero-friction daily entries — no required fields, just write
- Mood/energy tracking with trend visualization (sparkline chart)
- Weekly self-assessment (6 dimensions: Presence, Clarity, Feedback, Development, Advocacy, Follow-through)
- Journal streak tracking with loss-aversion mechanics
- On save: matched wisdom quote from the 620-idea library (variable reward)
- Coaching responses persisted with entries and visible in history
- CSV export of full journal history

### AI Coaching Sidebar
- Right-pane coaching on Journal, Events, Feedback, and Member Timeline pages
- **With API key**: Claude-powered coaching (claude-sonnet-4-6) — probing questions, framework application, devil's advocate, action prompts — grounded in 23 management books
- **Without API key**: Local fallback using keyword-matched wisdom + situation-specific question templates
- References specific authors: "Grove would say...", "Buckingham asks...", "Dellanna warns..."

### Dashboard (30-second value)
- **Daily coach suggestion** at the top — personalized first action
- Daily wisdom (different every day — variable reward)
- Nudges: "It's been 18 days since you met with Sarah" (loss-framed triggers)
- Anti-pattern alerts: "You're showing signs of The Ghost" (identity hook)
- Delegation and decision review nudges
- Streak counters, quick stats, onboarding checklist
- Quick-action buttons for common tasks

### Team Hub
- Team roster with inline add-member form and detail view
- Click "View Details" on a member → renders the full member timeline inline: pre-meeting prep with days since last meeting, feedback ratio, pending actions, active goals, recent notes, coaching pane, activity timeline. The standalone Timeline page was folded into Team detail; legacy `_DISPATCH["Timeline"]` and `_DISPATCH["Member Timeline"]` redirect to the new home.
- **Running 1:1 Notes**: Persistent per-member notes (general, meeting prep, observation, follow-up, praise) that carry forward between meetings — most recent notes surface automatically during meeting prep
- Career Development: conversation tracker, skills inventory with proficiency levels, development plans with milestones

### Delegation Tracker
- Track what you've delegated, to whom, with expected outcomes
- Three autonomy levels: Directed (step-by-step) → Guided (milestone check-ins) → Autonomous (deliver the result)
- Check-in dates with overdue alerts on dashboard
- Status tracking: Active → Completed / Stalled / Revoked
- Grounded in Dellanna's "delegate results, not methods"

### Decision Log
- Record decisions with full context: situation, alternatives considered, rationale, expected outcome
- Set a review date to check back: did it play out as expected?
- Record actual outcomes and update status: Active → Validated / Revised / Reversed
- Decisions due for review nudged on dashboard
- Grounded in Grove's "for every unambiguous decision we make, we probably nudge things a dozen times"

### Analytics & Anti-Pattern Detection
- Personal anti-pattern detector: The Ghost, The Micromanager, The Buddy, The Hero, The Scorekeeper, The Proxy
- Meeting cadence per member per month (bar chart)
- Feedback health ratios (ideal: 5:1 positive to constructive)
- Goal completion rates, action item stats, activity trends
- Self-assessment trends over time
- CSV export for meetings, feedback, goals

### Upcoming (one screen, four streams)
A single page aggregating items due in the next 7 days from four sources:
- **Events** scheduled (1:1s, check-ins, coaching sessions, quarterly reviews)
- **Action items** with `due_date` ≤ today + 7
- **Delegations** with `check_in_date` ≤ today + 7 (status = active)
- **Goals** with `target_date` ≤ today + 7 (status = not_started or in_progress)

Plus an **Overdue** group above that — past-due items in the same four streams so they don't silently disappear. Each row carries a text chip (`EVENT / TODO / CHECK-IN / GOAL`) — never color-only, WCAG 1.4.1 compliant. Date-grouped: Overdue → Today → Tomorrow → "Wed May 7" → ...

### Recurring Events (weekly + monthly + quarterly)
Schedule Event has an always-visible Repeats selector. Pick None / Weekly / Monthly / Quarterly + an end date; a live preview shows the next 4 occurrences. On submit, the parent + N children are materialized atomically — never an orphan parent. Defaults: 12 weekly / 12 monthly / 8 quarterly occurrences (server-capped at 32 hard).
- **End-of-month policy is anchor-preserving** (Algo A): Jan 31 monthly → Feb 28 → **Mar 31** → Apr 30 → **May 31**. The cadence finds the 31st whenever the target month has one.
- A series approaching its materialized horizon surfaces as a Next Step warning ("Your weekly 1:1 with X is about to end — extend it"). Clicking pre-populates the Schedule form with the series template and a start date the day after the latest existing child, so cadence continues with no gap.

### Actions, Feedback & Goals
- Action item tracking with inline add form, overdue warnings, and badge counts in sidebar ("To Do · 3 overdue")
- SBI framework feedback (Situation → Behavior → Impact) with edit/delete
- Quarterly goal tracking with OKR-style key results, optional `target_date`, inline add/update/delete
- Coaching pane available on feedback entry

### Manager Profiles & Security
- Individual login with username/password (bcrypt hashed, salted)
- Transparent migration of legacy SHA-256 hashes on login
- Password strength validation (8+ characters)
- Login rate limiting (locked after 5 failed attempts in 15 minutes)
- Sensitive config values (API keys, SMTP passwords) encrypted at rest with Fernet
- Work schedule and timezone configuration
- Complete data isolation between managers (multi-tenancy)

### Email & Calendar
- Weekly email digest: nudges, upcoming events, overdue actions, streak status (HTML + plain text)
- iCalendar (.ics) generation for meeting invites via SMTP
- "Send Weekly Digest Now" button in Settings

## Architecture

| File | Purpose |
|---|---|
| `web_app.py` | Streamlit web application — all pages, navigation, and UI |
| `database.py` | Dual-mode database layer (SQLite + Neon PostgreSQL); migration runner (`_MIGRATIONS` ledger); transactional `_materialize_in_txn` for recurring events |
| `coaching.py` | Claude API integration for coaching sidebar + Next Step + daily coach (rule-based + AI tiers) |
| `templates.py` | Wisdom engine, coaching provocations, anti-pattern detector, meeting agendas, behavioral design framework |
| `calendar_service.py` | iCalendar generation (one VEVENT per row today; RRULE export deferred), SMTP email, weekly digest |
| `auth.py` | Google OAuth 2.0 authentication |
| `365_Great_Management_Ideas.md` | 620 management ideas from 23 books |
| `schema_postgres.sql` | PostgreSQL schema (Neon or any standard Postgres) |
| `scripts/smoke_pg.py` | End-to-end smoke test against real PG (init_db, sessions, aggregator, recurring materialization, cross-tenant) |
| `.github/workflows/test.yml` | CI: pytest (SQLite) + smoke job (postgres:16 service) on every PR |
| `tests/` | 251 tests covering database CRUD, multi-tenancy, aggregator isolation, recurring events, materialization rollback, expiry warning, XSS escape, coaching, templates, dispatch |
| `manager_tool.py` | CLI interface (legacy) |
| `gui.py` | Tkinter desktop GUI (legacy) |

## Database

**Dual-mode**: SQLite for local development, Neon PostgreSQL for production. Auto-detects via `DATABASE_URL` environment variable. Neon's serverless proxy handles pooling; the app opens direct connections.

### Tables

| Category | Tables |
|---|---|
| **Auth** | managers, users (Google OAuth), sessions, login_attempts |
| **Team** | team_members |
| **Activities** | events (with recurrence_rule, parent_event_id, recurrence_warned_at), action_items, feedback, goals (with target_date) |
| **Journal** | journal_entries, self_assessments |
| **Career** | career_conversations, skills, development_plans, milestones |
| **Workflow** | delegations, running_notes, decisions, coach_suggestions |
| **System** | config (composite PK `(manager_id, key)`), schema_migrations |

All user-owned tables filtered by `manager_id` for multi-tenant isolation. Aggregator helpers (`get_upcoming_aggregate`, `get_overdue_aggregate`, `find_expiring_recurring_series`, `get_recurring_series_template`) take `manager_id` as a required keyword argument and assert non-None, so an unauthenticated session can't slip through.

### Migrations

Homegrown migration runner at `database.py:_run_migrations` reads a `schema_migrations` ledger and applies any sequenced `_MIGRATIONS` entry not yet recorded. Current sequence: `0001_journal_coaching_response`, `0002_orphan_table_manager_id`, `0003_partition_config_table`, `0004_sole_manager_backfill`, `0005_sessions_and_login_attempts`, `0006_save_uniqueness_constraints`, `0007_hot_path_indexes`, `0008_goals_target_date`, `0009_events_recurrence`. Each migration is idempotent (column-existence checks + `IF NOT EXISTS` indexes) so re-runs are no-ops. Schema changes follow a three-location dual-write rule: migration entry, `schema_postgres.sql`, and the SQLite block in `database.py`.

## The Wisdom Library

620 management ideas extracted from 23 books:

- *High Output Management* (Andy Grove)
- *First, Break All the Rules* (Buckingham & Coffman)
- *The Effective Manager* (Mark Horstman)
- *Scaling People* (Claire Hughes Johnson)
- *100 Truths You Will Learn Too Late* (Luca Dellanna)
- *Best Practices for Operating Excellence* (Luca Dellanna)
- *The New One Minute Manager* (Ken Blanchard)
- *Trust Me, I'm Lying* (Ryan Holiday)
- *Accountability Everywhere* (Tate, Pantalon & David)
- *The Algorithm* (Jonathan McNeill)
- *Game Theory* (Christoph Pfeiffer)
- *Slow Down, Sell Faster* (Kevin Davis)
- *Value-Based Fees* (Alan Weiss)
- *HBR Guide to Critical Thinking*
- *HBR Guide to Office Politics*
- *HBR Guide to Your Professional Growth*
- *HBR Guide to Leading Through Change*
- And others

## Behavioral Design Framework

21 behavioral psychology principles from 9 books (Hooked, Atomic Habits, The Power of Habit, Irresistible, Nudge, Influence, Thinking Fast & Slow, Predictably Irrational, Dopamine Nation) built into the UI:

- **Hook Model**: Trigger → Action → Variable Reward → Investment
- **Four Laws**: Make it Obvious, Attractive, Easy, Satisfying
- **Loss Aversion**: Streaks, loss-framed nudges, journal status dot
- **Variable Ratio Reinforcement**: Daily wisdom, coaching provocations, AI suggestions
- **Zeigarnik Effect**: Dashboard always shows open loops (overdue items, pending delegations)
- **Peak-End Rule**: Sessions end with wisdom, not failure lists
- **Daily Coach**: Reduces decision fatigue at entry, creates personal coach feeling

## Sidebar Navigation

Three sections — Manager / Directs / Reference — with the streak badge and journal status dot at the top, Settings/Log Out under a thin divider inside Reference:

```
Manager Tool                            ← (no emoji; flush-top)
🔥 12-day streak  🟢 Manager Name
─

MANAGER
  Dashboard
  Upcoming                              ← four-stream aggregator + Overdue
  Manager Journal
  Schedule Event                        ← + recurring events
  To Do                                 ← badge "· N overdue" only when N > 0
  Decisions

DIRECTS
  1:1 Notes
  Delegations
  Feedback
  Goals
  Career Dev

REFERENCE
  Analytics
  History
  Resources
  Team                                  ← click member → full timeline detail
  ─
  Settings
  Log Out
```

Page keys in `_DISPATCH` are stable across label rename rounds, so a cached `nav_page` value from a prior deploy keeps resolving. Legacy `Timeline` and `Member Timeline` keys redirect to `page_team_roster`.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the web app
streamlit run web_app.py
```

## Configuration

1. **Create an account** — username, password, work schedule
2. **Add your team** — name, role, email
3. **Start journaling** — the core habit. Even one sentence counts.
4. **Optional: Add Anthropic API key** in Settings for Claude-powered coaching + AI daily suggestions

## Deployment

### Streamlit Community Cloud (simplest)
1. Push to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your repo, set `web_app.py` as the main file
4. Deploy

### Production (Neon)
1. Create a Neon project (neon.tech)
2. Copy the **pooled** connection string from the Neon dashboard (Connection Details → "Pooled connection"). It looks like:
   ```
   postgresql://USER:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```
   Use the pooled endpoint (`-pooler` in the hostname) for Streamlit Cloud — it's serverless-friendly and handles connection churn.
3. Apply the schema:
   ```bash
   psql "$DATABASE_URL" -f schema_postgres.sql
   ```
4. Set `DATABASE_URL` as a Streamlit secret:
   ```toml
   # .streamlit/secrets.toml (or Streamlit Cloud Secrets UI)
   DATABASE_URL = "postgresql://USER:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
   ```
5. Set `CONFIG_ENCRYPTION_KEY` for config value encryption (required when `MANAGER_TOOL_ENV=prod`)
6. Set `MANAGER_TOOL_ENV=prod` so the SQLite fallback is disabled and a Postgres outage fails loud rather than silently splitting writes between backends
7. Deploy to Streamlit Cloud or any Python hosting

## Dependencies

```
streamlit>=1.38.0
pandas>=2.0.0
anthropic>=0.30.0
requests>=2.31.0
psycopg2-binary>=2.9.0
bcrypt>=4.0.0
cryptography>=41.0.0
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
# 251 tests across database CRUD, multi-tenancy, aggregator isolation,
# recurring events, materialization rollback, expiry warning, XSS escape,
# coaching, templates, dispatch
```

The pytest suite is **SQLite-only** — `tests/conftest.py` pins `_USE_PG=False`. PG-only safety is covered by `scripts/smoke_pg.py`, which runs against a real `postgres:16` service in CI on every PR (`.github/workflows/test.yml`). The smoke test mirrors production bootstrap, exercises the auth + session + aggregator + recurring-materialization paths, and includes a forced-failure no-orphan assertion. Any PR touching SQL or schema must extend it.

## Development with Claude Code

This project includes a `CLAUDE.md` file with:
- Project architecture context for AI-assisted development
- A **code-validator** skill that activates on review/debug requests with a mandatory validation checklist, troubleshooting recovery table, and critical rules (no hallucinations, test-first debugging)

## License

Private repository.
