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
import re
import subprocess
import time

from pydantic import BaseModel

from agent.pipeline_graph import DISPLAY_STAGES
from config.enums import SessionType

logger = logging.getLogger(__name__)

# Configurable retention for inactive sessions (default 48h)
DASHBOARD_RETENTION_HOURS = int(os.environ.get("DASHBOARD_RETENTION_HOURS", "48"))

# Cache GitHub issue/PR titles to avoid repeated subprocess calls
_github_title_cache: dict[str, str] = {}

# Non-terminal session statuses where a live-process probe is meaningful (#1269).
# Sessions in any other status (completed/failed/abandoned/killed/etc.) skip the
# probe entirely — there's no PID to probe and no operator question to answer.
_NON_TERMINAL_PROBE_STATUSES = frozenset({"running", "active", "paused", "paused_circuit"})


def _check_process_alive(pid: int | None) -> bool | None:
    """Return liveness for ``pid`` via a non-blocking ``os.kill(pid, 0)`` probe.

    Returns:
        ``True``  — process exists in the OS process table (alive).
        ``False`` — ``ProcessLookupError`` raised; the PID is not a live process
                    (ghost — the harness subprocess died but the session record
                    still claims running).
        ``None``  — uncertain. Returned when ``pid`` is None or ``pid <= 0``,
                    or when the kernel returned ``PermissionError`` / generic
                    ``OSError``. Caller should render "unknown" rather than lie.

    Why ``pid <= 0`` returns None instead of probing:
        ``kill(0, sig)`` and ``kill(-pid, sig)`` have process-group semantics on
        Linux/macOS — refuse to probe rather than risk a wrong answer.

    Recycled-PID caveat:
        ``os.kill(pid, 0)`` returns success for whatever process now holds the
        PID. The dashboard mitigates this by pairing the probe result with the
        ``last_evidence_at`` freshness chip — a recycled-PID "alive" still pairs
        with a stale freshness chip the operator can see.

    Performance:
        ``os.kill(pid, 0)`` is a single syscall (no IPC, no blocking on the
        target process). Spike-2 in `docs/plans/dashboard-session-detail-liveness.md`
        confirmed it's safe inline in request handlers without a timeout wrapper.
    """
    if pid is None or pid <= 0:
        # kill(0, ...) and kill(-pid, ...) have process-group semantics on
        # Linux/macOS — refuse to probe rather than risk a wrong answer.
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None  # uncertain — don't lie


def _fetch_github_title(url: str) -> str | None:
    """Return the title of a GitHub issue or PR URL, using a process-level cache."""
    if url in _github_title_cache:
        return _github_title_cache[url]
    try:
        if "/issues/" in url:
            result = subprocess.run(
                ["gh", "issue", "view", url, "--json", "title", "--jq", ".title"],
                capture_output=True,
                text=True,
                timeout=5,  # timeout-guard: allow
            )
        elif "/pull/" in url:
            result = subprocess.run(
                ["gh", "pr", "view", url, "--json", "title", "--jq", ".title"],
                capture_output=True,
                text=True,
                timeout=5,  # timeout-guard: allow
            )
        else:
            return None
        if result.returncode == 0:
            title = result.stdout.strip()
            if title:
                _github_title_cache[url] = title
                return title
    except Exception:
        pass
    return None


_SYSTEM_PROMPT_PREFIXES = ("PROJECT:", "[Prior session context")
_INTERNAL_SENDERS = {"valor-session (eng)", "None", ""}


def _extract_from_system_prompt(message_text: str) -> str | None:
    """Extract a human-readable label from a session system-prompt message_text.

    Tries MESSAGE: content first, then FROM: if it looks human-authored,
    then falls back to None so the caller can use a type/project label.
    """
    # Try MESSAGE: marker — the actual user task text
    msg_match = re.search(r"^MESSAGE:\s*(.+)$", message_text, re.MULTILINE)
    if msg_match:
        msg = msg_match.group(1).strip()
        if msg and msg != "None":
            return msg[:80] + ("..." if len(msg) > 80 else "")

    # Try FROM: — e.g. "Tom in PM: Valor", skip internal sender names
    from_match = re.search(r"^FROM:\s*(.+)$", message_text, re.MULTILINE)
    if from_match:
        sender = from_match.group(1).strip()
        if sender and sender not in _INTERNAL_SENDERS:
            return sender

    return None


