# Roadmap PR 10 — actual meeting duration (v2 gap, MIGRATION_STATUS.md).
# Additive nullable column; safe to apply before or after deploy.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_oneonone_prep_brief'),
    ]

    operations = [
        migrations.AddField(
            model_name='oneononesession',
            name='actual_duration_minutes',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
