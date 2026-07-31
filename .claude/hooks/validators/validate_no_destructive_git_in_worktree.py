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
  - `git checkout -- .` / `git checkout .` / `git checkout <ref> -- .`
  - `git restore .`
  - bare `git stash` / `git stash push` with NO pathspec

Deliberate coverage broadening (issue #2448): `git checkout <ref> -- .` (e.g.
`git checkout origin/main -- .`) is now blocked here too. Before #2448 this
guard's own `pathspecs == ["."]` check only matched no-ref forms, so a
ref-qualified whole-tree checkout slipped through. #2448 extracted the shared
`is_destructive_git` predicate (whose `_checkout_is_destructive` matches on
`"." in pathspecs`, ref-qualified or not) into `hook_utils/destructive_git_shapes.py`
for the new shared-checkout guardrail, and this module was switched to import
that shared predicate instead of keeping its own narrower one. The plan's
No-Gos deferred *deliberately changing* this sibling's blocked-shape set, but
reusing the shared predicate was the sanctioned extraction and the resulting
broadened coverage is a strict safety improvement (a ref-qualified whole-tree
checkout is exactly as destructive as the ref-less form), so it is kept
rather than reverted. See `test_blocks_ref_qualified_whole_tree_checkout`
below for the assertion that pins this as intentional.

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
import subprocess
import sys
from pathlib import Path

# Standalone script — sys.path mutation is safe (never imported as library).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hook_utils.destructive_git_shapes import (
    OVERRIDE_TOKEN,  # noqa: E402
    _split_simple_commands,  # noqa: E402
)
from hook_utils.destructive_git_shapes import effective_dir as _effective_dir  # noqa: E402
from hook_utils.destructive_git_shapes import (
    is_destructive_git as _is_destructive_git,  # noqa: E402
)
from hook_utils.destructive_git_shapes import is_worktree_path as _is_worktree_path  # noqa: E402

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


def find_violation_from_hook_input(command: str, hook_cwd: str) -> str | None:
    """Pure(ish) predicate: given the raw hook (command, cwd), compute
    `is_dirty` only when the cheap pre-checks pass, then return
    `find_violation`'s block-reason, else None.

    Mirrors `_run_hook`'s exact pre-check sequence (worktree path + a
    plausibly-destructive command) so callers -- including the in-process
    dispatcher -- get identical behavior without paying for a `git status`
    subprocess on every Bash invocation. Never raises -- any internal
    failure (bad command, git error) is treated as "no violation" (fail
    open), matching `find_violation`'s own contract.
    """
    if not command or OVERRIDE_TOKEN in command:
        return None
    try:
        effective_dir = _effective_dir(command, hook_cwd)
        if not _is_worktree_path(effective_dir):
            return None
        if not any(_is_destructive_git(sc) for sc in _split_simple_commands(command)):
            return None
        is_dirty = _is_tree_dirty(effective_dir)
    except Exception:
        return None
    return find_violation(command, hook_cwd, is_dirty)


def _run_hook() -> None:
    try:
        hook_input = read_stdin()
        if hook_input.get("tool_name") != "Bash":
            sys.exit(0)

        tool_input = hook_input.get("tool_input", {})
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        hook_cwd = hook_input.get("cwd", "") or ""

        reason = find_violation_from_hook_input(command, hook_cwd)
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
