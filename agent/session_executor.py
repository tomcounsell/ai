"""Core session execution: CLI harness subprocess lifecycle, turn-boundary steering,
nudge/re-enqueue paths, and calendar heartbeat."""

import asyncio
import logging
import os  # noqa: F401
import re
from datetime import UTC, datetime
from pathlib import Path

from agent.constants import REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS
from agent.output_router import NUDGE_MESSAGE, SendToChatResult
from agent.session_completion import (
    _complete_agent_session,  # noqa: F401
    _diagnose_missing_session,
)
from agent.session_health import HEARTBEAT_WRITE_INTERVAL
from agent.session_logs import save_session_snapshot
from agent.session_revival import _session_branch_name
from agent.session_runner.router import CLEAN_EXIT_REASONS as _CLEAN_RUNNER_EXIT_REASONS
from agent.session_state import (
    SessionHandle,
    _active_sessions,
)
from agent.worktree_manager import (
    WORKTREES_DIR,
    validate_workspace,
)
from config.enums import ClassificationType, SessionType
from config.settings import settings
from models.agent_session import AgentSession
from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

logger = logging.getLogger(__name__)


def _is_non_clean_runner_exit(agent_session) -> bool:
    """Return True when the session has a runner exit_reason that signals a real failure.

    None exit_reason = not yet set = clean (default behavior).
    Clean runner exits: pm_complete (normal end), pm_user (real ``[/user]`` answer
    the PM chose to deliver), pm_needs_human (runner-forwarded needs-input prompt
    from a ``needs_human`` hook edge on an unroutable turn — distinct from pm_user),
    pm_floor_delivered (wrap-up guard delivered PM's last assistant message directly
    when the PM produced a real but prefix-less response — issue #1719),
    steer_abort (operator-requested abort via a steering message; the user-facing
    "Session stopped at your request." is delivered before the loop breaks — #1779).
    Everything else (error, exception, turn_timeout, pm_empty_turn, pm_max_turns —
    the exit-classification vocabulary in ``agent/session_runner/router.py``)
    is non-clean → REACTION_ERROR.

    This does NOT suppress the success reaction for clean+communicated=False completions
    (those still get REACTION_SUCCESS or REACTION_COMPLETE via the normal branch).
    The communicated=False chip handles that signal non-destructively.
    """
    exit_reason = getattr(agent_session, "exit_reason", None)
    if exit_reason is None:
        return False
    return exit_reason not in _CLEAN_RUNNER_EXIT_REASONS


def _session_recorded_reap_failure(agent_session_id: str | None) -> bool:
    """True when the session recorded a ``runner_reap_failed`` event (issue #1938).

    The runner's ``_run_one_turn`` finally writes this durable session event when
    its synchronous SIGKILL could not confirm the turn's process group is dead
    (a pathological unkillable/D-state group). The synthetic-slug worktree cleanup
    reads it to SKIP deletion — so no worktree is removed under a possibly-live
    child. The in-scope ``session`` object may be stale, so re-read fresh from
    Popoto. Fail-silent: a read failure returns ``False`` (proceed with cleanup)
    rather than crashing the terminal path.
    """
    if not agent_session_id:
        return False
    try:
        fresh = AgentSession.get_by_id(agent_session_id)
        events = getattr(fresh, "session_events", None) or []
        return any(isinstance(ev, dict) and ev.get("type") == "runner_reap_failed" for ev in events)
    except Exception as e:  # noqa: BLE001 — a marker read must never crash cleanup
        logger.debug("[synthetic-slug] reap-marker reload failed (non-fatal): %s", e)
        return False


def _runner_final_status(task_error, agent_session) -> str:
    """Terminal AgentSession status for a finished runner session.

    ``SessionRunner.run()`` never raises — subprocess failures and loop
    exceptions become ``summary.exit_reason=ExitReason.ERROR/EXCEPTION`` — so
    ``task_error`` alone cannot gate finalization: a failed run would
    finalize ``completed`` (the #1916 class). Consult the runner's persisted
    ``exit_reason`` alongside ``task_error``; ``agent_session=None`` (lookup
    race) degrades to task_error-only gating.
    """
    if task_error or _is_non_clean_runner_exit(agent_session):
        return "failed"
    return "completed"


def _resolve_session_model(session: AgentSession | None) -> str | None:
    """D1 precedence cascade for session model.

    Order (closest to LLM call wins):
      1. ``session.model`` (explicit per-session, via
         ``valor-session create --model <name>``)
      2. ``settings.models.session_default_model`` (machine-local override,
         env var ``MODELS__SESSION_DEFAULT_MODEL``)
      3. codebase default ``"opus"`` (set on the pydantic Field default in
         ``config/settings.py``)

    Returns the resolved model alias (e.g. ``"opus"``, ``"sonnet"``), or
    ``None`` if the cascade resolves to an empty string (operator-
    misconfigured settings default to ``""``). ``None`` is treated by
    ``get_response_via_harness()`` as "omit ``--model``, use CLI default."
    """
    explicit = getattr(session, "model", None) if session else None
    if explicit:
        return explicit
    fallback = settings.models.session_default_model
    return fallback or None


def _fetch_live_active_run_id(agent_session: AgentSession | None) -> str | None:
    """Re-fetch ``active_run_id`` from Redis for the renewal tick (#2003 cycle-3).

    The executor's ``agent_session`` object is a snapshot fetched ONCE at
    session start -- BEFORE the session-ensure subprocess (spawned inside the
    ``claude -p`` turn) writes ``active_run_id`` to the record. Popoto objects
    do not lazily re-read Redis, so reading the snapshot attribute is
    permanently stale: ``None`` on fresh runs (renewal would skip forever and
    the lock lapses mid-stage, reopening the #1915 takeover window), or the
    PREVIOUS run's id on resumed sessions (a lapsed lock would be SET-NX
    re-acquired under a dead identity and renewed every tick, wedging the
    live run's own calls behind ISSUE_LOCKED until a worker restart).

    One indexed Popoto query per 60s tick -- never raw Redis. Returns ``None``
    (skip renewal this tick) when the record is gone, carries no run_id, or
    the fetch fails; the next tick retries.
    """
    sid = getattr(agent_session, "session_id", None)
    if not sid:
        return None
    try:
        rows = list(AgentSession.query.filter(session_id=sid))
    except Exception as exc:
        logger.debug(
            "[%s] issue-lock renewal: active_run_id re-fetch failed (%s: %s) -- skipping this tick",
            sid,
            type(exc).__name__,
            exc,
        )
        return None
    # Prefer the eng-typed record (mirrors the resolution the SDLC tools use).
    for row in rows:
        if getattr(row, "session_type", None) == "eng":
            rid = getattr(row, "active_run_id", None)
            if rid:
                return rid
    for row in rows:
        rid = getattr(row, "active_run_id", None)
        if rid:
            return rid
    return None


def _tick_issue_lock_renewal(
    session: AgentSession,
    agent_session: AgentSession | None,
) -> None:
    """Renew the per-issue SDLC ownership lock on the tier-1 (60s) heartbeat tick.

    Issue #1954: an in-progress eng session working an issue must keep the
    per-issue ``touch_issue_lock()`` lock alive for as long as it is ticking,
    or the lock's ``ISSUE_LOCK_TTL_SECONDS`` (default 1800s) will expire mid-session
    and let a second independent process claim the same issue (the #1915
    duplicate-PR root cause). This is deliberately called from the tier-1
    (60s) heartbeat block, NOT the 25-minute calendar block elsewhere in
    ``_heartbeat_loop`` -- that slower cadence would blow straight past the
    300s TTL and defeat the purpose of renewal.

    Guarded on ``agent_session.session_type == "eng"`` and a resolved
    (truthy) ``agent_session.issue_number`` -- non-eng sessions and eng
    sessions with no associated issue never touch the lock.

    Run identity (issue #2003, cycle-2 BLOCKER): renewal is keyed by the
    session record's ``active_run_id`` -- the read-back of the identity this
    pipeline's own ``ensure_session()`` established, never a foreign
    adoption. Cycle-3 BLOCKER 2: the value is RE-FETCHED from Redis on every
    tick via :func:`_fetch_live_active_run_id` -- the executor's in-memory
    ``agent_session`` snapshot predates the session-ensure subprocess write
    and would be permanently stale (None on fresh runs, the previous run's
    id on resumed runs). A record with no live ``active_run_id`` skips
    renewal: an identity-less tick must never extend or mint a lock. When
    renewal comes back not-owner, a WARNING is logged (no longer
    fire-and-forget) so an out-from-under takeover is visible before the
    TTL lapses.

    Best-effort and side-effect-only: never raises, returns nothing. A
    Redis hiccup or missing field never blocks the heartbeat loop.
    """
    if agent_session is None:
        return
    if getattr(agent_session, "session_type", None) != "eng":
        return
    issue_number = getattr(agent_session, "issue_number", None)
    if not issue_number:
        return

    run_id = _fetch_live_active_run_id(agent_session)
    if not run_id:
        logger.debug(
            "[%s] issue-lock renewal skipped: no live active_run_id on the session record",
            getattr(session, "session_id", "<unknown>"),
        )
        return

    try:
        from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS, touch_issue_lock

        session_id = getattr(session, "session_id", None) or ""
        result = touch_issue_lock(
            issue_number, run_id, session_id=session_id, ttl=ISSUE_LOCK_TTL_SECONDS
        )
        if not result.acquired:
            logger.warning(
                "[%s] issue-lock renewal for issue #%s returned not-owner: lock held "
                "by a foreign run (run_id=%s, session=%s)",
                getattr(session, "session_id", "<unknown>"),
                issue_number,
                result.owner_run_id,
                result.owner_session_id,
            )
    except Exception as exc:  # noqa: BLE001 - renewal must never crash the heartbeat loop
        logger.debug(
            "[%s] issue-lock renewal failed (non-fatal): %s",
            getattr(session, "session_id", "<unknown>"),
            exc,
        )


# -----------------------------------------------------------------------------
# Post-session memory extraction scheduling (hotfix #1055)
# -----------------------------------------------------------------------------
# Keyed by session_id to deduplicate when _execute_agent_session runs twice for
# the same session (health-check revival, retry, manual resume). dict (not set)
# is required so duplicate schedules can be detected and skipped BEFORE a second
# create_task fires.
_pending_extraction_tasks: dict[str, asyncio.Task] = {}


def _capture_turn_count(session_id: str) -> int | None:
    """Re-fetch the persisted ``turn_count`` for a session at schedule time (Fix 2, #1822).

    The in-scope executor ``session`` object is a *different* instance than the
    one ``sdk_client`` persists ``turn_count`` onto (``sdk_client.py:2573-2584``),
    so its in-memory ``session.turn_count`` is stale (typically ``0``). The
    durable, timing-independent source is the persisted ``AgentSession`` record —
    re-fetch the newest by ``session_id`` (the ``sdk_client.py:2573-2576``
    newest-by-``created_at`` pattern). Returns ``None`` on any failure so the
    gate stays a safe no-op (never over-skips).
    """
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return None
        sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
        return sessions[0].turn_count
    except Exception as exc:  # noqa: BLE001 - capture must never crash finalization
        logger.debug(
            "[memory_extraction] turn_count capture failed for %s (non-fatal): %s",
            session_id,
            exc,
        )
        return None


def _is_conversational_session(session: AgentSession) -> bool:
    """True when ``session`` originated from a real conversational channel (Fix 2, #1822).

    Telegram-originated sessions (``create_eng`` / ``create_teammate`` / SDLC
    children) carry an ``initial_telegram_message`` dict; local CLI sessions
    (``create_local``) do not. Conversational sessions must ALWAYS extract — a
    substantive single-turn Telegram correction is high-value — so they are
    exempt from the trivial-session skip. Defaults to ``True`` (no-skip) when the
    signal is unreadable, so the gate never over-skips.
    """
    try:
        return bool(getattr(session, "initial_telegram_message", None))
    except Exception:  # noqa: BLE001 - origin read must never crash finalization
        return True


