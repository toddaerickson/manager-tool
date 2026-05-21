# Migration status

Snapshot of where the Streamlit → Django migration stands. Update this doc when phase boundaries move; re-merge to main so it stays the source of truth for "where are we right now."

**Last updated:** 2026-05-11
**Live Django app:** https://manager-tool-django.onrender.com
**Plan:** `MIGRATION_PLAN.md` · **Gates:** `PHASE_GATES.md` · **Open design questions:** `manager-tool-django/ARCHITECTURE_DEFICITS.md`

---

## TL;DR

- **Phases 0–6 done.** Django app is feature-complete: all pages ported, coaching wired, crons running, analytics/history/resources live.
- **Phase 7 in progress** — cutover prep. Data-validation diff script written (`scripts/cutover_diff.py`). Checklist below.
- **Streamlit is FROZEN.** No new entries to `_MIGRATIONS` in `database.py`; no feature work on `web_app.py`.
- **Render auto-deploys main.** `render.yaml` drives it; the build step runs `manage.py migrate`.

## Phase progress

| Phase | Status | Notes |
|---|---|---|
| 0 — Prereqs | done | Devcontainer (Debian 12, Python 3.11, Node 22, gh) |
| 1 — Scaffold | done | Django 5.1, allauth, htmx, Sentry |
| 2 — Schema/models | done | All 22 tables modeled; `migrate --fake-initial` clean |
| 3 — Auth | done | Google OAuth via allauth; bridge middleware |
| 4 — Render deploy | done | Live; Sentry receiving |
| 5 — Page port | done | All sub-pages + dashboard + feedback + analytics/history/resources |
| 6 — Background jobs | done | Calendar + coaching + digest cron + purge cron |
| 7 — Cutover | **done** | Production live on Django as of 2026-05-10 |
| 8 — Decommission | **in progress** | Streamlit archived to `legacy/`; `gui.py`/`manager_tool.py` deleted; Streamlit CI jobs removed; `0006` drops `sessions`+`login_attempts` (needs prod apply); Neon dev branch deletion pending |

## Phase 7 — Cutover checklist

