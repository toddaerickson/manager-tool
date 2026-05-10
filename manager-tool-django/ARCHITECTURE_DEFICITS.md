# Architecture deficits

Open design questions and known gaps in the Django port that don't fit a per-PR PHASE_GATES item but should be tracked and resolved before the migration is "done." Each entry captures the deficit, why it matters, options considered, and a recommendation.

Status legend: 🔴 open · 🟡 partially addressed · 🟢 closed

---

## D1 · 🟡 Scheduled events have no edit function (unblocked by D2 contract)

**Deficit.** Once a Manager Tool event is created, the only state transitions available are Cancel (status → cancelled, kept for history), Complete (status → completed), and Delete (hard remove). There is no path to edit fields like title, agenda, location, date/time, or duration after creation. Streamlit's page didn't have full edit either, but this is a clear gap relative to user expectations of any modern scheduling tool.

**Why it matters.** Real scheduling work involves rescheduling. "Move the 1:1 from Tuesday to Wednesday," "fix the typo in the title," "add the meeting link Outlook just generated." Today the only workflow is delete + recreate, which loses any associated history (running notes, action items linked via FK, etc.).

**Open question (D2 below) blocks part of the answer:** if Outlook is the calendar source of truth, then editing date/time in Manager Tool is at best informational — the actual reschedule has to happen in Outlook, and Manager Tool needs to either follow or accept drift. So D1 and D2 are coupled.

### Options
A. **Edit-anything modal** — Django form pre-populated with the row, POST updates fields. Simplest. Matches conventional CRUD UX.
B. **Edit-cosmetic-only** — title / agenda / location / notes editable; date/time/duration immutable (if Outlook is source of truth, force the reschedule via Outlook).
C. **Edit-with-warning on date/time** — full edit, but date/time changes show "Outlook isn't notified — update there too" inline.

### Resolution path (per D2 contract)

With D2 decided (Outlook owns *when*, MT owns *context*), D1's design is now clear: **option C — full edit with a warning on date/time changes that Outlook isn't notified.**

