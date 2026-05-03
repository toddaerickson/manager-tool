# Phase Gates — Streamlit → Django Migration

Hard transition criteria for `MIGRATION_PLAN.md`. Definition-of-done in the plan is the *narrative*; this file is the *checklist*. **All items in a gate must be true before advancing.** A gate item that requires "decision" means a one-paragraph note committed to the repo, not a verbal commitment.

The gates exist because the Streamlit codebase has shipped four PG-only bugs that pytest missed. The pattern that bites this code is "looks fine, ships, breaks in prod" — gates are the antidote.

---

## Phase 0 → 1 (Prerequisites complete)

- [ ] `python3 --version` returns 3.11+ (Django 5.0/5.1/5.2 all support 3.10/3.11/3.12; the existing devcontainer's 3.11.13 is fine — pyenv was dropped from Phase 0 to save build time)
- [ ] `node --version` returns `22.x`
- [ ] `psql "$NEON_DEV_URL" -c "SELECT count(*) FROM team_members;"` returns the same number the Streamlit app shows for the same `manager_id`
- [ ] `git push` succeeds against the repo on a throwaway branch (proves SSH/HTTPS auth works in WSL)
- [ ] `.env.template` is committed to the repo with every required var listed (no values) — this is the deploy-time inventory of record
- [ ] Neon dev branch URL is in `.env.template` as a comment

## Phase 1 → 2 (Django scaffold + observability live)

- [ ] `python manage.py runserver` returns 200 on `/` and shows a "Hello" or Django welcome page
- [ ] `python manage.py dbshell` connects to dev branch and `\dt` lists existing tables
- [ ] **Sentry receives a deliberate test exception** within 60 seconds of triggering — proves observability works *before* there's anything complex to debug
- [ ] `git status` clean

## Phase 2 → 3 (Schema + models locked)

- [ ] One-shot `ALTER TABLE config` migration applied to Neon dev branch (composite-PK → autoincrement `id` + `unique_together`); verified with `\d config` showing `id` column and `ux_config_manager_key` unique index
- [ ] `python manage.py migrate --fake-initial` succeeds
- [ ] **`python manage.py makemigrations --dry-run` reports "No changes detected"** — this is the silent column-drift catch that `--fake-initial` misses
- [ ] For **three different tenant tables** (e.g., `team_members`, `events`, `journal_entries`), `Model.objects.for_manager(MGR_ID).count()` matches the Streamlit count exactly
- [ ] `pytest` green on the Django port of `tests/test_database.py::TestCrossManagerScoping`
- [ ] `scripts/smoke_pg_django.py` runs green locally
- [ ] CI job for `smoke_pg_django.py` against `postgres:16` service container is green on a PR
- [ ] **Date-shape decision documented** (option 1: native `date` objects + fix consumers, OR option 2: ISO-stringify shim) — one paragraph in the Django app's README, linked from any service module touching dates
- [ ] **Streamlit migration-runner freeze decision documented** — either explicitly stop the Streamlit app from running new migrations, OR accept ledger drift (and write down which)

## Phase 3 → 4 (Auth + multi-tenancy working)

- [ ] Google OAuth login round-trips locally: click "Sign in" → Google → returned to Django dashboard with `request.user.is_authenticated == True`
- [ ] A logged-in test view returns the right per-tenant row count via `for_manager(request.user.id)`
- [ ] `request.session` invalidates on logout (manual click-through verification)
- [ ] **`django-allauth` security-equivalence checklist filled out** — for each of H2 (server-side sessions + UA hash binding) and H3 (persistent rate-limit), explicitly mark: "allauth covers" / "allauth partially covers + mitigation X" / "allauth does not cover, accept gap because Y". Default-allauth is **not** equivalent to the audit-hardened code; this checklist forces the conscious decision rather than silent regression.
- [ ] Password-change-requires-current logic (audit P0.3) ported to allauth signal handler with a passing test

## Phase 4 → 5 (Render deploy live)

- [ ] `https://manager-tool-django.onrender.com/` (or chosen URL) serves the dashboard over HTTPS
- [ ] Login via Google works in production against the **Neon dev branch** (not prod — common slip-up)
- [ ] Render env-var inventory matches `.env.template` line-for-line; no missing vars
- [ ] A deliberate exception in the deployed app appears in Sentry within 60 seconds (proves prod observability, not just local)
- [ ] Render service is on a paid plan OR cold-start tolerance is explicitly documented (don't demo on free tier)

## Phase 5 (per page) → next page

For *every* page-port PR before merge:
- [ ] Visual parity confirmed via side-by-side screenshot in PR description
- [ ] All ported tests pass under `pytest`
- [ ] `smoke_pg_django.py` extended with cross-tenant assertion if this PR added an aggregator-style helper
- [ ] PG smoke CI job green
- [ ] Page works on the Render dev deploy (not just localhost) — a 30-second click-through is the gate
- [ ] Date-shape grep clean: no surviving `BETWEEN`/`startswith(`/`[:10]` on date columns from the Streamlit port that didn't account for native `date` objects (per Phase 2 decision)

## Phase 5 → 6 (All pages ported)

- [ ] Every page in the CLAUDE.md sidebar IA list is ported and gated above (Manager: Dashboard, Upcoming, Manager Journal, Schedule Event, To Do, Decisions; Directs: 1:1 Notes, Delegations, Feedback, Goals, Career Dev; Reference: Analytics, History, Resources, Team, Settings)
- [ ] `python manage.py check --deploy` returns zero issues
- [ ] All 220 Streamlit tests have either been ported, deliberately skipped (with reason), or marked obsolete (with reason)

## Phase 6 → 7 (Background jobs live)

- [ ] Weekly digest runs locally against dev branch and email arrives in a real inbox (not just "command exited 0")
- [ ] Render cron has executed at least once successfully — wait one schedule cycle, don't trust the dashboard "next run" estimate
- [ ] M3 sanitization tests (`_safe_address_pair`, `_safe_header_text`, `_ics_escape`) ported and green
- [ ] M8 `_redact_db_credentials` util ported into `core/utils.py` with a passing test

## Phase 7 → 8 (Production cutover complete)

The riskiest moment in the entire plan. **All five required:**
- [ ] **Backup taken AND test-restored to a throwaway Neon branch within 24h.** `pg_restore --list` parses the dump file. Restore time documented.
- [ ] **Data-validation diff script (`scripts/cutover_diff.py`) reports zero discrepancies** when run against prod immediately before cutover
- [ ] **Rollback rehearsed against dev branch** (DNS-flip simulated; Streamlit can read a row Django wrote and write its own afterward — proves no column-shape divergence)
- [ ] One-shot `ALTER TABLE config` migration applied to **prod** Neon
- [ ] You (the one manager) used the prod Django app for at least 30 minutes without finding a bug

## Phase 8 done (Decommission complete)

After **at least one full week** of running fine on Django:
- [ ] Streamlit code moved to `legacy/` (not deleted — keep one rollback option in git for 30 days)
- [ ] `sessions` and `login_attempts` tables dropped via Django migration that ran cleanly in prod (verified post-deploy with `\dt`)
- [ ] Neon dev branch deleted from Neon console
- [ ] README points new contributors at the Django app, not the Streamlit one
- [ ] Audit L5 finally honored: `gui.py` and `manager_tool.py` deleted

---

## Gate-failure protocol

If a gate item is false, **do not advance.** The temptation to "I'll fix it in the next phase" is exactly how the four PG-only bugs shipped. Either:
1. Fix it now, in the current phase, or
2. Document an explicit accept/defer with a written reason in the phase's PR description.

"It probably doesn't matter" is not an acceptable reason.
