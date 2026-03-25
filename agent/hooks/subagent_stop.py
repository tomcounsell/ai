"""SubagentStop hook: logs subagent completion, registers DevSession,
extracts findings for cross-agent relay, and injects SDLC pipeline
state back into the PM's context."""

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


def _get_stage_states(session_id: str) -> str | None:
    """Return the SDLC stage_states dict as a string, or None."""
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return None
        raw = sessions[0].stage_states
        if not raw:
            return None
        return str(json.loads(raw) if isinstance(raw, str) else raw)
    except Exception:
        return None


def _extract_and_persist_findings(input_data: dict, agent_id: str) -> None:
    """Extract findings from dev-session output and persist for cross-agent relay.

    Called when a dev-session completes. Looks up the parent session's slug
    and stage, then delegates to finding_extraction for Haiku-based extraction.

    Failures are logged but never raised -- this must not block completion.
    """
    parent_session_id = os.environ.get("VALOR_SESSION_ID")
    if not parent_session_id:
        return

    try:
        from models.agent_session import AgentSession

        # Find the parent session to get slug and project_key
        parent_sessions = list(AgentSession.query.filter(session_id=parent_session_id))
        if not parent_sessions:
            logger.debug("[subagent_stop] No parent session found, skipping finding extraction")
            return

        parent = parent_sessions[0]
        # slug is the canonical field; work_item_slug is the legacy alias (pre-v2 sessions)
        slug = parent.slug or parent.work_item_slug
        if not slug:
            logger.debug("[subagent_stop] No slug on parent session, skipping finding extraction")
            return

        project_key = parent.project_key or "default"

        # Get the current stage from the parent's pipeline state
        stage = parent.current_stage or ""

        # Get the full output text from the subagent
        # Try to get the full result text first, falling back to truncated summary
        full_output = ""
        for key in ("result", "output", "response", "summary", "message"):
            value = input_data.get(key)
            if value and isinstance(value, str):
                full_output = value
                break
        if not full_output:
            result = input_data.get("result")
            if isinstance(result, dict):
                for key in ("text", "message", "summary", "output"):
                    value = result.get(key)
                    if value and isinstance(value, str):
                        full_output = value
                        break
        if not full_output:
            full_output = str(input_data)[:8000]

        # Delegate to finding extraction module
        from agent.finding_extraction import extract_findings_from_output

        dev_session_id = f"dev-{parent_session_id}"
        findings = extract_findings_from_output(
            output=full_output,
            slug=slug,
            stage=stage,
            session_id=dev_session_id,
            project_key=project_key,
        )

        if findings:
            logger.info(
                f"[subagent_stop] Extracted {len(findings)} findings for slug={slug}, stage={stage}"
            )

    except Exception as e:
        logger.warning(f"[subagent_stop] Finding extraction failed (non-fatal): {e}")


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

    # Extract outcome summary from subagent return value
    outcome = _extract_outcome_summary(input_data)
    logger.info(
        f"[subagent_stop] Subagent completed: agent_type={agent_type}, "
        f"agent_id={agent_id}, outcome={outcome}"
    )

    # Register DevSession completion in Redis for parent ChatSession tracking
    if agent_type == "dev-session":
        _register_dev_session_completion(agent_id)

        # Extract and persist findings for cross-agent knowledge relay
        _extract_and_persist_findings(input_data, agent_id)

        # Inject SDLC stage state and outcome back to PM
        session_id = os.environ.get("VALOR_SESSION_ID")
        if session_id:
            stages = _get_stage_states(session_id)
            if stages:
                logger.info(f"[subagent_stop] Injecting stage state for {session_id}")
                reason = f"Pipeline state: {stages}"
                if outcome:
                    reason += f"\nOutcome: {outcome}"
                return {"reason": reason}

    return {}