# Match GitHub issue/PR URLs anywhere in a string.
# Intentionally permissive on owner/repo to tolerate org-scoped and nested paths.
_GITHUB_ISSUE_URL_RE = re.compile(r"https://github\.com/[^\s/]+/[^\s/]+/issues/\d+")
_GITHUB_PR_URL_RE = re.compile(r"https://github\.com/[^\s/]+/[^\s/]+/pull/\d+")


def _extract_github_links(events: list) -> tuple[str | None, str | None]:
    """Scan session events for GitHub issue/PR URLs.

    Used as a render-time fallback when an AgentSession's ``issue_url`` /
    ``pr_url`` fields are None but the links are mentioned in history (e.g.
    a lifecycle event like ``"PR opened: https://github.com/..."``). This
    backfills historical sessions that predate link capture without needing
    a data migration.

    The first URL found for each kind wins — this matches how ``set_link``
    behaves for the "set" case (though set_link will overwrite on repeat,
    the most recent write in history is what an operator wants to see).
    We iterate from newest to oldest so the most recent URL wins.

    Args:
        events: List of PipelineEvent objects, any objects with a ``text``
            attribute, or plain strings.

    Returns:
        (issue_url, pr_url) — either or both may be None if nothing found.
    """
    if not events:
        return None, None

    issue_url: str | None = None
    pr_url: str | None = None

    # Walk newest → oldest so the freshest URL wins.
    for event in reversed(events):
        if issue_url and pr_url:
            break
        if hasattr(event, "text"):
            text = event.text or ""
        elif isinstance(event, str):
            text = event
        else:
            continue
        if not text:
            continue

        if not issue_url:
            match = _GITHUB_ISSUE_URL_RE.search(text)
            if match:
                issue_url = match.group(0)
        if not pr_url:
            match = _GITHUB_PR_URL_RE.search(text)
            if match:
                pr_url = match.group(0)

    return issue_url, pr_url


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
    event_type: str | None = None  # raw event_type from history for template styling


