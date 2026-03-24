"""Data access layer for the SDLC Observer dashboard.

Provides Pydantic serializers for AgentSession.stage_states and history,
plus query functions for active/completed pipelines.

All functions are synchronous (def, not async def) because Popoto uses
synchronous Redis calls. FastAPI runs sync route handlers in a threadpool.
"""

import json
import logging
import time

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# SDLC stages in pipeline order (matches models/agent_session.py)
SDLC_STAGES = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]


# === Pydantic models ===


class StageState(BaseModel):
    """Typed representation of a single SDLC stage's status."""

    name: str
    status: str  # pending | ready | in_progress | completed | failed | skipped

    @property
    def is_active(self) -> bool:
        return self.status == "in_progress"

    @property
    def is_done(self) -> bool:
        return self.status in ("completed", "skipped")

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"


class PipelineEvent(BaseModel):
    """A single event from the session history."""

    role: str  # e.g., 'stage', 'lifecycle', 'user', 'system'
    text: str
    timestamp: float | None = None


class PipelineProgress(BaseModel):
    """Complete pipeline view for a single AgentSession."""

    job_id: str
    session_id: str | None = None
    session_type: str | None = None
    status: str | None = None
    slug: str | None = None
    message_text: str | None = None
    project_key: str | None = None
    branch_name: str | None = None
    created_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    last_activity: float | None = None

    # SDLC state
    stages: list[StageState] = []
    current_stage: str | None = None
    events: list[PipelineEvent] = []

    # Links
    issue_url: str | None = None
    plan_url: str | None = None
    pr_url: str | None = None

    @property
    def duration(self) -> float | None:
        """Total duration in seconds from start to completion or now."""
        if not self.started_at:
            return None
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def is_active(self) -> bool:
        return self.status in ("pending", "running", "active", "waiting_for_children")

    @property
    def is_complete(self) -> bool:
        return self.status in ("completed", "failed")

    @property
    def display_name(self) -> str:
        """Human-friendly name: slug if available, else truncated message."""
        if self.slug:
            return self.slug
        if self.message_text:
            text = self.message_text[:60]
            if len(self.message_text) > 60:
                text += "..."
            return text
        return self.job_id or "unknown"


# === Parsing helpers ===


def _parse_stage_states(raw: str | dict | None) -> list[StageState]:
    """Parse stage_states field into typed StageState objects."""
    if not raw:
        return [StageState(name=s, status="pending") for s in SDLC_STAGES]

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return [StageState(name=s, status="pending") for s in SDLC_STAGES]

    if not isinstance(raw, dict):
        return [StageState(name=s, status="pending") for s in SDLC_STAGES]

    stages = []
    for name in SDLC_STAGES:
        status = raw.get(name, "pending")
        if isinstance(status, dict):
            # Handle nested status dict (e.g., {"status": "completed", ...})
            status = status.get("status", "pending")
        stages.append(StageState(name=name, status=str(status)))

    return stages


def _parse_history(history_list: list | None) -> list[PipelineEvent]:
    """Parse session history entries into typed PipelineEvent objects."""
    if not history_list or not isinstance(history_list, list):
        return []

    events = []
    for entry in history_list:
        if isinstance(entry, str):
            # Format: "[role] text"
            if entry.startswith("[") and "]" in entry:
                bracket_end = entry.index("]")
                role = entry[1:bracket_end]
                text = entry[bracket_end + 2 :] if bracket_end + 2 < len(entry) else ""
            else:
                role = "system"
                text = entry
            events.append(PipelineEvent(role=role, text=text))
        elif isinstance(entry, dict):
            events.append(
                PipelineEvent(
                    role=entry.get("role", "system"),
                    text=entry.get("text", str(entry)),
                    timestamp=entry.get("timestamp"),
                )
            )

    return events


def _session_to_pipeline(session) -> PipelineProgress:
    """Convert an AgentSession instance to a PipelineProgress model."""
    stages = _parse_stage_states(session.stage_states)
    events = _parse_history(session.history if isinstance(session.history, list) else None)

    # Determine current stage
    current = None
    for s in stages:
        if s.is_active:
            current = s.name
            break

    return PipelineProgress(
        job_id=session.job_id or "",
        session_id=session.session_id,
        session_type=session.session_type,
        status=session.status,
        slug=session.slug or session.work_item_slug,
        message_text=session.message_text,
        project_key=session.project_key,
        branch_name=session.branch_name or (f"session/{session.slug}" if session.slug else None),
        created_at=session.created_at,
        started_at=session.started_at,
        completed_at=session.completed_at,
        last_activity=session.last_activity,
        stages=stages,
        current_stage=current,
        events=events,
        issue_url=session.issue_url,
        plan_url=session.plan_url,
        pr_url=session.pr_url,
    )


# === Public query functions ===


def get_active_pipelines() -> list[PipelineProgress]:
    """Get all active SDLC pipelines (sessions with stage_states that aren't completed).

    Returns:
        List of PipelineProgress for active pipelines, sorted by last activity.
    """
    from models.agent_session import AgentSession

    try:
        all_sessions = AgentSession.query.all()
    except Exception as e:
        logger.warning(f"Failed to query AgentSession: {e}")
        return []

    active = []
    for session in all_sessions:
        # Only include sessions that have SDLC stage tracking
        if not session.stage_states:
            continue
        # Skip completed/failed sessions
        if session.status in ("completed", "failed"):
            continue
        pipeline = _session_to_pipeline(session)
        active.append(pipeline)

    # Sort by last_activity descending (most recent first)
    active.sort(key=lambda p: p.last_activity or p.created_at or 0, reverse=True)
    return active


def get_pipeline_detail(job_id: str) -> PipelineProgress | None:
    """Get detailed pipeline information for a specific session.

    Args:
        job_id: The AgentSession job_id to look up.

    Returns:
        PipelineProgress with full details, or None if not found.
    """
    from models.agent_session import AgentSession

    try:
        session = AgentSession.query.get(job_id)
        if not session:
            return None
        return _session_to_pipeline(session)
    except Exception as e:
        logger.warning(f"Failed to get pipeline detail for {job_id}: {e}")
        return None


def get_recent_completions(limit: int = 25) -> list[PipelineProgress]:
    """Get recently completed SDLC pipelines.

    Args:
        limit: Maximum number of results to return.

    Returns:
        List of PipelineProgress for completed pipelines, newest first.
    """
    from models.agent_session import AgentSession

    try:
        all_sessions = AgentSession.query.all()
    except Exception as e:
        logger.warning(f"Failed to query AgentSession: {e}")
        return []

    completed = []
    for session in all_sessions:
        if not session.stage_states:
            continue
        if session.status not in ("completed", "failed"):
            continue
        pipeline = _session_to_pipeline(session)
        completed.append(pipeline)

    # Sort by completed_at descending
    completed.sort(key=lambda p: p.completed_at or p.created_at or 0, reverse=True)

    return completed[:limit]
