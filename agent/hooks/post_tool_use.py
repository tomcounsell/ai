"""PostToolUse hook: watchdog health check and Skill tool stage completion."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import HookContext, PostToolUseHookInput

logger = logging.getLogger(__name__)


def _complete_pipeline_stage(session_id: str) -> None:
    """Complete the currently in_progress pipeline stage for a session.

    Loads the parent AgentSession from Redis, creates a PipelineStateMachine,
    reads the current in_progress stage via current_stage(), and calls
    complete_stage(). This is the counterpart to _start_pipeline_stage() in
    pre_tool_use.py, called when the Skill tool completes.

    Avoids storing state between pre and post hooks by reading current_stage()
    directly from Redis rather than requiring the stage name to be passed.

    Failures are logged but never raised -- this must not block the PM session.
    """
    try:
        from agent.pipeline_state import PipelineStateMachine
        from models.agent_session import AgentSession

        parent_sessions = list(AgentSession.query.filter(session_id=session_id))
        if not parent_sessions:
            logger.warning(
                f"[post_tool_use] Session {session_id} not found, skipping complete_stage"
            )
            return

        parent = parent_sessions[0]
        sm = PipelineStateMachine(parent)
        stage = sm.current_stage()
        if not stage:
            logger.debug(
                f"[post_tool_use] No in_progress stage for session {session_id}, "
                "skipping complete_stage"
            )
            return

        sm.complete_stage(stage)
        logger.info(f"[post_tool_use] Completed pipeline stage {stage} on session {session_id}")
    except Exception as e:
        logger.warning(
            f"[post_tool_use] Failed to complete pipeline stage on session {session_id}: {e}"
        )


async def post_tool_use_hook(
    input_data: PostToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Run watchdog health check and handle Skill tool stage completion.

    For every tool call: runs the watchdog health check and clears the
    in-flight ``current_tool_name`` on the AgentSession (issue #1172, Pillar A).
    For Skill tool calls specifically: calls _complete_pipeline_stage() to
    advance the pipeline state machine after the skill finishes.
    """
    from agent.health_check import watchdog_hook
    from agent.hooks.pre_tool_use import _SKILL_TO_STAGE

    # Pillar A liveness write — fire-and-forget, clear the current tool name.
    try:
        from agent.hooks.liveness_writers import record_tool_boundary

        record_tool_boundary(tool_name=None, clear=True)
    except Exception as _liveness_err:
        logger.debug("[post_tool_use] liveness write failed (non-fatal): %s", _liveness_err)

    # Always run watchdog
    result = await watchdog_hook(input_data, tool_use_id, context)

    # Handle Skill tool completion for pipeline stage tracking
    tool_name = input_data.get("tool_name", "")
    if tool_name == "Skill":
        tool_input = input_data.get("tool_input", {})
        skill_name = tool_input.get("skill", "")
        # Only process SDLC skills to avoid noise from non-pipeline skills
        if skill_name in _SKILL_TO_STAGE:
            try:
                import os

                session_id = os.environ.get("AGENT_SESSION_ID")
                if session_id:
                    _complete_pipeline_stage(session_id)
                else:
                    logger.debug(
                        f"[post_tool_use] AGENT_SESSION_ID not set for Skill '{skill_name}', "
                        "skipping complete_stage"
                    )
            except Exception as e:
                logger.warning(f"[post_tool_use] Skill stage completion failed: {e}")

    return result or {}
