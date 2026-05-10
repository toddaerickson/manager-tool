# Migration status

Snapshot of where the Streamlit → Django migration stands. Update this doc when phase boundaries move; re-merge to main so it stays the source of truth for "where are we right now."

**Last updated:** 2026-05-10
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
| 8 — Decommission | not started | Drop Streamlit, drop legacy auth tables |

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

## Architecture deficits

All closed:
- D1 (event edit), D2 (Outlook contract), D3 (audit logging), D4 (HTMX consistency)

## Anchor data

- Manager: username `todd`, `manager_id=1` — use as the cross-tenant test anchor in any new smoke job.
