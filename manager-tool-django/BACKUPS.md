# Backups & data safety

Your data (team, 1:1 notes, feedback, journal, delegations, decisions, goals,
skills, etc.) lives in **Postgres on Neon** (`DATABASE_URL`). This doc covers
the three layers that keep you from losing it.

## Layer 1 — Neon automatic backups (set this up once — ~5 min)

Neon keeps a change history so you can **point-in-time restore (PITR)** the
whole database to any moment in the retention window. This is your primary
safety net against accidental deletes or corruption. The window depends on
your plan:

| Neon plan | PITR history window |
|---|---|
| Free | ~6 hours / 1 GB |
| Launch | 7 days |
| Scale | up to 30 days |

To enable/raise it (do this in the Neon console, not in code):

1. Log in to [Neon console](https://console.neon.tech) → your project → **Settings → Branches**.
2. Set the **History retention** (PITR window) for the project. On Launch pick
   **7 days**; on Scale you can go to **30 days**. Longer = more storage billed,
   so pick what your plan allows and what you're comfortable paying for.
3. (Recommended) **Neon snapshots / backup schedule** — on paid plans you can
   create **scheduled snapshots** (Neon Console → **Backups/Snapshots** →
   **Backup schedules**). Snapshots are a durable, point-in-time copy you can
   restore from even after the PITR window passes. Schedule daily.
4. Verify: after enabling, test a PITR restore to a throwaway **branch** in the
   console (Branch → Restore from history) to confirm you can actually get data
   back. Do this once, now, not during an emergency.

> This is the highest-leverage step: it's set-and-forget, needs no code, and
> protects the entire DB. If you do nothing else, do this.

## Layer 2 — Automated `backup_db` (pg_dump) cron

`python manage.py backup_db` dumps the whole database to a gzipped,
timestamped `manager-tool-YYYYMMDD-HHMMSS.sql.gz` and prunes old snapshots
(`BACKUP_RETENTION`, default 7). It's wired to a **Render cron job**
(`backup-db`, daily 9 AM ET) defined in `render.yaml`.

```bash
# Run once manually to verify (needs pg_dump installed + DATABASE_URL set):
python manage.py backup_db --dry-run        # prints target, writes nothing
python manage.py backup_db                  # real dump to ./backups/
python manage.py backup_db --dir /tmp/x --keep 14
```

Key properties:

- **Credentials stay out of the process list**: `DATABASE_URL` is parsed into
  libpq env vars (`PGHOST`/`PGUSER`/`PGPASSWORD`/...), never passed as argv.
- **Retention**: keeps the newest `BACKUP_RETENTION` files (default 7), deletes
  older.
- **Fail-loud**: if `pg_dump` exits non-zero the command raises, so the cron run
  is marked failed and you'll see it in Render logs.
- **Optional offsite copy**: if `BACKUP_S3_BUCKET` (plus AWS credentials) is set,
  the snapshot is also uploaded to S3.

> ⚠️ **Render cron disks are ephemeral.** The local `backups/` file on the cron
> job's filesystem is deleted after the job finishes. So for the dump to
> actually be *durable* you must either (a) set `BACKUP_S3_BUCKET` + AWS creds
> in the cron's env so each snapshot is uploaded offsite, or (b) mount a
> persistent disk to the cron (paid Render feature). Without one of these, the
> nightly dump is still generated but not retained — Layer 1 (Neon) remains the
> real backup until you configure an offsite target.

### Restore

```bash
# Create a fresh DB, then:
gunzip < manager-tool-2026-05-01-030000.sql.gz | psql "$NEW_DATABASE_URL"
# Or in the Neon console: restore the snapshot / PITR branch.
```

The dump includes the encrypted `config` table (SMTP/Anthropic secrets) and all
tenant data — store the file somewhere you trust (private bucket, encrypted
disk).

## Layer 3 — Manual in-app exports

For an always-available copy you control, the app offers user-triggered exports:

- **Settings → Export all data (JSON)** — full archive of every model
  (team, events, 1:1s, delegations, feedback, goals, decisions, journal, notes,
  career). Excludes secrets.
- **Journal → Export CSV** — full journal history as CSV.

These are on-demand, not automated — use them when you want a portable snapshot,
but don't rely on them as the backup.

## Delete semantics (so you know what "losing" looks like)

- **Hard delete (permanent, no undo):** notes, feedback, journal entries,
  delegations, decisions, goals, skills, events, meetings.
- **Soft delete with recycle:** to-dos (1-day window), team members (30-day
  window, then purged by the `purge-deleted-members` cron).
- Layer 1 (Neon PITR/snapshots) is what makes a mistaken hard-delete recoverable.

## Summary

1. **Enable Neon PITR window + scheduled snapshots** (console, 5 min) — the real backup.
2. **Configure an offsite target for the `backup_db` cron** (S3 bucket) so the
   nightly dump is retained.
3. Optionally, export all data manually whenever you want a portable copy.

---

## One-command activation (`scripts/activate_backups.py`)

A helper script automates both sides (Render cron + env vars, and Neon PITR +
snapshot + schedule). It uses the current Render v1 and Neon v2 APIs and has a
`--dry-run` mode so you can preview before applying.

**What you need before running (5 min):**

1. **Render API key** — https://dashboard.render.com/account/api-keys → *Create API key*.
2. **Neon API key** — Neon console → *Account → API keys* → *Create API key*.
3. **(Recommended) an S3 bucket** + AWS access key/secret for durable offsite
   retention of the nightly dump.

**Exact steps (run from the repo root, using the project venv python):**

```bash
# 1. Export your secrets (never commit these):
export RENDER_API_KEY="rnd_..."
export NEON_API_KEY="..."
# Optional - for durable offsite retention of the nightly pg_dump:
export BACKUP_S3_BUCKET="your-backup-bucket"
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
# Optional - days of PITR history (default 7; max depends on Neon plan):
export BACKUP_RETENTION="7"
# Optional - target a specific Neon project/branch (auto-detected otherwise):
# export PROJECT_ID="..."
# export BRANCH_ID="..."

# 2. Preview everything it will do (no changes):
manager-tool-django/.venv/bin/python scripts/activate_backups.py --dry-run

# 3. Apply it (Render + Neon):
manager-tool-django/.venv/bin/python scripts/activate_backups.py

# 4. Verify:
#    - Render dashboard → Cron Jobs → backup-db → last run "successful"
#    - Neon console → the project → Backups/Snapshots → PITR window set + a snapshot exists
```

What the script does:
- **Render**: finds the `backup-db` cron, sets `BACKUP_S3_BUCKET`, `BACKUP_RETENTION`,
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` env vars (if provided), redeploys so they
  apply, and triggers one run to confirm it works.
- **Neon**: sets the project **history retention** (PITR window, from `BACKUP_RETENTION`),
  creates a **snapshot now**, and requests a **daily backup schedule** (best-effort — the
  exact schedule body can vary by plan; any error prints verbatim so you can align).

> Because this script can't be exercised against live accounts in CI (no API keys),
> always run `--dry-run` first and confirm the planned calls look right. If Neon or Render
> changes an API shape, the script prints the raw error so you can adjust the affected call.

