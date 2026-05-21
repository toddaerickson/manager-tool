from django.db import migrations


class Migration(migrations.Migration):
    """Drop the Streamlit-era auth tables, decommissioned in Phase 8.

    `sessions` and `login_attempts` backed Streamlit's server-side session
    and rate-limit logic (audit H2/H3). They were modeled `managed = False`,
    so Django never owned their schema. django-allauth + Django's own
    `django_session` table replace them, leaving these orphaned.

    Both models are removed from migration state (so makemigrations stays
    clean) AND the tables are dropped from the database. DROP ... IF EXISTS
    keeps the run idempotent against prod and a no-op on the SQLite test DB
    (which never created them). Not reversible — the data is dead.
    """

    dependencies = [
        ('core', '0005_add_meeting_status_and_session_fk'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='Session'),
                migrations.DeleteModel(name='LoginAttempt'),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP TABLE IF EXISTS login_attempts;",
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql="DROP TABLE IF EXISTS sessions;",
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