class PipelineProgress(BaseModel):
    """Complete pipeline view for a single AgentSession.

    Fields:
        agent_session_id: Unique identifier for this agent session.
        session_id: Telegram/local session identifier.
        session_type: Display persona ("Engineer" or "Teammate"; see
            _resolve_persona_display). Legacy records may surface a raw fallback.
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
        thread_first_created_at: Timestamp the thread's first (earliest) run was created, carried
            forward across resumes. None on never-resumed / pre-migration records.
        thread_turn_count: Cumulative turn count from prior completed runs in this thread.
        thread_tool_call_count: Cumulative tool call count from prior completed runs in this thread.
        thread_run_count: Number of runs (resumes) this thread has had, including the current one.
        unhealthy_reason: Reason string when flagged unhealthy, None when healthy.
        priority: Session priority (urgent, high, normal, low).
        classification_type: Session classification (sdlc, qa, etc.).
        is_stale: True if session is running but updated_at is >10 minutes ago.
    """

    agent_session_id: str
    session_id: str | None = None
    session_type: str | None = None
    status: str | None = None
    slug: str | None = None
    initiator: str | None = None  # "telegram", "email", "local", or short parent session id
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

    # === Thread-level rollup (issue: dashboard-thread-timing-aggregation) ===
    # Raw fields carried forward from prior completed runs in the same thread.
    thread_first_created_at: float | None = None
    thread_turn_count: int | None = None
    thread_tool_call_count: int | None = None
    thread_run_count: int | None = None

    # Folded display values: per-thread totals (prior runs' rollup + this
    # run's in-flight counters), computed once in
    # _session_to_pipeline so JSON consumers never have to
    # re-derive the fold. Always populated (never None) — on a
    # never-resumed / pre-migration record these equal the per-run values
    # exactly, so the dashboard renders identically to before this feature.
    thread_display_turn_count: int = 0
    thread_display_tool_call_count: int = 0
    thread_display_started_at: float | None = None
    thread_display_run_count: int = 1

    unhealthy_reason: str | None = None
    priority: str | None = None
    classification_type: str | None = None
    is_stale: bool = False

    # Ledger sessions (#2042) are anchor records for locally-run /do-sdlc
    # sessions — the worker never executes them, so turn/tool telemetry never
    # populates. Every stage_states write refreshes ``updated_at`` via
    # ``session.save()`` (see ``tools/sdlc_session_ensure.py::_last_activity_at``),
    # so ``updated_at`` is already the correct "most recent progress" signal
    # for these sessions instead of started_at/tool_call_count.
    is_ledger: bool = False

    # Per-session token + cost accounting (issue #1128).
    # Always emitted (default 0 / 0.0) for forward-compat with existing
    # JSON consumers — never None, never omitted.
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cost_usd: float = 0.0

    # In-flight visibility (issue #1172, Pillar A). Operator-facing
    # liveness signals — what is the agent doing right now? All four are
    # nullable; sessions started before the deploy keep None until their
    # next tool / turn boundary. ``last_evidence_at`` is derived in
    # ``_session_to_json`` as max of every available evidence timestamp.
    current_tool_name: str | None = None
    last_tool_use_at: float | None = None
    last_turn_at: float | None = None
    recent_thinking_excerpt: str | None = None
    last_evidence_at: float | None = None

    # BYOB scheduler-layer serialization (issue #1256, Decision 2).
    # Surfaced so operators can see why a real-Chrome session is being
    # deferred when another holds the slot. Default False keeps the
    # field invisible for ordinary sessions.
    requires_real_chrome: bool = False

    # === Liveness signals (issue #1269) ===
    # Surfaced from AgentSession heartbeat / recovery fields so the dashboard
    # modal can answer "is this session actually progressing right now?" without
    # the operator running `valor-session status --id <id>`. ``harness_pid`` is
    # subprocess-scoped (cleared at proc.communicate() return) so the
    # ``process_alive`` probe is meaningful. ``process_alive`` is None for
    # terminal-status sessions (probe skipped) and for sessions where the probe
    # was uncertain (PID None, negative, or PermissionError).
    harness_pid: int | None = None
    last_heartbeat_at: float | None = None
    last_sdk_heartbeat_at: float | None = None
    last_stdout_at: float | None = None
    recovery_attempts: int = 0
    reprieve_count: int = 0
    process_alive: bool | None = None

    # === Runner exit classification + PM subprocess identity (issue #1648) ===
    # exit_reason uses the runner's exit-classification vocabulary; pm_pid is
    # the current turn's `claude -p` subprocess pid. Nullable — readers
    # tolerate absent fields on old records.
    exit_reason: str | None = None
    pm_pid: int | None = None

    # === Headless-runner resume scalars (#1924, Success Criterion 3) ===
    # What a simple resume would consume: the Dev subagent continuation
    # handle, the exact runner working dir, and the CLI version the session
    # ran under. All nullable — records predating the cutover lack them.
    dev_agent_id: str | None = None
    runner_cwd: str | None = None
    claude_version: str | None = None

    # Output routing state (issue #1647)
    # True once a user-facing message has been routed for this session.
    user_facing_routed: bool = False

    # === Stall advisory (issue #1538) ===
    # Read-only classification for non-terminal sessions. Populated by
    # _session_to_pipeline() via classify_session_stall(). None for terminal
    # sessions (skipped) and when classification raises (fail-soft).
    stall_advisory: str | None = None  # "healthy", "suspect", "stalled", or None
    stall_advisory_reason: str | None = None  # short reason slug

    # SDLC state
    stages: list[StageState] = []
    current_stage: str | None = None
    events: list[PipelineEvent] = []

    # Links
    issue_url: str | None = None
    plan_url: str | None = None
    pr_url: str | None = None

    # Claude Code resume
    claude_session_uuid: str | None = None

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
        """Human-friendly name: slug > issue/PR title > context_summary > message > type•project."""
        if self.slug:
            return self.slug
        for url in (self.issue_url, self.pr_url):
            if url:
                title = _fetch_github_title(url)
                if title:
                    return title
        if self.context_summary:
            return self.context_summary
        if self.message_text:
            if any(self.message_text.startswith(p) for p in _SYSTEM_PROMPT_PREFIXES):
                extracted = _extract_from_system_prompt(self.message_text)
                if extracted:
                    return extracted
                # Nothing useful in the prompt — show type • project
                parts = [p for p in (self.session_type, self.project_key) if p]
                return " • ".join(parts) if parts else self.agent_session_id or "unknown"
            text = self.message_text[:60]
            if len(self.message_text) > 60:
                text += "..."
            return text
        return self.agent_session_id or "unknown"

    @property
    def message_user_text(self) -> str | None:
        """The human message portion of message_text (after system prompt).

        For system-prompt messages (starting with PROJECT: or [Prior session context),
        extracts the MESSAGE: line and everything after it. Falls back to the FROM:
        block if MESSAGE: is absent (e.g. truncated storage). For plain messages,
        returns message_text unchanged.
        """
        if not self.message_text:
            return None
        if not any(self.message_text.startswith(p) for p in _SYSTEM_PROMPT_PREFIXES):
            return self.message_text
        # Prefer MESSAGE: marker — the actual user task text
        msg_idx = self.message_text.find("\nMESSAGE:")
        if msg_idx != -1:
            return self.message_text[msg_idx + 1 :]  # strip leading newline
        # Fallback: FROM: block (may still include metadata like SESSION_ID)
        from_idx = self.message_text.find("\nFROM:")
        if from_idx != -1:
            return self.message_text[from_idx + 1 :]
        return None

    @property
    def message_system_prompt(self) -> str | None:
        """The system prompt portion of message_text (before MESSAGE:/FROM: block).

        Returns None when message_text is not a system-prompt-style message.
        """
        if not self.message_text:
            return None
        if not any(self.message_text.startswith(p) for p in _SYSTEM_PROMPT_PREFIXES):
            return None
        # Split at MESSAGE: first, then FROM: as fallback
        msg_idx = self.message_text.find("\nMESSAGE:")
        if msg_idx != -1:
            return self.message_text[:msg_idx].rstrip()
        from_idx = self.message_text.find("\nFROM:")
        if from_idx != -1:
            return self.message_text[:from_idx].rstrip()
        return self.message_text


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


