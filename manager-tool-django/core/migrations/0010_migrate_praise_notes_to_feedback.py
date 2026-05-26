"""Data migration: convert RunningNote(category="praise") rows to
Feedback(feedback_type="positive") rows. The "praise" category overlapped
conceptually with the positive Feedback path; consolidating into Feedback
gives us a single structured (situation/behavior/impact) trail.

The conversion is best-effort: the unstructured note content lands in
Feedback.behavior so it's not lost. Manager + team_member are carried
across; for broadcast praise notes (team_member IS NULL) we skip the
row because Feedback requires a team_member.
"""

from django.db import migrations


def migrate_praise_to_feedback(apps, schema_editor):
    RunningNote = apps.get_model("core", "RunningNote")
    Feedback = apps.get_model("core", "Feedback")

    praise = RunningNote.objects.filter(category="praise")
    for note in praise.iterator():
        if note.team_member_id is None:
            # Broadcast praise — Feedback model requires a team_member.
            # Leave the row as-is; the form drops "praise" so no new
            # broadcast-praise rows can be created going forward, and
            # category-filter UIs will just see "praise" as a legacy tag.
            continue
        Feedback.objects.create(
            manager_id=note.manager_id,
            team_member_id=note.team_member_id,
            feedback_type="positive",
            situation=f"(migrated from running note dated {note.note_date})",
            behavior=note.content or "",
            impact="",
            created_at=note.created_at,
        )
        note.delete()


def reverse_unsupported(apps, schema_editor):
    """No clean reverse: the source RunningNote rows were deleted on
    forward. Block reverse rather than silently leave Feedback rows
    that look user-created. Re-add the "praise" choice manually in
    forms.py if a rollback is needed."""
    raise NotImplementedError(
        "Reverse migration not supported; restore from backup or re-add "
        "the 'praise' choice in forms.py and accept the duplicates."
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_add_audit_actor_type"),
    ]

    operations = [
        migrations.RunPython(
            migrate_praise_to_feedback,
            reverse_unsupported,
        ),
    ]
