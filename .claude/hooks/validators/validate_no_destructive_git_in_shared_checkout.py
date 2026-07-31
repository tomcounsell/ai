#!/usr/bin/env python3
"""
Guard (issue #2448): block whole-tree destructive git commands issued from
the SHARED main checkout (this repo's toplevel, outside any `.worktrees/`),
where they would silently clobber another concurrent SDLC lane's uncommitted
work -- most dangerously `docs/plans/*.md` edits, which this repo's
convention deliberately puts on `main` in the shared checkout rather than a
worktree.

Background: during a live parallel `/do-sdlc` batch, a MERGE-stage subagent
ran `git checkout origin/main -- .` in the shared main checkout to compare
its branch against main, transiently overwriting other lanes' in-flight
uncommitted work, then ran `git reset --hard HEAD` to "fix" it -- itself just
as dangerous in a shared checkout. This is the *inverse* surface from the
#2137 worktree guard (`validate_no_destructive_git_in_worktree.py`), which
protects a DIRTY `.worktrees/{slug}/` and never fires in the shared checkout.

Blocked signatures (only when the effective cwd IS this repo's shared
checkout -- i.e. `git -C <cwd> rev-parse --show-toplevel` resolves to this
repo's root AND the cwd is not under a `.worktrees/` segment):
  - `git reset --hard [<ref>]`
  - `git clean -f[dx...]` / `git clean --force`
  - `git checkout <ref> -- .` / `git checkout .` / `git checkout -- .`
  - `git restore .`

Deliberately NOT blocked (narrow deny-list -- see the plan's "Design
constraints" and "Rabbit Holes"):
  - `git stash` / `git stash push` (any form) -- out of the five-shape
    deny-list; the shared shape predicate used here
    (`is_destructive_git_shared_checkout`) has no stash branch at all.
  - path-scoped variants: `git checkout <ref> -- file.py`, `git restore
    path/`, `git checkout -- file.py`.
  - any command carrying the inline override token `# allow-destructive-git`.
  - the same shapes issued inside `.worktrees/{slug}/` (the #2137 guard's
    domain; this guard must NEVER fire there).
  - the same shapes issued in a *foreign* repo (a git toplevel that is not
    this repo's root) or a non-repo cwd -- fail-open, not our surface.

Unlike the #2137 worktree guard, this validator does NOT check whether the
tree is dirty and never calls `git status`: the danger here is to *other
lanes'* concurrent uncommitted work, invisible to the issuing agent's own
notion of "clean," so the block is unconditional in the shared checkout.

Detection is anchored to the *command position*, not a bare substring
search: `git commit -m "reset --hard bug"` must NOT be blocked. Shape
detection (`is_destructive_git_shared_checkout`, command splitting, `cd
<path> &&` cwd resolution, the override token) is shared with the #2137
worktree guard via `hook_utils/destructive_git_shapes.py` -- no duplicated
logic between the two validators.

Testability seam: the pure predicate `find_violation(command, cwd, *,
repo_root, in_worktree)` takes classification (repo_root, in_worktree) as
injected arguments -- no git calls inside, fully deterministic for tests.
The live git/`Path(__file__)` resolution lives in the thin wrapper
`find_violation_from_hook_input(command, hook_cwd)`.

Claude Code hook protocol:
- Stdin: JSON with tool_name, tool_input, cwd
- To BLOCK: print {"decision": "block", "reason": "..."} to stdout, exit 0
- To ALLOW: print nothing, exit 0

Fail-open: any parse error, git error, or unexpected exception results in
exit 0 (allow) -- this guard must never crash a legitimate Bash call.

Direct/manual invocation (also used by tests):
  python validate_no_destructive_git_in_shared_checkout.py <command> <cwd>
Exits 1 with a message on stderr if the command would be blocked (assumes
the shared checkout for the CLI path, i.e. repo_root=cwd, in_worktree=False),
0 otherwise.
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
from hook_utils.destructive_git_shapes import (  # noqa: E402
    is_destructive_git_shared_checkout as _is_destructive_git_shared_checkout,
)
from hook_utils.destructive_git_shapes import is_worktree_path as _is_worktree_path  # noqa: E402

_GIT_TIMEOUT_S = 10


def _resolve_this_repo_root() -> str | None:
    """Positive identity bind: the canonical main-checkout root of the repo
    this hook script instance belongs to, resolved via git's shared common
    directory rather than a plain filesystem walk.

    A plain `Path(__file__).resolve().parents[3]` would resolve to the
    WORKTREE's own root when this script is invoked from a worktree
    session's own `.worktrees/{slug}/.claude/hooks/...` copy (every
    worktree carries its own tracked copy of this file, and Claude Code
    always runs the hook script under `$CLAUDE_PROJECT_DIR`, which is the
    worktree itself for a worktree session) -- silently breaking the block
    for `cd <main-repo-root> && git reset --hard` issued from inside a
    worktree session (Success Criterion 5).

    `git rev-parse --git-common-dir` is invariant to that: every linked
    worktree and the main checkout all share the SAME common git directory
    (normally `<main-repo-root>/.git`), so its parent is always the one
    canonical main repo root regardless of which checkout's copy of this
    script is executing. Returns None on any git error (missing git,
    timeout, not a git repo at all) -- callers treat None as "never
    matches," which fails open.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(Path(__file__).resolve().parent),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
        if result.returncode != 0:
            return None
        common_dir = result.stdout.strip()
        return str(Path(common_dir).parent) if common_dir else None
    except (subprocess.SubprocessError, OSError):
        return None