_STAGE_STATUS_EMOJI = {
    "completed": "✓",
    "skipped": "↷",
    "failed": "✗",
    "in_progress": "◉",
    "ready": "○",
    "pending": "·",
}

_STAGE_STATUS_ORDER = ["completed", "skipped", "failed", "in_progress", "ready", "pending"]


def _stage_diff_text(prev: dict | None, curr: dict) -> str:
    """Summarise what changed between two stage snapshots.

    Returns a compact string like "PLAN completed · BUILD ready" listing only
    the stages that changed, ordered by the SDLC stage order.  Internal keys
    (prefixed with `_`) are ignored.  When there is no previous snapshot
    (first stage event), returns a summary of all non-pending stages.
    """
    named_keys = [k for k in curr if not k.startswith("_")]

    if prev is None:
        changed = [(k, curr[k]) for k in named_keys if curr.get(k) not in ("pending", None)]
    else:
        changed = [
            (k, curr[k])
            for k in named_keys
            if curr.get(k) != prev.get(k) and curr.get(k) not in (None,)
        ]

    if not changed:
        return "stage snapshot (no changes)"

    # Order by SDLC stage list when possible
    def _sort_key(item):
        name, status = item
        try:
            stage_idx = DISPLAY_STAGES.index(name)
        except ValueError:
            stage_idx = 999
        return (stage_idx,)

    changed.sort(key=_sort_key)

    parts = []
    for name, status in changed:
        emoji = _STAGE_STATUS_EMOJI.get(status, "")
        parts.append(f"{emoji} {name} {status}" if emoji else f"{name} {status}")
    return " · ".join(parts)


def _parse_history(history_list: list | None) -> list[PipelineEvent]:
    """Parse session history entries into typed PipelineEvent objects."""
    if not history_list or not isinstance(history_list, list):
        return []

    events = []
    prev_stage_snapshot: dict | None = None

    for entry in history_list:
        if isinstance(entry, str):
            # Legacy format: "[role] text"
            if entry.startswith("[") and "]" in entry:
                bracket_end = entry.index("]")
                role = entry[1:bracket_end]
                text = entry[bracket_end + 2 :] if bracket_end + 2 < len(entry) else ""
            else:
                role = "system"
                text = entry
            events.append(PipelineEvent(role=role, text=text, event_type=role))
        elif isinstance(entry, dict):
            event_type = entry.get("event_type") or entry.get("role") or "system"
            raw_text = entry.get("text", "")

            if event_type == "stage":
                data = entry.get("data") or {}
                stage_snapshot = data.get("stages") if isinstance(data, dict) else None
                if stage_snapshot:
                    # Compare only visible (non-internal) keys to skip duplicates
                    # caused by internal fields like _verdicts changing timestamps.
                    visible = {k: v for k, v in stage_snapshot.items() if not k.startswith("_")}
                    prev_visible = (
                        {k: v for k, v in prev_stage_snapshot.items() if not k.startswith("_")}
                        if prev_stage_snapshot
                        else None
                    )
                    if visible == prev_visible:
                        continue
                    text = _stage_diff_text(prev_stage_snapshot, stage_snapshot)
                    prev_stage_snapshot = stage_snapshot
                elif raw_text and raw_text != "bulk=update":
                    text = raw_text
                else:
                    text = "stage update"
            elif event_type == "user":
                # Trim long PM prompts to the first meaningful line
                first_line = (raw_text or "").splitlines()[0] if raw_text else ""
                text = first_line[:120] + ("…" if len(first_line) > 120 else "")
            elif event_type in (
                "granite_user_routed",
                "granite_complete_routed",
                "granite_delivery_recovered_via_outbox",
                "granite_delivery_dropped",
            ):
                text = raw_text or event_type
            elif event_type == "turn_history" or entry.get("type") == "turn_history":
                # Headless-runner turn mirror (#1924): label with the actor
                # (pm|dev) so PM vs Dev turns are distinguishable in the feed.
                # The ``type``-key fallback tolerates mirror entries written
                # before the writer dual-keyed them (exit_anomaly precedent).
                event_type = "turn_history"
                actor = entry.get("actor") or "pm"
                text = f"[{actor}] {raw_text}" if raw_text else f"[{actor}]"
            elif entry.get("type") == "exit_anomaly":
                reason = entry.get("exit_reason", "unknown")
                text = f"exit anomaly: {reason}"
                event_type = "exit_anomaly"
            else:
                text = raw_text or str(entry)

            # Granite events use "ts" key; standard events use "timestamp".
            ts = _safe_float(entry.get("timestamp") or entry.get("ts"))
            events.append(
                PipelineEvent(
                    role=event_type,
                    text=text,
                    timestamp=ts,
                    event_type=event_type,
                )
            )

    return events


