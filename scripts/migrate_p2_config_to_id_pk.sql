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
--   2. Phase 7 cutover: applied to production Neon during the cutover
--      window, before Django writes the first config row.

BEGIN;

ALTER TABLE config ADD COLUMN id BIGSERIAL;

-- Constraint name in dev/prod is "config_new_pkey" (kept from a prior
-- table rebuild during the per-tenant migration). Verify with \d config
-- before running this against a different environment.
ALTER TABLE config DROP CONSTRAINT config_new_pkey;
ALTER TABLE config ADD PRIMARY KEY (id);

CREATE UNIQUE INDEX ux_config_manager_key ON config (manager_id, key);

COMMIT;
