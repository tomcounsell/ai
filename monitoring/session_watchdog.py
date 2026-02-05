"""Session watchdog - detect stuck, looping, or failing agent sessions.

Monitors active agent sessions for signs of distress:
- Silent sessions (no activity for extended period)
- Looping behavior (repeated identical tool calls)
- Error cascades (high error rate in recent activity)
- Excessively long sessions

This is a pattern-matching heuristic system that runs periodically
and generates alerts without modifying session state.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from models.sessions import AgentSession

logger = logging.getLogger(__name__)

# Watchdog configuration constants
WATCHDOG_INTERVAL = 300  # 5 minutes in seconds
SILENCE_THRESHOLD = 600  # 10 minutes
LOOP_THRESHOLD = 5  # identical tool calls to trigger
ERROR_CASCADE_THRESHOLD = 5  # errors in last 20 calls
ERROR_CASCADE_WINDOW = 20
DURATION_THRESHOLD = 7200  # 2 hours
ALERT_COOLDOWN = 1800  # 30 minutes between alerts for same session

# Module-level state for alert cooldowns
_alert_cooldowns: dict[str, float] = {}


async def watchdog_loop(telegram_client=None) -> None:
    """Run the watchdog monitoring loop indefinitely.

    Args:
        telegram_client: Optional Telegram client for sending alerts

    This function never returns - it runs forever checking sessions
    at regular intervals. All exceptions are caught and logged to
    prevent the watchdog from crashing.
    """
    logger.info("[watchdog] Session watchdog started (interval=%ds)", WATCHDOG_INTERVAL)

    while True:
        try:
            await check_all_sessions(telegram_client)
        except Exception as e:
            logger.error("[watchdog] Error in watchdog loop: %s", e, exc_info=True)

        await asyncio.sleep(WATCHDOG_INTERVAL)


async def check_all_sessions(telegram_client=None) -> None:
    """Check all active sessions for health issues.

    Args:
        telegram_client: Optional Telegram client for sending alerts

    Queries all active sessions and assesses their health, sending
    alerts for any sessions showing signs of distress.
    """
    try:
        active_sessions = list(AgentSession.query.filter(status="active"))
    except Exception as e:
        logger.error("[watchdog] Failed to query active sessions: %s", e)
        return

    healthy_count = 0
    issue_count = 0

    for session in active_sessions:
        try:
            assessment = assess_session_health(session)

            if assessment["healthy"]:
                healthy_count += 1
            else:
                issue_count += 1
                logger.warning(
                    "[watchdog] Session %s has issues: %s (severity: %s)",
                    session.session_id,
                    ", ".join(assessment["issues"]),
                    assessment["severity"],
                )
                await send_health_alert(
                    telegram_client,
                    session,
                    assessment["issues"],
                    assessment["severity"],
                )
        except Exception as e:
            logger.error(
                "[watchdog] Error assessing session %s: %s",
                session.session_id,
                e,
                exc_info=True,
            )

    logger.info(
        "[watchdog] Checked %d active sessions: %d healthy, %d with issues",
        len(active_sessions),
        healthy_count,
        issue_count,
    )


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
                issues.append(
                    f"Looping: {repeated_tool} called {count} times consecutively"
                )

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


async def send_health_alert(
    telegram_client, session: AgentSession, issues: list[str], severity: str
) -> None:
    """Send a health alert for a problematic session.

    Args:
        telegram_client: Telegram client for sending alerts
        session: The session with health issues
        issues: List of issue descriptions
        severity: "warning" or "critical"

    Sends alert via Telegram if client is available, with formatted message
    including session details and issue list. Falls back to logging only.

    Respects cooldown period to avoid alert spam.
    """
    # Check cooldown
    now = time.time()
    last_alert = _alert_cooldowns.get(session.session_id, 0)

    if now - last_alert < ALERT_COOLDOWN:
        logger.debug(
            "[watchdog] Suppressing alert for %s (cooldown active)", session.session_id
        )
        return

    # Update cooldown
    _alert_cooldowns[session.session_id] = now

    # Format the alert message
    severity_emoji = "🚨" if severity == "critical" else "⚠️"
    session_id_short = session.session_id[:8]

    # Calculate duration in human-readable format
    duration_seconds = now - session.started_at
    hours = int(duration_seconds // 3600)
    minutes = int((duration_seconds % 3600) // 60)

    if hours > 0:
        duration_str = f"{hours}h {minutes}m"
    else:
        duration_str = f"{minutes}m"

    # Format issues as bulleted list
    issues_formatted = "\n".join(f"• {issue}" for issue in issues)

    message = f"""{severity_emoji} Session Health Alert

Session: {session_id_short}
Project: {session.project_key}
Duration: {duration_str}
Tool calls: {session.tool_call_count}

Issues:
{issues_formatted}"""

    # Log the alert
    logger.warning(
        "[watchdog] ALERT [%s] Session %s (chat_id=%s): %s",
        severity.upper(),
        session.session_id,
        session.chat_id,
        ", ".join(issues),
    )

    # Send via Telegram if client available
    if telegram_client and session.chat_id:
        try:
            chat_id = int(session.chat_id)
            await telegram_client.send_message(chat_id, message)
            logger.info(
                "[watchdog] Sent alert to chat %s for session %s",
                chat_id,
                session.session_id,
            )
        except Exception as e:
            logger.error(
                "[watchdog] Failed to send Telegram alert for session %s: %s",
                session.session_id,
                e,
                exc_info=True,
            )