def _resolve_initiator(session_id: str | None, parent_id: str | None) -> str | None:
    """Derive a human-readable initiator label from session_id / parent."""
    if parent_id:
        return f"session/{parent_id[:8]}"
    if not session_id:
        return None
    if session_id.startswith("tg_"):
        return "telegram"
    if "email" in session_id.lower():
        return "email"
    return "local"


def _resolve_persona_display(session) -> str | None:
    """Map session_type into a dashboard display persona.

    session_type is the sole discriminator:
      session_type="teammate"  → "Teammate"
      session_type="eng"       → "Engineer"
    """
    raw = getattr(session, "session_type", None)
    if raw is None:
        return None
    if raw == SessionType.TEAMMATE:
        return "Teammate"
    if raw == SessionType.ENG:
        return "Engineer"
    if raw == "chat":
        return "Engineer"  # Legacy fallback for pre-migration sessions
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
            pass
        try:
            dt = datetime.datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.UTC)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None
    return None


def _safe_nullable_int(val) -> int | None:
    """Return val as an int if it's a real integer value, else None.

    Used for nullable int fields (pm_pid) where MagicMock or other
    non-integer values should coerce to None rather than raise.
    """
    if val is None:
        return None
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _derive_issue_number(session) -> int | None:
    """Best-effort issue number for a session: the write-once mirror field
    set by ``ensure_session()``, falling back to parsing ``issue_url``.

    ``isinstance(..., int)`` rather than a bare truthiness check: a
    ``MagicMock()`` (used pervasively as an AgentSession double throughout
    this test suite) auto-vivifies ANY attribute access, including
    ``issue_number`` -- a truthiness check would treat that auto-vivified
    mock as a real (garbage) issue number and go on to mint a spurious
    ``PipelineLedger`` record keyed on its repr. Never raises.
    """
    issue_number = getattr(session, "issue_number", None)
    if isinstance(issue_number, int) and issue_number > 0:
        return issue_number
    try:
        from tools._sdlc_utils import _parse_issue_number_from_url

        return _parse_issue_number_from_url(getattr(session, "issue_url", None))
    except Exception:
        return None


def _resolve_issue_ledger(issue_number: int | None):
    """Resolve the ``(target_repo, ledger)`` pair for ``issue_number`` --
    lease-first (peek, no run_id claim) with an env-fallback (issue #2012
    task 2, reader side). Returns ``(None, None)`` when ``issue_number`` is
    falsy or ``target_repo`` cannot be resolved at all -- the defined
    empty-ledger outcome (Risk 5): never touch a phantom
    ``PipelineLedger[(None, issue)]`` key. Never raises."""
    if not issue_number:
        return None, None
    try:
        from agent.pipeline_ledger import PipelineLedger
        from tools._sdlc_utils import resolve_target_repo_for_read

        target_repo = resolve_target_repo_for_read(issue_number)
        if not target_repo:
            return None, None
        return target_repo, PipelineLedger.get_or_create(target_repo, issue_number)
    except Exception as e:
        logger.debug(f"_resolve_issue_ledger failed for issue #{issue_number}: {e}")
        return None, None


