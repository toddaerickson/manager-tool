-- Migration P1.3: partition the config table by manager_id (AUDIT C3).
-- Run via: psql "$DATABASE_URL" -f scripts/migrate_p1_3_per_tenant_config.sql
--
-- NOTE: As of P2.1, this migration is also applied automatically at app
-- startup via database.py `_run_migrations` (id `0003_partition_config_table`).
-- This .sql file remains as a manual escape hatch for DBA-driven deploys or
-- recovery from a corrupted schema_migrations ledger.
--
-- Background: AUDIT.md C3 — the config table had a single (key) PK, so all
-- tenants shared one set of API keys / SMTP credentials. This migration
-- partitions it by manager_id, with manager_id=0 reserved for system-wide
-- (deployment-owner) settings such as OAuth provider credentials.
--
-- Idempotent: safe to re-run. The marker `_migration_config_partitioned`
-- prevents double-application.

BEGIN;

DO $$
DECLARE
    has_manager_id BOOLEAN;
    sole_mid INTEGER;
    mgr_count INTEGER;
BEGIN
    -- Skip if already partitioned
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'config' AND column_name = 'manager_id'
    ) INTO has_manager_id;

    IF has_manager_id THEN
        RAISE NOTICE 'P1.3: config already has manager_id column; skipping migration.';
        RETURN;
    END IF;

    -- Determine the "sole manager" for orphan rows (everything that isn't a
    -- system key). If the deployment has exactly one manager, all per-tenant
    -- rows go to that manager. Otherwise rows go to manager_id=0 (system) and
    -- the operator MUST manually reassign before P1.2 scoping is enforced.
    SELECT COUNT(*) INTO mgr_count FROM managers;
    IF mgr_count = 1 THEN
        SELECT id INTO sole_mid FROM managers LIMIT 1;
    ELSE
        sole_mid := 0;
        RAISE NOTICE 'P1.3: % managers detected; per-tenant rows will land in manager_id=0. Operator must reassign manually.', mgr_count;
    END IF;

    -- Create new partitioned table
    CREATE TABLE config_new (
        manager_id INTEGER NOT NULL DEFAULT 0,
        key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY (manager_id, key)
    );

    -- Copy with classification: known system keys → manager_id=0, others → sole_mid
    INSERT INTO config_new (manager_id, key, value)
    SELECT
        CASE
            WHEN key IN ('google_client_id', 'google_client_secret',
                         'oauth_redirect_uri', 'allowed_emails',
                         'allowed_domain', '_migration_backfill_done',
                         '_migration_config_partitioned')
                 OR key LIKE '\_%' ESCAPE '\'
            THEN 0
            ELSE sole_mid
        END,
        key, value
    FROM config;

    DROP TABLE config;
    ALTER TABLE config_new RENAME TO config;

    INSERT INTO config (manager_id, key, value)
    VALUES (0, '_migration_config_partitioned', '1')
    ON CONFLICT (manager_id, key) DO NOTHING;

    RAISE NOTICE 'P1.3: config table partitioned (sole_mid=%).', sole_mid;
END $$;

COMMIT;
