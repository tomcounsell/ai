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

    Captures the subagent name/type for observability.
    """
    agent_name = input_data.get("agent_name", "unknown")
    stop_reason = input_data.get("stop_reason", "unspecified")

    logger.info(
        f"[subagent_stop] Subagent completed: agent={agent_name}, "
        f"reason={stop_reason}"
    )

    return {}
