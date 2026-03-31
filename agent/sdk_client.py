"""
Claude Agent SDK client wrapper for Valor.

This module provides a wrapper around ClaudeSDKClient configured for Valor's use case:
- Loads system prompt via the configurable persona system
- Configures permission mode for autonomous operation
- Handles session management
- Extracts text response from message stream

Authentication strategy (subscription-first):
    The SDK spawns Claude Code CLI as a subprocess. By NOT passing
    ANTHROPIC_API_KEY in the env, the CLI falls back to OAuth/subscription
    auth from `claude login` — using the Max plan instead of API credits.

    If Anthropic patches this fallback, known alternatives:
    - CLIProxyAPI (github.com/luispater/CLIProxyAPI): HTTP proxy that swaps
      API key headers for OAuth Bearer tokens. Any Anthropic-format client
      can go through it to use subscription auth.
    - Pi Coding Agent (github.com/badlogic/pi-mono): Independent coding agent
      with native `pi /login` subscription auth and --mode rpc for headless
      programmatic control. Fewer built-in tools but subscription-native.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

import psutil
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from agent.agent_definitions import get_agent_definitions
from agent.hooks import build_hooks_config
from agent.worktree_manager import WORKTREES_DIR, validate_workspace
from config.enums import ClassificationType, PersonaType, SessionType
from utils.github_patterns import ISSUE_NUMBER_RE as _ISSUE_NUMBER_RE
from utils.github_patterns import PR_NUMBER_RE as _PR_NUMBER_RE

logger = logging.getLogger(__name__)


# === Client Registry ===
# Module-level registry of active SDK clients keyed by session_id.
# In-memory only (intentionally not persisted). On crash/reboot, the dict
# is empty and recovered sessions create fresh clients. See plan doc for
# crash safety analysis.
_active_clients: dict[str, "ClaudeSDKClient"] = {}

# === Stop Reason Registry ===
# Stores the stop_reason from the most recent ResultMessage for each session.
# Populated by ValorAgent.query(), consumed by session_queue after query completes.
# In-memory only — cleared when the session finishes.
_session_stop_reasons: dict[str, str] = {}

# === Activity Tracking ===
# Tracks the timestamp of the last tool call or log output for each session.
# Used by the watchdog heartbeat for activity-based stall detection instead
# of hard wall-clock timeouts. Updated on each tool call callback and log output.
# In-memory only — reset on crash/reboot (new sessions start fresh).
_last_activity_timestamps: dict[str, float] = {}

# Configurable inactivity threshold (seconds). Sessions idle longer than this
# are considered stalled. Active sessions producing tool calls/logs are never
# interrupted regardless of total runtime.
SDK_INACTIVITY_TIMEOUT_SECONDS = int(os.environ.get("SDK_INACTIVITY_TIMEOUT_SECONDS", 300))


class CircuitOpenError(RuntimeError):
    """Raised when the Anthropic circuit breaker is open.

    The worker loop catches this specifically to leave the session as pending
    (instead of marking it failed) so the health check can retry when
    the circuit closes.
    """

    pass


# === Anthropic Circuit Breaker ===
# Protects against sustained Anthropic API failures. When open, queries fail fast
# instead of accumulating timeouts. Registered with DependencyHealth for diagnostics.
_anthropic_circuit = None  # Lazy-initialized to avoid import cycles


def _get_anthropic_circuit():
    """Get or create the Anthropic circuit breaker (lazy singleton)."""
    global _anthropic_circuit
    if _anthropic_circuit is None:
        from bridge.resilience import CircuitBreaker

        _anthropic_circuit = CircuitBreaker(
            name="anthropic",
            failure_threshold=5,
            failure_window=60.0,
            half_open_interval=30.0,
            on_open=lambda: logger.warning(
                "Anthropic circuit OPEN — queries will fail fast until recovery"
            ),
            on_close=lambda: logger.info("Anthropic circuit CLOSED — service recovered"),
        )
        # Register with global health tracker
        try:
            from bridge.health import get_health

            get_health().register("anthropic", _anthropic_circuit)
        except Exception:
            pass  # Non-fatal
    return _anthropic_circuit


def get_stop_reason(session_id: str) -> str | None:
    """Get and consume the stop_reason for a completed session query."""
    return _session_stop_reasons.pop(session_id, None)


def record_session_activity(session_id: str) -> None:
    """Record that a session produced activity (text output or result message).

    Called on text block output and result messages during SDK query execution.
    The watchdog uses this to detect stalls based on inactivity rather than
    wall-clock duration.
    """
    _last_activity_timestamps[session_id] = time.time()


def get_session_last_activity(session_id: str) -> float | None:
    """Get the timestamp of the last activity for a session.

    Returns:
        Unix timestamp of last tool call or log output, or None if
        no activity has been recorded for this session.
    """
    return _last_activity_timestamps.get(session_id)


def clear_session_activity(session_id: str) -> None:
    """Remove activity tracking for a completed/abandoned session."""
    _last_activity_timestamps.pop(session_id, None)


def _get_prior_session_uuid(session_id: str) -> str | None:
    """Look up the stored Claude Code UUID for a prior session.

    Returns the claude_session_uuid if a prior AgentSession exists with this
    session_id and has a stored UUID. Returns None if no prior session exists
    or no UUID was stored (first message in session).

    Used by _create_options() to resume the correct Claude Code transcript
    instead of falling back to the most recent session file on disk.

    See issue #232 for the original cross-wire bug, and issue #374 Bug 1
    for the UUID mapping fix.
    """
    try:
        from models.agent_session import AgentSession

        sessions = [
            s
            for s in AgentSession.query.filter(session_id=session_id)
            if s.status in ("completed", "running", "active", "dormant")
        ]
        if not sessions:
            return None
        # Sort by created_at desc to get the newest record
        sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
        uuid = getattr(sessions[0], "claude_session_uuid", None)
        if uuid:
            logger.info(f"_get_prior_session_uuid({session_id!r}): found UUID {uuid}")
        return uuid
    except Exception:
        # If Redis is down or model unavailable, fail safe: don't continue
        logger.warning(
            f"_get_prior_session_uuid({session_id!r}) failed, defaulting to None",
            exc_info=True,
        )
        return None


def _has_prior_session(session_id: str) -> bool:
    """Check if a prior AgentSession exists for this session_id.

    Used by _create_options() to decide whether to set continue_conversation=True.
    Only returns True if an AgentSession with this session_id has been previously
    saved (i.e., a prior session ran for this conversation thread). This prevents
    fresh sessions from reusing stale Claude Code session files on disk.

    See issue #232 for the cross-wire bug this fixes.
    """
    return _get_prior_session_uuid(session_id) is not None


def _store_claude_session_uuid(session_id: str, claude_uuid: str) -> None:
    """Store the Claude Code session UUID on the AgentSession.

    Called after SDK query completes to persist the mapping between the
    Telegram session ID and the Claude Code transcript UUID. This enables
    continuation sessions to resume the correct transcript.

    See issue #374 Bug 1 for the session cross-wire bug this fixes.

    Args:
        session_id: The bridge/Telegram session ID.
        claude_uuid: The Claude Code session UUID from ResultMessage.session_id.
    """
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            # Sort by created_at desc, update the newest record
            sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
            session = sessions[0]
            session.claude_session_uuid = claude_uuid
            session.save()
            logger.info(f"Stored Claude Code UUID {claude_uuid} on session {session_id}")
        else:
            logger.warning(f"_store_claude_session_uuid: no session found for {session_id}")
    except Exception:
        logger.warning(
            f"_store_claude_session_uuid({session_id!r}) failed",
            exc_info=True,
        )


def _extract_sdlc_env_vars(session_id: str, gh_repo: str | None = None) -> dict[str, str]:
    """Extract SDLC context variables from an AgentSession for env injection.

    Reads the AgentSession from Redis and maps its fields to SDLC_* env vars.
    Only returns vars for fields that are non-None and non-empty, ensuring
    skills never see "None" as a value (issue #420).

    Args:
        session_id: The bridge/Telegram session ID.
        gh_repo: Optional GH_REPO already set on the agent.

    Returns:
        Dict of SDLC_* env var name -> value. Empty dict if session not found.
    """
    env: dict[str, str] = {}
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return env
        # Pick the newest active session
        active = [s for s in sessions if s.status in ("running", "active", "pending")]
        candidates = active if active else sessions
        candidates.sort(key=lambda s: s.created_at or 0, reverse=True)
        session = candidates[0]

        # PR URL -> SDLC_PR_NUMBER and SDLC_PR_BRANCH
        # Use isinstance(str) guards to prevent TypeError from non-string
        # ORM field values (e.g. Popoto proxy objects).
        pr_url = getattr(session, "pr_url", None)
        if isinstance(pr_url, str) and pr_url:
            pr_match = _PR_NUMBER_RE.search(pr_url)
            if pr_match:
                env["SDLC_PR_NUMBER"] = pr_match.group(1)

        # Branch name
        branch = getattr(session, "branch_name", None)
        if isinstance(branch, str) and branch:
            env["SDLC_PR_BRANCH"] = branch

        # Work item slug (new DevSessions use session.slug, legacy uses work_item_slug)
        slug = getattr(session, "slug", None) or getattr(session, "work_item_slug", None)
        if isinstance(slug, str) and slug:
            env["SDLC_SLUG"] = slug

        # Plan URL -> SDLC_PLAN_PATH (convert URL to local path)
        plan_url = getattr(session, "plan_url", None)
        if isinstance(plan_url, str) and plan_url:
            # plan_url is typically a GitHub URL or a local path
            # Extract the path portion (docs/plans/...)
            if "docs/plans/" in plan_url:
                plan_path = "docs/plans/" + plan_url.split("docs/plans/")[-1]
                env["SDLC_PLAN_PATH"] = plan_path
            else:
                env["SDLC_PLAN_PATH"] = plan_url

        # Issue URL -> SDLC_ISSUE_NUMBER and SDLC_TRACKING_ISSUE
        issue_url = getattr(session, "issue_url", None)
        if isinstance(issue_url, str) and issue_url:
            issue_match = _ISSUE_NUMBER_RE.search(issue_url)
            if issue_match:
                issue_num = issue_match.group(1)
                env["SDLC_ISSUE_NUMBER"] = issue_num
                env["SDLC_TRACKING_ISSUE"] = issue_num

        # Repo (complement GH_REPO, don't replace it)
        if gh_repo:
            env["SDLC_REPO"] = gh_repo

        # PM self-messaging: inject TELEGRAM_REPLY_TO from the session's
        # telegram_message_id so tools/send_telegram.py can reply to the
        # original human message (issue #497).
        tg_msg_id = getattr(session, "telegram_message_id", None)
        if tg_msg_id is not None:
            env["TELEGRAM_REPLY_TO"] = str(tg_msg_id)

        if env:
            logger.info(
                f"SDLC env vars for session {session_id}: "
                f"{', '.join(f'{k}={v}' for k, v in sorted(env.items()))}"
            )
    except Exception:
        logger.warning(
            f"_extract_sdlc_env_vars({session_id!r}) failed, skipping SDLC vars",
            exc_info=True,
        )
    return env


def get_active_client(session_id: str) -> ClaudeSDKClient | None:
    """Get the live SDK client for a running session, if any.

    IMPORTANT: Only call from within the same async context as the client
    (e.g., from a PostToolUse hook). Do NOT call from external async tasks
    like the Telethon event handler — use the steering Redis queue instead.
    """
    return _active_clients.get(session_id)


def get_all_active_sessions() -> dict[str, "ClaudeSDKClient"]:
    """Get a snapshot of all active sessions. For monitoring/diagnostics."""
    return dict(_active_clients)


# Root of the ai/ repository (used as cwd for SDLC-routed requests)
AI_REPO_ROOT = str(Path(__file__).parent.parent)

# Path to SOUL.md system prompt (legacy fallback)
SOUL_PATH = Path(__file__).parent.parent / "config" / "SOUL.md"

# Path to persona base file (stays in repo — not private)
PERSONAS_BASE_DIR = Path(__file__).parent.parent / "config" / "personas"

# Path to persona overlay files (private, iCloud-synced)
# Overlays live in ~/Desktop/Valor/personas/ — falls back to config/personas/ for dev
PERSONAS_OVERLAY_DIR = Path.home() / "Desktop" / "Valor" / "personas"

# Path to PRINCIPAL.md — supervisor's operating context for strategic decisions
PRINCIPAL_PATH = Path(__file__).parent.parent / "config" / "PRINCIPAL.md"

# Worker safety rails injected into every agent session.
# ChatSession is the sole pipeline controller —
# it steers the worker one stage at a time via coaching messages.
# This constant provides only the safety rails the worker needs; it does NOT
# contain pipeline orchestration or /sdlc invocation instructions.
WORKER_RULES = """\
## Worker Safety Rails

Execute the task given to you. The ChatSession controls pipeline progression — \
you do not need to manage stages or orchestrate the pipeline yourself.

### Hard rules:

NEVER commit code directly to main.
NEVER push code to main — all code pushes go to session/{slug} branches.

Plan/doc changes (.md, .json, .yaml) may be committed directly to main.
Code changes (.py, .js, .ts) NEVER go directly to main.\
"""


def _log_system_resources(context: str = "") -> dict:
    """Log current system resource usage for diagnostics.

    Returns dict with metrics for comparison.
    """
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()

        # Get process-specific info
        process = psutil.Process()
        proc_memory = process.memory_info()
        proc_cpu = process.cpu_percent(interval=0.1)

        # Check for other heavy processes
        heavy_processes = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                if proc.info["cpu_percent"] and proc.info["cpu_percent"] > 20:
                    heavy_processes.append(
                        f"{proc.info['name']}(pid={proc.info['pid']}, "
                        f"cpu={proc.info['cpu_percent']:.1f}%)"
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        metrics = {
            "system_cpu_percent": cpu_percent,
            "system_memory_percent": memory.percent,
            "system_memory_available_gb": memory.available / (1024**3),
            "process_memory_mb": proc_memory.rss / (1024**2),
            "process_cpu_percent": proc_cpu,
            "heavy_processes": heavy_processes[:5],  # Top 5
        }

        prefix = f"[{context}] " if context else ""
        logger.info(
            f"{prefix}System resources: "
            f"CPU={cpu_percent:.1f}%, "
            f"RAM={memory.percent:.1f}% used ({memory.available / (1024**3):.1f}GB free), "
            f"Process: {proc_memory.rss / (1024**2):.0f}MB RSS"
        )

        if heavy_processes:
            logger.info(f"{prefix}Heavy processes: {', '.join(heavy_processes)}")

        # Warn if resources are constrained
        if cpu_percent > 80:
            logger.warning(f"{prefix}High CPU load: {cpu_percent:.1f}%")
        if memory.percent > 85:
            logger.warning(f"{prefix}High memory usage: {memory.percent:.1f}%")
        if memory.available < 1 * (1024**3):  # Less than 1GB free
            logger.warning(f"{prefix}Low available memory: {memory.available / (1024**3):.2f}GB")

        return metrics

    except Exception as e:
        logger.debug(f"Could not get system resources: {e}")
        return {}


def load_completion_criteria() -> str:
    """Load completion criteria from CLAUDE.md."""
    claude_md = Path(__file__).parent.parent / "CLAUDE.md"
    if not claude_md.exists():
        return ""

    import re

    content = claude_md.read_text()
    match = re.search(r"## Work Completion Criteria\n\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
    return match.group(0) if match else ""


def load_principal_context(condensed: bool = True) -> str:
    """Load principal (supervisor) context from PRINCIPAL.md.

    Provides strategic context for decision-making: mission, goals, project
    priorities, and operating assumptions. Used by workers (condensed) and
    the ChatSession (full) to ground autonomous decisions.

    Args:
        condensed: If True, return only Mission + Goals + Projects sections
                   (~300 tokens). If False, return the full file content.

    Returns:
        Principal context string, or empty string if file is missing/empty.
    """
    if not PRINCIPAL_PATH.exists():
        logger.warning(f"PRINCIPAL.md not found at {PRINCIPAL_PATH}, skipping principal context")
        return ""

    content = PRINCIPAL_PATH.read_text().strip()
    if not content:
        logger.warning("PRINCIPAL.md is empty, skipping principal context")
        return ""

    if not condensed:
        return content

    # Extract condensed summary: Mission + Goals + Projects sections only.
    # This keeps the worker prompt lean while providing strategic context.
    import re

    sections_to_extract = ["Mission", r"Goals[^\n]*", r"Projects[^\n]*"]
    extracted = []
    for pattern in sections_to_extract:
        match = re.search(
            rf"^(## {pattern})\n\n(.*?)(?=\n---|\n## |\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if match:
            extracted.append(f"{match.group(1)}\n\n{match.group(2).strip()}")

    if not extracted:
        # Fallback: return first 500 chars if section extraction fails
        return content[:500]

    return "\n\n".join(extracted)


def _resolve_overlay_path(persona: str) -> Path:
    """Resolve persona overlay file path.

    Checks ~/Desktop/Valor/personas/{persona}.md first (private, iCloud-synced),
    then falls back to config/personas/{persona}.md (in-repo, for development).
    """
    overlay_path = PERSONAS_OVERLAY_DIR / f"{persona}.md"
    if overlay_path.exists():
        return overlay_path

    # Fallback: in-repo overlay (for development or when Desktop/Valor not available)
    return PERSONAS_BASE_DIR / f"{persona}.md"


def load_persona_prompt(persona: str = "developer") -> str:
    """Load persona prompt from base + overlay files.

    Base is read from config/personas/_base.md (in-repo, shared).
    Overlays are read from ~/Desktop/Valor/personas/{persona}.md (private, iCloud-synced).
    Falls back to config/SOUL.md if persona files are missing.

    Args:
        persona: Persona name — one of "developer", "project-manager", "teammate".
            Defaults to "developer".

    Returns:
        Combined persona prompt (base + overlay).

    Raises:
        FileNotFoundError: If _base.md is missing (base is required).
    """
    base_path = PERSONAS_BASE_DIR / "_base.md"

    # Base is required — fail loudly if missing
    if not base_path.exists():
        raise FileNotFoundError(
            f"Persona base file not found at {base_path}. "
            "The _base.md file is required for the persona system."
        )

    base_content = base_path.read_text()

    # Resolve overlay: ~/Desktop/Valor/personas/ first, then config/personas/
    overlay_path = _resolve_overlay_path(persona)

    # Overlay is optional — fall back to SOUL.md if missing
    if overlay_path.exists():
        overlay_content = overlay_path.read_text()
        logger.info(f"Loaded persona '{persona}' from {overlay_path}")
        return f"{base_content}\n\n---\n\n{overlay_content}"

    # Invalid persona name — fall back to developer with warning
    if persona not in ("developer", "project-manager", "teammate"):
        logger.warning(f"Unknown persona '{persona}', falling back to developer persona")
        developer_path = _resolve_overlay_path("developer")
        if developer_path.exists():
            return f"{base_content}\n\n---\n\n{developer_path.read_text()}"

    # Persona overlay missing — fall back to SOUL.md
    logger.warning(
        f"Persona overlay '{persona}' not found at {overlay_path}, falling back to SOUL.md"
    )
    if SOUL_PATH.exists():
        return SOUL_PATH.read_text()

    logger.warning(f"SOUL.md not found at {SOUL_PATH}, using default prompt")
    return "You are Valor, an AI coworker. Be direct, concise, and helpful."


def load_system_prompt() -> str:
    """Load developer system prompt with worker rules and completion criteria.

    Wraps load_persona_prompt("developer") with WORKER_RULES and additional context.
    This is the default prompt for AgentSDK coding subprocesses.

    System prompt structure:
        [WORKER_RULES — safety rails for the worker, FIRST — takes precedence]
        ---
        [Persona prompt — base + developer overlay]
        ---
        [Principal Context — condensed mission/goals/priorities from PRINCIPAL.md]
        ---
        [Work Completion Criteria — from CLAUDE.md]

    ChatSession handles pipeline orchestration via nudge loop.
    The worker only receives safety rails — no pipeline stages or /sdlc references.
    """
    try:
        persona_prompt = load_persona_prompt("developer")
    except FileNotFoundError:
        # Fallback to legacy SOUL.md if persona system not set up
        logger.warning("Persona system not available, falling back to SOUL.md")
        if SOUL_PATH.exists():
            persona_prompt = SOUL_PATH.read_text()
        else:
            persona_prompt = "You are Valor, an AI coworker. Be direct, concise, and helpful."

    # Append completion criteria
    criteria = load_completion_criteria()
    criteria_section = f"\n\n---\n\n{criteria}" if criteria else ""

    # Load condensed principal context (mission + goals + project priorities)
    principal = load_principal_context(condensed=True)
    principal_section = f"\n\n---\n\n## Principal Context\n\n{principal}" if principal else ""

    # Worker rules FIRST — safety rails take precedence over persona
    return f"{WORKER_RULES}\n\n---\n\n{persona_prompt}{principal_section}{criteria_section}"


def load_pm_system_prompt(working_directory: str) -> str:
    """Load system prompt for PM (Project Manager) mode channels.

    Uses the project-manager persona (base + PM overlay). PM mode skips
    WORKER_RULES (no branch safety rails) and loads the project-specific
    CLAUDE.md from the work vault directory if it exists.

    System prompt structure:
        [Persona prompt — base + project-manager overlay]
        ---
        [Work-vault CLAUDE.md — PM-specific instructions for this project]

    Args:
        working_directory: Path to the work-vault project folder.

    Returns:
        Combined system prompt for PM mode.
    """
    try:
        persona_prompt = load_persona_prompt("project-manager")
    except FileNotFoundError:
        # Fallback to legacy SOUL.md if persona system not set up
        logger.warning("Persona system not available for PM, falling back to SOUL.md")
        if SOUL_PATH.exists():
            persona_prompt = SOUL_PATH.read_text()
        else:
            persona_prompt = "You are Valor, an AI coworker. Be direct, concise, and helpful."

    # Try to load project-specific CLAUDE.md from work-vault directory
    project_claude_path = Path(working_directory) / "CLAUDE.md"
    if project_claude_path.exists():
        project_instructions = project_claude_path.read_text()
        logger.info(f"Loaded PM instructions from {project_claude_path}")
        return f"{persona_prompt}\n\n---\n\n{project_instructions}"

    logger.info(f"No CLAUDE.md found at {project_claude_path}, using persona only for PM mode")
    return persona_prompt


def _is_code_file(file_path: str) -> bool:
    """Return True if the file path has a code extension (.py, .js, .ts).

    Inlined here to avoid a cross-layer import from .claude/hooks/post_tool_use.py.
    Keep in sync with ``post_tool_use.is_code_file`` — canonical version is there.
    """
    if not file_path:
        return False
    return Path(file_path).suffix.lower() in {".py", ".js", ".ts"}


def _check_no_direct_main_push(session_id: str, repo_root: Path | None = None) -> str | None:
    """Check whether a session pushed code directly to main.

    Reads the session's sdlc_state.json. If code was modified and the current
    git branch is 'main', checks ``modified_on_branch`` to distinguish:

    - Code written on a ``session/*`` branch and now on main via PR merge
      -> **allowed** (no violation).
    - Code written directly on main (or legacy state without the field)
      -> **hard-block** violation.

    Escape hatches:
    - ``SKIP_SDLC=1`` environment variable bypasses this check entirely,
      matching the project-level hook escape hatch. Use for recovery from
      false-positive infinite loops (see issue #261).

    Before reporting a violation, this function cross-checks the live git
    working tree to verify that code files actually have uncommitted changes.
    If the state file is stale (no actual changes on main), the check passes.

    Args:
        session_id: The session ID to check.
        repo_root: Path to the git repo root. Defaults to the ai/ repo root.

    Returns:
        None   -- session is clear (non-code, on a feature branch, or docs-only)
        str    -- error message describing the violation (hard-block)
    """
    # Escape hatch: SKIP_SDLC=1 bypasses the main branch check (issue #261)
    if os.environ.get("SKIP_SDLC") == "1":
        logger.warning(
            f"[sdlc-main-check] SKIP_SDLC=1 — bypassing main branch check for {session_id}"
        )
        return None

    if repo_root is None:
        repo_root = Path(__file__).parent.parent

    sessions_dir = repo_root / "data" / "sessions"
    state_path = sessions_dir / session_id / "sdlc_state.json"

    # Non-code session: no state file → no enforcement needed
    if not state_path.exists():
        return None

    try:
        import json

        with open(state_path) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt/unreadable state — fail open, do not block the session
        logger.warning(
            f"[sdlc-main-check] Could not read sdlc_state.json for {session_id}: "
            "fail open, skipping branch check"
        )
        return None

    # No code modified → docs/ops session, no enforcement
    if not state.get("code_modified", False):
        return None

    # Code was modified: check if we're on main
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5,
        )
        current_branch = result.stdout.strip()
    except Exception as e:
        logger.warning(
            f"[sdlc-main-check] Could not determine git branch for {session_id}: {e} "
            "— fail open, skipping branch check"
        )
        return None

    if current_branch != "main":
        # On a feature branch (inside /do-build worktree) — all good
        return None

    # Code modified + on main: check where the code was *originally* written.
    # If it was written on a session/* branch, it arrived here via PR merge —
    # not a direct push. Only block if modified_on_branch is "main" or absent
    # (legacy state without the field → preserve backward-compat behavior).
    modified_on_branch = state.get("modified_on_branch", "")
    if modified_on_branch.startswith("session/"):
        logger.info(
            f"[sdlc-main-check] Code for {session_id} was modified on "
            f"'{modified_on_branch}' and is now on main — arrived via merge, "
            "no violation."
        )
        return None

    # Before reporting a violation, cross-check against live git state.
    # If no code files actually have uncommitted changes on main, the
    # sdlc_state.json is stale (code was committed and moved to a branch
    # but the state was never updated). See issue #261.
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5,
        )
        staged_result = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=5,
        )
        all_changed = set(
            diff_result.stdout.strip().split("\n") + staged_result.stdout.strip().split("\n")
        )
        all_changed.discard("")

        if not any(_is_code_file(f) for f in all_changed):
            logger.info(
                f"[sdlc-main-check] State says code modified on main for {session_id} "
                "but no actual uncommitted code changes found — stale state, no violation."
            )
            return None
    except Exception as e:
        # If the git diff check fails, fall through to the violation path.
        # This preserves the existing conservative behavior.
        logger.warning(
            f"[sdlc-main-check] Live git diff check failed for {session_id}: {e} "
            "— proceeding with violation check"
        )

    # Code modified on main (or legacy state) = SDLC violation
    modified_files = state.get("files", [])
    files_list = "\n".join(f"  - {f}" for f in modified_files) if modified_files else "  (unknown)"
    return (
        "SDLC VIOLATION: Code was modified directly on the main branch.\n\n"
        f"Modified files:\n{files_list}\n\n"
        "The mandatory pipeline requires all code changes to go through a feature branch:\n"
        "  1. Create a GitHub issue for the change\n"
        "  2. Run /do-plan {slug} to create a plan\n"
        "  3. Run /do-build to implement on a session/{slug} branch\n"
        "  4. A PR is opened and merged to main — never pushed directly\n\n"
        "To remediate:\n"
        "  git checkout -b session/your-fix-slug\n"
        "  git push -u origin session/your-fix-slug\n"
        "  gh pr create\n\n"
        "Do NOT push these changes to main."
    )


class ValorAgent:
    """
    Valor's Claude Agent SDK wrapper.

    Provides a simplified interface for sending messages and receiving responses
    using the Claude Agent SDK with Valor's configuration.

    Permission mode is set to "bypassPermissions" (YOLO mode) - Valor has full
    system access with no approval gates.
    """

    def __init__(
        self,
        working_dir: str | Path | None = None,
        system_prompt: str | None = None,
        permission_mode: str = "bypassPermissions",
        task_list_id: str | None = None,
        chat_id: str | None = None,
        project_key: str | None = None,
        message_id: int | None = None,
        agent_session_id: str | None = None,
        gh_repo: str | None = None,
        target_repo: str | None = None,
        session_type: str | None = None,
    ):
        """
        Initialize ValorAgent.

        Args:
            working_dir: Working directory for the agent. Defaults to ai/ repo root.
            system_prompt: Custom system prompt. Defaults to SOUL.md contents.
            permission_mode: Permission mode for tool use. Default: "bypassPermissions".
            task_list_id: Optional task list ID to scope sub-agent Task storage
                via CLAUDE_CODE_TASK_LIST_ID environment variable.
            chat_id: Optional chat ID for routing context injection.
            project_key: Optional project key for routing context injection.
            message_id: Optional message ID for routing context injection.
            agent_session_id: Optional session ID injected as
                AGENT_SESSION_ID env var for child session spawning.
            gh_repo: Optional GitHub repo (org/repo) to set as GH_REPO env var.
                When set, all `gh` CLI commands in the subprocess automatically
                target this repo without needing explicit --repo flags.
            target_repo: Absolute path to the target project's repo root. For
                cross-repo SDLC builds this differs from working_dir (the
                orchestrator). Defaults to working_dir when not specified.
            session_type: Session type ("chat" for PM, None for dev). Injected as
                SESSION_TYPE env var so hooks can enforce write restrictions.
        """
        default_dir = Path(__file__).parent.parent
        allowed_root = Path.home() / "src"
        raw_path = Path(working_dir) if working_dir else default_dir
        is_wt = WORKTREES_DIR in str(raw_path)
        self.working_dir = validate_workspace(raw_path, allowed_root, is_worktree=is_wt)
        self.system_prompt = system_prompt or load_system_prompt()
        self.permission_mode = permission_mode
        self.task_list_id = task_list_id
        self.chat_id = chat_id
        self.project_key = project_key
        self.message_id = message_id
        self.agent_session_id = agent_session_id
        self.gh_repo = gh_repo or None  # Normalize empty string to None
        self.target_repo = target_repo
        self.session_type = session_type

    def _create_options(self, session_id: str | None = None) -> ClaudeAgentOptions:
        """Create ClaudeAgentOptions configured for Valor with full permissions.

        Auth: We intentionally omit ANTHROPIC_API_KEY from env so the CLI
        subprocess falls back to OAuth/subscription auth (Max plan). If the
        key is present in the process environment, we strip it to prevent
        the SDK from using API billing. Set USE_API_BILLING=true in .env
        to force API key auth as a fallback.
        """
        env: dict[str, str] = {}

        if os.getenv("USE_API_BILLING", "").lower() == "true":
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
                logger.info("Auth: using API key billing (USE_API_BILLING=true)")
            else:
                logger.warning("Auth: USE_API_BILLING=true but no ANTHROPIC_API_KEY set")
        else:
            # Strip API key so CLI falls back to subscription/OAuth
            env["ANTHROPIC_API_KEY"] = ""
            logger.info("Auth: using Max subscription (OAuth fallback)")

        # Task list isolation: scope sub-agent tasks by session/work-item
        if self.task_list_id:
            env["CLAUDE_CODE_TASK_LIST_ID"] = self.task_list_id

        # Pass bridge session_id so hooks can resolve the AgentSession
        # without relying on Claude Code's internal UUID matching.
        if session_id:
            env["VALOR_SESSION_ID"] = session_id

        # Pass agent_session_id so the agent can reference its own session when spawning children
        # via `schedule_session --parent-session $AGENT_SESSION_ID` (issue #359)
        if self.agent_session_id:
            env["AGENT_SESSION_ID"] = self.agent_session_id

        # Cross-repo gh resolution: set GH_REPO so all `gh` CLI commands in the
        # subprocess automatically target the correct repo (issue #375). This is
        # the deterministic fix -- SKILL.md --repo instructions remain as a safety net.
        if self.gh_repo:
            env["GH_REPO"] = self.gh_repo
        if self.target_repo:
            env["SDLC_TARGET_REPO"] = str(self.target_repo)
        if self.session_type:
            env["SESSION_TYPE"] = self.session_type

        # PM sessions: inject Telegram context so ChatSession can send its
        # own messages via tools/send_telegram.py (issue #497).
        # chat_id comes from the project config; reply_to is resolved from
        # the AgentSession's telegram_message_id in _extract_sdlc_env_vars below.
        if self.session_type == SessionType.CHAT and self.chat_id:
            env["TELEGRAM_CHAT_ID"] = str(self.chat_id)

        # PM sessions: inject Sentry auth token so sentry-cli works without
        # manual export. Token is stored in ~/Desktop/Valor/.env (iCloud-synced).
        if self.session_type == SessionType.CHAT:
            sentry_env = Path.home() / "Desktop" / "Valor" / ".env"
            if sentry_env.exists():
                for line in sentry_env.read_text().splitlines():
                    if line.startswith("SENTRY_PERSONAL_TOKEN="):
                        env["SENTRY_AUTH_TOKEN"] = line.split("=", 1)[1]
                        break

        # SDLC context injection: pre-resolve session fields as env vars so
        # skills can reference $SDLC_PR_NUMBER etc. instead of guessing (issue #420).
        # Only set vars when the field is non-None and non-empty.
        if session_id:
            sdlc_env = _extract_sdlc_env_vars(session_id, self.gh_repo)
            env.update(sdlc_env)

        system_prompt = self.system_prompt

        # Only continue a conversation if we have evidence of a prior session.
        # Without this check, fresh sessions set continue_conversation=True which
        # can cause Claude Code to reuse the most recent session file on disk,
        # leaking context between unrelated conversations (see issue #232).
        #
        # Bug 1 fix (issue #374): Use the stored Claude Code UUID for the resume
        # parameter instead of the Telegram session ID. The Telegram ID doesn't
        # match any .jsonl transcript file, causing Claude Code to fall back to
        # the most recent session on disk (wrong session).
        prior_uuid = _get_prior_session_uuid(session_id) if session_id else None
        should_continue = prior_uuid is not None

        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=str(self.working_dir),
            permission_mode=self.permission_mode,  # type: ignore[arg-type]
            continue_conversation=should_continue,
            resume=prior_uuid if should_continue else None,
            setting_sources=["user", "local", "project"],
            env=env,
            hooks=build_hooks_config(),
            agents=get_agent_definitions(),
        )

    async def query(self, message: str, session_id: str | None = None, max_retries: int = 2) -> str:
        """
        Send a message and get a response. On error, feeds the error back
        to the agent so it can attempt a different approach.

        For file-related errors (invalid PDF, corrupted files), instructs the
        agent to avoid reading the problematic file and work with text context only.

        Args:
            message: The user message to send
            session_id: Optional session ID for conversation continuity
            max_retries: Max times to retry by feeding error back to agent

        Returns:
            The assistant's text response
        """
        options = self._create_options(session_id)
        response_parts: list[str] = []
        retries = 0

        # Circuit breaker check: fail fast if Anthropic is down
        circuit = _get_anthropic_circuit()
        if not circuit.allows_request():
            logger.warning(
                "[SDK-circuit] Anthropic circuit is OPEN — failing fast for session %s",
                session_id,
            )
            raise CircuitOpenError(
                "Anthropic service unavailable (circuit breaker open). "
                "Session will remain pending and retry when service recovers."
            )

        # Bug 2 fix (issue #374): Reset watchdog tool counts at query start
        # so continuation sessions don't inherit inflated counts from prior runs.
        if session_id:
            from agent.health_check import reset_session_count

            reset_session_count(session_id)

        # Issue #597: Pre-register bridge session ID in the hook-side registry
        # so hooks (which run in this parent process) can resolve the correct
        # session ID instead of relying on os.environ (which is subprocess-only).
        claude_uuid_for_cleanup: str | None = None
        if session_id:
            from agent.hooks.session_registry import register_pending

            register_pending(session_id)

        # Log resources before SDK initialization
        init_start = time.time()
        logger.info(f"[SDK-init] Starting SDK initialization for session {session_id}")
        _log_system_resources("SDK-init-pre")

        try:
            # Safety ceiling timeout: prevents query from blocking a worker
            # forever if the SDK subprocess hangs. Set high (1 hour) because
            # the watchdog's activity-based stall detection handles real stalls.
            # This is only a backstop for truly hung processes.
            query_timeout = int(os.environ.get("SDK_QUERY_TIMEOUT_SECONDS", 3600))

            async with asyncio.timeout(query_timeout):
                async with ClaudeSDKClient(options) as client:
                    # Log successful initialization
                    init_elapsed = time.time() - init_start
                    logger.info(f"[SDK-init] SDK initialized successfully in {init_elapsed:.2f}s")
                    _log_system_resources("SDK-init-post")
                    # Register client for steering access
                    if session_id:
                        _active_clients[session_id] = client
                        logger.debug(f"Registered active client for session {session_id}")

                    # Record initial activity when query starts
                    if session_id:
                        record_session_activity(session_id)

                    await client.query(message)

                    while True:
                        async for msg in client.receive_response():
                            if isinstance(msg, AssistantMessage):
                                for block in msg.content:
                                    if isinstance(block, TextBlock):
                                        response_parts.append(block.text)
                                        # Record activity on each text output
                                        if session_id:
                                            record_session_activity(session_id)
                            elif isinstance(msg, ResultMessage):
                                # Record activity on result messages
                                if session_id:
                                    record_session_activity(session_id)
                                # Bug 1 fix (issue #374): Store Claude Code session UUID
                                # so continuation sessions resume the correct transcript.
                                if msg.session_id and session_id:
                                    _store_claude_session_uuid(session_id, msg.session_id)
                                    # Issue #597: Track UUID for registry cleanup in finally
                                    claude_uuid_for_cleanup = msg.session_id
                                # Capture stop_reason for nudge loop routing decisions
                                if msg.stop_reason and session_id:
                                    _session_stop_reasons[session_id] = msg.stop_reason
                                    logger.info(
                                        "SDK stop_reason=%s for session %s",
                                        msg.stop_reason,
                                        session_id,
                                    )

                                if msg.total_cost_usd is not None:
                                    cost = msg.total_cost_usd
                                    turns = msg.num_turns
                                    duration = msg.duration_ms
                                    # Always log at debug; warn if equivalent
                                    # cost exceeds threshold (sanity check even
                                    # on subscription — tracks what we'd pay on API)
                                    summary = (
                                        f"Query completed: {turns} turns, "
                                        f"${cost:.4f} equivalent, "
                                        f"{duration}ms"
                                    )
                                    logger.info(summary)
                                if msg.is_error and retries < max_retries:
                                    retries += 1
                                    error_text = msg.result or "(empty)"
                                    recovery_msg = _build_error_recovery_message(error_text)
                                    logger.warning(
                                        f"Agent error (attempt {retries}/{max_retries}), "
                                        f"feeding error back: {error_text}"
                                    )
                                    response_parts.clear()
                                    await client.query(recovery_msg)
                                    break  # Re-enter receive_response() loop
                                elif msg.is_error:
                                    result_text = msg.result or ""
                                    if _is_auth_error(result_text):
                                        logger.error(
                                            f"Auth failure after {retries} retries: {result_text}\n"
                                            "Subscription fallback may be patched. "
                                            "Set USE_API_BILLING=true or see module docstring."
                                        )
                                    else:
                                        logger.error(
                                            f"Agent error after {retries} retries: {result_text}"
                                        )
                        else:
                            # async for completed without break — done
                            break

        except TimeoutError:
            elapsed = time.time() - init_start
            logger.error(
                "[SDK-timeout] Query timed out after %.0fs for session %s "
                "(limit=%ds). Subprocess may be hung.",
                elapsed,
                session_id,
                query_timeout,
            )
            asyncio.ensure_future(circuit.record_failure(TimeoutError("query timeout")))
            raise

        except asyncio.CancelledError:
            elapsed = time.time() - init_start
            logger.warning(
                "[SDK-cancelled] Query cancelled after %.0fs for session %s",
                elapsed,
                session_id,
            )
            # CancelledError is not an API failure — don't record against circuit
            raise

        except Exception as e:
            # Record failure for circuit breaker
            asyncio.ensure_future(circuit.record_failure(e))

            error_str = str(e)
            init_elapsed = time.time() - init_start

            # Check if this is an initialization timeout
            is_init_timeout = "Control request timeout: initialize" in error_str

            if is_init_timeout:
                logger.error(
                    f"[SDK-init] INITIALIZATION TIMEOUT after {init_elapsed:.2f}s\n"
                    f"  Session: {session_id}\n"
                    f"  Working dir: {self.working_dir}\n"
                    f"  Error: {error_str}"
                )
                # Log current system state to help diagnose
                logger.error("[SDK-init] System state at timeout:")
                _log_system_resources("SDK-init-timeout")

                # Check if Claude CLI process exists
                try:
                    claude_procs = []
                    proc_attrs = ["pid", "name", "cmdline", "status", "create_time"]
                    for proc in psutil.process_iter(proc_attrs):
                        try:
                            if proc.info["name"] and "claude" in proc.info["name"].lower():
                                age = time.time() - proc.info["create_time"]
                                claude_procs.append(
                                    f"PID={proc.info['pid']} name={proc.info['name']} "
                                    f"status={proc.info['status']} age={age:.1f}s"
                                )
                            elif proc.info["cmdline"]:
                                cmdline = " ".join(proc.info["cmdline"] or [])
                                if "claude" in cmdline.lower():
                                    age = time.time() - proc.info["create_time"]
                                    claude_procs.append(
                                        f"PID={proc.info['pid']} cmd={cmdline[:80]} "
                                        f"status={proc.info['status']} age={age:.1f}s"
                                    )
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass

                    if claude_procs:
                        procs_str = "\n  ".join(claude_procs)
                        logger.error(f"[SDK-init] Found Claude processes:\n  {procs_str}")
                    else:
                        logger.error(
                            "[SDK-init] No Claude processes found - CLI may have failed to start"
                        )
                except Exception as proc_err:
                    logger.debug(f"Could not check for Claude processes: {proc_err}")

            elif _is_auth_error(error_str):
                logger.error(
                    f"SDK auth failure — subscription fallback may be patched: {e}\n"
                    "FALLBACK OPTIONS:\n"
                    "  1. Set USE_API_BILLING=true in .env to use API key billing\n"
                    "  2. CLIProxyAPI (github.com/luispater/CLIProxyAPI): OAuth proxy\n"
                    "  3. Pi Coding Agent: native subscription auth via --mode rpc"
                )
            else:
                logger.error(f"SDK query failed after {init_elapsed:.2f}s: {e}")
            raise

        else:
            # Query succeeded — record success for circuit breaker
            asyncio.ensure_future(circuit.record_success())

        finally:
            # Always unregister client from registry
            if session_id:
                _active_clients.pop(session_id, None)
                # Clean up activity tracking — session is done
                clear_session_activity(session_id)
                # Note: _session_stop_reasons is NOT cleaned here — it's consumed
                # by get_stop_reason() in session_queue after query returns. The pop()
                # in get_stop_reason() handles cleanup. If the nudge loop never runs
                # (crash), entries are tiny (session_id -> str) and cleared on restart.
                logger.debug(f"Unregistered active client for session {session_id}")

            # Issue #597: Clean up session registry entry
            if claude_uuid_for_cleanup:
                from agent.hooks.session_registry import cleanup_stale, unregister

                unregister(claude_uuid_for_cleanup)
                cleanup_stale()  # Safety net for leaked entries

        return "\n".join(response_parts) if response_parts else ""


# Patterns that indicate subscription/auth failures — if these appear,
# the subscription fallback may have been patched by Anthropic.
_AUTH_ERROR_PATTERNS = [
    "credit balance is too low",
    "authentication_failed",
    "invalid api key",
    "unauthorized",
    "billing",
    "quota exceeded",
    "rate_limit",
]


def _is_auth_error(error_text: str) -> bool:
    """Check if an error indicates subscription auth was rejected."""
    error_lower = error_text.lower()
    return any(pattern in error_lower for pattern in _AUTH_ERROR_PATTERNS)


# Patterns that indicate file/media-related API errors
_FILE_ERROR_PATTERNS = [
    "pdf",
    "image",
    "base64",
    "file",
    "media_type",
    "not valid",
    "could not process",
    "invalid_request_error",
]


def _is_file_related_error(error_text: str) -> bool:
    """Check if an error is related to file/media processing."""
    error_lower = error_text.lower()
    return any(pattern in error_lower for pattern in _FILE_ERROR_PATTERNS)


def _build_error_recovery_message(error_text: str) -> str:
    """
    Build an appropriate recovery message based on the error type.

    For file-related errors, instructs the agent to avoid reading problematic files.
    For other errors, uses the generic retry approach.
    """
    if _is_file_related_error(error_text):
        return (
            f"That failed with a file-related error:\n{error_text}\n\n"
            f"IMPORTANT: Do NOT attempt to read any PDF, image, or binary files from "
            f"the data/media/ directory. These files may be corrupted or invalid. "
            f"Work only with the text context provided in the conversation. "
            f"If you need file contents, they have already been extracted as text "
            f"in the message above. Please respond to the user's request using "
            f"only the text context available."
        )
    return (
        f"That failed with this error:\n{error_text}\n\n"
        f"Please try a different approach to accomplish the original task."
    )


def _resolve_persona(
    project: dict | None,
    chat_title: str | None,
    is_dm: bool = False,
) -> str:
    """Resolve the persona name from project config, chat title, and DM status.

    Resolution order:
    1. DMs: use project's dm_persona config (default: "teammate")
    2. Group chats: look up persona from project's telegram.groups[chat_title]
    3. PM mode projects: "project-manager"
    4. Default: "developer"

    Args:
        project: Project configuration dict from projects.json.
        chat_title: Telegram chat/group title, or None for DMs.
        is_dm: Whether this is a direct message.

    Returns:
        Persona name string (e.g., "developer", "project-manager", "teammate").
    """
    if not project:
        return PersonaType.TEAMMATE if is_dm else PersonaType.DEVELOPER

    telegram_config = project.get("telegram", {})

    # DMs use the dm_persona config
    if is_dm:
        return telegram_config.get("dm_persona", PersonaType.TEAMMATE)

    # PM mode projects always use project-manager persona
    project_mode = project.get("mode", "dev")
    if project_mode == "pm":
        return PersonaType.PROJECT_MANAGER

    # Group chats: look up persona from the groups dict
    if chat_title:
        groups = telegram_config.get("groups", {})
        if isinstance(groups, dict):
            for group_name, group_config in groups.items():
                if group_name.lower() in chat_title.lower():
                    if isinstance(group_config, dict):
                        persona = group_config.get("persona")
                        if persona:
                            return persona

    return PersonaType.DEVELOPER


async def get_agent_response_sdk(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
    sender_id: int | None = None,
    task_list_id: str | None = None,
    correlation_id: str | None = None,
    agent_session_id: str | None = None,
) -> str:
    """Get agent response using Claude Agent SDK.

    Orchestrates a complete agent session from message receipt to response.
    Uses config-driven persona resolution (resolve_persona from
    bridge.routing) to determine session behavior for ChatSessions:

    - Teammate persona: bypasses the Haiku intent classifier, sets
      session_mode=PersonaType.TEAMMATE directly on the session, reducing
      latency and API cost for DMs and groups with "teammate" persona.
    - Project Manager/Developer persona: bypasses the classifier, uses
      the config-determined persona without reclassification.
    - None (unconfigured): falls through to the existing Haiku intent
      classifier for Teammate vs work routing.

    Args:
        message: The message to process
        session_id: Session ID for conversation continuity
        sender_name: Name of the sender (for logging)
        chat_title: Chat title (for logging and mode resolution)
        project: Project configuration dict (contains telegram.groups with
            optional persona fields for config-driven mode)
        chat_id: Chat ID (unused, for compatibility)
        sender_id: Telegram user ID (for permission checking)
        task_list_id: Optional task list ID to scope sub-agent Task storage
        correlation_id: Optional end-to-end tracing ID from the bridge
        agent_session_id: Optional session ID for child session spawning (issue #359)

    Returns:
        The assistant's response text
    """
    import time
    import uuid

    start_time = time.time()
    # Use correlation_id as primary log prefix; fall back to generated ID
    if not correlation_id:
        correlation_id = uuid.uuid4().hex[:12]
    request_id = correlation_id

    # Determine working directory based on work request classification
    project_name = project.get("name", "Valor") if project else "Valor"
    project_working_dir = project.get("working_directory") if project else None
    if not project_working_dir:
        project_working_dir = AI_REPO_ROOT

    # Check project mode: "pm" channels bypass SDLC classification entirely
    project_mode = project.get("mode", "dev") if project else "dev"
    # Treat any unrecognized mode as "dev" (safe default)
    if project_mode not in ("dev", "pm"):
        logger.warning(f"[{request_id}] Unknown project mode '{project_mode}', treating as 'dev'")
        project_mode = "dev"

    if project_mode == "pm":
        # PM mode: skip classification, always use "question", work in project dir
        classification = ClassificationType.QUESTION
        working_dir = project_working_dir
        logger.info(f"[{request_id}] PM mode: cwd={working_dir}, skipping SDLC classification")
    else:
        # Dev mode: use classification from bridge (no re-classification).
        # The bridge handler already classified via routing.py and stored
        # classification_type on the AgentSession. Read it from session if
        # available, otherwise fall back to a simple heuristic.
        classification = None
        if session_id:
            try:
                from models.agent_session import AgentSession

                sessions = list(AgentSession.query.filter(session_id=session_id))
                active = [s for s in sessions if s.status in ("running", "active", "pending")]
                candidates = active if active else sessions
                if candidates:
                    candidates.sort(key=lambda s: s.created_at or 0, reverse=True)
                    classification = candidates[0].classification_type
            except Exception as e:
                logger.debug(f"[{request_id}] Could not read classification from session: {e}")

        if not classification:
            # Fallback: check for PR/issue references before defaulting to question.
            # The async classifier can lose the race with session pickup, so this
            # fast-path catches messages like "Complete PR 478" that must be SDLC.
            import re as _re_cls

            if _re_cls.search(
                r"(?:issue|pr|pull request)\s+#?\d+", message.lower()
            ) or _re_cls.match(r"^#\d+$", message.strip().lower()):
                classification = ClassificationType.SDLC
                logger.info(
                    f"[{request_id}] Fast-path SDLC classification (PR/issue reference in message)"
                )
            else:
                classification = ClassificationType.QUESTION

        if classification == ClassificationType.SDLC and project_working_dir != AI_REPO_ROOT:
            working_dir = AI_REPO_ROOT
            logger.info(
                f"[{request_id}] SDLC routed: orchestrator in ai/, target={project_working_dir}"
            )
        else:
            working_dir = project_working_dir
            logger.info(
                f"[{request_id}] Direct routed: cwd={working_dir} (classification={classification})"
            )

    logger.info(f"[{request_id}] SDK query for {project_name}")
    logger.debug(f"[{request_id}] Working directory: {working_dir}")

    # Build context-enriched message (includes user permission restrictions)
    from bridge.context import build_context_prefix

    context = build_context_prefix(project, chat_title is None, sender_id)
    enriched_message = context
    enriched_message += f"\n\nFROM: {sender_name}"
    if chat_title:
        enriched_message += f" in {chat_title}"
    enriched_message += f"\nSESSION_ID: {session_id}"
    if task_list_id:
        enriched_message += f"\nTASK_SCOPE: {task_list_id}"
    enriched_message += (
        "\nSCOPE: This session is scoped to the message below from this sender. "
        "When reporting completion or summarizing work, only reference tasks and "
        "work initiated in this specific session. Do not include work, PRs, or "
        "requests from other sessions, other senders, or prior conversation threads."
    )
    # For SDLC-routed requests, inject target repo context (never for PM mode).
    # ChatSession (session_type="chat") gets full pipeline instructions.
    # All sessions are ChatSessions — orchestrate via dev-session subagent
    _session_type = None
    if session_id:
        try:
            from models.agent_session import AgentSession as _AgentSession

            _sessions = list(_AgentSession.query.filter(session_id=session_id))
            if _sessions:
                _session_type = getattr(_sessions[0], "session_type", None)
        except Exception:
            pass

    # Cross-repo SDLC: inject target repo context
    if (
        project_mode != "pm"
        and classification == ClassificationType.SDLC
        and project_working_dir != AI_REPO_ROOT
    ):
        github_config = project.get("github", {}) if project else {}
        github_org = github_config.get("org", "")
        github_repo = github_config.get("repo", "")
        enriched_message += (
            f"\nWORK REQUEST for project {project_name}.\nTARGET REPO: {project_working_dir}"
        )
        if github_org and github_repo:
            enriched_message += f"\nGITHUB: {github_org}/{github_repo}"

    # ChatSession routing: classify intent and choose Teammate or PM dispatch path.
    # Teammate mode answers informational queries directly without spawning DevSession.
    _teammate_mode = False
    if _session_type == SessionType.CHAT:
        # Config-driven persona bypass: skip classifier when persona is already known
        from bridge.routing import resolve_persona as _resolve_persona_mode

        _config_persona = _resolve_persona_mode(project, chat_title, is_dm=(chat_title is None))

        if _config_persona == PersonaType.TEAMMATE:
            # DMs and Teammate-persona groups: skip classifier, go straight to Teammate
            _teammate_mode = True
            logger.info(
                f"[{request_id}] Config-driven Teammate mode "
                f"(persona={_config_persona!r}, is_dm={chat_title is None})"
            )
            # Record synthetic classification metric for observability
            try:
                from agent.teammate_metrics import record_classification

                record_classification("teammate", 1.0)
                logger.debug(
                    f"[{request_id}] Recorded synthetic teammate classification (config-determined)"
                )
            except Exception:
                pass  # Best-effort metrics
            # Update session mode flag
            if session_id:
                try:
                    from models.agent_session import AgentSession as _TMSession

                    for _s in _TMSession.query.filter(session_id=session_id):
                        if _s.status in ("running", "active", "pending"):
                            _s.session_mode = PersonaType.TEAMMATE
                            _s.save()
                            break
                except Exception:
                    pass  # Best-effort
        elif _config_persona in (PersonaType.PROJECT_MANAGER, PersonaType.DEVELOPER):
            # PM/Dev persona groups: skip classifier, use PM dispatch (not Teammate)
            logger.info(f"[{request_id}] Config-driven {_config_persona} mode, skipping classifier")
        else:
            # Unconfigured: fall through to intent classifier
            try:
                from agent.intent_classifier import classify_intent
                from agent.teammate_metrics import record_classification

                _intent_result = await classify_intent(message)
                record_classification(_intent_result.intent, _intent_result.confidence)
                logger.info(
                    f"[{request_id}] Intent: {_intent_result.intent} "
                    f"(conf={_intent_result.confidence:.2f}): {_intent_result.reasoning}"
                )

                if _intent_result.is_teammate:
                    _teammate_mode = True
                    logger.info(f"[{request_id}] Routing to Teammate mode (direct response)")
                    # Update session mode so nudge loop uses reduced cap
                    if session_id:
                        try:
                            from models.agent_session import AgentSession as _TMSession

                            for _s in _TMSession.query.filter(session_id=session_id):
                                if _s.status in ("running", "active", "pending"):
                                    _s.session_mode = PersonaType.TEAMMATE
                                    _s.save()
                                    break
                        except Exception:
                            pass  # Best-effort
            except Exception as e:
                logger.warning(
                    f"[{request_id}] Intent classification failed, defaulting to PM dispatch: {e}"
                )

        if _teammate_mode:
            # Teammate mode: inject Teammate instructions instead of PM dispatch
            from agent.teammate_handler import build_teammate_instructions

            enriched_message += build_teammate_instructions()
        else:
            # PM dispatch: orchestrate SDLC work stage-by-stage
            enriched_message += (
                "\n\nYou are the PM. Orchestrate SDLC work stage-by-stage:\n"
                "1. **Assess the current stage** — use read-only Bash commands "
                "(gh issue view, gh pr view, gh pr list, grep) to determine "
                "where work stands. You can run Bash for reads freely.\n"
                "1.5. **Gather prior stage context** — if a tracking issue exists, "
                "fetch the last few comments with "
                "`gh api repos/{owner}/{repo}/issues/{number}/comments` and look for "
                "comments containing `<!-- sdlc-stage-comment -->`. Include a summary "
                "of prior stage findings in the DevSession prompt so the next stage "
                "has full context from previous stages.\n"
                "2. **Spawn one dev-session for the next stage** — use the Agent tool "
                "to dispatch exactly one stage at a time:\n"
                '   Agent(subagent_type="dev-session", description="<stage>: <short desc>", '
                'prompt="Stage: <PLAN|BUILD|TEST|PATCH|REVIEW|DOCS>\\n'
                "Issue: <URL>\\nPR: <URL if exists>\\n"
                "Current state: <what's already done>\\n"
                'Acceptance criteria: <what done looks like>")\n'
                "3. **Verify the result** — check that the stage completed successfully "
                "before progressing to the next one.\n"
                "4. **Repeat** — assess, spawn, verify until the pipeline is complete "
                "or you need human input.\n\n"
                "For trivial or docs-only work, use your judgment on whether the full "
                "pipeline is warranted.\n"
                "Use the Agent tool for all coding work — slash commands like /do-build "
                "and /do-test are the dev-session's internal tools.\n\n"
                "**Communicating with the stakeholder:**\n"
                "You can send Telegram messages directly using:\n"
                '  `python tools/send_telegram.py "Your message here"`\n'
                "This sends your message immediately to the chat. Use it for:\n"
                "- Status updates and progress reports\n"
                "- Questions that need human input\n"
                "- Final delivery summaries\n"
                "Write in business terms — never expose SDLC stage names, "
                "pipeline internals, or implementation details. "
                "Speak like a project manager updating a stakeholder.\n"
                "If you don't call this tool, your return text will be "
                "automatically summarized and sent (fallback behavior)."
            )
    enriched_message += f"\nMESSAGE: {message}"

    # Log prompt summary before sending to agent
    has_worker_rules = project_mode != "pm"
    logger.info(
        f"[{request_id}] Sending to agent: {len(enriched_message)} chars, "
        f"classification={classification}, "
        f"task_list={task_list_id or 'none'}, mode={project_mode}"
    )
    wr_label = "yes" if has_worker_rules else "no (pm mode)"
    is_dm = chat_title is None
    # ChatSession always uses PM persona; otherwise resolve from config
    if _session_type == SessionType.CHAT:
        persona = PersonaType.PROJECT_MANAGER
    else:
        persona = _resolve_persona(project, chat_title, is_dm=is_dm)
    logger.info(
        f"[{request_id}] Context: persona={persona}, worker_rules={wr_label}, "
        f"session_id={session_id}"
    )

    try:
        # Extract project_key from config for env var injection
        _project_key = project.get("name", "valor").lower().replace(" ", "-") if project else None
        # Extract message_id from the session context (passed through _execute_agent_session)
        _message_id = None  # message_id not available at this layer

        logger.info(f"[{request_id}] Resolved persona: {persona}")

        # Build system prompt based on persona and project mode.
        # ChatSession (session_type="chat") uses PM persona with read-only permissions.
        custom_system_prompt = None
        _permission_mode = "bypassPermissions"  # Default: full permissions

        if _session_type == SessionType.CHAT:
            # ChatSession: PM persona, full permissions but hook-restricted.
            # Can write to docs/ and use gh CLI. Code writes blocked by pre_tool_use hook.
            custom_system_prompt = load_pm_system_prompt(working_dir)
            logger.info(f"[{request_id}] ChatSession mode: PM persona, bypassPermissions")
        elif project_mode == "pm":
            # PM mode: use PM system prompt (no WORKER_RULES, loads work-vault CLAUDE.md)
            custom_system_prompt = load_pm_system_prompt(working_dir)
        elif persona == PersonaType.TEAMMATE:
            # Teammate persona: casual mode, no WORKER_RULES
            try:
                custom_system_prompt = load_persona_prompt("teammate")
            except FileNotFoundError:
                logger.warning("Teammate persona not available, falling back to default")
        # Developer persona uses default (load_system_prompt via ValorAgent.__init__)

        # Determine gh_repo for cross-repo SDLC requests (issue #375).
        # When classification is "sdlc" and the project targets a non-ai repo,
        # set GH_REPO so all gh commands automatically target the correct repo.
        _gh_repo = None
        is_cross_repo_sdlc = (
            project_mode != "pm"
            and classification == ClassificationType.SDLC
            and project_working_dir != AI_REPO_ROOT
        )
        if is_cross_repo_sdlc:
            _github_config = project.get("github", {}) if project else {}
            _gh_org = _github_config.get("org", "")
            _gh_name = _github_config.get("repo", "")
            if _gh_org and _gh_name:
                _gh_repo = f"{_gh_org}/{_gh_name}"

        if _gh_repo:
            logger.info(f"[{request_id}] Cross-repo: GH_REPO={_gh_repo}")

        agent = ValorAgent(
            working_dir=working_dir,
            system_prompt=custom_system_prompt,
            permission_mode=_permission_mode,
            task_list_id=task_list_id,
            chat_id=chat_id,
            project_key=_project_key,
            message_id=_message_id,
            agent_session_id=agent_session_id,
            gh_repo=_gh_repo,
            target_repo=project_working_dir,
            session_type=_session_type,
        )
        response = await agent.query(enriched_message, session_id=session_id)

        elapsed = time.time() - start_time
        logger.info(f"[{request_id}] SDK responded in {elapsed:.1f}s ({len(response)} chars)")

        # Record response time metric for Teammate observability
        if _session_type == SessionType.CHAT:
            try:
                from agent.teammate_metrics import record_response_time

                record_response_time("teammate" if _teammate_mode else "work", elapsed)
            except Exception:
                pass  # Best-effort metrics

        return response

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[{request_id}] SDK error after {elapsed:.1f}s: {e}")
        # CRASH GUARD: Mark session as failed so the watchdog doesn't try to
        # interact with a dead session. Without this cleanup, the watchdog would
        # find the session still "active" and potentially trigger further errors.
        # See docs/features/coaching-loop.md "Error-Classified Output Bypass".
        try:
            from bridge.session_transcript import complete_transcript

            # Capture exception details so the reflections system can produce
            # actionable bug reports instead of "empty error summary" issues.
            error_summary = f"{type(e).__name__}: {e}"[:500]
            complete_transcript(session_id, status="failed", summary=error_summary)
        except Exception:
            pass  # Best-effort cleanup
        return (
            "Sorry, I ran into an issue and couldn't recover. "
            "The error has been logged for investigation."
        )
