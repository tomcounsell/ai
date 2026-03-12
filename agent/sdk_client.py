"""
Claude Agent SDK client wrapper for Valor.

This module provides a wrapper around ClaudeSDKClient configured for Valor's use case:
- Loads system prompt from SOUL.md
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
from agent.workflow_state import WorkflowState
from agent.workflow_types import WorkflowStateData

logger = logging.getLogger(__name__)


# === Client Registry ===
# Module-level registry of active SDK clients keyed by session_id.
# In-memory only (intentionally not persisted). On crash/reboot, the dict
# is empty and recovered jobs create fresh clients. See plan doc for
# crash safety analysis.
_active_clients: dict[str, "ClaudeSDKClient"] = {}

# === Stop Reason Registry ===
# Stores the stop_reason from the most recent ResultMessage for each session.
# Populated by ValorAgent.query(), consumed by job_queue after query completes.
# In-memory only — cleared when the session finishes.
_session_stop_reasons: dict[str, str] = {}


def get_stop_reason(session_id: str) -> str | None:
    """Get and consume the stop_reason for a completed session query."""
    return _session_stop_reasons.pop(session_id, None)


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
    saved (i.e., a prior job ran for this conversation thread). This prevents
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

# Path to SOUL.md system prompt
SOUL_PATH = Path(__file__).parent.parent / "config" / "SOUL.md"

# Log a warning when a single query's equivalent API cost exceeds this
_COST_WARN_THRESHOLD = float(os.getenv("SDK_COST_WARN_THRESHOLD", "0.50"))

# Worker safety rails injected into every agent session.
# The Observer Agent (bridge/observer.py) is the sole pipeline controller —
# it steers the worker one stage at a time via coaching messages.
# This constant provides only the safety rails the worker needs; it does NOT
# contain pipeline orchestration or /sdlc invocation instructions.
WORKER_RULES = """\
## Worker Safety Rails

Execute the task given to you. The Observer Agent controls pipeline progression — \
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


def load_system_prompt() -> str:
    """Load Valor's system prompt from SOUL.md with worker rules and completion criteria.

    System prompt structure:
        [WORKER_RULES — safety rails for the worker, FIRST — takes precedence]
        ---
        [SOUL.md — persona, attitude, purpose, communication style]
        ---
        [Work Completion Criteria — from CLAUDE.md]

    The Observer Agent (bridge/observer.py) handles pipeline orchestration.
    The worker only receives safety rails — no pipeline stages or /sdlc references.
    """
    soul_prompt = ""
    if SOUL_PATH.exists():
        soul_prompt = SOUL_PATH.read_text()
    else:
        logger.warning(f"SOUL.md not found at {SOUL_PATH}, using default prompt")
        soul_prompt = "You are Valor, an AI coworker. Be direct, concise, and helpful."

    # Append completion criteria
    criteria = load_completion_criteria()
    criteria_section = f"\n\n---\n\n{criteria}" if criteria else ""

    # Worker rules FIRST — safety rails take precedence over persona
    return f"{WORKER_RULES}\n\n---\n\n{soul_prompt}{criteria_section}"


