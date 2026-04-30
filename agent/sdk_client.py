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
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
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
_updated_at_timestamps: dict[str, float] = {}

# === Turn Count Tracking (issue #1127) ===
# Tracks the most recent `ResultMessage.num_turns` observed per session. Used
# by the SDK-tick backstop in `agent/session_executor.py` to detect compaction
# events where the PreCompact hook was skipped — a drop in num_turns across
# consecutive ResultMessages is the SDK's observable signature of a compaction
# that rewrote conversation history. In-memory only; reset on process restart.
_session_turn_counts: dict[str, int] = {}

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
    _updated_at_timestamps[session_id] = time.time()


def get_session_updated_at(session_id: str) -> float | None:
    """Get the timestamp of the last activity for a session.

    Returns:
        Unix timestamp of last tool call or log output, or None if
        no activity has been recorded for this session.
    """
    return _updated_at_timestamps.get(session_id)


def clear_session_activity(session_id: str) -> None:
    """Remove activity tracking for a completed/abandoned session."""
    _updated_at_timestamps.pop(session_id, None)


def get_turn_count(session_id: str) -> int | None:
    """Return the most recent ResultMessage.num_turns observed for a session.

    Used by the SDK-tick backstop in the executor's output-callback path to
    detect compaction-induced turn-count drops. Returns None if no
    ResultMessage has been observed yet for this session. See issue #1127.
    """
    return _session_turn_counts.get(session_id)


def record_turn_count(session_id: str, num_turns: int) -> None:
    """Record the observed ResultMessage.num_turns for a session.

    Called by the SDK query loop when a ResultMessage arrives. In-memory only.
    """
    if session_id and num_turns is not None:
        try:
            _session_turn_counts[session_id] = int(num_turns)
        except (TypeError, ValueError):
            pass


def clear_turn_count(session_id: str) -> None:
    """Remove turn-count tracking for a completed/abandoned session."""
    _session_turn_counts.pop(session_id, None)


