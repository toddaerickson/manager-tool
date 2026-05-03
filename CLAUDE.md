# CLAUDE.md — Project Intelligence

## Project Overview
Manager Tool is a Streamlit-based management coaching journal with a dual-mode database (SQLite local / Neon PostgreSQL). The primary interface is `web_app.py`. The wisdom library contains 620 management ideas from 23 books.

## Development Commands
```bash
# Run locally
streamlit run web_app.py

# Run tests (SQLite-only — see "Testing gotchas" below)
python -m pytest tests/ -v

# Verify syntax
python -c "import py_compile; py_compile.compile('web_app.py', doraise=True)"

# Smoke test against real Postgres (requires DATABASE_URL)
python scripts/smoke_pg.py
```

## Sidebar IA (3 sections)
The sidebar is organized into Manager → Directs → Reference. Settings/Log Out live inside Reference under a thin divider. Tile labels are deliberate; page-keys in `_DISPATCH` are stable across rename rounds so cached `nav_page` values from prior deploys keep resolving:

```
MANAGER:    Dashboard · Upcoming · Manager Journal · Schedule Event · To Do · Decisions
DIRECTS:    1:1 Notes · Delegations · Feedback · Goals · Career Dev
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

### pytest is SQLite-only
`tests/conftest.py` pins `_USE_PG=False` for every test. **Green pytest does not prove PG safety.** The codebase has shipped four PG-only bugs that the SQLite suite missed: `init_db AttributeError`, `validate_session TypeError`, `LEFT(timestamp, 10)`, and `text BETWEEN date`.

### scripts/smoke_pg.py is the PG safety net
Runs against a real `postgres:16` service in CI on every PR via `.github/workflows/test.yml`. Mirrors production bootstrap (apply `schema_postgres.sql` → `init_db()` → exercise auth + sessions + aggregators + recurring materialization) and includes a forced-failure no-orphan assertion for `_materialize_in_txn`. **Any PR touching SQL or schema must extend it.**

### Cross-tenant tests
The smoke test seeds a second manager via app helpers (NOT raw SQL — round-3 review caught raw-SQL seeding as tautological) and asserts bidirectional isolation: manager A sees zero of B's rows, AND vice versa. Mirror this when adding new aggregator-style helpers.

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