def load_pm_system_prompt(working_directory: str) -> str:
    """Load system prompt for PM (Project Manager) mode channels.

    PM mode skips WORKER_RULES (no branch safety rails) and loads
    the project-specific CLAUDE.md from the work vault directory if it exists.
    Falls back to SOUL.md persona only.

    System prompt structure:
        [SOUL.md — persona, attitude, purpose, communication style]
        ---
        [Work-vault CLAUDE.md — PM-specific instructions for this project]

    Args:
        working_directory: Path to the work-vault project folder.

    Returns:
        Combined system prompt for PM mode.
    """
    # Load SOUL.md for persona (Valor's attitude/style is valuable in PM mode too)
    soul_prompt = ""
    if SOUL_PATH.exists():
        soul_prompt = SOUL_PATH.read_text()
    else:
        logger.warning(f"SOUL.md not found at {SOUL_PATH}, using default prompt")
        soul_prompt = "You are Valor, an AI coworker. Be direct, concise, and helpful."

    # Try to load project-specific CLAUDE.md from work-vault directory
    project_claude_path = Path(working_directory) / "CLAUDE.md"
    if project_claude_path.exists():
        project_instructions = project_claude_path.read_text()
        logger.info(f"Loaded PM instructions from {project_claude_path}")
        return f"{soul_prompt}\n\n---\n\n{project_instructions}"

    logger.info(f"No CLAUDE.md found at {project_claude_path}, using SOUL.md only for PM mode")
    return soul_prompt


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
        workflow_id: str | None = None,
        task_list_id: str | None = None,
        max_budget_usd: float | None = None,
        chat_id: str | None = None,
        project_key: str | None = None,
        message_id: int | None = None,
    ):
        """
        Initialize ValorAgent.

        Args:
            working_dir: Working directory for the agent. Defaults to ai/ repo root.
            system_prompt: Custom system prompt. Defaults to SOUL.md contents.
            permission_mode: Permission mode for tool use. Default: "bypassPermissions".
            workflow_id: Optional workflow ID for multi-phase workflow tracking.
            task_list_id: Optional task list ID to scope sub-agent Task storage
                via CLAUDE_CODE_TASK_LIST_ID environment variable.
            max_budget_usd: Maximum budget in USD for a single agent session.
                Defaults to SDK_MAX_BUDGET_USD env var or 5.00.
            chat_id: Optional chat ID for routing context injection.
            project_key: Optional project key for routing context injection.
            message_id: Optional message ID for routing context injection.
        """
        self.working_dir = Path(working_dir) if working_dir else Path(__file__).parent.parent
        self.system_prompt = system_prompt or load_system_prompt()
        self.permission_mode = permission_mode
        self.workflow_id = workflow_id
        self.task_list_id = task_list_id
        self.max_budget_usd = max_budget_usd or float(os.getenv("SDK_MAX_BUDGET_USD", "5.00"))
        self.chat_id = chat_id
        self.project_key = project_key
        self.message_id = message_id
        self.workflow_state: WorkflowState | None = None

        # Load workflow state if workflow_id provided
        if self.workflow_id:
            try:
                self.workflow_state = WorkflowState.load(self.workflow_id)
                phase = self.workflow_state.data.phase if self.workflow_state.data else None
                logger.info(f"Loaded workflow state: {self.workflow_id} (phase={phase})")
            except FileNotFoundError:
                logger.warning(
                    f"Workflow ID {self.workflow_id} provided but no state file found. "
                    "Continuing without workflow state."
                )
            except Exception as e:
                logger.error(f"Failed to load workflow state for {self.workflow_id}: {e}")
                # Continue without workflow state rather than failing initialization

    def _build_workflow_context(self) -> str:
        """Build workflow context string for system prompt.

        Returns:
            Formatted workflow context including ID, phase, status, and plan file.
        """
        if not self.workflow_state or not self.workflow_state.data:
            return ""

        data = self.workflow_state.data
        context_parts = [
            "---",
            "WORKFLOW CONTEXT:",
            f"- Workflow ID: {data.workflow_id}",
            f"- Plan: {data.plan_file}",
        ]

        if data.phase:
            context_parts.append(f"- Current Phase: {data.phase}")
        if data.status:
            context_parts.append(f"- Status: {data.status}")
        if data.branch_name:
            context_parts.append(f"- Branch: {data.branch_name}")
        if data.tracking_url:
            context_parts.append(f"- Tracking: {data.tracking_url}")

        context_parts.append("---")
        return "\n".join(context_parts)

    def update_workflow_state(
        self, phase: str | None = None, status: str | None = None, **kwargs
    ) -> None:
        """Update workflow state and persist to disk.

        Args:
            phase: Optional workflow phase to update
            status: Optional workflow status to update
            **kwargs: Additional state fields to update

        Raises:
            ValueError: If no workflow_state is loaded
        """
        if not self.workflow_state:
            raise ValueError(
                "Cannot update workflow state - no workflow_id provided at initialization"
            )

        # Build update dict
        update_dict = {}
        if phase is not None:
            update_dict["phase"] = phase
        if status is not None:
            update_dict["status"] = status
        update_dict.update(kwargs)

        # Update and save
        self.workflow_state.update(**update_dict)
        self.workflow_state.save()
        logger.info(
            f"Updated workflow state: {self.workflow_id} "
            f"(phase={self.workflow_state.data.phase if self.workflow_state.data else None}, "
            f"status={self.workflow_state.data.status if self.workflow_state.data else None})"
        )

    def get_workflow_data(self) -> WorkflowStateData | None:
        """Get current workflow state data.

        Returns:
            WorkflowStateData if workflow state is loaded, None otherwise
        """
        if self.workflow_state:
            return self.workflow_state.data
        return None

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

        # Build system prompt with workflow context if workflow_id is present
        system_prompt = self.system_prompt
        if self.workflow_id and self.workflow_state and self.workflow_state.data:
            workflow_context = self._build_workflow_context()
            system_prompt += f"\n\n{workflow_context}"
            logger.debug(f"Including workflow context in system prompt: {self.workflow_id}")

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
            max_budget_usd=self.max_budget_usd,
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

        # Bug 2 fix (issue #374): Reset watchdog tool counts at query start
        # so continuation sessions don't inherit inflated counts from prior runs.
        if session_id:
            from agent.health_check import reset_session_count

            reset_session_count(session_id)

        # Log resources before SDK initialization
        init_start = time.time()
        logger.info(f"[SDK-init] Starting SDK initialization for session {session_id}")
        _log_system_resources("SDK-init-pre")

        try:
            async with ClaudeSDKClient(options) as client:
                # Log successful initialization
                init_elapsed = time.time() - init_start
                logger.info(f"[SDK-init] SDK initialized successfully in {init_elapsed:.2f}s")
                _log_system_resources("SDK-init-post")
                # Register client for steering access
                if session_id:
                    _active_clients[session_id] = client
                    logger.debug(f"Registered active client for session {session_id}")

                await client.query(message)

                while True:
                    async for msg in client.receive_response():
                        if isinstance(msg, AssistantMessage):
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    response_parts.append(block.text)
                        elif isinstance(msg, ResultMessage):
                            # Bug 1 fix (issue #374): Store Claude Code session UUID
                            # so continuation sessions resume the correct transcript.
                            if msg.session_id and session_id:
                                _store_claude_session_uuid(session_id, msg.session_id)
                            # Capture stop_reason for Observer routing decisions
                            if msg.stop_reason and session_id:
                                _session_stop_reasons[session_id] = msg.stop_reason
                                logger.info(
                                    f"SDK stop_reason={msg.stop_reason} for session {session_id}"
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
                                if cost >= _COST_WARN_THRESHOLD:
                                    logger.warning(f"High cost query: {summary}")
                                else:
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

        except Exception as e:
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
        finally:
            # Always unregister client from registry
            if session_id:
                _active_clients.pop(session_id, None)
                # Note: _session_stop_reasons is NOT cleaned here — it's consumed
                # by get_stop_reason() in job_queue after query returns. The pop()
                # in get_stop_reason() handles cleanup. If the Observer never runs
                # (crash), entries are tiny (session_id -> str) and cleared on restart.
                logger.debug(f"Unregistered active client for session {session_id}")

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


async def get_agent_response_sdk(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
    sender_id: int | None = None,
    workflow_id: str | None = None,
    task_list_id: str | None = None,
    correlation_id: str | None = None,
) -> str:
    """
    Get agent response using Claude Agent SDK.

    This function matches the signature of the existing get_agent_response()
    in telegram_bridge.py to enable seamless switching via feature flag.

    Args:
        message: The message to process
        session_id: Session ID for conversation continuity
        sender_name: Name of the sender (for logging)
        chat_title: Chat title (for logging)
        project: Project configuration dict
        chat_id: Chat ID (unused, for compatibility)
        sender_id: Telegram user ID (for permission checking)
        workflow_id: Optional 8-char workflow identifier for tracked work
        task_list_id: Optional task list ID to scope sub-agent Task storage
        correlation_id: Optional end-to-end tracing ID from the bridge

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
        classification = "question"
        working_dir = project_working_dir
        logger.info(f"[{request_id}] PM mode: cwd={working_dir}, skipping SDLC classification")
    else:
        # Dev mode: classify and route as before
        from bridge.routing import classify_work_request

        classification = classify_work_request(message)
        if classification == "sdlc" and project_working_dir != AI_REPO_ROOT:
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
    if workflow_id:
        enriched_message += f"\nWORKFLOW_ID: {workflow_id}"
    enriched_message += f"\nSESSION_ID: {session_id}"
    if task_list_id:
        enriched_message += f"\nTASK_SCOPE: {task_list_id}"
    enriched_message += (
        "\nSCOPE: This session is scoped to the message below from this sender. "
        "When reporting completion or summarizing work, only reference tasks and "
        "work initiated in this specific session. Do not include work, PRs, or "
        "requests from other sessions, other senders, or prior conversation threads."
    )
    # For SDLC-routed requests, inject target repo context (never for PM mode)
    if project_mode != "pm" and classification == "sdlc" and project_working_dir != AI_REPO_ROOT:
        github_config = project.get("github", {}) if project else {}
        github_org = github_config.get("org", "")
        github_repo = github_config.get("repo", "")
        enriched_message += (
            f"\nWORK REQUEST for project {project_name}.\nTARGET REPO: {project_working_dir}"
        )
        if github_org and github_repo:
            enriched_message += f"\nGITHUB: {github_org}/{github_repo}"
        enriched_message += "\nInvoke /sdlc immediately."
    enriched_message += f"\nMESSAGE: {message}"

    # Log prompt summary before sending to agent
    has_workflow = bool(workflow_id)
    has_worker_rules = project_mode != "pm"
    logger.info(
        f"[{request_id}] Sending to agent: {len(enriched_message)} chars, "
        f"classification={classification}, has_workflow={has_workflow}, "
        f"task_list={task_list_id or 'none'}, mode={project_mode}"
    )
    wr_label = "yes" if has_worker_rules else "no (pm mode)"
    logger.info(
        f"[{request_id}] Context: soul=yes, worker_rules={wr_label}, "
        f"workflow_context={'yes' if has_workflow else 'no'}, "
        f"session_id={session_id}"
    )

    try:
        # Extract project_key from config for env var injection
        _project_key = project.get("name", "valor").lower().replace(" ", "-") if project else None
        # Extract message_id from the job context (passed through _execute_job)
        _message_id = None  # message_id not available at this layer

        # PM mode: use PM system prompt (no WORKER_RULES, loads work-vault CLAUDE.md)
        pm_system_prompt = None
        if project_mode == "pm":
            pm_system_prompt = load_pm_system_prompt(working_dir)

        agent = ValorAgent(
            working_dir=working_dir,
            system_prompt=pm_system_prompt,
            workflow_id=workflow_id,
            task_list_id=task_list_id,
            chat_id=chat_id,
            project_key=_project_key,
            message_id=_message_id,
        )
        response = await agent.query(enriched_message, session_id=session_id)

        elapsed = time.time() - start_time
        logger.info(f"[{request_id}] SDK responded in {elapsed:.1f}s ({len(response)} chars)")

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

            complete_transcript(session_id, status="failed")
        except Exception:
            pass  # Best-effort cleanup
        return (
            "Sorry, I ran into an issue and couldn't recover. "
            "The error has been logged for investigation."
        )
