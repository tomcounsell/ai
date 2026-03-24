"""Watchdog health check for SDK agent sessions.

Registers as a PostToolUse hook that fires every CHECK_INTERVAL tool calls.
Reads the recent transcript and asks Haiku whether the agent is making
meaningful progress or is stuck in a loop.

Kill mechanism: PostToolUse hooks cannot stop CLI execution (continue_: False
is ignored). Instead, the watchdog sets watchdog_unhealthy on the AgentSession
model. The nudge loop in job_queue.py checks this field before auto-continuing.
When flagged unhealthy, the nudge loop delivers output to Telegram instead of
sending "Keep working".
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from claude_agent_sdk import HookContext, PostToolUseHookInput

from config.models import MODEL_FAST
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

# Health check fires every N tool uses
CHECK_INTERVAL = 20

# Track tool call count per session (in-memory, resets with process).
# Keyed by bridge session ID (VALOR_SESSION_ID env var) when available,
# falling back to Claude Code's internal session ID. See issue #374 Bug 2.
_tool_counts: dict[str, int] = {}


def _set_unhealthy(session_id: str, reason: str) -> None:
    """Flag a session as unhealthy on the AgentSession model."""
    try:
        from models.agent_session import AgentSession

        sessions = AgentSession.query.filter(session_id=session_id)
        if sessions:
            sessions[0].watchdog_unhealthy = reason
            sessions[0].save()
            logger.info(f"[health_check] Set unhealthy flag for {session_id}")
    except Exception as e:
        logger.error(f"[health_check] Failed to set unhealthy flag: {e}")


def is_session_unhealthy(session_id: str) -> str | None:
    """Check if a session has been flagged unhealthy by the watchdog.

    Called by the nudge loop in job_queue.py before auto-continuing.

    Returns:
        The reason string if unhealthy, None if healthy.
    """
    try:
        from models.agent_session import AgentSession

        sessions = AgentSession.query.filter(session_id=session_id)
        if sessions:
            return sessions[0].watchdog_unhealthy
        return None
    except Exception:
        return None


def clear_unhealthy(session_id: str) -> None:
    """Clear the unhealthy flag (e.g., when a session is manually restarted)."""
    try:
        from models.agent_session import AgentSession

        sessions = AgentSession.query.filter(session_id=session_id)
        if sessions:
            sessions[0].watchdog_unhealthy = None
            sessions[0].save()
    except Exception:
        pass


def reset_session_count(session_id: str) -> None:
    """Reset the tool call counter for a session.

    Called from sdk_client.py at query start to ensure continuation sessions
    start with a fresh count instead of inheriting stale counts from a
    prior (possibly unrelated) Claude Code session. See issue #374 Bug 2.

    Args:
        session_id: The bridge session ID (VALOR_SESSION_ID) to reset.
    """
    old_count = _tool_counts.pop(session_id, 0)
    if old_count > 0:
        logger.info(f"[health_check] Reset tool count for session {session_id} (was {old_count})")


JUDGE_PROMPT = """\
You are a watchdog monitoring an AI coding agent session. Based on the recent \
activity log below, determine if the agent is:
1. Making meaningful progress toward its goal
2. Stuck in a repetitive loop (same tools, same patterns, similar errors)
3. Exploring without converging (unbounded research with no clear deliverable)

{session_context}\
Recent activity (last {count} tool calls):
{activity}

