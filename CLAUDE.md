# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Manager Tool is a single-manager management coaching journal: 1:1 meeting records, journal, delegations, feedback, goals, decisions, analytics, and an AI coaching layer grounded in a wisdom library of 620 management ideas from 23 books (`365_Great_Management_Ideas.md`).

**The app is Django** (`manager-tool-django/`), live at https://manager-tool-django.onrender.com. It began as a Streamlit app that was migrated (Phases 0–8, complete 2026-07-16) and then decommissioned; the Streamlit code was deleted from the working tree and lives only in git history (last present under `legacy/` at commit `c252193`). `MIGRATION_STATUS.md`, `MIGRATION_PLAN.md`, `PHASE_GATES.md`, `PLAN.md`, and `AUDIT.md` are historical records — useful when reading old commits, not guidance for new work.

## Development Commands

```bash
cd manager-tool-django

# Run dev server (local dev uses SQLite via .env's DATABASE_URL)
.venv/bin/python manage.py runserver

# Run tests (SQLite in-memory, via mt/settings_test.py)
.venv/bin/pytest -q
.venv/bin/pytest core/tests.py::TestClassName::test_name -v

# Lint (CI runs this before pytest)
.venv/bin/ruff check .

# PG smoke test (needs a real Postgres DATABASE_URL; CI runs it against postgres:16)
DATABASE_URL=postgresql://... .venv/bin/python scripts/smoke_pg_django.py

# Django migrations
.venv/bin/python manage.py makemigrations
.venv/bin/python manage.py migrate

# Install deps (the .claude SessionStart hook does this on web sessions)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Architecture

### Stack

Django 5.1 + django-allauth (Google OAuth only) + django-htmx + compiled Tailwind CSS. Deployed on Render via `render.yaml` (auto-deploys `main`; the build step runs `manage.py migrate`). Postgres via Neon (single `production` branch; Neon's serverless proxy handles pooling — no app-side pool). Sentry for errors. Verify any deploy with `GET /health/` → `{status, git_sha}`.

**Visual design:** deliberate type + color system documented in `manager-tool-django/DESIGN.md` (Fraunces/Public Sans, single teal `accent-*`, tightened radius). Tokens live in `tailwind.config.js` and compile into `static/css/tw.css` — rebuild after class changes (command in base.html's head comment; render.yaml reruns it on every deploy; `TestCompiledCssCoverage` fails CI on class-without-rebuild). Build with `accent-*`/`font-display` rather than raw values, and check new UI against the forbidden-slop list in DESIGN.md.

### Project layout

- `mt/` — Django project: settings, urls, wsgi. `settings_test.py` overrides DB to SQLite `:memory:` for pytest.
- `core/` — the main app. `core/views/` is a package, one module per domain (`journal.py`, `events.py`, `one_on_ones.py`, `inbox.py`, `search.py`, …) sharing helpers in `_common.py`. Models, forms, middleware, managers, services, and management commands (`send_weekly_digests`, `purge_deleted_team_members`, `poll_inbox_email` — all wired as Render crons).
- `coaching/` — Anthropic API integration (`services.py`): daily coach suggestions, 1:1 prep briefs, SBI feedback drafting, digest "week plan". Reads the wisdom library from the repo-root `365_Great_Management_Ideas.md`. Per-manager API key from the DB first, `ANTHROPIC_API_KEY` env fallback.
- `templates/` — full-page templates extending `base.html` (which holds the sidebar). `templates/_partials/` — HTMX fragments (swapped via `hx-target`).
- `scripts/smoke_pg_django.py` — the PG safety net (see Testing).

### Sidebar IA (3 sections)

Rendered in `templates/base.html`, active state via `request.resolver_match.url_name`:

```
MANAGER:    Dashboard · Upcoming · Manager Journal · Schedule Event · To Do · Decisions
DIRECTS:    New 1:1 · Meetings · Notes · Delegations · Feedback · Goals · Career Dev
REFERENCE:  Analytics · History · Resources · Team · ── · Settings · Log Out
```

Plus a sidebar quick-add box feeding the `/inbox/` triage queue, and a cross-model search box (`/search/?q=` across all 11 content models). Meetings = structured 10/10/10 records; Notes = async between-meeting jots.

### Tenant isolation

- `TenantManager` (`core/managers.py`) — custom manager on all tenant-scoped models. Views must call `.objects.for_manager(request.manager.id)` instead of `.objects.all()`. Raises `ValueError` if `manager_id` is None — None must fail loud, never silently return zero rows.
- `ManagerBridgeMiddleware` (`core/middleware.py`) — maps allauth's `request.user.email` to the `Manager` row and sets `request.manager`. Views check `request.manager is None` → 403.
- Every view that touches tenant data is `@login_required` and gates on `request.manager`. HTMX requests return partials; full-page requests return top-level templates.

### Schema changes

One Django migration in `core/migrations/` is the whole change — merges to `main` apply it in prod via the Render build step. **Do not edit `schema_postgres.sql`** for new columns: it is the frozen cutover-era baseline that `smoke_pg_django.py` applies before `migrate --fake-initial` fakes `0001` and really applies `0002+`. Editing it to include a later migration's column would break the fake-initial handoff the smoke test exists to prove.

### Date semantics (inherited from the cutover)

- `*_date` columns are TEXT `'YYYY-MM-DD'` (lex order == chronological, so string `BETWEEN` works). The ORM returns them as `str`; don't add `DateField`s to these without a real migration plan.
- `created_at`/`updated_at` are real timestamps and come back as `datetime` objects.
- Historical bug class (4 shipped instances in the Streamlit era): comparing TEXT dates against SQL `date`/timestamp expressions is `UndefinedFunction` on PG but passes on SQLite. Compute date bounds in Python (`date.today().isoformat()`) and pass as string params; never compare a TEXT column to `CURRENT_DATE` or `LEFT(timestamp, 10)` in raw SQL.

### Auth & secrets

- Login is Google OAuth via allauth exclusively (no passwords; `ModelBackend` removed).
- Sensitive config values (per-manager Anthropic key, IMAP app password) use Fernet encryption, fail-closed: refuses to write plaintext on init failure, and `get_config` fails loud on decryption errors.
- `MANAGER_TOOL_ENV=prod` is required in production (gates encryption-key auto-generation off).
- **Anthropic API key handling.** Never paste, echo, or log a real `ANTHROPIC_API_KEY` in chat, terminal output, commits, PRs, or tests — use placeholders like `sk-ant-test-...`. `.env` files are gitignored at repo root and `manager-tool-django/`.

### Ops

- Nightly encrypted backups: pg_dump → encrypt → restore-proof, with a healthchecks.io dead-man switch.
- `/health/` checks the DB (503 when dead) and reports the live git SHA.
- Crons on Render: weekly digest (Monday), soft-delete purge, IMAP inbox poll (15 min).
- `.github/workflows/neon_workflow.yml` creates a Neon branch per PR for a real-Neon smoke run (14-day `expires_at`, deleted on PR close).

## Testing

### CI (`.github/workflows/test.yml` + `neon_workflow.yml`)

| Job | What it proves |
| --- | -------------- |
| `tests-django-sqlite` | ruff clean + logic correctness (SQLite `:memory:`, ~536 tests) |
| `smoke-pg-django` | PG safety against a real `postgres:16` service |
| `validate-render-blueprint` | `render.yaml` is deployable (`scripts/validate_render_blueprint.py`) |
| `smoke_pg_django_neon` | same smoke against a real Neon branch (PR-scoped) |

### pytest is SQLite-only

`mt/settings_test.py` overrides the DB to SQLite `:memory:`. **Green pytest does not prove PG safety** — the SQLite/PG divergence shipped four production bugs in the Streamlit era. `scripts/smoke_pg_django.py` is the safety net: **any PR touching SQL, model fields, or schema must extend it.**

### Cross-tenant tests

The smoke test seeds a second manager via app helpers (NOT raw SQL — raw-SQL seeding is tautological) and asserts bidirectional isolation: manager A sees zero of B's rows, AND vice versa. Mirror this for new aggregator-style queries.

### Other guards

- `except: pass` literals are forbidden (no-silent-excepts test in `core/tests.py`); assign a fallback or `logger.warning` instead.
- Multi-row writes that must be atomic get a forced-failure no-orphan assertion in the smoke test — it's the only credible guard for transaction bugs (SQLite autocommit hides them).

## Known limitations

- Recurring-series calendar invites: the series parent sends one `RRULE:FREQ=…;COUNT=…` invite (COUNT = actual series size in the DB — parent + children — so until-capped series and orphaned children degrade correctly; a parent invite stamps `calendar_invite_sent` on its children). Caveat: RFC 5545 monthly on day 29–31 SKIPS short months while the app's materializer clamps to month-end — the app's Event rows stay the source of truth.
- Local dev runs on SQLite (`sqlite:///db.sqlite3` in `.env`); there is no standing Neon dev branch. To test against real PG locally, create a throwaway Neon branch and point `DATABASE_URL` at it.

