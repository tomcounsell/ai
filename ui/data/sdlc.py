"""Data access layer for the SDLC Observer dashboard.

Provides Pydantic serializers for AgentSession.stage_states and history,
plus query functions for active/completed pipelines.

All functions are synchronous (def, not async def) because Popoto uses
synchronous Redis calls. FastAPI runs sync route handlers in a threadpool.
"""

import datetime
import json
import logging
import os
import time

from pydantic import BaseModel

from bridge.pipeline_graph import DISPLAY_STAGES
from config.enums import PersonaType

logger = logging.getLogger(__name__)

# Configurable retention for inactive sessions (default 48h)
DASHBOARD_RETENTION_HOURS = int(os.environ.get("DASHBOARD_RETENTION_HOURS", "48"))

# Module-level cache for project configs
_project_configs_cache: dict | None = None
_project_configs_ts: float = 0.0
_PROJECT_CONFIGS_TTL = 60.0  # seconds


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

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


class PipelineEvent(BaseModel):
    """A single event from the session history."""

    role: str  # e.g., 'stage', 'lifecycle', 'user', 'system'
    text: str
    timestamp: float | None = None


class PipelineProgress(BaseModel):
    """Complete pipeline view for a single AgentSession.

    Fields:
        agent_session_id: Unique identifier for this agent session.
        session_id: Telegram/local session identifier.
        session_type: Display persona (e.g., "Developer", "Project Manager").
        status: Lifecycle status (pending, running, completed, etc.).
        slug: Work item slug for planned work.
        message_text: Original message that triggered this session.
        project_key: Project identifier from projects.json.
        project_name: Human-readable project name.
        project_metadata: Enriched project info (repo, chat, stack, etc.).
        branch_name: Git branch for this session's work.
        created_at/started_at/completed_at/updated_at: Timestamps as floats.
        parent_agent_session_id: ID of the parent session (for hierarchy).
        children: Child sessions nested under this parent.
        context_summary: What this session is about (human-friendly).
        expectations: What the agent needs from the human (for dormant sessions).
        turn_count: Number of conversation turns.
        tool_call_count: Number of tool calls made.
        watchdog_unhealthy: Reason string when flagged unhealthy, None when healthy.
        priority: Session priority (urgent, high, normal, low).
        classification_type: Session classification (sdlc, qa, etc.).
        is_stale: True if session is running but updated_at is >10 minutes ago.
    """

    agent_session_id: str
    session_id: str | None = None
    session_type: str | None = None
    status: str | None = None
    slug: str | None = None
    message_text: str | None = None
    project_key: str | None = None
    project_name: str | None = None
    project_metadata: dict | None = None
    branch_name: str | None = None
    created_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    updated_at: float | None = None

    # Parent/child hierarchy
    parent_agent_session_id: str | None = None
    children: list["PipelineProgress"] = []

    # Session metadata
    context_summary: str | None = None
    expectations: str | None = None
    turn_count: int | None = None
    tool_call_count: int | None = None
    watchdog_unhealthy: str | None = None
    priority: str | None = None
    classification_type: str | None = None
    is_stale: bool = False

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
        start = self.started_at or self.created_at
        if not start:
            return None
        end = self.completed_at or time.time()
        return end - start

    @property
    def is_active(self) -> bool:
        return self.status in ("pending", "running", "active", "waiting_for_children")

    @property
    def is_complete(self) -> bool:
        return self.status in ("completed", "failed")

    @property
    def display_name(self) -> str:
        """Human-friendly name: context_summary, then slug, then truncated message."""
        if self.context_summary:
            return self.context_summary
        if self.slug:
            return self.slug
        if self.message_text:
            text = self.message_text[:60]
            if len(self.message_text) > 60:
                text += "..."
            return text
        return self.agent_session_id or "unknown"


# === Project config helpers ===


