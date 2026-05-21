#!/usr/bin/env bash
# pr-cycle.sh — semi-automated per-PR loop for Phase 5 page ports.
#
# What it automates: branch creation, commit, push, PR open via gh, CI watch,
# branch cleanup after merge.
# What it does NOT automate: the merge click. You eyeball the diff and CI
# before merging — this codebase has shipped four PG-only bugs that CI did
# not catch, and the audit cadence depends on human-in-the-loop review.
#
# Usage:
#   scripts/pr-cycle.sh "title of the PR" [--body "body text"] [--base main]
#
# Workflow:
#   1. Creates a branch from current HEAD if not already on a feature branch.
#   2. Commits all staged + unstaged changes with the title as the message.
#   3. Pushes with -u.
#   4. Opens a PR via gh with a templated body (or your --body).
#   5. Watches CI checks and prints status.
#   6. Opens the PR diff in your browser when CI passes.
#   7. Waits for you to merge via the gh UI, then deletes the local + remote branch.

set -euo pipefail

if ! command -v gh >/dev/null 2>&1; then
    echo "error: gh (GitHub CLI) is required. Install: https://cli.github.com/" >&2
    exit 1
fi

if [[ $# -lt 1 ]]; then
    echo "usage: $0 \"PR title\" [--body \"body\"] [--base main]" >&2
    exit 2
fi

TITLE="$1"; shift
BODY=""
BASE="main"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --body) BODY="$2"; shift 2 ;;
        --base) BASE="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" == "$BASE" ]]; then
    SLUG="$(echo "$TITLE" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g' | cut -c1-50)"
    BRANCH="port/$SLUG"
    echo "→ creating branch $BRANCH from $BASE"
    git checkout -b "$BRANCH"
else
    BRANCH="$CURRENT_BRANCH"
    echo "→ already on $BRANCH (not creating new branch)"
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "→ committing changes"
    git add -A
    git commit -m "$(cat <<EOF
$TITLE

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
else
    echo "→ no uncommitted changes; skipping commit"
fi

echo "→ pushing $BRANCH"
git push -u origin "$BRANCH"

if [[ -z "$BODY" ]]; then
    BODY="$(cat <<'EOF'
## Summary
<one or two bullets describing the change>

## Migration phase
Phase 5 — page port (see MIGRATION_PLAN.md and PHASE_GATES.md)

## Phase 5 gate checklist
- [ ] Visual parity confirmed (screenshot below or in commit)
- [ ] All ported tests pass under pytest
- [ ] smoke_pg_django.py extended if this PR added an aggregator
- [ ] PG smoke CI green
- [ ] Page works on Render dev deploy (not just localhost)
- [ ] No surviving BETWEEN / startswith( / [:10] on date columns from the Streamlit port

## Test plan
- [ ] Click through the page on the deployed dev URL
- [ ] Verify per-tenant scoping (log in as a different manager, confirm zero rows)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
fi

echo "→ opening PR against $BASE"
PR_URL="$(gh pr create --base "$BASE" --title "$TITLE" --body "$BODY")"
echo "→ PR opened: $PR_URL"

echo "→ watching CI (this blocks until checks complete)"
if gh pr checks --watch; then
    echo "→ CI green"
else
    echo "→ CI red. Investigate before merging. Exiting." >&2
    echo "   PR: $PR_URL" >&2
    exit 3
fi

echo "→ opening PR in browser for review and merge"
gh pr view --web

echo
echo "Review the diff, then merge via the gh web UI or run:"
echo "  gh pr merge $PR_URL --squash --delete-branch"
echo
read -r -p "Press ENTER once the PR is merged (or Ctrl-C to abort cleanup)... "

echo "→ syncing local $BASE and pruning"
git checkout "$BASE"
git pull --ff-only origin "$BASE"
if git show-ref --quiet "refs/heads/$BRANCH"; then
    git branch -d "$BRANCH" 2>/dev/null || git branch -D "$BRANCH"
fi
git remote prune origin

echo "→ done. you are on $BASE."
