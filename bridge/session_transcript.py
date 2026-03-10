"""
Session transcript logging for agent sessions.

Replaces the sparse snapshot approach in session_logs.py with append-only
transcript files that capture every turn, tool call, and tool result.

Transcript files: logs/sessions/{session_id}/transcript.txt
Metadata: SessionLog Popoto model

Transcript file format (one entry per line):
    [ISO_TIMESTAMP] ROLE: content
    [ISO_TIMESTAMP] TOOL_CALL: tool_name(input_summary)
    [ISO_TIMESTAMP] TOOL_RESULT: result_summary (truncated to 2000 chars)
"""

import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Transcript files live here (kept indefinitely — no TTL)
SESSION_LOGS_DIR = Path(__file__).parent.parent / "logs" / "sessions"
SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Max chars for tool result summaries in the transcript
TOOL_RESULT_MAX_CHARS = 2000


def _transcript_path(session_id: str) -> Path:
    """Return the path to the transcript file for a session."""
    session_dir = SESSION_LOGS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "transcript.txt"


def _now_iso() -> str:
    """Return current time as ISO 8601 string."""
    return datetime.now().isoformat()


def start_transcript(
    session_id: str,
    project_key: str,
    chat_id: str | None = None,
    sender: str | None = None,
    branch_name: str | None = None,
    work_item_slug: str | None = None,
    classification_type: str | None = None,
    correlation_id: str | None = None,
) -> str | None:
    """Create a SessionLog and open the transcript file.

    Args:
        session_id: Unique session identifier.
        project_key: Project key (e.g., "valor", "ai").
        chat_id: Telegram chat ID (optional).
        sender: Who triggered the session (optional).
        branch_name: Git branch associated with the session (optional).
        work_item_slug: Named work item slug (tier 2, optional).
        classification_type: Auto-classified type (bug/feature/chore, optional).
        correlation_id: End-to-end tracing ID (optional).

    Returns:
        Path to the transcript file as a string, or None on failure.
    """
    from models.agent_session import AgentSession

    log_path = str(_transcript_path(session_id))

    # Look up existing session (created by _push_job at enqueue time) and
    # update it with transcript-phase fields. Only create a new one if no
    # session exists (defensive fallback for standalone transcript usage).
    try:
        now = time.time()
        existing = list(AgentSession.query.filter(session_id=session_id))
        if existing:
            s = existing[0]
            s.log_path = log_path
            s.last_activity = now
            s.last_transition_at = now
            if sender:
                s.sender_name = sender
            if branch_name:
                s.branch_name = branch_name
            if work_item_slug:
                s.work_item_slug = work_item_slug
            if classification_type:
                s.classification_type = classification_type
            if chat_id is not None:
                s.chat_id = str(chat_id)
            s.save()
            # Log lifecycle transition
            try:
                s.log_lifecycle_transition("active", "transcript started")
            except Exception:
                pass
        else:
            # No existing session — create one (standalone transcript case)
            AgentSession.create(
                session_id=session_id,
                project_key=project_key,
                status="active",
                chat_id=str(chat_id) if chat_id is not None else None,
                sender_name=sender,
                created_at=now,
                started_at=now,
                last_activity=now,
                last_transition_at=now,
                turn_count=0,
                tool_call_count=0,
                log_path=log_path,
                branch_name=branch_name,
                work_item_slug=work_item_slug,
                classification_type=classification_type,
            )
            # Log lifecycle transition
            try:
                sessions = list(AgentSession.query.filter(session_id=session_id))
                if sessions:
                    sessions[0].log_lifecycle_transition("active", "transcript started")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Failed to update/create AgentSession for {session_id}: {e}")

    # Write transcript header
    try:
        transcript = Path(log_path)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        with transcript.open("a", encoding="utf-8") as f:
            f.write(
                f"[{_now_iso()}] SESSION_START: session_id={session_id}"
                f" project={project_key}"
                f"{' correlation_id=' + correlation_id if correlation_id else ''}"
                f"{' sender=' + sender if sender else ''}"
                f"{' chat_id=' + str(chat_id) if chat_id else ''}\n"
            )
        logger.debug(f"Transcript started: {log_path}")
        return log_path
    except Exception as e:
        logger.warning(f"Failed to write transcript header for {session_id}: {e}")
        return None


