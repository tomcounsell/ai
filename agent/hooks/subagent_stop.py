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
    """Mark a DevSession as completed in Redis.

    Looks up the DevSession by parent ChatSession and updates its status.
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
    except Exception as e:
        logger.warning(f"[subagent_stop] Failed to register DevSession completion: {e}")


def _build_sdlc_stage_summary(session_id: str) -> str | None:
    """Build a human-readable SDLC stage status from the AgentSession.

    Reads stage_states or sdlc_stages from the session and formats them
    so the PM sees which stages are done vs pending.

    Returns None if not an SDLC session or no stage data exists.
    """
    try:
        from models.agent_session import SDLC_STAGES, AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return None

        session = sessions[0]
        raw = session.sdlc_stages or session.stage_states
        if not raw:
            return None

        stages = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(stages, dict):
            return None

        # Build status line for each stage
        lines = []
        for stage in SDLC_STAGES:
            status = stages.get(stage, stages.get(stage.lower(), "pending"))
            if status == "completed":
                lines.append(f"  {stage}: DONE")
            elif status in ("in_progress", "running"):
                lines.append(f"  {stage}: IN PROGRESS")
            elif status == "failed":
                lines.append(f"  {stage}: FAILED")
            elif status == "skipped":
                lines.append(f"  {stage}: SKIPPED")
            else:
                lines.append(f"  {stage}: pending")

        return "SDLC Pipeline State:\n" + "\n".join(lines)

    except Exception as e:
        logger.debug(f"[subagent_stop] Could not build stage summary: {e}")
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
            summary = _build_sdlc_stage_summary(session_id)
            if summary:
                logger.info(f"[subagent_stop] Injecting SDLC stage state for {session_id}")
                return {
                    "reason": (
                        f"Dev-session completed. Current pipeline state "
                        f"(verify artifacts before marking stages done):\n\n{summary}"
                    ),
                }

    return {}
