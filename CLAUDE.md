# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
Manager Tool is a management coaching journal with a dual-mode database (SQLite local / Neon PostgreSQL). The wisdom library contains 620 management ideas from 23 books.

**Two apps live in this repo:**
- **Streamlit app** (`web_app.py`) — the current production app. **FROZEN**: no new features, no new `_MIGRATIONS` entries. Bug fixes only.
- **Django app** (`manager-tool-django/`) — the active migration target. All new development goes here. See `MIGRATION_STATUS.md` for phase progress and `MIGRATION_PLAN.md` for the full plan.

## Module Map (Streamlit app)
| File | Role |
|------|------|
| `web_app.py` (2.9k lines) | Streamlit UI — all page functions, sidebar nav, `_DISPATCH` table |
| `database.py` (3.8k lines) | Dual-backend DB layer — helpers, schema, migrations, encryption |
| `auth.py` | Session validation, login/logout, rate limiting |
| `coaching.py` | Anthropic API integration for coaching suggestions |
| `calendar_service.py` | ICS export and calendar integration |
| `templates.py` | Email and notification templates |
| `gui.py` | Shared Streamlit widget helpers |
| `manager_tool.py` | Legacy CLI (not used in production) |

## Development Commands

### Django app (active development)

```bash
cd manager-tool-django

# Run dev server
.venv/bin/python manage.py runserver

# Run tests (SQLite in-memory, via settings_test.py)
.venv/bin/pytest -v
.venv/bin/pytest core/tests.py -v
.venv/bin/pytest core/tests.py::TestClassName::test_name -v

# PG smoke test (requires DATABASE_URL)
DATABASE_URL=postgresql://... .venv/bin/python scripts/smoke_pg_django.py

# Django migrations
.venv/bin/python manage.py makemigrations
.venv/bin/python manage.py migrate

# Install deps
pip install -r requirements.txt
```

### Streamlit app (frozen — bug fixes only)

```bash
# Run locally
streamlit run web_app.py

# Run tests (SQLite-only)
python -m pytest tests/ -v
python -m pytest tests/test_database.py::test_name -v

# PG smoke test (requires DATABASE_URL)
python scripts/smoke_pg.py

# Install dev dependencies
pip install -r requirements-dev.txt
```

## Django App Architecture (`manager-tool-django/`)

### Stack

Django 5.1 + django-allauth (Google OAuth) + django-htmx + Tailwind CSS. Deployed on Render via `render.yaml`. PG via Neon.

### Project layout

