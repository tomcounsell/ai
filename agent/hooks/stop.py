"""Stop hook: logs session completion."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import HookContext, StopHookInput

logger = logging.getLogger(__name__)


async def stop_hook(
    input_data: StopHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Log when a session completes.

    Captures the session_id and stop reason for observability.
    """
    session_id = input_data.get("session_id", "unknown")
    stop_reason = input_data.get("stop_reason", "unspecified")

    logger.info(
        f"[stop_hook] Session completed: session_id={session_id}, "
        f"reason={stop_reason}"
    )

    return {}
