"""
Agent Session Queue - FILO stack with per-project sequential workers.

Serializes agent work per project working directory so git operations
never conflict. Agent runs directly in the project's working directory.

Architecture:
- AgentSession: unified popoto Model persisted in Redis
- Worker loop: one asyncio.Task per project, processes sessions sequentially
- Revival detection: lightweight git state check, no SDK agent call
"""

import asyncio
import logging
import os
import signal
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.branch_manager import (
    get_branch_state,
    get_plan_context,
    sanitize_branch_name,
)
from agent.worktree_manager import WORKTREES_DIR, validate_workspace
from bridge.response import REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS
from bridge.session_logs import save_session_snapshot
from config.enums import ClassificationType, PersonaType, SessionType
from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# 4-tier priority ranking: lower number = higher priority
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


@dataclass
class SendToChatResult:
    """Explicit state returned from send_to_chat instead of fragile nonlocal closures.

    Replaces the _defer_reaction and _completion_sent nonlocal variables that were
    set in send_to_chat() and read in the outer _execute_agent_session() scope. Multiple code
    paths previously set these via closure mutation; this dataclass makes the state
    explicit and eliminates inconsistency if an exception occurs between set and read.
    """

    completion_sent: bool = False
    defer_reaction: bool = False
    auto_continue_count: int = 0


def classify_nudge_action(
    msg: str,
    stop_reason: str | None,
    auto_continue_count: int,
    max_nudge_count: int,
    session_status: str | None = None,
    completion_sent: bool = False,
    watchdog_unhealthy: str | None = None,
) -> str:
    """Pure function: decide what send_to_chat should do with agent output.

    Returns one of:
        "deliver"       — send to Telegram
        "deliver_fallback" — send fallback message (empty output, cap reached)
        "nudge_rate_limited" — backoff then nudge (rate limited)
        "nudge_empty"   — nudge (empty output)
        "drop"          — drop output (completion already sent)
        "deliver_already_completed" — deliver without nudge (session already done)
    """
    if session_status == "completed":
        return "deliver_already_completed"
    if completion_sent:
        return "drop"
    # Watchdog flagged this session as stuck — deliver instead of nudging
    if watchdog_unhealthy:
        return "deliver" if msg and msg.strip() else "deliver_fallback"
    if stop_reason == "rate_limited":
        return "nudge_rate_limited"
    if not msg or not msg.strip():
        if auto_continue_count + 1 <= max_nudge_count:
            return "nudge_empty"
        return "deliver_fallback"
    if auto_continue_count >= max_nudge_count:
        return "deliver"
    if stop_reason in ("end_turn", None) and len(msg.strip()) > 0:
        return "deliver"
    return "deliver"


# Nudge loop: single nudge model for bridge output routing.
# The bridge has ONE response to any non-completion: nudge.
# ChatSession owns all SDLC intelligence; the bridge just keeps it working.
MAX_NUDGE_COUNT = 50  # Safety cap — deliver to Telegram after this many nudges
NUDGE_MESSAGE = "Keep working — only stop when you need human input or you're done."


# Agent session health check constants
AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300  # 5 minutes
AGENT_SESSION_TIMEOUT_DEFAULT = 2700  # 45 minutes for standard sessions
AGENT_SESSION_TIMEOUT_BUILD = (
    9000  # 2.5 hours for build sessions (detected by /do-build in message_text)
)
AGENT_SESSION_HEALTH_MIN_RUNNING = (
    300  # Don't recover sessions running less than 5 min (race condition guard)
)


# Fields to extract from AgentSession for delete-and-recreate pattern.
# Excludes agent_session_id (AutoKeyField, auto-generated on create).
_AGENT_SESSION_FIELDS = [
    "project_key",
    # status is an IndexedField (not KeyField), so it does not affect the Redis key
    # and does not need delete-and-recreate — just mutate and save.
    "priority",
    "scheduled_after",
    "scheduling_depth",
    "created_at",
    "session_id",
    "working_dir",
    "message_text",
    "sender_name",
    "sender_id",
    "chat_id",
    "telegram_message_id",
    "chat_title",
    "revival_context",
    "work_item_slug",
    "task_list_id",
    "classification_type",
    "auto_continue_count",
    "started_at",
    "telegram_message_key",
    # Session-phase fields preserved across delete-and-recreate
    "last_activity",
    "completed_at",
    "turn_count",
    "tool_call_count",
    "log_path",
    "summary",
    "branch_name",
    "tags",
    "classification_confidence",
    "history",
    "issue_url",
    "plan_url",
    "pr_url",
    # Semantic routing fields — must be preserved across delete-and-recreate
    "context_summary",
    "expectations",
    # Steering fields — must be preserved across delete-and-recreate
    "queued_steering_messages",
    # Tracing fields — must be preserved across delete-and-recreate
    "correlation_id",
    # Claude Code identity mapping — must be preserved across delete-and-recreate
    "claude_session_uuid",
    # Session hierarchy fields — must be preserved across delete-and-recreate
    "parent_agent_session_id",
    # Session dependency fields — must be preserved across delete-and-recreate
    "stable_agent_session_id",
    "depends_on",
    "commit_sha",
    # === ChatSession/DevSession fields ===
    "session_type",
    "result_text",
    "parent_chat_session_id",
    "slug",
    # === PM self-messaging fields ===
    "pm_sent_message_ids",
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
    work_item_slug: str | None = None,
    task_list_id: str | None = None,
    classification_type: str | None = None,
    auto_continue_count: int = 0,
    correlation_id: str | None = None,
    scheduled_after: float | None = None,
    scheduling_depth: int = 0,
    parent_agent_session_id: str | None = None,
    telegram_message_key: str | None = None,
    session_type: str = SessionType.CHAT,
    depends_on: list[str] | None = None,
) -> int:
    """Create an agent session in Redis and return the pending queue depth for this chat.

    Queue is keyed by chat_id so different chat groups for the same project
    can run in parallel. project_key is preserved on the model for config lookup.

    Args:
        depends_on: List of stable_agent_session_id values this session must wait for.
            Sessions with unmet dependencies are skipped by _pop_agent_session() until
            all dependencies reach a terminal state (completed). Dependencies
            in failed or cancelled state block dependents and trigger PM
            notification.

    Bug 3 fix (issue #374): When creating a new record for a continuation
    (reply-to-resume), mark old completed records with the same session_id
    as 'superseded' to prevent ambiguity in later record selection.
    """
    # Mark old completed records as superseded to prevent duplicate-record ambiguity
    try:

        def _mark_superseded():
            old_completed = [
                s
                for s in AgentSession.query.filter(session_id=session_id)
                if s.status == "completed"
            ]
            for old in old_completed:
                old.status = "superseded"
                old.save()
                logger.info(
                    f"Marked old completed session {old.agent_session_id} as superseded "
                    f"for session_id={session_id}"
                )

        await asyncio.to_thread(_mark_superseded)
    except Exception as e:
        logger.warning(f"Failed to mark old sessions as superseded for {session_id}: {e}")

    await AgentSession.async_create(
        project_key=project_key,
        status="pending",
        priority=priority,
        created_at=time.time(),
        session_id=session_id,
        session_type=session_type,
        working_dir=working_dir,
        message_text=message_text,
        sender_name=sender_name,
        sender_id=sender_id,
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
        chat_title=chat_title,
        revival_context=revival_context,
        work_item_slug=work_item_slug,
        task_list_id=task_list_id,
        classification_type=classification_type,
        auto_continue_count=auto_continue_count,
        correlation_id=correlation_id,
        scheduled_after=scheduled_after,
        scheduling_depth=scheduling_depth,
        parent_agent_session_id=parent_agent_session_id,
        telegram_message_key=telegram_message_key,
        stable_agent_session_id=uuid.uuid4().hex,
        depends_on=depends_on,
    )

    # Initialize stage_states for SDLC sessions so the dashboard shows
    # pipeline progress from the start (not just after a dev-session runs).
    if classification_type == ClassificationType.SDLC:
        try:

            def _init_stage_states():
                from bridge.pipeline_state import PipelineStateMachine

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
            session.save()
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


