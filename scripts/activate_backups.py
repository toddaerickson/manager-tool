#!/usr/bin/env python3
"""Activate the backup system end-to-end: Render cron + env vars, and Neon PITR + snapshots.

Usage (run with the project venv python so `requests` is available):

    python scripts/activate_backups.py --dry-run    # show what would be done (no changes)
    python scripts/activate_backups.py              # do everything (Render + Neon)
    python scripts/activate_backups.py --render     # Render only
    python scripts/activate_backups.py --neon       # Neon only

Required secrets (env vars - never commit):
    RENDER_API_KEY      https://dashboard.render.com/account/api-keys
    NEON_API_KEY        https://console.neon.tech -> Account -> API keys

Optional:
    BACKUP_S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY  (offsite S3 retention)
    BACKUP_RETENTION    days of PITR history (default 7; max depends on Neon plan)
    PROJECT_ID, BRANCH_ID    Neon project/branch to back up (auto-detected if omitted)

Endpoints verified against current docs:
    Render  https://api.render.com/v1   (Bearer RENDER_API_KEY)
    Neon    https://console.neon.tech/api/v2 (Bearer NEON_API_KEY)

This cannot be exercised against live accounts here (no API keys), so run --dry-run
first; if an endpoint differs from expectations, the script prints the raw error so you
can adjust (fields evolve).
"""

import json
import os
import sys
from datetime import date

try:
    import requests
except ImportError:
    print("This script needs `requests` (it is in requirements.txt). "
          "Run it with the project venv python:  python scripts/activate_backups.py")
    sys.exit(1)

RENDER_BASE = "https://api.render.com/v1"
NEON_BASE = "https://console.neon.tech/api/v2"

DRY_RUN = "--dry-run" in sys.argv
DO_RENDER = "--render" in sys.argv
DO_NEON = "--neon" in sys.argv
if not DO_RENDER and not DO_NEON:
    DO_RENDER = DO_NEON = True


def log(msg):
    print(("DRY-RUN: " if DRY_RUN else "") + msg)


def call(base, method, path, token, json_body=None):
    url = base + path
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if DRY_RUN:
        log(f"[{method}] {url}  body={json.dumps(json_body) if json_body else ''}")
        return {"dry_run": True}
    resp = requests.request(method, url, headers=headers, json=json_body, timeout=60)
    if resp.status_code >= 400:
        print(f"  ! {method} {url} -> HTTP {resp.status_code}: {resp.text[:800]}",
              file=sys.stderr)
        return None
    try:
        return resp.json()
    except Exception:
        return {"status": resp.status_code}


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def render_token():
    if DRY_RUN:
        return "DRY-RUN"  # not needed - call() only prints in dry-run
    tok = os.environ.get("RENDER_API_KEY")
    if not tok:
        print("RENDER_API_KEY is not set. Create one at "
              "https://dashboard.render.com/account/api-keys and export it.", file=sys.stderr)
    return tok


