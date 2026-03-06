"""
Per-session log snapshots at key lifecycle transitions.

Saves structured snapshots to logs/sessions/{session_id}/ for debugging.
Each snapshot captures session state at the moment of a pause, resume, or complete transition.
"""

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_LOGS_DIR = Path(__file__).parent.parent / "logs" / "sessions"
SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _get_git_summary(working_dir: str | None = None) -> str:
    """Get brief git status for snapshot context.

    Runs `git status --short` and `git log --oneline -3` to capture
    the current working tree state and recent commit history.
    Returns a combined string, or an error message on failure.
    """
    parts = []
    cwd = working_dir or str(Path(__file__).parent.parent)

    try:
        status_result = subprocess.run(
            ["git", "status", "--short"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if status_result.returncode == 0:
            status_text = status_result.stdout.strip()
            parts.append(f"Status:\n{status_text}" if status_text else "Status: clean")
        else:
            parts.append(f"Status: error ({status_result.stderr.strip()[:100]})")
    except Exception as e:
        parts.append(f"Status: unavailable ({e})")

    try:
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-3"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if log_result.returncode == 0:
            parts.append(f"Recent commits:\n{log_result.stdout.strip()}")
    except Exception:
        pass

    return "\n".join(parts)


def save_session_snapshot(
    session_id: str,
    event: str,
    project_key: str = "",
    branch_name: str = "",
    messages: list[dict] | None = None,
    task_summary: str = "",
    extra_context: dict | None = None,
    working_dir: str | None = None,
) -> Path | None:
    """Save a session snapshot to disk.

    Creates a JSON file at logs/sessions/{session_id}/{timestamp}_{event}.json
    capturing the session state at a lifecycle transition point.

    Args:
        session_id: Unique identifier for the session.
        event: Lifecycle event type ("resume", "complete", "error", "pause").
        project_key: Project identifier (e.g., "valor").
        branch_name: Git branch associated with the session.
        messages: Last N messages in the session conversation.
        task_summary: Brief summary of task status.
        extra_context: Additional key-value context data.
        working_dir: Working directory for git status capture.

    Returns:
        Path to the snapshot file, or None on failure.
    """
    try:
        session_dir = SESSION_LOGS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.time()
        # Use integer timestamp + event for filename to avoid float formatting issues
        filename = f"{int(timestamp)}_{event}.json"

        snapshot = {
            "session_id": session_id,
            "event": event,
            "timestamp": timestamp,
            "project_key": project_key,
            "branch_name": branch_name,
            "messages": messages or [],
            "task_summary": task_summary,
            "git_status": _get_git_summary(working_dir),
            "extra_context": extra_context or {},
        }

        snapshot_path = session_dir / filename
        snapshot_path.write_text(json.dumps(snapshot, indent=2, default=str))

        logger.info(
            f"Session snapshot saved: {event} for {session_id} -> {snapshot_path.name}"
        )
        return snapshot_path

    except Exception as e:
        logger.warning(f"Failed to save session snapshot ({event}, {session_id}): {e}")
        return None


def cleanup_old_snapshots(max_age_hours: float = 168) -> int:
    """Remove session log directories older than max_age_hours.

    Checks the modification time of each session directory under
    logs/sessions/ and removes directories that exceed the age threshold.

    Args:
        max_age_hours: Maximum age in hours before cleanup. Defaults to 168 (7 days).

    Returns:
        Count of session directories removed.
    """
    if not SESSION_LOGS_DIR.exists():
        return 0

    removed = 0
    cutoff = time.time() - (max_age_hours * 3600)

    try:
        for session_dir in SESSION_LOGS_DIR.iterdir():
            if not session_dir.is_dir():
                continue

            # Use the newest file's mtime as the session's last activity
            try:
                newest_mtime = max(
                    f.stat().st_mtime for f in session_dir.iterdir() if f.is_file()
                )
            except ValueError:
                # Empty directory — treat as old
                newest_mtime = 0

            if newest_mtime < cutoff:
                shutil.rmtree(session_dir, ignore_errors=True)
                removed += 1
                logger.info(f"Cleaned up old session logs: {session_dir.name}")

    except Exception as e:
        logger.warning(f"Error during session log cleanup: {e}")

    if removed:
        logger.info(f"Removed {removed} old session log director(ies)")

    return removed