def _ledger_has_data(ledger) -> bool:
    """True iff ``ledger`` carries any recorded stage state -- distinguishes
    a genuinely-written record from a freshly-created, still-empty
    ``PipelineLedger.get_or_create()`` result. Never raises."""
    if ledger is None:
        return False
    try:
        raw = getattr(ledger, "stage_states_json", None)
        return bool(raw) and bool(json.loads(raw))
    except Exception:
        return False


def _session_has_stage_data(session) -> bool:
    """True iff this session's own ``stage_states`` is populated, OR its
    issue has recorded data in the issue-keyed ``PipelineLedger`` (issue
    #2012 task 2). A takeover session whose writes all landed on the
    ledger (never on this particular session's ``stage_states`` field)
    must not be filtered out of dashboard listings that gate on "has this
    session recorded any SDLC progress"."""
    if session.stage_states:
        return True
    _, ledger = _resolve_issue_ledger(_derive_issue_number(session))
    return _ledger_has_data(ledger)


def _resolve_display_stages(session) -> list[StageState]:
    """Resolve stage display data for the dashboard.

    Issue-keyed ledger first, with a retained session-state fallback (issue
    #2012 task 2) -- mirrors the reader resolution in
    ``tools/sdlc_stage_query.py``: route through
    ``PipelineStateMachine.for_issue()`` ONLY when the ledger carries
    actual recorded data. Otherwise falls back to the existing
    session-keyed ``PipelineStateMachine(session)`` read (or
    ``_parse_stage_states()`` on exception) -- byte-identical to
    pre-#2012 behavior for issues the ledger hasn't been written to yet.
    """
    issue_number = _derive_issue_number(session)
    target_repo, ledger = _resolve_issue_ledger(issue_number)
    if _ledger_has_data(ledger):
        try:
            from agent.pipeline_state import PipelineStateMachine

            sm = PipelineStateMachine.for_issue(target_repo, issue_number)
            progress = sm.get_display_progress()
            return [StageState(name=name, status=status) for name, status in progress.items()]
        except Exception:
            pass  # fall through to the session-keyed path below -- never crash the dashboard

    if session.stage_states:
        try:
            from agent.pipeline_state import PipelineStateMachine

            sm = PipelineStateMachine(session)
            progress = sm.get_display_progress()
            return [StageState(name=name, status=status) for name, status in progress.items()]
        except Exception:
            # Fallback to direct parse if state machine fails
            return _parse_stage_states(session.stage_states)
    return []


