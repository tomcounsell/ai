#!/usr/bin/env python3
"""Shared SDLC context detection for user-level hooks.

This module provides the single source of truth for detecting whether the
current session is in an SDLC-managed context. All 3 SDLC hooks import
from here instead of duplicating the detection logic.

This is a STANDALONE module deployed to ~/.claude/hooks/sdlc/ by the update
system. The AgentSession import is optional — it falls back gracefully to
branch-only detection when Redis or the AI repo isn't available.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


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
