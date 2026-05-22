#!/bin/bash
set -euo pipefail

# SessionStart hook: prepare the Django app's virtualenv so tests and the
# linter work immediately in Claude Code on the web. Only runs in the remote
# environment; local checkouts manage their own env.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR/manager-tool-django"

# Create the venv on first boot; reuse it afterwards. Idempotent — the
# container state is cached after the hook completes, so reinstalls are cheap.
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

if ! .venv/bin/pip install -q -r requirements.txt; then
  echo "session-start: pip install failed for manager-tool-django/requirements.txt" >&2
  exit 1
fi

echo "session-start: manager-tool-django/.venv ready — run tests with 'cd manager-tool-django && .venv/bin/pytest', lint with '.venv/bin/ruff check'"
