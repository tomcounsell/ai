"""SubagentStop hook: logs subagent completion, registers DevSession,
and injects SDLC pipeline state back into the PM's context."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from claude_agent_sdk import HookContext, SubagentStopHookInput

logger = logging.getLogger(__name__)


def _register_dev_session_completion(agent_id: str) -> None:
    """Mark a DevSession as completed in Redis and record SDLC stage completion.

    Looks up the DevSession by parent ChatSession and updates its status.
    Also records stage completion via PipelineStateMachine if a stage is in_progress.
    Logs the parent -> child completion linkage for observability.
    """
    parent_session_id = os.environ.get("VALOR_SESSION_ID")
    if not parent_session_id:
        logger.debug("[subagent_stop] VALOR_SESSION_ID not set, skipping DevSession completion")
        return

    try:
        from models.agent_session import AgentSession

        # Find dev sessions for this parent
        dev_sessions = list(AgentSession.query.filter(parent_chat_session_id=parent_session_id))
        for dev in dev_sessions:
            if dev.status not in ("completed", "failed"):
                dev.status = "completed"
                dev.save()
                logger.info(
                    f"[subagent_stop] DevSession {dev.job_id} completed "
                    f"(parent={parent_session_id}, agent_id={agent_id})"
                )

        # Record SDLC stage completion on the parent ChatSession.
        # The parent session's stage_states tracks which pipeline stage is in_progress.
        # When the dev-session completes successfully, mark that stage as completed.
        _record_stage_on_parent(parent_session_id)
    except Exception as e:
        logger.warning(f"[subagent_stop] Failed to register DevSession completion: {e}")


def _record_stage_on_parent(parent_session_id: str) -> None:
    """Record stage completion on the parent ChatSession's PipelineStateMachine.

    Finds the current in_progress stage and marks it completed. This wires
    PipelineStateMachine.complete_stage() into the SDLC skill completion path.
    """
    try:
        from bridge.pipeline_state import PipelineStateMachine
        from models.agent_session import AgentSession

        parent_sessions = list(AgentSession.query.filter(session_id=parent_session_id))
        if not parent_sessions:
            logger.debug(f"[subagent_stop] Parent session {parent_session_id} not found")
            return

        parent = parent_sessions[0]
        sm = PipelineStateMachine(parent)
        current = sm.current_stage()

        if current:
            sm.complete_stage(current)
            logger.info(
                f"[subagent_stop] Recorded stage completion: {current} "
                f"on session {parent_session_id}"
            )
        else:
            logger.debug(
                f"[subagent_stop] No in_progress stage on {parent_session_id}, "
                f"skipping stage completion"
            )
    except Exception as e:
        logger.warning(f"[subagent_stop] Failed to record stage completion: {e}")


def _get_sdlc_stages(session_id: str) -> str | None:
    """Return the SDLC stage_states dict as a string, or None."""
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return None
        raw = sessions[0].sdlc_stages or sessions[0].stage_states
        if not raw:
            return None
        return str(json.loads(raw) if isinstance(raw, str) else raw)
    except Exception:
        return None


async def subagent_stop_hook(
    input_data: SubagentStopHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Log when a subagent finishes execution and register DevSession completion.

    When agent_type is dev-session:
    1. Updates DevSession status in Redis
    2. Injects current SDLC pipeline state via 'reason' so the PM sees
       which stages are actually complete vs still pending
    """
    agent_type = input_data.get("agent_type", "unknown")
    agent_id = input_data.get("agent_id", "unknown")

    logger.info(f"[subagent_stop] Subagent completed: agent_type={agent_type}, agent_id={agent_id}")

    # Register DevSession completion in Redis for parent ChatSession tracking
    if agent_type == "dev-session":
        _register_dev_session_completion(agent_id)

        # Inject SDLC stage state back to PM so it knows what's actually done
        session_id = os.environ.get("VALOR_SESSION_ID")
        if session_id:
            stages = _get_sdlc_stages(session_id)
            if stages:
                logger.info(f"[subagent_stop] Injecting stage state for {session_id}")
                return {"reason": f"Pipeline state: {stages}"}

    return {}