# Terminal statuses for dependency resolution
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _dependencies_met(session: AgentSession) -> bool:
    """Check if all dependencies for a session are met (completed).

    A dependency is met only when its stable_agent_session_id resolves to a session
    with status 'completed'. Dependencies in 'failed' or 'cancelled'
    state block the dependent session. Missing stable_agent_session_ids (deleted from
    Redis) are treated as blocked (conservative — notify PM).

    Args:
        session: The AgentSession to check dependencies for.

    Returns:
        True if the session has no dependencies or all are completed.
        False if any dependency is not completed.
    """
    deps = session.depends_on
    if not deps:
        return True

    for dep_stable_id in deps:
        if not dep_stable_id:
            continue
        try:
            dep_sessions_list = list(
                AgentSession.query.filter(stable_agent_session_id=dep_stable_id)
            )
            if not dep_sessions_list:
                # Missing dependency — treat as blocked (conservative)
                logger.warning(
                    f"[dependency] Session {session.agent_session_id} depends on missing "
                    f"stable_agent_session_id={dep_stable_id} — blocked"
                )
                return False
            dep = dep_sessions_list[0]
            if dep.status != "completed":
                return False
        except Exception as e:
            logger.warning(
                f"[dependency] Error checking dependency {dep_stable_id} "
                f"for session {session.agent_session_id}: {e} — treating as blocked"
            )
            return False

    return True


def dependency_status(session: AgentSession) -> dict[str, str]:
    """Return the status of each dependency for a session.

    Returns a dict mapping stable_agent_session_id -> status string.
    Missing dependencies are reported as 'missing'.
    """
    deps = session.depends_on
    if not deps:
        return {}

    result = {}
    for dep_stable_id in deps:
        if not dep_stable_id:
            continue
        try:
            dep_sessions_list = list(
                AgentSession.query.filter(stable_agent_session_id=dep_stable_id)
            )
            if not dep_sessions_list:
                result[dep_stable_id] = "missing"
            else:
                result[dep_stable_id] = dep_sessions_list[0].status or "unknown"
        except Exception:
            result[dep_stable_id] = "error"
    return result


async def _pop_agent_session(chat_id: str) -> AgentSession | None:
    """
    Pop the highest priority pending session for a chat.

    Queue is keyed by chat_id so different chat groups for the same project
    can process sessions in parallel. Within a chat, sessions run sequentially.

    Order: urgent > high > normal > low, then within same priority FIFO (oldest first).
    Jobs with scheduled_after in the future are skipped (deferred execution).
    Jobs with unmet depends_on are skipped (dependency blocking).

    Dependency semantics:
    - Only 'completed' is considered "met". 'failed' and 'cancelled' block dependents.
    - Missing stable_agent_session_id (deleted from Redis) is treated as blocked (conservative).
    - Empty or None depends_on is treated as no dependencies.

    Status is an IndexedField (not KeyField), so mutating and saving is safe --
    no delete-and-recreate needed for status transitions.
    """
    pending = await AgentSession.query.async_filter(chat_id=chat_id, status="pending")
    if not pending:
        return None

    # Filter out sessions with scheduled_after in the future
    now = time.time()
    eligible = [j for j in pending if not j.scheduled_after or j.scheduled_after <= now]
    if not eligible:
        return None

    # Filter out sessions with unmet dependencies
    eligible = [j for j in eligible if _dependencies_met(j)]
    if not eligible:
        return None

    # Sort: highest priority first (4-tier), then oldest first (FIFO)
    def sort_key(j):
        prio = PRIORITY_RANK.get(j.priority, 2)  # default to normal
        return (prio, j.created_at or 0)

    eligible.sort(key=sort_key)
    chosen = eligible[0]

    # Direct field mutation -- status is an IndexedField, not a KeyField,
    # so save() correctly updates the secondary index.
    logger.info(
        f"[chat:{chat_id}] Transitioning session {chosen.agent_session_id} "
        f"(session {chosen.session_id}) pending->running"
    )
    chosen.status = "running"
    chosen.started_at = time.time()
    await chosen.async_save()

    # Log lifecycle transition for session starting execution
    try:
        chosen.log_lifecycle_transition("running", "worker picked up session")
    except Exception as e:
        logger.warning(
            f"Failed to log lifecycle transition for session {chosen.agent_session_id}: {e}"
        )

    # Drain any steering messages queued during the pending window (#619).
    # Follow-up messages arriving while the session was pending get pushed to
    # the steering queue by the bridge. We drain them here and prepend to
    # message_text so the agent sees the combined message on first run.
    try:
        from agent.steering import pop_all_steering_messages

        steering_msgs = pop_all_steering_messages(chosen.session_id)
        if steering_msgs:
            extra_texts = [
                m["text"] for m in steering_msgs if m.get("text", "").strip()
            ]
            if extra_texts:
                prepend = "\n\n".join(extra_texts)
                original = chosen.message_text or ""
                chosen.message_text = f"{original}\n\n{prepend}" if original else prepend
                await chosen.async_save()
                logger.info(
                    f"[chat:{chat_id}] Drained {len(extra_texts)} steering message(s) "
                    f"into session {chosen.agent_session_id} message_text"
                )
    except Exception as e:
        # Drain failure must not crash session start
        logger.warning(
            f"[chat:{chat_id}] Failed to drain steering messages for session "
            f"{chosen.agent_session_id} (non-fatal): {e}"
        )

    return chosen


async def _pop_agent_session_with_fallback(chat_id: str) -> AgentSession | None:
    """Pop a pending session using async_filter first, then sync fallback.

    This is a separate function from _pop_agent_session() to avoid changing the hot path.
    Called only from the drain timeout path and exit-time diagnostic in _worker_loop.

    The sync fallback bypasses to_thread() scheduling, which eliminates the
    thread-pool race between async_create index writes and async_filter reads
    that is the root cause of the pending session drain bug.
    """
    # Try the normal async path first
    session = await _pop_agent_session(chat_id)
    if session is not None:
        return session

    # Sync fallback: bypass to_thread() to avoid the index visibility race.
    # This runs a synchronous Popoto query directly, which blocks the event loop
    # briefly (single Redis SINTER + HGETALL, microseconds). Acceptable tradeoff
    # on the cold drain path for correctness.
    try:
        pending = AgentSession.query.filter(chat_id=chat_id, status="pending")
        if not pending:
            return None

        # Apply the same filtering as _pop_agent_session: scheduled_after, dependencies
        now = time.time()
        eligible = [j for j in pending if not j.scheduled_after or j.scheduled_after <= now]
        eligible = [j for j in eligible if _dependencies_met(j)]
        if not eligible:
            return None

        # Sort: highest priority first, then oldest first (FIFO)
        def sort_key(j):
            prio = PRIORITY_RANK.get(j.priority, 2)
            return (prio, j.created_at or 0)

        eligible.sort(key=sort_key)
        chosen = eligible[0]

        # Direct field mutation -- status is an IndexedField, not a KeyField.
        logger.info(
            f"[chat:{chat_id}] Sync fallback: transitioning session {chosen.agent_session_id} "
            f"(session {chosen.session_id}) pending->running"
        )
        chosen.status = "running"
        chosen.started_at = time.time()
        await chosen.async_save()

        try:
            chosen.log_lifecycle_transition("running", "worker picked up session (sync fallback)")
        except Exception as e:
            logger.warning(
                f"Failed to log lifecycle transition for session {chosen.agent_session_id}: {e}"
            )

        # Drain steering messages (same logic as _pop_agent_session) (#619)
        try:
            from agent.steering import pop_all_steering_messages

            steering_msgs = pop_all_steering_messages(chosen.session_id)
            if steering_msgs:
                extra_texts = [
                    m["text"] for m in steering_msgs if m.get("text", "").strip()
                ]
                if extra_texts:
                    prepend = "\n\n".join(extra_texts)
                    original = chosen.message_text or ""
                    chosen.message_text = f"{original}\n\n{prepend}" if original else prepend
                    await chosen.async_save()
                    logger.info(
                        f"[chat:{chat_id}] Sync fallback: drained {len(extra_texts)} "
                        f"steering message(s) into session {chosen.agent_session_id} message_text"
                    )
        except Exception as e:
            logger.warning(
                f"[chat:{chat_id}] Sync fallback: failed to drain steering messages "
                f"for session {chosen.agent_session_id} (non-fatal): {e}"
            )

        return chosen
    except Exception:
        logger.exception(f"[chat:{chat_id}] Sync fallback query failed, falling through to exit")
        return None


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
        session = AgentSession.query.get(agent_session_id)
    except Exception:
        logger.warning(f"[pm-controls] Session {agent_session_id} not found for reorder")
        return False

    if session is None or session.status != "pending":
        logger.warning(
            f"[pm-controls] Session {agent_session_id} not pending "
            f"(status={getattr(session, 'status', None)}) — cannot reorder"
        )
        return False

    session.priority = new_priority
    session.save()
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
        session = AgentSession.query.get(agent_session_id)
    except Exception:
        logger.warning(f"[pm-controls] Session {agent_session_id} not found for cancel")
        return False

    if session is None or session.status != "pending":
        logger.warning(
            f"[pm-controls] Session {agent_session_id} not pending "
            f"(status={getattr(session, 'status', None)}) — cannot cancel"
        )
        return False

    session.status = "cancelled"
    session.completed_at = time.time()
    session.save()
    logger.info(
        f"[pm-controls] Cancelled session {agent_session_id} "
        f"(stable_agent_session_id={session.stable_agent_session_id})"
    )
    return True


