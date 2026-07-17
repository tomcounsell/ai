"""
Persona/system-prompt composition and SDLC guardrails for Valor's headless
sessions, plus the public re-export surface for the ``claude -p`` CLI
harness (the real subprocess/argv/stream-json implementation lives in
:mod:`agent.session_runner.harness.claude`; see plan #2000 Task 2.2).

This module:
- Loads system prompts via the configurable persona system
  (``load_system_prompt`` / ``load_eng_system_prompt`` / ``compose_system_prompt``)
- Enforces SDLC guardrails (``_check_no_direct_main_push``, WORKER_RULES)
- Re-exports the extracted harness functions (``get_response_via_harness``,
  ``verify_harness_health``, ``build_harness_turn_input``, and friends) so
  existing non-runner callers are unaffected by the extraction

Authentication strategy (subscription-first, applies to the harness
subprocess): the CLI is spawned WITHOUT ``ANTHROPIC_API_KEY`` in its env, so
it falls back to OAuth/subscription auth from `claude login` — using the Max
plan instead of API credits.

    If Anthropic patches this fallback, known alternatives:
    - CLIProxyAPI (github.com/luispater/CLIProxyAPI): HTTP proxy that swaps
      API key headers for OAuth Bearer tokens. Any Anthropic-format client
      can go through it to use subscription auth.
    - Pi Coding Agent (github.com/badlogic/pi-mono): Independent coding agent
      with native `pi /login` subscription auth and --mode rpc for headless
      programmatic control. Fewer built-in tools but subscription-native.
"""

import json
import logging
import os
import re
from pathlib import Path

import psutil

from config.enums import AccessLevel, PersonaType, SessionType
from utils.github_patterns import ISSUE_NUMBER_RE as _ISSUE_NUMBER_RE
from utils.github_patterns import PR_NUMBER_RE as _PR_NUMBER_RE

