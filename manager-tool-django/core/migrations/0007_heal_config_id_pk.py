from django.db import migrations

# Idempotent: only touches a config table still in the Streamlit-era shape
# (composite PK on (manager_id, key), no id column). Fresh deploys and CI
# already have id, so the whole block is a no-op there.
PG_HEAL = r"""
DO $$
DECLARE
    pk_name text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'config' AND column_name = 'id'
    ) THEN
        ALTER TABLE config ADD COLUMN id BIGSERIAL;

        SELECT conname INTO pk_name
        FROM pg_constraint
        WHERE conrelid = 'public.config'::regclass AND contype = 'p';
        IF pk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE config DROP CONSTRAINT %I', pk_name);
        END IF;

        ALTER TABLE config ADD PRIMARY KEY (id);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_config_manager_key
            ON config (manager_id, key);
    END IF;
END $$;
"""


def heal_config(apps, schema_editor):
    # SQLite test DB created config WITH id from 0001's CreateModel, so the
    # heal is PG-only. Postgres is the only backend that can be in the
    # un-migrated shape (prod's table came from schema_postgres.sql).
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(PG_HEAL)


class Migration(migrations.Migration):
    """Heal prod's config table to the id-PK shape PR 90's Config model expects.

    Prod's config table predates Django: schema_postgres.sql created it with a
    composite PK (manager_id, key) and no id column. Deploy runs
    `migrate --fake-initial`, which fake-applied 0001's CreateModel(Config)
    against the pre-existing table (fake-initial checks table existence, not
    columns), so id was never added. scripts/migrate_p2_config_to_id_pk.sql
    was meant to add it at cutover but never ran on prod, so `SELECT config.id`
    raised UndefinedColumn in production.

    RunPython (database-only — model state already matches via 0001), idempotent
    on PG, no-op on SQLite. Not reversible.
    """

    dependencies = [
        ('core', '0006_drop_streamlit_session_tables'),
    ]

    operations = [
        migrations.RunPython(heal_config, migrations.RunPython.noop),
    ]