def _schedule_post_session_extraction(
    session_id: str,
    response_text: str,
    turn_count: int | None = None,
    is_conversational: bool = True,
) -> None:
    """Fire-and-forget post-session memory extraction (hotfix #1055).

    Synchronous — creates and registers an ``asyncio.create_task``; does NOT
    await it. Preserves the #987 ordering invariant: extraction runs in the
    background so the eng nudge fires promptly while extraction is still
    pending.

    **CRITICAL**: this function is declared ``def`` (not ``async def``) and
    returns ``None``. Any ``await`` or ``asyncio.gather(...)`` on its result
    would re-couple extraction latency to the PM nudge and regress #987 /
    #1055. A review-time invariant guards against this.

    Deduplicates by ``session_id``: if a non-done task is already registered
    for this session, logs at INFO and returns. Prevents duplicate observation
    saves and a race on ``clear_session(session_id)`` when
    ``_execute_agent_session`` runs twice for the same session (health-check
    revival, retry, manual resume).

    Extraction failures (including the hard timeout in
    ``agent/memory_extraction.py``) are swallowed inside the task wrapper and
    never propagate out of this scheduler. ``CancelledError`` is re-raised so
    ``drain_pending_extractions`` can cooperate with worker shutdown.
    """
    existing = _pending_extraction_tasks.get(session_id)
    if existing is not None and not existing.done():
        logger.info(
            "[memory_extraction] Extraction already in-flight for %s, skipping duplicate",
            session_id,
        )
        return

    async def _wrapper() -> None:
        try:
            from agent.memory_extraction import run_post_session_extraction

            await run_post_session_extraction(
                session_id,
                response_text,
                turn_count=turn_count,
                is_conversational=is_conversational,
            )
        except asyncio.CancelledError:
            raise  # preserve cancellation semantics for shutdown drain
        except Exception as e:
            logger.debug(
                "[memory_extraction] Background extraction failed for %s (non-fatal): %s",
                session_id,
                e,
            )

    task = asyncio.create_task(_wrapper(), name=f"post_session_extraction:{session_id}")
    _pending_extraction_tasks[session_id] = task
    task.add_done_callback(lambda t: _pending_extraction_tasks.pop(session_id, None))


async def drain_pending_extractions(timeout: float = 5.0) -> None:
    """Drain in-flight post-session extraction tasks on worker shutdown (hotfix #1055).

    No-op if ``_pending_extraction_tasks`` is empty (first-deploy case / worker
    that never ran a session).

    Wiring: called from ``worker/__main__.py`` shutdown sequence AFTER the
    worker-task wait (line ~408, ``await asyncio.gather(*pending, ...)``)
    and BEFORE the health/notify/reflection cancels. At that ordering:

    - All worker loops have drained → every extraction that will be scheduled
      has been scheduled.
    - The event loop is still running → pending extractions can complete or be
      cancelled cleanly.
    - Health/notify/reflection tasks are still live → we are ordered before
      their cancellation, avoiding a mid-cancel scheduling race.

    Common case (extraction near-complete): the 5s window lets the typical
    1-5s extraction finish. Stall case (extraction wedged past the 35s hard
    timeout internally): we accept losing this on shutdown; the internal
    hard-timeout already caps worst-case latency.
    """
    if not _pending_extraction_tasks:
        return  # First-deploy case — nothing to drain

    pending = list(_pending_extraction_tasks.values())
    logger.info("[memory_extraction] Draining %d pending extraction task(s)", len(pending))
    done, still_pending = await asyncio.wait(pending, timeout=timeout)
    for task in still_pending:
        task.cancel()
    if still_pending:
        logger.warning(
            "[memory_extraction] Cancelled %d extraction task(s) that did not complete "
            "within %.1fs",
            len(still_pending),
            timeout,
        )


# Harness startup retry constants
_HARNESS_NOT_FOUND_PREFIX = "Error: CLI harness not found"
_HARNESS_NOT_FOUND_MAX_RETRIES = 3
_HARNESS_EXHAUSTION_MSG = (
    "Tried a few times but couldn't get Claude to start — "
    "looks like the CLI may not be on PATH. "
    "You can resend once that's sorted."
)


async def _handle_harness_not_found(raw: str, agent_session) -> tuple[str, bool]:
    """Handle a FileNotFoundError harness result with silent retry and persona-aligned exhaustion.

    Called from do_work() when the harness returns a string starting with
    _HARNESS_NOT_FOUND_PREFIX. Returns (result_string, harness_requeued).

    Extracted so both production do_work() and tests call the same code path.
    Tests that mock transition_status / _ensure_worker patch at the module level.

    Returns:
        (raw, False)   — B1 guard: agent_session is None, return raw unchanged
        ("", True)     — silently re-queued; BackgroundTask skips send on empty string
        (persona, False) — retries exhausted or status conflict; deliver exhaustion message
    """
    from models.session_lifecycle import StatusConflictError, transition_status  # noqa: PLC0415

    if agent_session is None:
        return raw, False

    ec = agent_session.extra_context or {}
    retry_count = int(ec.get("cli_retry_count", 0))

    if retry_count < _HARNESS_NOT_FOUND_MAX_RETRIES:
        ec["cli_retry_count"] = retry_count + 1
        agent_session.extra_context = ec
        # B2: reuse existing record in-place — no new async_create()
        try:
            await asyncio.to_thread(transition_status, agent_session, "pending", "harness-retry")
        except (StatusConflictError, ValueError) as conflict_err:
            # Health monitor or another process raced and changed status.
            # Fall through to the exhaustion message rather than leaking a
            # raw StatusConflictError string to Telegram.
            logger.warning(
                "[%s] Harness retry: status conflict (%s) — sending exhaustion msg",
                agent_session.session_id,
                conflict_err,
            )
            return _HARNESS_EXHAUSTION_MSG, False
        _call_ensure_worker(
            agent_session.worker_key,
            is_project_keyed=agent_session.is_project_keyed,
        )
        logger.warning(
            "[%s] Harness not found — retry %d/%d",
            agent_session.session_id,
            retry_count + 1,
            _HARNESS_NOT_FOUND_MAX_RETRIES,
        )
        return "", True  # harness_requeued=True; BackgroundTask skips send on empty string
    else:
        return _HARNESS_EXHAUSTION_MSG, False


def _call_ensure_worker(worker_key, *, is_project_keyed=False):
    """Deferred call to _ensure_worker to avoid circular import."""
    from agent.agent_session_queue import _ensure_worker  # noqa: PLC0415

    _ensure_worker(worker_key, is_project_keyed=is_project_keyed)


def _find_valor_calendar() -> str:
    """Find valor-calendar CLI, preferring venv installation."""
    import shutil

    # Check PATH first
    found = shutil.which("valor-calendar")
    if found:
        return found

    # Fall back to known locations
    for path in [
        Path(__file__).parent.parent / ".venv" / "bin" / "valor-calendar",
        Path.home() / "Library" / "Python" / "3.12" / "bin" / "valor-calendar",
        Path.home() / "src" / "ai" / ".venv" / "bin" / "valor-calendar",
    ]:
        if path.exists():
            return str(path)

    return "valor-calendar"  # Let it fail with clear error


async def _calendar_heartbeat(slug: str, project: str | None = None) -> None:
    """Fire-and-forget calendar heartbeat via subprocess."""
    try:
        valor_calendar = _find_valor_calendar()
        cmd = [valor_calendar]
        if project:
            cmd.extend(["--project", project])
        cmd.append(slug)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            logger.info(f"Calendar heartbeat: {stdout.decode().strip()}")
        else:
            logger.warning(f"Calendar heartbeat failed: {stderr.decode().strip()}")
    except Exception as e:
        logger.warning(f"Calendar heartbeat failed for '{slug}': {e}")


# Interval between calendar heartbeats during long-running sessions
CALENDAR_HEARTBEAT_INTERVAL = 25 * 60  # 25 minutes (fits within 30-min segments)


async def _enqueue_nudge(
    session: AgentSession,
    branch_name: str,
    task_list_id: str,
    auto_continue_count: int,
    output_msg: str,
    nudge_feedback: str = "continue",
) -> None:
    """Enqueue a nudge by reusing the existing AgentSession.

    The nudge loop uses this to re-enqueue the session with a nudge message
    ("Keep working") when the agent stops but hasn't completed. This
    re-spawns Claude Code with the nudge as input.

    Preserves all session metadata via delete-and-recreate pattern.

    Args:
        session: The current AgentSession being executed.
        branch_name: Git branch name for the session.
        task_list_id: Task list ID for sub-agent isolation.
        auto_continue_count: Current nudge count (already incremented).
        output_msg: The agent output that triggered the nudge.
        nudge_feedback: Nudge message sent to the agent.
    """

    # Terminal status guard: re-read session status from Redis and bail if terminal.
    # This makes _enqueue_nudge() self-defending rather than relying on caller
    # discipline (e.g., determine_delivery_action() upstream).
    current_status = getattr(session, "status", None)
    if current_status in _TERMINAL_STATUSES:
        logger.warning(
            f"[{session.project_key}] _enqueue_nudge() called for session "
            f"{session.session_id} in terminal status {current_status!r} — "
            f"returning early to prevent respawn"
        )
        return

    logger.info(
        f"[{session.project_key}] Nudge message "
        f"({len(nudge_feedback)} chars): {nudge_feedback[:120]!r}"
    )

    # Reuse existing AgentSession instead of creating a new one.
    # This preserves classification_type, history, links, context_summary,
    # expectations, and all other metadata that would be lost if we called
    # enqueue_agent_session() (which creates a brand new AgentSession record).
    #
    # Uses get_authoritative_session for tie-break re-read (prefers running,
    # then most recent by created_at) instead of blind sessions[0].
    from models.session_lifecycle import get_authoritative_session

    orig_session_id = session.session_id
    reread_session = await asyncio.to_thread(get_authoritative_session, orig_session_id)
    if reread_session is None:
        # Session not found in Redis — fall back to recreate from in-memory metadata.
        _diag = _diagnose_missing_session(orig_session_id)
        logger.error(
            f"[{session.project_key}] No session found for {orig_session_id} "
            f"— falling back to recreate from AgentSession metadata. "
            f"Diagnostics: {_diag}"
        )
        # Fallback path terminal guard: this path bypasses transition_status()
        # entirely (uses raw async_create), so it needs its own independent
        # terminal status check. The session object we have is from when it was
        # popped — re-check against the status we already read above.
        if current_status in _TERMINAL_STATUSES:
            logger.warning(
                f"[{session.project_key}] Fallback recreate blocked: session "
                f"{orig_session_id} has terminal status {current_status!r}"
            )
            return
        # Fallback: recreate session preserving ALL metadata from the
        # underlying AgentSession that was loaded when the session was popped.
        # This prevents loss of context_summary, expectations, issue_url,
        # pr_url, history, correlation_id, and other session-phase fields.
        from agent.agent_session_queue import _extract_agent_session_fields as _eaf  # noqa: PLC0415

        fields = _eaf(session)
        # Override fields that change for continuation
        fields["status"] = "pending"
        # Update initial_telegram_message directly (message_text/sender_name
        # are now consolidated into this DictField)
        itm = fields.get("initial_telegram_message") or {}
        itm["message_text"] = nudge_feedback
        itm["sender_name"] = "System (auto-continue)"
        fields["initial_telegram_message"] = itm
        fields.pop("message_text", None)
        fields.pop("sender_name", None)
        fields["auto_continue_count"] = auto_continue_count
        fields["priority"] = "high"
        fields["task_list_id"] = task_list_id
        await AgentSession.async_create(**fields)
        _call_ensure_worker(session.worker_key, is_project_keyed=session.is_project_keyed)
        logger.info(
            f"[{session.project_key}] Recreated session "
            f"{orig_session_id} from AgentSession metadata "
            f"(fallback path, auto_continue_count="
            f"{auto_continue_count})"
        )
        return

    session = reread_session

    # Re-read guard: session status may have changed between the initial check
    # and this point (e.g., another process finalized the session).
    reread_status = getattr(session, "status", None)
    if reread_status in _TERMINAL_STATUSES:
        logger.warning(
            f"[{session.project_key}] _enqueue_nudge() main path: session "
            f"{session.session_id} is now in terminal status {reread_status!r} "
            f"(changed since entry check) — returning early"
        )
        return

    # Apply companion fields directly to the already-loaded session object,
    # then transition via transition_status() which has its own CAS re-read.
    # This avoids the redundant Redis re-read that update_session() would do.
    from models.session_lifecycle import transition_status

    session.message_text = nudge_feedback
    session.auto_continue_count = auto_continue_count
    session.priority = "high"
    session.task_list_id = task_list_id
    transition_status(
        session,
        "pending",
        reason=f"nudge re-enqueue (auto_continue_count={auto_continue_count})",
    )

    _call_ensure_worker(session.worker_key, is_project_keyed=session.is_project_keyed)
    logger.info(
        f"[{session.project_key}] Reused session {session.session_id} for continuation "
        f"(auto_continue_count={auto_continue_count})"
    )