def append_turn(
    session_id: str,
    role: str,
    content: str,
    tool_name: str | None = None,
    tool_input: str | None = None,
) -> None:
    """Append a turn to the session transcript.

    For regular messages, appends: [timestamp] ROLE: content
    For tool calls, appends: [timestamp] TOOL_CALL: tool_name(input_summary)

    Also increments turn_count in the SessionLog.

    Args:
        session_id: Session identifier.
        role: Message role (user, assistant, tool_call, tool_result).
        content: Message content.
        tool_name: Tool name for TOOL_CALL entries.
        tool_input: Tool input for TOOL_CALL entries (summarized).
    """
    from models.agent_session import AgentSession

    log_path = _transcript_path(session_id)

    try:
        ts = _now_iso()
        if role.upper() in ("TOOL_CALL",) or tool_name:
            # Tool call entry
            input_summary = (str(tool_input or "")[:200]) if tool_input else ""
            line = f"[{ts}] TOOL_CALL: {tool_name}({input_summary})\n"
        else:
            role_upper = role.upper()
            line = f"[{ts}] {role_upper}: {content}\n"

        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.debug(f"Failed to append turn to transcript {session_id}: {e}")

    # Update SessionLog counters
    try:
        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            s = sessions[0]
            s.turn_count = (s.turn_count or 0) + 1
            s.last_activity = time.time()
            s.save()
    except Exception as e:
        logger.debug(f"Failed to update SessionLog turn_count for {session_id}: {e}")


def append_tool_result(
    session_id: str,
    result: str,
) -> None:
    """Append a tool result to the session transcript.

    Appends: [timestamp] TOOL_RESULT: result_summary (truncated to 2000 chars)

    Also increments tool_call_count in the SessionLog.

    Args:
        session_id: Session identifier.
        result: Tool result content (truncated to TOOL_RESULT_MAX_CHARS).
    """
    from models.agent_session import AgentSession

    log_path = _transcript_path(session_id)
    result_summary = str(result or "")[:TOOL_RESULT_MAX_CHARS]
    if len(str(result or "")) > TOOL_RESULT_MAX_CHARS:
        result_summary += "... [truncated]"

    try:
        ts = _now_iso()
        line = f"[{ts}] TOOL_RESULT: {result_summary}\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.debug(f"Failed to append tool result to transcript {session_id}: {e}")

    # Increment tool_call_count in SessionLog
    try:
        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            s = sessions[0]
            s.tool_call_count = (s.tool_call_count or 0) + 1
            s.last_activity = time.time()
            s.save()
    except Exception as e:
        logger.debug(f"Failed to update SessionLog tool_call_count for {session_id}: {e}")


def complete_transcript(
    session_id: str,
    status: str = "completed",
    summary: str | None = None,
) -> None:
    """Finalize the SessionLog metadata and write completion marker.

    Args:
        session_id: Session identifier.
        status: Final status (completed, failed, dormant).
        summary: Brief summary of session outcome (optional).
    """
    from models.agent_session import AgentSession

    # Write completion marker to transcript
    log_path = _transcript_path(session_id)
    try:
        ts = _now_iso()
        line = f"[{ts}] SESSION_END: status={status}"
        if summary:
            line += f" summary={summary[:200]}"
        line += "\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.debug(f"Failed to write transcript end for {session_id}: {e}")

    # Auto-tag the session before finalizing status
    try:
        from tools.session_tags import auto_tag_session

        auto_tag_session(session_id)
    except Exception as e:
        logger.debug(f"Auto-tagging failed for {session_id} (non-fatal): {e}")

    # Update SessionLog
    try:
        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            s = sessions[0]

            # Log lifecycle transition BEFORE status change
            # so log_lifecycle_transition captures old_status→new_status correctly
            try:
                s.log_lifecycle_transition(status, f"transcript completed: {status}")
            except Exception:
                pass

            # status is a KeyField — delete and recreate if changed
            if s.status != status:
                # Re-read after lifecycle log (it saved history/last_transition_at)
                sessions = list(AgentSession.query.filter(session_id=session_id))
                s = sessions[0] if sessions else s

                # Dynamically extract ALL fields to avoid dropping data.
                # Uses Popoto's _meta.fields registry instead of hardcoding
                # a subset. This prevents future field additions from being
                # silently dropped during status transitions.
                skip_fields = {"status", "job_id"}
                old_data = {
                    name: getattr(s, name)
                    for name in AgentSession._meta.fields
                    if name not in skip_fields and getattr(s, name, None) is not None
                }

                # Override specific fields for the transition
                old_data["last_activity"] = time.time()
                old_data["completed_at"] = time.time()
                if summary:
                    old_data["summary"] = summary

                s.delete()
                AgentSession.create(status=status, **old_data)
            else:
                s.completed_at = time.time()
                s.last_activity = time.time()
                if summary:
                    s.summary = summary
                s.save()
    except Exception as e:
        logger.warning(f"Failed to update SessionLog completion for {session_id}: {e}")