def retry_agent_session(stable_agent_session_id: str) -> AgentSession | None:
    """Re-queue a failed or cancelled session with the same parameters.

    Creates a new pending session preserving all fields from the original.

    Args:
        stable_agent_session_id: The stable_agent_session_id of the session to retry.

    Returns:
        The new AgentSession if retried, None if not found or not terminal.
    """
    try:
        sessions_list = list(
            AgentSession.query.filter(stable_agent_session_id=stable_agent_session_id)
        )
    except Exception:
        logger.warning(
            f"[pm-controls] stable_agent_session_id {stable_agent_session_id} not found for retry"
        )
        return None

    if not sessions_list:
        logger.warning(
            f"[pm-controls] No session found with stable_agent_session_id={stable_agent_session_id}"
        )
        return None

    session = sessions_list[0]
    if session.status not in ("failed", "cancelled"):
        logger.warning(
            f"[pm-controls] Session {session.agent_session_id} status is {session.status!r} — "
            f"can only retry failed/cancelled sessions"
        )
        return None

    fields = _extract_agent_session_fields(session)
    fields["status"] = "pending"
    fields["priority"] = "high"
    fields["started_at"] = None
    fields["completed_at"] = None
    fields["created_at"] = time.time()
    # Generate new stable_agent_session_id for the retry
    fields["stable_agent_session_id"] = uuid.uuid4().hex
    new_session = AgentSession.create(**fields)
    logger.info(
        f"[pm-controls] Retried session {session.agent_session_id} "
        f"-> {new_session.agent_session_id} "
        f"(old_stable={stable_agent_session_id}, "
        f"new_stable={new_session.stable_agent_session_id})"
    )

    # Update depends_on references in pending sessions that pointed to
    # the old stable_agent_session_id.
    # Without this, dependents would be stuck waiting for the original
    # (now failed/cancelled) session.
    new_stable_id = new_session.stable_agent_session_id
    if new_stable_id != stable_agent_session_id:
        chat_id = session.chat_id
        try:
            pending_sessions = list(AgentSession.query.filter(chat_id=chat_id, status="pending"))
            for pending in pending_sessions:
                deps = pending.depends_on
                if not deps or stable_agent_session_id not in deps:
                    continue
                # Delete-and-recreate required: depends_on is a ListField whose
                # contents are used in filter queries.  Popoto does not update
                # secondary indexes on in-place list mutation.
                updated_deps = [new_stable_id if d == stable_agent_session_id else d for d in deps]
                pending_fields = _extract_agent_session_fields(pending)
                pending.delete()
                pending_fields["depends_on"] = updated_deps
                AgentSession.create(**pending_fields)
                logger.info(
                    f"[pm-controls] Updated depends_on for session "
                    f"{pending_fields.get('stable_agent_session_id', '?')}: "
                    f"{stable_agent_session_id} -> {new_stable_id}"
                )
        except Exception as e:
            logger.warning(f"[pm-controls] Failed to update depends_on references after retry: {e}")

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
            "agent_session_id": entry.agent_session_id,
            "stable_agent_session_id": entry.stable_agent_session_id,
            "session_id": entry.session_id,
            "message_preview": (entry.message_text or "")[:100],
            "priority": entry.priority,
            "depends_on": entry.depends_on or [],
            "deps_met": _dependencies_met(entry) if status == "pending" else None,
            "created_at": entry.created_at,
            "started_at": entry.started_at,
        }
        result[status].append(summary)

    return result