- Editable freely: title, agenda, location, duration, notes
- Editable with warning: scheduled_date, scheduled_time (Outlook isn't notified — update there too)
- Per CLAUDE.md, recurring child edits do NOT propagate to siblings; the edit UI must make this obvious

### Acceptance criteria when this is closed
- Click "Edit" on any row in Upcoming → form pre-populated → save updates the row
- Tests: edit own event, cross-tenant rejected, recurring child edit doesn't propagate to siblings
- Date/time edit shows the inline warning

---

## D2 · 🟢 Manager Tool events vs. Outlook calendar — where do scheduled events really fit?

**DECIDED 2026-05-04 (user-confirmed):** Outlook owns *when* (date / time / reminders); Manager Tool owns *context* (agenda / notes / action items / coaching). See "Recommendation" below for the implementation track. Implementation work is tracked per-PR in Phase 6.


**Question (from user).** "Outlook is how we manage our calendars, not this tool. Where do these scheduled events really fit? Should these scheduled events create an ICS for Outlook? Or should we just copy a manager-tool hyperlink into the Outlook calendar entry that we are creating?"

**Why it matters.** Two systems holding "when is this meeting" data is the canonical recipe for drift, and drift on a calendar is *worse* than the absence of integration — the user trusts Outlook, doesn't trust Manager Tool, gets surprised when Manager Tool doesn't reflect the Outlook reschedule. Picking the source-of-truth contract clearly is the highest-leverage decision in this area.

### Options

| Option | Pros | Cons |
|---|---|---|
| **A. Manager Tool emits an ICS file on event creation; user downloads + imports to Outlook** | One-time per event; Streamlit already does this so the code exists in `calendar_service.py` | Manual download step every time; updates in MT don't propagate; ICS-with-RRULE support is uneven across clients (CLAUDE.md notes Streamlit currently emits N VEVENTs for a series, which is acceptable but not native) |
| **B. Manager Tool generates a hyperlink; user pastes into the Outlook event they're creating** | No file UX; Outlook stays canonical; clicking the link from Outlook brings the right MT context up; simple to ship | User must remember to paste the link (bookkeeping burden); date/time still held in two places |
| **C. Manager Tool sends an email calendar invite (RFC 5545 SMTP attachment); user accepts in Outlook native UI** | Lands the meeting in Outlook through the standard accept-invite path; for 1-on-1s the team member gets a native invite too — that's a real feature; Streamlit's `calendar_service.py` is the natural home | Requires SMTP credentials configured; updates require sending UPDATE invites which the codebase doesn't yet do; spam-folder risk |
| **D. Microsoft Graph API two-way sync (read + write Outlook calendar from Manager Tool)** | Single source of truth (Outlook); no file UX; updates flow both ways | Big lift (Graph OAuth + token refresh + delta sync + conflict resolution); too big for the migration; defer |

### Recommendation

**Pick the source-of-truth contract first; the integration mechanics fall out.**

Recommendation: **Outlook is the source of truth for *when*; Manager Tool is the source of truth for *context* (agenda, notes, action items, coaching wisdom).** Then:

- **Phase 6 short-term: Option B (hyperlink).** Add a "Copy link for Outlook" button on each event row that copies a stable URL like `https://manager-tool-django.onrender.com/events/<id>/`. User pastes it into the Outlook invite they're creating. Dirt cheap, no infrastructure. Uses Outlook's UI for the calendar work; Manager Tool URL preserves context.
- **Phase 6 medium-term: Option C (email invite) for 1-on-1s only.** When the event has a `team_member` with an email, send an RFC 5545 invite to both manager + direct via SMTP (Streamlit's existing weekly-digest infra extends naturally). For events without a team_member, fall back to the link.
- **Defer Option A and D entirely** unless a clear need surfaces. ICS download is the worst-of-both-worlds; Graph API is too big for the migration scope.

**Implication for D1 (edit function):** with Outlook as source-of-truth for *when*, Manager Tool's event date/time becomes effectively a denormalized cache. Editing date/time in MT means accepting drift; warn the user inline. Title / agenda / location / duration are MT's domain and can be edited freely.

### Acceptance criteria when this is closed
- One concrete decision committed (here in this doc) on the source-of-truth contract
- Phase 6 ships at minimum the link button (Option B)
- 1-on-1 events with team_member email ship the invite path (Option C) by Phase 7 cutover or as a follow-up
- D1 (edit) implemented consistently with the chosen contract

---

## D3 · 🟢 Audit logging for HR data mutations

**CLOSED 2026-05-10.** Flagged by /review-as audit on PR #67.

**Deficit.** Feedback, career dev, delegations, and goals contain HR-sensitive data. Create/update/delete operations had no audit trail — no way to trace who changed what and when for compliance review.

**Resolution.** Added `AuditLog` model (`core/models.py`) with immutable append-only entries. `core/services/audit.log_mutation()` is called from every HR-sensitive mutation in views: TeamMember (add/delete), Feedback (add/delete), Goal (add/edit/delete), Delegation (add/edit/delete), Decision (add/edit/delete), Skill (add/delete), DevelopmentPlan (add), Milestone (add), CareerConversation (add), RunningNote (add/delete).

Cross-tenant isolation is enforced via `TenantManager.for_manager()` on the `AuditLog` model.

---

## D4 · 🟢 HTMX consistency on Career Dev page

**CLOSED 2026-05-10.** Flagged during Phase 5.5 review.

**Deficit.** The Career Dev page used full-page redirects (`redirect("career-dev")`) after every add/update/delete operation, while all other Phase 5 pages (team members, todos, journal, goals) used HTMX partials for smooth in-place updates.

**Resolution.** Extracted career dev content into `_partials/career_dev_content.html` with an `#career-dev-content` target div. Converted skill, plan, milestone, and conversation forms to use `hx-post` with `hx-target="#career-dev-content"`. Plan status buttons and milestone completion use HTMX `hx-post` with `hx-vals`. Skill delete uses `hx-delete` targeting the same partial. All Career Dev mutation views now return `_career_dev_partial()` (200 with HTML fragment) instead of `redirect()` (302).

---

## How to use this doc

- Add new entries with sequential IDs (D5, D6, ...)
- Status updates as the migration progresses; promote 🔴 → 🟡 → 🟢
- When a deficit closes, leave the entry in place with the resolution noted — future migration archaeologists thank you
