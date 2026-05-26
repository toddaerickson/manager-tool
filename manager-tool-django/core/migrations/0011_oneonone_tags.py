from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_migrate_praise_notes_to_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="oneononesession",
            name="tags",
            field=models.TextField(blank=True, null=True),
        ),
    ]
