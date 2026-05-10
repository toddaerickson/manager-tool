# Migration status

Snapshot of where the Streamlit → Django migration stands. Update this doc when phase boundaries move; re-merge to main so it stays the source of truth for "where are we right now."

**Last updated:** 2026-05-10
**Live Django app:** https://manager-tool-django.onrender.com
**Plan:** `MIGRATION_PLAN.md` · **Gates:** `PHASE_GATES.md` · **Open design questions:** `manager-tool-django/ARCHITECTURE_DEFICITS.md`

---

## TL;DR

- Phases 0–4 done. Django app deployed to Render with Google OAuth, Sentry, real PG smoke job in CI.
- **Phase 5 complete** — all 8 sub-pages ported + dashboard panels + Feedback page. 183 Django tests.
- Phase 6 partial — D1 (event edit) and D2 (Outlook source-of-truth contract + per-event link page) shipped early. SMTP invite + crons still pending.
- **Streamlit is FROZEN.** No new entries to `_MIGRATIONS` in `database.py`; no feature work on `web_app.py`. All new development is in `manager-tool-django/`.
- **Render auto-deploys main.** `render.yaml` drives it; the build step runs `manage.py migrate` so Django migrations apply automatically on push.

## Phase progress

| Phase | Status | Notes |
|---|---|---|
| 0 — Prereqs | done | Devcontainer (Debian 12, Python 3.11, Node 22, gh) |
| 1 — Scaffold | done | Django 5.1, allauth, htmx, Sentry |
| 2 — Schema/models | done | All 22 tables modeled; `migrate --fake-initial` clean; smoke job runs against `postgres:16` in CI |
| 3 — Auth | done | Google OAuth via allauth; bridge middleware maps `request.user.email` → existing `Manager` row → `request.manager` |
| 4 — Render deploy | done | Live; Sentry receiving |
| **5 — Page port** | **done** | All sub-pages ported (see below) |
| 6 — Background jobs | **done** | Calendar service, coaching service, weekly digest, crons. PRs #71, #73. |
| 7 — Cutover | not started | Phase 7 of plan |
| 8 — Decommission | not started | Drop Streamlit, drop legacy auth tables |

### Phase 5 sub-page progress

- **5.1 Team Members** — list, HTMX add, soft-delete with 30-day undo, restore. PRs #51–#53.
- **5.2 Events** — one-off + recurring + dedupe + delete + detail + edit + "Copy link for Outlook". PRs #54–#60.
- **5.3 Action Items / "To Do"** — list + overdue + add + complete/uncomplete + delete. PRs #61–#63.
- **5.4 Journal entries** — daily/weekly/reflection, mood+energy, streak counter, HTMX add/edit. PR #65.
- **Dashboard panels** — 5 stat cards + upcoming events list + overdue to-dos list. PR #66.
- **5.5 Goals + Career Dev** — Goals CRUD with status; Skills, Dev Plans + Milestones, Career Conversations. PR #67.
- **5.6 Delegations + Decisions + 1:1 Notes** — delegation tracking with autonomy levels; decision log with review dates; running notes with broadcast. PR #68.
- **5.6b Feedback** — SBI framework (Situation, Behavior, Impact) per team member. PR #69.
- **5.7 Settings** — display name, timezone. PR #70.

### Phase 6 shipped early

- D1 — Edit events (Outlook contract: full edit with date/time warning) — PR #60
- D2 — Outlook source-of-truth contract + per-event detail page + "Copy link for Outlook" — PR #60

### Phase 6 shipped (PRs #71, #72, #73)

- Calendar service port (`core/services/calendar.py`) — ICS + SMTP invites with M3 sanitization
- Coaching service port (`coaching/services.py`) — Anthropic API + wisdom matcher, wired to journal page
- Weekly digest service + `send_weekly_digests` management command + Render cron (Mon 9 AM ET)
- `purge_deleted_team_members` Render cron (daily 1 AM ET)
- Shared email module (`core/services/email.py`) + journal streak utility (`core/services/journal.py`)
- D3 audit logging + D4 HTMX consistency — both closed
- **Still pending:** "Send invite" button on event detail page (D2 Option C UI trigger)

## Remaining sidebar placeholders

Three reference pages are not numbered sub-pages in the plan. They are aggregation/reporting views:
- **Analytics** — goal completion rates, feedback ratios, delegation metrics
- **History** — timeline of all activity across entities
- **Resources** — wisdom library (620 management ideas from 23 books)

These can be built post-Phase 5 or during Phase 6/7 prep.

## Architecture deficits

`manager-tool-django/ARCHITECTURE_DEFICITS.md` is the long-lived doc for design questions / known gaps.

- D1 (no event edit) → closed
- D2 (Outlook source-of-truth contract) → closed
- D3 (audit logging for HR data mutations) → closed (AuditLog model + log_mutation calls on all HR-sensitive views)
- D4 (HTMX consistency on Career Dev page) → closed (career dev content partial + hx-post on all forms)

## Suggested next moves

1. **"Send invite" button** on event detail page (D2 Option C UI trigger).
2. **Analytics/History/Resources** pages (additive, not blocking).
3. **Phase 7 — Cutover prep**: data-validation diff script, rollback rehearsal, telemetry for adoption tracking.

## Anchor data

- Manager: username `todd`, `manager_id=1` — use as the cross-tenant test anchor in any new smoke job.