def _load_project_configs() -> dict:
    """Load project configs from projects.json with a short TTL cache.

    Returns a dict mapping project_key -> project config dict.
    Falls back to empty dict if projects.json is unavailable.
    """
    global _project_configs_cache, _project_configs_ts

    now = time.time()
    if _project_configs_cache is not None and (now - _project_configs_ts) < _PROJECT_CONFIGS_TTL:
        return _project_configs_cache

    try:
        from bridge.routing import load_config

        config = load_config()
        projects = config.get("projects", {})
        _project_configs_cache = projects
        _project_configs_ts = now
        return projects
    except Exception as e:
        logger.warning(f"Failed to load project configs: {e}")
        _project_configs_cache = {}
        _project_configs_ts = now
        return {}


def _get_project_metadata(project_key: str | None) -> tuple[str | None, dict | None]:
    """Look up human-readable project name and metadata from projects.json.

    Returns:
        (project_name, project_metadata) tuple. Both None if not found.
    """
    if not project_key:
        return None, None

    configs = _load_project_configs()
    project = configs.get(project_key)
    if not project:
        return None, None

    name = project.get("name", project_key)
    context = project.get("context", {})
    telegram = project.get("telegram", {})

    metadata = {}
    if telegram.get("groups"):
        metadata["telegram_chat"] = ", ".join(telegram["groups"])
    if project.get("github_repo"):
        metadata["github_repo"] = project["github_repo"]
    if project.get("working_directory"):
        metadata["working_dir"] = project["working_directory"]
    if context.get("tech_stack"):
        metadata["tech_stack"] = context["tech_stack"]
    if project.get("machine"):
        metadata["machine"] = project["machine"]
    elif context.get("machine"):
        metadata["machine"] = context["machine"]

    return name, metadata if metadata else None


# === Parsing helpers ===


def _parse_stage_states(raw: str | dict | None) -> list[StageState]:
    """Parse stage_states field into typed StageState objects.

    Returns an empty list when raw is None/empty (non-SDLC sessions).
    Only returns stage objects when actual stage data exists.
    """
    if not raw:
        return []

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    if not isinstance(raw, dict):
        return []

    stages = []
    for name in DISPLAY_STAGES:
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


def _resolve_persona_display(session) -> str | None:
    """Map session_mode and session_type into a dashboard display persona.

    session_mode takes priority when set:
      session_mode="teammate"         → "Teammate"
      session_mode="project-manager"  → "Project Manager"
      session_mode="developer"        → "Developer"

    Fallback from session_type:
      session_type="dev"              → "Developer"
      session_type="chat"             → "Project Manager"
    """
    mode = getattr(session, "session_mode", None)
    if mode == PersonaType.TEAMMATE:
        return "Teammate"
    if mode == PersonaType.PROJECT_MANAGER:
        return "Project Manager"
    if mode == PersonaType.DEVELOPER:
        return "Developer"

    raw = getattr(session, "session_type", None)
    if raw is None:
        return None
    if raw == "dev":
        return "Developer"
    if raw == "pm":
        return "Project Manager"
    if raw == "teammate":
        return "Teammate"
    if raw == "chat":
        return "Project Manager"  # Legacy fallback for pre-migration sessions
    return _safe_str(raw)


def _safe_str(val, default: str | None = None) -> str | None:
    """Return val as a string if it's a real value, else default."""
    if val is None or not isinstance(val, str | int | float | bool):
        return default
    return str(val)


def _safe_float(val) -> float | None:
    """Return val as a float if it's a real number, else None.

    Handles datetime.datetime objects by converting via .timestamp(),
    which is needed because Popoto stores datetime fields as Python
    datetime objects, not raw floats.
    """
    if isinstance(val, datetime.datetime):
        if val.tzinfo is None:
            # Popoto strips timezone on serialize/deserialize; all datetimes in this
            # system are UTC, so re-attach UTC before converting to avoid local-tz offset
            val = val.replace(tzinfo=datetime.UTC)
        return val.timestamp()
    if isinstance(val, int | float):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return None


