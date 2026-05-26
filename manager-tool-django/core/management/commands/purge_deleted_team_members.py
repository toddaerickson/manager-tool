"""Hard-delete TeamMember rows that were soft-deleted more than 30 days
ago (the undo window has expired).

Wired to a Render Cron in Phase 6. Idempotent — safe to run repeatedly.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import TeamMember, TeamMemberManager
from core.services.audit import log_mutation


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
        if opts["dry_run"]:
            self.stdout.write(
                f"DRY-RUN: would hard-delete {qs.count()} TeamMember row(s)"
            )
            return

        # Audit each delete BEFORE the row goes away — we need
        # manager_id / id / name to land in the trail. Iterating is fine
        # at current scale (the purge only sees rows >30 days old).
        # actor="system" tags this as cron-driven so /audit/?actor=user
        # stays a clean operator view.
        rows = list(qs)
        for tm in rows:
            log_mutation(
                tm.manager_id, "delete", "TeamMember", tm.id,
                f"Cron purge of soft-deleted member: {tm.name}",
                actor="system",
            )
        qs.delete()
        self.stdout.write(f"Hard-deleted {len(rows)} TeamMember row(s)")
