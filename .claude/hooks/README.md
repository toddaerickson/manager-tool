# `.claude/hooks/`

Project hooks for this repo (wired in `.claude/settings.json`).

| Hook | Event | What it does |
|------|-------|--------------|
| `session-start.sh` | SessionStart | Remote-env only: builds `manager-tool-django/.venv` so tests/lint work immediately. |
| `session-worktree-notice.sh` | SessionStart | Prints a heads-up (and lists active worktrees) when you're in the primary tree, so the guard's first block isn't a surprise. Silent in a worktree or when the solo hatch is set. |
| `guard-shared-worktree.py` | PreToolUse (`Edit\|Write\|MultiEdit\|NotebookEdit`, `Bash`) | Requires an isolated git worktree for mutations. See below. |

## Worktree isolation guard

Todd sometimes runs **multiple concurrent Claude Code sessions on this one clone**.
Two sessions sharing one working tree collide: on 2026-07-05 a parallel session ran
`git reset` / `checkout` / `branch -D` in the shared tree and **deleted another
session's branch and reverted its edits mid-commit**. (See memory
`feedback_parallel_agent_git_isolation`.)

`guard-shared-worktree.py` prevents that by **denying mutations in the PRIMARY
working tree of this project's clone** and requiring a linked worktree instead. It is
race-free: it decides per-action based on *which tree the action targets*, with no
lock files or session detection. It is a **segment-based** evaluator — it tracks `cwd`
across `cd`/`pushd`/`env -C` and judges each `git` segment by its own target, so
`cd <primary> && git reset --hard` is caught (not laundered through a worktree cwd),
and it keys on the real command word so a body that merely *quotes* "git checkout"
(e.g. `gh pr create --body "…git checkout…"`) is not misread.

### What is blocked (only in THIS clone's primary tree)

- File edits: `Edit`, `Write`, `MultiEdit`, `NotebookEdit`.
- History/index mutations: `commit`, `merge`, `rebase`, `cherry-pick`, `apply`, `am`,
  `revert`, `update-ref`, `update-index`, `gc`.
- Branch ref ops: `checkout -b` / `switch -c`, branch switches (`git switch <b>`,
  `git checkout <b>`), and branch **delete/rename/force** (`git branch -d/-D/-m/-M/-f`).
- Reset (ref/tree moving): `git reset [--soft/--mixed/--hard/--merge/--keep] [<commit>]`.
- Working-tree clobbering: `clean -f`, `worktree remove`, whole-tree `checkout .` /
  `restore .`, and worktree-touching `git stash`.

### Never blocked

Reads, non-git Bash, read-only git (`status`/`log`/`diff`/`fetch`/`branch`/`stash list`),
targeted file restores (`git checkout <ref> -- <path>`, `git restore --staged .`),
bare `git reset` / `git reset -- <path>` (index-only unstage), `git branch <name>`
(create), **`git worktree add`**, edits/mutations in a linked worktree of this clone,
and anything in a **different repo** (e.g. `~/.claude-config`) or outside any repo
(`/tmp`). `MT_SOLO=1` or `.git/mt-solo` bypasses everything.

### Not defended against (by design)

Adversarial evasion — `bash -c '…'`, `eval`, writing a script then running it,
base64/obfuscation. This guard stops *accidental* collisions from well-meaning
sessions, not a determined bypass (no shell-command guard can promise the latter).

### How to work when blocked

```bash
git worktree add .claude/worktrees/<slug> -b <branch> origin/main
# then edit files under that path (absolute paths), or `cd` into it.
# when done + merged:
git worktree remove .claude/worktrees/<slug>
```
`.claude/worktrees/` is already gitignored (via `.claude/*`), so worktrees there
never pollute the primary tree's status.

### Deliberate solo work on the primary clone (escape hatches)

- Per-clone (persists): `touch "$(git rev-parse --git-dir)/mt-solo"`
  (remove the file to re-enable the guard).
- Per-session: launch the session with `MT_SOLO=1`.
