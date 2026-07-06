#!/usr/bin/env python3
"""PreToolUse guard: REQUIRE an isolated git worktree for mutations in the PRIMARY
working tree of THIS project's shared clone.

Todd sometimes runs multiple concurrent Claude Code sessions on this one clone. Two
sessions sharing one working tree collide: on 2026-07-05 a parallel session ran
`git reset` / `checkout` / `branch -D` in the shared tree and deleted another
session's branch + reverted its edits mid-commit (memory:
feedback_parallel_agent_git_isolation).

This guard DENIES file mutations (Edit/Write/MultiEdit/NotebookEdit) and git-mutating
Bash commands that would land in the PRIMARY working tree of THIS clone, and tells you
to isolate:

    git worktree add .claude/worktrees/<slug> -b <branch> origin/main

Mutations inside a linked worktree of this clone are ALLOWED. Mutations to OTHER repos,
reads, non-git Bash, read-only git, and edits outside any repo are ALLOWED — the guard
only protects this clone's primary tree.

Design (v3 — hardened over two adversarial-review rounds):
  * Heredoc bodies (`… <<EOF … EOF`) are stripped BEFORE parsing, so command examples
    inside a script/PR body written via `cat <<EOF` aren't misread as real commands.
  * Bash is split into segments; cwd is tracked with a proper `cd`/`pushd`/`popd`
    STACK, so `pushd <wt> && … && popd && git reset` is judged against the restored
    primary cwd, and `cd <primary> && git reset` against the primary tree.
  * Only a segment whose actual COMMAND WORD is `git` is treated as git — a body that
    merely QUOTES "git checkout" (`gh pr create --body "…git checkout…"`) is ignored.
  * Each git segment is judged against ITS OWN `git -C` target (all leading globals,
    incl. `-c k=v`, parsed) else the tracked cwd; a read-only `git -C <wt>` decoy can't
    launder a later primary mutation, and `-c k=v -C <primary>` can't hide the target.
  * A working-tree restore (`checkout … -- <file>`, `restore <file>`) is treated as a
    mutation — it overwrites shared-tree file content. Only index-only ops
    (`restore --staged`, bare `reset`, `reset -- <path>`) are allowed.
  * Scoped to THIS clone via $CLAUDE_PROJECT_DIR — other repos are never guarded.
  * A git mutation whose target can't be resolved (unexpanded $VAR / missing dir)
    FAILS CLOSED (deny).

Not defended against (ADVERSARIAL, not the accidental collisions this targets):
`bash -c '…'`, `eval`, command substitution `$(cd … && git …)`, brace/subshell groups
that hide a `cd`, line-continuation splits, writing then running a script,
base64/obfuscation, and any non-Bash/Edit/Write/MultiEdit/NotebookEdit tool (e.g. an
MCP git tool). A well-meaning session doesn't do those; no shell-command guard can
stop a determined bypass — the robust half is the file-path check + the tracked-cwd/-C
judgement of ordinary `cd`/`pushd`/`env`/`&&`/`;`/`|` forms.

Escape hatches for DELIBERATE solo work on the primary clone:
  * env  MT_SOLO=1    (per-session), or
  * file .git/mt-solo (per-clone — `touch "$(git rev-parse --git-dir)/mt-solo"`).

Pure-stdlib. Every non-firing path exits 0. Wired in .claude/settings.json as a
PreToolUse hook on Edit|Write|MultiEdit|NotebookEdit and on Bash.
"""
import json
import os
import re
import subprocess
import sys

FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SEG_SPLIT = re.compile(r"&&|\|\||;|\||\n|\(|\)")
HEREDOC = re.compile(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")
GLOBAL_TAKES_ARG = ("-C", "-c", "--git-dir", "--work-tree", "--namespace",
                    "--exec-path", "--super-prefix")


def git(*args):
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""


def allow():
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def nearest_existing_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    return d or "."


def _resolve_dir(tok, base):
    """Resolve a cd/env/-C target against `base`. Absolute existing dir, or None if
    unresolvable (shell var, ~, missing dir) — callers fail closed."""
    if not tok or tok[0] in "$`~" or "$" in tok or "`" in tok:
        return None
    b = base if (base and os.path.isdir(base)) else None
    p = tok if os.path.isabs(tok) else (os.path.join(b, tok) if b else None)
    if p is None:
        return None
    p = os.path.abspath(p)
    return p if os.path.isdir(p) else None


def _clone_primary_gitdir(path):
    """Abs git dir of the PRIMARY tree of `path`'s clone (a worktree's gitdir maps back
    to the shared .git). '' if not a repo. Identifies 'this clone'."""
    gd = git("-C", path or ".", "rev-parse", "--absolute-git-dir")
    if not gd:
        return ""
    m = re.match(r"^(.*)/worktrees/[^/]+/?$", gd)
    return os.path.abspath(m.group(1) if m else gd)


def _target_guarded(target, project_clone):
    if target is None:
        return False
    gd = git("-C", target, "rev-parse", "--absolute-git-dir")
    if not gd:
        return False                                   # outside any git repo
    if "/worktrees/" in gd:
        return False                                   # a linked worktree
    if project_clone and os.path.abspath(gd) != project_clone:
        return False                                   # a different clone
    if os.path.exists(os.path.join(gd, "mt-solo")):
        return False                                   # per-clone solo opt-out
    return True


def _strip_heredocs(cmd):
    """Drop heredoc BODY + closing delimiter lines, keeping the line bearing `<<WORD`
    (which holds the real command). Prevents body text like `git reset` from being
    parsed as a command."""
    if "<<" not in cmd:
        return cmd
    lines = cmd.split("\n")
    out, i = [], 0
    while i < len(lines):
        out.append(lines[i])
        m = HEREDOC.search(lines[i])
        i += 1
        if m:
            delim = m.group(1)
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            i += 1                                     # skip the closing delimiter line
    return "\n".join(out)


# ---- git mutation classification (subcommand + its args) ----

def _split_git(gitargs):
    """Skip git global options -> (subcommand, subargs)."""
    i = 0
    while i < len(gitargs):
        t = gitargs[i]
        if t in GLOBAL_TAKES_ARG:
            i += 2
            continue
        if t.startswith("--") and "=" in t:
            i += 1
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t, gitargs[i + 1:]
    return "", []


def _reset_mut(args):
    if "--help" in args or "-h" in args:
        return False
    if any(a in ("--soft", "--mixed", "--hard", "--merge", "--keep") for a in args):
        return True                                    # explicit mode = ref/tree move
    if "--" in args:
        return False                                   # path-scoped unstage
    return bool([a for a in args if not a.startswith("-")])  # 'reset <commit>' ref move; bare=unstage


def _restore_mut(args):
    if "--help" in args or "-h" in args:
        return False
    staged = "--staged" in args or "-S" in args
    worktree = "--worktree" in args or "-W" in args
    return worktree or not staged                      # --staged-only = index unstage (safe)


def _branch_mut(args):
    return any(a in ("-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force")
               for a in args)                          # delete/rename/force (not list/create)


def _is_mutation(sub, args):
    if sub in ("commit", "merge", "rebase", "cherry-pick", "am", "revert", "apply",
               "update-ref", "update-index", "gc"):
        return True
    if sub in ("checkout", "switch"):
        return not ("--help" in args or "-h" in args)  # switch/checkout are always tree/ref ops
    if sub == "restore":
        return _restore_mut(args)
    if sub == "reset":
        return _reset_mut(args)
    if sub == "branch":
        return _branch_mut(args)
    if sub == "clean":
        return any(a.startswith("-") and "f" in a for a in args)
    if sub == "stash":
        return not (args and args[0] in ("list", "show"))
    if sub == "reflog":
        return bool(args) and args[0] in ("delete", "expire")
    if sub == "worktree":
        return bool(args) and args[0] in ("remove", "prune", "move")
    return False


def _git_c(gitargs, base):
    """Resolve `git -C <path>` / `--git-dir=<path>` on this invocation, correctly
    skipping value-consuming globals like `-c k=v`. '' if none, None if unresolvable."""
    i = 0
    while i < len(gitargs):
        t = gitargs[i]
        if t == "-C" and i + 1 < len(gitargs):
            return _resolve_dir(gitargs[i + 1].strip("\"'"), base)
        if t.startswith("--git-dir="):
            gd = t[len("--git-dir="):].strip("\"'")
            return _resolve_dir(os.path.dirname(gd) or gd, base)
        if t in GLOBAL_TAKES_ARG:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        break                                          # reached the subcommand
    return ""


def _env_chdir(toks):
    """For a segment starting with `env`, return (cmd_word_index, chdir_target|'' ,
    unresolved). Handles env -C <p> / --chdir[=]<p> / VAR=val / flags."""
    i, target, un = 1, "", False
    while i < len(toks):
        t = toks[i]
        if t == "-C" and i + 1 < len(toks):
            d = _resolve_dir(toks[i + 1].strip("\"'"), None)
            target, un = (d or ""), (d is None)
            i += 2
            continue
        if t.startswith("--chdir="):
            d = _resolve_dir(t.split("=", 1)[1].strip("\"'"), None)
            target, un = (d or ""), (d is None)
            i += 1
            continue
        if t == "--chdir" and i + 1 < len(toks):
            d = _resolve_dir(toks[i + 1].strip("\"'"), None)
            target, un = (d or ""), (d is None)
            i += 2
            continue
        if t.startswith("-") or "=" in t:
            i += 1
            continue
        break
    return i, target, un


def _reason(target):
    branch = git("-C", target, "branch", "--show-current") or "(detached HEAD)"
    return (
        f"BLOCKED: this mutation targets the PRIMARY working tree of the shared clone "
        f"(branch '{branch}'). Concurrent Claude sessions share it; mutating here risks a "
        f"branch-switch/reset collision (memory: feedback_parallel_agent_git_isolation). "
        f"Isolate first:\n"
        f"  git worktree add .claude/worktrees/<slug> -b <branch> origin/main\n"
        f"then work there (cd into it, or `git -C <path>`). Deliberately solo on the "
        f"primary clone? Re-launch with MT_SOLO=1, or `touch \"$(git rev-parse --git-dir)/mt-solo\"`."
    )


UNRESOLVED = "\x00UNRESOLVED"


def _eval_bash(cmd, session_cwd, project_clone):
    """Deny reason, UNRESOLVED sentinel, or None (allow)."""
    cmd = _strip_heredocs(cmd)
    cwd = session_cwd if (session_cwd and os.path.isdir(session_cwd)) else "."
    stack = []
    for raw in SEG_SPLIT.split(cmd):
        toks = raw.split()
        if not toks:
            continue
        w = toks[0]
        if w == "cd":
            cwd = _resolve_dir(toks[1].strip("\"'"), cwd) if len(toks) > 1 else None
            continue
        if w == "pushd":
            stack.append(cwd)
            cwd = _resolve_dir(toks[1].strip("\"'"), cwd) if len(toks) > 1 else None
            continue
        if w == "popd":
            cwd = stack.pop() if stack else cwd
            continue
        seg_cwd, i = cwd, 0
        if w == "env":
            i, chd, un = _env_chdir(toks)
            seg_cwd = None if un else (chd or cwd)
        if i >= len(toks) or toks[i] != "git":
            continue                                   # command word isn't git
        gitargs = toks[i + 1:]
        sub, subargs = _split_git(gitargs)
        if not _is_mutation(sub, subargs):
            continue
        c = _git_c(gitargs, cwd)
        target = None if c is None else (c or seg_cwd)
        if target is None:
            return UNRESOLVED
        if _target_guarded(target, project_clone):
            return _reason(target)
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        allow()
    if not isinstance(data, dict):
        allow()

    if os.environ.get("MT_SOLO"):
        allow()

    tool = data.get("tool_name") or ""
    tin = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    session_cwd = data.get("cwd")
    session_cwd = session_cwd if isinstance(session_cwd, str) else "."
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or session_cwd
    project_clone = _clone_primary_gitdir(project_dir)

    if tool in FILE_TOOLS:
        fp = tin.get("file_path") or tin.get("notebook_path") or ""
        if not isinstance(fp, str) or not fp:
            allow()
        probe = nearest_existing_dir(fp)
        if _target_guarded(probe, project_clone):
            deny(_reason(probe))
        allow()

    if tool == "Bash":
        cmd = tin.get("command")
        if not isinstance(cmd, str) or not cmd:
            allow()
        verdict = _eval_bash(cmd, session_cwd, project_clone)
        if verdict is UNRESOLVED:
            deny(
                "BLOCKED: a git-mutating command relocates (cd/pushd/env) to a path this "
                "guard can't resolve, so it can't prove the target isn't the shared primary "
                "tree. Run it from inside the worktree, or use `git -C <literal worktree "
                "path>`. Deliberately solo? MT_SOLO=1 or "
                "`touch \"$(git rev-parse --git-dir)/mt-solo\"`."
            )
        if verdict:
            deny(verdict)
        allow()

    allow()


if __name__ == "__main__":
    main()
