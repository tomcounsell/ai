"""SubagentStop hook: logs subagent completion and injects SDLC pipeline state.

Stage tracking and GitHub comments for dev sessions run via the CLI harness
are handled by the worker's post-completion handler (_handle_dev_session_completion
in agent/agent_session_queue.py). This hook only logs subagent completion for
SDK-path subagents (builder, validator, code-reviewer).

See docs/features/harness-abstraction.md for the full design."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import HookContext, SubagentStopHookInput

logger = logging.getLogger(__name__)


def _extract_outcome_summary(input_data: dict) -> str:
    """Extract a brief outcome summary from the subagent's return value.

    Looks for common result patterns in the input_data dict and returns
    a truncated summary string. Returns a sensible default if no
    meaningful outcome can be extracted.

    Args:
        input_data: The SubagentStopHookInput dict.

    Returns:
        A brief outcome summary string (max 200 chars).
    """
    # Try common result fields
    for key in ("result", "output", "response", "summary", "message"):
        value = input_data.get(key)
        if value and isinstance(value, str):
            return value[:200]

    # Try to extract from nested result
    result = input_data.get("result")
    if isinstance(result, dict):
        for key in ("text", "message", "summary", "output"):
            value = result.get(key)
            if value and isinstance(value, str):
                return value[:200]

    return "completed (no detailed outcome available)"


async def subagent_stop_hook(
    input_data: SubagentStopHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Log when a subagent finishes execution.

    Logs the agent_type, agent_id, and outcome summary for all subagents.
    Dev session stage tracking is handled by the worker post-completion handler
    (_handle_dev_session_completion) in the worker post-completion handler.
    """
    agent_type = input_data.get("agent_type", "unknown")
    agent_id = input_data.get("agent_id", "unknown")

    outcome = _extract_outcome_summary(input_data)
    logger.info(
        f"[subagent_stop] Subagent completed: agent_type={agent_type}, "
        f"agent_id={agent_id}, outcome={outcome}"
    )

    return {}
