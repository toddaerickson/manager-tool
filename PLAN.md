# Remediation Plan — Manager Tool

**Source:** `AUDIT.md` (2026-05-02)
**Owner:** Engineering
**Planning date:** 2026-05-02
**Approach:** Ranked by impact × exploitability × blast-radius, with dependencies respected. Each milestone is independently shippable.

---

## Ranking method

Items are scored on three axes:

- **Impact** — What breaks or leaks if exploited? (data loss, cross-tenant exposure, account takeover, availability)
- **Exploitability** — How easy is it for a real attacker / accidental user? (1-click vs. requires misconfig)
- **Blast radius** — Who is affected? (one tenant, all tenants, all data)

Ties are broken by (a) reversibility — irreversible damage outranks recoverable, and (b) dependency graph — blockers ship before blocked.

---

## Priority 0 — STOP THE BLEEDING (this week)

These are issues that can destroy or leak data **today** with trivial effort. Ship as small, isolated PRs. No new features until P0 is done.

### P0.1 — Remove destructive auto-migration `os.remove(DB_PATH)` (C5)
- **Why first:** A normal upgrade silently deletes the user's database. Irreversible. One line of code.
- **Effort:** 30 min
- **Files:** `database.py:266-278`
- **Acceptance:** Delete the `os.remove` branch; replace with a clear error message instructing manual migration. Add a regression test that an existing SQLite file with the legacy schema is NOT removed on `init_db()`.
- **Dependencies:** None.

### P0.2 — Encryption fails-open → fail-closed (C4)
- **Why second:** Plaintext API keys silently written to DB; live keys rendered in the UI on misconfig. Single misconfigured deploy leaks every tenant's secrets.
- **Effort:** 2-3 hr
- **Files:** `database.py:33-69`, `web_app.py:1066-1070`
- **Acceptance:**
  - `_get_fernet` raises (not returns `None`) on init failure when writing a `_SENSITIVE_KEYS` value.
  - `_decrypt_value` raises on failure; never returns ciphertext.
  - UI mask uses `key in database._SENSITIVE_KEYS`, not substring match.
  - Tests: `test_sensitive_config_roundtrip`, `test_encryption_key_missing_fails_loud`.
- **Dependencies:** None.

### P0.3 — `update_manager_password` requires current password (H4)
- **Why third:** If anyone wires this to a route, account takeover is one HTTP call away. Cheap to fix preemptively.
- **Effort:** 1 hr
- **Files:** `database.py:651`, every caller
- **Acceptance:** Function signature takes `(manager_id, old_password, new_password)`; verifies old hash before writing. Caller in settings page passes the user's typed current password. Test added.
- **Dependencies:** None.

**P0 exit criteria:** All three merged to `main`, smoke tested on staging. Estimated total: **1 day** of one engineer.

---

## Priority 1 — MULTI-TENANCY (next 2 weeks)

The audit's headline finding. The CLAUDE.md guarantee is broken across the data layer; any logged-in tenant can read/mutate another's records by guessing IDs. This is the biggest single risk to the product and blocks any honest claim of multi-tenant safety.

### P1.1 — Add `manager_id` columns to orphan tables (C2)
- **Why before P1.2:** Without these columns, P1.2 has to scope through joins, which is slower and easier to get wrong. Doing the column add first makes P1.2 mechanical.
- **Effort:** 1 day (schema + backfill + dual-write)
- **Files:** `schema_postgres.sql:61-84,116-149`, `database.py` (table creation), new migration
- **Tables:** `feedback`, `goals`, `skills`, `development_plans`, `milestones`, `career_conversations`
- **Acceptance:**
  - New columns added with FK to `managers(id)`.
  - Backfill script populates from `team_members.manager_id`.
  - All inserts populate the column.
  - Composite indexes on `(manager_id, …)` per M5.
- **Dependencies:** Need P2.1 (migration system) ideally, but can ship as a hand-written script if P2.1 slips.