async def get_active_session_for_chat(chat_id: str) -> AgentSession | None:
    """Find the active AgentSession for a given Telegram chat_id.

    Used for routing steering messages to the correct ChatSession.
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


async def _complete_agent_session(session: AgentSession, *, failed: bool = False) -> None:
    """Mark a running session as completed and delete it from Redis.

    If this session is a child (has parent_agent_session_id), finalize the parent BEFORE
    deleting the child. The completing child's intended terminal status is
    passed to _finalize_parent so it can correctly count terminal children
    even though the child's Redis status hasn't been updated yet.

    Args:
        session: The AgentSession to complete.
        failed: If True, this session failed (used for parent finalization).
    """
    # Checkpoint branch state before completion for audit trail
    try:
        checkpoint_branch_state(session)
    except Exception as e:
        logger.debug(f"[checkpoint] Non-fatal checkpoint error at completion: {e}")

    parent_agent_session_id = getattr(session, "parent_agent_session_id", None)

    # Finalize parent BEFORE deleting child, passing the completing child's
    # intended status so _finalize_parent can treat it as terminal
    if parent_agent_session_id:
        child_status = "failed" if failed else "completed"
        await _finalize_parent(
            parent_agent_session_id,
            completing_child_id=session.agent_session_id,
            completing_child_status=child_status,
        )

    await session.async_delete()


async def _finalize_parent(
    parent_agent_session_id: str,
    completing_child_id: str | None = None,
    completing_child_status: str | None = None,
) -> None:
    """Check if all children of a parent are terminal; if so, finalize the parent.

    Transitions parent from waiting_for_children to completed (all children
    succeeded) or failed (any child failed). Idempotent: no-op if parent is
    already in a terminal state or no longer exists.

    Args:
        parent_agent_session_id: The agent_session_id of the parent AgentSession.
        completing_child_id: If provided, the agent_session_id of the child that is
            currently completing. Its Redis status may still be "running",
            so completing_child_status overrides it.
        completing_child_status: The intended terminal status ("completed"
            or "failed") of the completing child.
    """
    # NOTE: This function is async def but uses synchronous Redis operations
    # (AgentSession.query.get, _transition_parent with sync delete/create).
    # This is consistent with the existing codebase patterns — popoto's Redis
    # operations are synchronous under the hood. If the codebase ever moves to
    # true async Redis, these will need updating.
    try:
        parent = AgentSession.query.get(parent_agent_session_id)
    except Exception:
        logger.warning(
            f"[session-hierarchy] Parent session {parent_agent_session_id} lookup raised "
            f"exception during finalization — treating child as orphaned"
        )
        return

    if parent is None:
        logger.warning(
            f"[session-hierarchy] Parent session {parent_agent_session_id} not found during "
            f"finalization — parent may have been deleted or already finalized"
        )
        return

    # Only finalize if parent is in waiting_for_children status
    if parent.status != "waiting_for_children":
        logger.debug(
            f"[session-hierarchy] Parent {parent_agent_session_id} status is "
            f"{parent.status!r}, skipping finalization"
        )
        return

    # Check all children
    children = parent.get_children()
    if not children:
        # Edge case: parent has no children but is waiting_for_children.
        # Transition to completed as a safety guard.
        logger.warning(
            f"[session-hierarchy] Parent {parent_agent_session_id} has no children but "
            f"status is waiting_for_children — auto-completing"
        )
        _transition_parent(parent, "completed")
        return

    # Build effective status map: override the completing child's status
    # since its Redis status hasn't been updated yet
    terminal_statuses = _TERMINAL_STATUSES

    def effective_status(child: AgentSession) -> str:
        if completing_child_id and child.agent_session_id == completing_child_id:
            return completing_child_status or "completed"
        return child.status

    child_statuses = [effective_status(c) for c in children]
    non_terminal = [s for s in child_statuses if s not in terminal_statuses]

    if non_terminal:
        # Some children still running/pending — not ready to finalize
        logger.debug(
            f"[session-hierarchy] Parent {parent_agent_session_id} has "
            f"{len(non_terminal)} non-terminal children — waiting"
        )
        return

    # All children are terminal — determine final parent status
    any_failed = any(s == "failed" for s in child_statuses)
    new_status = "failed" if any_failed else "completed"

    completed_count = sum(1 for s in child_statuses if s == "completed")
    failed_count = sum(1 for s in child_statuses if s == "failed")
    logger.info(
        f"[session-hierarchy] Finalizing parent {parent_agent_session_id}: "
        f"{completed_count} completed, {failed_count} failed -> {new_status}"
    )

    _transition_parent(parent, new_status)


def _transition_parent(parent: AgentSession, new_status: str) -> None:
    """Transition a parent session to a new status.

    Status is an IndexedField, so direct mutation and save is safe.
    No delete-and-recreate needed, which means agent_session_id stays the same
    and children's parent_agent_session_id references remain valid.
    """
    parent.status = new_status
    # Only set completed_at for terminal statuses, not for waiting_for_children
    if new_status in ("completed", "failed"):
        parent.completed_at = time.time()
    parent.save()

    logger.info(
        f"[session-hierarchy] Parent {parent.agent_session_id} transitioned to status={new_status}"
    )


def _get_pending_agent_sessions_sync(project_key: str) -> list[AgentSession]:
    """Synchronous helper for startup: get pending sessions for a project."""
    return AgentSession.query.filter(project_key=project_key, status="pending")


def _recover_interrupted_agent_sessions_startup() -> int:
    """Reset ALL running sessions to pending at startup.

    At startup, all running sessions are by definition orphaned from the previous
    process. This runs synchronously before the event loop processes messages.

    Status is an IndexedField, so direct mutation and save is safe.
    Returns the number of recovered sessions.
    """
    running_sessions = list(AgentSession.query.filter(status="running"))
    if not running_sessions:
        return 0

    count = len(running_sessions)
    for entry in running_sessions:
        chat_id = entry.chat_id or entry.project_key
        logger.warning(
            "[startup-recovery] Recovering interrupted session %s "
            "(session=%s, chat=%s, msg=%.80r...)",
            entry.agent_session_id,
            entry.session_id,
            chat_id,
            entry.message_text or "",
        )
        try:
            entry.status = "pending"
            entry.priority = "high"
            entry.started_at = None
            entry.save()
            logger.info("[startup-recovery] Recovered session %s", entry.agent_session_id)
        except Exception as e:
            logger.warning(
                "[startup-recovery] Failed to recover session %s, deleting corrupted session: %s",
                entry.session_id,
                e,
            )
            try:
                entry.delete()
            except Exception:
                pass

    logger.warning("[startup-recovery] Recovered %d interrupted session(s)", count)
    return count


# === Agent Session Health Monitor ===


def _get_agent_session_timeout(session) -> int:
    """Return the timeout in seconds for a session based on its message_text.

    Build sessions (containing '/do-build') get a longer timeout since they
    involve full SDLC cycles. All other sessions get the standard timeout.
    """
    message_text = getattr(session, "message_text", "") or ""
    if "/do-build" in message_text:
        return AGENT_SESSION_TIMEOUT_BUILD
    return AGENT_SESSION_TIMEOUT_DEFAULT


async def _agent_session_health_check() -> None:
    """Unified health check for all sessions — the single recovery mechanism.

    Scans both 'running' and 'pending' sessions:

    For RUNNING sessions:
    1. If worker is dead/missing AND running > AGENT_SESSION_HEALTH_MIN_RUNNING: recover.
    2. If exceeded timeout: recover regardless of worker state.
    3. Legacy sessions without started_at and no worker: recover.

    For PENDING sessions:
    4. If no live worker for session.chat_id AND pending > AGENT_SESSION_HEALTH_MIN_RUNNING:
       start a worker. This replaces the old _recover_stalled_pending mechanism.

    Recovery resets status to 'pending' via direct mutation and save.
    Status is an IndexedField, so no delete-and-recreate is needed.
    Only sessions whose worker is confirmed dead are touched.
    """
    now = time.time()
    checked = 0
    recovered = 0
    workers_started = 0

    # === Check RUNNING sessions_list ===
    running_sessions = list(AgentSession.query.filter(status="running"))
    for entry in running_sessions:
        checked += 1
        worker_key = entry.chat_id or entry.project_key
        worker = _active_workers.get(worker_key)
        worker_alive = worker is not None and not worker.done()

        started_at = getattr(entry, "started_at", None)
        running_seconds = (now - started_at) if started_at else None

        should_recover = False
        reason = ""

        if not worker_alive:
            if started_at is None:
                should_recover = True
                reason = "worker dead/missing, no started_at (legacy session)"
            elif running_seconds is not None and running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING:
                should_recover = True
                reason = (
                    f"worker dead/missing, running for "
                    f"{int(running_seconds)}s (>{AGENT_SESSION_HEALTH_MIN_RUNNING}s guard)"
                )
            else:
                logger.debug(
                    "[session-health] Skipping session %s - worker dead but "
                    "running only %ss (under %ss guard)",
                    entry.agent_session_id,
                    int(running_seconds) if running_seconds else "?",
                    AGENT_SESSION_HEALTH_MIN_RUNNING,
                )
        elif started_at is not None:
            timeout = _get_agent_session_timeout(entry)
            if running_seconds is not None and running_seconds > timeout:
                should_recover = True
                reason = f"exceeded timeout ({int(running_seconds)}s > {timeout}s)"

        if should_recover:
            is_local = worker_key.startswith("local")
            logger.warning(
                "[session-health] Recovering stuck session %s (chat=%s, session=%s, local=%s): %s",
                entry.agent_session_id,
                worker_key,
                entry.session_id,
                is_local,
                reason,
            )
            if is_local:
                # Local CLI sessions have no bridge worker to resume them --
                # mark abandoned instead of resetting to pending
                entry.status = "abandoned"
                entry.completed_at = now
                entry.save()
                logger.info(
                    "[session-health] Marked local session %s as abandoned (chat=%s)",
                    entry.agent_session_id,
                    worker_key,
                )
            else:
                entry.status = "pending"
                entry.priority = "high"
                entry.started_at = None
                entry.save()
                logger.info(
                    "[session-health] Recovered session %s (chat=%s)",
                    entry.agent_session_id,
                    worker_key,
                )
                _ensure_worker(worker_key)
            recovered += 1

    # === Check PENDING sessions_list ===
    pending_sessions = list(AgentSession.query.filter(status="pending"))
    for entry in pending_sessions:
        checked += 1
        worker_key = entry.chat_id or entry.project_key
        worker = _active_workers.get(worker_key)
        worker_alive = worker is not None and not worker.done()

        if worker_alive:
            # Worker exists and is processing — pending is normal queue behavior
            continue

        # No live worker — check age threshold before starting one
        created_at = getattr(entry, "created_at", None)
        if created_at is None:
            continue
        pending_seconds = now - created_at
        if pending_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING:
            if worker_key.startswith("local"):
                # Local CLI sessions can't be resumed by bridge workers
                logger.info(
                    "[session-health] Marking orphaned local pending session %s "
                    "as abandoned (chat=%s, pending %.0fs)",
                    entry.agent_session_id,
                    worker_key,
                    pending_seconds,
                )
                entry.status = "abandoned"
                entry.completed_at = now
                entry.save()
            else:
                logger.info(
                    "[session-health] Starting worker for orphaned pending "
                    "session %s (chat=%s, pending %.0fs)",
                    entry.agent_session_id,
                    worker_key,
                    pending_seconds,
                )
                _ensure_worker(worker_key)
            workers_started += 1

    if checked > 0:
        logger.info(
            "[session-health] Health check: %d checked, %d recovered, %d workers started",
            checked,
            recovered,
            workers_started,
        )


async def _agent_session_hierarchy_health_check() -> None:
    """Check for orphaned children and stuck parents in session hierarchy.

    1. Orphaned children: child's parent_agent_session_id points to a non-existent session.
       Action: clear the parent_agent_session_id field (child completes normally).
    2. Stuck parents: status is waiting_for_children but all children are terminal.
       Action: finalize the parent (transition to completed/failed).
    """
    orphans_fixed = 0
    stuck_fixed = 0

    # Check for orphaned children
    try:
        all_sessions = list(AgentSession.query.all())
        children_with_parent = [s for s in all_sessions if s.parent_agent_session_id]
        parent_ids = {s.agent_session_id for s in all_sessions}

        for child in children_with_parent:
            if child.parent_agent_session_id not in parent_ids:
                logger.warning(
                    "[session-health] Orphaned child %s: parent %s no longer exists — "
                    "clearing parent_agent_session_id",
                    child.agent_session_id,
                    child.parent_agent_session_id,
                )
                # Delete-and-recreate required: parent_agent_session_id is a KeyField,
                # so mutating it directly would corrupt the index.
                fields = _extract_agent_session_fields(child)
                child.delete()
                fields["parent_agent_session_id"] = None
                AgentSession.create(**fields)
                orphans_fixed += 1
    except Exception as e:
        logger.error("[session-health] Orphan detection failed: %s", e, exc_info=True)

    # Check for stuck parents
    try:
        waiting_parents = list(AgentSession.query.filter(status="waiting_for_children"))
        for parent in waiting_parents:
            children = parent.get_children()
            if not children:
                # No children but waiting — auto-complete
                logger.warning(
                    "[session-health] Stuck parent %s has no children — auto-completing",
                    parent.agent_session_id,
                )
                _transition_parent(parent, "completed")
                stuck_fixed += 1
                continue

            terminal_statuses = _TERMINAL_STATUSES
            non_terminal = [c for c in children if c.status not in terminal_statuses]
            if not non_terminal:
                # All children terminal but parent still waiting
                any_failed = any(c.status == "failed" for c in children)
                new_status = "failed" if any_failed else "completed"
                logger.warning(
                    "[session-health] Stuck parent %s: all %d children terminal — finalizing as %s",
                    parent.agent_session_id,
                    len(children),
                    new_status,
                )
                _transition_parent(parent, new_status)
                stuck_fixed += 1
    except Exception as e:
        logger.error("[session-health] Stuck parent detection failed: %s", e, exc_info=True)

    if orphans_fixed or stuck_fixed:
        logger.info(
            "[session-health] Hierarchy check: %d orphan(s) fixed, %d stuck parent(s) fixed",
            orphans_fixed,
            stuck_fixed,
        )


async def _dependency_health_check() -> None:
    """Detect and handle stuck dependency chains.

    Checks pending sessions with depends_on references to failed, cancelled,
    or missing sessions. These will never become eligible without intervention.
    Logs warnings for PM visibility.
    """
    try:
        pending_sessions = list(AgentSession.query.filter(status="pending"))
        stuck_count = 0
        for entry in pending_sessions:
            deps = entry.depends_on
            if not deps:
                continue
            for dep_stable_id in deps:
                if not dep_stable_id:
                    continue
                dep_sessions_list = list(
                    AgentSession.query.filter(stable_agent_session_id=dep_stable_id)
                )
                if not dep_sessions_list:
                    logger.warning(
                        "[dependency-health] Session %s blocked on missing "
                        "dependency stable_agent_session_id=%s"
                        " — PM action needed",
                        entry.agent_session_id,
                        dep_stable_id,
                    )
                    stuck_count += 1
                elif dep_sessions_list[0].status in ("failed", "cancelled"):
                    logger.warning(
                        "[dependency-health] Session %s blocked on %s "
                        "dependency stable_agent_session_id=%s"
                        " (agent_session_id=%s) — PM action needed",
                        entry.agent_session_id,
                        dep_sessions_list[0].status,
                        dep_stable_id,
                        dep_sessions_list[0].agent_session_id,
                    )
                    stuck_count += 1
        if stuck_count:
            logger.info(
                "[dependency-health] Found %d stuck dependency chain(s)",
                stuck_count,
            )
    except Exception as e:
        logger.error("[dependency-health] Health check failed: %s", e, exc_info=True)


async def _agent_session_health_loop() -> None:
    """Periodically check running sessions for liveness and timeout."""
    logger.info(
        "[session-health] Agent session health monitor started (interval=%ds)",
        AGENT_SESSION_HEALTH_CHECK_INTERVAL,
    )
    while True:
        try:
            await _agent_session_health_check()
            await _agent_session_hierarchy_health_check()
            await _dependency_health_check()
        except Exception as e:
            logger.error("[session-health] Error in health check: %s", e, exc_info=True)
        await asyncio.sleep(AGENT_SESSION_HEALTH_CHECK_INTERVAL)


# === CLI Helpers ===


def format_duration(seconds) -> str:
    """Format seconds into human-readable duration."""
    if seconds is None:
        return "N/A"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining_mins = minutes % 60
    return f"{hours}h{remaining_mins}m"


# === Per-project worker ===

# Drain timeout: how long the worker waits for new work after completing a session.
# The Event-based drain uses this as the timeout for asyncio.Event.wait().
# If no event fires within this window, the worker falls back to a sync query.
DRAIN_TIMEOUT = 1.5  # seconds

_active_workers: dict[str, asyncio.Task] = {}
_active_events: dict[str, asyncio.Event] = {}

# Project configs registered by the bridge
_project_configs: dict[str, dict] = {}

# Callbacks registered by the bridge for sending messages and reactions
SendCallback = Callable[[str, str, int, Any], Awaitable[None]]  # (chat_id, text, reply_to, session)
ReactionCallback = Callable[[str, int, str | None], Awaitable[None]]
ResponseCallback = Callable[[object, str, str, int], Awaitable[None]]

_send_callbacks: dict[str, SendCallback] = {}
_reaction_callbacks: dict[str, ReactionCallback] = {}
_response_callbacks: dict[str, ResponseCallback] = {}


def register_project_config(project_key: str, config: dict) -> None:
    """Register a project's config for use by the session queue."""
    _project_configs[project_key] = config


