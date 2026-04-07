"""SubagentStop hook: logs subagent completion, registers child Dev session
completion, posts structured stage comments to the tracking GitHub issue, and
injects SDLC pipeline state back into the PM's context.

Stage comments are posted via utils.issue_comments after each Dev session
completes, turning the GitHub issue into a living record of stage-by-stage
progress. See docs/features/sdlc-stage-handoff.md for the full design."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from claude_agent_sdk import HookContext, SubagentStopHookInput

logger = logging.getLogger(__name__)


def _extract_output_tail(input_data: dict, max_chars: int = 500) -> str:
    """Extract the last N chars of output for classify_outcome().

    Tries two sources in order:
    1. agent_transcript_path -- reads the last max_chars from the transcript file
    2. _extract_outcome_summary() -- falls back to the 200-char summary

    Args:
        input_data: The SubagentStopHookInput dict.
        max_chars: Maximum characters to extract from the tail.

    Returns:
        Output tail string (may be shorter than max_chars).
    """
    # Try reading transcript file tail
    transcript_path = input_data.get("agent_transcript_path")
    if transcript_path:
        try:
            with open(transcript_path, "rb") as f:
                # Seek to the last max_chars bytes (approximate for UTF-8)
                f.seek(0, 2)  # Seek to end
                file_size = f.tell()
                read_size = min(file_size, max_chars * 2)  # Over-read for UTF-8
                f.seek(max(0, file_size - read_size))
                raw = f.read()
                text = raw.decode("utf-8", errors="replace")
                return text[-max_chars:]
        except OSError as e:
            logger.debug(f"[subagent_stop] Could not read transcript tail: {e}")

    # Fallback to outcome summary
    return _extract_outcome_summary(input_data)


def _register_dev_session_completion(
    agent_id: str, input_data: dict | None = None, claude_uuid: str | None = None
) -> None:
    """Mark a Dev session as completed in Redis and record SDLC stage completion.

    Looks up the Dev session by parent PM session and updates its status.
    Also records stage completion via PipelineStateMachine if a stage is in_progress.
    Uses classify_outcome() to determine success/fail before routing.

    Two-lookup pattern (issue #808):
    1. Resolve bridge session ID from Claude UUID via session_registry.
    2. Look up parent AgentSession by bridge session_id to get agent_session_id UUID.
    3. Query child sessions by that UUID (parent_agent_session_id = agent_session_id UUID).

    This is required because session_registry.resolve() returns a bridge session ID
    (e.g. "tg_valor_...") but parent_agent_session_id on local-* records stores the
    agent_session_id UUID (e.g. "agt_xxx") set by VALOR_PARENT_SESSION_ID env var.

    Args:
        agent_id: The agent ID of the completing dev-session.
        input_data: The SubagentStopHookInput dict, used to extract output_tail
            for outcome classification.
        claude_uuid: The Claude Code session UUID for registry lookup (issue #597).
    """
    from agent.hooks.session_registry import resolve

    parent_session_id = resolve(claude_uuid)
    if not parent_session_id:
        logger.debug(
            "[subagent_stop] No bridge session in registry, skipping Dev session completion"
        )
        return

    try:
        from models.agent_session import AgentSession
        from models.session_lifecycle import finalize_session

        # Two-lookup pattern: resolve bridge session_id → parent AgentSession → agent_session_id UUID.
        # Child local-* records store the parent's agent_session_id UUID in parent_agent_session_id,
        # not the bridge session_id, so we must look up the parent record first.
        parent_agent_uuid: str | None = None
        parent_sessions = list(AgentSession.query.filter(session_id=parent_session_id))
        if parent_sessions:
            parent_agent_uuid = parent_sessions[0].agent_session_id

        if not parent_agent_uuid:
            logger.debug(
                f"[subagent_stop] Parent AgentSession not found for bridge session "
                f"{parent_session_id}, skipping Dev session completion"
            )
            return

        # Find child dev sessions by agent_session_id UUID (set via VALOR_PARENT_SESSION_ID)
        dev_sessions = list(AgentSession.query.filter(parent_agent_session_id=parent_agent_uuid))
        for dev in dev_sessions:
            if dev.status not in ("completed", "failed"):
                finalize_session(
                    dev,
                    "completed",
                    reason=f"subagent stop (parent={parent_session_id}, agent_id={agent_id})",
                    skip_parent=True,  # Parent finalization handled separately below
                )
                logger.info(
                    f"[subagent_stop] Dev session {dev.agent_session_id} completed "
                    f"(parent={parent_session_id}/{parent_agent_uuid}, agent_id={agent_id})"
                )

        # Record SDLC stage completion on the parent PM session.
        # Extract output tail for outcome classification.
        output_tail = _extract_output_tail(input_data or {})
        _record_stage_on_parent(parent_session_id, stop_reason=None, output_tail=output_tail)
    except Exception as e:
        logger.warning(f"[subagent_stop] Failed to register Dev session completion: {e}")


def _record_stage_on_parent(
    parent_session_id: str,
    stop_reason: str | None = None,
    output_tail: str = "",
) -> None:
    """Record stage completion on the parent PM session's PipelineStateMachine.

    Finds the current in_progress stage, classifies the outcome using
    classify_outcome(), and routes to complete_stage() or fail_stage()
    accordingly. This wires the full outcome classification pipeline into
    the SDLC skill completion path.

    Args:
        parent_session_id: The parent PM session's session_id.
        stop_reason: SDK stop reason (e.g. 'end_turn', 'timeout'). None when
            not available from SubagentStopHookInput (which lacks this field).
        output_tail: Last ~500 chars of worker output for pattern matching.
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
            # Classify outcome before deciding complete vs fail
            try:
                outcome = sm.classify_outcome(current, stop_reason, output_tail)
            except Exception as e:
                logger.warning(
                    f"[subagent_stop] classify_outcome failed for {current}: {e}. "
                    f"Defaulting to complete_stage."
                )
                outcome = "ambiguous"

            if outcome in ("fail", "partial"):
                sm.fail_stage(current)
                logger.info(
                    f"[subagent_stop] Recorded stage failure: {current} "
                    f"(outcome={outcome}) on session {parent_session_id}"
                )
            else:
                # "success" or "ambiguous" -> complete (safe default)
                sm.complete_stage(current)
                logger.info(
                    f"[subagent_stop] Recorded stage completion: {current} "
                    f"(outcome={outcome}) on session {parent_session_id}"
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
    """Log when a subagent finishes execution and register Dev session completion.

    When agent_type is dev-session:
    1. Updates Dev session status in Redis
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

    # Register Dev session completion in Redis for parent PM session tracking
    if agent_type == "dev-session":
        # Issue #597: Use session registry instead of os.environ for bridge session ID
        from agent.hooks.session_registry import resolve

        claude_uuid = input_data.get("session_id")
        _register_dev_session_completion(agent_id, input_data=input_data, claude_uuid=claude_uuid)

        # Resolve current stage before it gets marked complete
        current_stage = None
        session_id = resolve(claude_uuid)
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
