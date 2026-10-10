"""Lint render.yaml for the two bug classes that have bitten us.

1. Field names (PR #71). Render's schema uses `startCommand:` on every
   service type (web / cron / worker). Using `command:` instead is
   silently rejected by Render's sync — the deploy looks fine, but the
   affected service is never created. PR #71 shipped this on the two
   cron services; PR #107 finally caught it after ~3 weeks.

2. Declared env that cannot boot the app (the `backup-db` cron). Render
   prompts for `sync: false` values only when a Blueprint is first
   created, so a service added later starts with only the keys someone
   remembered to paste. `backup-db` (2026-08-10) declared
   MANAGER_TOOL_ENV=prod but not CONFIG_ENCRYPTION_KEY /
   DJANGO_SECRET_KEY, so with that env settings.py refuses to import
   and the run exits 1 before reaching its own code. The boot check runs each
   service's Django entry point with exactly its declared keys
   (placeholder values for secrets) and fails on a non-zero exit.

Render's own `render blueprints validate` would catch (1), but it
requires an authenticated workspace (i.e. a Render API key as a
GitHub Actions secret). Heavy for a preventive check. This script is
narrower — just the bugs we already shipped — and offline. The boot
check needs the app's requirements installed in the running
interpreter.

Exit 0 on pass, 1 on issues. Run from repo root via CI or by hand.
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml


# Per-type required field names. The render spec has many more, but
# these are the ones whose absence indicates the bug class we care
# about. Extend if a future incident points at a different field.
REQUIRED = {
    "web":    {"startCommand"},
    "cron":   {"startCommand", "schedule"},
    "worker": {"startCommand"},
}

# Field names that are common typos / wrong schema. Render silently
# rejects the whole blueprint if any service has these.
FORBIDDEN = {"command", "run"}

# Stand-ins for `sync: false` / `generateValue` keys during the boot
# check. Nothing connects anywhere: `check` and `--help` never open the
# DB. Every other secret gets the generic placeholder.
PLACEHOLDERS = {
    "DATABASE_URL": "postgres://user:pass@db.invalid:5432/placeholder",
    # A fake non-empty DSN makes sentry_sdk.init() raise; empty is how
    # settings already spells "no Sentry".
    "SENTRY_DSN": "",
}
GENERIC_PLACEHOLDER = "placeholder"

MANAGE_PY_COMMAND = re.compile(r"^python manage\.py (\S+)")


def lint(blueprint: dict) -> list[str]:
    issues = []
    for i, svc in enumerate(blueprint.get("services") or []):
        name = svc.get("name") or f"services[{i}]"
        svc_type = svc.get("type")

        for missing in sorted(REQUIRED.get(svc_type, set()) - set(svc)):
            issues.append(f"{name} ({svc_type}): missing required field `{missing}`")

        for bad in sorted(FORBIDDEN & set(svc)):
            hint = " — use `startCommand`" if bad == "command" else ""
            issues.append(f"{name}: forbidden field `{bad}`{hint}")

    return issues


def declared_env(svc: dict) -> tuple[dict, list[str]]:
    """The env a service gets from render.yaml alone, plus anything the
    boot check cannot see (an env group's keys live in the dashboard)."""
    env, unseen = {}, []
    for var in svc.get("envVars") or []:
        key = var.get("key")
        if not key:
            unseen.append(str(var))
        elif "value" in var:
            env[key] = str(var["value"])
        else:
            env[key] = PLACEHOLDERS.get(key, GENERIC_PLACEHOLDER)
    return env, unseen


def boot_argv(svc: dict) -> list[str]:
    """A cron runs one management command: `<cmd> --help` imports
    settings, runs django.setup() and loads that command, so a settings
    crash and an unknown command both exit non-zero. Anything else (the
    gunicorn web service) gets `check`."""
    match = MANAGE_PY_COMMAND.match((svc.get("startCommand") or "").strip())
    return [match.group(1), "--help"] if match else ["check"]


def boot_issues(blueprint: dict, repo_root: Path) -> list[str]:
    if importlib.util.find_spec("django") is None:
        return [
            "boot check: Django is not importable — run with the app's "
            "requirements installed (pip install -r manager-tool-django/requirements.txt)"
        ]
    issues, booted = [], 0
    for i, svc in enumerate(blueprint.get("services") or []):
        name = svc.get("name") or f"services[{i}]"
        app_dir = repo_root / (svc.get("rootDir") or ".")
        if not (app_dir / "manage.py").is_file():
            continue
        env, unseen = declared_env(svc)
        if unseen:
            issues.append(
                f"{name}: boot check cannot see env entries without a `key` "
                f"(e.g. fromGroup): {', '.join(unseen)}"
            )
            continue
        argv = boot_argv(svc)
        proc = subprocess.run(
            [sys.executable, "manage.py", *argv],
            cwd=app_dir,
            env={"PATH": os.environ.get("PATH", ""), "MT_IGNORE_DOTENV": "1", **env},
            capture_output=True,
            text=True,
            timeout=120,
        )
        booted += 1
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
            issues.append(
                f"{name}: `manage.py {' '.join(argv)}` exits {proc.returncode} with only "
                f"its declared env ({', '.join(sorted(env))}):\n      "
                + "\n      ".join(tail)
            )
    if booted == 0:
        issues.append("boot check: no service has a manage.py under its rootDir — nothing was checked")
    return issues


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "render.yaml")
    blueprint = yaml.safe_load(path.read_text()) or {}
    issues = lint(blueprint) + boot_issues(blueprint, path.resolve().parent)
    if not issues:
        print(f"{path}: OK")
        return 0
    print(f"{path}: {len(issues)} issue(s):", file=sys.stderr)
    for msg in issues:
        print(f"  - {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