def get_project_config(project_key: str) -> dict:
    """Get a project's registered config."""
    return _project_configs.get(project_key, {})


def register_callbacks(
    project_key: str,
    send_callback: SendCallback,
    reaction_callback: ReactionCallback,
    response_callback: ResponseCallback | None = None,
) -> None:
    """
    Register bridge callbacks for a project.

    send_callback(chat_id, text, reply_to_msg_id) -> sends a Telegram message
    reaction_callback(chat_id, msg_id, emoji) -> sets a reaction on a message
    response_callback(event, text, chat_id, msg_id) -> sends response with file handling
    """
    _send_callbacks[project_key] = send_callback
    _reaction_callbacks[project_key] = reaction_callback
    if response_callback:
        _response_callbacks[project_key] = response_callback


# === Restart Flag (written by remote-update.sh) ===

_RESTART_FLAG = Path(__file__).parent.parent / "data" / "restart-requested"


def _check_restart_flag() -> bool:
    """Check if a restart has been requested and no sessions are running across all projects."""
    if not _RESTART_FLAG.exists():
        return False

    # Check all chats for running sessions
    for chat_key in list(_active_workers.keys()):
        running = AgentSession.query.filter(chat_id=chat_key, status="running")
        if running:
            logger.info(
                f"[chat:{chat_key}] Restart requested but "
                f"{len(running)} session(s) still running — deferring"
            )
            return False

    flag_content = _RESTART_FLAG.read_text().strip()
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
    work_item_slug: str | None = None,
    task_list_id: str | None = None,
    classification_type: str | None = None,
    auto_continue_count: int = 0,
    correlation_id: str | None = None,
    scheduled_after: float | None = None,
    scheduling_depth: int = 0,
    parent_agent_session_id: str | None = None,
    telegram_message_key: str | None = None,
    session_type: str = SessionType.CHAT,
) -> int:
    """
    Add a session to Redis and ensure worker is running.
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
        work_item_slug=work_item_slug,
        task_list_id=task_list_id,
        classification_type=classification_type,
        auto_continue_count=auto_continue_count,
        correlation_id=correlation_id,
        scheduled_after=scheduled_after,
        scheduling_depth=scheduling_depth,
        parent_agent_session_id=parent_agent_session_id,
        telegram_message_key=telegram_message_key,
        session_type=session_type,
    )
    _ensure_worker(chat_id)

    # Signal the worker that new work is available. asyncio.Event is level-triggered:
    # set() latches until clear(), so even if the worker is busy executing a session,
    # it will see the event when it next checks.
    event = _active_events.get(chat_id)
    if event is not None:
        event.set()

    log_prefix = f"[{correlation_id}]" if correlation_id else f"[{project_key}]"
    logger.info(
        f"{log_prefix} Enqueued session (priority={priority}, depth={depth}, chat={chat_id})"
    )
    return depth


def _ensure_worker(chat_id: str) -> None:
    """Start a worker for this chat if one isn't already running.

    Workers are per-chat so different chat groups (even for the same project)
    can process sessions in parallel. Within a chat, sessions run sequentially.

    Creates an asyncio.Event for the chat if one doesn't exist. The event is
    used by _worker_loop to wait for new work notifications from enqueue_agent_session().
    """
    existing = _active_workers.get(chat_id)
    if existing and not existing.done():
        return
    # Create or reset the event for this chat's worker
    event = asyncio.Event()
    _active_events[chat_id] = event
    task = asyncio.create_task(_worker_loop(chat_id, event))
    _active_workers[chat_id] = task
    logger.info(f"[chat:{chat_id}] Started session queue worker")


async def _worker_loop(chat_id: str, event: asyncio.Event) -> None:
    """
    Process sessions sequentially for one chat.
    Runs until queue is empty, then exits (restarted on next enqueue).
    After each session, checks for a restart flag written by remote-update.sh.

    Workers are per-chat_id so different chat groups can run in parallel.
    Within a chat, sessions run sequentially to prevent git conflicts.

    Uses an Event-based drain strategy to reliably pick up pending sessions:
    1. After completing a session, clear the event and wait for it (with DRAIN_TIMEOUT).
    2. If the event fires (new work enqueued), pop the next session via _pop_agent_session().
    3. If the timeout expires, use _pop_agent_session_with_fallback() which includes a
       synchronous Popoto query that bypasses the to_thread() index visibility race.
    4. Before exiting, run a final _pop_agent_session_with_fallback() as a safety net.

    CancelledError is caught explicitly to ensure proper session completion
    and worker cleanup instead of silent death.
    """
    try:
        while True:
            session = await _pop_agent_session(chat_id)
            if session is None:
                # Event-based drain: wait for enqueue_agent_session() to signal new work,
                # or fall back to sync query after timeout.
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=DRAIN_TIMEOUT)
                    # Event fired — new work was enqueued
                    session = await _pop_agent_session(chat_id)
                except TimeoutError:
                    # Timeout — use sync fallback to bypass index visibility race
                    session = await _pop_agent_session_with_fallback(chat_id)

                if session is not None:
                    logger.info(
                        f"[chat:{chat_id}] Drain guard caught session that would have been lost"
                    )
                else:
                    # Exit-time safety check: one final sync scan before giving up
                    session = await _pop_agent_session_with_fallback(chat_id)
                    if session is not None:
                        logger.warning(
                            f"[chat:{chat_id}] Found pending session at exit time: "
                            f"{session.agent_session_id} — processing instead of exiting"
                        )
                    else:
                        logger.info(f"[chat:{chat_id}] Queue empty, worker exiting")
                        if _check_restart_flag():
                            _trigger_restart()
                        break

            session_failed = False
            session_completed = False
            try:
                await _execute_agent_session(session)
            except asyncio.CancelledError:
                logger.warning(
                    "[chat:%s] Worker cancelled during session %s — completing session",
                    chat_id,
                    session.agent_session_id,
                )
                await _complete_agent_session(session, failed=True)
                session_completed = True
                raise  # Re-raise to exit worker loop
            except Exception as e:
                # Check if this is a circuit breaker rejection — leave session pending
                from agent.sdk_client import CircuitOpenError

                if isinstance(e, CircuitOpenError):
                    logger.warning(
                        "[chat:%s] Session %s deferred (circuit open) — "
                        "will retry when service recovers",
                        chat_id,
                        session.agent_session_id,
                    )
                    # Don't complete the session — leave it for health check to retry
                    session_completed = True
                    break  # Exit worker loop; health check will restart
                else:
                    logger.error(f"[chat:{chat_id}] Session {session.agent_session_id} failed: {e}")
                    session_failed = True
            finally:
                if not session_completed:
                    await _complete_agent_session(session, failed=session_failed)

            # Clear the event after processing so the next drain wait starts fresh
            event.clear()

            # Check restart flag after each completed session
            if _check_restart_flag():
                _trigger_restart()
                break

    except asyncio.CancelledError:
        logger.info("[chat:%s] Worker loop cancelled", chat_id)
    finally:
        _active_workers.pop(chat_id, None)
        _active_events.pop(chat_id, None)


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


def _diagnose_missing_session(session_id: str) -> dict:
    """Check Redis directly for session key diagnostics when Popoto query fails.

    Returns a dict with key_exists, ttl, and any error info to aid debugging
    why the session was not found by the ORM query.
    """
    try:
        import redis as redis_lib

        r = redis_lib.Redis()
        # Popoto stores keys with model-specific prefixes; scan for matches
        # TODO: Replace r.keys() with r.scan() if Redis grows beyond ~10k keys.
        #   KEYS is O(N) across the entire keyspace. Acceptable on error path
        #   with small Redis, but SCAN would be safer at scale. (PR #419 review)
        keys = r.keys(f"*{session_id}*")
        result = {"matching_keys": len(keys)}
        for key in keys[:5]:  # Cap at 5 to avoid log spam
            key_str = key.decode() if isinstance(key, bytes) else str(key)
            ttl = r.ttl(key)
            exists = r.exists(key)
            result[key_str] = {"exists": bool(exists), "ttl": ttl}
        return result
    except Exception as e:
        return {"error": str(e)}


async def _enqueue_nudge(
    session: AgentSession,
    branch_name: str,
    task_list_id: str,
    auto_continue_count: int,
    output_msg: str,
    coaching_message: str = "continue",
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
        coaching_message: Nudge message sent to the agent.
    """

    logger.info(
        f"[{session.project_key}] Nudge message "
        f"({len(coaching_message)} chars): {coaching_message[:120]!r}"
    )

    # Reuse existing AgentSession instead of creating a new one.
    # This preserves classification_type, history, links, context_summary,
    # expectations, and all other metadata that would be lost if we called
    # enqueue_agent_session() (which creates a brand new AgentSession record).
    #
    # Uses the same delete-and-recreate pattern as _pop_agent_session() to work
    # around Popoto's KeyField index corruption bug (on_save() adds to
    # new index set but never removes from old one).
    sessions = await asyncio.to_thread(
        lambda: list(AgentSession.query.filter(session_id=session.session_id))
    )
    if not sessions:
        # Diagnose why the session is missing before falling back.
        # Check Redis directly for key existence and TTL to aid debugging.
        _diag = _diagnose_missing_session(session.session_id)
        logger.error(
            f"[{session.project_key}] No session found for {session.session_id} "
            f"— falling back to recreate from AgentSession metadata. "
            f"Diagnostics: {_diag}"
        )
        # Fallback: recreate session preserving ALL metadata from the
        # underlying AgentSession that was loaded when the session was popped.
        # This prevents loss of context_summary, expectations, issue_url,
        # pr_url, history, correlation_id, and other session-phase fields.
        fields = _extract_agent_session_fields(session)
        # Override fields that change for continuation
        fields["status"] = "pending"
        fields["message_text"] = coaching_message
        fields["sender_name"] = "System (auto-continue)"
        fields["auto_continue_count"] = auto_continue_count
        fields["priority"] = "high"
        fields["task_list_id"] = task_list_id
        await AgentSession.async_create(**fields)
        _ensure_worker(session.chat_id)
        logger.info(
            f"[{session.project_key}] Recreated session "
            f"{session.session_id} from AgentSession metadata "
            f"(fallback path, auto_continue_count="
            f"{auto_continue_count})"
        )
        return

    session = sessions[0]

    # Direct mutation -- status is an IndexedField, no delete-and-recreate needed.
    session.status = "pending"
    session.message_text = coaching_message
    session.auto_continue_count = auto_continue_count
    session.priority = "high"
    session.task_list_id = task_list_id
    await session.async_save()

    _ensure_worker(session.chat_id)
    logger.info(
        f"[{session.project_key}] Reused session {session.session_id} for continuation "
        f"(auto_continue_count={auto_continue_count})"
    )


