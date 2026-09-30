#!/usr/bin/env python3
"""
Regression guard (issue #2050): block `uv sync` / `uv sync --frozen` when the
effective working directory is inside a git worktree.

Since issue #2052, `agent/worktree_manager.create_worktree` eagerly provisions
a per-worktree `.venv` for SDLC lanes (`.worktrees/{slug}/`). For a worktree
that has its own env (`<worktree-root>/.venv/pyvenv.cfg` exists), `uv sync` is
ALLOWED with a non-blocking notice -- it targets the worktree-local env and
cannot touch the shared one. This guard now protects only UNPROVISIONED
worktrees (e.g. harness-created `.claude/worktrees/{agent}/` checkouts before
their `uv venv .venv` bootstrap), which still share the single project `.venv`
at the repo root. `uv sync` is exact-by-default: it resolves against the *worktree's*
`pyproject.toml`/lock and removes any package not in that resolved set from
the *shared* environment, silently dropping `pytest`, `ruff`, `pytest-xdist`,
and any branch-only dependency that other concurrent lanes (and the standalone
worker) depend on.

This guard blocks the destructive command and points at the safe, additive
alternative: a scoped `uv pip install --python <repo>/.venv/bin/python
"<pkg>==<ver>"`, which does not run project resolution and therefore cannot
strip the shared environment.

Detection is anchored to the *command position*, not a bare substring search:
a command like `git commit -m "fix uv sync bug"` must NOT be blocked just
because the string "uv sync" appears inside it. The command is split on shell
control operators (`&&`, `||`, `;`, `|`, newlines) into simple commands, each
tokenized with `shlex`, and only a simple command whose first non-flag,
non-env-assignment token is `uv` and whose next non-flag token is `sync`
counts as a `uv sync` invocation.

The effective working directory is `hook_input["cwd"]`, unless the first
simple command in the string is a `cd <path>` prefix, in which case `<path>`
(resolved against `cwd`) is used instead. A worktree match is a path
*component* match against `.worktrees` or `.claude/worktrees` -- not a
substring match, so a sibling directory like `.worktrees-backup` never
matches.

This guard does NOT block `uv pip install`, `uv run`, `uv lock`, or any other
`uv` subcommand -- only `uv sync` strips the shared environment.

Claude Code hook protocol:
- Stdin: JSON with tool_name, tool_input, cwd
- To BLOCK: print {"decision": "block", "reason": "..."} to stdout, exit 0
- To ALLOW: print nothing (or exit 0 with no output)

Fail-open: any parse error (malformed JSON, unparseable shell tokens, etc.)
results in exit 0 (allow) -- this guard must never crash a legitimate Bash
call.

Direct/manual invocation (also used by tests):
  python validate_no_uv_sync_in_worktree.py <command> <cwd>
Exits 1 with a message on stderr if the command would be blocked, 0 otherwise.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

_CONTROL_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split_simple_commands(command: str) -> list[str]:
    """Split a shell command string on control operators into simple commands.

    This is intentionally not a full shell parser (see the plan's Rabbit
    Holes note) -- it handles the common `a && b`, `a; b`, `a | b` shapes and
    otherwise treats the whole string as one simple command.
    """
    return [s.strip() for s in _CONTROL_SPLIT_RE.split(command) if s.strip()]


def _is_uv_sync_invocation(simple_cmd: str) -> bool:
    """True if `simple_cmd` is a `uv sync [...]` invocation (command-position
    match, not a substring search).
    """
    try:
        tokens = shlex.split(simple_cmd)
    except ValueError:
        return False

    i = 0
    # Skip leading env-var assignments (FOO=bar uv sync).
    while i < len(tokens) and _ENV_ASSIGNMENT_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens) or tokens[i] != "uv":
        return False
    i += 1

    # Find the subcommand: skip `uv`-level flags (e.g. `uv --directory X sync`),
    # the first non-flag token is the subcommand.
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            i += 1
            continue
        return tok == "sync"
    return False


def _effective_dir(command: str, hook_cwd: str) -> str:
    """Resolve the effective working directory: `hook_cwd`, unless the first
    simple command is a `cd <path>` prefix, in which case that path (resolved
    against `hook_cwd`) wins.
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
                path = Path(hook_cwd) / path
            return str(path)
    return hook_cwd


def _is_worktree_path(path: str) -> bool:
    """Component match against `.worktrees` or `.claude/worktrees` -- never a
    bare substring match (so `.worktrees-backup` does not match).
    """
    parts = Path(path).parts
    if ".worktrees" in parts:
        return True
    for i in range(len(parts) - 1):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            return True
    return False


def _worktree_root(path: str) -> Path | None:
    """Return the worktree ROOT directory for a path inside a worktree
    (`.../.worktrees/{slug}` or `.../.claude/worktrees/{name}`), or None if
    the path has no worktree component (or IS the worktrees container dir
    itself, which has no slug component).
    """
    parts = Path(path).parts
    if ".worktrees" in parts:
        i = parts.index(".worktrees")
        if i + 1 < len(parts):
            return Path(*parts[: i + 2])
        return None
    for i in range(len(parts) - 1):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            if i + 2 < len(parts):
                return Path(*parts[: i + 3])
            return None
    return None


