"""SubagentStop hook: logs subagent completion."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import HookContext, SubagentStopHookInput

logger = logging.getLogger(__name__)


async def subagent_stop_hook(
    input_data: SubagentStopHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Log when a subagent finishes execution.

    Captures the agent type and transcript path for observability.
    """
    agent_type = input_data.get("agent_type", "unknown")
    agent_id = input_data.get("agent_id", "unknown")

    logger.info(
        f"[subagent_stop] Subagent completed: agent_type={agent_type}, agent_id={agent_id}"
    )

    return {}
