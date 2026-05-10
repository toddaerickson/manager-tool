# Migration status

Snapshot of where the Streamlit → Django migration stands. Update this doc when phase boundaries move; re-merge to main so it stays the source of truth for "where are we right now."

**Last updated:** 2026-05-09
**Live Django app:** https://manager-tool-django.onrender.com
**Plan:** `MIGRATION_PLAN.md` · **Gates:** `PHASE_GATES.md` · **Open design questions:** `manager-tool-django/ARCHITECTURE_DEFICITS.md`

---

## TL;DR

- Phases 0–4 done. Django app deployed to Render with Google OAuth, Sentry, real PG smoke job in CI.
- **Phase 5 in progress** — 4 of 8 sub-pages ported (Team Members, Events, Action Items, Journal). Dashboard panels fleshed out. 4 pages to go.
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
| **5 — Page port** (8 sub-pages) | **in progress** | 4 done + dashboard panels, 4 to go (see below) |
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
- ✅ **5.4 Journal entries** — daily/weekly/reflection entries with mood+energy (1-5), private notes, tags, streak counter, HTMX add/edit (#65)
- ✅ **Dashboard panels** — 5 stat cards (team, upcoming 7d, pending, overdue, streak) + upcoming events list + overdue to-dos list (#66)
- ⏳ **5.5 Goals + skills + development plans**
- ⏳ **5.6 Delegations + decisions + running notes**
- ⏳ **5.7 Settings + config**

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

1. **Phase 5.5 — Goals + skills + development plans** is the next plan item. These are team-member-scoped, so patterns from 5.1 (team member dropdown) and 5.3 (status transitions) apply.
2. Phase 5.6–5.7 in user-priority order; they're additive.
3. Dashboard panels will grow automatically as more pages land (e.g., delegation overdue count, decision review count).

## Anchor data

- Manager: `terickson@marathoncre.com`, username `todd`, `manager_id=1` — use as the cross-tenant test anchor in any new smoke job.
