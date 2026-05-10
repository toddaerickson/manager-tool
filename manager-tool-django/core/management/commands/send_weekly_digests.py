"""Send weekly digest emails to all managers with SMTP configured.

Wired to a Render Cron (Monday 9 AM) in Phase 6. Idempotent per week
— re-running won't re-send if the digest was already sent.
"""

import logging

from django.core.management.base import BaseCommand

from core.models import Config, Manager
from core.services.digest import send_weekly_digest

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send weekly digest emails to all managers with SMTP configured."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report which managers would receive digests without sending.",
        )
        parser.add_argument(
            "--manager-id",
            type=int,
            help="Send digest to a single manager (for testing).",
        )

    def handle(self, *args, **opts):
        if opts["manager_id"]:
            managers = Manager.objects.filter(id=opts["manager_id"])
        else:
            # Only managers who have SMTP configured
            configured_ids = (
                Config.objects.filter(key="smtp_server")
                .exclude(value__isnull=True)
                .exclude(value="")
                .values_list("manager_id", flat=True)
            )
            managers = Manager.objects.filter(id__in=configured_ids)

        sent = 0
        failed = 0
        skipped = 0

        for manager in managers:
            if opts["dry_run"]:
                self.stdout.write(
                    f"DRY-RUN: would send digest to manager {manager.id} "
                    f"({manager.display_name})"
                )
                skipped += 1
                continue

            success, message = send_weekly_digest(manager.id)
            if success:
                sent += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Sent digest to manager {manager.id} "
                        f"({manager.display_name}): {message}"
                    )
                )
            else:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"Failed for manager {manager.id} "
                        f"({manager.display_name}): {message}"
                    )
                )
                logger.warning(
                    "Weekly digest failed for manager %d: %s",
                    manager.id, message,
                )

        if opts["dry_run"]:
            self.stdout.write(f"DRY-RUN: {skipped} manager(s) would receive digests")
        else:
            self.stdout.write(f"Done: {sent} sent, {failed} failed")
