#!/usr/bin/env python3
"""User-level PreToolUse hook: Block code file commits to main.

This is a STANDALONE script deployed to ~/.claude/hooks/sdlc/ by the update
system. It imports shared utilities from sdlc_context.py in the same directory.

Behavior:
- If a `git commit` command targets the `main` branch AND staged files include
  code extensions (.py, .js, .ts), the commit is BLOCKED unconditionally.
- Non-code files (docs, plans, configs) are allowed on main.
- If not a git commit command, silently allows (fast path). "Is this a commit"
  is decided by tokenizing, not by a substring search: `git -C <dir> commit` is
  a commit and `echo "git commit"` is not.

Every git query is scoped to the directory the command will ACTUALLY run in --
`effective_git_dir` resolves `git -C`, a leading `cd`, then the payload `cwd`,
and only then the hook process's own cwd. Reading git state from the process
cwd is the defect this fixes (#3259): it made the guard answer about the main
checkout while the command ran in a worktree, producing both false blocks and,
worse, false allows.

Exit codes:
  0 — always (Claude Code hook protocol: block via stdout JSON, not exit code)

Claude Code hook protocol:
  Stdin: JSON with tool_name, tool_input, session_id
  To BLOCK: print {"decision": "block", "reason": "..."} to stdout, exit 0
  To ALLOW: print nothing, exit 0
"""

# Global-scope hook: runs under the oldest system python3 on the fleet
# (/usr/bin/python3 = 3.9 on macOS). Without this, the `str | None` return
# annotation on get_current_branch() is evaluated at import time and raises
# TypeError under <3.10, before main()'s fail-open guard can run (issue #2503).
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Standalone script — sys.path mutation is safe (never imported as library)
# Import shared utilities from sibling module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdlc_context import (
    allow,
    block,
    effective_git_dir,
    parse_git_invocation,
    read_stdin,
    split_simple_commands,
)

_GIT_TIMEOUT_S = 5
_CODE_EXTENSIONS = (".py", ".js", ".ts")
_PROTECTED_REPO = "popoto"


def _git(args: list, cwd: str) -> str | None:
    """Run `git -C <cwd> <args>` and return stripped stdout, or None on any
    error, non-zero exit, or timeout.

    The `-C` call form is deliberate (it matches
    validate_no_destructive_git_in_shared_checkout.py::_git_toplevel): a
    directory that no longer exists produces a git error this function
    absorbs, rather than a FileNotFoundError raised by subprocess itself
    before any handler sees it.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd] + list(args),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def get_current_branch(cwd: str) -> str | None:
    """Return the current git branch name in `cwd`, or None if it is not a
    git repo.
    """
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def get_repo_name(cwd: str) -> str | None:
    """Return the name of the repository `cwd` belongs to, or None if it
    cannot be determined.

    Identity comes from the COMMON git dir, never from the worktree root. In
    a linked worktree the worktree-root probe returns the worktree's own
    directory, whose basename is the lane slug -- so a commit made from
    `popoto/.worktrees/lane-a` would be identified as `lane-a`, fail the
    `!= popoto` test, and be allowed. That is a false-allow-ALWAYS for
    exactly the population this guard protects, which is why that probe is
    not used here on any path (spike-3, #3259).

    Two rungs: `--path-format=absolute` (git >= 2.31) first, then bare
    `--git-common-dir` (git >= 2.5), whose output may be relative and is
    resolved against `cwd`. None means unknown, and the caller treats
    unknown as protected.
    """
    common_dir = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    if common_dir is None:
        common_dir = _git(["rev-parse", "--git-common-dir"], cwd)
        if common_dir is None:
            return None
        common_dir = str((Path(cwd) / common_dir).resolve())
    return Path(common_dir).parent.name


def is_git_commit(command: str) -> bool:
    """True if `command` invokes `git commit` in any of its spellings.

    Recognition and directory resolution share one parser
    (`sdlc_context.parse_git_invocation`) on purpose: when they disagreed,
    the fast path here dropped every `git -C <dir> commit` before the
    resolver ever ran, which made the whole `-C` resolution unreachable
    through the real entry point.
    """
    for simple_cmd in split_simple_commands(command or ""):
        parsed = parse_git_invocation(simple_cmd)
        if parsed is not None and parsed["subcommand"] == "commit":
            return True
    return False


def commit_block_reason(command: str, hook_cwd: str) -> str | None:
    """Return the block reason for `command`, or None to allow.

    Pure decision function: every behavioral test drives this rather than
    main(), and it never raises.
    """
    try:
        if not command or not is_git_commit(command):
            return None

        cwd = effective_git_dir(command, hook_cwd)

        branch = get_current_branch(cwd)
        if branch != "main":
            return None

        repo_name = get_repo_name(cwd)
        if repo_name is not None and repo_name != _PROTECTED_REPO:
            return None
        # repo_name is None: identity is UNKNOWN, and unknown takes the
        # restrictive branch. A guard that cannot tell which repo it is in
        # must not conclude "not the protected one, therefore fine". This
        # degrades to allow only where git itself is unusable, because
        # get_current_branch would already have failed there.

        staged = _git(["diff", "--cached", "--name-only"], cwd)
        if staged is None:
            return None  # Fail open if git diff fails.
        staged_code = [f for f in staged.split("\n") if f and f.endswith(_CODE_EXTENSIONS)]
        if not staged_code:
            return None

        return (
            f"Cannot commit code files to main: {', '.join(staged_code[:3])}. "
            "Use /sdlc to create a branch and PR. "
            "Docs, plans, and configs can be committed to main."
        )
    except Exception:
        return None


def main():
    try:
        hook_input = read_stdin()

        # Fast path: ignore non-Bash tools
        if hook_input.get("tool_name") != "Bash":
            allow()

        tool_input = hook_input.get("tool_input", {})
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        hook_cwd = hook_input.get("cwd") or os.getcwd()

        reason = commit_block_reason(command, hook_cwd)
        if reason:
            block(reason)
        allow()

    except Exception:
        # Fail open: never block the user due to hook errors
        sys.exit(0)


if __name__ == "__main__":
    main()
