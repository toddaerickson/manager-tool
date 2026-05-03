# Security parity: Streamlit audit fixes vs. Django + allauth

The Streamlit codebase is audit-closed across 23 PRs. This document is the explicit checklist required by `PHASE_GATES.md` Phase 3 → 4: for each security-relevant audit finding, mark whether allauth+Django covers it, partially covers + mitigation, or doesn't cover + accept gap with reason. **Default-allauth is not equivalent to the audit-hardened Streamlit code; this checklist forces the conscious decision rather than silent regression.**

| Audit ID | Streamlit guarantee | Django + allauth status | Decision |
|---|---|---|---|
| **C1** | Every reader/mutator filters by `manager_id`; cross-tenant access returns no rows | `TenantManager.for_manager(X)` enforced via Phase 2 + per-PR review (Phase 5 grep gate) + `smoke_pg_django.py` cross-tenant assertion | **Covers.** Bridge middleware (`core/middleware.py`) adds `request.manager`; views call `for_manager(request.manager.id)`. |
| **H1** | Bcrypt password hashing + length cap | N/A — Django app uses Google OAuth only; no password storage in Django path | **Accept gap (intentional).** Streamlit's `managers.password_hash` rows stay in DB but are unused by Django. Dropped in Phase 8. |
| **H2** | Server-side sessions in `sessions` table with `session_token` cookie + `expires_at` + UA-hash binding | Django's session framework (used by allauth) gives server-side sessions + secure-cookie + HttpOnly + expiry. **Does NOT do UA-hash binding.** | **Partial cover + accept gap.** UA-binding was added to defeat token-replay attacks; Django's CSRF token + secure-cookie + SameSite=Lax cookies achieve a similar bar against the same threat class. The risk delta is small for a single-tenant app. Phase 5 adds explicit session inspection if/when needed. |
| **H3** | Persistent rate-limit table (`login_attempts`) blocks brute-force across server restarts | allauth's `ACCOUNT_RATE_LIMITS` defaults rate-limit login attempts in cache. **Without a persistent backend, restart wipes the limit.** No password login flow in Django app at all. | **Accept gap (intentional).** No password login → no brute-force surface. Google OAuth handles its own brute-force protection at Google's side. The `login_attempts` table is dropped in Phase 8. |
| **H6** | Atomic UPSERTs (no DELETE-then-INSERT race) | Django ORM's `update_or_create` is atomic in the same way. Phase 2's `Config.update_or_create` smoke assertion verifies. | **Covers.** |
| **L12** | HSTS / X-Frame-Options / Referrer-Policy / nosniff / CSP via reverse proxy | `mt/settings.py` sets `SECURE_HSTS_SECONDS`, `X_FRAME_OPTIONS=DENY`, `SECURE_REFERRER_POLICY`, `SECURE_CONTENT_TYPE_NOSNIFF`. HSTS only fires when `IS_PROD`. **CSP not yet wired** (no Django CSP middleware in Phase 1). | **Mostly covers + defer CSP.** Streamlit needed `unsafe-inline` in CSP because Streamlit injects inline scripts; Django doesn't, so a stricter CSP is feasible. Phase 5 adds `django-csp` if any page warrants it. |
| **M1** | HTML-escape Claude output before render | Django templates auto-escape by default (`{{ var }}` is HTML-escaped) | **Covers — for free.** No port needed. |
| **M2** | Wrap user input in `<user_input>` tags before sending to Claude (prompt injection defense) | Phase 5/6 ports `_sanitize_user_text` from `coaching.py` verbatim into `coaching/services/`. | **Will cover (Phase 5/6).** Tracked as Phase 5 deliverable. |
| **M3** | ICS / email header sanitization (`_safe_address_pair`, `_safe_header_text`, `_ics_escape`) | Phase 6 ports verbatim into `coaching/services/calendar.py`. | **Will cover (Phase 6).** Gate item in Phase 6 → 7. |
| **M5** | Btree indexes on hot WHERE columns | Phase 2 mirrored 13 hot-path indexes into `Meta.indexes` (some with shortened names; the long-named DB indexes still do the work). | **Covers.** |
| **M8** | `_redact_db_credentials` in PG error messages | Phase 6 ports verbatim into `core/utils.py`. | **Will cover (Phase 6).** Gate item in Phase 6 → 7. |
| **M9** | Encryption-key file (`chmod 600`) + prod-required env var; fail-closed init | `mt/settings.py` requires `CONFIG_ENCRYPTION_KEY` in prod (settings module fails import if missing in `IS_PROD` branch — TODO Phase 5 wire fail-closed assertion). Per-tenant encrypted config column reuses the same Fernet key/format. | **Will cover (Phase 5).** Currently env var is read but not asserted in prod. |
| **P0.3** | Password change requires current password (prevents session-hijack → password reset) | N/A — no password change flow in Django app (Google OAuth only) | **N/A.** Documented above. |
| **P1.2** | `assert manager_id is not None` at every aggregator (None must fail loud) | `TenantManager.for_manager(None)` raises `ValueError` (Phase 2 test covers). | **Covers.** |
| **P4** | Recurring events materialized atomically; `_materialize_in_txn` guards against orphaned children | Phase 5 ports the recurrence logic when the Events page is built. | **Will cover (Phase 5).** Tracked. |

## Summary

- **Covered now:** C1, H6, M1, M5, P1.2, L12 (mostly)
- **Covered later (Phase 5/6):** M2, M3, M8, M9, P4
- **Intentional gaps:** H1 (no password auth), H3 (no brute-force surface), H2 (UA-binding deemed low-marginal-value for Google-only single-tenant app)
- **Not yet wired:** L12 CSP (defer until any page warrants it)

**Re-review trigger:** if a second tenant is ever added to the production app, re-evaluate H2 (UA-binding) and H3 (rate limit) — the calculus changes when the blast radius is more than one user.
