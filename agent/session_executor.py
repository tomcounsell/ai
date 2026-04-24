"""Core session execution: CLI harness subprocess lifecycle, turn-boundary steering,
nudge/re-enqueue paths, and calendar heartbeat."""

import asyncio
import logging
import os  # noqa: F401
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from agent.constants import REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS
from agent.output_router import NUDGE_MESSAGE, SendToChatResult
from agent.session_completion import (
    _complete_agent_session,  # noqa: F401
    _diagnose_missing_session,
    _handle_dev_session_completion,
)
from agent.session_health import HEARTBEAT_WRITE_INTERVAL
from agent.session_logs import save_session_snapshot
from agent.session_revival import _session_branch_name
from agent.session_state import (
    SessionHandle,
    _active_sessions,
)
from agent.worktree_manager import WORKTREES_DIR, validate_workspace
from config.enums import SessionType
from config.settings import settings
from models.agent_session import AgentSession
from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

logger = logging.getLogger(__name__)


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


def _tick_backstop_check_compaction(
    session: AgentSession,
    agent_session: AgentSession | None,
) -> None:
    """SDK-tick backstop for missed PreCompact hook events (issue #1127).

    The primary compaction signal is the PreCompact hook
    (``agent/hooks/pre_compact.py``), which writes
    ``AgentSession.last_compaction_ts``. But hooks can fail: the SDK may skip
    a hook under internal error conditions, a hook may be deregistered by an
    unrelated code path, or a PreCompact event may fire so close to subprocess
    termination that the hook's async task never completes.

    This backstop detects compaction from the executor side by watching for a
    *drop* in ``ResultMessage.num_turns`` across consecutive ticks. A turn-
    count drop is the SDK's observable signature of a compaction that rewrote
    the conversation history. On detection, we arm the 30s nudge guard by
    writing ``last_compaction_ts`` + bumping ``compaction_skipped_count`` via
    a partial save. We do NOT attempt a recovery-path JSONL snapshot — the
    hook is the only place snapshots are taken.

    All failures are swallowed. The backstop MUST NOT crash the executor.
    """
    try:
        import time as _time

        from agent.sdk_client import get_turn_count

        if not session or not getattr(session, "session_id", None):
            return
        current_count = get_turn_count(session.session_id)
        if current_count is None:
            return  # No ResultMessage seen yet for this session — nothing to compare
        prior_count = getattr(session, "_last_observed_message_count", None)
        # Always update the tracker before early-returning so the next tick
        # has a baseline to compare against.
        session._last_observed_message_count = current_count
        if prior_count is None or current_count >= prior_count:
            return  # Steady or increasing — no compaction detected
        # Drop observed — backstop-detected compaction.
        if agent_session is None:
            logger.warning(
                "pre_compact hook appears to have missed a compaction for %s — "
                "backstop detected num_turns drop %s -> %s but no AgentSession "
                "available to arm the guard",
                session.session_id,
                prior_count,
                current_count,
            )
            return
        try:
            agent_session.last_compaction_ts = _time.time()
            current_skipped = int(getattr(agent_session, "compaction_skipped_count", 0) or 0)
            agent_session.compaction_skipped_count = current_skipped + 1
            agent_session.save(update_fields=["last_compaction_ts", "compaction_skipped_count"])
            logger.warning(
                "pre_compact hook appears to have missed a compaction for %s "
                "(num_turns %s -> %s) — backstop armed nudge guard",
                session.session_id,
                prior_count,
                current_count,
            )
        except Exception as exc:  # noqa: BLE001 - backstop must never crash executor
            logger.warning(
                "pre_compact backstop: AgentSession save failed for %s: %s",
                session.session_id,
                exc,
            )
    except Exception as exc:  # noqa: BLE001 - outer guard for any unexpected failure
        logger.warning(
            "pre_compact backstop: unexpected failure for %s: %s",
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


def _schedule_post_session_extraction(session_id: str, response_text: str) -> None:
    """Fire-and-forget post-session memory extraction (hotfix #1055).

    Synchronous — creates and registers an ``asyncio.create_task``; does NOT
    await it. Preserves the #987 ordering invariant:
    ``_handle_dev_session_completion`` must run before this extraction task
    completes, so the PM nudge fires promptly while extraction is still pending.

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

            await run_post_session_extraction(session_id, response_text)
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
    """Write a steering message to a session's queued_steering_messages.

    Any process can call this to inject a message into a running or pending
    session. The worker checks queued_steering_messages between turns and
    injects any pending messages as user input for the next SDK turn.

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

        session.push_steering_message(message)
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
      * Three messenger callbacks (``on_sdk_started``, ``on_heartbeat_tick``,
        ``on_stdout_event``) bump per-session ORM fields; the messenger
        itself imports nothing from ``models/``.
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
            branch_name = resolved_branch
            # If branch resolution says we need a worktree and working_dir isn't one
            if needs_wt and WORKTREES_DIR not in str(working_dir):
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
                    if _stype == "dev":
                        # Dev sessions with a slug MUST have worktree isolation.
                        # Falling back to the main checkout would contaminate it.
                        # See issue #887: session-isolation-bypass incident (2026-04-10).
                        logger.critical(
                            f"[branch-mapping] FATAL: Failed to create worktree for "
                            f"dev session slug={slug}: {e} — refusing to proceed in "
                            f"main checkout to prevent contamination"
                        )
                        raise RuntimeError(
                            f"Worktree provisioning failed for dev session "
                            f"slug={slug}: {e}. Refusing to run in main checkout."
                        ) from e
                    else:
                        logger.warning(
                            f"[branch-mapping] Failed to create worktree for "
                            f"slug={slug}: {e} — using original working dir"
                        )
        else:
            branch_name = _session_branch_name(session.session_id)

        # Main-checkout protection guard (issue #887): dev sessions with a slug
        # must NEVER run in the repo root. If worktree provisioning was skipped
        # or silently failed, catch it here before any git operations run.
        _stype = getattr(session, "session_type", None)
        if _stype == "dev" and slug and WORKTREES_DIR not in str(working_dir):
            logger.critical(
                f"[worktree-guard] Dev session {session.session_id} with slug={slug} "
                f"resolved to main checkout ({working_dir}). Refusing to proceed — "
                f"this would contaminate the shared working directory. "
                f"See issue #887."
            )
            raise RuntimeError(
                f"Dev session with slug={slug} must run in a worktree, "
                f"but working_dir={working_dir} is not a worktree. "
                f"This is a safety guard to prevent main checkout contamination (issue #887)."
            )

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

        # Calendar heartbeat at session start
        asyncio.create_task(_calendar_heartbeat(session.project_key, project=session.project_key))

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
            from agent.sdk_client import get_stop_reason

            stop_reason = get_stop_reason(session.session_id) if session.session_id else None

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
                except Exception:
                    pass  # Fall back to stale in-memory copy

            session_status = agent_session.status if agent_session else None
            unhealthy_reason = (
                is_session_unhealthy(session.session_id) if session.session_id else None
            )

            if unhealthy_reason:
                logger.warning(
                    f"[{session.project_key}] Watchdog flagged session "
                    f"unhealthy: {unhealthy_reason}"
                )

            # SDK-tick backstop for missed PreCompact hooks (issue #1127).
            # Runs BEFORE the delivery-action decision so a backstop-detected
            # compaction arms `last_compaction_ts` and the subsequent
            # `determine_delivery_action` call sees the freshly-armed guard.
            _tick_backstop_check_compaction(session, agent_session)

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
                # Uses local `import time as _time` matching the pattern in
                # `_tick_backstop_check_compaction` for consistency across the
                # two compaction-guard call sites (#1127 review nit).
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
                # Bump `nudge_deferred_count` for observability (C4). Best-
                # effort partial save; swallow any failure.
                if agent_session is not None:
                    try:
                        _current_deferred = int(
                            getattr(agent_session, "nudge_deferred_count", 0) or 0
                        )
                        agent_session.nudge_deferred_count = _current_deferred + 1
                        agent_session.save(update_fields=["nudge_deferred_count"])
                    except Exception as _exc:  # noqa: BLE001
                        logger.warning(
                            "[%s] Failed to bump nudge_deferred_count: %s",
                            session.project_key,
                            _exc,
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
                        except Exception:
                            pass
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
                session.last_sdk_heartbeat_at = datetime.now(tz=UTC)
                session.save(update_fields=["last_sdk_heartbeat_at"])
            except Exception as e:
                logger.warning(
                    "[%s] on_sdk_started save failed (pid=%s): %s",
                    session.session_id,
                    pid,
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

        def _on_stdout_event() -> None:
            try:
                session.last_stdout_at = datetime.now(tz=UTC)
                session.save(update_fields=["last_stdout_at"])
            except Exception as e:
                logger.warning(
                    "[%s] on_stdout_event save failed: %s",
                    session.session_id,
                    e,
                )

        messenger = BossMessenger(
            _send_callback=send_to_chat,
            chat_id=session.chat_id,
            session_id=session.session_id,
            on_sdk_started=_on_sdk_started,
            on_heartbeat_tick=_on_heartbeat_tick,
            on_stdout_event=_on_stdout_event,
        )

        # Deferred enrichment: process media, YouTube, links, reply chain.
        # Reads enrichment params exclusively from TelegramMessage via telegram_message_key.
        enriched_text = session.message_text
        enrich_has_media = False
        enrich_media_type = None
        enrich_youtube_urls = None
        enrich_non_youtube_urls = None
        enrich_reply_to_msg_id = None

        if session.telegram_message_key:
            try:
                from models.telegram import TelegramMessage

                trigger_msgs = list(
                    TelegramMessage.query.filter(msg_id=session.telegram_message_key)
                )
                if trigger_msgs:
                    tm = trigger_msgs[0]
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
                from bridge.enrichment import enrich_message, get_telegram_client

                tg_client = get_telegram_client()
                enriched_text = await enrich_message(
                    telegram_client=tg_client,
                    message_text=session.message_text,
                    has_media=enrich_has_media,
                    media_type=enrich_media_type,
                    raw_media_message_id=session.telegram_message_id,
                    youtube_urls=enrich_youtube_urls,
                    non_youtube_urls=enrich_non_youtube_urls,
                    reply_to_msg_id=enrich_reply_to_msg_id,
                    chat_id=session.chat_id,
                    sender_name=session.sender_name,
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
            except Exception:
                pass  # Non-critical: best-effort cross-reference

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

        # Check queued_steering_messages before starting this agent turn.
        # If the session has pending steering messages (written by steer_session()
        # or the PM), pop the first one and use it as the user input for this turn.
        # This is the mechanism that replaces hardcoded nudge text — any process
        # can write to queued_steering_messages to steer the session externally.
        _turn_input = enriched_text
        if agent_session:
            try:
                steering_msgs = agent_session.pop_steering_messages()
                if steering_msgs:
                    _turn_input = steering_msgs[0]
                    logger.info(
                        f"[{session.project_key}] Injecting steering message for session "
                        f"{session.session_id}: {_turn_input[:80]!r} "
                        f"({len(steering_msgs)} queued, used first)"
                    )
                    if len(steering_msgs) > 1:
                        # Re-queue remaining messages for future turns
                        for _remaining in steering_msgs[1:]:
                            agent_session.push_steering_message(_remaining)
            except Exception as _steer_err:
                logger.debug(
                    f"[{session.project_key}] Steering check failed (non-fatal): {_steer_err}"
                )

        # All session types route to CLI harness (claude -p)
        from agent.sdk_client import (
            HarnessThinkingBlockCorruptionError,
            _get_prior_session_uuid,
            _resolve_sentry_auth_token,
            build_harness_turn_input,
            get_response_via_harness,
            load_pm_system_prompt,
        )

        project_key = project_config.get("_key", "valor") if project_config else "valor"
        _classification = (
            getattr(agent_session, "classification_type", None) if agent_session else None
        )

        # Look up prior Claude Code session UUID for --resume (#976, extends PR #909 pattern)
        _prior_uuid = _get_prior_session_uuid(session.session_id)

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
            is_cross_repo=(project_key != "valor"),
        )

        # On resumed turns, also build the minimal message (no context prefix)
        _minimal_input = None
        if _prior_uuid:
            _minimal_input = await build_harness_turn_input(
                message=_turn_input,
                session_id=session.session_id,
                sender_name=session.sender_name,
                chat_title=session.chat_title,
                project=project_config,
                task_list_id=task_list_id,
                session_type=_session_type,
                sender_id=session.sender_id,
                classification=_classification,
                is_cross_repo=(project_key != "valor"),
                skip_prefix=True,
            )

        logger.info(
            f"{log_prefix} Routing {_session_type or 'unknown'} session to CLI harness"
            + (f" (--resume {_prior_uuid[:8]}...)" if _prior_uuid else " (first turn)")
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
        if _session_type in (SessionType.PM, SessionType.TEAMMATE) and session.agent_session_id:
            _harness_env["VALOR_PARENT_SESSION_ID"] = session.agent_session_id
        # PM/Teammate need Telegram + Sentry auth so tools/send_telegram.py and
        # sentry-cli work without manual export. Mirrors ValorAgent.env
        # (sdk_client.py:1264, 1272). chat_id comes from the project config.
        if _session_type in (SessionType.PM, SessionType.TEAMMATE):
            if session.chat_id:
                _harness_env["TELEGRAM_CHAT_ID"] = str(session.chat_id)
            _sentry_token = _resolve_sentry_auth_token()
            if _sentry_token:
                _harness_env["SENTRY_AUTH_TOKEN"] = _sentry_token

        _harness_requeued = False

        # D1 precedence cascade: session.model > settings > codebase default.
        _effective_model = _resolve_session_model(agent_session)

        # PM sessions get persona-level SDLC orchestration rules via
        # --append-system-prompt (issue #1148). Dev and Teammate sessions have
        # no harness-side persona loader; they keep the default Claude Code
        # protocol. Drafter call sites in session_completion.py MUST also leave
        # system_prompt at the default None — see Risk 4 in docs/plans/sdlc-1148.md.
        _pm_system_prompt: str | None = None
        if _session_type == SessionType.PM:
            try:
                _pm_system_prompt = load_pm_system_prompt(str(working_dir))
            except Exception as e:
                logger.warning(
                    f"{log_prefix} [pm-persona-missing] Failed to load PM persona: {e}; "
                    "session will run without SDLC orchestration rules"
                )

        async def do_work() -> str:
            nonlocal _harness_requeued
            try:
                raw = await get_response_via_harness(
                    message=_minimal_input if _prior_uuid else _harness_input,
                    working_dir=str(working_dir),
                    env=_harness_env,
                    prior_uuid=_prior_uuid,
                    session_id=session.session_id,
                    full_context_message=_harness_input,
                    model=_effective_model,
                    system_prompt=_pm_system_prompt,
                    # Two-tier no-progress detector callbacks (#1036). These route
                    # through messenger.notify_* wrappers so exceptions are caught
                    # and the queue-layer closures bump ORM fields on AgentSession.
                    on_sdk_started=messenger.notify_sdk_started,
                    on_stdout_event=messenger.notify_stdout_event,
                )
            except HarnessThinkingBlockCorruptionError as exc:
                # Mode 1 of issue #1099 — extended-thinking + compaction has
                # corrupted the transcript beyond in-process recovery. Surface a
                # clean user-facing message and re-raise so BackgroundTask._run_work
                # records the failure (task.error truthy → session finalizes as
                # "failed"). No retry — the sentinel only fires after the stale-UUID
                # fallback has already failed.
                logger.warning(
                    "[%s] Harness thinking-block corruption detected; finalizing as failed: %s",
                    session.session_id,
                    exc,
                )
                raise
            if raw.startswith(_HARNESS_NOT_FOUND_PREFIX):
                result, requeued = await _handle_harness_not_found(raw, agent_session)
                if requeued:
                    _harness_requeued = True
                return result
            return raw

        task = BackgroundTask(messenger=messenger)
        await task.run(do_work(), send_result=True)

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

                # Calendar + updated_at heartbeat on the 25-min cadence (preserved).
                if elapsed >= CALENDAR_HEARTBEAT_INTERVAL:
                    elapsed = 0
                    asyncio.create_task(
                        _calendar_heartbeat(session.project_key, project=session.project_key)
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

        # Post-completion SDLC handling for dev sessions (Phase 3)
        # Skip if the session was silently re-queued for harness retry — the re-queued
        # session hasn't run yet, so calling _handle_dev_session_completion with an empty
        # result would emit a spurious "fail" outcome to the PM pipeline before the retry
        # has a chance to succeed.
        if _harness_requeued:
            return

        # Update session status in Redis via AgentSession
        # When auto-continue deferred, session is still active (not completed)
        if agent_session:
            try:
                from bridge.session_transcript import complete_transcript

                final_status = (
                    "active"
                    if chat_state.defer_reaction
                    else ("completed" if not task.error else "failed")
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
                    f"{'completed' if not task.error else 'failed'}): {e}"
                )
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

                    final_status = "completed" if not task.error else "failed"
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

        # Schedule post-session memory extraction (hotfix #1055) — fire-and-forget.
        #
        # CRITICAL: synchronous call (no await, no gather). Any awaiting here would
        # re-couple extraction latency to the PM nudge below, regressing the 6-hour
        # stall observed in #1055 and the #987 ordering invariant.
        #
        # Runs AFTER both complete_transcript paths above (happy path at ~L1320
        # and the #917 fallback at ~L1346), and BEFORE _handle_dev_session_completion
        # below. Extraction runs in the background; its completion or failure does
        # not delay the PM nudge. See drain_pending_extractions() for shutdown wiring.
        _schedule_post_session_extraction(session.session_id, task._result or "")

        # Post-completion SDLC handling for dev sessions (Phase 3)
        # IMPORTANT ORDERING INVARIANT: This call is placed AFTER the entire
        # `if agent_session / else` block above. That block calls complete_transcript(),
        # which calls finalize_session() → _finalize_parent_sync() synchronously
        # (bridge/session_transcript.py:252, line 292). By the time
        # _handle_dev_session_completion runs here, _finalize_parent_sync has already
        # completed on BOTH the `if agent_session:` and `else:` paths. The re-check
        # guard inside _handle_dev_session_completion will therefore correctly observe
        # the PM's post-finalization (terminal) status and create a continuation PM.
        # Moving this call earlier (before complete_transcript) causes the race
        # described in issue #987: steer is accepted, then _finalize_parent_sync runs
        # and the PM goes terminal, orphaning the steering message.
        #
        # Nudge path note: on the nudge path (defer_reaction=True), complete_transcript
        # is skipped above, but _finalize_parent_sync still runs via the nudge path's
        # own finalize_session call. This call is guarded by `_session_type == "dev"`,
        # not by `defer_reaction`, so it executes on both the nudge and non-nudge paths.
        #
        # Skip if the session was silently re-queued for harness retry — the re-queued
        # session hasn't run yet, so calling _handle_dev_session_completion with an empty
        # result would emit a spurious "fail" outcome to the PM pipeline before the retry
        # has a chance to succeed. (The _harness_requeued early-return above handles this.)
        if _session_type == "dev" and not task.error:
            await _handle_dev_session_completion(
                session=session,
                agent_session=agent_session,
                result=task._result or "",
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
                        session_type=session.session_type or "pm",
                        project_config=getattr(session, "project_config", None),
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
            elif messenger.has_communicated():
                emoji = REACTION_COMPLETE
            else:
                emoji = REACTION_SUCCESS
            try:
                await react_cb(session.chat_id, session.telegram_message_id, emoji)
            except Exception as e:
                logger.warning(f"Failed to set reaction: {e}")

        # Auto-mark session as done after successful completion
        # Skip when auto-continue deferred — continuation session will handle cleanup
        if not task.error and not chat_state.defer_reaction:
            try:
                from agent.branch_manager import mark_work_done

                mark_work_done(working_dir, branch_name)
                # Also delete the session branch to keep git clean
                subprocess.run(
                    ["git", "branch", "-D", branch_name],
                    cwd=working_dir,
                    capture_output=True,
                    timeout=10,
                )
                logger.info(
                    f"[{session.project_key}] Auto-marked session done "
                    f"and cleaned up branch {branch_name}"
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
