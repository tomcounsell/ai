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
from pathlib import Path
from typing import Any

from popoto.exceptions import ModelException

from models.agent_session import AgentSession

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
STALL_THRESHOLD_ACTIVE = int(os.environ.get("STALL_TIMEOUT_SECONDS", 600))  # 10 min default

STALL_THRESHOLDS = {
    "pending": STALL_THRESHOLD_PENDING,
    "running": STALL_THRESHOLD_RUNNING,
    "active": STALL_THRESHOLD_ACTIVE,
}

# Stall retry configuration (configurable via .env)
# STALL_MAX_RETRIES: maximum number of automatic retries before abandoning + notifying human
STALL_MAX_RETRIES = int(os.environ.get("STALL_MAX_RETRIES", 3))
# STALL_BACKOFF_BASE_SECONDS: base delay for exponential backoff (delay = base * 2^retry_count)
STALL_BACKOFF_BASE = int(os.environ.get("STALL_BACKOFF_BASE_SECONDS", 10))
# STALL_BACKOFF_MAX_SECONDS: ceiling on backoff delay to prevent unreasonable waits
STALL_BACKOFF_MAX = int(os.environ.get("STALL_BACKOFF_MAX_SECONDS", 300))


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
            # on it every cycle. See docs/features/coaching-loop.md "Related Guards".
            try:
                session.status = "failed"
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
    how long they've been in that state. Uses last_transition_at (falling back
    to started_at or created_at) as the reference timestamp.

    For active sessions, also checks last_activity -- if last_activity is recent
    (within the active threshold), the session is not considered stalled.

    Thresholds:
        - pending > 300s (5 min) = stalled
        - running > 2700s (45 min) = stalled
        - active with no recent last_activity > 600s (10 min) = stalled

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
                session_id = session.session_id or session.job_id or "unknown"

                # Determine reference timestamp based on status
                ref_time = (
                    session.last_transition_at or session.started_at or session.created_at or now
                )

                # For active sessions, use last_activity as reference
                if status_val == "active":
                    last_activity = session.last_activity
                    if last_activity is not None:
                        # If last_activity is recent, session is not stalled
                        activity_age = now - last_activity
                        if activity_age < threshold:
                            continue
                        # Use last_activity as the reference for duration
                        ref_time = last_activity

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
    silence_duration = now - session.last_activity
    if silence_duration > SILENCE_THRESHOLD:
        issues.append(f"Silent for {int(silence_duration / 60)} minutes")

    # Check for excessive duration
    session_duration = now - session.started_at
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


def _compute_stall_backoff(retry_count: int) -> float:
    """Compute exponential backoff delay for stall retry.

    Formula: min(STALL_BACKOFF_BASE * 2^retry_count, STALL_BACKOFF_MAX)

    Progression with defaults (base=10, max=300):
        retry 0: 10s
        retry 1: 20s
        retry 2: 40s
        retry 3+: capped at 300s

    Args:
        retry_count: Current retry attempt (0-based). None is treated as 0.

    Returns:
        Backoff delay in seconds.
    """
    # Treat None as 0 for legacy sessions without retry_count
    if retry_count is None:
        retry_count = 0
    # Guard against negative values
    retry_count = max(0, retry_count)
    delay = STALL_BACKOFF_BASE * (2**retry_count)
    return min(delay, STALL_BACKOFF_MAX)


async def _kill_stalled_worker(project_key: str) -> bool:
    """Kill the worker task for a stalled session's project.

    Cancels the asyncio task from _active_workers, which should terminate
    the SDK subprocess. Waits briefly for cleanup.

    Args:
        project_key: The project key whose worker should be killed.

    Returns:
        True if a worker was found and cancelled, False otherwise.
    """
    from agent.job_queue import _active_workers

    worker = _active_workers.get(project_key)
    if worker is None or worker.done():
        logger.info(
            "[stall-retry] No active worker for project %s (already dead/missing)",
            project_key,
        )
        return False

    logger.info("[stall-retry] Cancelling worker task for project %s", project_key)
    worker.cancel()

    # Wait briefly for the task to clean up
    try:
        await asyncio.wait_for(asyncio.shield(worker), timeout=5.0)
    except (TimeoutError, asyncio.CancelledError, Exception):
        pass  # Expected -- task was cancelled or timed out

    # Remove from active workers so _ensure_worker creates a fresh one
    _active_workers.pop(project_key, None)
    logger.info("[stall-retry] Worker for project %s cancelled and removed", project_key)
    return True


