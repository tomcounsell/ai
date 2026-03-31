"""Data access layer for the SDLC Observer dashboard.

Provides Pydantic serializers for AgentSession.stage_states and history,
plus query functions for active/completed pipelines.

All functions are synchronous (def, not async def) because Popoto uses
synchronous Redis calls. FastAPI runs sync route handlers in a threadpool.
"""

import json
import logging
import os
import re
import time

from pydantic import BaseModel

from config.enums import PersonaType

logger = logging.getLogger(__name__)

# SDLC stages in pipeline order (matches models/agent_session.py)
SDLC_STAGES = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]

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
    """Complete pipeline view for a single AgentSession."""

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
        """Human-friendly name: slug if available, else truncated message."""
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


# === Stage inference from history ===


def _infer_stages_from_history(history_list: list | None) -> list["StageState"]:
    """DEPRECATED: Infer SDLC stage states from session history entries.

    This fallback exists for in-flight sessions created before stage_states
    was eagerly initialized at session creation (pre-#563). It will be removed
    in a future release once all existing sessions have stage_states populated.

    New sessions get stage_states initialized in _push_agent_session() when
    classification_type is "sdlc", so this path should only fire for
    legacy sessions.

    Returns empty list if no stage info found in history.
    """
    logger.warning(
        "DEPRECATED: _infer_stages_from_history() called. "
        "This session lacks stage_states -- it was created before eager initialization (#563). "
        "This fallback will be removed in a future release."
    )

    if not history_list or not isinstance(history_list, list):
        return []

    # Collect mentioned stage names from history
    mentioned_stages = set()
    active_stage = None
    stage_pattern = re.compile(r"^\[stage\]\s*(\w+)", re.IGNORECASE)

    for entry in history_list:
        if not isinstance(entry, str):
            continue
        match = stage_pattern.match(entry)
        if match:
            stage_name = match.group(1).upper()
            if stage_name in SDLC_STAGES:
                mentioned_stages.add(stage_name)

    if not mentioned_stages:
        return []

    # Build stage list: stages mentioned in history are completed,
    # the last mentioned stage is marked in_progress (heuristic)
    last_stage_idx = -1
    for stage_name in mentioned_stages:
        idx = SDLC_STAGES.index(stage_name)
        if idx > last_stage_idx:
            last_stage_idx = idx
            active_stage = stage_name

    stages = []
    for name in SDLC_STAGES:
        if name == active_stage:
            stages.append(StageState(name=name, status="in_progress"))
        elif name in mentioned_stages:
            stages.append(StageState(name=name, status="completed"))
        else:
            idx = SDLC_STAGES.index(name)
            if idx < last_stage_idx and all(SDLC_STAGES[i] in mentioned_stages for i in range(idx)):
                # Earlier stages implied completed
                stages.append(StageState(name=name, status="completed"))
            else:
                stages.append(StageState(name=name, status="pending"))

    return stages


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
    if raw == "chat":
        return "Project Manager"
    return _safe_str(raw)


def _safe_str(val, default: str | None = None) -> str | None:
    """Return val as a string if it's a real value, else default."""
    if val is None or not isinstance(val, str | int | float | bool):
        return default
    return str(val)


def _safe_float(val) -> float | None:
    """Return val as a float if it's a real number, else None."""
    if isinstance(val, int | float):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return None


def _session_to_pipeline(session) -> PipelineProgress:
    """Convert an AgentSession instance to a PipelineProgress model."""
    stages = _parse_stage_states(session.stage_states)

    # Fallback: infer stages from history when stage_states is empty
    history_list = session.history if isinstance(session.history, list) else None
    if not stages and history_list:
        stages = _infer_stages_from_history(history_list)

    events = _parse_history(history_list)

    # Determine current stage
    current = None
    for s in stages:
        if s.is_active:
            current = s.name
            break

    slug = _safe_str(session.slug) or _safe_str(session.work_item_slug) or ""

    # Resolve project name and metadata
    project_key = _safe_str(session.project_key)
    project_name, project_metadata = _get_project_metadata(project_key)

    return PipelineProgress(
        agent_session_id=_safe_str(session.agent_session_id) or "",
        session_id=_safe_str(session.session_id),
        session_type=_resolve_persona_display(session),
        status=_safe_str(session.status),
        slug=slug,
        message_text=_safe_str(session.message_text),
        project_key=project_key,
        project_name=project_name,
        project_metadata=project_metadata,
        branch_name=_safe_str(session.branch_name) or (f"session/{slug}" if slug else None),
        created_at=_safe_float(session.created_at),
        started_at=_safe_float(session.started_at),
        completed_at=_safe_float(session.completed_at),
        last_activity=_safe_float(session.last_activity),
        stages=stages,
        current_stage=current,
        events=events,
        issue_url=_safe_str(session.issue_url),
        plan_url=_safe_str(session.plan_url),
        pr_url=_safe_str(session.pr_url),
    )


# === Public query functions ===


def get_all_sessions(limit: int = 50) -> list[PipelineProgress]:
    """Get agent sessions sorted by last activity.

    Active sessions always appear (no cap). Inactive sessions are filtered
    to those within the configured retention period (DASHBOARD_RETENTION_HOURS
    env var, default 48h), capped at `limit` total.

    Args:
        limit: Maximum number of inactive sessions to show.

    Returns:
        List of PipelineProgress, newest activity first.
    """
    from models.agent_session import AgentSession

    try:
        all_sessions = AgentSession.query.all()
    except Exception as e:
        logger.warning(f"Failed to query AgentSession: {e}")
        return []

    cutoff = time.time() - DASHBOARD_RETENTION_HOURS * 3600
    active = []
    inactive = []

    def _best_timestamp(p: PipelineProgress) -> float:
        """Pick the best available timestamp for ordering/filtering."""
        return p.completed_at or p.last_activity or p.started_at or p.created_at or 0

    for session in all_sessions:
        try:
            pipeline = _session_to_pipeline(session)
        except Exception:
            logger.debug(f"Skipping corrupt session: {getattr(session, 'agent_session_id', '?')}")
            continue
        if pipeline.status in (
            "running",
            "pending",
            "in_progress",
            "active",
            "waiting_for_children",
        ):
            active.append(pipeline)
        else:
            if _best_timestamp(pipeline) >= cutoff:
                inactive.append(pipeline)

    active.sort(key=lambda p: p.last_activity or p.created_at or 0, reverse=True)
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
        session = AgentSession.query.get(agent_session_id)
        if not session:
            return None
        return _session_to_pipeline(session)
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