def _get_prior_session_uuid(session_id: str) -> str | None:
    """Look up the stored Claude Code UUID for a prior session.

    Returns the claude_session_uuid if a prior AgentSession exists with this
    session_id and has a stored UUID. Returns None if no prior session exists
    or no UUID was stored (first message in session).

    Used by _create_options() to resume the correct Claude Code transcript
    instead of falling back to the most recent session file on disk.

    See issue #232 for the original cross-wire bug, and issue #374 Bug 1
    for the UUID mapping fix.

    ``killed`` and ``failed`` statuses are included since #1061 to support
    operator-initiated resume via ``valor-session resume``. The original
    narrow filter defended against fresh-session UUID reuse (#374). Today
    the primary defense is keying the lookup on ``session_id`` (so only this
    thread's records are considered) and ``created_at desc`` sort (so an
    ancient killed record cannot shadow a newer completed one).
    Killed/failed sessions are included because operator-initiated resume
    is explicitly asking for this specific transcript to continue; the UUID
    it needs is valid until the transcript file is cleaned up on disk.
    """
    try:
        from models.agent_session import AgentSession

        sessions = [
            s
            for s in AgentSession.query.filter(session_id=session_id)
            if s.status in ("completed", "running", "active", "dormant", "killed", "failed")
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


def _env_flag_enabled(var_name: str, default: bool = True) -> bool:
    """Return True unless the env var is explicitly set to a falsy string.

    Used by watchdog-hardening feature gates (issue #1128). Falsy values
    (case-insensitive): "0", "false", "no". Any other value — including
    unset — means the flag is enabled.
    """
    raw = os.environ.get(var_name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no"}


def _usage_field(usage, name: str) -> int:
    """Safely read a numeric field from a `usage` container.

    Handles both SDK-style attribute access (dataclass-like objects) and
    harness-style dict access on the same field name. Missing or None
    values default to 0. Non-integer values default to 0 as well.

    Accepted shapes:
      * `None` → 0
      * dict (harness `data["usage"]`)   → `.get(name, 0) or 0`
      * object with attribute (SDK `msg.usage`) → `getattr(..., name, 0) or 0`
    """
    if usage is None:
        return 0
    raw: object
    if isinstance(usage, dict):
        raw = usage.get(name, 0)
    else:
        raw = getattr(usage, name, 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def accumulate_session_tokens(
    session_id: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cost_usd: float | None,
) -> None:
    """Add per-turn token + cost counts to an AgentSession record.

    Called as a side effect from BOTH execution paths so token accounting
    works uniformly for every session type:

      * SDK path: `ClaudeSDKClient` returns `ResultMessage.usage` + `.total_cost_usd`
        inside the query loop (see the `ResultMessage` handler below).
      * Harness path: `claude -p stream-json` emits `usage` + `total_cost_usd`
        on the `result` event; `_run_harness_subprocess` extracts them and
        threads them back to `get_response_via_harness`, which calls this
        helper before returning (mirroring `_store_claude_session_uuid`).

    Without the harness-path call, production PM/Dev/Teammate sessions —
    which always use the harness — would report 0 tokens forever (the
    critique B3 blocker).

    Persistence: Popoto `save(update_fields=[...])` with explicit field list
    so a concurrent write to other fields (e.g. `status`, `updated_at`) does
    not clobber this update. Fail-quiet on any exception — token accounting
    must never raise into the SDK / harness return path.

    Gate: `WATCHDOG_TOKEN_TRACKING_ENABLED` (default on). Disabling is an
    operator-only escape hatch for debugging or if a downstream issue is
    traced to this helper.

    Args:
        session_id: Bridge/Telegram session_id. No-op when None.
        input_tokens: Input token count for this turn (fallback 0 on None).
        output_tokens: Output token count for this turn (fallback 0 on None).
        cache_read_tokens: Cache-read input tokens for this turn (fallback 0).
        cost_usd: Dollar cost for this turn, taken verbatim from the SDK/CLI.
            Never recomputed. Fallback 0.0 on None.
    """
    if not session_id:
        return
    if not _env_flag_enabled("WATCHDOG_TOKEN_TRACKING_ENABLED"):
        return

    # Defensive coercion: SDK / harness occasionally omit fields on error
    # paths or older CLI versions.
    try:
        in_delta = int(input_tokens or 0)
        out_delta = int(output_tokens or 0)
        cache_delta = int(cache_read_tokens or 0)
        cost_delta = float(cost_usd or 0.0)
    except (TypeError, ValueError):
        logger.warning(
            "accumulate_session_tokens: non-numeric inputs for session %s "
            "(in=%r out=%r cache=%r cost=%r) — skipping",
            session_id,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cost_usd,
        )
        return

    # No-op when there is nothing to add (saves a Redis round-trip).
    if in_delta == 0 and out_delta == 0 and cache_delta == 0 and cost_delta == 0.0:
        return

    try:
        from popoto.exceptions import ModelException

        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            logger.debug(
                "accumulate_session_tokens: no AgentSession for session_id=%s — skipping",
                session_id,
            )
            return
        # Newest record wins — matches the pattern used by
        # `_store_claude_session_uuid`.
        sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
        session = sessions[0]
        try:
            session.total_input_tokens = (session.total_input_tokens or 0) + in_delta
            session.total_output_tokens = (session.total_output_tokens or 0) + out_delta
            session.total_cache_read_tokens = (session.total_cache_read_tokens or 0) + cache_delta
            session.total_cost_usd = float(session.total_cost_usd or 0.0) + cost_delta
            session.save(
                update_fields=[
                    "total_input_tokens",
                    "total_output_tokens",
                    "total_cache_read_tokens",
                    "total_cost_usd",
                ]
            )
        except ModelException as e:
            logger.warning(
                "accumulate_session_tokens: ModelException on save for session %s: %s",
                session_id,
                e,
            )
    except Exception as e:
        logger.warning(
            "accumulate_session_tokens(%s) failed: %s",
            session_id,
            e,
            exc_info=False,
        )


def _log_context_usage_if_risky(
    session_id: str | None,
    model: str | None,
    usage: dict | None,
) -> None:
    """Emit a single WARNING log when per-turn context usage exceeds 75%.

    Observability-only helper for issue #1099 Mode 2. Reads ``usage.input_tokens``
    from the harness ``result`` event and compares it against the model's
    configured context window (via ``config.models.get_model_context_window``).
    When the ratio exceeds 0.75, emits one structured ``logger.warning`` record
    that dashboards and operators can grep for (``grep "context_usage"``).

    No state change. No behavior change. The helper wraps its entire body in
    ``try/except Exception: return`` so observability can never crash the turn.

    Falls through silently when:
      * ``usage`` is ``None`` (no ``result`` event fired)
      * ``input_tokens`` is missing, zero, or non-numeric
      * ``model`` is ``None`` or not registered in ``config/models.py``
      * The ratio is at or below 0.75

    When the model is registered but the context window is unknown, emits a
    single WARNING flagging the unknown model so operators can fix the
    registration.
    """
    try:
        input_tokens = int((usage or {}).get("input_tokens", 0) or 0)
        if input_tokens <= 0:
            return
        from config.models import get_model_context_window

        window = get_model_context_window(model)
        if not window:
            logger.warning(
                "[harness] context_usage: unknown model=%r, skipping pct calc (session_id=%s)",
                model,
                session_id,
            )
            return
        pct = input_tokens / window
        if pct > 0.75:
            logger.warning(
                "context_usage pct=%.2f session_id=%s model=%s input_tokens=%d",
                pct,
                session_id,
                model,
                input_tokens,
            )
    except (
        Exception
    ):  # swallow-ok: observability-only; context-logging must never crash the session
        # Observability must never crash the turn. See issue #1099 Mode 2.
        return


def _store_exit_returncode(session_id: str | None, returncode: int | None) -> None:
    """Persist the last subprocess exit code on the AgentSession record.

    Best-effort writer for issue #1099 Mode 4. Used by the health check's
    recovery branch to distinguish OS-initiated OOM kills (``returncode == -9``)
    from health-check-initiated kills. See ``agent/session_health.py`` for the
    OOM-defer logic that reads the persisted value.

    Args:
        session_id: Bridge/Telegram session_id. No-op when None.
        returncode: Final subprocess exit code. No-op when None.

    Failure policy: every exception is swallowed at DEBUG level. The harness
    return path must never raise from this side effect.
    """
    if not session_id or returncode is None:
        return
    try:
        from models.agent_session import AgentSession

        for s in AgentSession.query.filter(session_id=session_id):
            s.exit_returncode = int(returncode)
            s.save(update_fields=["exit_returncode"])
            break
    except Exception as _e:
        logger.debug("exit_returncode store failed for session_id=%s: %s", session_id, _e)


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

        # Work item slug (Dev sessions use session.slug, legacy uses slug)
        slug = getattr(session, "slug", None) or getattr(session, "slug", None)
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

# Path to persona base directory (stays in repo — not private)
PERSONAS_BASE_DIR = Path(__file__).parent.parent / "config" / "personas"

# Path to persona segments directory
PERSONAS_SEGMENTS_DIR = PERSONAS_BASE_DIR / "segments"

# Path to identity config
IDENTITY_CONFIG_PATH = Path(__file__).parent.parent / "config" / "identity.json"

# Path to private identity override (iCloud-synced)
PRIVATE_IDENTITY_PATH = Path.home() / "Desktop" / "Valor" / "identity.json"

# Path to persona overlay files (private, iCloud-synced)
# Overlays live in ~/Desktop/Valor/personas/ — falls back to config/personas/ for dev
PERSONAS_OVERLAY_DIR = Path.home() / "Desktop" / "Valor" / "personas"

# Path to PRINCIPAL.md — supervisor's operating context for strategic decisions
PRINCIPAL_PATH = Path(__file__).parent.parent / "config" / "PRINCIPAL.md"

# Worker safety rails injected into every agent session.
# The PM session is the sole pipeline controller —
# it steers the worker one stage at a time via nudge messages.
# This constant provides only the safety rails the worker needs; it does NOT
# contain pipeline orchestration or /sdlc invocation instructions.
WORKER_RULES = """\
## Worker Safety Rails

Execute the task given to you. The PM session controls pipeline progression — \
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
    the PM session (full) to ground autonomous decisions.

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


def load_identity() -> dict:
    """Load structured identity data from config/identity.json.

    Merges with ~/Desktop/Valor/identity.json if present (shallow merge,
    private values override repo values for matching keys).

    Returns:
        Dict with identity fields (name, email, timezone, etc.).

    Raises:
        FileNotFoundError: If config/identity.json does not exist.
    """
    if not IDENTITY_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Identity config not found at {IDENTITY_CONFIG_PATH}. "
            "The identity.json file is required for the persona system."
        )

    try:
        identity = json.loads(IDENTITY_CONFIG_PATH.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed identity config at {IDENTITY_CONFIG_PATH}: {e}") from e

    # Remove doc field from identity data
    identity.pop("_doc", None)

    # Merge private overrides if available
    if PRIVATE_IDENTITY_PATH.exists():
        try:
            private = json.loads(PRIVATE_IDENTITY_PATH.read_text())
            private.pop("_doc", None)
            identity.update(private)  # Shallow merge, private wins
            logger.info(f"Merged private identity overrides from {PRIVATE_IDENTITY_PATH}")
        except json.JSONDecodeError as e:
            logger.warning(
                f"Malformed private identity override at {PRIVATE_IDENTITY_PATH}: {e}. "
                "Using repo defaults only."
            )

    return identity


def _assemble_segments(identity: dict) -> str:
    """Assemble prompt segments from manifest with identity field injection.

    Reads config/personas/segments/manifest.json for segment order,
    loads each segment file, and injects identity fields via {{identity.*}}
    marker substitution.

    Args:
        identity: Dict of identity fields from load_identity().

    Returns:
        Combined segment content with identity fields injected.

    Raises:
        FileNotFoundError: If manifest.json or any segment file is missing.
    """
    manifest_path = PERSONAS_SEGMENTS_DIR / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Segment manifest not found at {manifest_path}. "
            "The manifest.json file is required for segment assembly."
        )

    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed segment manifest at {manifest_path}: {e}") from e

    segment_names = manifest.get("segments", [])
    if not segment_names:
        raise ValueError(f"Segment manifest at {manifest_path} has no segments listed.")

    segments = []
    for seg_name in segment_names:
        seg_path = PERSONAS_SEGMENTS_DIR / seg_name
        if not seg_path.exists():
            raise FileNotFoundError(
                f"Segment file not found at {seg_path}. "
                f"Listed in manifest but missing from {PERSONAS_SEGMENTS_DIR}."
            )
        content = seg_path.read_text()

        # Inject identity fields via {{identity.*}} marker substitution
        for key, value in identity.items():
            content = content.replace(f"{{{{identity.{key}}}}}", str(value))

        segments.append(content)

    return "\n\n---\n\n".join(segments)


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
    """Load persona prompt from composable segments + overlay.

    Segments are assembled from config/personas/segments/ per manifest.json,
    with identity fields injected from config/identity.json.
    Overlays are read from ~/Desktop/Valor/personas/{persona}.md (private, iCloud-synced),
    falling back to config/personas/{persona}.md (in-repo, for development).

    Args:
        persona: Persona name — one of "developer", "project-manager", "teammate".
            Defaults to "developer".

    Returns:
        Combined persona prompt (assembled segments + overlay).

    Raises:
        FileNotFoundError: If identity config, segments, or overlay files are missing.
    """
    # Load identity and assemble segments
    identity = load_identity()
    base_content = _assemble_segments(identity)

    # Resolve overlay: ~/Desktop/Valor/personas/ first, then config/personas/
    overlay_path = _resolve_overlay_path(persona)

    if overlay_path.exists():
        overlay_content = overlay_path.read_text()
        if persona == "project-manager" and "CRITIQUE" not in overlay_content:
            logger.warning(
                f"PM persona overlay '{overlay_path}' is missing CRITIQUE gate rules "
                "— pipeline integrity may be compromised"
            )
        # Workflow-announcement guard: the PM overlay MUST contain the bucket-#3
        # announce-and-pause rule so coding/automation/config requests don't get
        # silently implemented. The substring is the unique opening clause of the
        # required announcement phrase. This guards against overlay drift on
        # bridge machines where the private overlay is iCloud-synced and could
        # fall out of sync with the in-repo template. Mirrors the CRITIQUE check
        # above and PR #802's loader-warning pattern. See issue #1189.
        if (
            persona == "project-manager"
            and "Unless you directly instruct me to skip" not in overlay_content
        ):
            logger.warning(
                f"PM persona overlay '{overlay_path}' is missing the "
                "workflow-announcement rule — PM may silently implement "
                "code/config changes without surfacing the SDLC contract."
            )
        if persona == "project-manager" and 'subagent_type="dev-session"' in overlay_content:
            logger.warning(
                f"PM persona overlay '{overlay_path}' still contains Agent tool dispatch "
                'instructions (subagent_type="dev-session"). '
                "Dev sessions are now created via "
                "`python -m tools.valor_session create --role dev`. "
                "Update ~/Desktop/Valor/personas/project-manager.md to remove the Agent tool "
                "dispatch pattern."
            )
        logger.info(f"Loaded persona '{persona}' from {overlay_path}")
        return f"{base_content}\n\n---\n\n{overlay_content}"

    # Invalid persona name — fall back to developer with warning
    if persona not in ("developer", "project-manager", "teammate"):
        logger.warning(f"Unknown persona '{persona}', falling back to developer persona")
        developer_path = _resolve_overlay_path("developer")
        if developer_path.exists():
            return f"{base_content}\n\n---\n\n{developer_path.read_text()}"

    # Persona overlay missing — fail loudly (no SOUL.md fallback)
    raise FileNotFoundError(
        f"Persona overlay '{persona}' not found at {overlay_path}. "
        "All persona overlays must exist — no fallback available."
    )


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

    The PM session handles pipeline orchestration via nudge loop.
    The worker only receives safety rails — no pipeline stages or /sdlc references.
    """
    persona_prompt = load_persona_prompt("developer")

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
    persona_prompt = load_persona_prompt("project-manager")

    # Try to load project-specific CLAUDE.md from work-vault directory
    project_claude_path = Path(working_directory) / "CLAUDE.md"
    if project_claude_path.exists():
        project_instructions = project_claude_path.read_text()
        logger.info(f"Loaded PM instructions from {project_claude_path}")
        return f"{persona_prompt}\n\n---\n\n{project_instructions}"

    logger.info(f"No CLAUDE.md found at {project_claude_path}, using persona only for PM mode")
    return persona_prompt


def _infer_task_type_from_message(message: str, classification) -> str | None:
    """Infer a TRM task_type from the incoming PM message using pattern matching.

    Pattern-based only — no LLM calls. Checks for SDLC stage keywords, issue
    URLs, and classification type. Returns the most specific match or None if
    the task type cannot be determined.

    Args:
        message: The raw message text being handled by the PM session.
        classification: ClassificationType value from bridge classification.

    Returns:
        A task_type string from TASK_TYPE_VOCABULARY, or None if indeterminate.
    """
    if not message:
        return None

    msg_lower = message.lower()

    # Check for explicit SDLC stage keywords in the message
    if "stage: build" in msg_lower or "do-build" in msg_lower:
        return "sdlc-build"
    if "stage: test" in msg_lower or "do-test" in msg_lower:
        return "sdlc-test"
    if "stage: patch" in msg_lower or "do-patch" in msg_lower:
        return "sdlc-patch"
    if "stage: plan" in msg_lower or "do-plan" in msg_lower or "make a plan" in msg_lower:
        return "sdlc-plan"

    # Classification-based inference (BUG is not a ClassificationType member;
    # bug detection happens via message content, not classification enum)
    if isinstance(classification, str) and classification.lower() == "bug":
        return "bug-fix"

    # Issue URL present without explicit stage → likely SDLC orchestration start
    if "github.com" in msg_lower and "/issues/" in msg_lower:
        return "sdlc-plan"

    return None


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


def _resolve_sentry_auth_token() -> str | None:
    """Resolve Sentry auth token for PM/Teammate session env injection.

    Cascade: SENTRY_PERSONAL_TOKEN env var -> SENTRY_AUTH_TOKEN env var ->
    ~/Desktop/Valor/.env file read (only in terminal mode; launchd blocks
    ~/Desktop under TCC).

    Returns:
        The Sentry auth token, or None if no source resolves successfully.
    """
    token = os.environ.get("SENTRY_PERSONAL_TOKEN") or os.environ.get("SENTRY_AUTH_TOKEN")
    if token:
        return token
    if os.environ.get("VALOR_LAUNCHD"):
        return None  # launchd cannot read ~/Desktop (macOS TCC)
    sentry_env = Path.home() / "Desktop" / "Valor" / ".env"
    try:
        if not sentry_env.exists():
            return None
        for line in sentry_env.read_text().splitlines():
            if line.startswith("SENTRY_PERSONAL_TOKEN="):
                return line.split("=", 1)[1]
    except (OSError, PermissionError):
        return None
    return None


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
        model: str | None = None,
    ):
        """
        Initialize ValorAgent.

        Args:
            working_dir: Working directory for the agent. Defaults to ai/ repo root.
            system_prompt: Custom system prompt. Defaults to persona segments.
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
            session_type: Session type ("pm", "teammate", or "dev"). Injected as
                SESSION_TYPE env var so hooks can enforce write restrictions.
            model: Optional Claude model name (e.g. "sonnet", "opus"). When set,
                overrides the environment-level model for this session. None inherits
                the CLI default. Used for per-SDLC-stage model selection.
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
        self.model = model or None  # Normalize empty string to None

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

        # Inject parent session ID so child subprocess user_prompt_submit.py can link the
        # local-* AgentSession back to this PM/Teammate session (issue #808).
        # Only PM/Teammate sessions spawn tracked children — dev sessions are excluded.
        # The env var carries the agent_session_id UUID (agt_xxx), which is the canonical
        # FK stored in parent_agent_session_id on the child's AgentSession record.
        if self.agent_session_id and self.session_type in (SessionType.PM, SessionType.TEAMMATE):
            env["VALOR_PARENT_SESSION_ID"] = self.agent_session_id

        # Cross-repo gh resolution: set GH_REPO so all `gh` CLI commands in the
        # subprocess automatically target the correct repo (issue #375). This is
        # the deterministic fix -- SKILL.md --repo instructions remain as a safety net.
        if self.gh_repo:
            env["GH_REPO"] = self.gh_repo
        if self.target_repo:
            env["SDLC_TARGET_REPO"] = str(self.target_repo)
        if self.session_type:
            env["SESSION_TYPE"] = self.session_type

        # PM sessions: inject Telegram context so the PM session can send its
        # own messages via tools/send_telegram.py (issue #497).
        # chat_id comes from the project config; reply_to is resolved from
        # the AgentSession's telegram_message_id in _extract_sdlc_env_vars below.
        if self.session_type in (SessionType.PM, SessionType.TEAMMATE) and self.chat_id:
            env["TELEGRAM_CHAT_ID"] = str(self.chat_id)

        # PM sessions: inject Sentry auth token so sentry-cli works without
        # manual export. Token resolution is delegated to _resolve_sentry_auth_token,
        # which encapsulates the env-var-then-file cascade and the VALOR_LAUNCHD
        # short-circuit (macOS TCC blocks open() on ~/Desktop files under launchd).
        if self.session_type in (SessionType.PM, SessionType.TEAMMATE):
            sentry_token = _resolve_sentry_auth_token()
            if sentry_token:
                env["SENTRY_AUTH_TOKEN"] = sentry_token

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

        options_kwargs: dict = dict(
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
        # Per-session model override: set only when explicitly specified so that
        # sessions without a model use the SDK/CLI default (e.g. max-quality).
        if self.model:
            options_kwargs["model"] = self.model
        return ClaudeAgentOptions(**options_kwargs)

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
                                # Capture stop_reason for nudge loop routing decisions
                                if msg.stop_reason and session_id:
                                    _session_stop_reasons[session_id] = msg.stop_reason
                                    logger.info(
                                        "SDK stop_reason=%s for session %s",
                                        msg.stop_reason,
                                        session_id,
                                    )

                                # Record turn count for SDK-tick backstop
                                # (issue #1127). Tracked per-session in an
                                # in-memory registry, consulted by the
                                # executor's output-callback.
                                if session_id and msg.num_turns is not None:
                                    record_turn_count(session_id, msg.num_turns)

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
                                    # Analytics: record token cost and turn count
                                    try:
                                        from analytics.collector import record_metric

                                        dims = {"session_id": session_id} if session_id else {}
                                        record_metric("session.cost_usd", cost, dims)
                                        if turns is not None:
                                            record_metric("session.turns", float(turns), dims)
                                    except Exception:
                                        pass

                                # Per-session token accumulation (issue #1128 —
                                # SDK path). Harness path extracts the same
                                # fields off the `result` event inside
                                # `_run_harness_subprocess`; both call the
                                # same `accumulate_session_tokens` helper.
                                if session_id:
                                    usage_obj = getattr(msg, "usage", None)
                                    accumulate_session_tokens(
                                        session_id,
                                        _usage_field(usage_obj, "input_tokens"),
                                        _usage_field(usage_obj, "output_tokens"),
                                        _usage_field(usage_obj, "cache_read_input_tokens"),
                                        msg.total_cost_usd,
                                    )
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
                # Clean up turn-count tracking (issue #1127) — session is done
                clear_turn_count(session_id)
                # Note: _session_stop_reasons is NOT cleaned here — it's consumed
                # by get_stop_reason() in session_queue after query returns. The pop()
                # in get_stop_reason() handles cleanup. If the nudge loop never runs
                # (crash), entries are tiny (session_id -> str) and cleared on restart.
                logger.debug(f"Unregistered active client for session {session_id}")

            # (Phase 5: session_registry removed; no cleanup needed)

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


def _load_persona_overlay_with_log(
    persona: str,
    request_id: str,
    session_id: str | None,
    fallback: str | None = None,
) -> str | None:
    """Load a persona overlay and emit one canonical log line.

    Wraps :func:`load_persona_prompt` with structured per-session logging so
    the test-cuttlefish-* skills (and any future test) can grep
    ``Persona overlay`` filtered by ``session_id=<sid>`` and get exactly one
    definitive answer per session. Without this funnel, the existing
    ``Resolved persona:`` and harness ``Appending N-char system prompt`` lines
    were not always emitted in worker.log, leaving the test unable to confirm
    whether the requested persona had loaded.

    Behavior:

    - Success: emits ``Persona overlay loaded: name=<persona> prompt_chars=<N>
      session_id=<sid>`` at INFO and returns the prompt.
    - Missing overlay with successful fallback: emits a single
      ``Persona overlay missing: requested=<persona> fell_back_to=<fallback>
      session_id=<sid>`` at WARNING and returns the fallback prompt. The
      ``Persona overlay loaded:`` line is intentionally NOT emitted in this
      branch — the WARNING is the canonical signal.
    - Missing overlay with no fallback (or fallback also missing): emits a
      WARNING and returns ``None``.

    Args:
        persona: The persona name to load (e.g. "customer-service",
            "teammate").
        request_id: The per-call correlation/request id used as the log
            prefix (matches sibling log lines like ``[{request_id}]``).
        session_id: The session id (for grep correlation). May be ``None``
            for callers that don't have one in scope.
        fallback: Optional persona name to try when the requested overlay
            file is missing. ``None`` means no fallback.

    Returns:
        The persona prompt string, or ``None`` if both the requested overlay
        and the fallback are missing.
    """
    try:
        prompt = load_persona_prompt(persona)
        logger.info(
            f"[{request_id}] Persona overlay loaded: name={persona} "
            f"prompt_chars={len(prompt) if prompt else 0} "
            f"session_id={session_id}"
        )
        return prompt
    except FileNotFoundError:
        if fallback:
            try:
                fallback_prompt = load_persona_prompt(fallback)
                logger.warning(
                    f"[{request_id}] Persona overlay missing: requested={persona} "
                    f"fell_back_to={fallback} session_id={session_id}"
                )
                return fallback_prompt
            except FileNotFoundError:
                logger.warning(
                    f"[{request_id}] Persona overlay missing: requested={persona} "
                    f"fell_back_to={fallback} session_id={session_id} "
                    "(fallback also missing, returning None)"
                )
                return None
        logger.warning(
            f"[{request_id}] Persona overlay missing: requested={persona} "
            f"fell_back_to=None session_id={session_id}"
        )
        return None


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


# === CLI Harness Streaming ===

# Maximum input chars for CLI harness arguments. Conservative cap below the
# claude binary's internal chunk limit (~200KB+). Prevents "Separator is not
# found, and chunk exceed the limit" crashes on long PM session resumes.
HARNESS_MAX_INPUT_CHARS = 100_000


def _apply_context_budget(message: str, max_chars: int = HARNESS_MAX_INPUT_CHARS) -> str:
    """Trim oldest context from harness input if it exceeds max_chars.

    Preserves everything from the final 'MESSAGE:' marker onward -- the
    steering message must never be truncated. If no MESSAGE: marker exists,
    trims from the start of the string.

    Returns the original string unchanged if within budget.
    """
    if len(message) <= max_chars:
        return message

    # Find the MESSAGE: boundary -- steering message must be preserved in full
    marker = "\nMESSAGE: "
    idx = message.rfind(marker)
    if idx != -1:
        tail = message[idx:]  # "\nMESSAGE: ..." must stay intact
        budget_for_prefix = max_chars - len(tail)
        if budget_for_prefix <= 0:
            # Steering message alone exceeds budget -- pass through unchanged
            # (harness may still fail, but we preserve message fidelity)
            return message
        # Take the end of the prefix (newest context) that fits the budget
        trim_marker = "[CONTEXT TRIMMED — oldest context omitted to fit harness budget]\n"
        available = budget_for_prefix - len(trim_marker)
        if available <= 0:
            return trim_marker + tail
        trimmed_prefix = message[idx - available : idx]
        return trim_marker + trimmed_prefix + tail
    else:
        # No MESSAGE: marker -- trim from start
        trim_marker = "[CONTEXT TRIMMED]\n"
        available = max_chars - len(trim_marker)
        if available <= 0:
            return trim_marker
        return trim_marker + message[len(message) - available :]


# Map harness names to CLI command templates
_HARNESS_COMMANDS: dict[str, list[str]] = {
    "claude-cli": [
        "claude",
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--permission-mode",
        "bypassPermissions",
    ],
    "opencode": ["opencode", "--non-interactive"],
}


_UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Sentinel string used to detect Claude Code's image-dimension error.
#
# Design note: this checks ``result_text`` (stdout, structured result), NOT stderr.
# The stale-UUID fallback intentionally avoids stderr substring gates because stderr
# is unstructured log noise that changes across CLI versions and locales.
# This sentinel is distinct for three reasons:
#   1. It checks result_text (stdout result), not stderr — stdout result strings are
#      stable protocol output produced by Claude Code as the turn's text response.
#   2. It only fires when prior_uuid was set (resume path) — the error is meaningless
#      on first-turn paths and would never appear there.
#   3. The image-dimension error arrives with exit code 0, making the returncode != 0
#      fallback structurally unable to catch it — a separate check is required.
IMAGE_DIMENSION_SENTINEL = "exceeds the dimension limit"


# Thinking-block corruption sentinel (issue #1099, Mode 1).
#
# When extended-thinking + compaction interact pathologically, the Claude CLI
# exits non-zero and its stderr contains the substring ``redacted_thinking``.
# Both the primary harness call AND the stale-UUID fallback fail the same way,
# so today the caller receives an empty ``""`` result text and the session is
# marked ``completed`` with nothing to deliver — the user gets silence.
#
# Detection rule: stderr contains ``THINKING_BLOCK_SENTINEL`` AND the final
# ``returncode != 0``. Both conditions are required; a healthy session exits
# with code 0 and never triggers. The sentinel is matched as a substring
# (``in`` operator), not a regex, to minimize false-positive surface.
#
# The string is taken from the amux "Every way Claude Code crashes" blog post
# and is **not** yet confirmed against Anthropic's published error taxonomy.
# To bound the blast radius during initial deployment:
#   * Every sentinel match emits ``logger.warning("THINKING_BLOCK_SENTINEL matched: ...")``
#     BEFORE raising, giving operators a grep-friendly audit trail.
#   * Operators may disable the check at runtime via
#     ``DISABLE_THINKING_SENTINEL=1`` (or any truthy value) without a code
#     rollback. When disabled, corruption falls through to the existing empty-
#     string behavior (still suboptimal, but no worse than today).
THINKING_BLOCK_SENTINEL = "redacted_thinking"

# Env-gated kill-switch for the Mode 1 sentinel check. Read once at module
# load; operators must restart the process to toggle. See docstring above.
_DISABLE_THINKING_SENTINEL = os.environ.get("DISABLE_THINKING_SENTINEL", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


class HarnessThinkingBlockCorruptionError(Exception):
    """Raised by ``get_response_via_harness`` when the harness subprocess exits
    non-zero AND its stderr contains ``THINKING_BLOCK_SENTINEL``.

    Indicates the extended-thinking + compaction interaction has corrupted the
    session's transcript beyond in-process recovery. The caller is expected to
    catch this and finalize the session as ``failed`` with the exception's
    message as the user-visible reason. See issue #1099 Mode 1 for the full
    rationale and the ``DISABLE_THINKING_SENTINEL`` escape hatch.
    """


async def get_response_via_harness(
    message: str,
    working_dir: str,
    harness_cmd: list[str] | None = None,
    env: dict[str, str] | None = None,
    *,
    prior_uuid: str | None = None,
    session_id: str | None = None,
    full_context_message: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    on_sdk_started: Callable[[int], None] | None = None,
    on_stdout_event: Callable[[], None] | None = None,
) -> str:
    """Run a CLI harness (e.g. claude -p) and return the final result text.

    Parses stdout as stream-json line-by-line. Extracts the final result from
    the ``result`` event, or falls back to accumulated ``content_block_delta``
    text if no result event fires. No streaming callback is used — intermediate
    chunks are accumulated internally and never delivered mid-session.

    When ``prior_uuid`` is provided and valid, injects ``--resume <uuid>`` into
    the subprocess argv. ``_apply_context_budget()`` is applied **unconditionally**
    to the final argv message, regardless of ``--resume`` state. On the typical
    resume path the message is small and the function is a no-op; on first turns
    and pathological large single messages it bounds the argv to prevent the
    binary's "Separator is found" overflow crash.

    If the resumed subprocess exits with **any** non-zero return code, retries
    once using ``full_context_message`` without ``--resume`` (no stderr substring
    gate — substring matching is brittle across CLI versions and locales).

    A separate exit-code-0 sentinel check (``IMAGE_DIMENSION_SENTINEL``) is placed
    **above** the ``returncode != 0`` guard to handle Claude Code's image-dimension
    error, which returns exit code 0.  This check fires only on resume paths and
    inspects ``result_text`` (stdout), not stderr.  See ``IMAGE_DIMENSION_SENTINEL``
    for the full design rationale.

    When ``session_id`` is provided, stores the captured Claude Code UUID on
    the AgentSession record after a successful turn (Popoto/Redis side effect).
    Tests that pass ``session_id`` must mock ``_store_claude_session_uuid``.

    Args:
        message: The prompt to send to the CLI.
        working_dir: Working directory for the subprocess.
        harness_cmd: Override CLI command (default: claude -p stream-json).
        env: Extra environment variables for the subprocess.
        prior_uuid: Claude Code session UUID from a prior turn (enables --resume).
        session_id: Bridge/Telegram session ID for UUID storage after the turn.
        full_context_message: Full-context first-turn message for stale-UUID fallback.
        model: Short alias (``opus``/``sonnet``/``haiku``) or full Claude model
            name to pin this turn to. When truthy, ``--model <value>`` is
            injected into ``harness_cmd`` so the Claude CLI honors the choice.
            When None/empty, the CLI uses its own default. Part of the per-
            session model routing cascade (see
            ``agent/session_executor.py::_resolve_session_model``).
        system_prompt: Optional persona/role text appended to Claude Code's
            default system prompt via ``--append-system-prompt`` (issue #1148).
            Use ``--append`` (not ``--system-prompt``) to preserve the default
            tool-handling protocol — the persona is additive guidance. PM
            sessions pass ``load_pm_system_prompt(working_dir)`` here; drafter
            and dev sessions must keep this ``None``. Strings larger than
            512KB are dropped with a warning to avoid ARG_MAX overflows.
    """
    # Validate prior_uuid format; treat empty or invalid as None
    if prior_uuid and not _UUID_PATTERN.match(prior_uuid):
        logger.warning(f"[harness] Invalid prior_uuid format, ignoring: {prior_uuid!r}")
        prior_uuid = None
    if not prior_uuid:
        prior_uuid = None

    if harness_cmd is None:
        harness_cmd = list(_HARNESS_COMMANDS["claude-cli"])
    else:
        # Defensive copy — callers may hand us a shared constant (e.g. test
        # fixtures sharing a module-level list). We must not mutate their
        # list when appending --model below.
        harness_cmd = list(harness_cmd)

    # Inject per-session model when caller supplied one. --model must live
    # inside harness_cmd so it precedes the positional message (and any
    # --resume <uuid>) in the final argv assembly below.
    if model:
        harness_cmd.extend(["--model", model])
        logger.info(f"[harness] Using --model {model} for session_id={session_id}")

    # System prompt injection (issue #1148). Use --append-system-prompt
    # (NOT --system-prompt) so Claude Code's default tool-handling protocol is
    # preserved — the PM persona is additive guidance, not a full replacement.
    # Defensive size cap: macOS ARG_MAX is 1MB; we cap at 512KB to leave room
    # for the rest of the argv. The current PM persona is ~25KB.
    if system_prompt:
        if len(system_prompt) > 512_000:
            logger.warning(
                f"[harness] system_prompt is {len(system_prompt)} bytes; "
                "exceeds 512KB soft cap, omitting to avoid ARG_MAX (session_id="
                f"{session_id})"
            )
        else:
            harness_cmd.extend(["--append-system-prompt", system_prompt])
            logger.info(
                f"[harness] Appending {len(system_prompt)}-char system prompt for "
                f"session_id={session_id}"
            )

    # Build subprocess env: inherit current env, merge extras, strip API key
    proc_env = dict(os.environ)
    proc_env.pop("ANTHROPIC_API_KEY", None)
    if env:
        proc_env.update(env)
        proc_env.pop("ANTHROPIC_API_KEY", None)

    # Apply context budget unconditionally. On resumed turns with a typical
    # small message this is a no-op (one length comparison). On first turns
    # and on pathological large single messages (pasted transcripts, forwarded
    # logs) it bounds the argv to prevent the binary's chunk-limit crash.
    original_len = len(message)
    message = _apply_context_budget(message)
    if len(message) < original_len:
        logger.info(
            f"[harness] Context budget applied: trimmed {original_len} → {len(message)} chars"
        )

    if prior_uuid:
        logger.info(f"[harness] Resuming Claude session {prior_uuid} for session_id={session_id}")
        cmd = harness_cmd + ["--resume", prior_uuid, message]
    else:
        cmd = harness_cmd + [message]

    # Call site 1 of 3 — primary harness invocation. 6-tuple unpack (issue #1099 Mode 1).
    (
        result_text,
        session_id_from_harness,
        returncode,
        usage,
        cost_usd,
        stderr_snippet,
    ) = await _run_harness_subprocess(
        cmd,
        working_dir,
        proc_env,
        on_sdk_started=on_sdk_started,
        on_stdout_event=on_stdout_event,
    )

    # Image-dimension sentinel: Claude Code returns the image-dimension error with
    # exit code 0, so the returncode != 0 fallback below cannot catch it.  This
    # check fires only on resume paths (prior_uuid set) and inspects result_text
    # (stdout result), not stderr.  See IMAGE_DIMENSION_SENTINEL for rationale.
    if prior_uuid and result_text and IMAGE_DIMENSION_SENTINEL in result_text:
        logger.warning(
            f"[harness] Image dimension error on --resume for session_id={session_id}; "
            "triggering full_context_message fallback"
        )
        if full_context_message is not None:
            fallback_msg = _apply_context_budget(full_context_message)
            fallback_cmd = harness_cmd + [fallback_msg]
            # Call site 2 of 3 — image-dimension fallback. 6-tuple unpack (issue #1099 Mode 1).
            (
                result_text,
                session_id_from_harness,
                _,
                usage,
                cost_usd,
                stderr_snippet,
            ) = await _run_harness_subprocess(
                fallback_cmd,
                working_dir,
                proc_env,
                on_sdk_started=on_sdk_started,
                on_stdout_event=on_stdout_event,
            )
        else:
            logger.error(
                f"[harness] Image dimension error on --resume for session_id={session_id}, "
                "no full_context_message available — returning plain-language error"
            )
            result_text = (
                "I couldn't resume because the session history contains images that are "
                "too large. Please start a new thread."
            )

    # Mandatory stale-UUID fallback: when prior_uuid was set and the subprocess
    # exits with ANY non-zero return code, retry once without --resume using the
    # full-context message. The fallback does NOT inspect stderr — substring
    # matching is brittle across CLI versions and locales, and an unnecessary
    # retry on a non-stale-UUID error costs only one extra subprocess spawn.
    if prior_uuid and returncode is not None and returncode != 0:
        if full_context_message is not None:
            logger.warning(
                f"[harness] Stale UUID {prior_uuid} for session_id={session_id}, "
                "falling back to first-turn path"
            )
            original_len = len(full_context_message)
            fallback_msg = _apply_context_budget(full_context_message)
            if len(fallback_msg) < original_len:
                logger.info(
                    f"[harness] Fallback budget: {original_len} → {len(fallback_msg)} chars"
                )
            fallback_cmd = harness_cmd + [fallback_msg]
            # Call site 3 of 3 — stale-UUID fallback. 6-tuple unpack (issue #1099 Mode 1).
            # We now DO capture the final returncode + stderr_snippet because the
            # Mode 1 sentinel check below inspects the LAST subprocess call's
            # exit state (both primary and fallback must fail to declare
            # thinking-block corruption).
            (
                result_text,
                session_id_from_harness,
                returncode,
                usage,
                cost_usd,
                stderr_snippet,
            ) = await _run_harness_subprocess(
                fallback_cmd,
                working_dir,
                proc_env,
                on_sdk_started=on_sdk_started,
                on_stdout_event=on_stdout_event,
            )
        else:
            logger.error(
                f"[harness] Stale UUID {prior_uuid} for session_id={session_id}, "
                "falling back to first-turn path — no full_context_message available"
            )
            result_text = None

    # Store the Claude Code UUID for next-turn --resume (#976)
    if session_id and session_id_from_harness:
        _store_claude_session_uuid(session_id, session_id_from_harness)

    # Accumulate tokens + cost on the AgentSession (issue #1128). Mirrors
    # the SDK path's in-handler call in `get_response_via_sdk`. Invoked
    # here as a side effect so the public signature stays `-> str` and
    # no caller of `get_response_via_harness` has to change. `usage` /
    # `cost_usd` may be None on harness error paths or older CLI
    # versions — the helper treats missing fields as 0.
    if session_id and (usage is not None or cost_usd is not None):
        accumulate_session_tokens(
            session_id,
            _usage_field(usage, "input_tokens"),
            _usage_field(usage, "output_tokens"),
            _usage_field(usage, "cache_read_input_tokens"),
            cost_usd,
        )

    # Mode 2 (issue #1099) — emit a single WARNING if per-turn context usage
    # crosses 75%. Pure observability; no state change, no behavior change.
    _log_context_usage_if_risky(session_id, model, usage)

    # Mode 4 (issue #1099) — persist the last subprocess exit code best-effort
    # so the health-check recovery branch can distinguish OS-initiated OOM
    # kills from health-check-initiated kills. Fail-quiet.
    _store_exit_returncode(session_id, returncode)

    # Mode 1 (issue #1099) — thinking-block corruption sentinel. Fires only
    # when BOTH (a) the final subprocess exited non-zero AND (b) its stderr
    # contains ``THINKING_BLOCK_SENTINEL``. A healthy session exits 0 and
    # never triggers. Disable at runtime with ``DISABLE_THINKING_SENTINEL=1``.
    if (
        not _DISABLE_THINKING_SENTINEL
        and returncode is not None
        and returncode != 0
        and stderr_snippet
        and THINKING_BLOCK_SENTINEL in stderr_snippet
    ):
        # Always log BEFORE raising so operators can grep for false positives
        # during initial deployment. The WARNING is grep-friendly by design.
        logger.warning(
            "[harness] THINKING_BLOCK_SENTINEL matched: session_id=%s returncode=%d "
            "stderr_prefix=%r",
            session_id,
            returncode,
            stderr_snippet[:200],
        )
        raise HarnessThinkingBlockCorruptionError(
            "Session context corrupted — please start a new thread"
        )

    if result_text is not None:
        return result_text
    return ""


async def _run_harness_subprocess(
    cmd: list[str],
    working_dir: str,
    proc_env: dict[str, str],
    *,
    on_sdk_started: Callable[[int], None] | None = None,
    on_stdout_event: Callable[[], None] | None = None,
) -> tuple[str | None, str | None, int | None, dict | None, float | None, str | None]:
    """Execute a harness subprocess and parse stream-json output.

    Returns ``(result_text, session_id_from_harness, returncode, usage, cost_usd, stderr_snippet)``.

    * ``result_text``: parsed result string from the final `result` event, or
      accumulated text from stream events when no result event fires, or
      ``None`` when neither is available.
    * ``session_id_from_harness``: Claude Code UUID for next-turn `--resume`.
    * ``returncode``: process exit code (0 on success, non-zero on failure, or
      ``None`` on binary-not-found).
    * ``usage``: dict from the `result` event's `usage` field (keys include
      ``input_tokens``, ``output_tokens``, ``cache_read_input_tokens``,
      ``cache_creation_input_tokens``). ``None`` when no `result` event fired
      or the event omitted it. Consumed by ``accumulate_session_tokens`` in
      ``get_response_via_harness`` — this is the harness-side half of the
      two-path token tracker introduced for issue #1128.
    * ``cost_usd``: raw ``total_cost_usd`` from the `result` event, taken
      verbatim and never recomputed locally so the value tracks upstream
      Anthropic pricing automatically.
    * ``stderr_snippet``: first 2000 chars of decoded stderr when
      ``returncode != 0``; ``None`` otherwise. Issue #1099 Mode 1 uses this
      for sentinel-based thinking-block corruption detection. Truncation
      bounds memory usage; sentinel matches reliably fall within this window
      per the amux report.

    On binary-not-found, returncode is None and result_text carries the
    error message (usage, cost_usd, stderr_snippet are all None).

    Optional callbacks (issue #1036):
        on_sdk_started(pid): fires once, immediately after the subprocess is
            spawned with a valid pid. Callback exceptions are caught + logged.
        on_stdout_event(): fires on each non-empty stdout line from the SDK.
            Callback exceptions are caught + logged.
    """
    # Default asyncio StreamReader limit is 64KB. The claude CLI outputs its
    # full result as a single JSON line — long responses (e.g. multi-cycle
    # analyses) can exceed that, raising LimitExceededError: "Separator is
    # found, but chunk is longer than limit". Set limit to 16MB to cover any
    # realistic Claude response.
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            env=proc_env,
            limit=16 * 1024 * 1024,  # 16 MB — covers any realistic Claude response
        )
    except FileNotFoundError as e:
        logger.error(f"Harness binary not found: {e}")
        return (f"Error: CLI harness not found — {e}", None, None, None, None, None)

    # Fire SDK-started callback once the pid is known (#1036).
    if on_sdk_started is not None and proc.pid is not None:
        try:
            on_sdk_started(proc.pid)
        except Exception as _cb_err:
            logger.warning("on_sdk_started callback raised: %s", _cb_err)

    full_text = ""
    result_text = None
    session_id_from_harness = None
    # Token + cost fields extracted off the `result` event (issue #1128).
    # Mirrors the SDK path's `ResultMessage.usage` / `.total_cost_usd`
    # so `accumulate_session_tokens` can be fed from either path.
    usage: dict | None = None
    cost_usd: float | None = None

    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        # Fire stdout-event callback for liveness tracking (#1036). Do NOT
        # block the harness loop if the callback raises.
        if on_stdout_event is not None:
            try:
                on_stdout_event()
            except Exception as _cb_err:
                logger.warning("on_stdout_event callback raised: %s", _cb_err)

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.debug(f"Harness: skipping malformed JSON line: {line[:120]}")
            continue

        event_type = data.get("type")

        if event_type == "result":
            result_text = data.get("result", "")
            session_id_from_harness = data.get("session_id")
            # Pillar A turn boundary (issue #1172). Bumps last_turn_at on
            # the in-flight AgentSession so the dashboard can show how
            # recently the SDK completed a turn. Best-effort, never raises.
            try:
                from agent.hooks.liveness_writers import record_turn_boundary

                record_turn_boundary()
            except Exception as _liveness_err:
                logger.debug("liveness turn-boundary write failed: %s", _liveness_err)
            # Extract per-turn token + cost counts (issue #1128). These
            # are the harness-side counterpart of `ResultMessage.usage`
            # and `ResultMessage.total_cost_usd` from the SDK path. The
            # `claude -p stream-json` protocol emits them on the same
            # `result` event. `usage` is a dict; missing fields default
            # to 0 inside `accumulate_session_tokens`. `total_cost_usd`
            # is taken verbatim so it tracks upstream Anthropic pricing
            # without a local price table.
            raw_usage = data.get("usage")
            if isinstance(raw_usage, dict):
                usage = raw_usage
            raw_cost = data.get("total_cost_usd")
            if isinstance(raw_cost, (int, float)):
                cost_usd = float(raw_cost)
            if session_id_from_harness:
                logger.debug(f"Harness session_id for resume: {session_id_from_harness}")
            break

        if event_type == "stream_event":
            event = data.get("event", {})
            if event.get("type") == "content_block_start":
                full_text = ""
            elif event.get("type") == "content_block_delta":
                delta = event.get("delta", {}) or {}
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    chunk = delta.get("text", "")
                    if chunk:
                        full_text += chunk
                elif delta_type == "thinking_delta":
                    # Pillar A (issue #1172): bubble extended-thinking content
                    # to the dashboard so operators can see what the agent is
                    # mulling. Best-effort; throttled in liveness_writers.
                    chunk = delta.get("thinking", "") or delta.get("text", "")
                    if chunk:
                        try:
                            from agent.hooks.liveness_writers import (
                                record_thinking_excerpt,
                            )

                            record_thinking_excerpt(chunk)
                        except Exception as _liveness_err:
                            logger.debug("liveness thinking-delta write failed: %s", _liveness_err)

    _, stderr_data = await proc.communicate()
    returncode = proc.returncode if proc.returncode is not None else 0
    # Capture first 2000 chars of stderr for Mode 1 sentinel checks (issue #1099).
    # Bound the snippet at 2000 chars: ~4x the 500-char log-only window already
    # used below, enough for sentinel matching while keeping memory tight.
    stderr_snippet: str | None = None
    if returncode != 0:
        stderr_text = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
        stderr_snippet = stderr_text[:2000]
        logger.warning(f"Harness exited with code {returncode}: {stderr_text[:500]}")

    if result_text is not None:
        return (result_text, session_id_from_harness, returncode, usage, cost_usd, stderr_snippet)
    if full_text:
        logger.warning(
            "Harness exited without result event, returning %d chars of accumulated text",
            len(full_text),
        )
        return (full_text, session_id_from_harness, returncode, usage, cost_usd, stderr_snippet)
    logger.error("Harness exited without a result event and no accumulated text")
    return (None, session_id_from_harness, returncode, usage, cost_usd, stderr_snippet)


async def verify_harness_health(harness_name: str) -> bool:
    """Check if a CLI harness is available and working.

    For claude-cli: verifies the binary exists on PATH and can produce
    a system init event. Checks apiKeySource for billing warnings.

    Returns True if healthy, False otherwise.
    """
    if harness_name not in _HARNESS_COMMANDS:
        logger.warning(f"Unknown harness: {harness_name}")
        return False

    cmd_template = _HARNESS_COMMANDS[harness_name]
    binary = cmd_template[0]

    if not shutil.which(binary):
        logger.warning(f"Harness binary not found on PATH: {binary}")
        return False

    try:
        # Run a minimal test command — we only need the system init event
        # (emitted before any API call), so kill the process immediately
        # after receiving it to avoid a full API round-trip.
        test_cmd = cmd_template + ["test"]
        proc = await asyncio.create_subprocess_exec(
            *test_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Read stdout line-by-line, kill as soon as we see the system event
        healthy = False
        assert proc.stdout is not None
        while True:
            try:
                raw_line = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
            except TimeoutError:
                logger.warning(f"Harness {harness_name} timed out waiting for system init event")
                break
            if not raw_line:
                break  # EOF
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "system":
                api_source = data.get("apiKeySource", "unknown")
                if api_source not in ("none", ""):
                    logger.warning(
                        f"Harness {harness_name} using API key billing (apiKeySource={api_source})"
                    )
                logger.info(
                    f"Harness {harness_name} health check passed (apiKeySource={api_source})"
                )
                healthy = True
                break

        # Terminate immediately — no need to wait for the full API response
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # Already exited
        await proc.wait()

        if not healthy:
            logger.warning(f"Harness {harness_name} did not produce system init event")
        return healthy

    except Exception as e:
        logger.error(f"Harness health check failed for {harness_name}: {e}")
        return False


async def build_harness_turn_input(
    message: str,
    session_id: str,
    sender_name: str | None,
    chat_title: str | None,
    project: dict | None,
    task_list_id: str | None,
    session_type: str | None,
    sender_id: int | None,
    classification: str | None = None,
    is_cross_repo: bool = False,
    *,
    skip_prefix: bool = False,
) -> str:
    """Build context-enriched message for CLI harness execution.

    Extracts the message enrichment logic that was previously inside
    get_agent_response_sdk() into a standalone function. Produces a
    context-prefixed message with PROJECT, FROM, SESSION_ID, TASK_SCOPE,
    and SCOPE headers suitable for any session type.

    When ``skip_prefix`` is True, returns the raw ``message`` unchanged.
    Used on resumed turns where the CLI binary already has prior context
    from its session file (#976).

    Args:
        message: Raw message text (already media-enriched by process_session).
        session_id: Session ID for conversation continuity.
        sender_name: Name of the sender (omitted from output if None).
        chat_title: Chat title for logging context.
        project: Project configuration dict from projects.json.
        task_list_id: Optional task list ID for sub-agent scoping.
        session_type: Session type (dev, pm, teammate).
        sender_id: Telegram user ID for permission checking.
        classification: Classification type from bridge (e.g., "sdlc", "question").
        is_cross_repo: Whether this is a cross-repo project (project_key != "valor").
        skip_prefix: If True, return raw message without context headers.

    Returns:
        Enriched message string with context headers prepended, or raw message
        if skip_prefix is True.
    """
    if skip_prefix:
        return message
    from bridge.context import build_context_prefix

    enriched = build_context_prefix(project, session_type, sender_id)

    if sender_name:
        enriched += f"\n\nFROM: {sender_name}"
        if chat_title:
            enriched += f" in {chat_title}"
    elif chat_title:
        enriched += f"\n\nin {chat_title}"

    if session_id:
        enriched += f"\nSESSION_ID: {session_id}"
    if task_list_id:
        enriched += f"\nTASK_SCOPE: {task_list_id}"

    enriched += (
        "\nSCOPE: This session is scoped to the message below from this sender. "
        "When reporting completion or summarizing work, only reference tasks and "
        "work initiated in this specific session. Do not include work, PRs, or "
        "requests from other sessions, other senders, or prior conversation threads."
    )

    # Cross-repo SDLC: inject target repo context
    project_mode = project.get("mode", "dev") if project else "dev"
    if project_mode != "pm" and classification == ClassificationType.SDLC and is_cross_repo:
        project_name = project.get("name", "Unknown") if project else "Unknown"
        project_working_dir = project.get("working_directory", "") if project else ""
        github_config = project.get("github", {}) if project else {}
        github_org = github_config.get("org", "")
        github_repo = github_config.get("repo", "")
        enriched += (
            f"\nWORK REQUEST for project {project_name}.\nTARGET REPO: {project_working_dir}"
        )
        if github_org and github_repo:
            enriched += f"\nGITHUB: {github_org}/{github_repo}"

    enriched += f"\nMESSAGE: {message}"
    return enriched


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
    bridge.routing) to determine session behavior for PM sessions:

    - Teammate persona: bypasses the Haiku intent classifier, sets
      session_type=SessionType.TEAMMATE directly on the session, reducing
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
    project_key = project.get("_key", "valor") if project else "valor"
    project_working_dir = project.get("working_directory") if project else None
    if not project_working_dir:
        project_working_dir = AI_REPO_ROOT
    is_cross_repo = project_key != "valor"

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

        if classification == ClassificationType.SDLC and is_cross_repo:
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

    # Resolve session_type FIRST — it determines permission restrictions injected below.
    # PM session (session_type="pm") gets full pipeline instructions.
    # PM sessions orchestrate via dev-session subagent
    _session_type = None
    _session_model = None
    _session_extra_context: dict = {}
    if session_id:
        try:
            from models.agent_session import AgentSession as _AgentSession

            _sessions = list(_AgentSession.query.filter(session_id=session_id))
            if _sessions:
                _session_type = getattr(_sessions[0], "session_type", None)
                _session_model = getattr(_sessions[0], "model", None)
                _session_extra_context = getattr(_sessions[0], "extra_context", None) or {}
        except Exception:
            pass

    # Build context-enriched message (includes user permission restrictions)
    from bridge.context import build_context_prefix

    context = build_context_prefix(project, _session_type, sender_id)
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

    # Cross-repo SDLC: inject target repo context
    if project_mode != "pm" and classification == ClassificationType.SDLC and is_cross_repo:
        github_config = project.get("github", {}) if project else {}
        github_org = github_config.get("org", "")
        github_repo = github_config.get("repo", "")
        enriched_message += (
            f"\nWORK REQUEST for project {project_name}.\nTARGET REPO: {project_working_dir}"
        )
        if github_org and github_repo:
            enriched_message += f"\nGITHUB: {github_org}/{github_repo}"

    # PM/Teammate routing: classify intent and choose Teammate or PM dispatch path.
    # Teammate mode answers informational queries directly without spawning a Dev session.
    _teammate_mode = False
    _collaboration_mode = False
    _classification_context = ""  # Advisory routing context for the agent
    if _session_type in (SessionType.PM, SessionType.TEAMMATE):
        # Config-driven persona bypass: skip classifier when persona is already known
        from bridge.routing import resolve_persona as _resolve_persona_mode

        _config_persona = _resolve_persona_mode(project, chat_title, is_dm=(chat_title is None))

        if _config_persona == PersonaType.TEAMMATE:
            # DMs and Teammate-persona groups: skip classifier, go straight to Teammate
            _teammate_mode = True
            _classification_context = "teammate (config-driven, direct message or teammate group)"
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
            # Update session type to Teammate (skip PM sessions — they must stay PM)
            if session_id and _session_type != SessionType.PM:
                try:
                    from models.agent_session import AgentSession as _TMSession

                    for _s in _TMSession.query.filter(session_id=session_id):
                        if _s.status in ("running", "active", "pending"):
                            _s.session_type = SessionType.TEAMMATE
                            _s.save()
                            break
                except Exception:
                    pass  # Best-effort
        elif _config_persona in (PersonaType.PROJECT_MANAGER, PersonaType.DEVELOPER):
            # PM/Dev persona groups: skip intent classifier, but check bridge-level
            # classification for collaboration/other to avoid unnecessary SDLC overhead
            if classification in (ClassificationType.COLLABORATION, ClassificationType.OTHER):
                _collaboration_mode = True
                _classification_context = (
                    f"{_config_persona} (config-driven, collaboration via bridge classifier)"
                )
                logger.info(
                    f"[{request_id}] Config-driven {_config_persona} mode, "
                    f"collaboration detected (bridge={classification})"
                )
            else:
                _classification_context = f"{_config_persona} (config-driven)"
                logger.info(
                    f"[{request_id}] Config-driven {_config_persona} mode, skipping classifier"
                )
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

                _classification_context = (
                    f"{_intent_result.intent} "
                    f"(classifier confidence={_intent_result.confidence:.0%})"
                )
                if _intent_result.is_teammate:
                    _teammate_mode = True
                    logger.info(
                        f"[{request_id}] Haiku reclassified PM→teammate: session_id={session_id}"
                    )
                elif _intent_result.is_direct_action:
                    _collaboration_mode = True
                    logger.info(
                        f"[{request_id}] Routing to collaboration mode "
                        f"(direct action, intent={_intent_result.intent})"
                    )
                    # Update session type to Teammate so nudge loop uses reduced cap
                    # (skip PM sessions — they must stay PM)
                    if session_id and _session_type != SessionType.PM:
                        try:
                            from models.agent_session import AgentSession as _TMSession

                            for _s in _TMSession.query.filter(session_id=session_id):
                                if _s.status in ("running", "active", "pending"):
                                    _s.session_type = SessionType.TEAMMATE
                                    _s.save()
                                    break
                        except Exception:
                            pass  # Best-effort
            except Exception as e:
                logger.warning(
                    f"[{request_id}] Intent classification failed, defaulting to PM dispatch: {e}"
                )

        # PM sessions must never be forced into Teammate mode by DM origin signal.
        # session_type is the authoritative permission signal, not chat_title.
        if _session_type == SessionType.PM:
            _teammate_mode = False

        # Inject classification context as advisory information
        if _classification_context:
            enriched_message += (
                f"\n\n[Routing context: classified as {_classification_context}. "
                f"This is an initial guess — use your judgment.]"
            )

        if _teammate_mode:
            # Teammate mode: inject Teammate instructions instead of PM dispatch
            from agent.teammate_handler import build_teammate_instructions

            enriched_message += build_teammate_instructions()
        elif _collaboration_mode:
            # Collaboration mode: PM handles the task directly without a dev-session
            enriched_message += (
                "\n\nHandle this task directly using your available tools. "
                "You have access to Bash, file operations, GitHub CLI (gh), "
                "Google Workspace (gws), memory search "
                "(python -m tools.memory_search), Office CLI (officecli), "
                "and session management (python -m tools.valor_session). "
                "No dev-session needed unless you determine the task requires "
                "code changes to the repository.\n\n"
                "If you determine this task actually requires code changes, "
                "create a dev session via `python -m tools.valor_session create "
                '--role dev --parent "$AGENT_SESSION_ID" --message "..."` instead.\n\n'
                "**Communicating with the stakeholder:**\n"
                "You can send Telegram messages directly using:\n"
                '  `python tools/send_telegram.py "Your message here"`\n'
                "To attach a file (screenshot, document, image):\n"
                '  `python tools/send_telegram.py "Caption text" --file /path/to/file.png`\n'
                "Multiple files as an album (max 10):\n"
                "  `python tools/send_telegram.py"
                ' "Album caption" --file a.png --file b.png --file c.png`\n'
                "File-only (no caption):\n"
                "  `python tools/send_telegram.py --file /path/to/file.png`\n"
                "Use --file to attach screenshots, images, or documents. "
                "Repeat --file for albums. Telethon auto-detects the media type.\n"
                "Use this tool ONLY for:\n"
                "- Questions that require human input or a decision\n"
                "- Final delivery summary when the task is complete\n"
                "- Sharing screenshots or files as deliverables\n"
                "DO NOT narrate your process or send step-by-step progress updates. "
                "Do not send intermediate status messages like 'I'm working on X' or "
                "'Now running Y'. Work silently and communicate only at decision points "
                "or completion.\n"
                "Write in business terms — never expose SDLC stage names, "
                "pipeline internals, or implementation details. "
                "Speak like a project manager updating a stakeholder.\n"
                "If you don't call this tool, your return text will be "
                "automatically drafted and sent (fallback behavior)."
            )
        else:
            # PM dispatch: orchestrate SDLC work stage-by-stage.
            # Consult TaskTypeProfile (TRM) to determine delegation style.
            _pm_project_key = project.get("_key", "valor") if project else "unknown"
            _trm_task_type = _infer_task_type_from_message(message, classification)
            _delegation = "structured"  # safe default
            try:
                from models.task_type_profile import get_delegation_recommendation

                _delegation = get_delegation_recommendation(_pm_project_key, _trm_task_type)
            except Exception:
                pass  # Always fall back to structured

            # MULTI-ISSUE FAN-OUT applies to all delegation paths.
            enriched_message += (
                "\n\nMULTI-ISSUE FAN-OUT: If the message contains more than one GitHub "
                "issue number (e.g., 'Run SDLC on issues 777, 775, 776'), you MUST fan out. "
                "For each issue number N, run:\n"
                "  python -m tools.valor_session create \\\\\n"
                "    --role pm \\\\\n"
                '    --parent "$AGENT_SESSION_ID" \\\\\n'
                '    --message "Run SDLC on issue N"\n'
                "After spawning ALL children, run:\n"
                "  python -m tools.valor_session wait-for-children"
                ' --session-id "$AGENT_SESSION_ID"\n'
                "to pause this session. Do NOT process multiple issues in a single session. "
                "Spawn child sessions sequentially (one create call at a time) then call "
                "wait-for-children once. Send a Telegram update before pausing so Valor "
                "knows fan-out happened.\n\n"
            )

            if _delegation == "autonomous":
                # Autonomous handoff: proven task type — objective + constraints only.
                # Skip step-by-step SDLC scaffolding; trust the dev session.
                enriched_message += (
                    "You are the PM. This task type is well-proven — delegate efficiently.\n"
                    "Spawn a dev session with the objective and constraints. "
                    "No step-by-step scaffolding needed.\n\n"
                    "  python -m tools.valor_session create \\\\\n"
                    "    --role dev \\\\\n"
                    '    --parent "$AGENT_SESSION_ID" \\\\\n'
                    '    --message "Objective: <what needs to be done>\\n'
                    "Constraints: <key requirements or acceptance criteria>\\n"
                    'Context: <any relevant state>"\n\n'
                    "After spawning, wait for the steering message"
                    " when the dev session finishes.\n\n"
                    "**Communicating with the stakeholder:**\n"
                    "You can send Telegram messages directly using:\n"
                    '  `python tools/send_telegram.py "Your message here"`\n'
                    "Use this tool ONLY for questions requiring human input, "
                    "the final delivery summary, or sharing files. "
                    "Do NOT send intermediate progress narration or step-by-step updates. "
                    "Work silently; communicate only at decision points or completion.\n"
                    "Write in business terms — never expose SDLC stage names, "
                    "pipeline internals, or implementation details. "
                    "Speak like a project manager updating a stakeholder.\n"
                    "If you don't call this tool, your return text will be "
                    "automatically drafted and sent (fallback behavior)."
                )
            else:
                # Structured handoff: novel or error-prone task type — full SDLC guidance.
                enriched_message += (
                    "You are the PM. A good PM reads the situation, decides what stage "
                    "comes next, and gives clear and concise direction to dev sessions — "
                    "manageable blocks of work with crisp acceptance criteria. The dev "
                    "session does the hands-on work: running /do-build, /do-test, "
                    "/do-merge, checking merge gates, running tests. Your job is to "
                    "direct well and judge the output, not to do the work yourself.\n\n"
                    "**Spawn vs. resume:** When a dev session recently finished a stage "
                    "on the same issue, prefer resuming it — it already has the codebase "
                    "loaded, branch checked out, and full context in its transcript. "
                    "Use `python -m tools.valor_session resume --id <id> --message '...'` "
                    "to hand it the next stage. Spawn a fresh dev session when starting "
                    "a new issue, when the prior session's context would be stale or "
                    "misleading, or when parallel work on separate issues is needed.\n\n"
                    "Orchestration loop:\n"
                    "1. **Assess** — read the issue, PR, and prior comments to understand "
                    "where the pipeline stands "
                    "(gh issue view, gh pr view, gh pr list, gh pr checks).\n"
                    "1.5. **Gather prior stage context** — if a tracking issue exists, "
                    "fetch the last few comments with "
                    "`gh api repos/{owner}/{repo}/issues/{number}/comments` and look for "
                    "comments containing `<!-- sdlc-stage-comment -->`. Include a summary "
                    "of prior stage findings in the dev-session message so the next stage "
                    "has full context.\n"
                    "2. **Direct a dev session** — resume the prior one if context is "
                    "still warm, otherwise spawn fresh:\n"
                    "   python -m tools.valor_session create \\\\\n"
                    "     --role dev \\\\\n"
                    '     --parent "$AGENT_SESSION_ID" \\\\\n'
                    '     --message "Stage: <PLAN|CRITIQUE|BUILD|TEST|PATCH|REVIEW|DOCS>\\n'
                    "Issue: <URL>\\nPR: <URL if exists>\\n"
                    "Current state: <what's already done>\\n"
                    'Acceptance criteria: <what done looks like>"\n'
                    "   The worker steers you back when the dev session finishes — wait.\n"
                    "3. **Judge the result** — review what the dev session produced and "
                    "decide whether it clears the bar or needs a patch.\n"
                    "4. **Advance** — move to the next stage or surface a decision to "
                    "the stakeholder if you're genuinely blocked.\n\n"
                    "For trivial or docs-only work, use your judgment on whether full "
                    "pipeline stages are warranted.\n\n"
                    "**Communicating with the stakeholder:**\n"
                    "You can send Telegram messages directly using:\n"
                    '  `python tools/send_telegram.py "Your message here"`\n'
                    "To attach a file (screenshot, document, image):\n"
                    '  `python tools/send_telegram.py "Caption text" --file /path/to/file.png`\n'
                    "Multiple files as an album (max 10):\n"
                    "  `python tools/send_telegram.py"
                    ' "Album caption" --file a.png --file b.png --file c.png`\n'
                    "File-only (no caption):\n"
                    "  `python tools/send_telegram.py --file /path/to/file.png`\n"
                    "Use --file to attach screenshots, images, or documents. "
                    "Repeat --file for albums. Telethon auto-detects the media type.\n"
                    "Use this tool ONLY for:\n"
                    "- Questions that require human input or a decision\n"
                    "- Final delivery summary when the full task is complete\n"
                    "- Sharing screenshots or files as deliverables\n"
                    "DO NOT narrate your process. Do not send step-by-step progress updates "
                    "or intermediate status messages ('working on X', 'now running Y', "
                    "'checking PR #N'). Work silently through the pipeline and communicate "
                    "only when you need a human decision or the work is done.\n"
                    "Write in business terms — never expose SDLC stage names, "
                    "pipeline internals, or implementation details. "
                    "Speak like a project manager updating a stakeholder.\n"
                    "If you don't call this tool, your return text will be "
                    "automatically drafted and sent (fallback behavior)."
                )
    # --- Single-issue scoping (Fix 3) ---
    # Prevent PM from cross-contaminating pipelines by dispatching work for
    # issues other than the one it was assigned. Only relevant for PM sessions.
    if _session_type == SessionType.PM and not _teammate_mode:
        enriched_message += (
            "\n\nSINGLE-ISSUE SCOPING: If the message references a specific issue number "
            "(e.g., 'issue 934', '#934', 'issues/934'), you MUST only assess and advance "
            "that issue. Do NOT query `gh issue list` for other issues. Do NOT dispatch "
            "stages for any issue other than the one specified.\n"
        )

    # --- Wait-for-children after dev dispatch (Fix 2) ---
    # Ensure PM stays alive while dev session runs, so steering messages
    # are received directly rather than requiring a continuation PM.
    if _session_type == SessionType.PM and not _teammate_mode:
        enriched_message += (
            "\nDEV SESSION WAIT RULE: After dispatching ANY dev session via "
            "`python -m tools.valor_session create --role dev`, you MUST:\n"
            "1. Call `python -m tools.valor_session wait-for-children "
            '--session-id "$AGENT_SESSION_ID"`\n'
            "2. Output a brief status message (e.g., 'Dispatched BUILD. Waiting.')\n"
            "3. WAIT for the steering response — do NOT produce a final answer or "
            "closing statement. The worker will steer you when the dev session completes.\n"
            "This keeps your session alive so the dev session result is delivered "
            "directly to you instead of requiring a continuation PM.\n"
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
    # PM session always uses PM persona; Teammate uses Teammate; otherwise resolve from config
    if _session_type == SessionType.PM:
        persona = PersonaType.PROJECT_MANAGER
    elif _session_type == SessionType.TEAMMATE:
        persona = PersonaType.TEAMMATE
        # Email sessions may use a project-specific persona (e.g. customer-service)
        if _session_extra_context.get("transport") == "email" and project:
            _email_persona_str = project.get("email", {}).get("persona", "")
            if _email_persona_str and _email_persona_str != "teammate":
                try:
                    persona = PersonaType(_email_persona_str)
                except ValueError:
                    pass  # Unknown persona value — stay with TEAMMATE
    else:
        persona = _resolve_persona(project, chat_title, is_dm=is_dm)
    logger.info(
        f"[{request_id}] Context: persona={persona}, worker_rules={wr_label}, "
        f"session_id={session_id}"
    )

    try:
        # Extract project_key from config for env var injection
        _project_key = project.get("_key", "valor") if project else None
        # Extract message_id from the session context (passed through _execute_agent_session)
        _message_id = None  # message_id not available at this layer

        logger.info(f"[{request_id}] Resolved persona: {persona}")

        # Build system prompt based on persona and project mode.
        # PM session (session_type="pm") uses PM persona with read-only permissions.
        custom_system_prompt = None
        _permission_mode = "bypassPermissions"  # Default: full permissions

        if _session_type == SessionType.PM:
            # PM session: PM persona with hook-restricted tool access.
            # agent/hooks/pre_tool_use.py enforces: (1) Write/Edit blocked to paths
            # outside docs/, (2) Bash restricted to a read-only allowlist
            # (git status/log/diff/show, gh issue/pr view/list, tail logs/,
            # cat docs/, python -m tools.valor_session status, etc.).
            # Any mutation must be dispatched to a dev-session subagent.
            custom_system_prompt = load_pm_system_prompt(working_dir)
            logger.info(
                f"[{request_id}] Persona overlay loaded: name=project-manager "
                f"prompt_chars={len(custom_system_prompt) if custom_system_prompt else 0} "
                f"session_id={session_id}"
            )
            logger.info(f"[{request_id}] PM session mode: PM persona, bypassPermissions")
        elif project_mode == "pm":
            # PM mode: use PM system prompt (no WORKER_RULES, loads work-vault CLAUDE.md)
            custom_system_prompt = load_pm_system_prompt(working_dir)
            logger.info(
                f"[{request_id}] Persona overlay loaded: name=project-manager "
                f"prompt_chars={len(custom_system_prompt) if custom_system_prompt else 0} "
                f"session_id={session_id}"
            )
        elif persona == PersonaType.CUSTOMER_SERVICE:
            # Customer-service persona: action-oriented, can run tools/skills, no code writes
            custom_system_prompt = _load_persona_overlay_with_log(
                "customer-service",
                request_id=request_id,
                session_id=session_id,
                fallback="teammate",
            )
        elif persona == PersonaType.TEAMMATE:
            # Teammate persona: casual mode, no WORKER_RULES
            custom_system_prompt = _load_persona_overlay_with_log(
                "teammate",
                request_id=request_id,
                session_id=session_id,
            )
        # Developer persona uses default (load_system_prompt via ValorAgent.__init__)

        # Determine gh_repo for cross-repo SDLC requests (issue #375).
        # When classification is "sdlc" and the project targets a non-ai repo,
        # set GH_REPO so all gh commands automatically target the correct repo.
        _gh_repo = None
        is_cross_repo_sdlc = (
            project_mode != "pm" and classification == ClassificationType.SDLC and is_cross_repo
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
            model=_session_model,
        )
        response = await agent.query(enriched_message, session_id=session_id)

        elapsed = time.time() - start_time
        logger.info(f"[{request_id}] SDK responded in {elapsed:.1f}s ({len(response)} chars)")

        # Record response time metric for Teammate observability
        if _session_type in (SessionType.PM, SessionType.TEAMMATE):
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
        # See nudge loop error-classified output bypass.
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
