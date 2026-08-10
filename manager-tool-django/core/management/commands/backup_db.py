"""Dump the production Postgres database to a gzipped pg_dump snapshot.

Wired to a Render Cron (`backup-db`, daily). Writes a timestamped
`manager-tool-YYYYMMDD-HHMMSS.sql.gz` to `BACKUP_DIR` (default `backups/`)
and prunes old snapshots beyond `BACKUP_RETENTION` (default 7).

The connection is passed via libpq env vars (PGHOST/PGUSER/PGPASSWORD/...)
so the password never appears in the process list — important for a cron
that runs unattended.

Restore (via psql into a fresh DB):

    gunzip < manager-tool-2026-05-01-030000.sql.gz | psql "$DATABASE_URL"

Because the dump includes the encrypted `config` table (which holds SMTP
creds and the Anthropic key) and all tenant data, the snapshot file must be
stored somewhere you trust. If `BACKUP_S3_BUCKET` + AWS credentials are set,
the snapshot is also uploaded to S3 (offsite copy) after the local write.
"""

import gzip
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

_PREFIX = "manager-tool-"
_SUFFIX = ".sql.gz"


def backup_filename(now=None):
    """Timestamped, lexicographically-sortable filename (oldest < newest)."""
    now = now or datetime.now()
    return f"{_PREFIX}{now.strftime('%Y%m%d-%H%M%S')}{_SUFFIX}"


def prune_old_backups(directory, keep):
    """Keep the `keep` most recent backups in `directory`, delete older.

    Returns the list of deleted paths. Sorted by filename — the timestamp
    format sorts chronologically.
    """
    directory = Path(directory)
    files = sorted(directory.glob(f"{_PREFIX}*{_SUFFIX}"), reverse=True)
    deleted = []
    for p in files[keep:]:
        p.unlink(missing_ok=True)
        deleted.append(str(p))
    return deleted


def pg_env_from_url(url):
    """Return a libpq-compatible env dict parsed from a DATABASE_URL.

    Puts credentials in env vars (never argv) so they don't leak into the
    process list. `sslmode` from the URL query defaults to `require`."""
    parts = urlsplit(url)
    qs = parse_qs(parts.query)
    return {
        "PGHOST": parts.hostname or "",
        "PGPORT": str(parts.port or 5432),
        "PGUSER": unquote(parts.username or ""),
        "PGPASSWORD": unquote(parts.password or ""),
        "PGDATABASE": parts.path.lstrip("/") or "",
        "PGSSLMODE": qs.get("sslmode", ["require"])[0],
    }


class Command(BaseCommand):
    help = "pg_dump the database to a gzipped snapshot with retention."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir", default=None,
            help="Backup directory (default: BACKUP_DIR env, else ./backups).",
        )
        parser.add_argument(
            "--keep", type=int, default=None,
            help="Number of snapshots to retain (default: BACKUP_RETENTION env, else 7).",
        )
        parser.add_argument(
            "--out", default=None,
            help="Exact output path (used by tests).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would be written without dumping.",
        )

    def _backup_dir(self, opts):
        return Path(opts["dir"] or os.environ.get("BACKUP_DIR") or "backups")

    def _keep(self, opts):
        if opts["keep"] is not None:
            return opts["keep"]
        return int(os.environ.get("BACKUP_RETENTION", "7"))

    def _pg_env(self):
        url = os.environ.get("DATABASE_URL")
        if url:
            return pg_env_from_url(url)
        cfg = getattr(settings, "DATABASES", {}).get("default", {})
        if not cfg.get("HOST"):
            raise CommandError(
                "DATABASE_URL is not set and settings has no DB host — cannot back up.",
            )
        return {
            "PGHOST": cfg.get("HOST", ""),
            "PGPORT": str(cfg.get("PORT") or 5432),
            "PGUSER": cfg.get("USER", ""),
            "PGPASSWORD": cfg.get("PASSWORD", ""),
            "PGDATABASE": cfg.get("NAME", ""),
            "PGSSLMODE": (cfg.get("OPTIONS") or {}).get("sslmode", "require"),
        }

    def _run_pg_dump(self, out_path):
        env = os.environ.copy()
        env.update(self._pg_env())
        cmd = [
            "pg_dump",
            "--no-owner",
            "--no-privileges",
            "--clean",
            "--if-exists",
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        )
        try:
            with gzip.open(out_path, "wb") as gz:
                shutil.copyfileobj(proc.stdout, gz)
        finally:
            proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", "replace")
        proc.wait()
        if proc.returncode != 0:
            self.stderr.write(
                f"pg_dump failed (rc={proc.returncode}):\n{stderr[:2000]}",
            )
            return None
        return out_path

    def _upload_to_s3(self, out_path):
        bucket = os.environ.get("BACKUP_S3_BUCKET")
        if not bucket:
            return None
        try:
            import boto3
        except ImportError:
            self.stderr.write(
                "BACKUP_S3_BUCKET is set but boto3 is not installed — "
                "skipping S3 upload.",
            )
            return None
        key = f"manager-tool/{out_path.name}"
        boto3.client("s3").upload_file(str(out_path), bucket, key)
        return key

    def handle(self, *args, **opts):
        out_dir = self._backup_dir(opts)
        keep = self._keep(opts)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = Path(opts["out"]) if opts["out"] else (out_dir / backup_filename())

        self.stdout.write(f"Backup target: {out_path}")
        if opts["dry_run"]:
            self.stdout.write("DRY-RUN: nothing written.")
            return

        written = self._run_pg_dump(out_path)
        if written is None:
            raise CommandError("pg_dump failed — no backup written.")

        self.stdout.write(self.style.SUCCESS(
            f"Backup written: {written} ({written.stat().st_size:,} bytes)",
        ))

        s3_key = self._upload_to_s3(out_path)
        if s3_key:
            self.stdout.write(self.style.SUCCESS(
                f"Uploaded to s3://{os.environ.get('BACKUP_S3_BUCKET')}/{s3_key}",
            ))

        deleted = prune_old_backups(out_dir, keep)
        if deleted:
            self.stdout.write(f"Pruned {len(deleted)} old backup(s).")
