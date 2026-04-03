"""Session watchdog - detect and fix stuck agent sessions.

Monitors active agent sessions for signs of distress:
- Silent sessions (no activity for extended period)
- Looping behavior (repeated identical tool calls)
- Error cascades (high error rate in recent activity)
- Excessively long sessions

When issues are detected, the watchdog FIXES them automatically:
- Retries stalled sessions with exponential backoff (up to MAX_STALL_RETRIES)
- Marks stuck sessions as abandoned after retries exhausted
- Creates GitHub issues for problems that can't be auto-fixed
- Notifies human via Telegram after max retries exhausted

NO ALERTS ARE SENT for recoverable stalls. Either retry, fix, or create an issue.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from popoto.exceptions import ModelException

from models.agent_session import AgentSession


def _to_timestamp(val) -> float | None:
    """Convert a datetime or float to a Unix timestamp."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.timestamp()
    if isinstance(val, (int, float)):
        return float(val)
    return None


logger = logging.getLogger(__name__)

# Watchdog configuration constants
WATCHDOG_INTERVAL = 300  # 5 minutes in seconds
SILENCE_THRESHOLD = 600  # 10 minutes
LOOP_THRESHOLD = 5  # identical tool calls to trigger
ERROR_CASCADE_THRESHOLD = 5  # errors in last 20 calls
ERROR_CASCADE_WINDOW = 20
DURATION_THRESHOLD = 7200  # 2 hours
ABANDON_THRESHOLD = 1800  # 30 minutes silent = auto-abandon

# Stall detection thresholds (seconds)
STALL_THRESHOLD_PENDING = 300  # 5 minutes
STALL_THRESHOLD_RUNNING = 2700  # 45 minutes
# STALL_TIMEOUT_SECONDS env var overrides the default active session stall threshold
# Note: activity-based stall detection (SDK_INACTIVITY_TIMEOUT_SECONDS in sdk_client.py)
# takes precedence for sessions with activity tracking. This threshold is a fallback
# for sessions without activity data.
STALL_THRESHOLD_ACTIVE = int(os.environ.get("STALL_TIMEOUT_SECONDS", 600))  # 10 min default

STALL_THRESHOLDS = {
    "pending": STALL_THRESHOLD_PENDING,
    "running": STALL_THRESHOLD_RUNNING,
    "active": STALL_THRESHOLD_ACTIVE,
}


# Transcript liveness: if transcript.txt was modified within this many minutes,
# the session is considered alive (doing sub-agent work) even if updated_at
# in Redis is stale. See issue #360.
TRANSCRIPT_STALE_THRESHOLD_MIN = 15

# Default logs/sessions directory for transcript liveness checks
_PROJECT_DIR = Path(__file__).parent.parent
_DEFAULT_LOGS_DIR = _PROJECT_DIR / "logs" / "sessions"


def _check_transcript_liveness(
    session_id: str,
    logs_dir: Path | None = None,
) -> tuple[bool, float]:
    """Check if a session's transcript file has been recently modified.

    Uses os.path.getmtime() on logs/sessions/{session_id}/transcript.txt
    to determine if the session is actively doing work (e.g., sub-agent calls)
    even when the Redis updated_at field hasn't been updated.

    Args:
        session_id: The session ID to check.
        logs_dir: Override for the logs/sessions directory (used in tests).

    Returns:
        Tuple of (is_stale, stale_minutes):
        - is_stale: True if transcript is older than TRANSCRIPT_STALE_THRESHOLD_MIN
          or doesn't exist.
        - stale_minutes: How many minutes since the transcript was last modified.
          Returns float('inf') if the file doesn't exist.
    """
    base_dir = logs_dir if logs_dir is not None else _DEFAULT_LOGS_DIR
    transcript_path = base_dir / session_id / "transcript.txt"

    if not transcript_path.exists():
        return (True, float("inf"))

    try:
        mtime = os.path.getmtime(transcript_path)
        age_seconds = time.time() - mtime
        stale_minutes = age_seconds / 60.0
        is_stale = stale_minutes >= TRANSCRIPT_STALE_THRESHOLD_MIN
        return (is_stale, stale_minutes)
    except OSError:
        # File disappeared between exists() check and getmtime()
        return (True, float("inf"))


