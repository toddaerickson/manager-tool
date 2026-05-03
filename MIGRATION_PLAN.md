# Migration Plan: Streamlit → Django + HTMX on Render

**Source app:** `manager-tool` (current Streamlit + Python on Neon Postgres, audit-closed, 220 tests)
**Target app:** Django 5 + HTMX + Tailwind, hosted on Render, Postgres on Neon (kept)
**Audience:** Solo novice programmer + Claude Code as pair, working in VS Code WSL Ubuntu
**Estimated effort:** 3-4 weeks calendar time, ~15-20 days of focused work

---

## Guiding principles

- **The current Streamlit app keeps running the entire migration.** New Django app is built next to it in a new directory.
- **The new Django app builds against a Neon branch**, not production. Cutover is the last step. Branching is free on Neon.
- **One PR per phase**, with an agent-style audit before each merge (the same cadence the audit work used).
- **Deploy to Render early** (phase 4, not phase 8). Catches deployment issues while the codebase is small.
- **Don't port the legacy CLI clients** (`gui.py`, `manager_tool.py`). They were already on the chopping block per audit L5.

---

## Phase 0 — Prerequisites (2-3 hours)

### Goal
WSL Ubuntu has everything you need. VS Code talks to it. Claude extension is configured. You can `git push`.

### Commands (in WSL Ubuntu shell)

```bash
sudo apt update
sudo apt install -y build-essential libpq-dev postgresql-client

# Python 3.12 via pyenv
curl -fsSL https://pyenv.run | bash
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
echo '[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc
exec bash
pyenv install 3.12.7
pyenv global 3.12.7

# Node 22 (for Tailwind)
curl -fsSL https://fnm.vercel.app/install | bash
exec bash
fnm install 22
fnm default 22
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
  "whitenoise>=6"

# Dev tools
pip install pytest pytest-django pytest-mock freezegun ruff

pip freeze > requirements.txt

django-admin startproject mt .
python manage.py startapp core      # team_members, events, etc.
python manage.py startapp coaching  # ports coaching.py logic
```

### Key files to create
- `.env` (gitignored) — `DATABASE_URL=postgres://...neon-dev-branch.../db`, `CONFIG_ENCRYPTION_KEY=...`, `DJANGO_SECRET_KEY=...`
- `mt/settings.py` — pull from `.env` via `django-environ`; use `psycopg` driver.
- `.gitignore` — add `.venv/`, `.env`, `__pycache__/`, `db.sqlite3`.

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

## Phase 2 — Schema + models (2 days)

### Goal
Django models that exactly match the existing Neon schema, with `python manage.py migrate --fake-initial` accepting reality.

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

### Definition of done
- `python manage.py migrate --fake-initial` succeeds.
- A `python manage.py shell` query like `TeamMember.objects.for_manager(1).count()` returns the same count as the old Streamlit app shows.
- `pytest` passes for at least one model-level test (port `tests/test_database.py::TestCrossManagerScoping` to Django ORM).

### Where Claude Code shines
> "Convert `schema_postgres.sql` and this `inspectdb` output into clean Django models grouped by app, with TenantManager on every tenant-scoped table, and a Meta.indexes block matching the ix_* indexes from P4.1."

### Common pitfalls
- Composite PK on `config` (manager_id, key) — Django doesn't love composite PKs. Use `unique_together` + auto `id` PK, or `django-composite-pk`.
- `schema_migrations` table — Django manages its own migrations. Leave the existing rows in place; ignore the table from Django (`Meta.managed = False`).

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
- [ ] All test cases pass: `pytest manager-tool-django/`.
- [ ] Run a final smoke through every page in prod.
- [ ] Migrate the Neon dev branch's data drift back to main, OR point Django at the production Neon main branch and re-run `migrate --fake-initial`. Pick one approach and commit; the safer one is "just point Django at prod Neon" since all the schema changes match what Streamlit's migration runner already applied.
- [ ] Take a Postgres backup (`scripts/backup.sh`) immediately before cutover.

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
