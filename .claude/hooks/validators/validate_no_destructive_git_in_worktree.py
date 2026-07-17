#!/usr/bin/env python3
"""
Guard (issue #2137): block destructive git commands issued from inside a DIRTY
session worktree (`.worktrees/{slug}/`), where they would silently destroy
uncommitted work with no recovery path.

Background: a production incident destroyed six uncommitted files in a
`session/dev-XXXX` worktree — the reflog showed `reset: moving to HEAD`,
matching an agent-issued `git reset --hard`/`git stash` during a confused
post-interruption recovery. The unmerged-branch guard (#1646) protects only
*committed* work; nothing stopped an agent from hard-resetting a dirty tree.
This PreToolUse hook is the agent-facing half of the #2137 backstop (the other
half is `preserve_uncommitted_worktree_changes` in `agent/worktree_manager.py`,
which auto-WIP-commits before teardown).

Blocked signatures (only when the tree is DIRTY and cwd is inside `.worktrees/`):
  - `git reset --hard [<ref>]`
  - `git clean -f[dx...]` / `git clean --force`
  - `git checkout -- .` / `git checkout .`
  - `git restore .`
  - bare `git stash` / `git stash push` with NO pathspec

Explicitly ALLOWED (out of scope — see the plan Rabbit Holes):
  - the same commands on a CLEAN tree (a reset on a clean tree loses nothing)
  - the same commands OUTSIDE `.worktrees/`
  - a specific-path variant: `git checkout -- file.py`, `git stash push -- file`
  - `git reset --soft`, `git stash list/pop/apply`, etc.
  - any command carrying the inline override token `# allow-destructive-git`

Detection is anchored to the *command position*, not a bare substring search:
`git commit -m "reset --hard bug"` must NOT be blocked. The command is split on
shell control operators into simple commands, each tokenized with `shlex`, and
only a simple command whose first non-env token is `git` and whose subcommand
matches a destructive signature counts. A `cd <path> && git reset --hard` chain
resolves the effective directory from the `cd` prefix (mirrors
`validate_no_uv_sync_in_worktree.py`).

Claude Code hook protocol:
- Stdin: JSON with tool_name, tool_input, cwd
- To BLOCK: print {"decision": "block", "reason": "..."} to stdout, exit 0
- To ALLOW: print nothing, exit 0

Fail-open: any parse error, git error, or unexpected exception results in
exit 0 (allow) — this guard must never crash a legitimate Bash call.

Direct/manual invocation (also used by tests):
  python validate_no_destructive_git_in_worktree.py <command> <cwd>
Exits 1 with a message on stderr if the command would be blocked (dirty tree
assumed for the CLI path), 0 otherwise.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

_CONTROL_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

OVERRIDE_TOKEN = "# allow-destructive-git"


def _split_simple_commands(command: str) -> list[str]:
    """Split a shell command on control operators into simple commands.

    Not a full shell parser (see the plan Rabbit Holes) — handles the common
    `a && b`, `a; b`, `a | b` shapes and otherwise treats the whole string as
    one simple command.
    """
    return [s.strip() for s in _CONTROL_SPLIT_RE.split(command) if s.strip()]


def _git_tokens(simple_cmd: str) -> list[str] | None:
    """Return the token list starting at `git` if `simple_cmd` is a git
    invocation (command-position match, skipping leading env assignments),
    else None.
    """
    try:
        tokens = shlex.split(simple_cmd)
    except ValueError:
        return None
    i = 0
    while i < len(tokens) and _ENV_ASSIGNMENT_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens) or tokens[i] != "git":
        return None
    return tokens[i:]


def _subcommand_and_args(git_tokens: list[str]) -> tuple[str | None, list[str]]:
    """From tokens starting at `git`, return (subcommand, args_after_it).

    Skips `git`-level flags (e.g. `git -C path reset`). Note: `-C`/`--git-dir`
    take a value; we skip the value too so it is not mistaken for the
    subcommand.
    """
    i = 1  # skip `git`
    while i < len(git_tokens):
        tok = git_tokens[i]
        if tok in ("-C", "--git-dir", "--work-tree", "-c"):
            i += 2  # flag + its value
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok, git_tokens[i + 1 :]
    return None, []


def _is_destructive_git(simple_cmd: str) -> bool:
    """True if `simple_cmd` is one of the destructive git signatures in scope."""
    git_tokens = _git_tokens(simple_cmd)
    if git_tokens is None:
        return False
    sub, args = _subcommand_and_args(git_tokens)
    if sub is None:
        return False

    if sub == "reset":
        return "--hard" in args

    if sub == "clean":
        # A force flag is required for `git clean` to delete anything: `-f`,
        # `-fd`, `-fdx`, or `--force`.
        for a in args:
            if a == "--force":
                return True
            if a.startswith("-") and not a.startswith("--") and "f" in a:
                return True
        return False

    if sub == "checkout":
        # Block only the whole-tree discard `checkout -- .` / `checkout .`.
        # A specific pathspec (`checkout -- file.py`) is allowed.
        pathspecs = [a for a in args if a != "--" and not a.startswith("-")]
        return pathspecs == ["."]

    if sub == "restore":
        pathspecs = [a for a in args if a != "--" and not a.startswith("-")]
        return "." in pathspecs

    if sub == "stash":
        # Bare `git stash` (no subcommand) → block.
        if not args:
            return True
        # `git stash push` with NO pathspec → block; with a pathspec → allow.
        if args[0] == "push":
            push_args = args[1:]
            if "--" in push_args:
                # everything after `--` is a pathspec → scoped, allow
                return False
            # A trailing non-flag token that is not an option value is a
            # pathspec (e.g. `git stash push file`). `-m msg` has no pathspec.
            has_pathspec = _stash_push_has_pathspec(push_args)
            return not has_pathspec
        # Any other stash subcommand (list/show/pop/apply/drop/...) is allowed.
        return False

    return False


def _stash_push_has_pathspec(push_args: list[str]) -> bool:
    """Heuristic: does `git stash push <push_args>` name a pathspec?

    `-m/--message` takes a value; `-p/--patch`, `-k/--keep-index`,
    `-u/--include-untracked`, `-a/--all` are boolean flags. Any bare
    non-flag token that is not the value of `-m/--message` is treated as a
    pathspec.
    """
    i = 0
    while i < len(push_args):
        tok = push_args[i]
        if tok in ("-m", "--message"):
            i += 2  # consume the message value
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return True  # a bare positional token → pathspec
    return False


def _effective_dir(command: str, hook_cwd: str) -> str:
    """Resolve the effective working directory: `hook_cwd`, unless the first
    simple command is a `cd <path>` prefix, in which case that path (resolved
    against `hook_cwd`) wins. Mirrors `validate_no_uv_sync_in_worktree.py`.
    """
    simple_cmds = _split_simple_commands(command)
    if simple_cmds:
        try:
            first_tokens = shlex.split(simple_cmds[0])
        except ValueError:
            first_tokens = []
        if len(first_tokens) >= 2 and first_tokens[0] == "cd":
            path = Path(first_tokens[1])
            if not path.is_absolute():
                path = Path(hook_cwd) / path if hook_cwd else path
            return str(path)
    return hook_cwd


def _is_worktree_path(path: str) -> bool:
    """Component match against `.worktrees` — never a bare substring match, so
    `.worktrees-backup` never matches.
    """
    return ".worktrees" in Path(path).parts


_BLOCK_MESSAGE_TEMPLATE = (
    "BLOCKED: destructive git command `{command}` in a DIRTY session worktree "
    "({worktree}). This would permanently destroy uncommitted work (staged, "
    "unstaged, and untracked) with no recovery path — the exact failure mode "
    "of the #2137 incident.\n\n"
    "Do one of the following instead:\n"
    "  - Commit or WIP-commit first:  git add -A && git commit --no-verify -m 'WIP'\n"
    "  - Scope to a specific path:     git checkout -- <file>  (not `.`)\n"
    "  - If you REALLY mean it, append the override token to the command:\n"
    "        {command}  {override}\n\n"
    "Uncommitted work is auto-preserved to refs/session-wip/<slug> on session "
    "teardown, but an in-session destructive reset happens before that backstop."
)


def find_violation(command: str, cwd: str, is_dirty: bool) -> str | None:
    """Return a block-reason string if `command` is a destructive git command
    issued from a DIRTY worktree cwd (no override), else None.

    Pure and injectable: `is_dirty` is supplied by the caller (`_run_hook`
    computes it from `git status --porcelain`; tests inject it directly).
    Never raises — any internal parse failure is treated as "no violation"
    (fail open).
    """
    if not command or not cwd:
        return None
    try:
        if OVERRIDE_TOKEN in command:
            return None
        effective_dir = _effective_dir(command, cwd)
        if not _is_worktree_path(effective_dir):
            return None
        if not is_dirty:
            # A destructive command on a clean tree loses nothing → allow.
            return None
        for simple_cmd in _split_simple_commands(command):
            if _is_destructive_git(simple_cmd):
                return _BLOCK_MESSAGE_TEMPLATE.format(
                    command=command.strip(),
                    worktree=effective_dir,
                    override=OVERRIDE_TOKEN,
                )
    except Exception:
        return None
    return None


def _is_tree_dirty(cwd: str) -> bool:
    """Return True iff `git -C <cwd> status --porcelain` reports changes.

    Fail-closed-to-allow: any git error, timeout, or missing path returns
    False (treated as "not dirty" → the guard does not block), preserving the
    fail-open contract.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        return bool(result.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _run_hook() -> None:
    try:
        hook_input = read_stdin()
        if hook_input.get("tool_name") != "Bash":
            sys.exit(0)

        tool_input = hook_input.get("tool_input", {})
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        hook_cwd = hook_input.get("cwd", "") or ""

        # Cheap pre-check: only pay for `git status` when the command is even
        # plausibly destructive-git inside a worktree.
        if not command or OVERRIDE_TOKEN in command:
            sys.exit(0)
        effective_dir = _effective_dir(command, hook_cwd)
        if not _is_worktree_path(effective_dir):
            sys.exit(0)
        if not any(_is_destructive_git(sc) for sc in _split_simple_commands(command)):
            sys.exit(0)

        is_dirty = _is_tree_dirty(effective_dir)
        reason = find_violation(command, hook_cwd, is_dirty)
        if reason:
            block(reason)
    except Exception:
        # Fail open: never crash a legitimate Bash call.
        sys.exit(0)

    sys.exit(0)


def _run_cli(command: str, cwd: str) -> None:
    """Direct-invocation path used by tests/humans: validate a single
    (command, cwd) pair. The CLI path assumes a dirty tree (worst case) so a
    human can check whether a command *would* be blocked.
    """
    reason = find_violation(command, cwd, is_dirty=True)
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def main():
    argv = sys.argv[1:]
    if len(argv) == 2:
        _run_cli(argv[0], argv[1])
    else:
        _run_hook()


if __name__ == "__main__":
    main()