logger = logging.getLogger(__name__)


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
        except Exception:  # noqa: S110 -- optional observability registration
            pass  # Non-fatal
    return _anthropic_circuit


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
    """Add per-turn token + cost counts to an AgentSession's `total_*` fields.

    Called as a side effect from every execution path so token accounting
    works uniformly for every session type — the headless session-runner
    role turns (PM/Dev/Teammate) and every other harness caller (drafter,
    probes) all accumulate onto the SAME `total_*` scalars. Single write
    path, single set of fields: `claude -p stream-json` emits `usage` +
    `total_cost_usd` on the `result` event; `_run_harness_subprocess`
    extracts them and threads them back to `get_response_via_harness`,
    which calls this helper before returning (mirroring
    `_store_claude_session_uuid`).

    Schema diet (#1927): this used to branch on a `metered` flag to write a
    disjoint set of "metered-leg" fields for session-runner role turns (plan
    #1842), motivated by a PTY transcript-tailer that no longer exists post
    plan #1924/#2000. That tailer is gone, so every caller has always
    written the SAME `total_*` fields for the lifetime of a session — the
    disjointness this parameter existed to preserve was already vestigial.
    The `metered=` parameter and both branches are removed; there is now
    exactly one write path. `get_response_via_harness`'s own `metered`/
    `role` parameters remain (part of the `TurnRequest` harness-adapter
    contract) but are no longer forwarded here — they no longer change
    accounting behavior. The per-turn "metered-leg cost" ledger-metric
    series that the metered branch used to emit ended at this migration;
    there is no `total_*` replacement (deliberate, matches the accepted
    loss of longitudinal comparability).

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
        # telegram_message_id so tools/send_message.py can reply to the
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


class _SafeFormatDict(dict):
    """A dict subclass that preserves missing keys as literal {key} placeholders.

    Used by load_persona_prompt to apply substitutions without raising KeyError
    when the persona file contains brace-delimited tokens not in the provided
    substitutions dict. Unreferenced braces are preserved verbatim.

    Example:
        d = _SafeFormatDict({"customer_id": "cust-42"})
        "{customer_id} and {other_key}".format_map(d)
        # -> "cust-42 and {other_key}"
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def load_persona_prompt(persona: str = "engineer", substitutions: dict | None = None) -> str:
    """Load persona prompt from composable segments + overlay.

    Segments are assembled from config/personas/segments/ per manifest.json,
    with identity fields injected from config/identity.json.
    Overlays are read from ~/Desktop/Valor/personas/{persona}.md (private, iCloud-synced),
    falling back to config/personas/{persona}.md (in-repo, for development).

    Args:
        persona: Persona name — one of "engineer", "teammate",
            "customer-service". Defaults to "engineer".
        substitutions: Optional dict of {placeholder: value} pairs applied to the
            assembled prompt via _SafeFormatDict.format_map. Missing keys are
            preserved as literal {key} placeholders (never raise KeyError).
            Pass None (default) to skip substitution entirely (backward-compatible).

    Returns:
        Combined persona prompt (assembled segments + overlay), with substitutions
        applied if provided.

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
        if persona == "engineer" and "CRITIQUE" not in overlay_content:
            logger.warning(
                f"Engineer persona overlay '{overlay_path}' is missing CRITIQUE gate rules "
                "— pipeline integrity may be compromised"
            )
        # Workflow-announcement guard: the engineer overlay MUST contain the bucket-#3
        # announce-and-pause rule so coding/automation/config requests don't get
        # silently implemented. The substring is the unique opening clause of the
        # required announcement phrase. This guards against overlay drift on
        # bridge machines where the private overlay is iCloud-synced and could
        # fall out of sync with the in-repo template. Mirrors the CRITIQUE check
        # above and PR #802's loader-warning pattern. See issue #1189.
        if (
            persona == "engineer"
            and "Unless you directly instruct me to skip" not in overlay_content
        ):
            logger.warning(
                f"Engineer persona overlay '{overlay_path}' is missing the "
                "workflow-announcement rule — engineer may silently implement "
                "code/config changes without surfacing the SDLC contract."
            )
        if persona == "engineer" and 'subagent_type="dev-session"' in overlay_content:
            logger.warning(
                f"Engineer persona overlay '{overlay_path}' still contains Agent tool dispatch "
                'instructions (subagent_type="dev-session"). '
                "Eng sessions are now created via "
                "`python -m tools.valor_session create --role eng`. "
                "Update ~/Desktop/Valor/personas/engineer.md to remove the Agent tool "
                "dispatch pattern."
            )
        logger.info(f"Loaded persona '{persona}' from {overlay_path}")
        result = f"{base_content}\n\n---\n\n{overlay_content}"
        if substitutions:
            # _SafeFormatDict preserves unreferenced {braces} as literal text.
            result = result.format_map(_SafeFormatDict(substitutions))
        return result

    # Invalid persona name — fall back to engineer with warning
    if persona not in ("engineer", "teammate", "customer-service"):
        logger.warning(f"Unknown persona '{persona}', falling back to engineer persona")
        engineer_path = _resolve_overlay_path("engineer")
        if engineer_path.exists():
            result = f"{base_content}\n\n---\n\n{engineer_path.read_text()}"
            if substitutions:
                result = result.format_map(_SafeFormatDict(substitutions))
            return result

    # Persona overlay missing — fail loudly (no SOUL.md fallback)
    raise FileNotFoundError(
        f"Persona overlay '{persona}' not found at {overlay_path}. "
        "All persona overlays must exist — no fallback available."
    )


def compose_system_prompt(
    persona: PersonaType,
    access_level: AccessLevel,
    channel: str | None = None,
    *,
    project: dict | None = None,
    working_directory: str | None = None,
) -> str:
    """Single composer for agent system prompts — one path for every cell.

    Replaces the hand-coded ``load_system_prompt`` / ``load_eng_system_prompt``
    pickers with a structural assembly keyed on ``(persona, access_level)``.
    Channel is reserved for forward-compat (Open Question 1 in
    ``docs/plans/composed-persona-system.md``) and is not consumed today by
    any cell — channel-specific concerns live in the message drafter
    (``bridge/message_drafter.py:draft_message`` ``medium=`` parameter).

    Composition order (strict additive layering, no redaction):

    1. ``WORKER_RULES`` (only when ``access_level == AccessLevel.WORKER``).
    2. Persona prompt = identity + segments per ``manifest.json`` + persona overlay.
    3. Principal context (only when ``access_level == AccessLevel.WORKER``).
    4. Completion criteria (only when ``access_level == AccessLevel.WORKER``).
    5. Work-vault ``CLAUDE.md`` (only when ``access_level == AccessLevel.WORKER``
       and ``working_directory`` is provided and the file exists at
       ``Path(working_directory) / "CLAUDE.md"``).

    Byte-stability invariant (issue #1227): for the
    ``(ENGINEER, WORKER)`` cell, the bytes returned here are stable across
    consecutive sessions on the same machine in the same ``working_directory``,
    preserving Anthropic's prompt-cache prefix. Asserted by
    ``tests/unit/test_compose_system_prompt.py`` against per-machine fixtures
    in ``tests/fixtures/{hostname}/``.

    Args:
        persona: Which persona overlay to load.
        access_level: Which rails to apply.
        channel: Optional output channel hint. Reserved; no current cell
            reads it (per Question 4). Kept for forward-compat.
        project: Project config dict (currently unused; reserved for future
            project-level overlays).
        working_directory: Optional path to the work-vault project folder
            containing ``CLAUDE.md``. When provided under ``WORKER`` access
            and the file exists, it is appended to the composed prompt.

    Returns:
        Fully assembled system prompt string ready to pass to ``claude -p``
        via ``--append-system-prompt``.

    Raises:
        TypeError: If ``persona`` is not a ``PersonaType`` member or
            ``access_level`` is not an ``AccessLevel`` member.
        FileNotFoundError: If the persona overlay or required identity /
            segment files are missing (re-raised from ``load_persona_prompt``).
    """
    # Argument validation — fail loudly before any IO.
    if not isinstance(persona, PersonaType):
        valid = ", ".join(p.value for p in PersonaType)
        raise TypeError(
            f"compose_system_prompt: persona must be a PersonaType member, "
            f"got {type(persona).__name__}={persona!r}. Valid: {valid}"
        )
    if not isinstance(access_level, AccessLevel):
        valid = ", ".join(a.value for a in AccessLevel)
        raise TypeError(
            f"compose_system_prompt: access_level must be an AccessLevel "
            f"member, got {type(access_level).__name__}={access_level!r}. "
            f"Valid: {valid}"
        )

    # 2. Persona prompt (identity + segments + overlay).
    #    load_persona_prompt() preserves the loader-warning pattern from
    #    sdk_client.py:919–948 (CRITIQUE / workflow-announcement / dev-session).
    persona_prompt = load_persona_prompt(persona.value)

    # 1 + 3 + 4 + 5: WORKER rails layer.
    if access_level == AccessLevel.WORKER:
        criteria = load_completion_criteria()
        criteria_section = f"\n\n---\n\n{criteria}" if criteria else ""

        principal = load_principal_context(condensed=True)
        principal_section = f"\n\n---\n\n## Principal Context\n\n{principal}" if principal else ""

        # Worker rules FIRST — safety rails take precedence over persona.
        prompt = f"{WORKER_RULES}\n\n---\n\n{persona_prompt}{principal_section}{criteria_section}"

        # 5. Append work-vault CLAUDE.md when a working_directory is provided.
        if working_directory is not None:
            project_claude_path = Path(working_directory) / "CLAUDE.md"
            if project_claude_path.exists():
                project_instructions = project_claude_path.read_text()
                logger.info(f"Loaded eng instructions from {project_claude_path}")
                prompt = f"{prompt}\n\n---\n\n{project_instructions}"
            else:
                logger.info(
                    f"No CLAUDE.md found at {project_claude_path}, using worker prompt only"
                )

        return prompt

    # TEAMMATE / CUSTOMER_SERVICE: no rails, no appendices — return persona as-is.
    return persona_prompt


def _resolve_compose_args(
    session_type: SessionType | str | None,
    project: dict | None = None,
    transport: str | None = None,
    chat_title: str | None = None,
    is_dm: bool = False,
    project_mode: str | None = None,
) -> tuple[PersonaType, AccessLevel, str | None]:
    """Single source of truth mapping session context to ``(persona, access_level, channel)``.

    Both ``agent/sdk_client.py:get_response_via_harness`` and
    ``agent/session_executor.py`` call this helper instead of duplicating the
    branch ladder. The email-persona override
    (``project.email.persona`` for ``transport == "email"``) lives **only**
    here.

    Mapping (input → output):

    - ``SessionType.ENG`` → ``(ENGINEER, WORKER, None)``
    - ``SessionType.TEAMMATE`` + ``transport=="email"`` + ``project.email.persona``
      set → ``(<email persona>, <access level matching persona>, "email")``
    - ``SessionType.TEAMMATE`` (default) →
      ``(TEAMMATE, TEAMMATE, None)``
    - Unknown session type → resolved via
      ``_resolve_persona(project, chat_title, is_dm)`` →
      ``(<persona>, <access level matching persona>, None)``

    Persona → access-level mapping (today's 1:1; orthogonality preserved for
    future per-project rails):

    - ``ENGINEER`` → ``WORKER``
    - ``TEAMMATE`` → ``TEAMMATE``
    - ``CUSTOMER_SERVICE`` → ``CUSTOMER_SERVICE``
    """
    # Email override: per-project persona swap for email-spawned sessions.
    if session_type == SessionType.TEAMMATE and transport == "email" and project:
        email_persona_str = (project.get("email") or {}).get("persona") or ""
        if email_persona_str and email_persona_str != "teammate":
            try:
                persona = PersonaType(email_persona_str)
                return persona, _access_level_for_persona(persona), "email"
            except ValueError:
                pass  # Unknown persona value — fall through to default teammate handling.

    if session_type == SessionType.ENG:
        return PersonaType.ENGINEER, AccessLevel.WORKER, None

    # project_mode == "eng" forces engineer rails even for non-ENG session types.
    if project_mode == "eng":
        return PersonaType.ENGINEER, AccessLevel.WORKER, None

    if session_type == SessionType.TEAMMATE:
        return PersonaType.TEAMMATE, AccessLevel.TEAMMATE, None

    # Unknown session type: resolve from project config.
    persona = _resolve_persona(project, chat_title, is_dm=is_dm)
    return persona, _access_level_for_persona(persona), None


def _access_level_for_persona(persona: PersonaType) -> AccessLevel:
    """Default access-level for a persona (today's 1:1 mapping)."""
    if persona == PersonaType.TEAMMATE:
        return AccessLevel.TEAMMATE
    if persona == PersonaType.CUSTOMER_SERVICE:
        return AccessLevel.CUSTOMER_SERVICE
    return AccessLevel.WORKER


def load_system_prompt() -> str:
    """Load engineer system prompt with worker rules and completion criteria.

    Thin wrapper that delegates to ``compose_system_prompt``. Preserved for
    backward compatibility with existing call sites; new code should call
    ``compose_system_prompt`` directly.
    """
    return compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)


def load_eng_system_prompt(working_directory: str) -> str:
    """Load system prompt for engineer mode channels with work-vault context.

    Uses the engineer persona (base + engineer overlay) with WORKER_RULES and
    optionally appends the project-specific CLAUDE.md from the work vault
    directory if it exists.

    System prompt structure:
        [WORKER_RULES]
        ---
        [Persona prompt — base + engineer overlay]
        ---
        [Principal context]
        ---
        [Completion criteria]
        ---
        [Work-vault CLAUDE.md — eng-specific instructions for this project]

    Caching strategy (issue #1227):
        The returned string is passed to ``get_response_via_harness()`` as
        ``system_prompt=``, which injects both ``--exclude-dynamic-system-
        prompt-sections`` and ``--append-system-prompt <text>`` into the
        ``claude -p`` argv.  The former flag removes per-machine dynamic
        sections (cwd, env info, memory paths, git status) from the system
        prompt into the first user message, leaving the prefix byte-for-byte
        stable across consecutive eng sessions on the same machine in the same
        ``working_directory``.  This enables Anthropic's server-side prompt
        cache (5-minute TTL) to serve the prefix on cache hit,
        reducing TTFT from 15–20 min (cold) to < 90 s (warm).

    Invariants:
        - Must include WORKER_RULES (engineer mode has full branch safety rails).
        - Must include the engineer persona overlay.
        - Must NOT silently swallow FileNotFoundError on persona load — caller
          catches and logs ``[eng-persona-missing]`` so failures are visible.

    Args:
        working_directory: Path to the work-vault project folder.

    Returns:
        Combined system prompt for engineer mode.
    """
    return compose_system_prompt(
        PersonaType.ENGINEER,
        AccessLevel.WORKER,
        working_directory=working_directory,
    )


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


def _load_persona_overlay_with_log(
    persona: str,
    request_id: str,
    session_id: str | None,
    fallback: str | None = None,
    substitutions: dict | None = None,
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
        substitutions: Optional dict passed through to load_persona_prompt for
            placeholder substitution (e.g. {"customer_id": "cust-42"}).

    Returns:
        The persona prompt string, or ``None`` if both the requested overlay
        and the fallback are missing.
    """
    try:
        prompt = load_persona_prompt(persona, substitutions=substitutions)
        logger.info(
            f"[{request_id}] Persona overlay loaded: name={persona} "
            f"prompt_chars={len(prompt) if prompt else 0} "
            f"session_id={session_id}"
        )
        return prompt
    except FileNotFoundError:
        if fallback:
            try:
                fallback_prompt = load_persona_prompt(fallback, substitutions=substitutions)
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
    2. Eng mode projects: "engineer"
    3. Group chats: look up persona from project's telegram.groups[chat_title]
    4. Default: "engineer"

    Args:
        project: Project configuration dict from projects.json.
        chat_title: Telegram chat/group title, or None for DMs.
        is_dm: Whether this is a direct message.

    Returns:
        Persona name string (e.g., "engineer", "teammate").
    """
    if not project:
        return PersonaType.TEAMMATE if is_dm else PersonaType.ENGINEER

    telegram_config = project.get("telegram", {})

    # DMs use the dm_persona config
    if is_dm:
        return telegram_config.get("dm_persona", PersonaType.TEAMMATE)

    # Eng mode projects always use engineer persona (only if explicitly configured)
    project_mode = project.get("mode")
    if project_mode == "eng":
        return PersonaType.ENGINEER

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

    return PersonaType.ENGINEER


# === CLI Harness (claude -p) ===
#
# All argv/env assembly, stream-json parsing, health checks, and turn-input
# composition for the "claude -p" CLI harness live in
# agent/session_runner/harness/claude.py (plan #2000, Phase 2 extraction).
# Re-exported here (explicit `as`-aliased re-export idiom -- tells ruff F401
# these are intentional, not dead imports) so existing (non-runner) callers
# -- agent/session_completion.py, agent/__init__.py, and tests that import
# these names from agent.sdk_client -- keep working unchanged. New call
# sites should import directly from agent.session_runner.harness.claude.
from agent.session_runner.harness.claude import _HARNESS_COMMANDS as _HARNESS_COMMANDS  # noqa: E402
from agent.session_runner.harness.claude import _UUID_PATTERN as _UUID_PATTERN  # noqa: E402
from agent.session_runner.harness.claude import (  # noqa: E402
    HARNESS_MAX_INPUT_CHARS as HARNESS_MAX_INPUT_CHARS,
)
from agent.session_runner.harness.claude import (  # noqa: E402
    IMAGE_DIMENSION_SENTINEL as IMAGE_DIMENSION_SENTINEL,
)
from agent.session_runner.harness.claude import (  # noqa: E402
    THINKING_BLOCK_SENTINEL as THINKING_BLOCK_SENTINEL,
)
from agent.session_runner.harness.claude import (  # noqa: E402
    HarnessThinkingBlockCorruptionError as HarnessThinkingBlockCorruptionError,
)
from agent.session_runner.harness.claude import (  # noqa: E402
    _apply_context_budget as _apply_context_budget,
)
from agent.session_runner.harness.claude import (  # noqa: E402
    _run_harness_subprocess as _run_harness_subprocess,
)
from agent.session_runner.harness.claude import (  # noqa: E402
    build_harness_turn_input as build_harness_turn_input,
)
from agent.session_runner.harness.claude import (  # noqa: E402
    get_response_via_harness as get_response_via_harness,
)
from agent.session_runner.harness.claude import (  # noqa: E402
    verify_harness_health as verify_harness_health,
)
