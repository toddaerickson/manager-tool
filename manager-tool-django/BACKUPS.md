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

## Layer 2 — Nightly encrypted dump (GitHub Actions)

`.github/workflows/backup.yml` runs daily at 08:30 UTC (and on demand:
`gh workflow run backup.yml`). Each run:

1. `pg_dump`s production with a **Postgres 17** client — prod is Postgres 17,
   and `pg_dump` refuses to dump a server newer than itself.
2. Encrypts the dump (AES-256, `BACKUP_PASSPHRASE`) and proves the
   decrypt round-trip.
3. **Restores it into a scratch Postgres 17 and counts rows** on the core
   tables — an empty or wrong-database dump fails the run.
4. Fails if the row total shrank more than 30% since the previous run.
5. Uploads the encrypted dump as a workflow artifact, kept **30 days**.
6. Pings healthchecks.io. A day without a ping alerts you — this is what
   catches GitHub silently disabling a scheduled workflow.

Repo secrets: `PROD_DATABASE_URL`, `BACKUP_PASSPHRASE` (also in your password
manager — **backups are unrecoverable without it**), `HEALTHCHECK_PING_URL`.

### Restore

```bash
# 1. Pick a run (last 30 days are kept) and download its artifact:
gh run list --workflow backup.yml --limit 10
gh run download <run-id>                 # -> neon-backup-<N>/backup.dump.enc

# 2. Decrypt (prompts for BACKUP_PASSPHRASE):
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -in neon-backup-<N>/backup.dump.enc -out backup.dump

# 3. Restore into a FRESH database with a Postgres 17+ pg_restore. For a Neon
#    target, use the direct (non "-pooler") connection string:
pg_restore --no-owner --no-privileges -d "$NEW_DATABASE_URL" backup.dump
```

The dump includes the encrypted `config` table (SMTP/Anthropic secrets) and all
tenant data — keep the decrypted file somewhere you trust and delete it after.

> **Why not a Render cron?** A Render cron (`backup-db`, added 2026-08-10)
> duplicated this workflow and was retired: Render's native runtime documents
> no Postgres 17 client (prod is 17), its disk is wiped after every run, and it
> had no encryption or restore proof. The env it declared could not even boot
> `settings.py` in prod mode, so it exited 1 before dumping anything —
> `scripts/validate_render_blueprint.py` now checks that for every service.

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

1. **Enable Neon PITR window + scheduled snapshots** (console, 5 min).
2. **Keep the nightly workflow healthy** — it is already running; keep its three
   secrets set and the healthchecks.io check active.
3. Optionally, export all data manually whenever you want a portable copy.

---

## One-command Neon activation (`scripts/activate_backups.py`)

A helper script automates Layer 1 (Neon PITR + snapshot + schedule) via the
Neon v2 API, with a `--dry-run` mode so you can preview before applying.
Layer 2 needs no activation — it runs from repo secrets.

**What you need before running:** a **Neon API key** — Neon console →
*Account → API keys* → *Create API key*.

**Exact steps (run from the repo root, using the project venv python):**

```bash
# 1. Export your secret (never commit it):
export NEON_API_KEY="..."
# Optional - days of PITR history (default 7; max depends on Neon plan):
export BACKUP_RETENTION="7"
# Optional - target a specific Neon project/branch (auto-detected otherwise):
# export PROJECT_ID="..."
# export BRANCH_ID="..."

# 2. Preview everything it will do (no changes):
manager-tool-django/.venv/bin/python scripts/activate_backups.py --dry-run

# 3. Apply it:
manager-tool-django/.venv/bin/python scripts/activate_backups.py

# 4. Verify: Neon console → the project → Backups/Snapshots → PITR window set + a snapshot exists
```

What the script does:
- **Neon**: sets the project **history retention** (PITR window, from `BACKUP_RETENTION`),
  creates a **snapshot now**, and requests a **daily backup schedule** (best-effort — the
  exact schedule body can vary by plan; any error prints verbatim so you can align).

> Because this script can't be exercised against live accounts in CI (no API keys),
> always run `--dry-run` first and confirm the planned calls look right. If Neon
> changes an API shape, the script prints the raw error so you can adjust the affected call.