def _session_to_pipeline(session) -> PipelineProgress:
    """Convert an AgentSession instance to a PipelineProgress model.

    Routes stage reads through PipelineStateMachine.get_display_progress()
    for sessions with stage_states data (canonical stored-state path).
    Falls back to _parse_stage_states() if PipelineStateMachine raises.
    """
    slug = _safe_str(session.slug) or ""

    if session.stage_states:
        try:
            from bridge.pipeline_state import PipelineStateMachine

            sm = PipelineStateMachine(session)
            progress = sm.get_display_progress()
            stages = [StageState(name=name, status=status) for name, status in progress.items()]
        except Exception:
            # Fallback to direct parse if state machine fails
            stages = _parse_stage_states(session.stage_states)
    else:
        stages = []

    history_list = session.history if isinstance(session.history, list) else None
    events = _parse_history(history_list)

    # Determine current stage
    current = None
    for s in stages:
        if s.is_active:
            current = s.name
            break

    # Resolve project name and metadata
    project_key = _safe_str(session.project_key)
    project_name, project_metadata = _get_project_metadata(project_key)

    # Populate new metadata fields from AgentSession attributes
    status = _safe_str(session.status)
    updated_at = _safe_float(session.updated_at)

    # Compute staleness: running/active sessions with no update in >10 minutes
    is_stale = False
    if status in ("running", "active") and updated_at:
        is_stale = (time.time() - updated_at) > 600  # 10 minutes

    # Extract classification_type (stored in extra_context dict)
    classification_type = None
    extra_context = getattr(session, "extra_context", None)
    if isinstance(extra_context, dict):
        classification_type = extra_context.get("classification_type")
    elif hasattr(session, "classification_type"):
        classification_type = _safe_str(getattr(session, "classification_type", None))

    # Safe int extraction for count fields
    turn_count = getattr(session, "turn_count", None)
    if turn_count is not None:
        try:
            turn_count = int(turn_count)
        except (ValueError, TypeError):
            turn_count = None

    tool_call_count = getattr(session, "tool_call_count", None)
    if tool_call_count is not None:
        try:
            tool_call_count = int(tool_call_count)
        except (ValueError, TypeError):
            tool_call_count = None

    return PipelineProgress(
        agent_session_id=_safe_str(session.agent_session_id) or "",
        session_id=_safe_str(session.session_id),
        session_type=_resolve_persona_display(session),
        status=status,
        slug=slug,
        message_text=_safe_str(session.message_text),
        project_key=project_key,
        project_name=project_name,
        project_metadata=project_metadata,
        branch_name=_safe_str(session.branch_name) or (f"session/{slug}" if slug else None),
        created_at=_safe_float(session.created_at),
        started_at=_safe_float(session.started_at),
        completed_at=_safe_float(session.completed_at),
        updated_at=updated_at,
        parent_agent_session_id=_safe_str(getattr(session, "parent_agent_session_id", None)),
        context_summary=_safe_str(getattr(session, "context_summary", None)),
        expectations=_safe_str(getattr(session, "expectations", None)),
        turn_count=turn_count,
        tool_call_count=tool_call_count,
        watchdog_unhealthy=_safe_str(getattr(session, "watchdog_unhealthy", None)),
        priority=_safe_str(getattr(session, "priority", None)),
        classification_type=classification_type,
        is_stale=is_stale,
        stages=stages,
        current_stage=current,
        events=events,
        issue_url=_safe_str(session.issue_url),
        plan_url=_safe_str(session.plan_url),
        pr_url=_safe_str(session.pr_url),
    )


# === Public query functions ===