### P1.2 — Scope every reader and mutator by `manager_id` (C1)
- **Why now:** This is THE fix. Highest impact item in the audit.
- **Effort:** 3-4 days (mostly mechanical; ~30 functions)
- **Files:** `database.py` (every function listed in C1), every caller in `web_app.py`
- **Acceptance:**
  - Every public DB helper that reads/writes tenant data takes a required `manager_id: int`.
  - WHERE clauses include `AND manager_id = ?` (or scoped join).
  - The `delete_team_member` pattern (805-814) is the reference.
  - **Regression test per table** (T#3): manager B cannot read or mutate manager A's row. This is the audit's #3 priority test and is non-negotiable.
- **Dependencies:** P1.1.
- **Risk:** Easy to miss a function. Mitigation: grep for `def ` in `database.py`, build a checklist, tick each off. CI test must cover all listed tables.

### P1.3 — Per-tenant config table (C3)
- **Why now:** Currently all tenants share one Anthropic API key, one SMTP password. `get_all_config` returns everything to anyone. Fixing in the same milestone as C1/C2 keeps the data-layer rewrite atomic.
- **Effort:** 1 day
- **Files:** `database.py:674-727`, `schema_postgres.sql` (config table)
- **Acceptance:**
  - `config` PK becomes `(manager_id, key)`.
  - All callers pass `manager_id`.
  - Backfill assigns existing rows to a designated owner manager (document who).
  - Test: manager B cannot read manager A's API key.
- **Dependencies:** P1.1 (migration system pattern).

**P1 exit criteria:** Cross-manager regression suite passes for every table. Cannot ship to new tenants without this. Estimated total: **5-6 days** of one engineer.

---

## Priority 2 — DURABILITY & OPERATIONS (weeks 3-4)

Once tenancy is fixed, the next risk is operational: connections leak under load, the app silently degrades, backups leak credentials, there is no migration system. These bite under real usage rather than under attack, but they bite hard.

### P2.1 — Adopt a real migration system (depends-on for P1.1, but can ship after)
- **Effort:** 1-2 days (Alembic) or 4 hr (homegrown `schema_versions` table)
- **Files:** new `migrations/` dir, startup hook in `database.py`
- **Acceptance:** Sequenced upgrade scripts checked at startup. Backfill from P1.1 ported into a numbered migration. Postgres + SQLite both honored.
- **Recommendation:** Alembic. The audit specifically calls it out and it pays for itself within two migrations.

### P2.2 — Connection-leak refactor (H1)
- **Effort:** 1 day (mechanical)
- **Files:** Every public function in `database.py` (~88 call sites)
- **Acceptance:** Every helper uses `with _connect() as conn:`. The currently-defined-but-unused context manager (L6) becomes the standard. No `conn.close()` outside `_connect`.
- **Why now:** Neon's per-DB connection cap will throttle the app under any real concurrency. One-day fix, very high payoff.

### P2.3 — Server-side sessions + persistent rate limiting (H2 + H3)
- **Effort:** 2 days
- **Files:** `web_app.py:138-179`, `auth.py:144-157`, new `sessions` and `login_attempts` tables
- **Acceptance:**
  - `sessions` table with `created_at`, `last_seen`, `expires_at`, validated each request.
  - `login_attempts` keyed by username with exponential backoff, persists across tabs/sessions.
  - Logout clears the session row.
  - Test T#10 (`test_login_attempts_persist_across_sessions`) lands here.
- **Why bundled:** Both touch the auth layer. Doing them together avoids two passes through the same code.

### P2.4 — Backup hardening (H7)
- **Effort:** 4 hr
- **Files:** `scripts/backup.sh`
- **Acceptance:** No credentials on `ps`. `chmod 700 backups/`. GPG-encrypted dump. Off-host destination documented (S3/B2). Checksum + retention policy.

### P2.5 — Production safety: SQLite fallback gate (H5)
- **Effort:** 1 hr
- **Files:** `database.py:140-153`
- **Acceptance:** Fallback only fires when `MANAGER_TOOL_ENV != "prod"`. Production raises a hard error.

### P2.6 — Race-condition fixes (H6)
- **Effort:** 2 hr
- **Files:** `database.py:1315-1331`, `database.py:2143-2156`
- **Acceptance:** Replace delete-then-insert with `INSERT ... ON CONFLICT DO UPDATE`; add the missing unique constraints.

**P2 exit criteria:** App is production-safe under concurrent load with proper migrations, sessions, and backups. Estimated total: **6-7 days** of one engineer.

---

## Priority 3 — INPUT-HANDLING ATTACK SURFACES (week 5)

XSS, prompt injection, and header injection. Lower individual risk than P0-P2, but each is independently exploitable and trivial to fix.

### P3.1 — XSS in daily-coach suggestion (M1)
- **Effort:** 30 min
- **Files:** `web_app.py:353-362`
- **Acceptance:** Either `html.escape(suggestion["suggestion"])` or `st.info(...)` without `unsafe_allow_html`. Done together with P3.2 because they share threat surface.

### P3.2 — Prompt-injection mitigation (M2)
- **Effort:** 2-3 hr
- **Files:** `coaching.py:131-193`
- **Acceptance:** User content wrapped in `<user_input>...</user_input>`; system prompt instructed to ignore instructions inside those tags; response token cap; HTML escape on render (covered by P3.1).

### P3.3 — ICS / email header injection (M3)
- **Effort:** 2 hr
- **Files:** `calendar_service.py:35-79, 116-118, 168-171`
- **Acceptance:** `_ics_escape` strips `\r` and other control chars; email headers use `email.header.Header`. RFC 5545 conformance test (T#7) added.

**P3 exit criteria:** External input cannot escape into HTML, prompts, or mail headers. Estimated total: **1 day**.

---

## Priority 4 — PERFORMANCE (week 6)

User-visible latency, not security. Worth doing once the foundation is sound.

### P4.1 — Indexes on hot WHERE columns (M5)
- **Effort:** 2 hr
- **Files:** `schema_postgres.sql`, schema_sqlite (after L8 split)
- **Acceptance:** All eight indexes from M5 added via migration (P2.1).

### P4.2 — Streamlit caching + connection batching (M4)
- **Effort:** 1 day
- **Files:** `web_app.py` dashboard render path
- **Acceptance:** `@st.cache_data` / `@st.cache_resource` applied to read-only helpers. Dashboard render opens ≤2 connections. Fix N+1 at `web_app.py:1539-1545`. Replace `get_pending_action_items` double-call with a single `IN` query.

**P4 exit criteria:** Dashboard P95 render time materially improved (measure before/after). Estimated total: **1.5 days**.

---

## Priority 5 — POLISH & HYGIENE (ongoing)

Group these into a single "tech debt" PR or schedule one per sprint.

| ID | Item | Effort |
|----|------|--------|
| M6 | Replace silent `except` blocks with `logger.warning`/`logger.exception` | 2 hr |
| M7 | Add `key=` to dialog buttons | 15 min |
| M8 | Sanitize SMTP/Postgres errors before they reach UI | 1 hr |
| M9 | Refuse to start in prod without `CONFIG_ENCRYPTION_KEY`; `chmod 600` on key file | 1 hr |
| L1 | Extract `_build_update(table, allowed, fields)` helper | 2 hr |
| L2 | Pin `bcrypt.gensalt(rounds=12)` | 5 min |
| L3 | Validate OAuth nonce via `id_token` instead of `userinfo` | 3 hr |
| L4 | Lock dependencies with pip-tools, add `pip-audit` to CI | 2 hr |
| L5 | Move `gui.py` to `legacy/` or delete | 30 min |
| L7-L11 | Dead code, function splits, magic numbers, `_exec_returning_id` defensiveness | 4 hr total |
| L12 | Document expected proxy-layer security headers | 30 min |
| H8 | Document `fix_sequences.sql` as recovery-only; document `pg_dump` correct usage | 30 min |

**Estimated total:** ~2 days, parallelizable across multiple PRs.

---

## Priority 6 — TEST COVERAGE (parallel track)

Tests should land **with** the fixes, not after. The list below is what's owed beyond the regression tests already named in P0-P5.

1. **T#1** `test_sensitive_config_roundtrip` — lands in P0.2.
2. **T#2** `test_encryption_key_missing_fails_loud` — lands in P0.2.
3. **T#3** `test_cross_manager_*_rejected` (per table) — lands in P1.2. Non-negotiable.
4. **T#4** `test_coaching_handles_anthropic_error` — lands in P3.2.
5. **T#5** `test_coaching_no_api_key` — lands in P3.2.
6. **T#6** `test_is_email_allowed` — separate small PR.
7. **T#7** `test_ics_escape_rfc5545` — lands in P3.3.
8. **T#8** `test_generate_ics_structure` — lands in P3.3.
9. **T#9** `test_journal_streak_breaks_on_gap` — separate small PR.
10. **T#10** `test_login_attempts_persist_across_sessions` — lands in P2.3.

**Test-infra prerequisites (do first):**
- Add `requirements-dev.txt` with `pytest`, `freezegun`, `pytest-mock`. The current environment has no `pytest`; the suite cannot run from a fresh checkout. **Block:** ship this as the very first PR in P0 so all subsequent regression tests are runnable.
- Add a `manager` fixture to remove `db.create_manager(...)` boilerplate.
- Replace `datetime.now()` in time-sensitive tests with `freezegun`.

---

## Sequencing summary

```
Week 1:  P0 (stop the bleeding)            [1 day eng]   + dev-deps PR
Week 1:  Test infra (requirements-dev)     [parallel]
Week 2:  P1.1 — manager_id columns         [1 day]
Week 2:  P1.2 — scoping + regression tests [3-4 days]
Week 2:  P1.3 — per-tenant config          [1 day]
Week 3:  P2.1 — migrations                 [1-2 days]
Week 3:  P2.2 — connection refactor        [1 day]
Week 4:  P2.3 — sessions + rate limit      [2 days]
Week 4:  P2.4–P2.6 — backup, fallback gate, race fixes [1 day]
Week 5:  P3 — XSS / prompt / ICS           [1 day]
Week 6:  P4 — perf                         [1.5 days]
Ongoing: P5 — polish (one item per sprint)
Parallel:P6 — tests land with fixes
```

**Total focused engineering effort:** ~3-4 weeks for one engineer through P4. Polish (P5) extends indefinitely as background work.

---

## Risks to the plan

1. **P1.2 is mechanical but tedious.** Risk of missing a function. Mitigation: build the checklist from `grep "^def " database.py` and tick each off; CI test enforces coverage on the listed tables.
2. **P1.1 backfill on production data.** Risk: orphan rows with `team_member_id` pointing to a deleted member. Mitigation: dry-run on a staging clone; log orphan count before applying.
3. **P2.1 migration system adoption** can balloon if the team bikesheds Alembic vs homegrown. Decision should be made on day 1 of the milestone — recommend Alembic and move on.
4. **Encryption-fails-closed (P0.2)** will cause loud breakage on misconfigured deploys that were silently working. That is the point, but operators should be warned in the release notes.
5. **No staging environment is mentioned in the codebase.** Several P2 items (migrations, fallback gate, sessions) need somewhere to test before prod. If staging doesn't exist, that becomes a P2.0 prerequisite.

---

## Definition of Done (per PR)

Every PR in this plan must:
- Reference the audit ID it addresses (e.g. "Closes C1 — manager_id scoping").
- Include the regression test named in P6.
- Pass `python -m pytest tests/ -v`.
- Pass `python -c "import py_compile; py_compile.compile('web_app.py', doraise=True)"`.
- Update `CLAUDE.md` if the architecture decision changes (e.g., when the multi-tenancy guarantee is finally enforced, that line stops being aspirational).
