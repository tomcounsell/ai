"""Shared utilities for Claude Code hooks."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

# Single definition of the stdin-payload parser (issue #2750). This module
# previously carried its own read_hook_input() that caught JSONDecodeError
# only: it was annotated -> dict but returned whatever json.loads produced,
# so a payload of `null`, `[1,2]`, `"str"` or `42` reached callers as a
# non-dict and blew up on the first .get(). The hook_target.py version guards
# that and is the one every caller now gets. Re-exported rather than moved so
# existing `from hook_utils.constants import read_hook_input` imports keep
# working — there is exactly one implementation behind both names.
from .hook_target import read_hook_input  # noqa: F401


def get_project_dir() -> Path:
    """Get the project directory from environment or script location."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return Path(project_dir)
    # Fallback: assume hooks are in .claude/hooks/utils/ relative to project
    return Path(__file__).parent.parent.parent.parent


def get_data_sessions_dir() -> Path:
    """Return the data/sessions directory under the project root.

    Used by SDLC hooks (post_tool_use.py, sdlc_reminder.py, validate_sdlc_on_stop.py)
    to read/write session state files.
    """
    return get_project_dir() / "data" / "sessions"


def get_session_id(hook_input: dict) -> str:
    """Extract session ID from hook input."""
    return hook_input.get("session_id", "unknown")


def ensure_session_log_dir(session_id: str) -> Path:
    """Ensure the session log directory exists and return its path."""
    project_dir = get_project_dir()
    session_dir = project_dir / "logs" / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def append_to_log(session_dir: Path, filename: str, entry: dict) -> None:
    """Append an entry to a JSON log file (stored as JSON lines)."""
    log_path = session_dir / filename
    entry["timestamp"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def log_hook_error(hook_name: str, error: str) -> None:
    """Log a hook error to logs/hooks.log in a format reflections can parse.

    Format: 2026-04-01 12:00:00 - hook_name - ERROR - message
    """
    try:
        log_path = get_project_dir() / "logs" / "hooks.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"{ts} - {hook_name} - ERROR - {error}\n")
    except Exception:
        pass  # Last-resort silence — logging must never crash a hook


def write_json_log(session_dir: Path, filename: str, data: dict) -> None:
    """Write data to a JSON file (overwrites)."""
    log_path = session_dir / filename
    data["timestamp"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with open(log_path, "w") as f:
        json.dump(data, f, indent=2)