def get_all_sessions(limit: int = 15) -> list[PipelineProgress]:
    """Get agent sessions sorted by last activity.

    Active parent sessions always appear (no cap). Inactive parent sessions
    are filtered to those within the configured retention period
    (DASHBOARD_RETENTION_HOURS env var, default 48h), capped at `limit`.

    The limit applies only to top-level (parent) rows. All children of
    included parents are attached regardless of the limit.

    Args:
        limit: Maximum number of inactive parent sessions to show.

    Returns:
        List of top-level PipelineProgress (with children nested), newest first.
    """
    from models.agent_session import AgentSession

    try:
        all_sessions = AgentSession.query.all()
    except Exception as e:
        logger.warning(f"Failed to query AgentSession: {e}")
        return []

    cutoff = time.time() - DASHBOARD_RETENTION_HOURS * 3600

    def _best_timestamp(p: PipelineProgress) -> float:
        """Pick the best available timestamp for ordering/filtering."""
        return p.completed_at or p.updated_at or p.started_at or p.created_at or 0

    # Convert all sessions to PipelineProgress, skipping test data
    all_pipelines = []
    for session in all_sessions:
        if getattr(session, "project_key", None) == "test":
            continue
        try:
            pipeline = _session_to_pipeline(session)
        except Exception:
            logger.debug(f"Skipping corrupt session: {getattr(session, 'agent_session_id', '?')}")
            continue
        if _best_timestamp(pipeline) >= cutoff or pipeline.status in (
            "running",
            "pending",
            "in_progress",
            "active",
            "waiting_for_children",
        ):
            all_pipelines.append(pipeline)

    # Group children under parents (no N+1 queries)
    by_id: dict[str, PipelineProgress] = {p.agent_session_id: p for p in all_pipelines}
    child_ids: set[str] = set()
    for p in all_pipelines:
        if p.parent_agent_session_id and p.parent_agent_session_id in by_id:
            parent = by_id[p.parent_agent_session_id]
            parent.children.append(p)
            child_ids.add(p.agent_session_id)
        # Orphaned children (parent not in list) remain as top-level rows

    top_level = [p for p in all_pipelines if p.agent_session_id not in child_ids]

    # Split into active/inactive, apply limit only to inactive parents
    active = []
    inactive = []
    for p in top_level:
        if p.status in ("running", "pending", "in_progress", "active", "waiting_for_children"):
            active.append(p)
        else:
            inactive.append(p)

    active.sort(key=lambda p: p.updated_at or p.created_at or 0, reverse=True)
    inactive.sort(key=_best_timestamp, reverse=True)

    return active + inactive[:limit]


def get_active_pipelines() -> list[PipelineProgress]:
    """Get active SDLC pipelines (sessions with stage_states that aren't completed).

    Filtered version of get_all_sessions() for backward compatibility.

    Returns:
        List of PipelineProgress for active SDLC pipelines, sorted by last activity.
    """
    all_sessions = get_all_sessions()
    return [p for p in all_sessions if p.stages and p.status not in ("completed", "failed")]


def get_pipeline_detail(agent_session_id: str) -> PipelineProgress | None:
    """Get detailed pipeline information for a specific session.

    Args:
        agent_session_id: The AgentSession agent_session_id to look up.

    Returns:
        PipelineProgress with full details, or None if not found.
    """
    from models.agent_session import AgentSession

    try:
        # AgentSession.query.get() requires a Popoto key object, not a raw string.
        # Filter via all() instead.
        matches = [s for s in AgentSession.query.all() if s.id == agent_session_id]
        if not matches:
            return None
        return _session_to_pipeline(matches[0])
    except Exception as e:
        logger.warning(f"Failed to get pipeline detail for {agent_session_id}: {e}")
        return None


def get_recent_completions(limit: int = 25, page: int = 1) -> list[PipelineProgress]:
    """Get recently completed SDLC pipelines.

    Args:
        limit: Maximum number of results per page.
        page: Page number (1-indexed).

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
        try:
            pipeline = _session_to_pipeline(session)
        except Exception:
            logger.debug(f"Skipping corrupt session: {getattr(session, 'agent_session_id', '?')}")
            continue
        completed.append(pipeline)

    # Sort by completed_at descending
    completed.sort(key=lambda p: p.completed_at or p.created_at or 0, reverse=True)

    # Paginate
    start = (page - 1) * limit
    end = start + limit
    return completed[start:end]
