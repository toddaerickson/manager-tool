-- Phase 2 schema change: drop config's composite PK, add autoincrement id.
--
-- Why: Django ORM doesn't love composite PKs. By giving config an id PK
-- and a UNIQUE INDEX on (manager_id, key), Django's update_or_create
-- becomes the idiomatic replacement for the existing
-- "INSERT ... ON CONFLICT(manager_id, key) DO UPDATE" upsert
-- (see database.py:set_config). No FKs reference config, so this is safe.
--
-- Where this runs:
--   1. Phase 2: applied to the Neon dev branch (dev-django) BEFORE
--      `python manage.py inspectdb`, so generated models pick up the
--      new shape cleanly.
--   2. CI: applied by smoke_pg_django.py on a fresh postgres:16 service
--      container after schema_postgres.sql.
--   3. Phase 7 cutover: applied to production Neon during the cutover
--      window, before Django writes the first config row.
--
-- The PK constraint name varies by environment ("config_pkey" on a
-- fresh deploy from schema_postgres.sql; "config_new_pkey" on the dev
-- branch where a prior per-tenant migration rebuilt the table). The
-- DO block below discovers whichever name exists.

BEGIN;

ALTER TABLE config ADD COLUMN id BIGSERIAL;

DO $$
DECLARE
    pk_name text;
BEGIN
    SELECT conname INTO pk_name
    FROM pg_constraint
    WHERE conrelid = 'public.config'::regclass
      AND contype = 'p';
    IF pk_name IS NULL THEN
        RAISE EXCEPTION 'config table has no primary key — cannot proceed';
    END IF;
    EXECUTE format('ALTER TABLE config DROP CONSTRAINT %I', pk_name);
END $$;

ALTER TABLE config ADD PRIMARY KEY (id);

CREATE UNIQUE INDEX ux_config_manager_key ON config (manager_id, key);

COMMIT;
