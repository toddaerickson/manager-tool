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

### Daily Coach Suggestion
At login, a personalized suggestion tells you exactly what to do next — no blank-canvas paralysis:
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
- Team roster with inline add-member form and member detail views
- Member Timeline: pre-meeting prep with days since last meeting, feedback ratio, pending actions, active goals
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

### Actions, Feedback & Goals
- Action item tracking with inline add form, overdue warnings, and badge counts in sidebar
- SBI framework feedback (Situation → Behavior → Impact) with edit/delete
- Quarterly goal tracking with OKR-style key results, inline add/update/delete
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
| `database.py` | Dual-mode database layer (SQLite + PostgreSQL/Supabase) with connection pooling, ~70 typed functions |
| `coaching.py` | Claude API integration for coaching sidebar + daily coach suggestion engine (rule-based + AI) |
| `templates.py` | Wisdom engine, coaching provocations, anti-pattern detector, meeting agendas, addictive design framework |
| `calendar_service.py` | iCalendar generation, SMTP email, weekly digest |
| `auth.py` | Google OAuth 2.0 authentication |
| `365_Great_Management_Ideas.md` | 620 management ideas from 23 books |
| `schema_postgres.sql` | PostgreSQL schema for Supabase deployment |
| `tests/` | 61 tests covering database CRUD, multi-tenancy, coaching, templates |
| `manager_tool.py` | CLI interface (legacy) |
| `gui.py` | Tkinter desktop GUI (legacy) |

## Database

**Dual-mode**: SQLite for local development, PostgreSQL (Supabase) for production. Auto-detects via `DATABASE_URL` environment variable. Connection pooling (1-5 connections) for PostgreSQL with transparent pool-return on close.

### Tables

| Category | Tables |
|---|---|
| **Auth** | managers, users (Google OAuth) |
| **Team** | team_members |
| **Activities** | events, action_items, feedback, goals |
| **Journal** | journal_entries, self_assessments |
| **Career** | career_conversations, skills, development_plans, milestones |
| **New Features** | delegations, running_notes, decisions, coach_suggestions |
| **System** | config |

All user-owned tables filtered by `manager_id` for complete multi-tenant isolation.

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

Flat, compact, workflow-ordered — no accordion expanders, every feature one click away:

```
Dashboard          ← daily overview + coach suggestion
Journal            ← keystone habit
── PEOPLE ──
Team               ← roster + inline add
Timeline           ← pre-meeting prep
1:1 Notes          ← persistent notes
Career Dev         ← skills + plans
── TRACKING ──
Actions (n)        ← badge count for overdue
Feedback
Delegations
Goals
Decisions
── EVENTS ──
Schedule / Upcoming / History
── REFERENCE ──
Analytics / Resources
```

Journal status dot (green/red) and streak badge always visible.

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

### Production (Supabase)
1. Create a Supabase project
2. Run `schema_postgres.sql` in the SQL Editor
3. Get the **Session Pooler** connection string from Supabase (Settings → Database → Connection Pooling). It looks like:
   ```
   postgresql://postgres.PROJECT_REF:PASSWORD@aws-X-region.pooler.supabase.com:5432/postgres
   ```
   **Important**: Use the pooler URL, not the direct connection. Streamlit Cloud cannot reach Supabase via IPv6 (direct), only IPv4 (pooler).
4. Set `DATABASE_URL` as a Streamlit secret:
   ```toml
   # .streamlit/secrets.toml (or Streamlit Cloud Secrets UI)
   DATABASE_URL = "postgresql://postgres.YOUR_REF:YOUR_PW@aws-X-region.pooler.supabase.com:5432/postgres"
   ```
5. Optionally set `CONFIG_ENCRYPTION_KEY` for config value encryption
6. Deploy to Streamlit Cloud or any Python hosting

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
pip install pytest bcrypt cryptography
python -m pytest tests/ -v
# 61 tests covering database, coaching, and templates
```

## Development with Claude Code

This project includes a `CLAUDE.md` file with:
- Project architecture context for AI-assisted development
- A **code-validator** skill that activates on review/debug requests with a mandatory validation checklist, troubleshooting recovery table, and critical rules (no hallucinations, test-first debugging)

## License

Private repository.
