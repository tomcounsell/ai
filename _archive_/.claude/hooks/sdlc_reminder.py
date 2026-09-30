#!/usr/bin/env python3
"""Hook: PostToolUse - Emit a one-time SDLC advisory reminder for code file writes.

When a .py, .js, or .ts file is written or edited, print a one-time reminder to run
tests and linting. The reminder is only emitted once per session, tracked via a
`reminder_sent` flag in the session's sdlc_state.json file.

This hook always exits 0 — it is purely advisory and never blocks tool execution.
"""

import json
import sys
from pathlib import Path

# Add the hooks directory to sys.path so utils can be imported
sys.path.insert(0, str(Path(__file__).parent))

from hook_utils.constants import get_data_sessions_dir, read_hook_input  # noqa: E402

# Code file extensions that warrant the SDLC reminder
CODE_EXTENSIONS = {".py", ".js", ".ts"}

# The advisory message emitted once per session
SDLC_REMINDER_MESSAGE = (
    "SDLC: Remember to run tests and linting before completing this task "
    "(pytest tests/ && python -m ruff check . && python -m ruff format --check .)"
)


def get_reminder_state_path(session_id: str) -> Path:
    """Return the path to the sdlc_state.json file for a given session."""
    return get_data_sessions_dir() / session_id / "sdlc_state.json"


def has_reminder_been_sent(session_id: str) -> bool:
    """Return True if the SDLC reminder has already been sent in this session."""
    state_path = get_reminder_state_path(session_id)
    if not state_path.exists():
        return False
    try:
        with open(state_path) as f:
            data = json.load(f)
        return bool(data.get("reminder_sent", False))
    except (json.JSONDecodeError, OSError):
        return False


def mark_reminder_sent(session_id: str) -> None:
    """Persist reminder_sent=True in the session's sdlc_state.json.

    If the file already exists (written by post_tool_use.py), merge the flag in
    rather than overwriting the whole file.
    """
    state_path = get_reminder_state_path(session_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing state if present to preserve other fields
    existing: dict = {}
    if state_path.exists():
        try:
            with open(state_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}

    existing["reminder_sent"] = True
    tmp_path = state_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(existing, f, indent=2)
        tmp_path.rename(state_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def emit_reminder_if_needed(hook_input: dict) -> None:
    """Print the SDLC advisory reminder if this is the first code file write.

    Conditions for emitting the reminder:
    - tool_name is Write or Edit
    - file_path has a code extension (.py, .js, .ts)
    - reminder has not been sent yet this session

    Always a no-op for non-code files and non-Write/Edit tools.
    """
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        return

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        return

    suffix = Path(file_path).suffix.lower()
    if suffix not in CODE_EXTENSIONS:
        return

    session_id = hook_input.get("session_id", "unknown")

    if has_reminder_been_sent(session_id):
        return

    print(SDLC_REMINDER_MESSAGE)
    mark_reminder_sent(session_id)


def main() -> None:
    """Entry point for the PostToolUse hook."""
    hook_input = read_hook_input()
    if not hook_input:
        sys.exit(0)

    emit_reminder_if_needed(hook_input)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        from hook_utils.constants import log_hook_error

        log_hook_error("sdlc_reminder", str(e))