Respond with ONLY a JSON object, no other text:
{{"healthy": true/false, "reason": "brief explanation"}}\
"""


def _write_activity_stream(
    session_id: str, tool_name: str, key_args: str, tool_call_count: int
) -> None:
    """Append one JSONL line to the activity stream for this session.

    Writes to logs/sessions/{session_id}/activity.jsonl. Creates the
    directory lazily on first write. Zero API calls, zero cost.

    Designed as a feed that SubconsciousMemory can consume later.

    Args:
        session_id: The bridge session ID.
        tool_name: Name of the tool that was called.
        key_args: Brief summary of the tool's input.
        tool_call_count: Current tool call count for this session.
    """
    import time

    try:
        session_dir = Path("logs/sessions") / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        activity_file = session_dir / "activity.jsonl"

        entry = json.dumps(
            {
                "ts": time.time(),
                "tool": tool_name,
                "args": key_args,
                "n": tool_call_count,
            }
        )
        with open(activity_file, "a") as f:
            f.write(entry + "\n")
    except Exception:
        pass  # Never block agent on activity logging


def _get_session_context(session_id: str) -> str:
    """Build session context preamble for the health check judge prompt.

    Reads session_type and message_text from AgentSession. Extracts gh CLI
    commands from recent tool calls for PM session context.

    Returns an empty string if no context is available.
    """
    try:
        from models.agent_session import AgentSession

        sessions = AgentSession.query.filter(session_id=session_id)
        if not sessions:
            return ""

        s = sessions[0]
        session_type = s.session_type or "unknown"
        message_text = (s.message_text or "")[:200]

        context = f"This is a {session_type} session working on: {message_text}\n\n"

        # Extract gh CLI commands from activity stream for additional context
        gh_commands = _extract_gh_commands(session_id)
        if gh_commands:
            context += f"Recent GitHub CLI commands: {', '.join(gh_commands[:5])}\n\n"

        return context
    except Exception:
        return ""


def _extract_gh_commands(session_id: str) -> list[str]:
    """Extract gh CLI commands from the activity stream.

    Reads the activity JSONL file and finds Bash tool calls containing 'gh '.
    Returns a list of command summaries (high-signal for PM sessions).
    """
    try:
        activity_file = Path("logs/sessions") / session_id / "activity.jsonl"
        if not activity_file.exists():
            return []

        commands = []
        for line in activity_file.read_text().strip().splitlines()[-20:]:
            try:
                entry = json.loads(line)
                if entry.get("tool") == "Bash" and "gh " in entry.get("args", ""):
                    commands.append(entry["args"][:80])
            except json.JSONDecodeError:
                continue
        return commands
    except Exception:
        return []


def _get_api_key() -> str:
    """Resolve Anthropic API key from env or shared .env files."""

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


async def _judge_health(
    activity: str, session_context: str = ""
) -> dict[str, Any]:
    """Ask Haiku to judge whether the agent is healthy.

    Args:
        activity: Formatted tool call activity summary.
        session_context: Optional session context preamble (session_type + task).
    """
    import anthropic

    api_key = _get_api_key()
    if not api_key:
        logger.warning("Health check: no API key available, skipping")
        return {"healthy": True, "reason": "no API key for health check"}

    prompt = JUDGE_PROMPT.format(
        count=CHECK_INTERVAL,
        activity=activity,
        session_context=session_context,
    )

    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=MODEL_FAST,
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
            target_agent=msg.get("target_agent"),
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
            # PostToolUse can't enforce continue_: False, but inject a strong
            # stop directive via additionalContext so Claude sees it.
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        f"ABORT from {sender}: {msg.get('text', 'stop')}. "
                        "You MUST stop immediately. Output a brief summary of "
                        "what you found and end your turn. No more tool calls."
                    ),
                },
            }

    # Combine all steering messages into one injection
    parts = []
    for msg in messages:
        sender = msg.get("sender", "supervisor")
        text = msg.get("text", "")
        target = msg.get("target_agent")
        prefix = f"[{sender}]"
        if target:
            prefix = f"[{sender} -> @{target}]"
        parts.append(f"{prefix}: {text}")

    combined = "\n".join(parts)
    logger.info(f"[steering] Injecting {len(messages)} message(s) into session {session_id}")

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
        logger.error(f"[steering] Failed to inject message: {e} — re-pushing to preserve")
        # Re-push so messages aren't lost on injection failure
        _repush_messages(session_id, messages)

    return {"continue_": True}


async def watchdog_hook(
    input_data: PostToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """PostToolUse hook — fires every tool call.

    1. Check steering queue (every call — lightweight Redis LPOP)
    2. Update session tracking in Redis (every call)
    3. Run health check via Haiku judge (every CHECK_INTERVAL calls)
    """
    # Bug 2 fix (issue #374): Use VALOR_SESSION_ID (bridge session ID) for
    # count tracking instead of Claude Code's internal session ID. This prevents
    # stale counts from a prior unrelated session from triggering the watchdog
    # prematurely on continuation sessions.
    valor_session_id = os.environ.get("VALOR_SESSION_ID")
    session_id = valor_session_id or input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path", "")

    # === STEERING CHECK (every tool call) ===
    try:
        steering_result = await _handle_steering(session_id)
        if steering_result is not None:
            return steering_result
    except Exception as e:
        logger.error(f"[steering] Error in steering check: {e}")
        # Never block due to steering bug

    # Increment counter
    _tool_counts[session_id] = _tool_counts.get(session_id, 0) + 1
    count = _tool_counts[session_id]

    # === ACTIVITY STREAM (every tool call) ===
    # Extract tool name and summarize input for the activity log
    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})
    key_args = _summarize_input(tool_name, tool_input) if isinstance(tool_input, dict) else ""
    _write_activity_stream(session_id, tool_name, key_args, count)

    # Update session tracking in Redis (best-effort, every call)
    try:
        import time

        from models.agent_session import AgentSession

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

    logger.info(f"[health_check] Running health check at tool call #{count} (session={session_id})")

    try:
        activity = _read_recent_activity(transcript_path)
        # Enrich with session context for more accurate health verdicts
        session_context = _get_session_context(session_id)
        result = await _judge_health(activity, session_context=session_context)

        healthy = result.get("healthy", True)
        reason = result.get("reason", "no reason given")

        # Log gh commands alongside verdict for PM session visibility
        gh_cmds = _extract_gh_commands(session_id)
        gh_info = f" gh_commands={gh_cmds}" if gh_cmds else ""

        if healthy:
            logger.info(f"[health_check] Healthy at #{count}: {reason}{gh_info}")
            return {"continue_": True}
        else:
            logger.warning(f"[health_check] UNHEALTHY at #{count}: {reason}{gh_info}")
            # Two-pronged kill:
            # 1. Set flag on AgentSession so nudge loop won't auto-continue
            # 2. Inject additionalContext telling Claude to stop immediately
            _set_unhealthy(session_id, reason)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "WATCHDOG ALERT: You have been flagged as stuck in a "
                        "repetitive loop. STOP what you are doing. Output a brief "
                        "summary of what you found and what blocked you, then end "
                        "your turn. Do NOT make any more tool calls."
                    ),
                },
            }

    except Exception as e:
        # Never block due to a watchdog bug
        logger.error(f"[health_check] Error during health check: {e}")
        return {"continue_": True}
