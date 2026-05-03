"""Hard-delete TeamMember rows that were soft-deleted more than 30 days
ago (the undo window has expired).

Wired to a Render Cron in Phase 6. Idempotent — safe to run repeatedly.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import TeamMember, TeamMemberManager


class Command(BaseCommand):
    help = "Hard-delete TeamMember rows soft-deleted >30 days ago."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without actually deleting.",
        )

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(days=TeamMemberManager.UNDO_WINDOW_DAYS)
        qs = TeamMember.objects.filter(deleted_at__lt=cutoff)
        count = qs.count()
        if opts["dry_run"]:
            self.stdout.write(f"DRY-RUN: would hard-delete {count} TeamMember row(s)")
            return
        qs.delete()
        self.stdout.write(f"Hard-deleted {count} TeamMember row(s)")
