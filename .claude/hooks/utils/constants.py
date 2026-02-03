"""Shared utilities for Claude Code hooks."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def get_project_dir() -> Path:
    """Get the project directory from environment or script location."""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return Path(project_dir)
    # Fallback: assume hooks are in .claude/hooks/utils/ relative to project
    return Path(__file__).parent.parent.parent.parent


def get_session_id(hook_input: dict) -> str:
    """Extract session ID from hook input."""
    return hook_input.get("session_id", "unknown")


def ensure_session_log_dir(session_id: str) -> Path:
    """Ensure the session log directory exists and return its path."""
    project_dir = get_project_dir()
    session_dir = project_dir / "logs" / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def read_hook_input() -> dict:
    """Read and parse JSON input from stdin."""
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            return {}
        return json.loads(raw_input)
    except json.JSONDecodeError:
        return {}


def append_to_log(session_dir: Path, filename: str, entry: dict) -> None:
    """Append an entry to a JSON log file (stored as JSON lines)."""
    log_path = session_dir / filename
    entry["timestamp"] = datetime.utcnow().isoformat() + "Z"
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def write_json_log(session_dir: Path, filename: str, data: dict) -> None:
    """Write data to a JSON file (overwrites)."""
    log_path = session_dir / filename
    data["timestamp"] = datetime.utcnow().isoformat() + "Z"
    with open(log_path, "w") as f:
        json.dump(data, f, indent=2)
