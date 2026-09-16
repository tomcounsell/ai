#!/usr/bin/env python3
"""Shared SDLC context detection for user-level hooks.

This module provides the single source of truth for detecting whether the
current session is in an SDLC-managed context. All 3 SDLC hooks import
from here instead of duplicating the detection logic.

This is a STANDALONE module deployed to ~/.claude/hooks/sdlc/ by the update
system. The AgentSession import is optional — it falls back gracefully to
branch-only detection when Redis or the AI repo isn't available.
"""

# Global-scope module: runs under the oldest system python3 on the fleet
# (/usr/bin/python3 = 3.9 on macOS), so `str | None` annotations must stay
# unevaluated at import time. Enforced by tests/unit/test_hook_interpreter.py.
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

_CONTROL_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")

# A path token that still contains shell syntax was never expanded: `shlex.split`
# hands `$(git rev-parse ...)` or `$REPO_ROOT` back verbatim, and using it as a
# directory sends every downstream git call into its fail-open handler, which
# ALLOWS. Reject the token and fall through to the next rung, where the payload
# `cwd` is a real, already-resolved directory -- the correct answer for exactly
# this command shape.
_UNEXPANDED_MARKERS = ("$(", "`", "${")


def _split_simple_commands(command: str) -> list:
    """Split a shell command string on control operators into simple commands.

    Intentionally not a full shell parser: it handles the common `a && b`,
    `a; b`, `a | b` shapes and otherwise treats the whole string as one simple
    command. Ported from validate_no_uv_sync_in_worktree.py.
    """
    return [s.strip() for s in _CONTROL_SPLIT_RE.split(command) if s.strip()]


def _is_literal_path_token(token: str) -> bool:
    """True if `token` is a usable literal path rather than an unexpanded
    shell construct.
    """
    if not token:
        return False
    if token.startswith("$"):
        return False
    return not any(marker in token for marker in _UNEXPANDED_MARKERS)


def _resolve_against(path_token: str, base: str) -> str:
    path = Path(path_token)
    if not path.is_absolute():
        path = Path(base) / path
    return str(path)


def effective_git_dir(command: str, hook_cwd: str) -> str:
    """Resolve the directory a `git commit` command will actually run in.

    Precedence, highest first:

    1. ``git -C <path>`` appearing in the simple command that contains
       ``commit``. Relative paths resolve against rung 3.
    2. A leading ``cd <path>`` simple command, resolved against `hook_cwd`
       when relative.
    3. The payload ``cwd`` (`hook_cwd`).
    4. ``os.getcwd()`` -- reached only when the payload carries no ``cwd``.
       This is an explicit LAST RESORT, not a default: silently falling back
       to the hook process's own cwd is the defect this function exists to
       fix, because that cwd is the main checkout no matter which worktree
       the command targets.

    Rungs 1 and 2 accept a path token only if it is literal; an unexpanded
    shell construct falls through to the next rung. Never raises: it always
    returns a directory string.
    """
    return os.getcwd()
    try:
        base = hook_cwd or os.getcwd()

        # Rung 1: `git -C <path>` in the simple command that does the commit.
        for simple_cmd in _split_simple_commands(command or ""):
            if "commit" not in simple_cmd:
                continue
            try:
                tokens = shlex.split(simple_cmd)
            except ValueError:
                continue
            for i, token in enumerate(tokens):
                if token != "-C" or i + 1 >= len(tokens):
                    continue
                candidate = tokens[i + 1]
                if _is_literal_path_token(candidate):
                    return _resolve_against(candidate, base)

        # Rung 2: a leading `cd <path>`.
        simple_cmds = _split_simple_commands(command or "")
        if simple_cmds:
            try:
                first_tokens = shlex.split(simple_cmds[0])
            except ValueError:
                first_tokens = []
            if len(first_tokens) >= 2 and first_tokens[0] == "cd":
                if _is_literal_path_token(first_tokens[1]):
                    return _resolve_against(first_tokens[1], base)

        # Rungs 3 and 4.
        return base
    except Exception:
        return hook_cwd or os.getcwd()


def is_sdlc_context() -> bool:
    """Detect if we are in an SDLC-managed session.

    Two-tier check:
    1. Git branch starts with "session/" (inside do-build worktree)
    2. AgentSession model shows SDLC stages (requires Redis + AI repo)
    """
    # Check 1: On a session/ branch (inside do-build worktree)
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if branch.startswith("session/"):
            return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Check 2: Query AgentSession model for active SDLC session
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        try:
            # Standalone script — sys.path mutation is safe (never imported as library)
            sys.path.insert(0, str(Path.home() / "src" / "ai"))
            from models.agent_session import AgentSession

            sessions = AgentSession.query.filter(session_id=session_id, status="active")
            for s in sessions:
                history = getattr(s, "history", None)
                if history and any("stage" in str(h) for h in history):
                    return True
        except Exception:
            pass  # Redis unavailable, model not importable, etc.

    return False


def read_stdin() -> dict:
    """Read and parse JSON from stdin. Returns empty dict on failure."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {}


def block(reason: str) -> None:
    """Print a block decision and exit 0 (Claude Code hook protocol)."""
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def allow() -> None:
    """Allow the command through and exit 0."""
    sys.exit(0)