# ---------------------------------------------------------------------------
# Public steering API
# ---------------------------------------------------------------------------


async def re_enqueue_session(
    session: AgentSession,
    branch_name: str,
    task_list_id: str,
    auto_continue_count: int,
    output_msg: str,
    nudge_feedback: str = "continue",
) -> None:
    """Public wrapper for _enqueue_nudge — re-enqueue a session with a nudge message.

    Encapsulates Redis state management and worker wake-up. Callers outside
    this module (e.g., output_router, valor-session CLI) should use this
    instead of _enqueue_nudge directly.

    Args:
        session: The current AgentSession being re-enqueued.
        branch_name: Git branch name for the session.
        task_list_id: Task list ID for sub-agent isolation.
        auto_continue_count: Current nudge count (already incremented).
        output_msg: The agent output that triggered the nudge.
        nudge_feedback: Nudge message sent to the agent.
    """
    await _enqueue_nudge(
        session=session,
        branch_name=branch_name,
        task_list_id=task_list_id,
        auto_continue_count=auto_continue_count,
        output_msg=output_msg,
        nudge_feedback=nudge_feedback,
    )


def steer_session(session_id: str, message: str) -> dict:
    """Push a steering message onto a session's Redis steering queue.

    Any process can call this to inject a message into a running or pending
    session. The worker drains the Redis steering list (agent.steering)
    between turns and injects any pending messages as user input for the
    next SDK turn.

    Args:
        session_id: The session_id of the target AgentSession.
        message: The steering message to inject.

    Returns:
        dict with keys: success (bool), session_id (str), error (str | None)
    """
    if not message or not message.strip():
        return {
            "success": False,
            "session_id": session_id,
            "error": "Empty message rejected",
        }

    try:
        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return {
                "success": False,
                "session_id": session_id,
                "error": f"Session not found: {session_id}",
            }

        session = sessions[0]
        current_status = getattr(session, "status", None)

        if current_status in _TERMINAL_STATUSES:
            return {
                "success": False,
                "session_id": session_id,
                "error": f"Session is in terminal status {current_status!r} — steering rejected",
            }

        from agent.steering import push_steering_message as _push_steering_message

        _push_steering_message(session.session_id, message, "pm")
        try:
            _call_ensure_worker(session.worker_key, is_project_keyed=session.is_project_keyed)
        except RuntimeError:
            pass  # No event loop (CLI context) — worker will pick it up on next loop
        logger.info(
            f"[steering] Queued steering message for session {session_id}: "
            f"{message[:80]!r} (status={current_status})"
        )
        return {"success": True, "session_id": session_id, "error": None}

    except Exception as e:
        logger.error(f"[steering] steer_session failed for {session_id}: {e}")
        return {"success": False, "session_id": session_id, "error": str(e)}


async def _maybe_send_failure_notice(messenger, session_id: str) -> None:
    """Best-effort user-facing notice on a running->failed transition (#1877 defect #2).

    Mirrors the CancelledError best-effort interrupted-message pattern. Guarantees:

    * **Deduped.** A ``failed-sent:{session_id}`` SET NX key (120s TTL) ensures the
      three finalize paths in the executor's failure block never double-send.
    * **Never double-narrates a no-resume killed session.** If a killer already owns
      a no-resume exit narrative it has written ``cancel-reason:{session_id}=no_resume``
      (and sent its own interrupt message); this function returns early so the user is
      not sent two competing exit stories (folded-in critique concern: cross-class dedup
      collision). An absent cancel-reason does NOT suppress the notice — a session that
      was silently auto-resumed (or requeued) and then genuinely crashed still deserves
      the failure copy.
    * **Never blocks finalization.** The send is bounded by a 2s ``wait_for`` and
      every error (including the timeout) is swallowed — this coroutine never
      raises, so the caller's finalize path always proceeds.
    """
    try:
        from agent.cancel_reason import get_cancel_reason
        from agent.notification_copy import FAILURE_NOTICE

        # Cross-class dedup collision (critique concern): a killer that already
        # owns a *no-resume* exit narrative must not be double-messaged. The
        # post-silent-resume contract narrows the signal to two states:
        # `"no_resume"` present -> a killer owns the terminal exit narrative,
        # suppress this notice; absent -> no killer narrative (the interruption
        # was silent and the session either resumed or is genuinely crashing
        # now), so the failure notice must still surface.
        if get_cancel_reason(session_id) == "no_resume":
            logger.info(
                "[%s] Failure notice suppressed — a killer already owns the "
                "no-resume exit narrative (cancel-reason=no_resume)",
                session_id,
            )
            return

        should_send = True
        try:
            from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

            should_send = bool(
                POPOTO_REDIS_DB.set(f"failed-sent:{session_id}", "1", nx=True, ex=120)
            )
        except Exception as dedup_err:
            # Redis unavailable: send anyway (a duplicate is preferable to silence
            # on a genuine failure).
            logger.debug(
                "[%s] failed-sent dedup lock unavailable (%s); sending anyway",
                session_id,
                dedup_err,
            )

        if should_send:
            await asyncio.wait_for(messenger._send_callback(FAILURE_NOTICE), timeout=2.0)
    except (TimeoutError, Exception) as err:
        logger.warning("[%s] Failure-notice best-effort send failed: %s", session_id, err)


