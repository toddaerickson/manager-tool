# Migration Plan: Streamlit → Django + HTMX on Render

**Source app:** `manager-tool` (current Streamlit + Python on Neon Postgres, audit-closed, 220 tests)
**Target app:** Django 5 + HTMX + Tailwind, hosted on Render, Postgres on Neon (kept)
**Audience:** Solo novice programmer + Claude Code as pair, working in VS Code WSL Ubuntu
**Estimated effort:** 5-7 weeks calendar time, ~25-35 days of focused work (revised from initial 3-4 week estimate after senior-PM review — novice + first-encounter Django/HTMX/Render/allauth gotchas eat 2-4 hours each)
**Phase gates:** see `PHASE_GATES.md` for hard transition criteria. Definition-of-done in this file is the *narrative*; gates are the *checklist* that must be true before advancing.

---

## Guiding principles

- **The current Streamlit app keeps running the entire migration.** New Django app is built next to it in a new directory.
- **The new Django app builds against a Neon branch**, not production. Cutover is the last step. Branching is free on Neon.
- **One PR per phase**, with an agent-style audit before each merge (the same cadence the audit work used).
- **Deploy to Render early** (phase 4, not phase 8). Catches deployment issues while the codebase is small.
- **Don't port the legacy CLI clients** (`gui.py`, `manager_tool.py`). They were already on the chopping block per audit L5.

---

## Phase 0 — Prerequisites (1-2 hours)

### Goal
The dev environment has everything you need. VS Code talks to it. Claude extension is configured. You can `git push`. You can `psql` to a Neon dev branch.

### Commands