async def _enqueue_stall_retry(
    session: AgentSession,
    stall_reason: str,
) -> bool:
    """Re-enqueue a stalled session for retry with context.

    Uses the same delete-and-recreate pattern as _enqueue_continuation()
    in job_queue.py. Increments retry_count, sets the stall reason, and
    re-enqueues as pending with high priority.

    Args:
        session: The stalled AgentSession to retry.
        stall_reason: Human-readable reason why the session stalled.

    Returns:
        True if successfully re-enqueued, False on error.
    """
    from agent.job_queue import _ensure_worker, _extract_job_fields

    try:
        retry_count = (session.retry_count or 0) + 1
        session_id = session.session_id or session.job_id or "unknown"

        # Build retry context message
        retry_context = (
            f"[STALL RETRY {retry_count}/{STALL_MAX_RETRIES}] "
            f"Session stalled: {stall_reason}. "
            f"Automatically retrying with context preserved. "
            f"Previous message: {(session.message_text or '')[:200]}"
        )

        # Extract all fields, delete old record, recreate with retry context
        fields = _extract_job_fields(session)
        session.delete()

        fields["status"] = "pending"
        fields["priority"] = "high"
        fields["retry_count"] = retry_count
        fields["last_stall_reason"] = stall_reason
        fields["message_text"] = retry_context
        fields["started_at"] = None  # Reset for re-processing

        new_session = AgentSession.create(**fields)

        # Log lifecycle transition on the new session
        try:
            new_session.log_lifecycle_transition(
                "pending",
                f"stall retry {retry_count}/{STALL_MAX_RETRIES}: {stall_reason}",
            )
        except Exception:
            pass  # Non-fatal

        project_key = fields.get("project_key", "?")
        _ensure_worker(project_key)

        logger.info(
            "[stall-retry] Re-enqueued session %s as %s (retry %d/%d, reason: %s)",
            session_id,
            new_session.job_id,
            retry_count,
            STALL_MAX_RETRIES,
            stall_reason,
        )
        return True

    except Exception as e:
        logger.error(
            "[stall-retry] Failed to re-enqueue session %s: %s",
            getattr(session, "session_id", "?"),
            e,
            exc_info=True,
        )
        return False


async def _notify_stall_failure(session: AgentSession, stall_reason: str) -> None:
    """Send Telegram notification when stall retries are exhausted.

    Uses the registered send callback from job_queue to route the notification
    to the original chat where the session was initiated.

    Args:
        session: The failed AgentSession.
        stall_reason: The reason for the final stall.
    """
    from agent.job_queue import _send_callbacks

    session_id = session.session_id or session.job_id or "unknown"
    retry_count = session.retry_count or 0
    chat_id = getattr(session, "chat_id", None)
    message_id = getattr(session, "message_id", None)
    project_key = getattr(session, "project_key", "?")

    notification = (
        f"Session stalled and retries exhausted.\n\n"
        f"Session: {session_id[:12]}\n"
        f"Retries: {retry_count}/{STALL_MAX_RETRIES}\n"
        f"Last stall reason: {stall_reason}\n"
        f"Project: {project_key}\n\n"
        f"The session has been marked as failed. "
        f"Please re-send your request to try again."
    )

    if chat_id and project_key in _send_callbacks:
        try:
            send_cb = _send_callbacks[project_key]
            await send_cb(str(chat_id), notification, message_id, session)
            logger.info(
                "[stall-retry] Sent failure notification for session %s to chat %s",
                session_id,
                chat_id,
            )
        except Exception as e:
            logger.error(
                "[stall-retry] Failed to send notification for session %s: %s",
                session_id,
                e,
            )
    else:
        logger.warning(
            "[stall-retry] No send callback for project %s, cannot notify chat %s "
            "about stall failure for session %s",
            project_key,
            chat_id,
            session_id,
        )