- `mt/` — Django project: settings, urls, wsgi. `settings_test.py` overrides DB to SQLite `:memory:` for pytest.
- `core/` — Single Django app: models, views, forms, middleware, services, management commands.
- `coaching/` — Anthropic API integration (not yet ported from Streamlit).
- `templates/` — Full-page templates. `_partials/` — HTMX fragment templates (swapped via `hx-target`).
- `scripts/smoke_pg_django.py` — PG smoke test (mirrors Streamlit's `scripts/smoke_pg.py`).

### Tenant isolation

- `TenantManager` (`core/managers.py`) — custom manager on all tenant-scoped models. Views must call `.objects.for_manager(request.manager.id)` instead of `.objects.all()`. Raises `ValueError` if `manager_id` is None.
- `ManagerBridgeMiddleware` (`core/middleware.py`) — maps allauth's `request.user.email` to the existing `Manager` row and sets `request.manager`. Views check `request.manager is None` → 403.

### View pattern

All views are function-based in `core/views.py`. Every view that touches tenant data is `@login_required` and gates on `request.manager`. HTMX requests return partials from `_partials/`; full-page requests return top-level templates extending `base.html`.

### Date-shape decision

Django ORM returns native `datetime.date`/`datetime.datetime` objects (unlike Streamlit's ISO-string normalization). `*_date` columns remain `TextField` because the DB stores `'YYYY-MM-DD'` text. `DateTimeField` columns (`created_at`, `updated_at`) return Python datetime objects. Before porting helpers, grep for `BETWEEN`, `startswith(`, `[:10]` patterns that assume string dates.

## Sidebar IA (3 sections)
The sidebar is organized into Manager → Directs → Reference. Settings/Log Out live inside Reference under a thin divider. Tile labels are deliberate; page-keys in `_DISPATCH` are stable across rename rounds so cached `nav_page` values from prior deploys keep resolving:

```
MANAGER:    Dashboard · Upcoming · Manager Journal · Schedule Event · To Do · Decisions
DIRECTS:    Meetings · 1:1 Notes · Delegations · Feedback · Goals · Career Dev
REFERENCE:  Analytics · History · Resources · Team · ── · Settings · Log Out
```

Timeline is folded into Team detail. Clicking a member sets `st.session_state["team_member_id"]`; `page_team_roster` short-circuits to render the `_render_member_timeline(member_id, name)` body. Legacy `_DISPATCH["Timeline"]` and `_DISPATCH["Member Timeline"]` redirect to `page_team_roster`.

## Key Architecture Decisions

### Database access
- All reads/writes go through `_exec()` / `_fetchone()` / `_fetchall()` / `_exec_returning_id()` helpers. They handle the `?` → `%s` placeholder conversion (`_q()`) and PG/SQLite cursor differences. **Never call `conn.execute(...)` on PG paths** — psycopg2 connections don't expose `.execute()`; SQLite ones do, which is how this bug class slips past local tests.
- `_fetchone` / `_fetchall` route through `_normalize_row()` which converts psycopg2's `datetime`/`date` returns to ISO strings so callers see uniform string-typed shapes on both backends.
- Every user-owned helper takes a required `manager_id: int` and filters on it. `assert manager_id is not None` at the top of aggregator-style helpers — None must fail loud, never silently return zero rows.

### Schema dual-write rule
Every schema change touches **three** locations:
1. A migration entry in `_MIGRATIONS` (`database.py:631`) for existing deploys.
2. The `CREATE TABLE` in `schema_postgres.sql` for fresh PG deploys.
3. The `CREATE TABLE` in the SQLite block in `database.py` for pytest fixtures.

Indexes get the same treatment: in the migration AND in `schema_postgres.sql`'s index block. Skipping any of the three breaks one of {existing prod, fresh deploy, pytest}.

### Date semantics
- Date columns are TEXT (`YYYY-MM-DD`) on both backends. Lex order matches chronological order, so `text_col BETWEEN ? AND ?` with string params works on PG and SQLite.
- TIMESTAMP columns (PG) / TEXT columns (SQLite) — use `_sql_date_of_timestamp(col)` to render either as a `'YYYY-MM-DD'` text value when comparing in WHERE clauses.
- **Do NOT inline `_sql_current_date()` into a BETWEEN comparison.** On PG it returns a `date` and `text BETWEEN date AND date` is the same `UndefinedFunction` class that has bitten this codebase four times. Compute date bounds in Python (`date.today().isoformat()`) and bind as TEXT params.

### Auth & secrets
- Passwords use bcrypt; sensitive config values use Fernet encryption (fail-closed — refuses to write plaintext on init failure).
- Server-side sessions in the `sessions` table (`session_token` cookie + `expires_at` + UA hash binding). Persistent rate-limited login via `login_attempts`.
- `MANAGER_TOOL_ENV=prod` is required in production: it gates encryption-key auto-generation off and forces the SQLite fallback off when `DATABASE_URL` is set but Postgres is unreachable.

### Recurring events (PR 4)
- `events.recurrence_rule`, `events.parent_event_id` (FK with `ON DELETE SET NULL`), `events.recurrence_warned_at`.
- `create_recurring_events(...)` materializes the parent + N-1 children atomically via `_materialize_in_txn`.
- `_materialize_in_txn` bypasses BOTH backends' auto-commit pitfalls: PG's `conn.autocommit = True` (explicit `BEGIN/COMMIT/ROLLBACK` on the cursor) and SQLite's `_exec_returning_id` auto-commit (raw cursor + deferred `conn.commit()`). Hard cap of 32 children. **The forced-failure no-orphan smoke assertion is the only credible guard against this bug class.**
- `_add_months_anchored(start, n)` implements end-of-month clamp anchored to the original start date (Algo A): Jan 31 + 1mo = Feb 28, + 2mo = Mar 31 (anchor preserved).
- The `next_step_for(manager_id)` wrapper surfaces an expiry-warning when a series' latest child is within 14 days; warning state is stamped on every child of the series so a SET-NULL'd parent doesn't break suppression.

### Pooling
Pooling is handled by Neon's serverless proxy; the app opens direct connections (no app-side pool).

## Testing gotchas

### CI runs 4 jobs (`.github/workflows/test.yml`)

| Job | Scope | What it proves |
| --- | ----- | -------------- |
| `tests-sqlite` | Streamlit `tests/` via pytest | Logic correctness (SQLite only) |
| `smoke-pg` | `scripts/smoke_pg.py` against `postgres:16` | Streamlit PG safety |
| `tests-django-sqlite` | Django `core/tests.py` via pytest | Django logic (SQLite `:memory:`) |
| `smoke-pg-django` | `scripts/smoke_pg_django.py` against `postgres:16` | Django PG safety |

### pytest is SQLite-only (both apps)

Streamlit: `tests/conftest.py` pins `_USE_PG=False`. Django: `mt/settings_test.py` overrides DB to SQLite `:memory:`. **Green pytest does not prove PG safety in either app.** The codebase has shipped four PG-only bugs that the SQLite suite missed: `init_db AttributeError`, `validate_session TypeError`, `LEFT(timestamp, 10)`, and `text BETWEEN date`.

### PG smoke tests are the safety net

Each app has its own smoke script. Both run against a real `postgres:16` service in CI on every PR. **Any PR touching SQL or schema must extend the relevant smoke script.**

### Cross-tenant tests

The smoke tests seed a second manager via app helpers (NOT raw SQL — round-3 review caught raw-SQL seeding as tautological) and assert bidirectional isolation: manager A sees zero of B's rows, AND vice versa. Mirror this when adding new aggregator-style helpers.

## Dispatch (`_DISPATCH`)
Sidebar nav is dispatched via a string→callable dict at `web_app.py:2005`. Renames change the *label* on the button, not the page key — that keeps cached `nav_page` values resolving across deploys. `tests/test_dispatch.py` parses the file via AST and asserts every value resolves to a defined function.

## Known limitations
- ICS export emits N separate `VEVENT`s for a recurring series (no `RRULE`). Acceptable temporary state; planned future PR.
- `prefill_series_id` flow (clicking the expiry warning to extend a series) populates from session_state and is lost on browser refresh — same limitation as the rest of the app's session-state-driven nav.
- Streamlit version is unpinned upper (`streamlit>=1.38.0`); deliberately no bespoke CSS pinned to internal class names.

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
- **Silent Failures:** Search for `try-except` blocks without logging or re-raising. Flag any instance where an error is "swallowed". Repository convention: `except: pass` literal patterns are forbidden by `tests/test_no_silent_excepts.py`; prefer assigning a fallback value or calling `logger.warning`.
- **Import Accuracy:** Verify that all newly added imports actually exist in the project environment.
- **Edge Cases:** Explicitly check for null/undefined inputs, empty lists, and network timeouts.
- **PG safety:** Any change to SQL, helpers, or schema must run via `scripts/smoke_pg.py`. SQLite-only pytest is not enough.
- **Schema dual-write:** New columns/indexes must land in all three locations (migration, `schema_postgres.sql`, SQLite block in `database.py`).

## 3. Troubleshooting & Recovery
| Problem | Immediate Action |
|---------|------------------|
| Tool Timeout | Wait 5s and retry once. If it fails again, report the specific timeout to the user. |
| Missing Context | Use `Grep` to find where the variable/class is defined before guessing its structure. |
| Test Failure | Do NOT "fix" the test to pass. Analyze the logic error in the source code first. |
| PG-only failure | Suspect TIMESTAMP-vs-TEXT comparison, `LEFT()` on a timestamp, or `conn.execute()` on a psycopg2 connection. |

## 4. Critical Rules
- **No Hallucinations:** If an MCP tool returns "no results," do NOT generate synthetic data. State "No results found" and ask for alternative search parameters.
- **Opinionated Naming:** Flag "clever" or ambiguous variable names (e.g., `temp`, `data`, `handle`). Suggest explicit alternatives.
- **Test-First Debugging:** Always suggest a specific test case that would have caught the bug being fixed.
