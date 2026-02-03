"""Watchdog health check for SDK agent sessions.

Registers as a PostToolUse hook that fires every CHECK_INTERVAL tool calls.
Reads the recent transcript and asks Haiku whether the agent is making
meaningful progress or is stuck in a loop. Returns a block decision if unhealthy.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Health check fires every N tool uses
CHECK_INTERVAL = 20

# Track tool call count per session (in-memory, resets with process)
_tool_counts: dict[str, int] = {}

JUDGE_PROMPT = """\
You are a watchdog monitoring an AI coding agent session. Based on the recent \
activity log below, determine if the agent is:
1. Making meaningful progress toward its goal
2. Stuck in a repetitive loop (same tools, same patterns, similar errors)
3. Exploring without converging (unbounded research with no clear deliverable)

Recent activity (last {count} tool calls):
{activity}

Respond with ONLY a JSON object, no other text:
{{"healthy": true/false, "reason": "brief explanation"}}\
"""


def _get_api_key() -> str:
    """Resolve Anthropic API key from env or shared .env files."""
    from utils.api_keys import get_anthropic_api_key

    return get_anthropic_api_key()


def _read_recent_activity(transcript_path: str, max_entries: int = 30) -> str:
    """Read the last N lines from a transcript JSONL and summarize tool activity."""
    path = Path(transcript_path)
    if not path.exists():
        return "(transcript not found)"

    lines = path.read_text().strip().splitlines()
    # Take the tail
    recent = lines[-max_entries:] if len(lines) > max_entries else lines

    tool_calls: list[str] = []
    for line in recent:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if obj.get("type") != "assistant":
            continue

        content = obj.get("message", {}).get("content", [])
        for block in content:
            if block.get("type") == "tool_use":
                tool_name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                # Summarize the input briefly
                summary = _summarize_input(tool_name, tool_input)
                tool_calls.append(f"- {tool_name}: {summary}")

    if not tool_calls:
        return "(no tool calls found in recent transcript)"

    return "\n".join(tool_calls[-CHECK_INTERVAL:])


def _summarize_input(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Create a brief summary of a tool input for the judge."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:120] + ("..." if len(cmd) > 120 else "")
    if tool_name in ("Read", "Write", "Edit"):
        path = tool_input.get("file_path", tool_input.get("path", ""))
        return path
    if tool_name == "Grep":
        pattern = tool_input.get("pattern", "")
        return f'pattern="{pattern}"'
    if tool_name == "Glob":
        pattern = tool_input.get("pattern", "")
        return f'pattern="{pattern}"'
    if tool_name == "WebFetch":
        url = tool_input.get("url", "")
        return url[:100]
    if tool_name == "Task":
        desc = tool_input.get("description", "")
        return desc[:80]
    # Generic fallback
    text = json.dumps(tool_input)
    return text[:100] + ("..." if len(text) > 100 else "")


async def _judge_health(activity: str) -> dict[str, Any]:
    """Ask Haiku to judge whether the agent is healthy."""
    import anthropic

    api_key = _get_api_key()
    if not api_key:
        logger.warning("Health check: no API key available, skipping")
        return {"healthy": True, "reason": "no API key for health check"}

    prompt = JUDGE_PROMPT.format(count=CHECK_INTERVAL, activity=activity)

    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text if response.content else ""

    # Strip markdown code fences (Haiku often wraps JSON in ```json ... ```)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0] if "\n" in text else text[:-3]
    text = text.strip()

    # Parse JSON from response
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Health check: could not parse judge response: {text}")
        return {"healthy": True, "reason": f"unparseable judge response: {text[:80]}"}


def _repush_messages(session_id: str, messages: list[dict]) -> None:
    """Re-push consumed messages back to the steering queue to prevent loss."""
    from agent.steering import push_steering_message

    for msg in messages:
        push_steering_message(
            session_id,
            msg.get("text", ""),
            msg.get("sender", "unknown"),
            is_abort=msg.get("is_abort", False),
        )
    logger.info(f"[steering] Re-pushed {len(messages)} message(s) to {session_id}")


async def _handle_steering(session_id: str) -> dict[str, Any] | None:
    """Check the steering queue and handle any pending messages.

    Returns a hook result dict if steering action was taken, None otherwise.
    This runs on EVERY tool call (lightweight Redis LPOP).
    """
    from agent.steering import pop_all_steering_messages

    messages = pop_all_steering_messages(session_id)
    if not messages:
        return None

    # Check for abort signal first
    for msg in messages:
        if msg.get("is_abort"):
            sender = msg.get("sender", "supervisor")
            logger.warning(f"[steering] ABORT from {sender} for session {session_id}")
            return {
                "decision": "block",
                "continue_": False,
                "stopReason": f"Aborted by {sender}: {msg.get('text', 'stop')}",
            }

    # Combine all steering messages into one injection
    parts = []
    for msg in messages:
        sender = msg.get("sender", "supervisor")
        text = msg.get("text", "")
        parts.append(f"[{sender}]: {text}")

    combined = "\n".join(parts)
    logger.info(
        f"[steering] Injecting {len(messages)} message(s) into session {session_id}"
    )

    # Get the active SDK client and inject the steering message
    try:
        from agent.sdk_client import get_active_client

        client = get_active_client(session_id)
        if client:
            await client.interrupt()
            await client.query(
                f"STEERING MESSAGE FROM SUPERVISOR (mid-execution update):\n\n{combined}"
            )
            logger.info(f"[steering] Successfully injected into session {session_id}")
        else:
            logger.warning(
                f"[steering] No active client for session {session_id}, "
                f"re-pushing messages for next session"
            )
            _repush_messages(session_id, messages)
    except Exception as e:
        logger.error(
            f"[steering] Failed to inject message: {e} — re-pushing to preserve"
        )
        # Re-push so messages aren't lost on injection failure
        _repush_messages(session_id, messages)

    return {"continue_": True}


async def watchdog_hook(
    input_data: Any, tool_use_id: str | None, context: Any
) -> dict[str, Any]:
    """PostToolUse hook — fires every tool call.

    1. Check steering queue (every call — lightweight Redis LPOP)
    2. Update session tracking in Redis (every call)
    3. Run health check via Haiku judge (every CHECK_INTERVAL calls)
    """
    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path", "")

    # === STEERING CHECK (every tool call) ===
    try:
        steering_result = await _handle_steering(session_id)
        if steering_result is not None:
            # Steering took action — return its result
            # (either abort or continue with injected message)
            if not steering_result.get("continue_", True):
                return steering_result
            # If steering injected a message but wants to continue,
            # still do the rest of the hook (tracking, health check)
    except Exception as e:
        logger.error(f"[steering] Error in steering check: {e}")
        # Never block due to steering bug

    # Increment counter
    _tool_counts[session_id] = _tool_counts.get(session_id, 0) + 1
    count = _tool_counts[session_id]

    # Update session tracking in Redis (best-effort, every call)
    try:
        import time

        from models.sessions import AgentSession

        sessions = AgentSession.query.filter(session_id=session_id)
        if sessions:
            s = sessions[0]
            s.tool_call_count = count
            s.last_activity = time.time()
            s.save()
    except Exception:
        pass  # Non-fatal: don't let tracking break the agent

    if count % CHECK_INTERVAL != 0:
        return {"continue_": True}

    logger.info(
        f"[health_check] Running health check at tool call #{count} (session={session_id})"
    )

    try:
        activity = _read_recent_activity(transcript_path)
        result = await _judge_health(activity)

        healthy = result.get("healthy", True)
        reason = result.get("reason", "no reason given")

        if healthy:
            logger.info(f"[health_check] Healthy at #{count}: {reason}")
            return {"continue_": True}
        else:
            logger.warning(f"[health_check] UNHEALTHY at #{count}: {reason}")
            return {
                "decision": "block",
                "continue_": False,
                "stopReason": f"Watchdog: {reason}",
            }

    except Exception as e:
        # Never block due to a watchdog bug
        logger.error(f"[health_check] Error during health check: {e}")
        return {"continue_": True}
