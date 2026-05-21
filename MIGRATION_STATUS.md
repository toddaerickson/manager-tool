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

## Prose quality — deslop workstream (not started)

The app ships prose on two surfaces, and both read like default AI output today. The `stop-slop` skill (installed at `~/.claude/skills/stop-slop/`) is the standard to hold them to: cut filler and adverbs, active voice with a human subject, no "not X, it's Y" contrasts, no em dashes, specific over vague, vary sentence rhythm. Score a sample against the skill's 1-10 rubric (Directness / Rhythm / Trust / Authenticity / Density); below 35/50 means revise.

Two surfaces, two different fixes:

**1. Generated coaching prose (the AI-authored surface).** Claude writes this fresh on every call, so the only durable lever is the system prompt. The prompts already say "No fluff. No corporate-speak." but don't encode the specific tells.
- Bake a condensed house-style block into `manager-tool-django/coaching/services.py:278` (`SYSTEM_PROMPT`) and `:307` (`DAILY_COACH_SYSTEM`): active voice, no em dashes, no binary contrasts, cut adverbs, name the person/situation over vague nouns, vary sentence length. Keep the existing word caps (250 words / 1-2 sentences).
- Mirror the same edit into Streamlit `coaching.py:137` so the two apps stay in parity until Phase 8 decommission (Streamlit is frozen for features, but a prompt-text edit to keep parity is a maintenance change, not a feature).
- Acceptance: generate 5 sample suggestions against real anchor data (`manager_id=1`), score each on the rubric, land them all at 35+/50. No em dashes, no "not X, it's Y", no `-ly` filler in the sample.

**2. Static prose (write-once, hand-edited).** No model in the loop — just run `/stop-slop` over the strings.
- Django: `core/services/digest.py` (weekly digest body), `core/services/email.py` (subjects + bodies).
- Streamlit: `templates.py` (email/notification templates).
- UI microcopy: toasts, empty states, button labels, and tooltips in `manager-tool-django/templates/` and `_partials/` (and the equivalent strings in `web_app.py`).
- Optional guard: a unit test or CI grep that fails on an em dash or a banned-phrase in template/email string literals. Static text can be checked statically; generated text can't, which is why surface 1 leans on the prompt instead.

**Out of scope unless asked:** `365_Great_Management_Ideas.md` and the wisdom library are curated quotes from named authors — deslopping them would misattribute. Leave them. The planning docs (this file included) are internal; deslop them only if onboarding readability becomes a real complaint.

**Coupling note:** surface 1 is the only place prose is generated, so it's the highest-leverage and the only one that needs ongoing attention. Surface 2 is edit-once and stays fixed.

## Architecture deficits

All closed:
- D1 (event edit), D2 (Outlook contract), D3 (audit logging), D4 (HTMX consistency)

## Anchor data

- Manager: username `todd`, `manager_id=1` — use as the cross-tenant test anchor in any new smoke job.
