---
name: ship-pr
description: Open a pull request following the manager-tool house style — rebase onto main, run the venv tests + ruff, push the feature branch, draft the PR body in the Summary/Changes/Test plan format, and open it via the GitHub MCP. Use when the user asks to "ship", "open a PR", "PR this", "ready to merge", or otherwise finalize a piece of work.
---

# ship-pr

The disciplined PR flow for the manager-tool repo. Follow these steps in order. Don't skip the gate, don't push to main, don't merge automatically.

## House conventions

- **Base:** `main`. **Merge style:** squash. (Squash subject = PR title.)
- **Branch:** `claude/<topic-slug>` (kebab-case, ≤40 chars). If a designated branch is pinned by the harness, use that.
- **Pre-push gate (non-negotiable):** Django tests green + ruff clean on touched files.
- **PR body:** Summary → Changes → Test plan. The session-link footer is appended automatically by the harness — don't add it twice.
- **Never push to `main`.** Never `--no-verify`, never skip the gate to chase a green CI.

## Steps

### 1. Sync the branch

If a merged branch was just deleted (the common case after a squash merge):

```bash
git fetch origin --prune
git checkout -B claude/<topic-slug>
git reset --hard origin/main
```

If the branch exists with unmerged work, rebase instead:

```bash
git fetch origin
git rebase origin/main
```

### 2. Run the gate

```bash
cd manager-tool-django
.venv/bin/pytest -q
.venv/bin/ruff check <touched files or paths>
```

The SessionStart hook (`.claude/hooks/session-start.sh`) creates `.venv` on web sessions. If it doesn't exist for any reason:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

**If either command fails, fix the code. Do not push.**

### 3. Push

```bash
git push -u origin <branch-name>
```

On network failure, retry up to 4 times with exponential backoff (2s → 4s → 8s → 16s). Don't bypass hooks or sign-off requirements.

### 4. Open the PR

Load the GitHub MCP tool if its schema isn't already available:

```
ToolSearch select:mcp__github__create_pull_request,mcp__github__pull_request_read
```

Then call `mcp__github__create_pull_request` with:

- `owner: toddaerickson`, `repo: manager-tool`
- `base: main`, `head: <branch>`, `draft: false`
- `title`: ≤70 chars, imperative voice ("Add X", "Fix Y", "Decommission Z")
- `body`: the template below

#### PR body template

```markdown
## Summary

<1–3 sentences. Why this exists.>

## Changes

- <bullet per logical change, with file paths when useful>

## Test plan

- [x] `pytest` — N passed
- [x] `ruff check` clean on touched files
- [ ] <any post-merge verification the user owns, if applicable>
```

Do NOT manually append the session link — the harness adds it.

### 5. Confirm CI started (once)

Right after creating the PR, fetch the checks **once**:

```
mcp__github__pull_request_read method=get_check_runs
```

Report the status (queued/in-progress/success) to the user in one sentence. **Do not poll.** The webhook subscription delivers follow-up events; react to those, not to a sleep loop.

### 6. After merge

When the merge webhook arrives:

```bash
git fetch origin --prune
git checkout main || true
git reset --hard origin/main
```

Confirm to the user. Don't reopen the merged PR, don't open a duplicate.

## Doc-bump (do this alongside the PR when a user-visible feature ships)

For any feature visible to the user, update the project state docs in the same PR:

- `MIGRATION_STATUS.md` — add a bullet to "Shipped since cutover", trim from "Remaining v2 gaps" if applicable, bump "Last updated".
- `README.md` — add the feature to the appropriate "Core Features" section (or the Deployment section for ops changes like `/health`).
- `PHASE_GATES.md` / `MIGRATION_PLAN.md` — only touch if the change closes a phase gate.

Skip the doc-bump for pure refactors, test-only changes, and CI fixes.

## Edge cases

- **Force-push:** Only with `--force-with-lease`, and only if you confirmed the remote ref. After a squash merge the remote branch is usually gone — a fresh `git push -u` recreates it (no force needed).
- **Sandbox blocks branch deletion (HTTP 403):** Report it to the user with the branch name; they'll delete in the GitHub UI. Don't keep retrying.
- **Stale `mergeable_state`:** Refetch the PR with `method=get` before merging. Head SHA must match the latest commit you pushed.
- **Mixed-scope PR:** If the work spans two unrelated changes, prefer two PRs over one grab-bag. If pinned to a single branch, broaden the title and split the body into numbered sections.

## What this skill does NOT do

- **Doesn't merge** — the user controls merge timing.
- **Doesn't pick the task** — the user defines the work.
- **Doesn't poll CI** — webhooks drive follow-up.
- **Doesn't comment on PRs** unless a real reply is needed (terse, only when necessary).