- [x] Django app is feature-complete vs Streamlit (all sidebar pages live)
- [x] Send invite button on event detail page (D2 Option C)
- [x] All test cases pass in CI (229+ Django tests + PG smoke)
- [x] Data-validation diff script written (`scripts/cutover_diff.py`)
- [ ] Render service on paid plan ($7/mo starter — already configured in render.yaml)
- [ ] Run `cutover_diff.py` against Neon dev branch (proves the script works)
- [ ] Run `cutover_diff.py` against production Neon (go/no-go signal)
- [ ] Backup taken AND test-restored to throwaway Neon branch
- [ ] Rollback rehearsed: Django writes readable by Streamlit and vice versa
- [ ] Point Django's `DATABASE_URL` at production Neon
- [ ] Run `manage.py migrate` against prod (should be no-op with --fake-initial)
- [ ] Smoke-write: create and delete a scratch journal entry via Django shell to confirm write path
- [ ] Update DNS / Render custom domain to point to Django
- [ ] Verify login + write + read on production
- [ ] **Rollback window: 30 minutes.** If any write fails within 30 min of DNS flip, revert DNS and re-enable Streamlit.
- [ ] Stop Streamlit deploy (don't delete code yet)

## Phase 5 sub-page progress (all done)

- **5.1** Team Members — PRs #51–#53
- **5.2** Events — PRs #54–#60
- **5.3** Action Items — PRs #61–#63
- **5.4** Journal entries — PR #65
- **Dashboard panels** — PR #66
- **5.5** Goals + Career Dev — PR #67
- **5.6** Delegations + Decisions + 1:1 Notes — PR #68
- **5.6b** Feedback — PR #69
- **5.7** Settings — PR #70

## Phase 6 (all done)

- Calendar service, coaching service, weekly digest, purge cron — PRs #71–#73
- Analytics, History, Resources pages — PRs #71–#73
- D1–D4 architecture deficits — all closed
- Send invite button — shipped in events_detail.html

## Post-cutover features

### Meetings page (PR #84) — shipped 2026-05-11

10/10/10 structured meeting recorder. Centerpiece of the Directs section.

**Gaps / v2 items:**
- **Prep mode**: auto-populate "Your Agenda" with open delegations/action items for the direct before the meeting starts
- **Tags + FTS search** across meeting notes (Django ORM search or django-watson)
- **Soft gate on "Their Agenda" first**: collapse "Your Agenda" by default to reinforce the MT direct-first principle
- **Meeting duration tracking**: actual duration vs scheduled
- **Deploy SHA in health endpoint**: `/verify-deploy` cannot confirm exact deployed version — add `git_sha` to the landing page or a `/health` JSON endpoint
- **1:1 Notes clarification**: "1:1 Notes" (RunningNote) and "Meetings" (OneOnOneSession) coexist in the sidebar. Notes = async between-meeting jots; Meetings = structured session records. Consider renaming "1:1 Notes" to just "Notes" or adding tooltip text to clarify the distinction. Evaluate whether Notes should eventually fold into the Meetings workflow.

## Phase 8 — Decommission checklist

- [x] Streamlit code moved to `legacy/` (kept in git as a rollback option, not deleted)
- [x] `gui.py` and `manager_tool.py` deleted (audit L5 finally honored)
- [x] Streamlit CI jobs (`tests-sqlite`, `smoke-pg`) removed; Django jobs remain
- [x] `sessions` + `login_attempts` models removed; `core/migrations/0006` drops the tables (`SeparateDatabaseAndState`, idempotent `DROP ... IF EXISTS`)
- [x] README points contributors at the Django app
- [ ] **Run `manage.py migrate` against prod Neon** to apply `0006` (drops the orphaned tables in production) — backup first
- [ ] Delete the Neon dev branch from the Neon console (manual — no API access from here)

`schema_postgres.sql`, `scripts/migrate_p2_config_to_id_pk.sql`, and `365_Great_Management_Ideas.md` stay at the repo root: the Django PG smoke test bootstraps the schema from the first two (`smoke_pg_django.py` mirrors the cutover bootstrap), and the coaching engine reads the wisdom library from the last (`coaching/services.py:107`). The other Streamlit `scripts/migrate_p1_*.sql` and `fix_sequences.sql` are pure history and live in `legacy/scripts/`.

### Runbook — apply migration 0006 to prod Neon

`0006` drops `sessions` + `login_attempts`. It is idempotent (`DROP TABLE IF EXISTS`) but irreversible — the reverse op is a no-op, so the backup is the only undo. Back up first.

1. **Back up prod (Neon branch snapshot).** Neon Console → project → Branches → Create branch → source = the production (Default) branch → name `backup-pre-0006`. Copy-on-write, instant. Optional file dump as well:
   ```bash
   pg_dump "$PROD_DATABASE_URL" -Fc -f backup_pre_0006.dump
   pg_restore --list backup_pre_0006.dump   # confirm it's readable
   ```
   If Neon is out of branch slots, delete the orphaned `preview/pr-*` and `smoke-*` CI branches first — they hold no real data and auto-expire. Never delete the Default/primary branch or the dev branch.
2. **Apply.** `render.yaml`'s build runs `manage.py migrate` on every deploy, so merging this work to `main` applies `0006` automatically. To do it deliberately instead, use Render → web service → Shell (prod `DATABASE_URL` is already in the env):
   ```bash
   python manage.py migrate core 0006 --plan   # preview
   python manage.py migrate                     # apply
   ```
3. **Verify.** In the Neon SQL editor or `psql "$PROD_DATABASE_URL"`:
   ```sql
   \dt sessions          -- expect "Did not find any relation"
   \dt login_attempts    -- expect "Did not find any relation"
   ```
   Then load the app and confirm login + a read/write still work (nothing in the Django app references those tables).
4. **Rollback (only if broken).** Point Render's `DATABASE_URL` at the `backup-pre-0006` branch, or restore that branch over prod. No data lost — the snapshot predates the drop.

## Architecture deficits

All closed:
- D1 (event edit), D2 (Outlook contract), D3 (audit logging), D4 (HTMX consistency)

## Anchor data

- Manager: username `todd`, `manager_id=1` — use as the cross-tenant test anchor in any new smoke job.
