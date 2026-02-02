"""Watchdog health check for SDK agent sessions.

Registers as a PostToolUse hook that fires every CHECK_INTERVAL tool calls.
Reads the recent transcript and asks Haiku whether the agent is making
meaningful progress or is stuck in a loop. Returns a block decision if unhealthy.
"""

from __future__ import annotations

import json
import logging
import os
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
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if key:
        return key
    # Fallback: read from shared env files
    for env_path in [
        Path.home() / "src" / ".env",
        Path.home() / "src" / "ai" / ".env",
    ]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    return ""


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
    import httpx

    api_key = _get_api_key()
    if not api_key:
        logger.warning("Health check: no API key available, skipping")
        return {"healthy": True, "reason": "no API key for health check"}

    prompt = JUDGE_PROMPT.format(count=CHECK_INTERVAL, activity=activity)

    async with httpx.AsyncClient(timeout=15.0) as http:
        resp = await http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # Extract text from response
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]

    # Parse JSON from response
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Health check: could not parse judge response: {text}")
        return {"healthy": True, "reason": f"unparseable judge response: {text[:80]}"}


async def watchdog_hook(input_data: Any, tool_use_id: str | None, context: Any) -> dict[str, Any]:
    """PostToolUse hook that runs a health check every CHECK_INTERVAL tool calls."""
    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path", "")

    # Increment counter
    _tool_counts[session_id] = _tool_counts.get(session_id, 0) + 1
    count = _tool_counts[session_id]

    if count % CHECK_INTERVAL != 0:
        return {"continue_": True}

    logger.info(f"[health_check] Running health check at tool call #{count} (session={session_id})")

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
