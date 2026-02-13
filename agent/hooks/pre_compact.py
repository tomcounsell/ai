"""PreCompact hook: logs context compaction events."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import HookContext, PreCompactHookInput

logger = logging.getLogger(__name__)


async def pre_compact_hook(
    input_data: PreCompactHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Log when context compaction is about to occur.

    This fires before the SDK compacts the conversation context,
    useful for monitoring how often compaction happens.
    """
    session_id = input_data.get("session_id", "unknown")

    logger.info(f"[pre_compact] Context compaction triggered: session_id={session_id}")

    return {}
