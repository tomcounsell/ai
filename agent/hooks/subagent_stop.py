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


def _resolve_tracking_issue() -> int | None:
    """Resolve tracking issue number from env var or plan frontmatter.

    Checks SDLC_TRACKING_ISSUE first, then SDLC_ISSUE_NUMBER, then falls
    back to parsing the plan frontmatter if SDLC_SLUG is set.

    Returns:
        Issue number as int, or None if not found.
    """
    # Direct env var (set by sdk_client.py)
    for env_key in ("SDLC_TRACKING_ISSUE", "SDLC_ISSUE_NUMBER"):
        value = os.environ.get(env_key)
        if value and value.isdigit():
            return int(value)

    # Fallback: parse plan frontmatter
    slug = os.environ.get("SDLC_SLUG")
    if not slug:
        return None

    plan_path = os.environ.get("SDLC_PLAN_PATH")
    if not plan_path:
        plan_path = f"docs/plans/{slug}.md"

    try:
        import re

        # Try both absolute and relative paths
        for path in [plan_path, os.path.join(os.getcwd(), plan_path)]:
            if os.path.isfile(path):
                with open(path) as f:
                    content = f.read(2000)  # Frontmatter is at the top
                pattern = r"tracking:\s*https?://github\.com/[^/]+/[^/]+/issues/(\d+)"
                match = re.search(pattern, content)
                if match:
                    return int(match.group(1))
    except Exception as e:
        logger.debug(f"[subagent_stop] Failed to parse plan frontmatter: {e}")

    return None


def _post_stage_comment_on_completion(input_data: dict, current_stage: str | None) -> None:
    """Post a structured stage comment to the tracking issue.

    Called after _register_dev_session_completion(). Wraps all logic in
    try/except so comment posting never crashes the hook.
    """
    try:
        issue_number = _resolve_tracking_issue()
        if not issue_number:
            logger.debug("[subagent_stop] No tracking issue found, skipping comment")
            return

        stage = current_stage or "UNKNOWN"
        outcome = _extract_outcome_summary(input_data)

        from utils.issue_comments import post_stage_comment

        success = post_stage_comment(
            issue_number=issue_number,
            stage=stage,
            outcome=outcome,
        )
        if success:
            logger.info(f"[subagent_stop] Posted stage comment: {stage} on issue #{issue_number}")
        else:
            logger.warning(f"[subagent_stop] Failed to post stage comment on issue #{issue_number}")
    except Exception as e:
        logger.warning(f"[subagent_stop] Comment posting failed (non-fatal): {e}")


async def subagent_stop_hook(
    input_data: SubagentStopHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Log when a subagent finishes execution and register DevSession completion.

    When agent_type is dev-session:
    1. Updates DevSession status in Redis
    2. Posts a structured stage comment to the tracking issue
    3. Injects current SDLC pipeline state via 'reason' so the PM sees
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

        # Resolve current stage before it gets marked complete
        current_stage = None
        session_id = os.environ.get("VALOR_SESSION_ID")
        if session_id:
            stages_str = _get_stage_states(session_id)
            if stages_str:
                # Find the stage that was just completed (most recent in_progress -> completed)
                try:
                    stages_dict = json.loads(stages_str) if isinstance(stages_str, str) else {}
                    for stage_name, state in stages_dict.items():
                        if state in ("completed", "done"):
                            current_stage = stage_name
                except (json.JSONDecodeError, TypeError):
                    pass

        # Post stage comment to tracking issue (non-blocking, never crashes)
        _post_stage_comment_on_completion(input_data, current_stage)

        # Inject SDLC stage state and outcome back to PM
        if session_id:
            stages = _get_stage_states(session_id)
            if stages:
                logger.info(f"[subagent_stop] Injecting stage state for {session_id}")
                reason = f"Pipeline state: {stages}"
                if outcome:
                    reason += f"\nOutcome: {outcome}"
                return {"reason": reason}

    return {}