_THIS_REPO_ROOT = _resolve_this_repo_root()

_BLOCK_MESSAGE_TEMPLATE = (
    "BLOCKED: whole-tree destructive git command `{command}` in the SHARED "
    "main checkout ({cwd}). In a shared checkout, other concurrent SDLC "
    "lanes may have uncommitted work in the same working tree (this repo's "
    "convention puts plan edits on `main` directly) -- a whole-tree reset, "
    "clean, checkout, or restore can silently destroy another lane's "
    "in-flight work with no recovery path.\n\n"
    "Do one of the following instead:\n"
    "  - For a baseline comparison, use a disposable detached worktree:\n"
    "        git worktree add --detach .worktrees/_merge_baseline_main origin/main\n"
    "  - To revert a single file you own, scope the command to a path:\n"
    "        git checkout -- <file>   (not `.`)\n"
    "  - If you REALLY mean it, append the override token to the command:\n"
    "        {command}  {override}"
)


def find_violation(
    command: str,
    cwd: str,
    *,
    repo_root: str | None,
    in_worktree: bool,
) -> str | None:
    """Return a block-reason string if `command` is a whole-tree destructive
    git command issued from the shared checkout of THIS repo (no override),
    else None.

    Pure and injectable: `repo_root` (this cwd's git toplevel, or None if not
    a git repo / git error) and `in_worktree` (whether the effective cwd is
    under a `.worktrees/` segment) are supplied by the caller
    (`find_violation_from_hook_input` computes them live; tests inject them
    directly). Never raises -- any internal parse failure is treated as "no
    violation" (fail open).
    """
    if not command or not cwd:
        return None
    try:
        if OVERRIDE_TOKEN in command:
            return None
        if in_worktree:
            # Never fires inside .worktrees/ -- that's the #2137 guard's
            # domain.
            return None
        if repo_root != _THIS_REPO_ROOT:
            # Foreign repo, non-repo, or unresolved toplevel -> not our
            # surface, fail open.
            return None
        for simple_cmd in _split_simple_commands(command):
            if _is_destructive_git_shared_checkout(simple_cmd):
                return _BLOCK_MESSAGE_TEMPLATE.format(
                    command=command.strip(),
                    cwd=cwd,
                    override=OVERRIDE_TOKEN,
                )
    except Exception:
        return None
    return None


def _git_toplevel(cwd: str) -> str | None:
    """Return `git -C <cwd> rev-parse --show-toplevel`, or None on any git
    error, timeout, or missing path. Fail-open by construction: a None
    return never matches `_THIS_REPO_ROOT`, so `find_violation` allows.
    """
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
        if result.returncode != 0:
            return None
        toplevel = result.stdout.strip()
        return toplevel or None
    except (subprocess.SubprocessError, OSError):
        return None


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
    """Live-classification wrapper: resolve the effective cwd, its git
    toplevel, and whether it is under `.worktrees/`, then delegate to the
    pure `find_violation`. Never raises -- any internal failure (bad
    command, git error) is treated as "no violation" (fail open).
    """
    if not command or OVERRIDE_TOKEN in command:
        return None
    try:
        effective_dir = _effective_dir(command, hook_cwd)
        in_worktree = _is_worktree_path(effective_dir)
        repo_root = None if in_worktree else _git_toplevel(effective_dir)
    except Exception:
        return None
    return find_violation(
        command,
        effective_dir,
        repo_root=repo_root,
        in_worktree=in_worktree,
    )


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
    (command, cwd) pair, assuming `cwd` IS the shared checkout (repo_root =
    cwd, in_worktree = False) so a human can check whether a command *would*
    be blocked there.
    """
    reason = find_violation(command, cwd, repo_root=cwd, in_worktree=False)
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
