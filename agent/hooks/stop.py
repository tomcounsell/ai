"""Stop hook: logs session completion and enforces SDLC branch rules."""

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
    """Log when a session completes. Hard-blocks code-on-main violations.

    Captures the session_id and transcript path for observability.

    SDLC enforcement: if code was modified on the main branch, the session
    is hard-blocked with an explanatory message. Sessions on feature branches
    (session/{slug}) or docs-only sessions always pass.
    """
    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path", "")

    logger.info(
        f"[stop_hook] Session completed: session_id={session_id}, transcript={transcript_path}"
    )

    # SDLC enforcement: hard-block code pushed directly to main
    try:
        from agent.sdk_client import _check_no_direct_main_push

        violation = _check_no_direct_main_push(session_id)
        if violation:
            logger.error(
                f"[stop_hook] SDLC violation detected for session {session_id}: "
                "code modified on main branch"
            )
            # Return decision=block to prevent session completion
            return {
                "decision": "block",
                "reason": violation,
            }
    except Exception as e:
        # Never let the SDLC check crash a session — fail open with a warning
        logger.warning(
            f"[stop_hook] SDLC branch check failed for {session_id}: {e} — "
            "failing open, session allowed to complete"
        )

    return {}