def _is_isolated_worktree(effective_dir: str) -> bool:
    """True if the worktree containing `effective_dir` has its own venv
    (`<worktree-root>/.venv/pyvenv.cfg` exists) -- issue #2052 isolation.

    Deliberately keys on `pyvenv.cfg`, NOT the provisioner's `.provisioned`
    success marker: allowing `uv sync` against a partial worktree-local venv
    is the REPAIR action (uv completes that env in place), and requiring the
    marker would dead-end the manual bootstrap path (`uv venv .venv` never
    writes the marker). The repo root is never a worktree path, so the
    shared env keeps full block protection.
    """
    root = _worktree_root(effective_dir)
    if root is None:
        return False
    return (root / ".venv" / "pyvenv.cfg").exists()


_BLOCK_MESSAGE_TEMPLATE = (
    "BLOCKED: `uv sync` from an UNPROVISIONED worktree ({effective_dir}) can "
    "strip the shared repo-root .venv. uv sync is exact-by-default: it "
    "resolves against this worktree's pyproject.toml/lock and removes any "
    "package not in that set from the environment -- silently dropping "
    "pytest/ruff/pytest-xdist and any other lane's dependencies. This "
    "worktree has no isolated .venv of its own yet.\n\n"
    "Either bootstrap an isolated env first (issue #2052):\n"
    "  uv venv .venv\n"
    "then re-run `uv sync` (allowed once the worktree-local .venv exists), "
    "or use a scoped, additive install into the shared env:\n"
    '  uv pip install --python <repo>/.venv/bin/python "<pkg>==<ver>"\n\n'
    "The latter does not run project resolution, so it cannot strip the "
    "shared env."
)

_ISOLATION_NOTICE_TEMPLATE = (
    "note: `uv sync` allowed -- this worktree has an isolated .venv "
    "({worktree_root}/.venv), so the sync targets the worktree-local "
    "environment and cannot affect sibling lanes or the shared repo-root "
    ".venv (issue #2052)."
)


def find_isolation_notice(command: str, hook_cwd: str) -> str | None:
    """Return a non-blocking notice string when `command` runs `uv sync`
    from an ISOLATED worktree (allowed, warn-not-block -- issue #2052),
    else None. Fail-open like find_violation: any parse failure means no
    notice.
    """
    if not command or not hook_cwd:
        return None
    try:
        effective_dir = _effective_dir(command, hook_cwd)
        if not _is_worktree_path(effective_dir):
            return None
        if not _is_isolated_worktree(effective_dir):
            return None
        for simple_cmd in _split_simple_commands(command):
            if _is_uv_sync_invocation(simple_cmd):
                root = _worktree_root(effective_dir)
                return _ISOLATION_NOTICE_TEMPLATE.format(worktree_root=root)
    except Exception:
        return None
    return None


def find_violation(command: str, hook_cwd: str) -> str | None:
    """Return a block-reason string if `command` runs `uv sync` from an
    effective directory under a worktree, else None. Never raises -- any
    internal parse failure is treated as "no violation" (fail open).
    """
    if not command or not hook_cwd:
        return None
    try:
        effective_dir = _effective_dir(command, hook_cwd)
        if not _is_worktree_path(effective_dir):
            return None
        if _is_isolated_worktree(effective_dir):
            # Issue #2052: the worktree has its own .venv -- `uv sync` targets
            # that local env and cannot strip the shared one. Relaxed from
            # block to allow-with-notice (see find_isolation_notice).
            return None
        for simple_cmd in _split_simple_commands(command):
            if _is_uv_sync_invocation(simple_cmd):
                return _BLOCK_MESSAGE_TEMPLATE.format(effective_dir=effective_dir)
    except Exception:
        return None
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


def _run_hook() -> None:
    try:
        hook_input = read_stdin()
        if hook_input.get("tool_name") != "Bash":
            sys.exit(0)

        tool_input = hook_input.get("tool_input", {})
        command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        hook_cwd = hook_input.get("cwd", "") or ""

        reason = find_violation(command, hook_cwd)
        if reason:
            block(reason)

        notice = find_isolation_notice(command, hook_cwd)
        if notice:
            # Allowed, warn-not-block (issue #2052): no "decision" key means
            # the tool call proceeds; systemMessage surfaces the notice.
            print(json.dumps({"systemMessage": notice}))
    except Exception:
        # Fail open: never crash a legitimate Bash call.
        sys.exit(0)

    sys.exit(0)


def _run_cli(command: str, cwd: str) -> None:
    """Direct-invocation path used by tests: validate a single (command, cwd)
    pair without the JSON stdin protocol.
    """
    reason = find_violation(command, cwd)
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(1)
    notice = find_isolation_notice(command, cwd)
    if notice:
        print(notice, file=sys.stderr)
    sys.exit(0)


def main():
    argv = sys.argv[1:]
    if len(argv) == 2:
        _run_cli(argv[0], argv[1])
    else:
        _run_hook()


if __name__ == "__main__":
    main()
