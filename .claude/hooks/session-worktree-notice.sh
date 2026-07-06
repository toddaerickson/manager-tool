#!/bin/bash
# SessionStart notice (companion to guard-shared-worktree.py).
#
# One concise line, only when it matters: this session is in the PRIMARY tree of the
# shared clone AND another linked worktree exists (a real concurrency signal) AND no
# solo opt-out is set. Otherwise silent. Never fails the session — every path exits 0.
set -uo pipefail

gitdir=$(git rev-parse --absolute-git-dir 2>/dev/null) || exit 0
case "$gitdir" in
  */worktrees/*) exit 0 ;;              # already isolated — nothing to warn about
esac
[ -e "$gitdir/mt-solo" ] && exit 0      # per-clone solo opt-out
[ -n "${MT_SOLO:-}" ] && exit 0         # per-session solo opt-out

# Count OTHER linked worktrees (concurrency signal). git worktree list line 1 is the
# primary tree; any further lines are linked worktrees.
n=$(git worktree list 2>/dev/null | tail -n +2 | grep -c . || true)
if [ "${n:-0}" -gt 0 ]; then
  echo "NOTE: $n other git worktree(s) exist on this shared clone (possible concurrent Claude session). You're in the PRIMARY tree — the guard BLOCKS repo edits and git branch/commit/reset/checkout here. Isolate: 'git worktree add .claude/worktrees/<slug> -b <branch> origin/main'. Solo: MT_SOLO=1 or touch \"\$(git rev-parse --git-dir)/mt-solo\"."
fi
exit 0