async def _execute_agent_session(session: AgentSession) -> None:
    """
    Execute a single agent session:
    1. Log calendar heartbeat (start)
    2. Run agent work via BackgroundTask + BossMessenger (in project working dir)
    3. Periodic calendar heartbeats during long-running work
    4. Set reaction based on result

    Two-tier no-progress detector integration (#1036):
      * A ``SessionHandle`` is registered in ``_active_sessions`` BEFORE any
        raise site so the health check always has a cancellable reference.
      * Cleanup happens in an explicit ``finally`` block wrapping the entire
        session body — ``_active_sessions.pop()`` runs on every exit path
        (return, raise, ``CancelledError``).
      * A T+0 ``last_heartbeat_at`` write ensures the first health-check tick
        after session start sees a fresh heartbeat.
      * Two messenger callbacks (``on_sdk_started``, ``on_heartbeat_tick``)
        bump per-session ORM fields; the messenger itself imports nothing
        from ``models/``. ``last_stdout_at`` liveness is owned by
        ``SessionRunner._stamp_stdout_liveness`` (issue #1935) — this
        messenger no longer duplicates that write (a prior, unlanded,
        dead-in-production attempt at the same signal was removed here).
    """
    from agent import BackgroundTask, BossMessenger

    # === Two-tier no-progress detector registration (#1036) ===
    # Register an empty handle BEFORE any raise site so observability is
    # consistent even if setup/enrichment/hydration fails.
    #
    # The cancellable task reference (``handle.task``) is populated AFTER
    # ``BackgroundTask.run()`` creates the session-scoped task at
    # ``messenger.py:198``. Between registration and that point the session
    # runs on the worker-loop task — cancelling the current task from the
    # health check would kill the entire worker (plan spike-1 explicitly
    # forbids this; #1039 review).
    #
    # Cleanup is guaranteed by the ``finally`` block wrapping the entire
    # session body below: ``_active_sessions.pop()`` runs on every exit path
    # (return, raise, ``CancelledError``), satisfying the "pop in a finally
    # block, always" contract from the SessionHandle docstring.
    _session_id_for_registry = session.agent_session_id
    if _session_id_for_registry:
        _active_sessions[_session_id_for_registry] = SessionHandle(task=None)
    try:
        # T+0 heartbeat write: guarantee the very first health-check tick after
        # session start sees a fresh heartbeat. Uses the pre-loaded `session`
        # (the enqueue record) directly with a partial save scoped to a single
        # field so it cannot clobber status or any other field.
        try:
            session.last_heartbeat_at = datetime.now(tz=UTC)
            session.save(update_fields=["last_heartbeat_at"])
        except Exception as _hb_err:
            logger.warning(
                "[%s] T+0 last_heartbeat_at save failed (non-fatal): %s",
                session.session_id,
                _hb_err,
            )

        # Defense-in-depth guard (issue #1195): convert silent TypeError on
        # `Path(None)` (or downstream `sanitize_branch_name(None)` AttributeError)
        # into an observable failure. The historical offender was
        # `_create_continuation_pm` saving sessions with both fields ``None``;
        # any future spawn site that forgets a required field will fail loudly
        # here instead of mid-startup with no Telegram message. The session is
        # marked ``failed`` so dashboards / reflections surface it.

        # Synthesis precondition (issue #1272): a slugless eng session needs
        # ``agent_session_id`` to derive its synthetic slug ``dev-{aid[:8]}``.
        # (The ``dev-`` slug prefix is a stable historical literal that the
        # cleanup regex below still matches; only the session type is ``eng``.)
        # If both ``slug`` and ``agent_session_id`` are missing on an eng session
        # the synthesis branch below would crash with
        # ``TypeError: 'NoneType' object is not subscriptable``. Fail loudly
        # here so the failure mode is observable instead of obscured.
        _stype_pre = getattr(session, "session_type", None)
        _slug_pre = getattr(session, "slug", None)
        _aid_pre = getattr(session, "agent_session_id", None)
        if _stype_pre == "eng" and _slug_pre is None and _aid_pre is None:
            _parent = getattr(session, "parent_agent_session_id", None)
            logger.error(
                "[executor-guard] Refusing to start slugless eng session with None "
                "agent_session_id (reason=missing_aid_for_synthetic_slug): "
                f"slug={_slug_pre!r} session_type={_stype_pre} "
                f"parent_agent_session_id={_parent} "
                f"working_dir={session.working_dir!r} session_id={session.session_id!r}"
            )
            try:
                from models.session_lifecycle import (  # noqa: PLC0415
                    StatusConflictError,
                    finalize_session,
                )

                finalize_session(
                    session,
                    "failed",
                    reason=(
                        "slugless eng session requires agent_session_id for "
                        "synthetic slug derivation (issue #1272)"
                    ),
                )
            except StatusConflictError as finalize_conflict:
                logger.info(
                    "[executor-guard] Skipping finalize for %s: %s",
                    getattr(session, "agent_session_id", "?"),
                    finalize_conflict,
                )
            except Exception as finalize_err:
                logger.error(
                    "[executor-guard] finalize_session(failed) raised: %s",
                    finalize_err,
                )
                try:
                    session.status = "failed"
                    session.save(update_fields=["status", "updated_at"])
                except Exception as last_resort_err:
                    logger.debug(
                        "[executor-guard] last-resort status save failed (non-fatal): %s",
                        last_resort_err,
                    )
            return

        if session.working_dir is None or session.session_id is None:
            offending_field = "working_dir" if session.working_dir is None else "session_id"
            _aid = getattr(session, "agent_session_id", None) or getattr(session, "id", "?")
            _stype = getattr(session, "session_type", "?")
            _parent = getattr(session, "parent_agent_session_id", None)
            logger.error(
                "[executor-guard] Refusing to start session with None "
                f"{offending_field} "
                f"(reason=missing_working_dir_or_session_id): "
                f"agent_session_id={_aid} "
                f"session_type={_stype} "
                f"parent_agent_session_id={_parent} "
                f"working_dir={session.working_dir!r} session_id={session.session_id!r}"
            )
            try:
                from models.session_lifecycle import (  # noqa: PLC0415
                    StatusConflictError,
                    finalize_session,
                )

                finalize_session(
                    session,
                    "failed",
                    reason=f"missing_working_dir_or_session_id: {offending_field} is None",
                )
            except StatusConflictError as finalize_conflict:
                # Session is already terminal (kill-is-terminal #1208): don't
                # alarm-log, just record at INFO and let the existing terminal
                # status stand. No fallback save needed — the session is already
                # in a terminal state by definition.
                logger.info(
                    "[executor-guard] Skipping finalize for %s: %s",
                    getattr(session, "agent_session_id", "?"),
                    finalize_conflict,
                )
            except Exception as finalize_err:
                logger.error(
                    "[executor-guard] finalize_session(failed) raised: %s",
                    finalize_err,
                )
                # Last-resort: mark status directly so the worker doesn't loop on
                # this entry. ``reason`` is not a stored field on AgentSession —
                # the structured ``[executor-guard]`` log above is the canonical
                # reason record (visible to reflections / dashboards via log).
                try:
                    session.status = "failed"
                    session.save(update_fields=["status", "updated_at"])
                except Exception as last_resort_err:
                    logger.debug(
                        "[executor-guard] last-resort status save failed (non-fatal): %s",
                        last_resort_err,
                    )
            return

        working_dir = Path(session.working_dir)
        allowed_root = Path.home() / "src"
        is_wt = WORKTREES_DIR in str(working_dir)
        working_dir = validate_workspace(working_dir, allowed_root, is_worktree=is_wt)

        # Restore branch state from checkpoint if this is a resumed session
        try:
            from agent.agent_session_queue import restore_branch_state  # noqa: PLC0415

            restore_branch_state(session)
        except Exception as e:
            logger.debug(f"[restore] Non-fatal restore error at session start: {e}")

        # Resolve branch: use slug + stage mapping if available, else session-based
        slug = session.slug
        stage = None
        # Synthetic-slug synthesis for slugless eng sessions (issue #1272,
        # Alternative A from docs/plans/parallel-session-checkout-guard.md).
        #
        # The #887 main-checkout protection guard below short-circuits on
        # ``slug is None``: ``_stype == "eng" and slug and ...``. That left a
        # residual hole — an eng session created without a slug (future
        # debug harness, test fixture, or any code path that bypasses the
        # CLI) would skip worktree provisioning AND skip the guard, landing
        # in the main checkout. Synthesizing ``dev-{aid[:8]}`` here funnels
        # every eng session through the existing worktree-creation path so
        # the guard always has a slug to enforce against. The
        # ``agent_session_id`` precondition above (executor-guard) ensures
        # ``aid`` is non-None before this line runs.
        # Synthetic slug shape: dev-{first 8 chars of agent_session_id} (the
        # ``dev-`` prefix is a stable historical literal the cleanup regex
        # below matches; only the session type is ``eng``).
        is_synthetic_slug = False
        if not slug and getattr(session, "session_type", None) == "eng":
            _aid_for_slug = getattr(session, "agent_session_id", None)
            if _aid_for_slug:
                slug = f"dev-{_aid_for_slug[:8]}"
                is_synthetic_slug = True
                # Stable grep marker for post-deploy reflection scans —
                # MUST be the literal token ``[synthetic-slug]`` so log
                # audits can count occurrences without false positives.
                logger.info(
                    f"[synthetic-slug] Allocated synthetic slug {slug} "
                    f"for slugless eng session {_aid_for_slug} (issue #1272)"
                )
        if slug:
            # Try to read current stage from the AgentSession
            try:
                from models.session_lifecycle import get_authoritative_session as _get_auth

                _auth = _get_auth(session.session_id)
                if _auth:
                    stage = _auth.current_stage
            except Exception as e:
                logger.debug(
                    f"[{session.project_key}] current_stage lookup failed for "
                    f"{session.session_id} (non-fatal): {e}"
                )
            from agent.agent_session_queue import resolve_branch_for_stage  # noqa: PLC0415

            resolved_branch, needs_wt = resolve_branch_for_stage(slug, stage)
            # Synthetic dev slugs (#1272) have no SDLC stage, so the
            # default mapping returns ``("main", False)`` — which would
            # bypass worktree provisioning AND trip the main-checkout
            # guard below. Force the synthetic case onto a session branch
            # in a worktree so isolation is guaranteed.
            if is_synthetic_slug:
                resolved_branch = f"session/{slug}"
                needs_wt = True
            # Stageless eng sessions with a pre-provisioned worktree
            # (typical for /do-todos batch dispatch: a parent eng session creates
            # child eng sessions with working_dir already pointing at
            # ``.worktrees/{slug}/`` but no ``current_stage`` set yet).
            # ``resolve_branch_for_stage`` returns ``("main", False)`` in
            # that case, which trips the branch-mismatch guard below
            # because the worktree is on ``session/{slug}`` and
            # ``git checkout main`` cannot succeed when main is owned by
            # the primary checkout. Trust the worktree's branch.
            _stype_early = getattr(session, "session_type", None)
            if (
                _stype_early == "eng"
                and slug
                and stage is None
                and resolved_branch == "main"
                and not needs_wt
                and WORKTREES_DIR in str(working_dir)
                and working_dir.exists()
            ):
                resolved_branch = f"session/{slug}"
                needs_wt = True
            branch_name = resolved_branch
            # If branch resolution says we need a worktree and working_dir isn't one,
            # OR the path looks like a worktree but the directory is missing on disk
            # (e.g., enqueued path points at .worktrees/{slug}/ that was never created
            # or got cleaned up between runs — see issue #887 follow-up).
            if needs_wt and (WORKTREES_DIR not in str(working_dir) or not working_dir.exists()):
                try:
                    from agent.worktree_manager import get_or_create_worktree

                    wt_path = get_or_create_worktree(working_dir, slug)
                    working_dir = Path(wt_path)
                    logger.info(
                        f"[branch-mapping] Resolved worktree for slug={slug} "
                        f"stage={stage}: {working_dir}"
                    )
                except Exception as e:
                    _stype = getattr(session, "session_type", None)
                    if _stype == "eng":
                        # Eng sessions with a slug MUST have worktree isolation.
                        # Falling back to the main checkout would contaminate it.
                        # See issue #887: session-isolation-bypass incident (2026-04-10).
                        logger.critical(
                            f"[branch-mapping] FATAL: Failed to create worktree for "
                            f"eng session slug={slug}: {e} — refusing to proceed in "
                            f"main checkout to prevent contamination"
                        )
                        raise RuntimeError(
                            f"Worktree provisioning failed for eng session "
                            f"slug={slug}: {e}. Refusing to run in main checkout."
                        ) from e
                    else:
                        logger.warning(
                            f"[branch-mapping] Failed to create worktree for "
                            f"slug={slug}: {e} — using original working dir"
                        )
        else:
            branch_name = _session_branch_name(session.session_id)

        # Main-checkout protection guard (issue #887): eng sessions with a slug
        # must NEVER run in the repo root. If worktree provisioning was skipped
        # or silently failed, catch it here before any git operations run.
        # The check verifies BOTH that the path is under .worktrees/ AND that the
        # directory actually exists on disk — a stale path string pointing at a
        # missing worktree would otherwise let an eng session fall back to the
        # parent CWD (the main checkout) at shell-launch time.
        _stype = getattr(session, "session_type", None)
        if (
            _stype == "eng"
            and slug
            and (WORKTREES_DIR not in str(working_dir) or not working_dir.exists())
        ):
            logger.critical(
                f"[worktree-guard] Eng session {session.session_id} with slug={slug} "
                f"resolved to main checkout or missing worktree ({working_dir}, "
                f"exists={working_dir.exists()}). Refusing to proceed — this would "
                f"contaminate the shared working directory. See issue #887."
            )
            raise RuntimeError(
                f"Eng session with slug={slug} must run in an existing worktree, "
                f"but working_dir={working_dir} (exists={working_dir.exists()}) "
                f"is not a usable worktree. This is a safety guard to prevent "
                f"main checkout contamination (issue #887)."
            )

        # Branch-mismatch guard (issue #1377): a reused worktree handed off
        # between SDLC stages may still be checked out to the previous stage's
        # branch. If we proceed without verifying, the Claude Code subprocess
        # launches on the wrong branch, produces no output, and is killed by
        # startup-recovery 6+ minutes later. verify_worktree_branch
        # auto-recovers clean worktrees and raises on dirty ones — the latter
        # surfaces as a session failure with last_error populated instead of
        # a silent hang.
        if _stype == "eng" and slug and WORKTREES_DIR in str(working_dir):
            from agent.worktree_manager import (  # noqa: PLC0415
                WorktreeBranchMismatchError,
                verify_worktree_branch,
            )

            try:
                verify_worktree_branch(working_dir, branch_name)
            except WorktreeBranchMismatchError as e:
                logger.error(
                    f"[worktree-branch-guard] Session {session.session_id} "
                    f"slug={slug}: {e} — refusing to launch harness (issue #1377)"
                )
                raise

        # Compute task list ID for sub-agent task isolation
        # Tier 2: planned work uses the slug directly
        # Tier 1: ad-hoc sessions use thread-{chat_id}-{root_msg_id}
        if session.slug:
            task_list_id = session.slug
        elif session.task_list_id:
            task_list_id = session.task_list_id
        else:
            # Derive from session_id which encodes chat_id and root message
            parts = session.session_id.split("_")
            root_id = parts[-1] if "_" in session.session_id else session.telegram_message_id
            task_list_id = f"thread-{session.chat_id}-{root_id}"

        # Read correlation_id from session for end-to-end tracing
        cid = session.correlation_id
        log_prefix = f"[{cid}]" if cid else f"[{session.project_key}]"

        logger.info(
            f"{log_prefix} Executing session {session.agent_session_id} "
            f"(session={session.session_id}, branch={branch_name}, cwd={working_dir})"
        )

        # Save session snapshot at session start
        save_session_snapshot(
            session_id=session.session_id,
            event="resume",
            project_key=session.project_key,
            branch_name=branch_name,
            task_summary=f"Session {session.agent_session_id} starting",
            extra_context={
                "agent_session_id": session.agent_session_id,
                "sender": session.sender_name,
                "message_preview": session.message_text[:200] if session.message_text else "",
                "correlation_id": cid,
            },
            working_dir=str(working_dir),
        )

        # Update the AgentSession (already created at enqueue time) with session-phase fields
        agent_session = None
        try:
            sessions = list(
                AgentSession.query.filter(project_key=session.project_key, status="running")
            )
            for s in sessions:
                if s.session_id == session.session_id:
                    agent_session = s
                    break
            if agent_session:
                agent_session.updated_at = datetime.now(tz=UTC)
                agent_session.branch_name = branch_name
                # Persist task_list_id so hooks can resolve this session
                agent_session.task_list_id = task_list_id
                agent_session.save(update_fields=["updated_at", "branch_name", "task_list_id"])
                agent_session.append_history("user", (session.message_text or "")[:200])
        except Exception as e:
            logger.debug(f"AgentSession update failed (non-fatal): {e}")

        # Determine session type for routing decisions
        _session_type = getattr(agent_session, "session_type", None) if agent_session else None

        # Calendar heartbeat at session start. Planned eng sessions use their
        # work-item slug as the event title; Telegram-originated sessions have no
        # slug, so fall back to the project key so their activity still lands on
        # the project's assigned calendar (rolls into one extending daily event).
        # The calendar tool skips silently for projects without a mapping.
        cal_slug = session.slug or session.project_key
        if cal_slug:
            asyncio.create_task(_calendar_heartbeat(cal_slug, project=session.project_key))

        # Create messenger with bridge callbacks, falling back to file output
        # Find the transport from extra_context to support multiple transports per project
        _transport = None
        if agent_session:
            _extra = getattr(agent_session, "extra_context", None) or {}
            _transport = _extra.get("transport")

        from agent.agent_session_queue import _resolve_callbacks  # noqa: PLC0415

        send_cb, react_cb = _resolve_callbacks(session.project_key, _transport)

        if not send_cb:
            from agent.output_handler import FileOutputHandler

            _fallback = FileOutputHandler()
            send_cb = _fallback.send
            react_cb = react_cb or _fallback.react
            logger.info(
                f"[{session.project_key}] No bridge callbacks registered, "
                f"using FileOutputHandler fallback"
            )

        # Explicit state object replaces fragile nonlocal closures (_defer_reaction,
        # _completion_sent, auto_continue_count). State is passed as a mutable object
        # rather than mutated through shared closure references.
        chat_state = SendToChatResult(
            auto_continue_count=session.auto_continue_count or 0,
        )

        async def send_to_chat(msg: str) -> None:
            """Route agent output via nudge loop.

            Simple nudge model: the bridge has ONE response to any non-completion:
            "Keep working -- only stop when you need human input or you're done."
            The PM session owns all SDLC intelligence. The bridge just nudges.

            Completion detection:
            - stop_reason == "end_turn" AND output is non-empty → deliver
            - stop_reason == "rate_limited" → wait with backoff, then nudge
            - Empty output → nudge (not deliver)
            - Safety cap of MAX_NUDGE_COUNT nudges → deliver regardless
            """
            nonlocal agent_session  # Re-read from Redis for fresh stage data

            from agent.health_check import is_session_unhealthy

            # stop_reason was an SDK-loop-only concept (ResultMessage.stop_reason,
            # populated by the now-deleted ValorAgent query loop) -- the CLI
            # harness path never populated it, so this was already always None
            # in production before the SDK path was removed (plan #2000 Task 2.2).
            stop_reason = None

            # Re-read agent_session from Redis for fresh status.  The in-memory
            # copy was loaded with status="running" at session start and is stale
            # when the PM calls wait-for-children (which updates Redis directly).
            # Without this re-read the waiting_for_children guard in output_router
            # never fires.  Issue #1004.
            if agent_session is not None:
                try:
                    from models.agent_session import AgentSession as _FreshAS

                    _fresh = list(_FreshAS.query.filter(session_id=session.session_id))
                    if _fresh:
                        agent_session = sorted(
                            _fresh,
                            key=lambda s: s.created_at or 0,
                            reverse=True,
                        )[0]
                except Exception as _reread_err:
                    # Fall back to stale in-memory copy.
                    logger.warning(
                        f"[{session.project_key}] Session re-read failed; using stale "
                        f"in-memory copy: {_reread_err}"
                    )

            session_status = agent_session.status if agent_session else None
            unhealthy_reason = (
                is_session_unhealthy(session.session_id) if session.session_id else None
            )

            if unhealthy_reason:
                logger.warning(
                    f"[{session.project_key}] Watchdog flagged session "
                    f"unhealthy: {unhealthy_reason}"
                )

            # Resolve session type and classification for PM auto-continue
            _session_type = getattr(agent_session, "session_type", None) if agent_session else None
            _classification = getattr(session, "classification_type", None)
            _is_teammate = (
                agent_session is not None
                and getattr(agent_session, "session_type", None) == SessionType.TEAMMATE
            )

            # Read last_compaction_ts for the post-compact nudge guard (#1127).
            _last_compaction_ts = (
                getattr(agent_session, "last_compaction_ts", None)
                if agent_session is not None
                else None
            )

            # Delegate routing decision to output_router (call site preserved here)
            from agent.output_router import route_session_output

            action, _effective_nudge_cap = route_session_output(
                msg=msg,
                stop_reason=stop_reason,
                auto_continue_count=chat_state.auto_continue_count,
                session_status=session_status,
                completion_sent=chat_state.completion_sent,
                watchdog_unhealthy=unhealthy_reason,
                session_type=_session_type,
                classification_type=_classification,
                is_teammate=_is_teammate,
                last_compaction_ts=_last_compaction_ts,
            )

            if action == "deliver_already_completed":
                logger.info(
                    f"[{session.project_key}] Session already completed — "
                    f"delivering without nudge ({len(msg)} chars)"
                )
                await send_cb(session.chat_id, msg, session.telegram_message_id, agent_session)
                chat_state.completion_sent = True

            elif action == "drop":
                logger.info(
                    f"[{session.project_key}] Dropping suppressed output "
                    f"(completion sent or nudged) "
                    f"({len(msg)} chars): {msg[:100]!r}"
                )

            elif action == "defer_post_compact":
                # Post-compaction nudge guard (issue #1127). A compaction
                # landed less than 30s ago — skip this tick entirely to let
                # the SDK finish writing the compacted transcript and return
                # cleanly to idle. Pure no-op: no `_enqueue_nudge` call, no
                # `completion_sent` flip, no counter on `auto_continue_count`.
                # The next SDK idle tick naturally re-invokes this callback;
                # if the 30s window has expired, the normal nudge flow fires;
                # if real SDK output arrived first, it routes via `"deliver"`.
                try:
                    import time as _time

                    _age = (
                        _time.time() - float(_last_compaction_ts)
                        if _last_compaction_ts is not None
                        else None
                    )
                except (TypeError, ValueError):
                    _age = None
                logger.info(
                    "[%s] Post-compaction nudge guard active (%s) — deferring nudge for this tick",
                    session.project_key,
                    f"last_compaction_ts age={_age:.1f}s" if _age is not None else "age unknown",
                )

            elif action == "nudge_rate_limited":
                chat_state.auto_continue_count += 1
                logger.warning(
                    f"[{session.project_key}] Rate limited — backoff then nudge "
                    f"(nudge {chat_state.auto_continue_count}/{_effective_nudge_cap})"
                )
                await asyncio.sleep(5)
                await _enqueue_nudge(
                    session,
                    branch_name,
                    task_list_id,
                    chat_state.auto_continue_count,
                    msg,
                    nudge_feedback=NUDGE_MESSAGE,
                )
                chat_state.completion_sent = True
                chat_state.defer_reaction = True

            elif action == "nudge_empty":
                chat_state.auto_continue_count += 1
                logger.info(
                    f"[{session.project_key}] Empty output — nudging "
                    f"(nudge {chat_state.auto_continue_count}/{_effective_nudge_cap})"
                )
                await _enqueue_nudge(
                    session,
                    branch_name,
                    task_list_id,
                    chat_state.auto_continue_count,
                    msg,
                    nudge_feedback=NUDGE_MESSAGE,
                )
                chat_state.completion_sent = True
                chat_state.defer_reaction = True

            elif action == "nudge_continue":
                chat_state.auto_continue_count += 1
                logger.info(
                    f"[{session.project_key}] PM/SDLC session — nudging to continue pipeline "
                    f"(nudge {chat_state.auto_continue_count}/{_effective_nudge_cap})"
                )
                await _enqueue_nudge(
                    session,
                    branch_name,
                    task_list_id,
                    chat_state.auto_continue_count,
                    msg,
                    nudge_feedback=NUDGE_MESSAGE,
                )
                chat_state.completion_sent = True
                chat_state.defer_reaction = True

            elif action == "deliver_fallback":
                logger.warning(
                    f"[{session.project_key}] Empty output and nudge cap "
                    f"reached — delivering fallback"
                )
                await send_cb(
                    session.chat_id,
                    "The task completed but produced no output. "
                    "Please re-trigger if you expected results.",
                    session.telegram_message_id,
                    agent_session,
                )
                chat_state.completion_sent = True

            elif action == "deliver":
                # PM outbox drain: if messages are pending in the relay queue,
                # wait briefly for them to be sent before the drafter fires.
                # This prevents the race where PM queues a message but the session
                # completes before the relay processes it (issue #497).
                if session.session_id:
                    try:
                        from bridge.telegram_relay import get_outbox_length

                        for _drain_i in range(20):  # 20 x 100ms = 2s max
                            if get_outbox_length(session.session_id) == 0:
                                break
                            await asyncio.sleep(0.1)
                        # Re-read session for fresh pm_sent_message_ids
                        try:
                            fresh_sessions = list(
                                AgentSession.query.filter(session_id=session.session_id)
                            )
                            if fresh_sessions:
                                fresh_sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
                                agent_session = fresh_sessions[0]
                        except Exception as _reread_err:
                            logger.debug(
                                f"[{session.project_key}] Session re-read after outbox "
                                f"drain failed: {_reread_err}"
                            )
                    except Exception as drain_err:
                        logger.debug(
                            f"[{session.project_key}] Outbox drain check failed: {drain_err}"
                        )

                await send_cb(session.chat_id, msg, session.telegram_message_id, agent_session)
                chat_state.completion_sent = True
                logger.info(
                    f"[{session.project_key}] Output delivered "
                    f"(stop_reason={stop_reason}, {len(msg)} chars)"
                )
                # Stamp response_delivered_at so health check won't re-queue (#918)
                try:
                    if agent_session is not None:
                        agent_session.response_delivered_at = datetime.now(UTC)
                        agent_session.save(update_fields=["response_delivered_at", "updated_at"])
                        logger.info(
                            f"Stamped response_delivered_at for session {session.session_id}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to stamp response_delivered_at for {session.session_id}: {e}"
                    )

        # === Two-tier no-progress detector callbacks (#1036) ===
        # These closures bump per-session ORM fields on each signal. They are
        # passed to BossMessenger so the messenger itself imports nothing from
        # models/ — it just blindly invokes callbacks (notify_* wrappers catch
        # exceptions). Each save is scoped to a single field so it cannot
        # clobber status or any other field.
        def _on_sdk_started(pid: int) -> None:
            handle = _active_sessions.get(session.agent_session_id)
            if handle is not None:
                handle.pid = pid
            try:
                # Persist the harness subprocess PID alongside the SDK heartbeat.
                # Two fields, two lifecycles, same value:
                # - `claude_pid` (#1271): session-lifetime, cleared on terminal
                #   transitions in models/session_lifecycle.py::finalize_session.
                #   Used by the cross-process orphan reaper via
                #   AgentSession.find_by_claude_pid().
                # - `harness_pid` (#1269): subprocess-scoped, paired with
                #   `_on_sdk_finished` below to clear at proc.communicate()
                #   return for THIS subprocess. Multi-spawn turns (primary +
                #   image-dim fallback + stale-UUID fallback) overwrite this
                #   field 3x with their own PIDs. Used by the dashboard
                #   liveness probe.
                session.last_sdk_heartbeat_at = datetime.now(tz=UTC)
                session.claude_pid = pid
                session.harness_pid = pid
                session.save(update_fields=["last_sdk_heartbeat_at", "claude_pid", "harness_pid"])
            except Exception as e:
                logger.warning(
                    "[%s] on_sdk_started save failed (pid=%s): %s",
                    session.session_id,
                    pid,
                    e,
                )

        def _on_sdk_finished() -> None:
            # #1269: clear PID the instant proc.communicate() returns for the
            # harness subprocess. Sibling closure to _on_sdk_started — together
            # they bracket subprocess lifetime so the dashboard's os.kill(pid,0)
            # probe never reads a stale PID that has been recycled by a
            # worker-spawned gh/git/pytest/ruff/MCP subprocess on a busy host.
            try:
                session.harness_pid = None
                session.save(update_fields=["harness_pid"])
            except Exception as e:
                logger.warning(
                    "[%s] on_sdk_finished save failed: %s",
                    session.session_id,
                    e,
                )

        def _on_heartbeat_tick() -> None:
            try:
                session.last_sdk_heartbeat_at = datetime.now(tz=UTC)
                session.save(update_fields=["last_sdk_heartbeat_at"])
            except Exception as e:
                logger.warning(
                    "[%s] on_heartbeat_tick save failed: %s",
                    session.session_id,
                    e,
                )

        messenger = BossMessenger(
            _send_callback=send_to_chat,
            chat_id=session.chat_id,
            session_id=session.session_id,
            on_sdk_started=_on_sdk_started,
            on_sdk_finished=_on_sdk_finished,
            on_heartbeat_tick=_on_heartbeat_tick,
        )

        # Deferred enrichment: process media, YouTube, links, reply chain.
        # Reads enrichment params exclusively from TelegramMessage via telegram_message_key.
        enriched_text = session.message_text
        enrich_has_media = False
        enrich_media_type = None
        enrich_youtube_urls = None
        enrich_non_youtube_urls = None
        enrich_reply_to_msg_id = None
        # sdlc-1297: hold the loaded TelegramMessage to pass into enrich_message,
        # so the worker-side media branch can read media_local_path off the record
        # rather than reaching for a (non-existent) Telethon client.
        trigger_telegram_message = None

        if session.telegram_message_key:
            try:
                from models.telegram import TelegramMessage

                trigger_msgs = list(
                    TelegramMessage.query.filter(msg_id=session.telegram_message_key)
                )
                if trigger_msgs:
                    tm = trigger_msgs[0]
                    trigger_telegram_message = tm
                    enrich_has_media = bool(tm.has_media)
                    enrich_media_type = tm.media_type
                    enrich_youtube_urls = tm.youtube_urls
                    enrich_non_youtube_urls = tm.non_youtube_urls
                    enrich_reply_to_msg_id = tm.reply_to_msg_id
                    logger.debug(
                        f"[{session.project_key}] Resolved enrichment from "
                        f"TelegramMessage {session.telegram_message_key}"
                    )
                else:
                    logger.debug(
                        f"[{session.project_key}] telegram_message_key "
                        f"{session.telegram_message_key} not found, skipping enrichment"
                    )
            except Exception as e:
                logger.debug(f"[{session.project_key}] TelegramMessage lookup failed: {e}")

        # Idempotency guard (Plan IN-1 / Race 1): belt-and-suspenders against
        # double-hydration when the handler already prepended a REPLY THREAD
        # CONTEXT block (e.g. resume-completed branch pre-hydrates synchronously).
        #   Primary:   extra_context["reply_chain_hydrated"] flag stamped by the
        #              bridge handler at enqueue time — explicit and reviewable.
        #   Defensive: REPLY_THREAD_CONTEXT_HEADER substring scan of message_text
        #              — catches sessions enqueued before the flag shipped and
        #              any future code path that pre-hydrates without the flag.
        # Either guard triggering skips the deferred reply-chain fetch.
        from bridge.context import REPLY_THREAD_CONTEXT_HEADER

        if enrich_reply_to_msg_id:
            _extra_ctx = getattr(session, "extra_context", None) or {}
            _flag_hydrated = bool(_extra_ctx.get("reply_chain_hydrated"))
            _header_present = REPLY_THREAD_CONTEXT_HEADER in (session.message_text or "")
            if _flag_hydrated or _header_present:
                logger.debug(
                    f"[{session.project_key}] Reply chain already hydrated by handler; "
                    f"skipping deferred fetch (session={session.session_id}, "
                    f"flag={_flag_hydrated}, header={_header_present})"
                )
                enrich_reply_to_msg_id = None

        if (
            enrich_has_media
            or enrich_youtube_urls
            or enrich_non_youtube_urls
            or enrich_reply_to_msg_id
        ):
            try:
                from bridge.enrichment import enrich_message

                # sdlc-1297: pass the loaded TelegramMessage; the worker no
                # longer needs a Telethon client. Media files are downloaded
                # by the bridge at intake and read from media_local_path here.
                enriched_text = await enrich_message(
                    message_text=session.message_text,
                    telegram_message=trigger_telegram_message,
                    youtube_urls=enrich_youtube_urls,
                    non_youtube_urls=enrich_non_youtube_urls,
                    sender_name=session.sender_name,
                    chat_id=session.chat_id,
                    message_id=session.telegram_message_id,
                )
            except Exception as e:
                logger.warning(f"[{session.project_key}] Enrichment failed, using raw text: {e}")

        # Set back-reference: TelegramMessage.agent_session_id -> this session's agent_session_id
        if session.telegram_message_key:
            try:
                from models.telegram import TelegramMessage

                trigger_msgs = list(
                    TelegramMessage.query.filter(msg_id=session.telegram_message_key)
                )
                if trigger_msgs and not trigger_msgs[0].agent_session_id:
                    trigger_msgs[0].agent_session_id = session.agent_session_id
                    trigger_msgs[0].save()
            except Exception as _xref_err:
                # Non-critical: best-effort cross-reference.
                logger.debug(
                    f"[{session.project_key}] Trigger-message back-reference failed: {_xref_err}"
                )

        # Run agent work directly in the project working directory.
        # Read project config from the session (populated at enqueue time).
        # Transitional fallback: if session.project_config is empty (legacy sessions
        # created before this migration), load from projects.json directly.
        project_config = getattr(session, "project_config", None) or {}
        if not project_config:
            try:
                from bridge.routing import load_config as _load_projects_config

                _all_projects = _load_projects_config().get("projects", {})
                project_config = _all_projects.get(session.project_key, {})
            except Exception as e:
                logger.debug(
                    f"Failed to load project config for {session.project_key} "
                    f"from projects.json (non-fatal): {e}"
                )
        if not project_config:
            project_config = {
                "_key": session.project_key,
                "working_directory": str(working_dir),
                "name": session.project_key,
            }

        # Check the Redis steering queue before starting this agent turn. If the
        # session has pending steering messages (written by steer_session() or
        # the PM), pop the first one and use it as the user input for this turn.
        # This is the mechanism that replaces hardcoded nudge text — any process
        # can push to the Redis steering list (agent.steering) to steer the
        # session externally.
        #
        # Single-consumer invariant: pop_all_steering_messages() drains via
        # sequential LPOPs, not one atomic multi-pop. That is safe only because
        # exactly one process drains a given session's list at a time (this
        # turn-boundary read). Each individual LPOP is still atomic against a
        # concurrent RPUSH, so a steer pushed mid-drain either lands in this
        # pass or sits in the list for the next turn boundary — it is never
        # silently lost. Do not assume whole-drain atomicity in future
        # refactors, and do not add a second concurrent drainer for the same
        # session_id without revisiting this invariant.
        _turn_input = enriched_text
        if agent_session:
            try:
                from agent.steering import pop_all_steering_messages as _pop_all_steering

                steering_msgs = _pop_all_steering(session.session_id)
                if steering_msgs:
                    _turn_input = steering_msgs[0].get("text", "")
                    logger.info(
                        f"[{session.project_key}] Injecting steering message for session "
                        f"{session.session_id}: {_turn_input[:80]!r} "
                        f"({len(steering_msgs)} queued, used first)"
                    )
                    if len(steering_msgs) > 1:
                        # Re-queue remaining messages for future turns
                        from agent.steering import push_steering_message as _push_steering

                        for _remaining in steering_msgs[1:]:
                            _push_steering(
                                session.session_id,
                                _remaining.get("text", ""),
                                _remaining.get("sender", "unknown"),
                                is_abort=_remaining.get("is_abort", False),
                                target_agent=_remaining.get("target_agent"),
                            )
            except Exception as _steer_err:
                logger.debug(
                    f"[{session.project_key}] Steering check failed (non-fatal): {_steer_err}"
                )

        # Fix B (issue #1741): fail loud on a messageless task. A None/empty/"None"
        # first message means the originating intent never reached this record (the
        # #1460 sdlc-local silent no-op). Guard the PRE-SCOPE value: once
        # build_harness_turn_input wraps _turn_input in the SCOPE header block, the
        # bare "None" is buried inside "MESSAGE: None" and can never be detected by a
        # strip()=="None" check. Catch it here, before the runner is constructed.
        _pre_scope = "" if _turn_input is None else str(_turn_input).strip()
        if _pre_scope == "" or _pre_scope == "None":
            _guard_reason = f"empty_turn_input: _turn_input stripped to {_turn_input!r}"
            logger.error(
                "[executor-guard] session %s: refusing empty turn input — %s",
                session.agent_session_id,
                _guard_reason,
            )
            try:
                from models.session_lifecycle import (  # noqa: PLC0415
                    StatusConflictError,
                    finalize_session,
                )

                finalize_session(session, "failed", reason=_guard_reason)
            except StatusConflictError:
                logger.info(
                    "[executor-guard] session %s already terminal, skipping finalize",
                    session.agent_session_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[executor-guard] last-resort status save for session %s: %s",
                    session.agent_session_id,
                    exc,
                )
                try:
                    session.status = "failed"
                    session.save(update_fields=["status", "updated_at"])
                except Exception as last_resort_err:  # noqa: BLE001
                    logger.warning(
                        "[executor-guard] last-resort status save failed for "
                        "session %s (non-fatal): %s",
                        session.agent_session_id,
                        last_resort_err,
                        exc_info=True,
                    )
            return

        # All session types route through the headless session runner (plan
        # #1924): one ``claude -p`` stream-json subprocess per turn via
        # SessionRunner/HeadlessRoleDriver, reusing the preserved harness in
        # ``agent/sdk_client.py``. There is one execution transport and no
        # seam. See docs/features/headless-session-runner.md.
        from agent.sdk_client import (
            _extract_sdlc_env_vars,
            _resolve_compose_args,
            _resolve_sentry_auth_token,
            build_harness_turn_input,
        )
        from agent.session_runner import (
            ResumeContext,
            SessionRunner,
            SessionRunnerAdapter,
        )

        project_key = project_config.get("_key", "valor") if project_config else "valor"
        _classification = (
            getattr(agent_session, "classification_type", None) if agent_session else None
        )
        _is_cross_repo = project_key != "valor"

        # Cross-repo GH_REPO resolution (issue #375), mirrored from the
        # deleted ValorAgent path (main sdk_client.py ~line 3930) and from
        # the identical org/repo lookup in build_harness_turn_input's
        # cross-repo prefix injection (agent/session_runner/harness/claude.py).
        _gh_repo: str | None = None
        if _classification == ClassificationType.SDLC and _is_cross_repo and project_config:
            _github_config = project_config.get("github", {})
            _gh_org = _github_config.get("org", "")
            _gh_name = _github_config.get("repo", "")
            if _gh_org and _gh_name:
                _gh_repo = f"{_gh_org}/{_gh_name}"

        _harness_input = await build_harness_turn_input(
            message=_turn_input,
            session_id=session.session_id,
            sender_name=session.sender_name,
            chat_title=session.chat_title,
            project=project_config,
            task_list_id=task_list_id,
            session_type=_session_type,
            sender_id=session.sender_id,
            classification=_classification,
            is_cross_repo=_is_cross_repo,
        )

        logger.info(
            f"{log_prefix} Routing {_session_type or 'unknown'} session to the "
            "headless session runner"
        )

        # Streaming chunks from the CLI harness are suppressed for all session types
        # (Telegram and email). Forwarding them bypasses the nudge loop and sends
        # mid-sentence fragments directly. BackgroundTask delivers the final result instead.

        # PM and Teammate sessions set VALOR_PARENT_SESSION_ID so child subprocesses
        # (spawned via valor_session create --parent or via Agent tool) can link their
        # AgentSession records back to this session in user_prompt_submit.py.
        _harness_env: dict[str, str] = {
            "AGENT_SESSION_ID": session.agent_session_id or "",
            "CLAUDE_CODE_TASK_LIST_ID": task_list_id or "",
        }
        # SESSION_TYPE drives pre_tool_use hook behavior (_is_pm_session in
        # agent/hooks/pre_tool_use.py:97-99). Without it, PM Bash restrictions
        # are silently disabled in the harness subprocess (issue #1148).
        if _session_type:
            _harness_env["SESSION_TYPE"] = _session_type
        if _session_type in (SessionType.ENG, SessionType.TEAMMATE) and session.agent_session_id:
            _harness_env["VALOR_PARENT_SESSION_ID"] = session.agent_session_id
        # PM/Teammate need Telegram + Sentry auth so tools/send_message.py and
        # sentry-cli work without manual export. chat_id comes from the project
        # config.
        if _session_type in (SessionType.ENG, SessionType.TEAMMATE):
            if session.chat_id:
                _harness_env["TELEGRAM_CHAT_ID"] = str(session.chat_id)
            _sentry_token = _resolve_sentry_auth_token()
            if _sentry_token:
                _harness_env["SENTRY_AUTH_TOKEN"] = _sentry_token

        # SDLC context injection: pre-resolve session fields (PR/branch/slug/
        # plan/issue) as SDLC_* env vars so skills can reference $SDLC_PR_NUMBER
        # etc. instead of guessing (issue #420). Restored call site after the
        # ValorAgent deletion dropped it (issue #2039) — this is the sole point
        # in the harness path where session.session_id (bridge session_id) and
        # the resolved _gh_repo are both available before _harness_env reaches
        # the subprocess env= via SessionRunner -> HeadlessRoleDriver. Applied
        # last so it never clobbers the more-specific overrides set above, and
        # env.update mirrors main's ValorAgent._create_options ordering.
        _sdlc_env = _extract_sdlc_env_vars(session.session_id, _gh_repo)
        if _sdlc_env:
            _harness_env.update(_sdlc_env)

        # D1 precedence cascade: session.model > settings > codebase default.
        # Applied to the runner's PM subprocess; the Dev role runs as a
        # subagent inside the PM session (D1, plan #1924).
        _effective_model = _resolve_session_model(agent_session)

        # Persona is now delivered entirely via the prime commands
        # (the role prime slash commands the HeadlessRoleDriver prepends on
        # the first turn). The compose_system_prompt / --append-system-prompt
        # path has been removed (issue #1692). Email-spawned sessions receive
        # the teammate prime; session_type drives the prime selection inside
        # the runner. The _resolve_compose_args resolver is preserved for
        # future prime-command selection keyed on email.persona.
        _composed_persona, _composed_access_level, _ = _resolve_compose_args(
            session_type=_session_type,
            project=project_config,
            transport=_transport,
            chat_title=None,
            is_dm=False,
        )
        logger.info(
            f"{log_prefix} Resolved persona for session={session.session_id}: "
            f"{_composed_persona.value if _composed_persona else '<none>'} "
            f"(source=prime-command; no system-prompt injection)"
        )

        # Build the SessionRunnerAdapter + SessionRunner (plan #1924). The
        # adapter is single-shot (one adapter, one runner.run, one
        # user-message). It resolves the bridge send_cb once at construction
        # — keyed by (project_key, transport) per the repo's delivery-channel
        # convention — and publishes mid-loop `[/user]` / `[/complete]`
        # payloads through the registered callback. No mid-loop delivery
        # surfaces via BackgroundTask — `do_work` returns `""` and
        # BackgroundTask has `send_result=False` so the harness layer does
        # NOT double-deliver.
        # The runner receives the per-session env (SESSION_TYPE for the
        # pre_tool_use PM Bash restrictions, AGENT_SESSION_ID for hook
        # attribution, CLAUDE_CODE_TASK_LIST_ID for task-list isolation,
        # VALOR_PARENT_SESSION_ID for child-session linking) and the
        # D1-resolved model. No system prompt is passed — persona arrives
        # via the prime commands only (issue #1692).
        _runner_adapter = SessionRunnerAdapter(
            agent_session=agent_session,
            project_key=project_key,
            transport=_transport or "telegram",
        )

        # Four-scalar resume (D3, spike #1928): hand the persisted scalars to
        # the runner, which validates them (UUID shape, cwd-scoped lookup,
        # dev_agent_id shape) and consumes them — seed `--resume`, skip the
        # prime, reintroduce the SAME dev agent. Any invalid scalar discards
        # the whole context and cold-starts with the prime. Only built when a
        # prior claude session UUID exists; a fresh session passes resume=None.
        _resume_ctx = None
        _prior_uuid = getattr(agent_session, "claude_session_uuid", None) if agent_session else None
        if _prior_uuid:
            _resume_ctx = ResumeContext(
                claude_session_id=_prior_uuid,
                dev_agent_id=getattr(agent_session, "dev_agent_id", None),
                runner_cwd=getattr(agent_session, "runner_cwd", None),
                claude_version=getattr(agent_session, "claude_version", None),
            )

        _runner = SessionRunner(
            agent_session=agent_session,
            adapter=_runner_adapter,
            working_dir=str(working_dir),
            session_type=_session_type,
            model=_effective_model,
            session_env=_harness_env,
            resume=_resume_ctx,
        )

        # The message the runner receives: the full-context turn input, so
        # resumed (reply-to) threads keep their conversation context. On a
        # resumed session this IS the reply/steer — the runner injects it as
        # the resumed session's first message. The runner self-emits
        # turn_start/turn_end telemetry per turn; the executor must not
        # double-emit.
        _runner_message = _harness_input

        async def do_work() -> str:
            await _runner.run(_runner_message)
            return ""

        # Pass working_dir so BackgroundTask._watchdog can detect a vanished
        # worktree mid-run (issue #1357). Pre-existing local `working_dir` is
        # the same path used to spawn the SDK subprocess earlier in this
        # function, so the watchdog observes the exact directory the SDK is
        # holding open as cwd. project_key is supplied here (not resolved
        # inside messenger.py) to preserve the messenger's ORM-free invariant
        # — see tests/unit/test_messenger_callbacks.py::
        # TestMessengerArchitecturalBoundary.
        task = BackgroundTask(
            messenger=messenger,
            working_dir=str(working_dir),
            project_key=getattr(session, "project_key", None),
        )
        # `send_result=False` is the right call: the runner adapter publishes
        # `[/user]` and `[/complete]` payloads mid-loop through the bridge
        # callback. Returning "" keeps the harness layer from double-delivering.
        await task.run(do_work(), send_result=False)

        # === Two-tier no-progress detector: populate cancellable task ref (#1036) ===
        # Now that BackgroundTask.run() has created its session-scoped task at
        # messenger.py:198, record it on the handle so the health-check kill path
        # targets the SDK work — NOT the worker-loop task (plan spike-1; #1039
        # review). Until this line the handle's task is None, and the health
        # check must no-op on cancel.
        _handle_for_task_ref = _active_sessions.get(session.agent_session_id)
        if _handle_for_task_ref is not None and task._task is not None:
            _handle_for_task_ref.task = task._task

        # Wait for the background task to complete, with periodic heartbeats.
        # The loop now ticks at HEARTBEAT_WRITE_INTERVAL (60s) for the two-tier
        # no-progress detector's queue-layer heartbeat (#1036). Every N ticks
        # (where N*interval == CALENDAR_HEARTBEAT_INTERVAL, i.e. every 25 min)
        # it also fires the calendar heartbeat and updated_at save to keep the
        # prior behavior intact.
        async def _heartbeat_loop():
            elapsed = 0
            while not task._task.done():
                await asyncio.sleep(HEARTBEAT_WRITE_INTERVAL)
                elapsed += HEARTBEAT_WRITE_INTERVAL
                if task._task.done():
                    break

                # Tier 1 queue-layer heartbeat (#1036): write every tick.
                try:
                    session.last_heartbeat_at = datetime.now(tz=UTC)
                    session.save(update_fields=["last_heartbeat_at"])
                except Exception as hb_err:
                    logger.warning(
                        "[%s] last_heartbeat_at save failed: %s",
                        session.session_id,
                        hb_err,
                    )

                # Issue-lock renewal (issue #1954): tier-1 (60s) block, NOT the
                # 25-min calendar block below -- see _tick_issue_lock_renewal
                # docstring for why that cadence would blow past
                # ISSUE_LOCK_TTL_SECONDS (default 1800s) and let the lock expire mid-cycle.
                _tick_issue_lock_renewal(session, agent_session)

                # Calendar + updated_at heartbeat on the 25-min cadence (preserved).
                if elapsed >= CALENDAR_HEARTBEAT_INTERVAL:
                    elapsed = 0
                    # Fall back to project key when there's no slug so ad-hoc
                    # (Telegram-originated) sessions still record calendar activity.
                    cal_slug = session.slug or session.project_key
                    if cal_slug:
                        asyncio.create_task(
                            _calendar_heartbeat(cal_slug, project=session.project_key)
                        )
                    if agent_session:
                        try:
                            agent_session.updated_at = datetime.now(tz=UTC)
                            agent_session.save(update_fields=["updated_at"])
                        except Exception as hb_err:
                            logger.warning(
                                "[%s] updated_at heartbeat save failed: %s",
                                session.session_id,
                                hb_err,
                            )

        heartbeat = asyncio.create_task(_heartbeat_loop())
        try:
            # Await the actual task future -- propagates exceptions immediately
            await task._task
        except Exception as e:
            # Exception escaped BackgroundTask._run_work's handler
            if not task.error:
                task._error = e
                logger.error(
                    "[%s] Task crashed outside _run_work: %s",
                    session.session_id,
                    e,
                )
        finally:
            heartbeat.cancel()

        # Failure notification (#1877 defect #2). A running->failed transition
        # used to be silent — no Telegram message at all. Best-effort, deduped,
        # and never blocking finalization; see `_maybe_send_failure_notice`.
        if task.error and not chat_state.defer_reaction:
            await _maybe_send_failure_notice(messenger, session.session_id)

        # Update session status in Redis via AgentSession
        # When auto-continue deferred, session is still active (not completed)
        # Bug A (issue #1730): complete_transcript is confirmed to fire on the
        # deferred-self-draft completion path (defer_reaction=False, since
        # _inject_self_draft_steering does NOT call _enqueue_nudge).  However, if
        # complete_transcript itself throws, the session can ghost as ``running`` for
        # up to the health-check TTL (32 min in the production timeline).  The
        # defensive fallback below ensures a terminal finalize always lands on the
        # COMPLETION/DELIVERY exit when complete_transcript fails.  Scoped to this
        # exit only — the nudge / unconsumed-steering re-enqueue path is gated by
        # chat_state.defer_reaction=True and the CancelledError path is health-checker-
        # owned; neither is touched here.
        if agent_session:
            try:
                from bridge.session_transcript import complete_transcript

                # Non-clean runner exits (error/exception/timeout — the runner
                # never raises) finalize as failed, never completed (#1916).
                final_status = (
                    "active"
                    if chat_state.defer_reaction
                    else _runner_final_status(task.error, agent_session)
                )
                if not chat_state.defer_reaction:
                    complete_transcript(session.session_id, status=final_status)
                # else: nudge path — _enqueue_nudge already wrote the authoritative
                # post-nudge state (status=pending, auto_continue_count, nudge event,
                # new message_text). Do NOT save the stale `agent_session` local here;
                # it would clobber the nudge. updated_at is refreshed on the next
                # worker pop. See #898 for the regression history.
            except Exception as e:
                logger.warning(
                    f"AgentSession update failed for session {session.agent_session_id} "
                    f"session {session.session_id} (operation: finalize status to "
                    f"{_runner_final_status(task.error, agent_session)}): {e}"
                )
                # No fallback-finalize here: the unconditional completion-exit
                # guard below (after this whole if/else block) re-reads the
                # authoritative session and finalizes it if still `running`. It
                # subsumes what used to be a duplicate defensive fallback in this
                # except-only branch -- see the guard's comment for the full
                # rationale (round-2 CONCERN 3: this exception-only branch never
                # covered the `else:` / agent_session-is-None exit below anyway).
        else:
            # agent_session lookup returned None (race on status="running" filter,
            # e.g. after health-check recovery). Finalize using outer `session`
            # param directly to prevent session from staying in `running` state
            # permanently. Uses complete_transcript() (not finalize_session()
            # directly) to ensure the SESSION_END transcript marker is written —
            # complete_transcript queries by session_id alone (no status filter),
            # so it works here. See issue #917.
            if not chat_state.defer_reaction:
                try:
                    from bridge.session_transcript import complete_transcript
                    from models.session_lifecycle import StatusConflictError

                    # agent_session is None here (lookup race) — degrades to
                    # task_error-only gating inside _runner_final_status.
                    final_status = _runner_final_status(task.error, None)
                    complete_transcript(session.session_id, status=final_status)
                    logger.info(
                        "Fallback finalization: session %s → %s (agent_session was None)",
                        session.agent_session_id,
                        final_status,
                    )
                except StatusConflictError:
                    # CAS conflict = another process already finalized. This is success.
                    logger.info(
                        "Fallback finalization skipped: session %s already transitioned "
                        "(CAS conflict — expected)",
                        session.agent_session_id,
                    )
                except Exception as e:
                    logger.warning(
                        "Fallback finalization failed for session %s: %s",
                        session.agent_session_id,
                        e,
                    )

        # Unconditional completion-exit finalize guard (Defect B, #2007).
        #
        # (a) Scope: this covers the non-deferred completion exit only, gated by
        #     `not chat_state.defer_reaction` -- the nudge / unconsumed-steering
        #     re-enqueue path (defer_reaction=True) is untouched, since
        #     `_enqueue_nudge` already writes the authoritative post-nudge state
        #     (status=pending) itself; finalizing here would clobber it.
        #
        # (b) Placement: this runs AFTER the entire `if agent_session: / else:`
        #     block above closes -- deliberately NOT nested inside the
        #     `if agent_session:` branch. A prior version of this fallback lived
        #     only inside that branch's `except Exception` handler, which meant
        #     the `else:` exit (agent_session lookup returned None, e.g. a race
        #     on the status="running" filter -- see #917) had no re-read+finalize
        #     backstop at all: if `complete_transcript` silently no-op'd there
        #     instead of raising, the authoritative record stayed `running`
        #     forever. Placing the guard after the whole if/else covers both exits.
        #
        # (c) Unconditional: this guard runs every time regardless of whether
        #     `complete_transcript` succeeded, raised, or (in the `else` branch)
        #     already ran its own fallback -- it re-reads the authoritative
        #     record fresh and only acts if it is still `running`, making it a
        #     safe no-op on the ordinary happy path. It subsumes the old
        #     exception-only fallback that used to live inside the
        #     `if agent_session:` branch (that one only fired when
        #     `complete_transcript` itself raised); this is the single
        #     finalize-guarantee mechanism for the completion exit now.
        if not chat_state.defer_reaction:
            try:
                from models.session_lifecycle import (  # noqa: PLC0415
                    StatusConflictError,
                    finalize_session,
                    get_authoritative_session,
                )

                _auth = get_authoritative_session(session.session_id)
                if _auth is not None and _auth.status == "running":
                    _guard_status = _runner_final_status(task.error, agent_session)
                    finalize_session(
                        _auth,
                        _guard_status,
                        reason="unconditional completion-exit finalize guard (#2007)",
                    )
                    logger.info(
                        "[executor] Completion-exit guard finalized session %s → %s",
                        session.session_id,
                        _guard_status,
                    )
            except StatusConflictError:
                # CAS conflict = another actor (complete_transcript, a concurrent
                # finalize, the health-checker) already finalized this session.
                # Treat as success -- do not re-raise, do not log as error.
                pass
            except Exception as _guard_err:
                logger.warning(
                    "[executor] Completion-exit finalize guard failed for %s: %s",
                    session.session_id,
                    _guard_err,
                )

        # Schedule post-session memory extraction (hotfix #1055) — fire-and-forget.
        #
        # CRITICAL: synchronous call (no await, no gather). Any awaiting here would
        # re-couple extraction latency to the PM nudge below, regressing the 6-hour
        # stall observed in #1055 and the #987 ordering invariant.
        #
        # Runs AFTER both complete_transcript paths above (happy path at ~L1320
        # and the #917 fallback at ~L1346). Extraction runs in the background;
        # its completion or failure does not delay the eng nudge. See
        # drain_pending_extractions() for shutdown wiring.
        #
        # Fix 2 (#1822): capture the trivial-session gate signals synchronously
        # HERE (before teardown clears the in-memory turn-count tracker) and pass
        # them by value. turn_count is re-fetched from the persisted AgentSession
        # (the in-scope session.turn_count is a stale instance); origin comes from
        # the in-scope session's initial_telegram_message.
        _ext_turn_count = _capture_turn_count(session.session_id)
        _ext_is_conversational = _is_conversational_session(session)
        _schedule_post_session_extraction(
            session.session_id,
            task._result or "",
            turn_count=_ext_turn_count,
            is_conversational=_ext_is_conversational,
        )

        # Save session snapshot for error cases
        if task.error:
            save_session_snapshot(
                session_id=session.session_id,
                event="error",
                project_key=session.project_key,
                branch_name=branch_name,
                task_summary=f"Session {session.agent_session_id} failed: {task.error}",
                extra_context={
                    "agent_session_id": session.agent_session_id,
                    "error": str(task.error),
                    "sender": session.sender_name,
                    "correlation_id": cid,
                },
                working_dir=str(working_dir),
            )

        # Clean up steering queue — re-enqueue unconsumed messages as a continuation
        try:
            from agent.steering import pop_all_steering_messages

            leftover = pop_all_steering_messages(session.session_id)
            if leftover:
                texts = [f"  [{m.get('sender', '?')}]: {m.get('text', '')[:500]}" for m in leftover]
                logger.warning(
                    f"[{session.project_key}] {len(leftover)} unconsumed steering "
                    f"message(s) for session {session.session_id} — re-enqueuing as continuation:\n"
                    + "\n".join(texts)
                )
                try:
                    from agent.agent_session_queue import enqueue_agent_session

                    combined_text = "\n\n".join(
                        m.get("text", "").strip() for m in leftover if m.get("text", "").strip()
                    )
                    _summary = (
                        getattr(agent_session, "context_summary", None)
                        or "This continues a previously completed session."
                    )
                    augmented = f"[Prior session context: {_summary}]\n\n{combined_text}"
                    # Reuse the slug already checked out in working_dir (if it's a
                    # worktree) so the continuation's synthetic-slug fallback
                    # (session_executor.py's `is_synthetic_slug` branch) doesn't
                    # mint a fresh slug from the *new* agent_session_id — that
                    # mismatches the branch already checked out here and trips
                    # the worktree-branch-guard (issue #1377), killing the
                    # continuation instantly instead of resuming it.
                    continuation_slug = (
                        working_dir.name if WORKTREES_DIR in str(working_dir) else None
                    )
                    await enqueue_agent_session(
                        project_key=session.project_key,
                        session_id=session.session_id,
                        working_dir=str(working_dir),
                        message_text=augmented,
                        sender_name=leftover[0].get("sender", session.sender_name or ""),
                        chat_id=session.chat_id,
                        telegram_message_id=session.telegram_message_id,
                        chat_title=session.chat_title,
                        priority=session.priority or "normal",
                        sender_id=session.sender_id,
                        session_type=session.session_type or "eng",
                        project_config=getattr(session, "project_config", None),
                        slug=continuation_slug,
                    )
                    logger.info(
                        f"[{session.project_key}] Re-enqueued {len(leftover)} steering "
                        f"message(s) as continuation for session {session.session_id} "
                        f"(session_type={session.session_type})"
                    )
                except Exception as re_enqueue_err:
                    logger.warning(
                        f"[{session.project_key}] Failed to re-enqueue steering messages "
                        f"for session {session.session_id} (dropping): {re_enqueue_err}"
                    )
        except Exception as e:
            logger.debug(f"Steering queue cleanup failed (non-fatal): {e}")

        # Set reaction based on result and delivery state
        # Skip if a continuation session was enqueued (defer reaction to that session)
        if react_cb and not chat_state.defer_reaction:
            # Teammate sessions: clear the processing reaction instead of setting completion emoji
            if (
                agent_session
                and getattr(agent_session, "session_type", None) == SessionType.TEAMMATE
                and not task.error
            ):
                emoji = None  # Clear reaction
            elif task.error:
                emoji = REACTION_ERROR
            elif _is_non_clean_runner_exit(agent_session):
                # Non-clean runner exit_reason (error, exception, timeout, etc.)
                # → ERROR reaction regardless of whether the session communicated.
                # Only applies when exit_reason is explicitly set to a non-clean value.
                emoji = REACTION_ERROR
            elif messenger.has_communicated() or getattr(
                agent_session, "user_facing_routed", False
            ):
                # The session runner delivers [/user]/[/complete] payloads
                # through SessionRunnerAdapter, never through messenger.send(),
                # so has_communicated() stays False even on a real delivery.
                # The adapter's publish_exit_summary sets
                # agent_session.user_facing_routed=True when delivery succeeded;
                # OR'ing it here makes the emoji branch mean what it should
                # (issue #1647).
                emoji = REACTION_COMPLETE
            else:
                emoji = REACTION_SUCCESS
            try:
                await react_cb(session.chat_id, session.telegram_message_id, emoji)
            except Exception as e:
                logger.warning(f"Failed to set reaction: {e}")

        # Auto-mark session as done after successful completion
        # Skip when auto-continue deferred — continuation session will handle
        # cleanup — and on non-clean runner exits (a failed run must not
        # mark_work_done or auto-delete its branch as if it succeeded).
        if (
            not task.error
            and not _is_non_clean_runner_exit(agent_session)
            and not chat_state.defer_reaction
        ):
            try:
                from agent.branch_manager import mark_work_done
                from agent.worktree_manager import (  # noqa: PLC0415
                    merged_via_ancestor,
                    safe_delete_branch,
                )

                mark_work_done(working_dir, branch_name)
                # Also delete the session branch to keep git clean — guarded by
                # the unmerged-branch guard (issue #1646): use merged_via_ancestor
                # since this path runs at session completion before any PR merge.
                branch_del = safe_delete_branch(
                    str(working_dir),
                    branch_name,
                    predicate=merged_via_ancestor,
                    force=False,
                )
                if branch_del["deleted"]:
                    logger.info(
                        f"[{session.project_key}] Auto-marked session done "
                        f"and cleaned up branch {branch_name}"
                    )
                elif branch_del["skipped_unmerged"]:
                    logger.warning(
                        "[unmerged-branch-guard] branch '%s' preserved"
                        " — work not yet merged to main",
                        branch_name,
                    )
                    logger.info(
                        f"[{session.project_key}] Auto-marked session done "
                        f"(branch {branch_name} preserved — unmerged)"
                    )
                else:
                    logger.info(
                        f"[{session.project_key}] Auto-marked session done "
                        f"(branch {branch_name} cleanup error: {branch_del.get('error')})"
                    )
            except Exception as e:
                logger.warning(f"[{session.project_key}] Failed to auto-mark session done: {e}")

            # Save session snapshot on successful completion
            save_session_snapshot(
                session_id=session.session_id,
                event="complete",
                project_key=session.project_key,
                branch_name=branch_name,
                task_summary=f"Session {session.agent_session_id} completed successfully",
                extra_context={
                    "agent_session_id": session.agent_session_id,
                    "sender": session.sender_name,
                    "correlation_id": cid,
                },
                working_dir=str(working_dir),
            )
        elif chat_state.defer_reaction:
            logger.info(
                f"[{session.project_key}] Skipping session cleanup — "
                f"continuation session enqueued (auto-continue {chat_state.auto_continue_count})"
            )
    finally:
        # === Two-tier no-progress detector cleanup (#1036) ===
        # Always pop the registry entry, regardless of how the session body exited
        # (normal return, exception, CancelledError). This prevents leaking entries
        # into _active_sessions across sessions on the same worker.
        if _session_id_for_registry:
            _active_sessions.pop(_session_id_for_registry, None)

        # === Synthetic-slug worktree cleanup (issue #1272) ===
        # Slugless eng sessions get a synthesized slug ``dev-{aid[:8]}`` and a
        # worktree provisioned for them above. Without an explicit cleanup
        # hook, those worktrees linger forever — ``prune_worktrees()`` only
        # runs ``git worktree prune`` (removes references, not directories)
        # and ``cleanup_after_merge()`` is normally only triggered by a PR
        # merge. Synthetic-slug sessions may never open a PR. Match the
        # exact ``dev-{8 hex chars}`` shape so we never touch a real slug.
        try:
            _slug_for_cleanup = locals().get("slug")
            if (
                _slug_for_cleanup
                and isinstance(_slug_for_cleanup, str)
                and re.match(r"^dev-[0-9a-f]{8}$", _slug_for_cleanup)
            ):
                from agent.worktree_manager import (  # noqa: PLC0415
                    cleanup_after_merge,
                    resolve_main_repo_root,
                )

                _wd = locals().get("working_dir")
                if _wd is not None:
                    # Reap-failed marker skip (Fix 3, issue #1938): the runner's
                    # ``_run_one_turn`` finally SYNCHRONOUSLY reaps + confirms its
                    # process group before this cleanup runs (finally-ordering
                    # guarantee), so the common case is safe. The ONE residual is
                    # a pathological unkillable/D-state group the ~1s SIGKILL
                    # confirm could not verify dead — there the runner wrote a
                    # durable ``runner_reap_failed`` session event. SKIP deletion
                    # when present, so no worktree is removed under a possibly-live
                    # child. Manual reclamation: ``git worktree prune`` + dir
                    # removal.
                    if _session_recorded_reap_failure(session.agent_session_id):
                        logger.warning(
                            "[synthetic-slug] SKIPPING worktree cleanup for %s — "
                            "runner_reap_failed marker present (subprocess group could "
                            "not be confirmed dead). Reclaim manually: `git worktree "
                            "prune` + remove the worktree dir %r.",
                            _slug_for_cleanup,
                            _wd,
                        )
                    else:
                        _repo_for_cleanup = resolve_main_repo_root(_wd)
                        cleanup_result = cleanup_after_merge(_repo_for_cleanup, _slug_for_cleanup)
                        logger.info(
                            f"[synthetic-slug] Cleaned up worktree+branch for "
                            f"{_slug_for_cleanup}: {cleanup_result}"
                        )
        except Exception as cleanup_err:
            # Cleanup failures must NEVER propagate as session failures.
            logger.warning(
                f"[synthetic-slug] Cleanup failed for synthetic slug (non-fatal): {cleanup_err}"
            )

        # === Defensive PID clear (#1269) ===
        # Idempotent backstop for the abnormal-termination path where
        # `_on_sdk_finished` could not fire (worker crash inside the harness
        # loop, CancelledError propagation before proc.communicate() returns).
        # No-op when the field is already None (the common case).
        try:
            if getattr(session, "harness_pid", None) is not None:
                session.harness_pid = None
                session.save(update_fields=["harness_pid"])
        except Exception as _pid_clear_err:
            logger.warning(
                "[%s] defensive harness_pid clear failed: %s",
                getattr(session, "session_id", "?"),
                _pid_clear_err,
            )
