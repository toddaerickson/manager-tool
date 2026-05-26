"""Lint render.yaml for the field-name bug class that bit us in PR #71.

Render's schema uses `startCommand:` on every service type (web / cron
/ worker). Using `command:` instead is silently rejected by Render's
sync — the deploy looks fine, but the affected service is never
created. PR #71 shipped this on the two cron services; PR #107 finally
caught it after ~3 weeks.

Render's own `render blueprints validate` would catch this, but it
requires an authenticated workspace (i.e. a Render API key as a
GitHub Actions secret). Heavy for a preventive check. This script is
narrower — just the bug we already shipped — and offline.

Exit 0 on pass, 1 on issues. Run from repo root via CI or by hand.
"""

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


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "render.yaml")
    issues = lint(yaml.safe_load(path.read_text()) or {})
    if not issues:
        print(f"{path}: OK")
        return 0
    print(f"{path}: {len(issues)} issue(s):", file=sys.stderr)
    for msg in issues:
        print(f"  - {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