The audit-hardened Streamlit app already runs in this devcontainer (Debian 12 bookworm) on Python 3.11.13, with `build-essential` and `libpq-dev` already present. **Phase 0 was simplified vs. the original draft:** dropped pyenv (Django 5 supports 3.11; bookworm doesn't ship 3.12 in main repos so installing it would mean a ~10 min pyenv build for no real gain) and dropped fnm (we only need one Node version). NodeSource apt is the standard install path.

```bash
# psql client for Neon dev-branch verification
sudo apt-get install -y postgresql-client

# Node 22 LTS via NodeSource (one Node version, no version manager needed)
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | \
  sudo gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" | \
  sudo tee /etc/apt/sources.list.d/nodesource.list > /dev/null
sudo apt-get update -o Dir::Etc::sourcelist="sources.list.d/nodesource.list" \
  -o Dir::Etc::sourceparts="-" -o APT::Get::List-Cleanup="0"
sudo apt-get install -y nodejs
```

### VS Code setup
- Install the **WSL** extension (likely already installed).
- Install the **Claude** extension; connect it to your Anthropic account.
- Open the existing `manager-tool` repo via "Open Folder in WSL".
- Install Python + Pylance extensions in the WSL context.

### Decisions to lock before phase 1
- **Repo strategy:** New folder `manager-tool-django/` inside the existing repo (sibling to `web_app.py`). Keeps history; allows side-by-side reference.
- **Neon branch:** Create a `dev-django` branch from `main` in the Neon console. Build everything against this branch. Production stays on `main`.
- **Domain:** Decide if the Django app launches on a new subdomain (e.g., `app.yourdomain.com`) or replaces the Streamlit URL at cutover.

### Definition of done
- `python --version` → 3.12.7
- `node --version` → 22.x
- VS Code with Claude extension can edit a file in `~/manager-tool/`
- A new Neon branch exists and you can connect to it: `psql "$NEON_DEV_URL" -c "\dt"` shows your tables.

---

## Phase 1 — Django project scaffold (1 day)

### Goal
A `manage.py runserver` that loads, connects to the Neon dev branch, and serves a "Hello" page.

### Commands

```bash
cd ~/manager-tool
mkdir manager-tool-django && cd manager-tool-django

python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# Core stack
pip install \
  "Django>=5.0,<5.2" \
  "psycopg[binary]>=3.1" \
  "python-dotenv>=1.0" \
  "django-htmx>=1.17" \
  "django-tailwind>=3.8" \
  "django-allauth[socialaccount]>=0.61" \
  "django-environ>=0.11" \
  "cryptography>=42" \
  "bcrypt>=4" \
  "gunicorn>=22" \
  "whitenoise>=6" \
  "sentry-sdk>=2.0"

# Dev tools
pip install pytest pytest-django pytest-mock freezegun ruff

pip freeze > requirements.txt

django-admin startproject mt .
python manage.py startapp core      # team_members, events, etc.
python manage.py startapp coaching  # ports coaching.py logic
```

### Key files to create
- `.env` (gitignored) — `DATABASE_URL=postgres://...neon-dev-branch.../db`, `CONFIG_ENCRYPTION_KEY=...`, `DJANGO_SECRET_KEY=...`, `SENTRY_DSN=...`
- `.env.template` (committed, no secrets) — every var in `.env` listed with empty values, so deploy-time env-var inventory has a source of truth.
- `mt/settings.py` — pull from `.env` via `django-environ`; use `psycopg` driver; `sentry_sdk.init()` at module load with `DJANGO_INTEGRATION` and traces sampled at 0.1.
- `.gitignore` — add `.venv/`, `.env`, `__pycache__/`, `db.sqlite3`.

### Observability (wire NOW, not later)
Sentry goes in at Phase 1 because every later phase generates errors that you want to see in one place — not in 3 different terminal scrollbacks. The free tier is sufficient for this app's volume. Phase gate requires a deliberate test exception to appear in the Sentry dashboard before advancing.

### What Claude Code is best at here
> "Generate a Django 5 settings.py that reads DATABASE_URL from .env, configures django-allauth for Google OAuth, registers django-htmx and django-tailwind, and sets the security headers (HSTS, X-Frame-Options, etc.) from `.streamlit/config.toml`'s comment block."

### Definition of done
- `python manage.py runserver` shows the Django welcome page on `http://localhost:8000`.
- `python manage.py dbshell` connects to the Neon dev branch.
- `git status` clean (everything committed).

### Common pitfalls
- WSL networking: if `localhost:8000` doesn't load from Windows, use `python manage.py runserver 0.0.0.0:8000` and visit `http://wsl-ip:8000`.
- `psycopg` vs `psycopg2`: use `psycopg[binary]` (psycopg 3) — modern, faster, fewer build issues.

---

## Phase 2 — Schema + models (2-3 days)

### Goal
Django models that exactly match the existing Neon schema, with `python manage.py migrate --fake-initial` accepting reality AND `python manage.py makemigrations --dry-run` reporting "No changes detected" — the second check is what catches the silent column-drift `--fake-initial` ignores.

### Pre-decision: composite PK on `config(manager_id, key)`

**Resolution: drop the composite PK, add an autoincrement `id` PK, keep `unique_together = ('manager_id', 'key')`.** Use Django `update_or_create(manager_id=X, key=Y, defaults={'value': Z})` — semantically identical to today's `INSERT ... ON CONFLICT(manager_id, key) DO UPDATE` ([database.py:1665](database.py#L1665)).

Why this option (vs `django-composite-pk` library): only three callers (`set_config` / `get_config` / `get_all_config`), no FKs reference `config`, and `update_or_create` is the Django-idiomatic replacement. Adding a library for one table is overhead. The one-time SQL migration to add `id` runs against the Neon dev branch *before* `inspectdb`:

```sql
ALTER TABLE config ADD COLUMN id BIGSERIAL;
ALTER TABLE config DROP CONSTRAINT config_pkey;
ALTER TABLE config ADD PRIMARY KEY (id);
CREATE UNIQUE INDEX ux_config_manager_key ON config (manager_id, key);
```

After this runs on the dev branch, `inspectdb` will pick up the new shape cleanly. Production keeps the composite PK until the cutover migration applies the same `ALTER` against prod (Phase 7).

### Approach
Use `inspectdb` to generate models from the live schema, then hand-clean them.

```bash
# Auto-generate models from the existing Neon schema
python manage.py inspectdb > core/models_raw.py

# Then split + clean by hand (with Claude's help):
# - Move team_members, events, etc. into core/models.py
# - Move sessions, login_attempts into auth-related app
# - Add Meta.managed = False for tables we don't want Django to migrate yet
# - Once aligned, set managed=True and fake-apply the initial migration
```

### Multi-tenancy strategy
Match what's in production: row-scoped via `manager_id`. Implement once, use everywhere:

```python
# core/managers.py
class TenantManager(models.Manager):
    def for_manager(self, manager_id):
        return self.get_queryset().filter(manager_id=manager_id)

class TeamMember(models.Model):
    objects = TenantManager()
    manager_id = models.IntegerField(db_index=True)
    # ... rest of fields
```

Then every view does `TeamMember.objects.for_manager(request.user.id)` and never bare `.all()`. Audit-closed C1 stays closed.

### Date-shape gotcha (read this before writing any model)
Streamlit's `_normalize_row()` ([database.py](database.py)) converts psycopg2's `datetime`/`date` returns to ISO strings so callers see uniform `'YYYY-MM-DD'` text. **Django ORM does not do this** — it returns `datetime.date` and `datetime.datetime` objects. Every template, helper, and JSON serializer that assumes string dates will break silently (string compares against `date` objects raise `TypeError` in Python 3, but `==` returns False without raising).

Two options, pick one in Phase 2 and stick with it:
1. **Let Django return native objects, fix every consumer** — preferred for new Django code; `{{ obj.date|date:"Y-m-d" }}` in templates handles it.
2. **Add a project-wide model mixin or `to_dict()` that ISO-stringifies on the way out** — preserves the existing data shape; less Django-idiomatic but smaller blast radius if you're porting helpers verbatim.

Whichever you pick, write a one-paragraph decision note in the new Django app's README and link it from any service module that touches dates.

### Django PG smoke job (the SQLite suite is not the safety net)
`tests/conftest.py` pins `_USE_PG=False` (per CLAUDE.md). The Streamlit codebase has shipped four PG-only bugs that pytest missed; `scripts/smoke_pg.py` is what catches them. Django needs the equivalent.

Create `manager-tool-django/scripts/smoke_pg_django.py` and a CI job that runs it against a `postgres:16` service container on every PR. Minimum coverage: bootstrap the schema, exercise allauth login + session creation, run `for_manager()` filters across three tenant tables, run `update_or_create` against `config`, and assert cross-tenant isolation bidirectionally (manager A → 0 of B's rows AND vice versa). **No PR in Phase 5+ merges without this job green.**

### Definition of done
- `python manage.py migrate --fake-initial` succeeds.
- `python manage.py makemigrations --dry-run` reports "No changes detected" (silent column-drift check).
- A `python manage.py shell` query like `TeamMember.objects.for_manager(1).count()` returns the same count as the old Streamlit app shows. Verify on three different tenant tables, not one.
- `pytest` passes for at least one model-level test (port `tests/test_database.py::TestCrossManagerScoping` to Django ORM).
- `smoke_pg_django.py` runs green locally and in CI.
- Date-shape decision (option 1 or 2 above) is documented in the Django app's README.

### Where Claude Code shines
> "Convert `schema_postgres.sql` and this `inspectdb` output into clean Django models grouped by app, with TenantManager on every tenant-scoped table, and a Meta.indexes block matching the ix_* indexes from P4.1."

### Common pitfalls
- Composite PK on `config` (manager_id, key) — **resolved above** via `id` autoincrement + `unique_together` + the one-shot `ALTER TABLE`. Don't re-litigate.
- `schema_migrations` table — Django manages its own migrations. Leave the existing rows in place; ignore the table from Django (`Meta.managed = False`).
- `inspectdb` will not preserve partial indexes or expression indexes from M5. Diff the generated `Meta.indexes` against `schema_postgres.sql` by hand.

---

## Phase 3 — Auth + multi-tenancy (2 days)

### Goal
Google OAuth login works. After login, every page knows `request.user` and `TenantManager` filters automatically.

### Steps
1. Configure `django-allauth` for Google OAuth (your existing OAuth client ID/secret port over).
2. Create a `Manager` model that bridges Django's `User` to your existing `managers` table. Either: extend `AbstractUser` (preferred) or use `OneToOneField`.
3. Decommission custom session/rate-limit code — `django-allauth` handles both. The `sessions` and `login_attempts` tables can be dropped post-cutover.
4. Add a CSRF check helper for HTMX requests: `django-htmx` already wires this.

### Definition of done
- Click "Sign in with Google" → redirected to Google → returned to a dashboard URL.
- `request.user` is set; `request.user.is_authenticated` works.
- A test view that calls `TeamMember.objects.for_manager(request.user.id).count()` returns the correct per-tenant count.

### Common pitfalls
- `ALLOWED_HOSTS` in `settings.py` — set to `["localhost", "127.0.0.1", "your-render-app.onrender.com"]` once you deploy.
- OAuth redirect URI mismatch — Google Console needs `http://localhost:8000/accounts/google/login/callback/` for dev.

---

## Phase 4 — Base layout + first page + DEPLOY TO RENDER (2 days)

**Critical insight:** deploy to Render NOW, when there's almost nothing to debug. You'll catch deploy issues while the codebase is small.

### Steps
1. Set up Tailwind via `django-tailwind`. Create a base template (`templates/base.html`) with the layout.
2. Build ONE page end-to-end: the dashboard. Use HTMX for the dashboard's lazy-loaded panels (mirrors the `_dashboard_bundle` caching but with HTMX).
3. Push to Render.

### Render setup (in the Render dashboard)
- New → Web Service → Connect your GitHub repo.
- Set the **Root Directory** to `manager-tool-django/`.
- Build command: `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`
- Start command: `gunicorn mt.wsgi:application --bind 0.0.0.0:$PORT`
- Add env vars: `DATABASE_URL`, `CONFIG_ENCRYPTION_KEY`, `DJANGO_SECRET_KEY`, `MANAGER_TOOL_ENV=prod`, Google OAuth secrets.
- Add a **cron job** service for the weekly digest (you'll wire this in phase 6).

### Definition of done
- `https://manager-tool-django.onrender.com/` loads the dashboard.
- Login via Google works in production.
- Render logs are visible in the Render dashboard.

### Common pitfalls
- `collectstatic` fails: missing `STATIC_ROOT` setting or `whitenoise` in `MIDDLEWARE`.
- Free tier sleeps after 15 min idle. Either pay $7/mo or accept the cold start during dev.

---

## Phase 5 — Port pages, one at a time (5-8 days)

Each page = one branch + one PR. Same audit-then-merge cadence the audit work used.

### Recommended order (cheapest first)
1. **Dashboard** (already done in phase 4 — flesh out)
2. **Team members** (CRUD list/detail/create)
3. **Events / 1-on-1s** (with calendar invite via existing logic)
4. **Action items**
5. **Journal entries**
6. **Goals + skills + development plans**
7. **Delegations + decisions + running notes**
8. **Settings + config**

### Pattern per page
- Create a Django view, URL route, template.
- Use HTMX for partial swaps (e.g., "Add team member" form submits and swaps the list without a full reload).
- Port any Python logic from `web_app.py` into a service module (don't put logic in the view).
- Port any tests from `tests/test_*.py` into Django tests (`pytest-django`).
- **Date-shape check:** any helper ported from `web_app.py` that does string ops on a date field (BETWEEN, lex compare, `.startswith("2026-")`, etc.) is a landmine — Django returns `date` objects, not strings. Per Phase 2 decision, either let the ORM return natives and fix the helper, or run it through the ISO-stringify shim. Grep your ported code for `BETWEEN`, `startswith(`, and `[:10]` on date columns before opening the PR.
- **Smoke check:** the Django PG smoke job (`scripts/smoke_pg_django.py`) gets a new assertion for any aggregator-style helper added in this PR. Mirrors the Streamlit cadence — every new aggregator gets cross-tenant coverage.

### Where Claude Code shines
> "Port `web_app.py:page_team_members` to a Django ListView + HTMX-driven create form. Match the existing Streamlit UX (toast on success, member detail link). Use my `TenantManager.for_manager` pattern."

### Definition of done per page
- Visual parity with Streamlit (modulo "now it actually feels fast").
- All tests for that page pass.
- The page works in the Render production deploy.

---

## Phase 6 — Background jobs (1-2 days)

### Goal
The weekly digest cron runs in production. AI coaching calls happen in-request (already do).

### Steps
1. Port `calendar_service.py` into `coaching/services.py` and `core/services/digest.py`. Keep the audit-closed M3 sanitization intact.
2. Port `coaching.py` into `coaching/services.py`. Same deal.
3. Add a Render Cron job: command `python manage.py send_weekly_digests`, schedule `0 9 * * MON`. Implement that as a Django management command that loops over managers and calls the digest service.

### Definition of done
- Manually run `python manage.py send_weekly_digests` locally → digest email arrives.
- Render cron entry shows "succeeded" after one schedule trigger.

---

## Phase 7 — Production cutover (1 day, plan carefully)

### Goal
Production traffic flips from Streamlit to Django.

### Pre-cutover checklist
- [ ] Django app is feature-complete vs Streamlit (check page-by-page).
- [ ] Render service is on a paid plan (no cold starts).
- [ ] Custom domain configured on Render (if applicable).
- [ ] All test cases pass: `pytest manager-tool-django/` AND `smoke_pg_django.py` green in CI.
- [ ] Run a final smoke through every page in prod.
- [ ] **Cutover schema strategy locked in Phase 2** (see Phase 2 composite-PK section): point Django at production Neon main, run the one-shot `ALTER TABLE config` migration, re-run `migrate --fake-initial`. The dev-branch-drift-back path was rejected as higher-risk.
- [ ] **Backup taken AND test-restored to a throwaway Neon branch within last 24h.** A backup never restored is a wish. Procedure: `scripts/backup.sh` → create temp Neon branch from prod → `psql` the dump in → run a row-count diff against prod. Document the timing (5 minutes? 30?) so cutover-window planning is real.
- [ ] **Data-validation diff script reports zero discrepancies** (see below).
- [ ] **Rollback rehearsed against dev branch** (DNS-flip simulated — see below).

### Data-validation diff script (`scripts/cutover_diff.py`)
Before flipping DNS, prove Django reads match Streamlit reads against the same Neon database. The script does this for every tenant-scoped table:

```python
# Pseudocode — implement in scripts/cutover_diff.py
for manager_id in active_manager_ids:
    for table in TENANT_TABLES:  # team_members, events, journal_entries, ...
        streamlit_count = streamlit_db.count(table, manager_id)
        django_count = DjangoModel.objects.for_manager(manager_id).count()
        assert streamlit_count == django_count, f"DRIFT: {table} mgr={manager_id}"

    # Spot-check a few rows for shape parity (config decryption, date round-trip)
    for key in SAMPLE_CONFIG_KEYS:
        assert streamlit_get_config(key, manager_id) == django_get_config(key, manager_id)
```

Script must exit non-zero on any drift. Run it twice: once against dev branch (proves the script works), then against prod immediately before cutover. Zero discrepancies on prod is the go/no-go signal.

### Rollback rehearsal
"Point DNS back to Streamlit" is not a rollback plan until you've done it once. Procedure:
1. On the Neon dev branch, deploy both Streamlit and Django pointing at it.
2. Switch DNS (or Render custom domain) to Django.
3. Make a write through Django (add a team member).
4. Switch DNS back to Streamlit.
5. Verify Streamlit can read the row Django wrote AND can write a new row of its own.
6. If step 5 fails, you have a column-shape divergence Django introduced — fix before real cutover.

Rehearse this once in Phase 7 prep, not on cutover day.

### Backup taken (mechanical, separate from rehearsal above)
- [ ] Take a Postgres backup (`scripts/backup.sh`) immediately before cutover. Confirm the file exists and `pg_restore --list` parses it before proceeding.

### Cutover sequence
1. Put Streamlit in read-only / maintenance mode (or just stop the process — there's no real-time concurrency).
2. Point Render's `DATABASE_URL` at production Neon.
3. Run `python manage.py migrate` against prod (should be a no-op if you fake-applied; otherwise apply any pending Django migrations).
4. Update DNS (or Render custom domain) to point to the new app.
5. Verify login + a write + a read.
6. Stop the Streamlit deploy. Don't delete the code yet.

### Rollback plan
If something is broken: point DNS back to the Streamlit URL. Both apps target the same Postgres so no data was lost.

---

## Phase 8 — Decommission (1 day, a week later)

After a week of running fine on Django:
- Archive the Streamlit code (`git mv web_app.py legacy/`, etc.).
- Delete `gui.py`, `manager_tool.py` (audit L5 finally honored).
- Drop the obsolete tables: `sessions`, `login_attempts` (django-allauth replaced them). One Django migration.
- Remove the Neon dev branch.
- Update README.

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Schema drift between Streamlit's migration runner and Django's migration system | Medium | Use `--fake-initial`. Build against Neon dev branch first. |
| Multi-tenancy regression (forget `for_manager`) | Medium | Make `TenantManager` the default `objects` manager for tenant tables. Code review every view for `.all()` vs `.for_manager()`. |
| OAuth secrets misconfigured in Render | High the first time | Keep a `.env.production.template` and check each var against it before deploy. |
| Free-tier Render sleeps during demo | Low | Pay $7/mo before any user traffic. |
| Novice + Claude Code generates plausible but subtly wrong Django patterns (e.g., raw SQL where ORM should be used) | Medium | Code-review every PR. Ask Claude to produce tests alongside code. |
| Claude Code suggests a library you don't need | Medium | Stick to the `requirements.txt` from phase 1. Reject any new dep without a clear reason. |

---

## What you can ask Claude Code to do at each phase

| Phase | Best Claude Code prompts |
|---|---|
| 0 | "Audit my WSL Ubuntu setup against the prereq list and flag anything missing." |
| 1 | "Generate a Django 5 settings.py with django-allauth for Google OAuth, django-htmx, django-tailwind, and the security headers from `.streamlit/config.toml`'s comment block." |
| 2 | "Convert `schema_postgres.sql` into Django models grouped by app, with TenantManager on every tenant-scoped table." |
| 3 | "Port the AUDIT P0.3 password-change-requires-current logic from `database.py:update_manager_password` to a Django allauth `account.signals` handler." |
| 4 | "Build a base.html template with a Tailwind sidebar matching `web_app.py`'s layout, and a dashboard view that mirrors `_dashboard_bundle`." |
| 5 (per page) | "Port `web_app.py:page_team_members` to a Django ListView + HTMX-driven add form, with a passing pytest." |
| 6 | "Convert `calendar_service.send_weekly_digest` to a Django management command, preserving the M3 escape rules and M8 error handling." |
| 7 | "Generate a step-by-step cutover runbook based on my migration plan." |

---

## Top three rookie mistakes to avoid

1. **Putting business logic in views.** Keep views thin. Real logic in `services.py` modules. Same separation `database.py` already has.
2. **Skipping `pytest-django` setup early.** Without tests in phase 2, every later phase is harder to verify.
3. **Trying to keep the Streamlit + Django apps in feature-parity simultaneously.** Don't. Streamlit is frozen during the migration; only port to Django. New features wait until cutover.

---

## TL;DR command sequence

```bash
# Day 0
cd ~/manager-tool && mkdir manager-tool-django && cd manager-tool-django
python -m venv .venv && source .venv/bin/activate
pip install -r ../template_requirements.txt
django-admin startproject mt .
python manage.py startapp core
python manage.py startapp coaching

# Day 1-2: schema port
python manage.py inspectdb > core/models_raw.py
# ... clean up models_raw.py with Claude's help into core/models.py
python manage.py makemigrations
python manage.py migrate --fake-initial

# Day 3-4: auth + first deploy
# (configure allauth, push to Render)

# Days 5-15: port pages, one PR each (same audit cadence as the security audit work)

# Day 16: cutover
```

---

## Reference: the audit work this plan stands on

Before this migration starts, the existing Streamlit codebase has been hardened across 23 PRs. Concepts, patterns, and tests that should carry forward:

- **Multi-tenancy via `manager_id` row scoping** (audit C1, C2, C3) — keep as-is in Django via `TenantManager`.
- **Server-side sessions + persistent rate limit** (H2, H3) — replaced by `django-allauth`'s built-in equivalents.
- **Encryption-key file with chmod 600 + prod-required env var** (M9) — same pattern, same `cryptography`/Fernet code, same `_SENSITIVE_KEYS` set.
- **Migration runner with `schema_migrations` ledger** (P2.1) — Django's own migration framework replaces it; existing rows can be ignored.
- **Btree indexes on hot WHERE columns** (M5) — replicate via `Meta.indexes` on each Django model.
- **Atomic UPSERTs replacing DELETE-then-INSERT** (H6) — Django's `update_or_create` covers this pattern.
- **`_redact_db_credentials` for PG error messages** (M8) — keep as a small util in `core/utils.py`.
- **ICS / email header sanitization** (M3) — port `_safe_address_pair`, `_safe_header_text`, `_ics_escape` verbatim into `coaching/services/calendar.py`.
- **HTML-escaping Claude output before render** (M1) — Django templates auto-escape, so this becomes free.
- **Prompt-injection wrapping in `<user_input>` tags** (M2) — port the `_sanitize_user_text` helper unchanged.
- **220 tests** — port the regression tests gradually as you port each page; the audit-fix tests are the spec.
