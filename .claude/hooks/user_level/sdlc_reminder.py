#!/usr/bin/env python3
"""User-level PostToolUse hook: One-time SDLC advisory reminder for code file writes.

This is a STANDALONE script deployed to ~/.claude/hooks/sdlc/ by the update
system. It has NO imports from the AI project — all logic is self-contained.

Behavior:
- When a .py, .js, or .ts file is written/edited AND we are in SDLC context,
  emit a one-time reminder about tests and linting.
- Tracks reminder state in /tmp to avoid repeating.
- If not in SDLC context, silently allows.

Exit codes:
  0 — always (advisory hook, never blocks)

Claude Code hook protocol:
  Stdin: JSON with tool_name, tool_input, session_id
  Advisory: print message to stdout, exit 0
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# Code file extensions that warrant the SDLC reminder
CODE_EXTENSIONS = {".py", ".js", ".ts"}

# The advisory message emitted once per session
SDLC_REMINDER_MESSAGE = (
    "SDLC: Remember to run tests and linting before completing this task "
    "(pytest tests/ && ruff check . && black .)"
)


def is_sdlc_context() -> bool:
    """Detect if we are in an SDLC-managed session.

    Two-tier check:
    1. Git branch starts with "session/" (inside do-build worktree)
    2. AgentSession model shows SDLC stages (requires Redis + AI repo)
    """
    # Check 1: On a session/ branch
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

    # Check 2: Query AgentSession model
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        try:
            sys.path.insert(0, str(Path.home() / "src" / "ai"))
            from models.agent_session import AgentSession

            sessions = AgentSession.query.filter(
                session_id=session_id, status="active"
            )
            for s in sessions:
                history = getattr(s, "history", None)
                if history and any("stage" in str(h) for h in history):
                    return True
        except Exception:
            pass

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


def get_reminder_flag_path(session_id: str) -> Path:
    """Return a temp file path used to track whether the reminder was sent.

    Uses /tmp so we don't pollute any repo with state files.
    The session_id is hashed to create a safe filename.
    """
    safe_id = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return Path("/tmp") / f"sdlc_reminder_{safe_id}"


def has_reminder_been_sent(session_id: str) -> bool:
    """Return True if the SDLC reminder has already been sent in this session."""
    return get_reminder_flag_path(session_id).exists()


def mark_reminder_sent(session_id: str) -> None:
    """Create a flag file indicating the reminder was sent."""
    try:
        get_reminder_flag_path(session_id).touch()
    except OSError:
        pass  # Best effort — don't fail if /tmp is weird


def main():
    try:
        hook_input = read_stdin()
        if not hook_input:
            sys.exit(0)

        tool_name = hook_input.get("tool_name", "")
        if tool_name not in ("Write", "Edit"):
            sys.exit(0)

        tool_input = hook_input.get("tool_input", {})
        file_path = tool_input.get("file_path", "")
        if not file_path:
            sys.exit(0)

        suffix = Path(file_path).suffix.lower()
        if suffix not in CODE_EXTENSIONS:
            sys.exit(0)

        # Only remind in SDLC context
        if not is_sdlc_context():
            sys.exit(0)

        session_id = hook_input.get("session_id", "unknown")
        if has_reminder_been_sent(session_id):
            sys.exit(0)

        print(SDLC_REMINDER_MESSAGE)
        mark_reminder_sent(session_id)
        sys.exit(0)

    except Exception:
        # Fail open: never block the user due to hook errors
        sys.exit(0)


if __name__ == "__main__":
    main()
