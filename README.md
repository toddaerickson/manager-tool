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

### Meetings (10/10/10 recorder)
- Structured 1:1 recorder with three sections: Their Agenda → Your Agenda → Coaching/Their Future. Autosaves as you type (HTMX).
- Side panel surfaces the direct's context: recent meetings, open delegations, active goals, recent feedback, carried-over action items.
- **Prep mode**: when Your Agenda is empty, one click pulls the direct's open delegations + action items into the box as a checklist — non-destructive, never overwrites.

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
- Google OAuth login via django-allauth (no passwords stored in the app)
- Sensitive config values (API keys, SMTP passwords) encrypted at rest with Fernet
- Work schedule and timezone configuration
- Complete data isolation between managers (multi-tenancy via `TenantManager.for_manager`)

### Email & Calendar
- Weekly email digest: nudges, upcoming events, overdue actions, streak status (HTML + plain text)
- **"This week's plan"** at the top of the Monday digest: an AI-generated, prioritized 3–5 action list grounded in your real data and the management corpus
- iCalendar (.ics) generation for meeting invites via SMTP
- "Send Weekly Digest Now" button in Settings

## Architecture

The production app is **Django 5.1 + HTMX + Tailwind**, deployed on Render with Postgres on Neon. It lives in `manager-tool-django/`. The original Streamlit app was archived to `legacy/` after the 2026-05 cutover (kept in git as a rollback option; see `MIGRATION_STATUS.md`).

| Path | Purpose |
|---|---|
| `manager-tool-django/mt/` | Django project: settings, urls, wsgi. `settings_test.py` pins SQLite `:memory:` for pytest |
| `manager-tool-django/core/` | Models, views, forms, middleware, services, management commands |
| `manager-tool-django/coaching/` | Claude API integration — coaching sidebar, Next Step, daily coach (rule-based + AI tiers) |
| `manager-tool-django/templates/` | Full-page templates; `_partials/` holds HTMX fragments swapped via `hx-target` |
| `manager-tool-django/core/services/` | `calendar.py` (ICS + SMTP digest), `digest.py`, `email.py`, `audit.py`, `events.py`, etc. |
| `manager-tool-django/scripts/smoke_pg_django.py` | End-to-end smoke test against real PG (allauth login, `for_manager` filters, cross-tenant isolation) |
| `365_Great_Management_Ideas.md` | 620 management ideas from 23 books — the shared wisdom library, read by the coaching engine |
| `schema_postgres.sql` | Postgres schema; used to bootstrap the Django PG smoke test |
| `.github/workflows/test.yml` | CI: Django pytest (SQLite) + Django PG smoke (postgres:16 service) on every PR |
| `render.yaml` | Render deploy config (build runs `manage.py migrate`) |
| `legacy/` | Archived Streamlit app (`web_app.py`, `database.py`, `coaching.py`, `auth.py`, its `tests/` and `scripts/`) — frozen, not deployed |

## Database

**Neon PostgreSQL** in production; SQLite `:memory:` for the pytest suite (`mt/settings_test.py`). Neon's serverless proxy handles pooling; the app opens direct connections.

### Tables

| Category | Tables |
|---|---|
| **Auth** | managers, users (Google OAuth via allauth), `django_session` |
| **Team** | team_members |
| **Activities** | events (with recurrence_rule, parent_event_id, recurrence_warned_at), action_items, feedback, goals (with target_date) |
| **Journal** | journal_entries, self_assessments |
| **Career** | career_conversations, skills, development_plans, milestones |
| **Meetings** | one_on_one_sessions |
| **Workflow** | delegations, running_notes, decisions, coach_suggestions, audit_log |
| **System** | config (autoincrement `id` PK + `unique_together (manager_id, key)`) |

The Streamlit-era `sessions` and `login_attempts` tables were dropped in Phase 8 (`core/migrations/0006`); django-allauth + `django_session` replace them. Every tenant-scoped model uses `TenantManager`; views call `.objects.for_manager(request.manager.id)` and `for_manager(None)` raises, so an unauthenticated request can't slip through.

### Migrations

Django's own migration framework (`manager-tool-django/core/migrations/`). Render's build step runs `python manage.py migrate` on deploy. Streamlit's old `schema_migrations` ledger remains in the DB as a frozen artifact (modeled `managed = False`) and is not touched by Django.

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

The sidebar is rendered by `base.html`; member detail folds the standalone Timeline into the Team detail view.

## Quick Start

```bash
cd manager-tool-django
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the dev server
python manage.py runserver
```

Set `DATABASE_URL`, `DJANGO_SECRET_KEY`, `CONFIG_ENCRYPTION_KEY`, and the Google OAuth secrets in `manager-tool-django/.env` (see `.env.template`). Without `DATABASE_URL` the dev server falls back to local SQLite.

## Configuration

1. **Sign in with Google** — allauth creates the session; the bridge middleware maps your email to a `Manager` row
2. **Add your team** — name, role, email
3. **Start journaling** — the core habit. Even one sentence counts.
4. **Optional: Add an Anthropic API key** in Settings for Claude-powered coaching + AI daily suggestions

## Deployment

Deployed on **Render** (`render.yaml`), Postgres on **Neon**.

- Root directory: `manager-tool-django/`
- Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`
- Start: `gunicorn mt.wsgi:application --bind 0.0.0.0:$PORT`
- Env vars: `DATABASE_URL`, `DJANGO_SECRET_KEY`, `CONFIG_ENCRYPTION_KEY`, `MANAGER_TOOL_ENV=prod`, Google OAuth secrets, `SENTRY_DSN`
- A Render Cron service runs `python manage.py send_weekly_digests`
- Build runs migrations automatically, so merging to `main` applies pending Django migrations on deploy
- **Confirm a deploy:** `GET /health/` returns `{"status": "ok", "git_sha": ...}` (the live commit, from Render's `RENDER_GIT_COMMIT`)
- CI (`.github/workflows/`): Django pytest (SQLite) + PG smoke against `postgres:16`, plus a per-PR Neon-branch smoke that auto-creates/deletes a `preview/pr-<n>` branch

## Dependencies

Django stack (see `manager-tool-django/requirements.txt`):

```
Django>=5.0,<5.2
psycopg2-binary
django-allauth[socialaccount]
django-htmx
django-tailwind
cryptography
anthropic
gunicorn
whitenoise
sentry-sdk
```

## Tests

```bash
cd manager-tool-django
pytest -v
```

The pytest suite runs against **SQLite `:memory:`** (`mt/settings_test.py`). PG-only safety is covered by `scripts/smoke_pg_django.py`, which runs against a real `postgres:16` service in CI on every PR (`.github/workflows/test.yml`). The smoke test bootstraps the schema, exercises allauth login + session creation, runs `for_manager` filters across tenant tables, and asserts cross-tenant isolation bidirectionally. Any PR touching SQL or schema must extend it.

## Development with Claude Code

This project includes a `CLAUDE.md` file with:
- Project architecture context for AI-assisted development (Django app + frozen Streamlit archive)
- A **code-validator** skill that activates on review/debug requests with a mandatory validation checklist, troubleshooting recovery table, and critical rules (no hallucinations, test-first debugging)

## License

Private repository.
