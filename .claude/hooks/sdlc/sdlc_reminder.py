#!/usr/bin/env python3
"""User-level PostToolUse hook: One-time SDLC advisory reminder for code file writes.

This is a STANDALONE script deployed to ~/.claude/hooks/sdlc/ by the update
system. It imports shared utilities from sdlc_context.py in the same directory.

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
import os
import sys
from pathlib import Path

# Import shared utilities from sibling module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sdlc_context import is_sdlc_context, read_stdin

# Code file extensions that warrant the SDLC reminder
CODE_EXTENSIONS = {".py", ".js", ".ts"}

# The advisory message emitted once per session
SDLC_REMINDER_MESSAGE = (
    "SDLC: Remember to run tests and linting before completing this task "
    "(pytest tests/ && ruff check . && black .)"
)


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
