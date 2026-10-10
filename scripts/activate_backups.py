#!/usr/bin/env python3
"""Activate Neon's managed backups: PITR history window + snapshots.

The nightly logical dump is NOT configured here — it is
.github/workflows/backup.yml and needs only repo secrets (see BACKUPS.md).
This script once also configured a Render `backup-db` cron; that cron was
retired as a weaker duplicate of the workflow.

Usage (run with the project venv python so `requests` is available):

    python scripts/activate_backups.py --dry-run    # show what would be done (no changes)
    python scripts/activate_backups.py              # apply it

Required secret (env var - never commit):
    NEON_API_KEY        https://console.neon.tech -> Account -> API keys

Optional:
    BACKUP_RETENTION    days of PITR history (default 7; max depends on Neon plan)
    PROJECT_ID, BRANCH_ID    Neon project/branch to back up (auto-detected if omitted)

Endpoint:
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

NEON_BASE = "https://console.neon.tech/api/v2"

DRY_RUN = "--dry-run" in sys.argv


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
              f"({branch_id}). Production data lives on the `production` (default) "
              f"branch; set BRANCH_ID if that ever changes.", file=sys.stderr)
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
    print("manager-tool backup activation (Neon)")
    print("  dry-run:", "ON" if DRY_RUN else "off")
    activate_neon()
    if DRY_RUN:
        print("\nDry run complete - no changes were made. Re-run without --dry-run to apply.")
    else:
        print("\nDone. Verify: Neon console -> Backups/Snapshots (PITR + snapshot).")


if __name__ == "__main__":
    main()