def _session_to_pipeline(session) -> PipelineProgress:
    """Convert an AgentSession instance to a PipelineProgress model.

    Routes stage reads through ``_resolve_display_stages()`` (issue-keyed
    ledger first, session-state fallback -- issue #2012 task 2).
    """
    slug = _safe_str(session.slug) or ""

    stages = _resolve_display_stages(session)

    history_list = session.history if isinstance(session.history, list) else None
    events = _parse_history(history_list)

    from agent.session_health import _is_ledger

    is_ledger = _is_ledger(session)

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

    # === Thread-level rollup (issue: dashboard-thread-timing-aggregation) ===
    # Raw ORM fields may be None/unset on pre-migration records or the very
    # first run of a thread — coerce safely, no crash.
    thread_first_created_at = _safe_float(getattr(session, "thread_first_created_at", None))

    thread_turn_count = getattr(session, "thread_turn_count", None)
    if thread_turn_count is not None:
        try:
            thread_turn_count = int(thread_turn_count)
        except (ValueError, TypeError):
            thread_turn_count = None

    thread_tool_call_count = getattr(session, "thread_tool_call_count", None)
    if thread_tool_call_count is not None:
        try:
            thread_tool_call_count = int(thread_tool_call_count)
        except (ValueError, TypeError):
            thread_tool_call_count = None

    thread_run_count = getattr(session, "thread_run_count", None)
    if thread_run_count is not None:
        try:
            thread_run_count = int(thread_run_count)
        except (ValueError, TypeError):
            thread_run_count = None

    # Fold per-thread rollup + this run's in-flight counters into display
    # values. When all thread_* fields are null (never-resumed thread /
    # pre-migration record), these equal the per-run values exactly.
    thread_display_turn_count = (thread_turn_count or 0) + (turn_count or 0)
    thread_display_tool_call_count = (thread_tool_call_count or 0) + (tool_call_count or 0)
    thread_display_started_at = thread_first_created_at or _safe_float(session.created_at)
    thread_display_run_count = thread_run_count or 1

    # Per-session token + cost fields (issue #1128). Always coerced to
    # numeric (0 / 0.0) so `/dashboard.json` never returns None here.
    def _to_int(val, default: int = 0) -> int:
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return default

    def _to_float(val, default: float = 0.0) -> float:
        try:
            return float(val or 0.0)
        except (TypeError, ValueError):
            return default

    total_input_tokens = _to_int(getattr(session, "total_input_tokens", 0))
    total_output_tokens = _to_int(getattr(session, "total_output_tokens", 0))
    total_cache_read_tokens = _to_int(getattr(session, "total_cache_read_tokens", 0))
    total_cost_usd = _to_float(getattr(session, "total_cost_usd", 0.0))

    # Pillar A in-flight visibility (issue #1172). Coerce to floats so
    # JSON consumers see a stable shape; absent timestamps stay None.
    current_tool_name = _safe_str(getattr(session, "current_tool_name", None))
    last_tool_use_at = _safe_float(getattr(session, "last_tool_use_at", None))
    last_turn_at = _safe_float(getattr(session, "last_turn_at", None))
    recent_thinking_excerpt = _safe_str(getattr(session, "recent_thinking_excerpt", None))

    # Derive ``last_evidence_at`` as the max of every available evidence
    # timestamp (heartbeats, stdout, tool, turn, compaction). None if every
    # contributing field is None — the dashboard renders None as "no
    # evidence yet" rather than synthesizing a misleading zero.
    evidence_candidates = [
        _safe_float(getattr(session, "last_heartbeat_at", None)),
        _safe_float(getattr(session, "last_sdk_heartbeat_at", None)),
        _safe_float(getattr(session, "last_stdout_at", None)),
        last_tool_use_at,
        last_turn_at,
        _safe_float(getattr(session, "last_compaction_ts", None)),
    ]
    evidence_present = [t for t in evidence_candidates if t is not None]
    last_evidence_at = max(evidence_present) if evidence_present else None

    # === Liveness fields (issue #1269) ===
    # Read the new ORM fields and probe the harness PID for non-terminal
    # sessions only. The probe is a single os.kill(pid, 0) syscall — no IPC,
    # no blocking, no cache (spike-3 ruled it out).
    raw_pid = getattr(session, "harness_pid", None)
    try:
        harness_pid = int(raw_pid) if raw_pid is not None else None
    except (TypeError, ValueError):
        harness_pid = None
    last_heartbeat_at = _safe_float(getattr(session, "last_heartbeat_at", None))
    last_sdk_heartbeat_at = _safe_float(getattr(session, "last_sdk_heartbeat_at", None))
    last_stdout_at = _safe_float(getattr(session, "last_stdout_at", None))
    recovery_attempts = _to_int(getattr(session, "recovery_attempts", 0))
    reprieve_count = _to_int(getattr(session, "reprieve_count", 0))

    # Probe only for non-terminal status — completed/failed/etc. sessions have
    # no live process to interrogate, and surfacing a probe result would be
    # misleading. None means "not probed" (or probe was uncertain).
    process_alive: bool | None = None
    if status in _NON_TERMINAL_PROBE_STATUSES:
        process_alive = _check_process_alive(harness_pid)

    # Resolve issue/PR links with a history fallback. When do-build /
    # do-issue run, they shell out to `gh` which emits the URL to stdout
    # but doesn't always make it back onto the AgentSession model fields.
    # Scanning the already-loaded events list for GitHub URLs backfills
    # existing sessions without a data migration and is cheap (bounded
    # regex over in-memory strings).
    issue_url = _safe_str(session.issue_url)
    pr_url = _safe_str(session.pr_url)
    if not issue_url or not pr_url:
        fallback_issue, fallback_pr = _extract_github_links(events)
        if not issue_url:
            issue_url = fallback_issue
        if not pr_url:
            pr_url = fallback_pr

    # === Stall advisory (issue #1538) ===
    # Classify non-terminal sessions only — fail-soft, never breaks dashboard.
    stall_advisory: str | None = None
    stall_advisory_reason: str | None = None
    try:
        from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES_LOCAL

        # Ledgers (#2042) are anchor records for locally-run /do-sdlc sessions
        # — they never produce SDK telemetry (no turn/tool events), so the
        # classifier reads their empty timeline as never-started/stalled. The
        # stall-advisory reflection already excludes ledgers for this same
        # reason (reflections/stall_advisory.py); mirror that guard here.
        if status not in _TERMINAL_STATUSES_LOCAL and not is_ledger:
            from agent.session_stall_classifier import classify_session_stall
            from agent.session_telemetry import read_session_timeline

            _stall_events = read_session_timeline(
                _safe_str(session.session_id) or _safe_str(session.agent_session_id) or ""
            )
            _verdict = classify_session_stall(_stall_events, session=session)
            stall_advisory = _verdict.level
            stall_advisory_reason = _verdict.reason
    except Exception:
        pass  # fail-soft: advisory never breaks dashboard rendering

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
        initiator=_resolve_initiator(
            _safe_str(session.session_id),
            _safe_str(getattr(session, "parent_agent_session_id", None)),
        ),
        context_summary=_safe_str(getattr(session, "context_summary", None)),
        expectations=_safe_str(getattr(session, "expectations", None)),
        turn_count=turn_count,
        tool_call_count=tool_call_count,
        thread_first_created_at=thread_first_created_at,
        thread_turn_count=thread_turn_count,
        thread_tool_call_count=thread_tool_call_count,
        thread_run_count=thread_run_count,
        thread_display_turn_count=thread_display_turn_count,
        thread_display_tool_call_count=thread_display_tool_call_count,
        thread_display_started_at=thread_display_started_at,
        thread_display_run_count=thread_display_run_count,
        unhealthy_reason=_safe_str(getattr(session, "unhealthy_reason", None)),
        priority=_safe_str(getattr(session, "priority", None)),
        classification_type=classification_type,
        is_stale=is_stale,
        is_ledger=is_ledger,
        stages=stages,
        current_stage=current,
        events=events,
        issue_url=issue_url,
        plan_url=_safe_str(session.plan_url),
        pr_url=pr_url,
        claude_session_uuid=_safe_str(getattr(session, "claude_session_uuid", None)),
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_cache_read_tokens=total_cache_read_tokens,
        total_cost_usd=total_cost_usd,
        current_tool_name=current_tool_name,
        last_tool_use_at=last_tool_use_at,
        last_turn_at=last_turn_at,
        recent_thinking_excerpt=recent_thinking_excerpt,
        last_evidence_at=last_evidence_at,
        requires_real_chrome=bool(getattr(session, "requires_real_chrome", False)),
        harness_pid=harness_pid,
        last_heartbeat_at=last_heartbeat_at,
        last_sdk_heartbeat_at=last_sdk_heartbeat_at,
        last_stdout_at=last_stdout_at,
        recovery_attempts=recovery_attempts,
        reprieve_count=reprieve_count,
        process_alive=process_alive,
        exit_reason=_safe_str(getattr(session, "exit_reason", None)),
        pm_pid=_safe_nullable_int(getattr(session, "pm_pid", None)),
        dev_agent_id=_safe_str(getattr(session, "dev_agent_id", None)),
        runner_cwd=_safe_str(getattr(session, "runner_cwd", None)),
        claude_version=_safe_str(getattr(session, "claude_version", None)),
        user_facing_routed=bool(getattr(session, "user_facing_routed", False)),
        stall_advisory=stall_advisory,
        stall_advisory_reason=stall_advisory_reason,
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

    # Convert all sessions to PipelineProgress, skipping test data.
    # The env-fallback repo resolution inside `_session_to_pipeline` is
    # issue-independent and stable, so resolve it once per request instead of
    # once per session — collapses an O(N·subprocess) `gh repo view` fan-out
    # that made dashboard.json take ~20s at realistic session counts (#2122).
    from tools._sdlc_utils import cached_target_repo_resolution

    all_pipelines = []
    with cached_target_repo_resolution():
        for session in all_sessions:
            if not session.id:
                logger.warning(
                    "Skipping session with no id (partial write): "
                    f"status={session.status}, updated_at={session.updated_at}"
                )
                continue
            if getattr(session, "project_key", None) == "test":
                continue
            try:
                pipeline = _session_to_pipeline(session)
            except Exception:
                logger.debug(
                    f"Skipping corrupt session: {getattr(session, 'agent_session_id', '?')}"
                )
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

    active.sort(key=lambda p: p.started_at or p.created_at or 0, reverse=True)
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
        session = AgentSession.get_by_id(agent_session_id)
        if session is None:
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
        if not _session_has_stage_data(session):
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
