# Migration status

Snapshot of where the Streamlit → Django migration stands. Update this doc when phase boundaries move; re-merge to main so it stays the source of truth for "where are we right now."

**Last updated:** 2026-05-04
**Live Django app:** https://manager-tool-django.onrender.com
**Plan:** `MIGRATION_PLAN.md` · **Gates:** `PHASE_GATES.md` · **Open design questions:** `manager-tool-django/ARCHITECTURE_DEFICITS.md`

---

## TL;DR

- Phases 0–4 done. Django app deployed to Render with Google OAuth, Sentry, real PG smoke job in CI.
- **Phase 5 in progress** — 3 of 8 sub-pages ported (Team Members, Events, Action Items). 5 pages to go.
- Phase 6 partial — D1 (event edit) and D2 (Outlook source-of-truth contract + per-event link page) shipped early because they were tied to Phase 5 UX. SMTP invite + management commands still pending.
- **Streamlit is FROZEN.** No new entries to `_MIGRATIONS` in `database.py`; no feature work on `web_app.py`. All new development is in `manager-tool-django/`.
- **Render auto-deploys main.** `render.yaml` drives it; the build step runs `manage.py migrate` so Django migrations apply automatically on push.

## Phase progress

| Phase | Status | Notes |
|---|---|---|
| 0 — Prereqs | ✅ done | Devcontainer (Debian 12, Python 3.11, Node 22, gh) |
| 1 — Scaffold | ✅ done | Django 5.1, allauth, htmx, Sentry |
| 2 — Schema/models | ✅ done | All 22 tables modeled; `migrate --fake-initial` clean; smoke job runs against `postgres:16` in CI |
| 3 — Auth | ✅ done | Google OAuth via allauth; bridge middleware maps `request.user.email` → existing `Manager` row → `request.manager` |
| 4 — Render deploy | ✅ done | Live; Sentry receiving |
| **5 — Page port** (8 sub-pages) | **in progress** | 3 done, 5 to go (see below) |
| 6 — Background jobs | partial | D1 + D2 shipped; SMTP invite + crons pending |
| 7 — Cutover | not started | Phase 7 of plan |
| 8 — Decommission | not started | Drop Streamlit, drop legacy auth tables |

### Phase 5 sub-page progress

- ✅ **5.1 Team Members** — list, HTMX add, soft-delete with 30-day undo, restore. PRs #51, #52, #53.
- ✅ **5.2 Events**
  - One-off + cancel/complete (#54)
  - Time dropdown with AM/PM (#55)
  - Recurring (`core/services/events.py`, `transaction.atomic`, no-orphan smoke assertion) (#56 → recovered #57)
  - Dedupe + delete + 3-column row layout (#58)
  - Detail page + Edit + "Copy link for Outlook" (#60, also Phase 6 work)
- ✅ **5.3 Action Items / "To Do"** — list + overdue indicator + add + complete/uncomplete + delete; assignee removed; due_time dropdown; Event.__str__ for dropdowns (#61, #62, #63)
- ⏳ **5.4 Journal entries**
- ⏳ **5.5 Goals + skills + development plans**
- ⏳ **5.6 Delegations + decisions + running notes**
- ⏳ **5.7 Settings + config**
- ⏳ Dashboard panels — was deferred from Phase 4; aggregator panels (overdue todos, upcoming events, journal streak) become buildable as each Phase 5 page lands

### Phase 6 shipped early

- ✅ **D1 — Edit events** (Outlook contract: full edit with date/time warning) — PR #60
- ✅ **D2 — Outlook source-of-truth contract** decided + first integration: per-event detail page + "Copy link for Outlook" button — PR #60

### Phase 6 still pending

- ⏳ **D2 medium-term** — SMTP calendar invite for 1-on-1s (uses Streamlit's `calendar_service.py` as starting point)
- ⏳ Weekly digest cron
- ⏳ `purge_deleted_team_members` cron (the management command exists; just needs Render Cron wiring)

## Architecture deficits

`manager-tool-django/ARCHITECTURE_DEFICITS.md` is the long-lived doc for design questions / known gaps that don't fit a per-PR `PHASE_GATES.md` item. Status legend: 🔴 open · 🟡 partial · 🟢 closed.

- D1 (no event edit) → 🟢 closed
- D2 (Outlook source-of-truth contract) → 🟢 closed
- Add new entries here with sequential IDs (D3, D4, ...) when scope/design questions surface.

## Suggested next moves

1. **Phase 5.4 — Journal entries** is the next plan item. Pattern is well-established (mirror 5.3 for the simpler list+create; mirror 5.1 if soft-delete becomes useful).
2. After 5.4, **flesh out Dashboard panels** while user data is fresh — currently shows just team_member_count.
3. Phase 5.5–5.7 in user-priority order; they're additive.

## Anchor data

- Manager: `terickson@marathoncre.com`, username `todd`, `manager_id=1` — use as the cross-tenant test anchor in any new smoke job.