---

## Skills

---
name: code-validator
description: Expert code quality and error-checking skill. Activates on requests to "review code", "check for bugs", "debug", or "fix errors".
allowed-tools: [Read, Grep, LS]
---

# Code Validation & Error Checking

## 1. Triggering Context
Use this skill whenever you are asked to review code, troubleshoot a failure, or before finalizing any significant code change.

## 2. Mandatory Validation Checklist
Before declaring a task "fixed," you MUST verify:
- **Silent Failures:** Search for `try-except` blocks without logging or re-raising. Flag any instance where an error is "swallowed". Repository convention: `except: pass` literal patterns are forbidden by the no-silent-excepts test in `core/tests.py`; prefer assigning a fallback value or calling `logger.warning`.
- **Import Accuracy:** Verify that all newly added imports actually exist in the project environment.
- **Edge Cases:** Explicitly check for null/undefined inputs, empty lists, and network timeouts.
- **PG safety:** Any change to SQL, model fields, or schema must run via `scripts/smoke_pg_django.py`. SQLite-only pytest is not enough.
- **Schema changes:** One Django migration is the whole change — never edit `schema_postgres.sql` (frozen fake-initial baseline).

## 3. Troubleshooting & Recovery
| Problem | Immediate Action |
|---------|------------------|
| Tool Timeout | Wait 5s and retry once. If it fails again, report the specific timeout to the user. |
| Missing Context | Use `Grep` to find where the variable/class is defined before guessing its structure. |
| Test Failure | Do NOT "fix" the test to pass. Analyze the logic error in the source code first. |
| PG-only failure | Suspect a TEXT-date vs SQL-date comparison in raw SQL, or timezone-naive vs aware datetime handling. |

## 4. Critical Rules
- **No Hallucinations:** If an MCP tool returns "no results," do NOT generate synthetic data. State "No results found" and ask for alternative search parameters.
- **Opinionated Naming:** Flag "clever" or ambiguous variable names (e.g., `temp`, `data`, `handle`). Suggest explicit alternatives.
- **Test-First Debugging:** Always suggest a specific test case that would have caught the bug being fixed.