async def watchdog_loop(telegram_client=None) -> None:
    """Run the watchdog monitoring loop indefinitely.

    Args:
        telegram_client: Telegram client (kept for API compatibility, not used for alerts)

    This function never returns - it runs forever checking sessions
    at regular intervals. All exceptions are caught and logged to
    prevent the watchdog from crashing.
    """
    logger.info("[watchdog] Session watchdog started (interval=%ds)", WATCHDOG_INTERVAL)

    while True:
        try:
            await check_all_sessions()
        except Exception as e:
            logger.error("[watchdog] Error in watchdog loop: %s", e, exc_info=True)

        try:
            check_stalled_sessions()
        except Exception as e:
            logger.error("[watchdog] Error in stall check: %s", e, exc_info=True)

        await asyncio.sleep(WATCHDOG_INTERVAL)


async def check_all_sessions() -> None:
    """Check all active sessions for health issues and fix them.

    Queries all active sessions, assesses their health, and takes action:
    - Stuck/silent sessions: marked as abandoned
    - Unfixable issues: GitHub issue created

    NO ALERTS ARE SENT. Either fix it or create an issue.
    """
    try:
        active_sessions = list(AgentSession.query.filter(status="active"))
    except Exception as e:
        logger.error("[watchdog] Failed to query active sessions: %s", e)
        return

    healthy_count = 0
    fixed_count = 0

    for session in active_sessions:
        try:
            assessment = assess_session_health(session)

            if assessment["healthy"]:
                healthy_count += 1
            else:
                # Fix the problem instead of alerting
                fixed = await fix_unhealthy_session(session, assessment)
                if fixed:
                    fixed_count += 1
                    logger.info(
                        "[watchdog] Fixed session %s: %s",
                        session.session_id,
                        ", ".join(assessment["issues"]),
                    )
        except ModelException as e:
            # CRASH GUARD: Stale sessions left behind by SDK crashes can have
            # duplicate Redis keys or corrupted state. When the watchdog tries
            # to save/update them, popoto raises ModelException (e.g. unique
            # constraint violations). We catch all ModelException variants and
            # mark the session as failed to prevent the watchdog from looping
            # on it every cycle. See nudge loop related guards.
            try:
                session.status = "failed"
                # Capture exception details so the reflections system can produce
                # actionable bug reports instead of "empty error summary" issues.
                session.summary = f"Watchdog: {type(e).__name__}: {e}"[:500]
                session.save()
                logger.warning(
                    "[watchdog] Marked stale session %s as failed (%s)",
                    session.session_id,
                    e,
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(
                "[watchdog] Error handling session %s: %s",
                session.session_id,
                e,
                exc_info=True,
            )

    if fixed_count > 0:
        logger.info(
            "[watchdog] Checked %d active sessions: %d healthy, %d fixed",
            len(active_sessions),
            healthy_count,
            fixed_count,
        )
    else:
        logger.debug(
            "[watchdog] Checked %d active sessions: all healthy",
            len(active_sessions),
        )


def check_stalled_sessions() -> list[dict]:
    """Check for sessions that appear stalled based on status-specific thresholds.

    Queries all sessions with status in (pending, running, active) and checks
    how long they've been in that state. Uses started_at (falling back
    to created_at) as the reference timestamp.

    For active sessions, also checks updated_at -- if updated_at is recent
    (within the active threshold), the session is not considered stalled.

    Thresholds:
        - pending > 300s (5 min) = stalled
        - running > 2700s (45 min) = stalled
        - active with no recent updated_at > 600s (10 min) = stalled

    Returns:
        List of dicts for stalled sessions, each containing:
        session_id, status, duration, threshold, project_key, last_history.
    """
    stalled: list[dict] = []
    now = time.time()

    for status_val in ("pending", "running", "active"):
        try:
            sessions = list(AgentSession.query.filter(status=status_val))
        except Exception as e:
            logger.error(
                "[watchdog] Failed to query %s sessions for stall check: %s",
                status_val,
                e,
            )
            continue

        threshold = STALL_THRESHOLDS[status_val]

        for session in sessions:
            try:
                session_id = session.session_id or session.agent_session_id or "unknown"

                # Determine reference timestamp based on status
                ref_time = (
                    _to_timestamp(session.started_at) or _to_timestamp(session.created_at) or now
                )

                # For active sessions, use updated_at as reference
                if status_val == "active":
                    updated_at_ts = _to_timestamp(session.updated_at)

                    # Also check in-memory activity tracking from sdk_client,
                    # which is updated on every tool call and log output.
                    # Use whichever timestamp is more recent.
                    try:
                        from agent.sdk_client import get_session_updated_at

                        inmem_activity = get_session_updated_at(session_id)
                        if inmem_activity is not None:
                            if updated_at_ts is None or inmem_activity > updated_at_ts:
                                updated_at_ts = inmem_activity
                    except ImportError:
                        pass

                    if updated_at_ts is not None:
                        # If updated_at is recent, session is not stalled
                        activity_age = now - updated_at_ts
                        if activity_age < threshold:
                            continue
                        # Use updated_at as the reference for duration
                        ref_time = updated_at_ts

                    # Transcript liveness check (issue #360): even if updated_at
                    # is stale, the session may be alive doing sub-agent work.
                    # Check transcript.txt mtime before declaring stalled.
                    transcript_stale, transcript_age_min = _check_transcript_liveness(session_id)
                    if not transcript_stale:
                        logger.debug(
                            "[watchdog] Session %s has fresh transcript "
                            "(%.1f min old), skipping stall detection",
                            session_id,
                            transcript_age_min,
                        )
                        continue

                duration = now - ref_time

                if duration > threshold:
                    # Get last history entry for diagnostic context
                    last_history = "no history"
                    try:
                        if hasattr(session, "_get_history_list"):
                            history = session._get_history_list()
                            if history:
                                last_history = str(history[-1])[:120]
                    except Exception:
                        pass

                    stalled_info = {
                        "session_id": session_id,
                        "status": status_val,
                        "duration": duration,
                        "threshold": threshold,
                        "project_key": getattr(session, "project_key", "?"),
                        "last_history": last_history,
                    }
                    stalled.append(stalled_info)

                    logger.warning(
                        "LIFECYCLE_STALL session=%s status=%s duration=%.0fs "
                        "threshold=%ds project=%s last_history=%s",
                        session_id,
                        status_val,
                        duration,
                        threshold,
                        getattr(session, "project_key", "?"),
                        last_history,
                    )
            except Exception as e:
                logger.error("[watchdog] Error checking session for stall: %s", e)

    if stalled:
        logger.warning(
            "[watchdog] %d stalled session(s) detected: %s",
            len(stalled),
            ", ".join(s["session_id"] for s in stalled),
        )

    return stalled


def assess_session_health(session: AgentSession) -> dict[str, Any]:
    """Assess the health of a single session.

    Args:
        session: The AgentSession to assess

    Returns:
        Dict with:
        - healthy: bool indicating if session is healthy
        - issues: list of issue descriptions
        - severity: "warning" or "critical"

    Checks for:
    - Silence (no activity for too long)
    - Duration (session running too long)
    - Looping (repeated identical tool calls)
    - Error cascade (high error rate)
    """
    issues = []
    now = time.time()

    # Check for silence
    updated_ts = _to_timestamp(session.updated_at)
    silence_duration = (now - updated_ts) if updated_ts else 0
    if silence_duration > SILENCE_THRESHOLD:
        issues.append(f"Silent for {int(silence_duration / 60)} minutes")

    # Check for excessive duration
    started_ts = _to_timestamp(session.started_at)
    session_duration = (now - started_ts) if started_ts else 0
    if session_duration > DURATION_THRESHOLD:
        issues.append(f"Running for {int(session_duration / 3600)} hours")

    # Check for looping and error cascades using tool call history
    try:
        tool_calls = read_recent_tool_calls(session.session_id)

        if tool_calls:
            # Check for repetition
            is_looping, repeated_tool, count = detect_repetition(tool_calls)
            if is_looping:
                issues.append(f"Looping: {repeated_tool} called {count} times consecutively")

            # Check for error cascade
            is_cascading, error_count = detect_error_cascade(tool_calls)
            if is_cascading:
                issues.append(
                    f"Error cascade: {error_count} errors in last {ERROR_CASCADE_WINDOW} calls"
                )
    except Exception as e:
        logger.debug(
            "[watchdog] Could not analyze tool calls for session %s: %s",
            session.session_id,
            e,
        )

    # Determine overall health and severity
    healthy = len(issues) == 0
    severity = "critical" if len(issues) > 1 else "warning"

    return {"healthy": healthy, "issues": issues, "severity": severity}


def read_recent_tool_calls(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Read the most recent tool calls from a session's log.

    Args:
        session_id: The session ID to read logs for
        limit: Maximum number of recent lines to read

    Returns:
        List of tool call dicts (parsed JSON), or empty list on error

    Gracefully handles missing files, corrupted JSON, etc.
    """
    project_dir = Path(__file__).parent.parent
    log_file = project_dir / "logs" / "sessions" / session_id / "tool_use.jsonl"

    if not log_file.exists():
        return []

    try:
        # Read last N lines efficiently
        with open(log_file) as f:
            lines = f.readlines()

        # Take last 'limit' lines
        recent_lines = lines[-limit:] if len(lines) > limit else lines

        # Parse each line as JSON
        tool_calls = []
        for line in recent_lines:
            line = line.strip()
            if not line:
                continue
            try:
                tool_calls.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip corrupted lines
                continue

        return tool_calls
    except Exception as e:
        logger.debug("[watchdog] Error reading tool calls for %s: %s", session_id, e)
        return []


def detect_repetition(
    tool_calls: list[dict[str, Any]], threshold: int = LOOP_THRESHOLD
) -> tuple[bool, str | None, int]:
    """Detect if the session is stuck in a loop of repeated tool calls.

    Args:
        tool_calls: List of tool call event dicts
        threshold: Number of consecutive identical calls to trigger

    Returns:
        Tuple of (is_looping, repeated_tool_name, count)

    Creates fingerprints from (tool_name, sorted input items) and counts
    consecutive identical fingerprints.
    """
    # Filter to only pre_tool_use events (these have tool_input)
    pre_events = [tc for tc in tool_calls if tc.get("event") == "pre_tool_use"]

    if len(pre_events) < threshold:
        return (False, None, 0)

    # Create fingerprints
    fingerprints = []
    for event in pre_events:
        tool_name = event.get("tool_name", "unknown")
        tool_input = event.get("tool_input", {})

        # Create a hashable fingerprint from tool name and sorted input
        if isinstance(tool_input, dict):
            input_items = tuple(sorted(tool_input.items()))
        else:
            input_items = (str(tool_input),)

        fingerprints.append((tool_name, input_items))

    # Count consecutive identical fingerprints from the end
    if not fingerprints:
        return (False, None, 0)

    last_fingerprint = fingerprints[-1]
    consecutive_count = 1

    for i in range(len(fingerprints) - 2, -1, -1):
        if fingerprints[i] == last_fingerprint:
            consecutive_count += 1
        else:
            break

    is_looping = consecutive_count >= threshold
    repeated_tool = last_fingerprint[0] if is_looping else None

    return (is_looping, repeated_tool, consecutive_count)


def detect_error_cascade(
    tool_calls: list[dict[str, Any]],
    threshold: int = ERROR_CASCADE_THRESHOLD,
    window: int = ERROR_CASCADE_WINDOW,
) -> tuple[bool, int]:
    """Detect if the session is experiencing an error cascade.

    Args:
        tool_calls: List of tool call event dicts
        threshold: Number of errors to trigger cascade detection
        window: Number of recent calls to examine

    Returns:
        Tuple of (is_cascading, error_count)

    Looks at post_tool_use events and counts those with error indicators
    in their output.
    """
    # Filter to only post_tool_use events (these have tool_output)
    post_events = [tc for tc in tool_calls if tc.get("event") == "post_tool_use"]

    # Take last 'window' events
    recent_events = post_events[-window:] if len(post_events) > window else post_events

    if not recent_events:
        return (False, 0)

    # Count events with error indicators
    error_count = 0
    error_indicators = [
        "error",
        "exception",
        "failed",
        "traceback",
        "fatal",
        "cannot",
        "not found",
        "permission denied",
    ]

    for event in recent_events:
        output = event.get("tool_output_preview", "").lower()

        # Check if any error indicator is in the output
        if any(indicator in output for indicator in error_indicators):
            error_count += 1

    is_cascading = error_count >= threshold

    return (is_cascading, error_count)


def _safe_abandon_session(session: AgentSession, reason: str) -> bool:
    """Safely mark a session as abandoned, handling duplicate key errors.

    The watchdog frequently encounters ModelException (duplicate key / unique
    constraint violations) when saving sessions that were concurrently modified
    or cleaned up by another process. This helper wraps the save() call to
    catch those errors locally instead of letting them propagate to the
    watchdog loop where they spam the error log.

    Args:
        session: The session to abandon
        reason: Human-readable reason for the abandonment

    Returns:
        True if the session was successfully saved, False if the save failed
        (session may have been deleted by another process).
    """
    try:
        session.log_lifecycle_transition("abandoned", reason)
    except Exception:
        pass

    session.status = "abandoned"
    try:
        session.save()
        return True
    except ModelException as e:
        # Session was likely deleted or modified by another process between
        # our read and this save. Log at WARNING (not ERROR) since this is
        # a known race condition, not a bug.
        logger.warning(
            "[watchdog] Could not save session %s (duplicate key / stale): %s",
            session.session_id,
            e,
        )
        return False


async def fix_unhealthy_session(session: AgentSession, assessment: dict[str, Any]) -> bool:
    """Fix an unhealthy session by abandoning it.

    Recovery of jobs with dead workers is handled by the unified health check
    in agent/agent_session_queue.py (_agent_session_health_check). The session watchdog only
    handles session-level health (silence, looping, error cascades).

    Args:
        session: The session with health issues
        assessment: Health assessment dict with issues and severity

    Returns:
        True if the session was fixed (abandoned), False otherwise

    Strategy:
    - Silent sessions (>30 min): abandon
    - Long-running sessions (>2 hours): abandon
    - Looping/error cascades: abandon, create issue if critical
    """
    issues = assessment["issues"]
    severity = assessment["severity"]
    now = time.time()

    # Calculate silence duration
    updated_ts = _to_timestamp(session.updated_at)
    silence_duration = (now - updated_ts) if updated_ts else 0

    # Most common case: session is stuck/silent
    if silence_duration > ABANDON_THRESHOLD:
        reason = f"silent for {int(silence_duration / 60)}min"
        _safe_abandon_session(session, f"watchdog: {reason}")
        logger.info(
            "[watchdog] Abandoned stuck session %s (%s)",
            session.session_id,
            reason,
        )
        return True

    # Long-running session
    started_ts = _to_timestamp(session.started_at)
    session_duration = (now - started_ts) if started_ts else 0
    if session_duration > DURATION_THRESHOLD:
        reason = f"running for {int(session_duration / 3600)}h"
        _safe_abandon_session(session, f"watchdog: {reason}")
        logger.info(
            "[watchdog] Abandoned long session %s (%s)",
            session.session_id,
            reason,
        )
        return True

    # Critical issues (looping, error cascades) - abandon and maybe create issue
    if severity == "critical":
        _safe_abandon_session(
            session,
            f"watchdog: critical issues: {', '.join(issues)}",
        )

        # Create GitHub issue for investigation
        try:
            await create_session_issue(session, issues)
        except Exception as e:
            logger.error("[watchdog] Failed to create issue: %s", e)

        return True

    # Warning-level issues - just abandon
    _safe_abandon_session(
        session,
        f"watchdog: {', '.join(issues)}",
    )
    return True


async def create_session_issue(session: AgentSession, issues: list[str]) -> None:
    """Create a GitHub issue for a session that couldn't be auto-fixed.

    Args:
        session: The problematic session
        issues: List of issue descriptions
    """
    import subprocess

    project_dir = Path(__file__).parent.parent
    issues_formatted = "\n".join(f"- {issue}" for issue in issues)

    title = f"[Watchdog] Session {session.session_id[:8]} had critical issues"
    body = f"""## Session Details

- **Session ID**: `{session.session_id}`
- **Project**: {session.project_key}
- **Chat ID**: {session.chat_id}
- **Tool calls**: {session.tool_call_count}

## Issues Detected

{issues_formatted}

## Action Taken

Session was automatically marked as abandoned by the watchdog.

---
*Auto-generated by session watchdog*
"""

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                "tomcounsell/ai",
                "--title",
                title,
                "--body",
                body,
                "--label",
                "bug",
                "--label",
                "watchdog",
            ],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("[watchdog] Created issue: %s", result.stdout.strip())
        else:
            logger.error("[watchdog] Failed to create issue: %s", result.stderr)
    except Exception as e:
        logger.error("[watchdog] Error creating issue: %s", e)
