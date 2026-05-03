# manager-tool-django

Django 5 + HTMX + Tailwind target of the Streamlit → Django migration. See `MIGRATION_PLAN.md` and `PHASE_GATES.md` at the repo root for the phased plan and hard transition criteria.

## Local dev

```bash
cd manager-tool-django
.venv/bin/python manage.py runserver
```

`.env` is gitignored — copy from `../.env.template` and fill in values. The Phase 0 ALTER on Neon `dev-django` is applied; pointing `DATABASE_URL` at that branch is the standard local dev setup.

## Tests

```bash
.venv/bin/pytest                              # SQLite, fast (TenantManager unit tests)
DATABASE_URL=postgresql://... \
  .venv/bin/python scripts/smoke_pg_django.py  # real PG, end-to-end bootstrap
```

The pytest suite is **SQLite-only by design** — `mt/settings_test.py` overrides `DATABASES` to `:memory:`. PG-specific behavior (composite uniques, partial indexes, datetime/date shape) is the smoke job's responsibility. Per CLAUDE.md, the SQLite suite alone is **not proof of PG safety** — the four PG-only bugs that have shipped in this codebase are exactly why.

## Phase 2 decisions (referenced from MIGRATION_PLAN.md gates)

### 1. Date-shape: option 1 — native `date`/`datetime` objects + fix consumers

Streamlit's `_normalize_row()` (in `database.py`) ISO-stringifies psycopg2's `date` and `datetime` returns so callers see `'YYYY-MM-DD'` text everywhere. **Django ORM does not do this** — it returns `datetime.date` / `datetime.datetime` objects.

We chose **option 1** (let the ORM return natives, fix every consumer) over option 2 (ISO-stringify shim). Reasoning:

- We haven't ported any helpers yet, so the migration cost is paid as we go in Phase 5, not retroactively
- `{{ obj.date|date:"Y-m-d" }}` in templates and `obj.date.isoformat()` in services are idiomatic Django
- Option 2 would re-introduce Streamlit's quirk into a clean Django codebase — bad smell for a future-Django dev encountering the project for the first time

**Per-PR check (Phase 5):** before opening any page-port PR, grep ported helpers for `BETWEEN`, `startswith(`, `[:10]` on date-typed columns. Those patterns assume strings and break silently against `date` objects (`==` returns `False` instead of raising). The grep check is in `MIGRATION_PLAN.md` Phase 5's "Pattern per page".

`*_date` columns are still typed `models.TextField()` here because the underlying schema stores them as TEXT `'YYYY-MM-DD'` (Streamlit convention; CLAUDE.md). Django returns those as Python `str`, not `date`. The native-vs-string concern is only for the `DateTimeField` columns (`created_at`, `updated_at`, etc.).

### 2. Streamlit migration-runner freeze

During the dual-run window (Phase 1 through Phase 7 cutover), Streamlit and Django both touch the same Neon database. Streamlit's `init_db()` reads `_MIGRATIONS` in `database.py:631` and applies anything new at startup; the ledger lives in `schema_migrations` (which Django marks `managed=False` and ignores).

**Decision: informal freeze — no new entries added to `_MIGRATIONS` until Phase 8 decommission.**

The plan calls for Streamlit feature work to be frozen during the migration (rookie-mistake #3 in `MIGRATION_PLAN.md`), so this falls out naturally. No code change required in `web_app.py`. If a hotfix to Streamlit needs a schema change (unexpected but possible), the change goes through Django's migration system, not the Streamlit runner — Streamlit's `init_db()` would then idempotently no-op against an already-applied schema.

Phase 8 drops the `schema_migrations` table entirely along with the Streamlit decommission.

## Phase 2 schema notes

- `config(manager_id, key)` was a composite-PK table; Phase 2 ALTER (see `../scripts/migrate_p2_config_to_id_pk.sql`) gave it an `id BIGSERIAL` PK and a `UNIQUE INDEX ux_config_manager_key`. Use `Config.objects.update_or_create(manager_id=X, key=Y, defaults={'value': Z})` as the upsert.
- `Session` and `LoginAttempt` are `managed=False` because django-allauth replaces them in Phase 3; the tables are dropped in Phase 8.
- Index names in `Meta.indexes` are sometimes shorter than the actual DB index names (Django enforces a 30-char limit; PG allows 63). The DB indexes are unaffected at query time — this is purely a Django bookkeeping concern. `core/models.py` has the rationale inline.