async def fix_unhealthy_session(session: AgentSession, assessment: dict[str, Any]) -> bool:
    """Fix an unhealthy session. Retry if possible, otherwise abandon.

    Args:
        session: The session with health issues
        assessment: Health assessment dict with issues and severity

    Returns:
        True if the session was fixed (retried or abandoned), False otherwise

    Strategy:
    - Silent sessions (>30 min): attempt stall retry if retries remain, else abandon
    - Long-running sessions (>2 hours): attempt stall retry if retries remain, else abandon
    - Looping/error cascades: mark as abandoned, create issue if critical
    """
    issues = assessment["issues"]
    severity = assessment["severity"]
    now = time.time()

    # Calculate silence duration
    silence_duration = now - session.last_activity

    # Get current retry count (treat None as 0 for legacy sessions)
    retry_count = session.retry_count if session.retry_count is not None else 0

    # Most common case: session is stuck/silent
    if silence_duration > ABANDON_THRESHOLD:
        stall_reason = f"silent for {int(silence_duration / 60)}min"

        # Attempt retry if retries remain
        if retry_count < STALL_MAX_RETRIES:
            # Kill the stalled worker before retrying
            await _kill_stalled_worker(session.project_key)

            # Compute and wait for backoff
            backoff = _compute_stall_backoff(retry_count)
            logger.info(
                "[stall-retry] Backing off %.1fs before retry %d/%d for session %s",
                backoff,
                retry_count + 1,
                STALL_MAX_RETRIES,
                session.session_id,
            )
            await asyncio.sleep(backoff)

            # Re-enqueue with retry context
            retried = await _enqueue_stall_retry(session, stall_reason)
            if retried:
                return True
            # If re-enqueue failed, fall through to abandon

        # Retries exhausted or re-enqueue failed -- abandon and notify
        saved = _safe_abandon_session(session, f"watchdog: {stall_reason} (retries exhausted)")
        if saved:
            logger.info(
                "[watchdog] Abandoned stuck session %s (silent for %d min, "
                "%d/%d retries exhausted)",
                session.session_id,
                int(silence_duration / 60),
                retry_count,
                STALL_MAX_RETRIES,
            )
            await _notify_stall_failure(session, stall_reason)
        return True

    # Long-running session
    session_duration = now - session.started_at
    if session_duration > DURATION_THRESHOLD:
        stall_reason = f"running for {int(session_duration / 3600)}h"

        # Attempt retry if retries remain
        if retry_count < STALL_MAX_RETRIES:
            await _kill_stalled_worker(session.project_key)
            backoff = _compute_stall_backoff(retry_count)
            logger.info(
                "[stall-retry] Backing off %.1fs before retry %d/%d for session %s",
                backoff,
                retry_count + 1,
                STALL_MAX_RETRIES,
                session.session_id,
            )
            await asyncio.sleep(backoff)
            retried = await _enqueue_stall_retry(session, stall_reason)
            if retried:
                return True

        saved = _safe_abandon_session(session, f"watchdog: {stall_reason} (retries exhausted)")
        if saved:
            logger.info(
                "[watchdog] Abandoned long session %s (running for %d hours, "
                "%d/%d retries exhausted)",
                session.session_id,
                int(session_duration / 3600),
                retry_count,
                STALL_MAX_RETRIES,
            )
            await _notify_stall_failure(session, stall_reason)
        return True

    # Critical issues (looping, error cascades) - abandon and maybe create issue
    # These are not retried because the stall is likely deterministic
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

    # Warning-level issues - just abandon for now
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
