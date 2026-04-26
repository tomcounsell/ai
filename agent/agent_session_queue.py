"""
Agent Session Queue - FILO stack with per-project sequential workers.

Serializes agent work per project working directory so git operations
never conflict. Agent runs directly in the project's working directory.

This module has no module-level bridge/ imports and can be used by both
the Telegram bridge (I/O only) and the standalone worker (python -m worker).
The execution functions live here and are called by the standalone worker;
the bridge handles Telegram I/O and registers output callbacks.
Output routing uses the OutputHandler protocol defined in agent/output_handler.py,
with FileOutputHandler as fallback when no bridge callbacks are registered.

Architecture:
- AgentSession: unified popoto Model persisted in Redis
- Worker loop: one asyncio.Task per project, processes sessions sequentially
- Revival detection: lightweight git state check, no SDK agent call
- Output: OutputHandler protocol (Telegram callbacks or file logging)
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Shared mutable session-tracking state — re-exported here for backward compatibility.
import agent.session_state as _session_state  # noqa: F401 (also used for mutation sites)
from agent.branch_manager import get_branch_state  # noqa: F401
from agent.output_handler import OutputHandler

# Output routing — decision logic lives in output_router; re-exported here
# for backward compatibility with callers that import from agent_session_queue.
from agent.output_router import (
    MAX_NUDGE_COUNT,  # noqa: F401
    NUDGE_MESSAGE,  # noqa: F401
    SendToChatResult,  # noqa: F401
    determine_delivery_action,  # noqa: F401
)

# Session completion (post-execution lifecycle) — re-exported here for backward compatibility.
from agent.session_completion import (  # noqa: F401
    _CONTINUATION_PM_MAX_DEPTH,
    _complete_agent_session,
    _create_continuation_pm,
    _diagnose_missing_session,
    _extract_issue_number,
    _handle_dev_session_completion,
    _transition_parent,
)

# Session executor (CLI harness, nudge/re-enqueue, steer) — re-exported for backward compatibility.
from agent.session_executor import (  # noqa: F401
    _HARNESS_EXHAUSTION_MSG,
    _HARNESS_NOT_FOUND_MAX_RETRIES,
    _HARNESS_NOT_FOUND_PREFIX,
    _calendar_heartbeat,
    _enqueue_nudge,
    _execute_agent_session,
    _find_valor_calendar,
    _handle_harness_not_found,
    re_enqueue_session,
    steer_session,
)

# Health monitoring — re-exported here for backward compatibility.
from agent.session_health import (  # noqa: F401
    AGENT_SESSION_HEALTH_CHECK_INTERVAL,
    AGENT_SESSION_HEALTH_MIN_RUNNING,
    HEARTBEAT_FRESHNESS_WINDOW,
    HEARTBEAT_WRITE_INTERVAL,
    MAX_RECOVERY_ATTEMPTS,
    _agent_session_health_check,
    _agent_session_health_loop,
    _agent_session_hierarchy_health_check,
    _cleanup_orphaned_claude_processes,
    _dependency_health_check,
    _has_progress,
    _recover_interrupted_agent_sessions_startup,
    _tier2_reprieve_signal,
    _write_worker_heartbeat,
    cleanup_corrupted_agent_sessions,
    format_duration,
)
from agent.session_logs import save_session_snapshot

# Session pickup (pop locking, startup steering drain, dependency checks) — re-exported here.
from agent.session_pickup import (  # noqa: F401
    _POP_LOCK_TTL_SECONDS,
    _acquire_pop_lock,
    _drain_startup_steering,
    _maybe_inject_resume_hydration,
    _pop_agent_session,
    _pop_agent_session_with_fallback,
    _release_pop_lock,
    dependency_status,
)

# Revival detection — re-exported here for backward compatibility.
from agent.session_revival import (  # noqa: F401
    _COOLDOWN_FILE,
    REVIVAL_COOLDOWN_SECONDS,
    _load_cooldowns,
    _save_cooldowns,
    _session_branch_name,
    check_revival,
    cleanup_stale_branches,
    cleanup_stale_branches_all_projects,
    maybe_send_revival_prompt,
    queue_revival_agent_session,
    record_revival_cooldown,
)
from agent.session_state import (  # noqa: F401
    ReactionCallback,
    ResponseCallback,
    SendCallback,
    SessionHandle,
    _active_events,
    _active_sessions,
    _active_workers,
    _global_session_semaphore,
    _reaction_callbacks,
    _response_callbacks,
    _send_callbacks,
    _shutdown_requested,
    _starting_workers,
)
from config.enums import ClassificationType, SessionType
from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# 4-tier priority ranking: lower number = higher priority
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


# Fields to extract from AgentSession for delete-and-recreate pattern.
# Used by callers that legitimately need a fresh AutoKeyField-generated ID:
# retry, orphan fix, and the continuation fallback in _enqueue_nudge. The pop
# path (_pop_agent_session) does NOT use this — it mutates in place via
# transition_status() because status is an IndexedField, not a KeyField, and
# the secondary index is updated correctly on save().
_AGENT_SESSION_FIELDS = [
    "project_key",
    "status",
    "priority",
    "scheduled_at",
    "created_at",
    "session_id",
    "working_dir",
    "initial_telegram_message",
    "chat_id",
    "extra_context",
    "task_list_id",
    "auto_continue_count",
    "started_at",
    "telegram_message_key",
    "updated_at",
    "completed_at",
    "turn_count",
    "tool_call_count",
    "log_path",
    "branch_name",
    "tags",
    "session_events",
    "issue_url",
    "plan_url",
    "pr_url",
    "context_summary",
    "expectations",
    "queued_steering_messages",
    "correlation_id",
    "claude_session_uuid",
    "parent_agent_session_id",
    "session_type",
    "slug",
    "pm_sent_message_ids",
    "last_heartbeat_at",
    "last_sdk_heartbeat_at",
    "last_stdout_at",
    "recovery_attempts",
    "reprieve_count",
]


def _extract_agent_session_fields(redis_session: AgentSession) -> dict:
    """Extract all non-auto fields from an AgentSession instance.

    Returns a dict suitable for AgentSession.create(**fields) or
    AgentSession.async_create(**fields). Excludes agent_session_id since that is
    an AutoKeyField and will be auto-generated on create.
    """
    return {field: getattr(redis_session, field) for field in _AGENT_SESSION_FIELDS}


async def _push_agent_session(
    project_key: str,
    session_id: str,
    working_dir: str,
    message_text: str,
    sender_name: str,
    chat_id: str,
    telegram_message_id: int,
    chat_title: str | None = None,
    priority: str = "normal",
    revival_context: str | None = None,
    sender_id: int | None = None,
    slug: str | None = None,
    task_list_id: str | None = None,
    classification_type: str | None = None,
    auto_continue_count: int = 0,
    correlation_id: str | None = None,
    scheduled_at: datetime | float | None = None,
    parent_agent_session_id: str | None = None,
    telegram_message_key: str | None = None,
    session_type: str = SessionType.PM,
    scheduling_depth: int = 0,  # ignored, now derived
    depends_on: list[str] | None = None,  # ignored, removed
    project_config: dict | None = None,
    extra_context_overrides: dict | None = None,
    model: str | None = None,
    **_kwargs,
) -> int:
    """Create an agent session in Redis and return the pending queue depth for this chat.

    Queue is keyed by chat_id so different chat groups for the same project
    can run in parallel. project_key is preserved on the model for config lookup.

    Bug 3 fix (issue #374): When creating a new record for a continuation
    (reply-to-resume), mark old completed records with the same session_id
    as 'superseded' to prevent ambiguity in later record selection.

    Args:
        extra_context_overrides: Additional key/value pairs merged into extra_context
            before saving. Use for transport-specific metadata (e.g., transport="email",
            email_message_id=...). Keys in overrides take precedence over derived values.
        model: Optional Claude model name (e.g. "sonnet", "opus"). When set, overrides
            the environment-level default for this session. None inherits the default.
    """
    # Convert float timestamps to datetime (backward compat)
    if isinstance(scheduled_at, int | float):
        scheduled_at = datetime.fromtimestamp(scheduled_at, tz=UTC)

    # Build consolidated dicts
    initial_telegram_message = {
        "message_text": message_text,
        "sender_name": sender_name,
        "telegram_message_id": telegram_message_id,
    }
    if sender_id is not None:
        initial_telegram_message["sender_id"] = sender_id
    if chat_title is not None:
        initial_telegram_message["chat_title"] = chat_title

    extra_context = {}
    if revival_context:
        extra_context["revival_context"] = revival_context
    if classification_type:
        extra_context["classification_type"] = classification_type
    if extra_context_overrides:
        extra_context.update(extra_context_overrides)

    # Mark old completed records as superseded to prevent duplicate-record ambiguity
    try:

        def _mark_superseded():
            from models.session_lifecycle import transition_status

            old_completed = [
                s
                for s in AgentSession.query.filter(session_id=session_id)
                if s.status == "completed"
            ]
            for old in old_completed:
                transition_status(
                    old,
                    "superseded",
                    reason=f"superseded by new session for {session_id}",
                    # (#730) The reject_from_terminal override was intentionally removed.
                    # Terminal sessions must not be re-activated. The guard in
                    # transition_status() will reject completed→superseded transitions,
                    # leaving the completed record intact and preventing worker re-activation.
                )
                logger.info(
                    f"Marked old completed session {old.id} as superseded "
                    f"for session_id={session_id}"
                )

        await asyncio.to_thread(_mark_superseded)
    except Exception as e:
        logger.warning(f"Failed to mark old sessions as superseded for {session_id}: {e}")

    await AgentSession.async_create(
        project_key=project_key,
        status="pending",
        priority=priority,
        created_at=datetime.now(tz=UTC),
        session_id=session_id,
        session_type=session_type,
        working_dir=working_dir,
        initial_telegram_message=initial_telegram_message,
        chat_id=chat_id,
        extra_context=extra_context or None,
        slug=slug,
        task_list_id=task_list_id,
        auto_continue_count=auto_continue_count,
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        parent_agent_session_id=parent_agent_session_id,
        telegram_message_key=telegram_message_key,
        project_config=project_config or None,
        model=model or None,
    )

    # Initialize stage_states for SDLC sessions so the dashboard shows
    # pipeline progress from the start (not just after a dev-session runs).
    if classification_type == ClassificationType.SDLC:
        try:

            def _init_stage_states():
                from agent.pipeline_state import PipelineStateMachine

                sessions = list(AgentSession.query.filter(session_id=session_id, status="pending"))
                if sessions and not sessions[0].stage_states:
                    sm = PipelineStateMachine(sessions[0])
                    # PipelineStateMachine.__init__ already sets ISSUE=ready, rest=pending
                    sm._save()
                    logger.info(f"Initialized stage_states for SDLC session {session_id}")

            await asyncio.to_thread(_init_stage_states)
        except Exception as e:
            logger.warning(f"Failed to initialize stage_states for {session_id}: {e}")

    # Log lifecycle transition for newly created pending agent session
    try:

        def _log_lifecycle():
            sessions = list(AgentSession.query.filter(session_id=session_id, status="pending"))
            if sessions:
                sessions[0].log_lifecycle_transition("pending", "agent session enqueued")

        await asyncio.to_thread(_log_lifecycle)
    except Exception as e:
        logger.warning(f"Failed to log lifecycle transition for session {session_id}: {e}")

    # Publish notification so the standalone worker picks up immediately (~1s latency)
    # instead of waiting for the 5-minute health check. Fire-and-forget: publish
    # failure is logged as a warning and never raises. If the worker is not running
    # or Redis pub/sub drops the message, the health check is the safety net.
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        # KEEP IN SYNC with AgentSession.worker_key in models/agent_session.py
        # Compute worker_key inline from the same inputs as AgentSession.worker_key
        if session_type == SessionType.TEAMMATE:
            _wk = chat_id or project_key
        elif session_type == SessionType.PM:
            _wk = project_key
        elif slug:
            _wk = slug
        else:
            _wk = project_key
        payload = json.dumps(
            {
                "chat_id": chat_id,
                "session_id": session_id,
                "worker_key": _wk,
                "is_project_keyed": _wk == project_key,
            }
        )
        await asyncio.to_thread(POPOTO_REDIS_DB.publish, "valor:sessions:new", payload)
        logger.debug(f"Published session notification for worker_key={_wk}")
    except Exception as e:
        logger.warning(f"Failed to publish session notification for {session_id}: {e}")

    return await AgentSession.query.async_count(chat_id=chat_id, status="pending")


def resolve_branch_for_stage(slug: str | None, stage: str | None) -> tuple[str, bool]:
    """Determine the correct branch and whether a worktree is needed for a given stage.

    Maps (slug, stage) pairs to deterministic branch names. This replaces
    implicit branch resolution that previously relied on skill context.

    Args:
        slug: The work item slug (e.g., 'auth-feature'). None for non-SDLC.
        stage: The SDLC stage (e.g., 'PLAN', 'BUILD', 'TEST'). None for non-SDLC.

    Returns:
        Tuple of (branch_name, needs_worktree).
        - branch_name: The git branch to work on.
        - needs_worktree: Whether a worktree should be created/used.
    """
    if not slug:
        return ("main", False)

    if not stage:
        return ("main", False)

    stage_upper = stage.upper()

    # PLAN and ISSUE stages work on main (plans committed to main)
    if stage_upper in ("PLAN", "ISSUE", "CRITIQUE"):
        return ("main", False)

    # BUILD, TEST, PATCH, REVIEW, DOCS stages use session branch in worktree
    if stage_upper in ("BUILD", "TEST", "PATCH", "REVIEW", "DOCS"):
        return (f"session/{slug}", True)

    # MERGE stage uses session branch but no new worktree needed
    if stage_upper == "MERGE":
        return (f"session/{slug}", False)

    # Fallback for unknown stages
    logger.warning(
        f"[branch-mapping] Unknown stage {stage!r} for slug {slug!r}, falling back to main"
    )
    return ("main", False)


def checkpoint_branch_state(session: AgentSession) -> None:
    """Record current branch + HEAD commit SHA on the AgentSession.

    Called when a session pauses (steering, dependency block) to preserve
    the exact git state for later restoration.

    Args:
        session: The AgentSession to checkpoint.
    """
    working_dir = session.working_dir
    if not working_dir:
        return

    try:
        branch = subprocess.run(
            ["git", "-C", working_dir, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit = subprocess.run(
            ["git", "-C", working_dir, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if branch.returncode == 0 and commit.returncode == 0:
            branch_name = branch.stdout.strip()
            commit_sha = commit.stdout.strip()
            session.branch_name = branch_name
            session.commit_sha = commit_sha
            session.save(update_fields=["branch_name", "session_events", "updated_at"])
            logger.info(
                f"[checkpoint] Saved branch={branch_name} commit={commit_sha[:8]} "
                f"for session {session.session_id}"
            )
        else:
            logger.warning(
                f"[checkpoint] Failed to read git state for session "
                f"{session.session_id}: branch={branch.stderr.strip()}, "
                f"commit={commit.stderr.strip()}"
            )
    except Exception as e:
        logger.warning(f"[checkpoint] Error checkpointing state for {session.session_id}: {e}")


def restore_branch_state(session: AgentSession) -> bool:
    """Verify and restore branch + commit state from a checkpoint.

    Called when a session resumes to ensure it starts on the correct branch
    at the correct commit. If the recorded commit is an ancestor of
    current HEAD, proceeds on HEAD (newer commits are fine).

    Args:
        session: The AgentSession with checkpoint data.

    Returns:
        True if state was successfully verified/restored, False otherwise.
    """
    working_dir = session.working_dir
    recorded_branch = session.branch_name
    recorded_sha = session.commit_sha

    if not working_dir or not recorded_branch or not recorded_sha:
        logger.debug(
            f"[restore] No checkpoint data for session {session.session_id} — "
            f"proceeding on current state"
        )
        return True

    try:
        # Check current branch
        current = subprocess.run(
            ["git", "-C", working_dir, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        current_branch = current.stdout.strip() if current.returncode == 0 else ""

        if current_branch != recorded_branch:
            logger.info(
                f"[restore] Branch mismatch: current={current_branch}, "
                f"recorded={recorded_branch} — checking out recorded branch"
            )
            checkout = subprocess.run(
                ["git", "-C", working_dir, "checkout", recorded_branch],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if checkout.returncode != 0:
                logger.warning(
                    f"[restore] Failed to checkout {recorded_branch}: "
                    f"{checkout.stderr.strip()} — proceeding on {current_branch}"
                )
                return False

        # Verify commit is reachable
        ancestor_check = subprocess.run(
            [
                "git",
                "-C",
                working_dir,
                "merge-base",
                "--is-ancestor",
                recorded_sha,
                "HEAD",
            ],
            capture_output=True,
            timeout=5,
        )
        if ancestor_check.returncode == 0:
            logger.info(
                f"[restore] Commit {recorded_sha[:8]} is ancestor of HEAD — "
                f"proceeding on current HEAD"
            )
            return True
        else:
            logger.warning(
                f"[restore] Commit {recorded_sha[:8]} is not ancestor of HEAD — "
                f"proceeding on current HEAD anyway (may have been force-pushed)"
            )
            return True

    except Exception as e:
        logger.warning(f"[restore] Error restoring state for {session.session_id}: {e}")
        return False


# Session pickup functions extracted to agent/session_pickup.py.
# All symbols re-exported at the top of this module for backward compatibility.


async def _pending_depth(chat_id: str) -> int:
    """Count of pending sessions for a chat."""
    return await AgentSession.query.async_count(chat_id=chat_id, status="pending")


async def _remove_by_session(chat_id: str, session_id: str) -> bool:
    """Remove all pending sessions for a session. Returns True if any removed."""
    sessions_list = await AgentSession.query.async_filter(chat_id=chat_id, status="pending")
    removed = False
    for j in sessions_list:
        if j.session_id == session_id:
            await j.async_delete()
            removed = True
    return removed


def reorder_agent_session(agent_session_id: str, new_priority: str) -> bool:
    """Change the priority of a pending session.

    Args:
        agent_session_id: The agent_session_id (AutoKeyField) of the session to reorder.
        new_priority: New priority level (urgent/high/normal/low).

    Returns:
        True if the session was reordered, False if not found or not pending.
    """
    if new_priority not in PRIORITY_RANK:
        logger.warning(f"[pm-controls] Invalid priority: {new_priority}")
        return False

    try:
        session = AgentSession.get_by_id(agent_session_id)
    except Exception as exc:
        logger.warning(
            "[pm-controls] Session %s lookup failed for reorder: %s",
            agent_session_id,
            exc,
        )
        return False

    if session is None or session.status != "pending":
        logger.warning(
            f"[pm-controls] Session {agent_session_id} not pending "
            f"(status={getattr(session, 'status', None)}) — cannot reorder"
        )
        return False

    session.priority = new_priority
    session.save(update_fields=["priority", "updated_at"])
    logger.info(f"[pm-controls] Reordered session {agent_session_id} (priority={new_priority})")
    return True


def cancel_agent_session(agent_session_id: str) -> bool:
    """Cancel a pending session by setting its status to 'cancelled'.

    Cancelled sessions block their dependents (same as failed). PM is notified
    and decides whether to cancel or unblock dependent sessions.

    Args:
        agent_session_id: The agent_session_id of the session to cancel.

    Returns:
        True if the session was cancelled, False if not found or not pending.
    """
    try:
        session = AgentSession.get_by_id(agent_session_id)
    except Exception as exc:
        logger.warning(
            "[pm-controls] Session %s lookup failed for cancel: %s",
            agent_session_id,
            exc,
        )
        return False

    if session is None or session.status != "pending":
        logger.warning(
            f"[pm-controls] Session {agent_session_id} not pending "
            f"(status={getattr(session, 'status', None)}) — cannot cancel"
        )
        return False

    from models.session_lifecycle import finalize_session

    finalize_session(session, "cancelled", reason=f"PM cancelled session {agent_session_id}")
    logger.info(f"[pm-controls] Cancelled session {agent_session_id}")
    return True


def retry_agent_session(agent_session_id: str) -> AgentSession | None:
    """Re-queue a failed or cancelled session with the same parameters.

    Creates a new pending session preserving all fields from the original.

    Args:
        agent_session_id: The id of the session to retry.

    Returns:
        The new AgentSession if retried, None if not found or not terminal.
    """
    try:
        session = AgentSession.get_by_id(agent_session_id)
    except Exception as exc:
        logger.warning(
            "[pm-controls] agent_session_id %s lookup failed for retry: %s",
            agent_session_id,
            exc,
        )
        return None

    if not session:
        logger.warning(f"[pm-controls] No session found with id={agent_session_id}")
        return None

    if session.status not in ("failed", "cancelled"):
        logger.warning(
            f"[pm-controls] Session {session.id} status is {session.status!r} -- "
            f"can only retry failed/cancelled sessions"
        )
        return None

    fields = _extract_agent_session_fields(session)
    fields["status"] = "pending"
    fields["priority"] = "high"
    fields["started_at"] = None
    fields["completed_at"] = None
    fields["created_at"] = datetime.now(tz=UTC)
    new_session = AgentSession.create(**fields)
    logger.info(f"[pm-controls] Retried session {session.id} -> {new_session.id}")

    return new_session


def get_queue_status(chat_id: str) -> dict:
    """Return full queue state with dependency graph for a chat.

    Returns a dict with pending, running, completed, and failed session summaries
    including dependency information.

    Args:
        chat_id: The chat_id to query.

    Returns:
        Dict with keys: pending, running, completed, failed, cancelled.
        Each value is a list of session summary dicts.
    """
    result: dict[str, list[dict]] = {
        "pending": [],
        "running": [],
        "completed": [],
        "failed": [],
        "cancelled": [],
    }

    try:
        all_sessions = list(AgentSession.query.filter(chat_id=chat_id))
    except Exception as e:
        logger.warning(f"[pm-controls] Failed to query sessions for chat {chat_id}: {e}")
        return result

    for entry in all_sessions:
        status = entry.status or "unknown"
        if status not in result:
            continue

        summary = {
            "agent_session_id": entry.id,
            "session_id": entry.session_id,
            "message_preview": (entry.message_text or "")[:100],
            "priority": entry.priority,
            "created_at": entry.created_at,
            "started_at": entry.started_at,
        }
        result[status].append(summary)

    return result


async def get_active_session_for_chat(chat_id: str) -> AgentSession | None:
    """Find the active AgentSession for a given Telegram chat_id.

    Used for routing steering messages to the correct PM session.
    Returns the most recent running AgentSession for this chat.
    """
    sessions = await asyncio.to_thread(
        lambda: list(AgentSession.query.filter(chat_id=chat_id, status="running"))
    )
    if not sessions:
        return None
    # Most recent first
    sessions.sort(key=lambda s: s.created_at or 0, reverse=True)
    return sessions[0]


async def _session_notify_listener() -> None:
    """Subscribe to valor:sessions:new and wake the worker on new sessions.

    Fire-and-forget coroutine started by the standalone worker alongside the
    health monitor. Reconnects automatically on transient Redis errors.

    Uses a queue to bridge the blocking pubsub.listen() thread and the asyncio
    event loop: the background thread puts chat_ids onto the queue, and the
    coroutine awaits them and calls _ensure_worker / sets the event.
    """
    while True:
        notify_queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _listen_in_thread() -> None:
            """Blocking loop that drains pubsub and forwards chat_ids to the queue.

            Uses a dedicated Redis connection with socket_timeout=None so that
            pubsub.listen() blocks indefinitely between messages.  The global
            POPOTO_REDIS_DB pool has socket_timeout=5 (tuned for request-response),
            which would cause spurious "Timeout reading from socket" exceptions and
            a 10-second reconnect cycle that drops notifications published during
            the dead window.  We read host/port/db from the global pool's kwargs but
            override both timeout parameters explicitly to avoid inheriting them.
            """
            import redis as _redis
            from popoto.redis_db import POPOTO_REDIS_DB

            conn: _redis.Redis | None = None
            pubsub = None
            try:
                kw = POPOTO_REDIS_DB.connection_pool.connection_kwargs
                conn = _redis.Redis(
                    host=kw.get("host", "localhost"),
                    port=kw.get("port", 6379),
                    db=kw.get("db", 0),
                    username=kw.get("username"),
                    password=kw.get("password"),
                    decode_responses=kw.get("decode_responses", False),
                    socket_timeout=None,
                    socket_connect_timeout=None,
                )
                pubsub = conn.pubsub()
                pubsub.subscribe("valor:sessions:new")
                logger.info("Session notify listener subscribed to valor:sessions:new")
                for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                        wk = data.get("worker_key") or data.get("chat_id")
                        is_pk = data.get("is_project_keyed", False)
                        session_id = data.get("session_id")
                        if wk is not None:
                            logger.info(
                                "Received session notify: worker_key=%s session_id=%s",
                                wk,
                                session_id,
                            )
                            loop.call_soon_threadsafe(notify_queue.put_nowait, (wk, is_pk))
                    except json.JSONDecodeError as e:
                        logger.warning("Session notify: bad JSON payload: %s", e)
                    except Exception as e:
                        logger.warning("Session notify: error processing message: %s", e)
            except Exception as e:
                logger.warning("Session notify listener thread error: %s", e)
            finally:
                # Teardown in order: unsubscribe → close pubsub → close connection
                # This prevents dangling Redis subscribers on reconnect cycles.
                if pubsub is not None:
                    try:
                        pubsub.unsubscribe()
                    except Exception:
                        pass
                    try:
                        pubsub.close()
                    except Exception:
                        pass
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                # Signal the coroutine side to restart
                loop.call_soon_threadsafe(notify_queue.put_nowait, None)

        try:
            # Run the blocking pubsub loop in a thread; process results here
            listener_future = asyncio.to_thread(_listen_in_thread)
            task = asyncio.create_task(listener_future)

            while True:
                item = await notify_queue.get()
                if item is None:
                    # Thread exited (error path); restart after delay
                    break
                wk, is_pk = item
                _ensure_worker(wk, is_project_keyed=is_pk)
                event = _active_events.get(wk)
                if event is not None:
                    event.set()

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        except Exception as e:
            logger.warning("Session notify listener error: %s. Retrying in 5s...", e)

        await asyncio.sleep(5)


# === Per-project worker ===

# Drain timeout: how long the worker waits for new work after completing a session.
# The Event-based drain uses this as the timeout for asyncio.Event.wait().
# If no event fires within this window, the worker falls back to a sync query.
DRAIN_TIMEOUT = 1.5  # seconds

# Mutable session-tracking globals live in agent.session_state (re-imported above).
# _active_workers, _active_events, SessionHandle, _active_sessions, _starting_workers,
# _global_session_semaphore, _shutdown_requested, _send_callbacks, _reaction_callbacks,
# _response_callbacks are all imported from there.


def request_shutdown() -> None:
    """Signal all worker loops to finish current work and exit.

    Called by the standalone worker's SIGTERM handler. Sets the shutdown flag
    and wakes all waiting workers so they can check the flag and exit.
    """
    _session_state._shutdown_requested = True
    # Wake all waiting workers so they see the flag
    for event in _active_events.values():
        event.set()
    logger.info("Shutdown requested — workers will finish current sessions and exit")


def register_callbacks(
    project_key: str,
    send_callback: SendCallback | None = None,
    reaction_callback: ReactionCallback | None = None,
    response_callback: ResponseCallback | None = None,
    *,
    transport: str | None = None,
    handler: OutputHandler | None = None,
) -> None:
    """
    Register output callbacks for a project.

    Accepts either raw callables (backward compatible with bridge) or an
    OutputHandler instance (for standalone worker and new platform bridges).

    Args:
        project_key: Project identifier to register callbacks for.
        send_callback: Callable (chat_id, text, reply_to_msg_id, session) -> sends output.
        reaction_callback: Callable (chat_id, msg_id, emoji) -> sets a reaction.
        response_callback: Callable (event, text, chat_id, msg_id) ->
            sends response with file handling.
        transport: Optional transport name (e.g. "email", "telegram"). When provided,
                   callbacks are stored under a (project_key, transport) composite key,
                   allowing multiple transports to coexist for the same project.
                   When None (default), callbacks are stored under the plain project_key
                   string key for backward compatibility.
        handler: An OutputHandler instance. If provided, its send() and react()
                 methods are wrapped as send_callback and reaction_callback.
    """
    # Use composite key when transport is specified, else plain project_key
    key: str | tuple[str, str] = (project_key, transport) if transport else project_key

    if handler is not None:
        # Wrap OutputHandler methods as raw callbacks for internal use
        if send_callback is None:
            _send_callbacks[key] = handler.send
        else:
            _send_callbacks[key] = send_callback

        if reaction_callback is None:
            _reaction_callbacks[key] = handler.react
        else:
            _reaction_callbacks[key] = reaction_callback
    else:
        if send_callback is None:
            raise ValueError("Either send_callback or handler must be provided")
        if reaction_callback is None:
            raise ValueError("Either reaction_callback or handler must be provided")
        _send_callbacks[key] = send_callback
        _reaction_callbacks[key] = reaction_callback

    if response_callback:
        _response_callbacks[key] = response_callback


# === Restart Flag (written by remote-update.sh) ===

_RESTART_FLAG = Path(__file__).parent.parent / "data" / "restart-requested"


_RESTART_FLAG_TTL = timedelta(hours=1)


def _check_restart_flag() -> bool:
    """Check if a restart has been requested and no sessions are running across all projects.

    Returns False (and deletes the flag) if the flag is older than 1 hour,
    malformed, or empty — preventing stale flags from triggering self-destruct.
    """
    if not _RESTART_FLAG.exists():
        return False

    flag_content = _RESTART_FLAG.read_text().strip()

    # Validate flag freshness via embedded timestamp
    try:
        timestamp_str = flag_content.split()[0]
        flag_time = datetime.fromisoformat(timestamp_str)
        # Ensure timezone-aware comparison
        if flag_time.tzinfo is None:
            flag_time = flag_time.replace(tzinfo=UTC)
        flag_age = datetime.now(UTC) - flag_time
        if flag_age > _RESTART_FLAG_TTL:
            logger.warning(f"Restart flag is stale (age={flag_age}) — ignoring and deleting")
            _RESTART_FLAG.unlink(missing_ok=True)
            return False
    except (ValueError, IndexError):
        logger.warning(
            f"Restart flag has malformed content ({flag_content!r}) — ignoring and deleting"
        )
        _RESTART_FLAG.unlink(missing_ok=True)
        return False

    # Check all workers for running sessions
    running = list(AgentSession.query.filter(status="running"))
    if running:
        logger.info(f"Restart requested but {len(running)} session(s) still running — deferring")
        return False

    logger.info(f"Restart flag found ({flag_content}), no running sessions — restarting bridge")
    return True


def _trigger_restart() -> None:
    """Trigger graceful bridge restart by sending SIGTERM to self.

    SIGTERM is caught by the existing _shutdown_handler in the bridge which
    sets SHUTTING_DOWN=True and calls _graceful_shutdown(). Launchd KeepAlive
    restarts the process with new code.
    """
    _RESTART_FLAG.unlink(missing_ok=True)
    logger.info("Triggering graceful restart via SIGTERM...")
    os.kill(os.getpid(), signal.SIGTERM)


def clear_restart_flag() -> bool:
    """Clear the restart flag on startup. Returns True if a flag was cleared."""
    if _RESTART_FLAG.exists():
        _RESTART_FLAG.unlink(missing_ok=True)
        return True
    return False


def _resolve_callbacks(
    project_key: str,
    transport: str | None,
) -> tuple[SendCallback | None, ReactionCallback | None]:
    """Resolve send and reaction callbacks for a project+transport combination.

    Lookup order:
    1. (project_key, transport) composite key — transport-specific handler
    2. project_key string key — transport-agnostic handler (backward compat)
    3. None — falls through to FileOutputHandler in caller
    """
    if transport:
        composite_key = (project_key, transport)
        send_cb = _send_callbacks.get(composite_key) or _send_callbacks.get(project_key)
        react_cb = _reaction_callbacks.get(composite_key) or _reaction_callbacks.get(project_key)
    else:
        send_cb = _send_callbacks.get(project_key)
        react_cb = _reaction_callbacks.get(project_key)
    return send_cb, react_cb


async def enqueue_agent_session(
    project_key: str,
    session_id: str,
    working_dir: str,
    message_text: str,
    sender_name: str,
    chat_id: str,
    telegram_message_id: int,
    chat_title: str | None = None,
    priority: str = "normal",
    revival_context: str | None = None,
    sender_id: int | None = None,
    slug: str | None = None,
    task_list_id: str | None = None,
    classification_type: str | None = None,
    auto_continue_count: int = 0,
    correlation_id: str | None = None,
    scheduled_at: float | None = None,
    parent_agent_session_id: str | None = None,
    telegram_message_key: str | None = None,
    session_type: str = SessionType.PM,
    scheduling_depth: int = 0,  # ignored, now derived
    project_config: dict | None = None,
    extra_context_overrides: dict | None = None,
) -> int:
    """
    Add a session to Redis and ensure worker is running.

    Args:
        project_config: Full project dict from projects.json. Stored on the
            AgentSession so downstream code can read project properties without
            re-deriving from a parallel registry. Pass None for backward compat
            (legacy callers); the worker will fall back to loading from projects.json.
        extra_context_overrides: Additional key/value pairs merged into extra_context.
            Use for transport-specific metadata, e.g. transport="email".

    Returns queue depth after push.
    """
    from tools.field_utils import log_large_field

    log_large_field("message_text", message_text)
    if revival_context:
        log_large_field("revival_context", revival_context)

    depth = await _push_agent_session(
        project_key=project_key,
        session_id=session_id,
        working_dir=working_dir,
        message_text=message_text,
        sender_name=sender_name,
        sender_id=sender_id,
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
        chat_title=chat_title,
        priority=priority,
        revival_context=revival_context,
        slug=slug,
        task_list_id=task_list_id,
        classification_type=classification_type,
        auto_continue_count=auto_continue_count,
        correlation_id=correlation_id,
        scheduled_at=scheduled_at,
        parent_agent_session_id=parent_agent_session_id,
        telegram_message_key=telegram_message_key,
        session_type=session_type,
        project_config=project_config,
        extra_context_overrides=extra_context_overrides,
    )
    # KEEP IN SYNC with AgentSession.worker_key in models/agent_session.py
    # Compute worker_key from the same inputs the property uses, without re-querying Redis
    if session_type == SessionType.TEAMMATE:
        wk = chat_id or project_key
    elif session_type == SessionType.PM:
        wk = project_key
    elif slug:
        wk = slug
    else:
        wk = project_key
    is_pk = wk == project_key

    _ensure_worker(wk, is_project_keyed=is_pk)

    event = _active_events.get(wk)
    if event is not None:
        event.set()

    log_prefix = f"[{correlation_id}]" if correlation_id else f"[{project_key}]"
    logger.info(
        f"{log_prefix} Enqueued session (priority={priority}, depth={depth}, "
        f"worker={wk}, chat={chat_id})"
    )
    return depth


def _ensure_worker(worker_key: str, is_project_keyed: bool = False) -> None:
    """Start a worker for this worker_key if one isn't already running.

    Workers are keyed by worker_key — either project_key (for PM and
    dev-without-slug sessions that share the main working tree) or chat_id
    (for teammate and slugged-dev sessions with isolated worktrees).

    Creates an asyncio.Event for the key if one doesn't exist. The event is
    used by _worker_loop to wait for new work notifications.

    Idempotency guarantee (two-guard mechanism):
    1. _active_workers[worker_key]: task exists and is not done — steady-state guard.
    2. _starting_workers: worker_key was added here before create_task() and removed
       once the task is live — startup-race guard.

    Leak-safety guarantee: _starting_workers.discard() runs in ``finally``, so no
    exit path can leave the key in the set. If any statement after create_task()
    raises (e.g., a pathological dict assignment or logger failure), the newly-
    created task is cancelled and NOT stored in _active_workers, so no orphan
    runs.
    """
    existing = _active_workers.get(worker_key)
    if existing and not existing.done():
        return
    if worker_key in _starting_workers:
        logger.warning(f"[worker:{worker_key}] Duplicate worker spawn blocked — in-flight")
        return
    _starting_workers.add(worker_key)
    task: asyncio.Task | None = None
    try:
        event = asyncio.Event()
        _active_events[worker_key] = event
        task = asyncio.create_task(_worker_loop(worker_key, event, is_project_keyed))
        _active_workers[worker_key] = task
        logger.info(f"[worker:{worker_key}] Started session queue worker")
    except Exception:
        # If the task was created but not published, cancel it so no orphan runs.
        if task is not None and worker_key not in _active_workers:
            task.cancel()
            logger.exception(
                f"[worker:{worker_key}] _ensure_worker post-create failure; cancelled orphan task"
            )
        raise
    finally:
        _starting_workers.discard(worker_key)


async def _worker_loop(
    worker_key: str, event: asyncio.Event, is_project_keyed: bool = False
) -> None:
    """Process sessions sequentially for one worker_key.

    Workers are keyed by worker_key — either project_key (PM and dev-without-slug)
    or chat_id (teammate and dev-with-slug).  Project-keyed workers serialize
    sessions that share the main working tree.  Chat-keyed workers handle
    sessions with isolated worktrees or no shared state.

    In standalone mode (VALOR_WORKER_MODE=standalone): waits indefinitely for new work.
    In bridge mode (default): runs until queue is empty, then exits.
    """
    standalone = os.environ.get("VALOR_WORKER_MODE") == "standalone"
    try:
        while True:
            # Check shutdown flag before starting new work
            if _session_state._shutdown_requested:
                logger.info(f"[worker:{worker_key}] Shutdown requested, worker exiting")
                break

            # Acquire global concurrency slot BEFORE popping — ensures that
            # transition_status("running") never occurs without a semaphore slot,
            # keeping the dashboard running count accurate.
            semaphore = _session_state._global_session_semaphore
            if semaphore is not None:
                await semaphore.acquire()
            _semaphore_acquired = semaphore is not None

            try:
                session = await _pop_agent_session(worker_key, is_project_keyed)
            except BaseException:
                if _semaphore_acquired:
                    semaphore.release()
                raise

            if session is None:
                # No work found — release the semaphore slot before waiting.
                if _semaphore_acquired:
                    semaphore.release()
                    _semaphore_acquired = False

                # Guard against event.set()/event.clear() race: if a notify fired
                # while we were in _pop_agent_session (e.g. startup health check),
                # clearing the event here would lose it. Do a cheap sync check
                # first; if there IS pending work, skip the wait entirely.
                _has_pending = bool(
                    AgentSession.query.filter(
                        **(
                            {"project_key": worker_key}
                            if is_project_keyed
                            else {"chat_id": worker_key}
                        ),
                        status="pending",
                    )
                )
                if _has_pending:
                    continue

                # Event-based drain: wait for enqueue_agent_session() to signal new work,
                # or fall back to sync query after timeout.
                event.clear()

                if standalone:
                    # Persistent mode: wait indefinitely for new work
                    await event.wait()
                    if _session_state._shutdown_requested:
                        logger.info(f"[worker:{worker_key}] Woke from wait, shutdown requested")
                        break
                    # Re-acquire semaphore before retry pop
                    if semaphore is not None:
                        await semaphore.acquire()
                        _semaphore_acquired = True
                    try:
                        session = await _pop_agent_session(worker_key, is_project_keyed)
                    except BaseException:
                        if _semaphore_acquired:
                            semaphore.release()
                            _semaphore_acquired = False
                        raise
                    if session is None:
                        if _semaphore_acquired:
                            semaphore.release()
                            _semaphore_acquired = False
                        continue
                else:
                    # Bridge mode: timeout and exit if no work arrives
                    try:
                        await asyncio.wait_for(event.wait(), timeout=DRAIN_TIMEOUT)
                        # Event fired — new work was enqueued
                        # Re-acquire semaphore before retry pop
                        if semaphore is not None:
                            await semaphore.acquire()
                            _semaphore_acquired = True
                        try:
                            session = await _pop_agent_session(worker_key, is_project_keyed)
                        except BaseException:
                            if _semaphore_acquired:
                                semaphore.release()
                                _semaphore_acquired = False
                            raise
                        if session is None:
                            if _semaphore_acquired:
                                semaphore.release()
                                _semaphore_acquired = False
                    except TimeoutError:
                        # Timeout — use sync fallback to bypass index visibility race
                        # Re-acquire semaphore before fallback pop
                        if semaphore is not None:
                            await semaphore.acquire()
                            _semaphore_acquired = True
                        try:
                            session = await _pop_agent_session_with_fallback(
                                worker_key, is_project_keyed
                            )
                        except BaseException:
                            if _semaphore_acquired:
                                semaphore.release()
                                _semaphore_acquired = False
                            raise
                        if session is None:
                            if _semaphore_acquired:
                                semaphore.release()
                                _semaphore_acquired = False

                if session is not None:
                    logger.info(f"[worker:{worker_key}] Drain guard caught session")
                elif standalone:
                    # Persistent mode: event fired but no session found — loop back
                    continue
                else:
                    # Bridge mode: exit-time safety check
                    # Re-acquire semaphore for final fallback pop
                    if semaphore is not None:
                        await semaphore.acquire()
                        _semaphore_acquired = True
                    try:
                        session = await _pop_agent_session_with_fallback(
                            worker_key, is_project_keyed
                        )
                    except BaseException:
                        if _semaphore_acquired:
                            semaphore.release()
                            _semaphore_acquired = False
                        raise
                    if session is not None:
                        logger.warning(
                            f"[worker:{worker_key}] Found pending session at exit time: "
                            f"{session.agent_session_id} — processing instead of exiting"
                        )
                    else:
                        if _semaphore_acquired:
                            semaphore.release()
                            _semaphore_acquired = False
                        logger.info(f"[worker:{worker_key}] Queue empty, worker exiting")
                        if _check_restart_flag():
                            _trigger_restart()
                        break

            session_failed = False
            session_completed = False
            # finalized_by_execute: True after _execute_agent_session returns normally
            # (happy path — nudge or completion). Prevents the outer finally block from
            # firing its stale lifecycle log / _complete_agent_session on a session
            # that was already finalized inside _execute_agent_session. See #898.
            # On crash or CancelledError, this stays False so the finally block runs.
            finalized_by_execute = False

            # Deadlock prevention lives in #1004's child-boost ordering
            # (sort_key at line 794) and force-deliver on waiting_for_children
            # (output_router.py). The swap trick was removed in #1021.

            try:
                await _execute_agent_session(session)
                finalized_by_execute = True  # reached only on non-exceptional return
            except asyncio.CancelledError:
                logger.warning(
                    "[worker:%s] Worker cancelled during session %s — session interrupted, "
                    "will be re-queued by startup recovery",
                    worker_key,
                    session.agent_session_id,
                )
                try:
                    session.log_lifecycle_transition(
                        "running", "worker cancelled — startup recovery will re-queue"
                    )
                except Exception:
                    pass
                # Do NOT call _complete_agent_session here — leave session in `running`
                # state so _recover_interrupted_agent_sessions_startup() can re-queue it
                # on next worker startup. Calling transition_status here would race with
                # the new worker's startup recovery.
                session_completed = True
                raise  # Re-raise to exit worker loop
            except Exception as e:
                # Check if this is a circuit breaker rejection — leave session pending
                from agent.sdk_client import CircuitOpenError

                if isinstance(e, CircuitOpenError):
                    logger.warning(
                        "[worker:%s] Session %s paused (circuit open) — "
                        "will resume when service recovers",
                        worker_key,
                        session.agent_session_id,
                    )
                    # Transition session to paused so it is preserved and can be drip-resumed
                    try:
                        from models.session_lifecycle import transition_status

                        transition_status(
                            session,
                            "paused",
                            reason="circuit open — worker hibernating",
                        )
                    except Exception as _ts_err:
                        logger.error(
                            "[worker:%s] Failed to transition session %s to paused: %s",
                            worker_key,
                            session.agent_session_id,
                            _ts_err,
                        )
                    # Write hibernation flag to stop further session pops
                    try:
                        from popoto.redis_db import POPOTO_REDIS_DB as _R

                        # Empty/whitespace VALOR_PROJECT_KEY falls back to "valor" so the
                        # hibernation flag lands in the same namespace AgentSession writers
                        # (and circuit_health_gate / session_pickup) use (issue #1171).
                        _v = os.environ.get("VALOR_PROJECT_KEY", "").strip()
                        _pk = _v or "valor"
                        _hib_key = f"{_pk}:worker:hibernating"
                        _was_hibernating = _R.exists(_hib_key)
                        _R.set(_hib_key, "1", ex=600)
                        if not _was_hibernating:
                            logger.warning(
                                "[worker:%s] Worker entering hibernation — circuit open",
                                worker_key,
                            )
                            # Enqueue notification asynchronously (best-effort)
                            try:
                                from agent.sustainability import send_hibernation_notification

                                send_hibernation_notification("hibernating", project_key=_pk)
                            except Exception as _notif_err:
                                logger.error(
                                    "[worker:%s] Failed to enqueue hibernation notification: %s",
                                    worker_key,
                                    _notif_err,
                                )
                    except Exception as _hib_err:
                        logger.error(
                            "[worker:%s] Failed to write hibernation flag: %s",
                            worker_key,
                            _hib_err,
                        )
                    session_completed = True
                    break  # Exit worker loop; health gate will clear flag when recovered
                else:
                    logger.error(
                        f"[worker:{worker_key}] Session {session.agent_session_id} failed: {e}"
                    )
                    session_failed = True
            finally:
                if not session_completed and not finalized_by_execute:
                    # Crash/cancel path only. On the happy path (nudge or completion),
                    # finalized_by_execute=True keeps this block from firing on a stale
                    # session object and clobbering _enqueue_nudge's authoritative write.
                    # Fix 4: Log lifecycle transition before completing (crash path only)
                    try:
                        target = "failed" if session_failed else "completed"
                        session.log_lifecycle_transition(target, "worker finally block")
                    except Exception:
                        pass
                    # Fix 3: Always save diagnostic snapshot before deleting Redis record
                    try:
                        _event = "crash" if session_failed else "complete"
                        save_session_snapshot(
                            session_id=session.session_id,
                            event=_event,
                            project_key=session.project_key,
                            branch_name=_session_branch_name(session.session_id),
                            task_summary=(
                                f"Session {session.agent_session_id} "
                                f"{'failed' if session_failed else 'terminated'}"
                            ),
                            extra_context={
                                "agent_session_id": session.agent_session_id,
                                "trigger": "finally_block",
                            },
                            working_dir=str(
                                Path(session.working_dir)
                                if hasattr(session, "working_dir")
                                else Path(__file__).parent.parent
                            ),
                        )
                    except Exception as snap_err:
                        logger.warning(
                            "Failed to save crash snapshot for %s: %s",
                            session.agent_session_id,
                            snap_err,
                        )
                    # Guard against nudge overwrite: re-read session from Redis
                    # to check if a nudge was enqueued during execution. If the
                    # session was set to "pending" by _enqueue_nudge(), or was
                    # deleted by the nudge fallback path, skip completion to avoid
                    # overwriting the nudge's status back to "completed".
                    try:
                        fresh = AgentSession.query.get(redis_key=session.db_key.redis_key)
                        if not fresh:
                            logger.info(
                                "[worker:%s] Session %s no longer exists in Redis "
                                "(likely recreated by nudge fallback) — skipping "
                                "completion",
                                worker_key,
                                session.agent_session_id,
                            )
                        elif fresh.status == "pending":
                            logger.info(
                                "[worker:%s] Session %s has status 'pending' in Redis "
                                "(nudge was enqueued) — skipping completion to "
                                "preserve nudge",
                                worker_key,
                                session.agent_session_id,
                            )
                        else:
                            await _complete_agent_session(session, failed=session_failed)
                    except Exception as guard_err:
                        logger.warning(
                            "[worker:%s] Nudge guard check failed for %s: %s "
                            "— completing session as fallback",
                            worker_key,
                            session.agent_session_id,
                            guard_err,
                        )
                        await _complete_agent_session(session, failed=session_failed)
                # Release the global concurrency slot after session is done
                if _semaphore_acquired and semaphore is not None:
                    semaphore.release()
                    _semaphore_acquired = False

            # Clear the event after processing so the next drain wait starts fresh
            event.clear()

            # Check shutdown flag after each completed session
            if _session_state._shutdown_requested:
                logger.info(f"[worker:{worker_key}] Shutdown requested after session, exiting")
                break

            # Check restart flag after each completed session
            if _check_restart_flag():
                _trigger_restart()
                break

    except asyncio.CancelledError:
        logger.info("[worker:%s] Worker loop cancelled", worker_key)
    finally:
        _active_workers.pop(worker_key, None)
        _active_events.pop(worker_key, None)


# Revival detection functions extracted to agent/session_revival.py.
# All symbols re-exported at the top of this module for backward compatibility.


# === CLI Entry Point ===


def _cli_show_status() -> None:
    """Show current queue state grouped by worker_key, with worker and health info."""
    all_sessions = list(AgentSession.query.all())
    if not all_sessions:
        print("Queue is empty.")
        return

    # Group by worker_key (the canonical routing key — project_key, chat_id, or slug)
    by_worker: dict[str, list] = {}
    for entry in all_sessions:
        key = entry.worker_key
        if key not in by_worker:
            by_worker[key] = []
        by_worker[key].append(entry)

    now_ts = time.time()

    def _to_ts_safe(val):
        if val is None:
            return 0.0
        if isinstance(val, datetime):
            return val.timestamp() if val.tzinfo else val.replace(tzinfo=UTC).timestamp()
        if isinstance(val, int | float):
            return float(val)
        return 0.0

    for worker_key, sessions_group in sorted(by_worker.items()):
        project_key = sessions_group[0].project_key if sessions_group else worker_key
        print(f"\n=== {project_key} (worker: {worker_key}) ===")
        worker = _active_workers.get(worker_key)
        worker_status = "alive" if (worker and not worker.done()) else "DEAD/missing"
        print(f"  Worker: {worker_status}")

        for session in sorted(sessions_group, key=lambda j: _to_ts_safe(j.created_at)):
            duration = ""
            started_ts = _to_ts_safe(getattr(session, "started_at", None))
            if session.status == "running" and started_ts:
                duration = f" (running {format_duration(now_ts - started_ts)})"
            elif session.created_at:
                duration = f" (queued {format_duration(now_ts - _to_ts_safe(session.created_at))})"

            session_id = (getattr(session, "session_id", "") or "")[:12]
            corr_id = (getattr(session, "correlation_id", "") or "")[:8]
            msg_preview = (session.message_text or "")[:50]
            extras = []
            if session_id:
                extras.append(f"sid={session_id}")
            if corr_id:
                extras.append(f"cid={corr_id}")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            status_line = (
                f"  [{session.status:>9}] "
                f"{session.agent_session_id}{duration}"
                f"{extra_str} - {msg_preview}"
            )
            print(status_line)

    # Health summary
    try:
        from bridge.health import get_health

        health = get_health()
        degraded = health.degraded_dependencies()
        if degraded:
            print(f"\nHealth: DEGRADED ({', '.join(degraded)})")
        else:
            print("\nHealth: OK")
    except Exception:
        print("\nHealth: unknown (bridge not running)")

    # Summary
    status_counts: dict[str, int] = {}
    for entry in all_sessions:
        status_counts[entry.status] = status_counts.get(entry.status, 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(status_counts.items()))
    print(f"Total: {len(all_sessions)} sessions ({summary})")


def _cli_flush_stuck() -> None:
    """Find and recover all stuck running sessions with dead/missing workers."""
    running = list(AgentSession.query.filter(status="running"))
    if not running:
        print("No running sessions found.")
        return

    recovered = 0
    for session in running:
        worker_key = session.worker_key
        worker = _active_workers.get(worker_key)
        is_alive = worker and not worker.done()

        if not is_alive:
            print(
                f"Recovering orphaned session {session.agent_session_id} "
                f"(project={session.project_key}, worker_key={worker_key})"
            )
            _cli_recover_single_agent_session(session)
            recovered += 1
        else:
            print(f"Skipping {session.agent_session_id} - worker still alive")

    print(f"\nRecovered {recovered}/{len(running)} running sessions.")


def _cli_flush_agent_session(agent_session_id: str) -> None:
    """Recover a specific session by ID."""
    import sys

    try:
        session = AgentSession.get_by_id(agent_session_id)
    except Exception as exc:
        logger.warning(
            "[cli] AgentSession lookup failed for %s: %s",
            agent_session_id,
            exc,
        )
        session = None

    if not session:
        print(f"Session {agent_session_id} not found.")
        sys.exit(1)

    if session.status != "running":
        print(
            f"Session {agent_session_id} is '{session.status}', not 'running'. Nothing to recover."
        )
        return

    print(f"Recovering session {agent_session_id} (project={session.project_key})")
    _cli_recover_single_agent_session(session)
    print("Done.")


def _cli_recover_single_agent_session(session: AgentSession) -> None:
    """Recover a stuck session by resetting it to pending."""
    from models.session_lifecycle import update_session

    update_session(
        session.session_id,
        new_status="pending",
        fields={"priority": "high", "started_at": None},
        expected_status="running",
        reason="CLI manual recovery",
    )
    print(f"  Re-enqueued as pending (id: {session.agent_session_id})")


def _cli_main() -> None:
    """CLI entry point for agent session queue management.

    Usage:
        python -m agent.agent_session_queue --status              # Show queue state
        python -m agent.agent_session_queue --flush-stuck       # Recover stuck sessions
        python -m agent.agent_session_queue --flush-session ID     # Recover specific session
    """
    import argparse

    parser = argparse.ArgumentParser(description="Agent session queue management CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show current queue state")
    group.add_argument(
        "--flush-stuck", action="store_true", help="Recover all stuck running sessions"
    )
    group.add_argument(
        "--flush-session",
        dest="flush_session",
        metavar="SESSION_ID",
        help="Recover a specific session by ID",
    )

    args = parser.parse_args()

    if args.status:
        _cli_show_status()
    elif args.flush_stuck:
        _cli_flush_stuck()
    elif args.flush_session:
        _cli_flush_agent_session(args.flush_session)


if __name__ == "__main__":
    _cli_main()