async def _execute_agent_session(session: AgentSession) -> None:
    """
    Execute a single agent session:
    1. Log calendar heartbeat (start)
    2. Run agent work via BackgroundTask + BossMessenger (in project working dir)
    3. Periodic calendar heartbeats during long-running work
    4. Set reaction based on result
    """
    from agent import BackgroundTask, BossMessenger, get_agent_response_sdk

    working_dir = Path(session.working_dir)
    allowed_root = Path.home() / "src"
    is_wt = WORKTREES_DIR in str(working_dir)
    working_dir = validate_workspace(working_dir, allowed_root, is_worktree=is_wt)

    # Restore branch state from checkpoint if this is a resumed session
    try:
        restore_branch_state(session)
    except Exception as e:
        logger.debug(f"[restore] Non-fatal restore error at session start: {e}")

    # Resolve branch: use slug + stage mapping if available, else session-based
    slug = session.work_item_slug
    stage = None
    if slug:
        # Try to read current stage from the AgentSession
        try:
            sessions = list(AgentSession.query.filter(session_id=session.session_id))
            if sessions:
                stage = sessions[0].current_stage
        except Exception:
            pass
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
                logger.warning(
                    f"[branch-mapping] Failed to create worktree for "
                    f"slug={slug}: {e} — using original working dir"
                )
    else:
        branch_name = _session_branch_name(session.session_id)

    # Compute task list ID for sub-agent task isolation
    # Tier 2: planned work uses the slug directly
    # Tier 1: ad-hoc sessions use thread-{chat_id}-{root_msg_id}
    if session.work_item_slug:
        task_list_id = session.work_item_slug
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
            agent_session.last_activity = time.time()
            agent_session.branch_name = branch_name
            # Persist task_list_id so hooks can resolve this session
            agent_session.task_list_id = task_list_id
            agent_session.save()
            agent_session.append_history("user", (session.message_text or "")[:200])
    except Exception as e:
        logger.debug(f"AgentSession update failed (non-fatal): {e}")

    # Determine session type for routing decisions
    _session_type = getattr(agent_session, "session_type", None) if agent_session else None

    # Calendar heartbeat at session start
    asyncio.create_task(_calendar_heartbeat(session.project_key, project=session.project_key))

    # Create messenger with bridge callbacks
    send_cb = _send_callbacks.get(session.project_key)
    react_cb = _reaction_callbacks.get(session.project_key)

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
        ChatSession owns all SDLC intelligence. The bridge just nudges.

        Completion detection:
        - stop_reason == "end_turn" AND output is non-empty → deliver
        - stop_reason == "rate_limited" → wait with backoff, then nudge
        - Empty output → nudge (not deliver)
        - Safety cap of MAX_NUDGE_COUNT nudges → deliver regardless
        """
        nonlocal agent_session  # Re-read from Redis for fresh stage data

        if not send_cb:
            return

        from agent.health_check import is_session_unhealthy
        from agent.sdk_client import get_stop_reason

        stop_reason = get_stop_reason(session.session_id) if session.session_id else None
        session_status = agent_session.status if agent_session else None
        unhealthy_reason = is_session_unhealthy(session.session_id) if session.session_id else None

        if unhealthy_reason:
            logger.warning(
                f"[{session.project_key}] Watchdog flagged session unhealthy: {unhealthy_reason}"
            )

        # Use reduced nudge cap for Teammate sessions
        _effective_nudge_cap = MAX_NUDGE_COUNT
        if agent_session:
            if getattr(agent_session, "session_mode", None) == PersonaType.TEAMMATE:
                from agent.teammate_handler import TEAMMATE_MAX_NUDGE_COUNT

                _effective_nudge_cap = TEAMMATE_MAX_NUDGE_COUNT

        action = classify_nudge_action(
            msg=msg,
            stop_reason=stop_reason,
            auto_continue_count=chat_state.auto_continue_count,
            max_nudge_count=_effective_nudge_cap,
            session_status=session_status,
            completion_sent=chat_state.completion_sent,
            watchdog_unhealthy=unhealthy_reason,
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

        elif action == "nudge_rate_limited":
            chat_state.auto_continue_count += 1
            logger.warning(
                f"[{session.project_key}] Rate limited — backoff then nudge "
                f"(nudge {chat_state.auto_continue_count}/{MAX_NUDGE_COUNT})"
            )
            await asyncio.sleep(5)
            await _enqueue_nudge(
                session,
                branch_name,
                task_list_id,
                chat_state.auto_continue_count,
                msg,
                coaching_message=NUDGE_MESSAGE,
            )
            chat_state.completion_sent = True
            chat_state.defer_reaction = True

        elif action == "nudge_empty":
            chat_state.auto_continue_count += 1
            logger.info(
                f"[{session.project_key}] Empty output — nudging "
                f"(nudge {chat_state.auto_continue_count}/{MAX_NUDGE_COUNT})"
            )
            await _enqueue_nudge(
                session,
                branch_name,
                task_list_id,
                chat_state.auto_continue_count,
                msg,
                coaching_message=NUDGE_MESSAGE,
            )
            chat_state.completion_sent = True
            chat_state.defer_reaction = True

        elif action == "deliver_fallback":
            logger.warning(
                f"[{session.project_key}] Empty output and nudge cap reached — delivering fallback"
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
            # wait briefly for them to be sent before the summarizer fires.
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
                    logger.debug(f"[{session.project_key}] Outbox drain check failed: {drain_err}")

            await send_cb(session.chat_id, msg, session.telegram_message_id, agent_session)
            chat_state.completion_sent = True
            logger.info(
                f"[{session.project_key}] Delivered to Telegram "
                f"(stop_reason={stop_reason}, {len(msg)} chars)"
            )

    messenger = BossMessenger(
        _send_callback=send_to_chat,
        chat_id=session.chat_id,
        session_id=session.session_id,
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

            trigger_msgs = list(TelegramMessage.query.filter(msg_id=session.telegram_message_key))
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
                    f"[{session.project_key}] telegram_message_key {session.telegram_message_key} "
                    f"not found, skipping enrichment"
                )
        except Exception as e:
            logger.debug(f"[{session.project_key}] TelegramMessage lookup failed: {e}")

    if enrich_has_media or enrich_youtube_urls or enrich_non_youtube_urls or enrich_reply_to_msg_id:
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

            trigger_msgs = list(TelegramMessage.query.filter(msg_id=session.telegram_message_key))
            if trigger_msgs and not trigger_msgs[0].agent_session_id:
                trigger_msgs[0].agent_session_id = session.agent_session_id
                trigger_msgs[0].save()
        except Exception:
            pass  # Non-critical: best-effort cross-reference

    # Run agent work directly in the project working directory.
    # Use the full registered project config (from projects.json) so that
    # downstream code (e.g., GH_REPO injection for cross-repo SDLC) has
    # access to all fields including "github", "mode", etc.
    project_config = get_project_config(session.project_key)
    if not project_config:
        project_config = {
            "_key": session.project_key,
            "working_directory": str(working_dir),
            "name": session.project_key,
        }

    async def do_work() -> str:
        return await get_agent_response_sdk(
            enriched_text,
            session.session_id,
            session.sender_name,
            session.chat_title,
            project_config,
            session.chat_id,
            session.sender_id,
            task_list_id,
            cid,
            session.agent_session_id,
        )

    task = BackgroundTask(messenger=messenger)
    await task.run(do_work(), send_result=True)

    # Wait for the background task to complete, with periodic calendar heartbeats
    last_heartbeat = time.time()
    while task.is_running:
        await asyncio.sleep(2)
        if time.time() - last_heartbeat >= CALENDAR_HEARTBEAT_INTERVAL:
            asyncio.create_task(
                _calendar_heartbeat(session.project_key, project=session.project_key)
            )
            last_heartbeat = time.time()

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
            else:
                agent_session.last_activity = time.time()
                agent_session.save()
        except Exception as e:
            logger.warning(
                f"AgentSession update failed for session {session.agent_session_id} "
                f"session {session.session_id} (operation: finalize status to "
                f"{'completed' if not task.error else 'failed'}): {e}"
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

    # Clean up steering queue — log content of any unconsumed messages
    try:
        from agent.steering import pop_all_steering_messages

        leftover = pop_all_steering_messages(session.session_id)
        if leftover:
            # Use 500-char limit (not 120) to preserve enough intent for forensics
            texts = [f"  [{m.get('sender', '?')}]: {m.get('text', '')[:500]}" for m in leftover]
            logger.warning(
                f"[{session.project_key}] {len(leftover)} unconsumed steering "
                f"message(s) dropped for session {session.session_id}:\n" + "\n".join(texts)
            )
    except Exception as e:
        logger.debug(f"Steering queue cleanup failed (non-fatal): {e}")

    # Set reaction based on result and delivery state
    # Skip if a continuation session was enqueued (defer reaction to that session)
    if react_cb and not chat_state.defer_reaction:
        # Teammate sessions: clear the processing reaction instead of setting completion emoji
        if (
            agent_session
            and getattr(agent_session, "session_mode", None) == PersonaType.TEAMMATE
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


def _session_branch_name(session_id: str) -> str:
    """Convert session_id to a git branch name."""
    safe = sanitize_branch_name(session_id)
    return f"session/{safe}"


# === Revival Detection ===

REVIVAL_COOLDOWN_SECONDS = 86400
_COOLDOWN_FILE = Path(__file__).parent.parent / "data" / "revival_cooldowns.json"


def _load_cooldowns() -> dict[str, float]:
    """Load revival cooldowns from disk."""
    try:
        if _COOLDOWN_FILE.exists():
            import json

            return json.loads(_COOLDOWN_FILE.read_text())
    except Exception as e:
        logger.warning(f"Failed to load revival cooldowns from {_COOLDOWN_FILE}: {e}")
    return {}


def _save_cooldowns(cooldowns: dict[str, float]) -> None:
    """Persist revival cooldowns to disk."""
    try:
        import json

        _COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COOLDOWN_FILE.write_text(json.dumps(cooldowns))
    except Exception as e:
        logger.warning(f"Failed to save revival cooldowns to {_COOLDOWN_FILE}: {e}")


def check_revival(project_key: str, working_dir: str, chat_id: str) -> dict | None:
    """
    Lightweight check for existing session branches with unmerged work,
    scoped strictly to this chat_id.

    Uses Popoto (Redis) as the source of truth for which sessions belong
    to this chat — avoids false positives from other chats' branches.
    Does NOT spawn an SDK agent.
    """
    wd = Path(working_dir)

    # Check cooldown (persisted to disk so it survives restarts)
    cooldowns = _load_cooldowns()
    last_notified = cooldowns.get(chat_id, 0)
    if time.time() - last_notified < REVIVAL_COOLDOWN_SECONDS:
        return None

    # Find sessions belonging to this chat via Redis (pending + running sessions)
    chat_id_str = str(chat_id)
    branches = []
    try:
        for status in ("pending", "running"):
            sessions_list = AgentSession.query.filter(project_key=project_key, status=status)
            for session in sessions_list:
                if str(session.chat_id) == chat_id_str:
                    branch = _session_branch_name(session.session_id)
                    if branch not in branches:
                        branches.append(branch)
    except Exception as e:
        logger.warning(f"[{project_key}] Redis revival check failed: {e}")

    # Verify branches actually exist in git (they may have been cleaned up)
    if branches:
        existing = []
        for branch in branches:
            try:
                result = subprocess.run(
                    ["git", "branch", "--list", branch],
                    cwd=wd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.stdout.strip():
                    existing.append(branch)
            except Exception as e:
                logger.warning(f"Failed to check if branch {branch} exists: {e}")
        branches = existing

    if not branches:
        return None

    # Build context from branch manager state
    state = get_branch_state(wd)
    plan_context = ""
    if state.active_plan:
        plan_context = get_plan_context(state.active_plan)

    return {
        "branch": branches[0],
        "all_branches": branches,
        "has_uncommitted": state.has_uncommitted_changes,
        "plan_context": plan_context[:200] if plan_context else "",
    }


def record_revival_cooldown(chat_id: str) -> None:
    """Record that we sent a revival notification so we don't spam."""
    cooldowns = _load_cooldowns()
    cooldowns[chat_id] = time.time()
    _save_cooldowns(cooldowns)