def activate_render():
    tok = render_token()
    if not tok:
        return False
    if DRY_RUN:
        print("  Render plan - find the 'backup-db' cron job, then:")
        for key in ("BACKUP_S3_BUCKET", "BACKUP_RETENTION",
                    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            print(f"    PUT /services/<cron-id>/env-vars/{key}")
        print("    POST /services/<cron-id>/deploys   (apply env vars)")
        print("    POST /cron-jobs/<cron-id>/runs     (verify one run)")
        return True
    services = call(RENDER_BASE, "GET", "/services", tok)
    if not services:
        return False
    cron = next(
        (s for s in services if s.get("type") == "cron_job" or s.get("name") == "backup-db"),
        None,
    )
    if not cron:
        print("Could not find the backup-db cron job on Render. It is defined in "
              "render.yaml - deploy the repo (autoDeploy) first, then re-run. "
              "Available services: " + ", ".join(s.get("name", "?") for s in services),
              file=sys.stderr)
        return False
    sid = cron["id"]
    log(f"Found backup-db cron on Render: {cron.get('name')} ({sid})")

    # Env vars for the cron. BACKUP_S3_BUCKET + AWS creds enable durable offsite
    # retention (Render cron disks are ephemeral). Secret-type for the AWS key.
    desired = {
        "BACKUP_S3_BUCKET": os.environ.get("BACKUP_S3_BUCKET"),
        "BACKUP_RETENTION": os.environ.get("BACKUP_RETENTION", "7"),
        "AWS_ACCESS_KEY_ID": os.environ.get("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": os.environ.get("AWS_SECRET_ACCESS_KEY"),
    }
    for key, value in desired.items():
        if not value:
            if key == "BACKUP_S3_BUCKET":
                print("  NOTE: BACKUP_S3_BUCKET not set - the nightly dump will only live "
                      "on the cron's ephemeral disk. Set it (+ AWS creds) for durable "
                      "offsite backups.", file=sys.stderr)
            continue
        body = {
            "value": value,
            "envVarType": "secret" if key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY") else "plaintext",
        }
        log(f"Set {key} on backup-db cron")
        call(RENDER_BASE, "PUT", f"/services/{sid}/env-vars/{key}", tok, body)

    log("Deploy backup-db cron (applies env vars)")
    call(RENDER_BASE, "POST", f"/services/{sid}/deploys", tok, {})
    log("Trigger a backup-db run")
    call(RENDER_BASE, "POST", f"/cron-jobs/{sid}/runs", tok, {})
    return True


# ---------------------------------------------------------------------------
# Neon
# ---------------------------------------------------------------------------
def neon_token():
    if DRY_RUN:
        return "DRY-RUN"  # not needed - call() only prints in dry-run
    tok = os.environ.get("NEON_API_KEY")
    if not tok:
        print("NEON_API_KEY is not set. Create one in the Neon console "
              "(Account -> API keys) and export it.", file=sys.stderr)
    return tok


def _neon_target(tok):
    """Return (project_id, branch_id) - from env or auto-detected."""
    projects = call(NEON_BASE, "GET", "/projects", tok)
    if not projects or not projects.get("projects"):
        return None, None
    project = projects["projects"][0]
    if os.environ.get("PROJECT_ID"):
        project = next(
            (p for p in projects["projects"] if p["id"] == os.environ["PROJECT_ID"]),
            project,
        )
    pid = project["id"]
    branches = call(NEON_BASE, "GET", f"/projects/{pid}/branches", tok) or {}
    blist = branches.get("branches", [])
    branch_id = os.environ.get("BRANCH_ID")
    if not branch_id:
        default = next((b for b in blist if b.get("default")), blist[0] if blist else None)
        branch_id = default["id"] if default else None
        print(f"  Auto-detected Neon branch: {default.get('name') if default else '?'} "
              f"({branch_id}). If your production data lives on a different branch "
              f"('dev-django'), set BRANCH_ID explicitly.", file=sys.stderr)
    return pid, branch_id


def activate_neon():
    tok = neon_token()
    if not tok:
        return False
    if DRY_RUN:
        pid = os.environ.get("PROJECT_ID", "<project-id>")
        bid = os.environ.get("BRANCH_ID", "<branch-id>")
        print("  Neon plan:")
        print(f"    PATCH /projects/{pid}  {{'project': {{'history_retention_seconds': "
              f"{int(os.environ.get('BACKUP_RETENTION', '7')) * 86400}}}}}")
        print(f"    POST /projects/{pid}/branches/{bid}/snapshot")
        print(f"    PUT  /projects/{pid}/branches/{bid}/backup_schedule")
        print("  (Set PROJECT_ID/BRANCH_ID env vars for a fully concrete dry-run.)")
        return True
    pid, bid = _neon_target(tok)
    if not pid or not bid:
        print("Could not determine Neon project/branch. Set PROJECT_ID and BRANCH_ID.",
              file=sys.stderr)
        return False
    log(f"Neon target: project={pid} branch={bid}")

    # 1. PITR / history retention window (seconds). 7d=604800, 30d=2592000.
    days = int(os.environ.get("BACKUP_RETENTION", "7"))
    secs = days * 86400
    log(f"Set Neon history retention to {days} day(s) = {secs}s (PITR window)")
    call(NEON_BASE, "PATCH", f"/projects/{pid}", tok,
         {"project": {"history_retention_seconds": secs}})

    # 2. Create a snapshot now (durable point-in-time copy).
    log("Create a Neon snapshot")
    call(NEON_BASE, "POST", f"/projects/{pid}/branches/{bid}/snapshot",
         tok, {"name": f"backup-{date.today().isoformat()}"})

    # 3. Recurring backup schedule (daily) - best-effort; the exact schedule body
    #    can vary by plan. --dry-run shows it; errors print verbatim.
    log("Set Neon backup schedule to daily (best-effort)")
    call(NEON_BASE, "PUT", f"/projects/{pid}/branches/{bid}/backup_schedule",
         tok, {"snapshot_frequency": {"interval": "P1D", "at": "09:00:00"}})
    return True


def main():
    print("manager-tool backup activation")
    print("  dry-run:", "ON" if DRY_RUN else "off", "| render:", DO_RENDER, "| neon:", DO_NEON)
    if DO_RENDER:
        activate_render()
    if DO_NEON:
        activate_neon()
    if DRY_RUN:
        print("\nDry run complete - no changes were made. Re-run without --dry-run to apply.")
    else:
        print("\nDone. Verify: Neon console -> Backups/Snapshots (PITR + snapshot), "
              "Render dashboard -> Cron Jobs -> backup-db (last run).")


if __name__ == "__main__":
    main()

