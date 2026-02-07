"""Crash tracker - correlate bridge crashes with recent git commits.

Logs each bridge start/crash event with timestamp and git commit hash.
Detects patterns that suggest code-caused crashes (3+ crashes within
30 minutes after a recent commit).
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
CRASH_HISTORY_FILE = PROJECT_DIR / "data" / "crash_history.jsonl"

# Detection thresholds
CRASH_COUNT_THRESHOLD = 3  # Number of crashes to trigger detection
CRASH_WINDOW_SECONDS = 1800  # 30 minutes
COMMIT_AGE_THRESHOLD = 3600  # Only consider commits < 1 hour old


@dataclass
class CrashEvent:
    """A single crash or start event."""

    timestamp: float
    event_type: str  # "start" or "crash"
    commit_sha: str
    commit_age_seconds: float
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "commit_sha": self.commit_sha,
            "commit_age_seconds": self.commit_age_seconds,
            "reason": self.reason,
            "datetime": datetime.fromtimestamp(self.timestamp).isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CrashEvent":
        return cls(
            timestamp=d["timestamp"],
            event_type=d["event_type"],
            commit_sha=d["commit_sha"],
            commit_age_seconds=d.get("commit_age_seconds", 0),
            reason=d.get("reason"),
        )


def get_current_commit() -> tuple[str, float]:
    """Get current HEAD commit SHA and its age in seconds."""
    try:
        # Get commit SHA
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = result.stdout.strip()[:8] if result.returncode == 0 else "unknown"

        # Get commit timestamp
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            commit_time = int(result.stdout.strip())
            age = time.time() - commit_time
        else:
            age = float("inf")

        return sha, age
    except Exception as e:
        logger.debug(f"Could not get git info: {e}")
        return "unknown", float("inf")


def log_event(event_type: str, reason: str | None = None) -> CrashEvent:
    """Log a start or crash event to the history file."""
    CRASH_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

    sha, age = get_current_commit()
    event = CrashEvent(
        timestamp=time.time(),
        event_type=event_type,
        commit_sha=sha,
        commit_age_seconds=age,
        reason=reason,
    )

    try:
        with open(CRASH_HISTORY_FILE, "a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")
    except Exception as e:
        logger.error(f"Failed to log crash event: {e}")

    return event


def log_start() -> CrashEvent:
    """Log a bridge start event."""
    return log_event("start")


def log_crash(reason: str | None = None) -> CrashEvent:
    """Log a bridge crash event."""
    return log_event("crash", reason)


def get_recent_events(window_seconds: float = CRASH_WINDOW_SECONDS) -> list[CrashEvent]:
    """Get events from the last N seconds."""
    if not CRASH_HISTORY_FILE.exists():
        return []

    cutoff = time.time() - window_seconds
    events = []

    try:
        with open(CRASH_HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d["timestamp"] >= cutoff:
                        events.append(CrashEvent.from_dict(d))
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception as e:
        logger.error(f"Failed to read crash history: {e}")

    return events


def get_recent_crashes(window_seconds: float = CRASH_WINDOW_SECONDS) -> list[CrashEvent]:
    """Get crash events from the last N seconds."""
    return [e for e in get_recent_events(window_seconds) if e.event_type == "crash"]


def detect_crash_pattern() -> tuple[bool, str | None]:
    """Detect if recent crashes correlate with a recent commit.

    Returns:
        (should_revert, commit_sha) - True if auto-revert recommended
    """
    recent_crashes = get_recent_crashes(CRASH_WINDOW_SECONDS)

    if len(recent_crashes) < CRASH_COUNT_THRESHOLD:
        return False, None

    # Check if crashes happened after a recent commit
    current_sha, commit_age = get_current_commit()

    if commit_age > COMMIT_AGE_THRESHOLD:
        # Commit is old, crashes aren't code-related
        return False, None

    # Check if most crashes are on the current commit
    crashes_on_current = sum(1 for c in recent_crashes if c.commit_sha == current_sha)

    if crashes_on_current >= CRASH_COUNT_THRESHOLD:
        logger.warning(
            f"Crash pattern detected: {crashes_on_current} crashes on commit {current_sha} "
            f"(commit age: {commit_age:.0f}s)"
        )
        return True, current_sha

    return False, None


def get_previous_commit() -> str | None:
    """Get the commit SHA before HEAD."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()[:8] if result.returncode == 0 else None
    except Exception:
        return None


def clear_old_history(max_age_seconds: float = 86400) -> int:
    """Remove events older than max_age_seconds. Returns count removed."""
    if not CRASH_HISTORY_FILE.exists():
        return 0

    cutoff = time.time() - max_age_seconds
    kept = []
    removed = 0

    try:
        with open(CRASH_HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if d["timestamp"] >= cutoff:
                        kept.append(line)
                    else:
                        removed += 1
                except (json.JSONDecodeError, KeyError):
                    removed += 1

        if removed > 0:
            with open(CRASH_HISTORY_FILE, "w") as f:
                for line in kept:
                    f.write(line + "\n")

    except Exception as e:
        logger.error(f"Failed to clear old crash history: {e}")

    return removed