async def queue_revival_agent_session(
    revival_info: dict,
    chat_id: str,
    message_id: int,
    additional_context: str | None = None,
) -> int:
    """
    Queue a revival session (low priority) when user reacts/replies to revival notification.
    Returns queue depth.
    """
    revival_text = f"Continue the unfinished work on branch `{revival_info['branch']}`."
    if additional_context:
        revival_text += (
            f"\n\nAsked user whether to resume and user responded with: {additional_context}"
        )

    return await enqueue_agent_session(
        project_key=revival_info["project_key"],
        session_id=revival_info["session_id"],
        working_dir=revival_info["working_dir"],
        message_text=revival_text,
        sender_name="System (Revival)",
        chat_id=chat_id,
        message_id=message_id,
        priority="low",
        revival_context=additional_context,
    )


async def cleanup_stale_branches(working_dir: str, max_age_hours: float = 72) -> list[str]:
    """
    Clean up session branches older than max_age_hours.
    Returns list of cleaned branch names.
    """
    wd = Path(working_dir)
    cleaned = []

    if not wd.exists():
        return cleaned

    try:
        result = subprocess.run(
            ["git", "branch", "--list", "session/*"],
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        branches = [b.strip().lstrip("* ") for b in result.stdout.strip().split("\n") if b.strip()]

        for branch in branches:
            age_result = subprocess.run(
                ["git", "log", "-1", "--format=%ct", branch],
                cwd=wd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if age_result.returncode != 0:
                continue

            try:
                last_commit_ts = int(age_result.stdout.strip())
            except ValueError:
                continue

            age_hours = (time.time() - last_commit_ts) / 3600

            if age_hours > max_age_hours:
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=wd,
                    capture_output=True,
                    timeout=10,
                )
                cleaned.append(branch)
                logger.info(f"Cleaned stale branch: {branch} (age: {age_hours:.1f}h)")

    except Exception as e:
        logger.error(f"Branch cleanup error: {e}")

    return cleaned


# === Reflection-callable wrappers ===
# These are called by the reflection scheduler (agent/reflection_scheduler.py)
# and iterate all registered projects, so they don't need a project_key argument.


def recover_orphaned_agent_sessions_all_projects() -> int:
    """No-op: orphan recovery is now handled by the unified _agent_session_health_check.

    Kept for backward compatibility with the reflection scheduler.
    """
    logger.debug("[reflection] Orphan recovery delegated to unified health check")
    return 0


async def cleanup_stale_branches_all_projects() -> list[str]:
    """Clean up stale session branches across all registered projects.

    Called by the reflection scheduler as the 'stale-branch-cleanup' reflection.
    Returns list of all cleaned branch names.
    """
    all_cleaned = []
    for project_key, config in list(_project_configs.items()):
        working_dir = config.get("working_dir", "")
        if not working_dir:
            continue
        try:
            cleaned = await cleanup_stale_branches(working_dir)
            all_cleaned.extend(cleaned)
        except Exception as e:
            logger.error("[reflection] Branch cleanup failed for %s: %s", project_key, e)
    if not _project_configs:
        logger.debug("[reflection] No projects registered, skipping branch cleanup")
    return all_cleaned


# === CLI Entry Point ===


def _cli_show_status() -> None:
    """Show current queue state grouped by chat_id, with worker and health info."""
    all_sessions = list(AgentSession.query.all())
    if not all_sessions:
        print("Queue is empty.")
        return

    # Group by chat_id (worker key)
    by_chat: dict[str, list] = {}
    for entry in all_sessions:
        key = entry.chat_id or entry.project_key
        if key not in by_chat:
            by_chat[key] = []
        by_chat[key].append(entry)

    now = time.time()
    for chat_key, sessions_group in sorted(by_chat.items()):
        project_key = sessions_group[0].project_key if sessions_group else chat_key
        print(f"\n=== {project_key} (chat: {chat_key}) ===")
        worker = _active_workers.get(chat_key)
        worker_status = "alive" if (worker and not worker.done()) else "DEAD/missing"
        print(f"  Worker: {worker_status}")

        for session in sorted(sessions_group, key=lambda j: j.created_at or 0):
            duration = ""
            started = getattr(session, "started_at", None)
            if session.status == "running" and isinstance(started, int | float):
                duration = f" (running {format_duration(now - started)})"
            elif session.created_at:
                duration = f" (queued {format_duration(now - session.created_at)})"

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
        status_counts[session.status] = status_counts.get(session.status, 0) + 1
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
        worker_key = session.chat_id or session.project_key
        worker = _active_workers.get(worker_key)
        is_alive = worker and not worker.done()

        if not is_alive:
            print(
                f"Recovering orphaned session {session.agent_session_id} "
                f"(project={session.project_key}, chat={worker_key})"
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
        session = AgentSession.query.get(agent_session_id)
    except Exception:
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
    session.status = "pending"
    session.priority = "high"
    session.started_at = None
    session.save()
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
