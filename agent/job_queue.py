"""
Job Queue - FILO stack with per-project sequential workers.

Serializes agent work per project working directory so git operations
never conflict. Agent runs directly in the project's working directory.

Architecture:
- AgentSession: unified popoto Model persisted in Redis (replaces RedisJob + SessionLog)
- Worker loop: one asyncio.Task per project, processes jobs sequentially
- Revival detection: lightweight git state check, no SDK agent call
"""

import asyncio
import logging
import os
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.branch_manager import (
    get_branch_state,
    get_plan_context,
    sanitize_branch_name,
)
from bridge.response import REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS
from bridge.session_logs import save_session_snapshot
from models.agent_session import AgentSession

logger = logging.getLogger(__name__)


@dataclass
class SendToChatResult:
    """Explicit state returned from send_to_chat instead of fragile nonlocal closures.

    Replaces the _defer_reaction and _completion_sent nonlocal variables that were
    set in send_to_chat() and read in the outer _execute_job() scope. Multiple code
    paths previously set these via closure mutation; this dataclass makes the state
    explicit and eliminates inconsistency if an exception occurs between set and read.
    """

    completion_sent: bool = False
    defer_reaction: bool = False
    auto_continue_count: int = 0


# Backward compatibility alias
RedisJob = AgentSession

MSG_MAX_CHARS = 20_000  # ~5k tokens — reasonable context limit for agent input
MAX_AUTO_CONTINUES = 3  # Max status updates to auto-continue before sending to chat
MAX_AUTO_CONTINUES_SDLC = 10  # Higher cap for SDLC jobs (stage progress is real signal)

# Planning language patterns — signals that the agent is sharing its approach
# before executing, not delivering a substantive answer. Used by the non-SDLC
# auto-continue guard to distinguish "here's my plan" from "here's the answer".
_PLANNING_PREFIXES = (
    "i'll ",
    "i will ",
    "let me ",
    "first i need to ",
    "first, i need to ",
    "first i'll ",
    "first, i'll ",
    "i'm going to ",
    "i need to ",
    "let's ",
)


def _is_planning_language(msg: str) -> bool:
    """Check if an output contains planning language indicating the agent is
    sharing its approach before executing work.

    Non-SDLC status updates that are "here's my plan" should auto-continue,
    but informational answers should be delivered to the user. This heuristic
    checks for planning prefixes in the first 500 chars of the output.

    See issue #232 and docs/plans/fix_chat_cross_wire.md for design rationale.
    """
    # Check a reasonable prefix — planning language appears early
    prefix = msg[:500].lower().strip()
    return any(prefix.startswith(p) for p in _PLANNING_PREFIXES)


# Job health check constants
JOB_HEALTH_CHECK_INTERVAL = 300  # 5 minutes
JOB_TIMEOUT_DEFAULT = 2700  # 45 minutes for standard jobs
JOB_TIMEOUT_BUILD = (
    9000  # 2.5 hours for build jobs (detected by /do-build in message_text)
)
JOB_HEALTH_MIN_RUNNING = (
    300  # Don't recover jobs running less than 5 min (race condition guard)
)


class Job:
    """Convenience wrapper around AgentSession for the worker interface."""

    def __init__(self, redis_job: AgentSession):
        self._rj = redis_job

    @property
    def job_id(self) -> str:
        return self._rj.job_id

    @property
    def project_key(self) -> str:
        return self._rj.project_key

    @property
    def session_id(self) -> str:
        return self._rj.session_id

    @property
    def working_dir(self) -> str:
        return self._rj.working_dir

    @property
    def message_text(self) -> str:
        return self._rj.message_text

    @property
    def sender_name(self) -> str:
        return self._rj.sender_name

    @property
    def sender_id(self) -> int | None:
        return self._rj.sender_id

    @property
    def chat_id(self) -> str:
        return self._rj.chat_id

    @property
    def message_id(self) -> int:
        return self._rj.message_id

    @property
    def chat_title(self) -> str | None:
        return self._rj.chat_title

    @property
    def priority(self) -> str:
        return self._rj.priority or "high"

    @property
    def revival_context(self) -> str | None:
        return self._rj.revival_context

    @property
    def created_at(self) -> float:
        return self._rj.created_at

    @property
    def workflow_id(self) -> str | None:
        return self._rj.workflow_id

    @property
    def work_item_slug(self) -> str | None:
        return self._rj.work_item_slug

    @property
    def task_list_id(self) -> str | None:
        return self._rj.task_list_id

    @property
    def has_media(self) -> bool:
        return bool(self._rj.has_media)

    @property
    def media_type(self) -> str | None:
        return self._rj.media_type

    @property
    def youtube_urls(self) -> str | None:
        return self._rj.youtube_urls

    @property
    def non_youtube_urls(self) -> str | None:
        return self._rj.non_youtube_urls

    @property
    def reply_to_msg_id(self) -> int | None:
        return self._rj.reply_to_msg_id

    @property
    def chat_id_for_enrichment(self) -> str | None:
        return self._rj.chat_id_for_enrichment

    @property
    def classification_type(self) -> str | None:
        return self._rj.classification_type

    @property
    def auto_continue_count(self) -> int:
        return self._rj.auto_continue_count or 0


# Fields to extract from AgentSession for delete-and-recreate pattern.
# Excludes job_id (AutoKeyField, auto-generated on create).
_JOB_FIELDS = [
    "project_key",
    "status",
    "priority",
    "created_at",
    "session_id",
    "working_dir",
    "message_text",
    "sender_name",
    "sender_id",
    "chat_id",
    "message_id",
    "chat_title",
    "revival_context",
    "workflow_id",
    "work_item_slug",
    "task_list_id",
    "has_media",
    "media_type",
    "youtube_urls",
    "non_youtube_urls",
    "reply_to_msg_id",
    "chat_id_for_enrichment",
    "classification_type",
    "auto_continue_count",
    "started_at",
    # Session-phase fields preserved across delete-and-recreate
    "last_activity",
    "completed_at",
    "last_transition_at",
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
]

# Backward compat alias
_REDIS_JOB_FIELDS = _JOB_FIELDS


def _extract_job_fields(redis_job: AgentSession) -> dict:
    """Extract all non-auto fields from an AgentSession instance.

    Returns a dict suitable for AgentSession.create(**fields) or
    AgentSession.async_create(**fields). Excludes job_id since that is
    an AutoKeyField and will be auto-generated on create.
    """
    return {field: getattr(redis_job, field) for field in _JOB_FIELDS}


async def _push_job(
    project_key: str,
    session_id: str,
    working_dir: str,
    message_text: str,
    sender_name: str,
    chat_id: str,
    message_id: int,
    chat_title: str | None = None,
    priority: str = "high",
    revival_context: str | None = None,
    sender_id: int | None = None,
    workflow_id: str | None = None,
    work_item_slug: str | None = None,
    task_list_id: str | None = None,
    has_media: bool = False,
    media_type: str | None = None,
    youtube_urls: str | None = None,
    non_youtube_urls: str | None = None,
    reply_to_msg_id: int | None = None,
    chat_id_for_enrichment: str | None = None,
    classification_type: str | None = None,
    auto_continue_count: int = 0,
) -> int:
    """Create a job in Redis and return the pending queue depth for this project."""
    await AgentSession.async_create(
        project_key=project_key,
        status="pending",
        priority=priority,
        created_at=time.time(),
        session_id=session_id,
        working_dir=working_dir,
        message_text=message_text,
        sender_name=sender_name,
        sender_id=sender_id,
        chat_id=chat_id,
        message_id=message_id,
        chat_title=chat_title,
        revival_context=revival_context,
        workflow_id=workflow_id,
        work_item_slug=work_item_slug,
        task_list_id=task_list_id,
        has_media=has_media,
        media_type=media_type,
        youtube_urls=youtube_urls,
        non_youtube_urls=non_youtube_urls,
        reply_to_msg_id=reply_to_msg_id,
        chat_id_for_enrichment=chat_id_for_enrichment,
        classification_type=classification_type,
        auto_continue_count=auto_continue_count,
    )

    # Log lifecycle transition for newly created pending job
    try:
        sessions = list(
            AgentSession.query.filter(session_id=session_id, status="pending")
        )
        if sessions:
            sessions[0].log_lifecycle_transition("pending", "job enqueued")
    except Exception:
        pass  # Non-fatal: don't break enqueue on logging errors

    return await AgentSession.query.async_count(
        project_key=project_key, status="pending"
    )


async def _pop_job(project_key: str) -> Job | None:
    """
    Pop the highest priority pending job for a project.

    Order: high priority first, then within same priority FILO (newest first).

    Uses delete-and-recreate instead of field mutation to avoid KeyField
    index corruption. Popoto's KeyField.on_save() only ADDs to the new
    status index set but never REMOVEs from the old one, so mutating
    status and calling save() leaves a stale entry in the pending index.
    """
    pending = await AgentSession.query.async_filter(
        project_key=project_key, status="pending"
    )
    if not pending:
        return None

    # Sort: high priority first, then newest first (FILO)
    def sort_key(j):
        prio = 0 if j.priority == "high" else 1
        return (prio, -(j.created_at or 0))

    pending.sort(key=sort_key)
    chosen = pending[0]

    # Delete-and-recreate to avoid KeyField index corruption
    fields = _extract_job_fields(chosen)
    await chosen.async_delete()
    fields["status"] = "running"
    fields["started_at"] = time.time()
    new_job = await AgentSession.async_create(**fields)

    # Log lifecycle transition for job starting execution
    try:
        new_job.log_lifecycle_transition("running", "worker picked up job")
    except Exception:
        pass  # Non-fatal: don't break pop on logging errors

    return Job(new_job)


async def _pending_depth(project_key: str) -> int:
    """Count of pending jobs for a project."""
    return await AgentSession.query.async_count(
        project_key=project_key, status="pending"
    )


async def _remove_by_session(project_key: str, session_id: str) -> bool:
    """Remove all pending jobs for a session. Returns True if any removed."""
    jobs = await AgentSession.query.async_filter(
        project_key=project_key, status="pending"
    )
    removed = False
    for j in jobs:
        if j.session_id == session_id:
            await j.async_delete()
            removed = True
    return removed


async def _complete_job(job: Job) -> None:
    """Mark a running job as completed and delete it from Redis."""
    await job._rj.async_delete()


def _get_pending_jobs_sync(project_key: str) -> list[AgentSession]:
    """Synchronous helper for startup: get pending jobs for a project."""
    return AgentSession.query.filter(project_key=project_key, status="pending")


def _recover_interrupted_jobs(project_key: str) -> int:
    """
    Reset any jobs stuck in 'running' status back to 'pending' with high priority.

    Called at startup to recover jobs orphaned by a previous crash or restart.
    Uses delete-and-recreate to avoid KeyField index corruption.
    Returns the number of recovered jobs.
    """
    running_jobs = list(
        AgentSession.query.filter(project_key=project_key, status="running")
    )
    if not running_jobs:
        return 0

    count = len(running_jobs)
    for job in running_jobs:
        old_id = job.job_id
        logger.warning(
            f"[{project_key}] Recovering interrupted job {old_id} "
            f"(session={job.session_id}, msg={job.message_text[:80]!r}...)"
        )
        fields = _extract_job_fields(job)
        job.delete()
        fields["status"] = "pending"
        fields["priority"] = "high"
        new_job = AgentSession.create(**fields)
        logger.info(f"[{project_key}] Recovered job {old_id} -> {new_job.job_id}")

    logger.warning(f"[{project_key}] Recovered {count} interrupted job(s)")
    return count


async def _reset_running_jobs(project_key: str) -> int:
    """
    Async version: reset running jobs back to pending during graceful shutdown.
    Uses delete-and-recreate to avoid KeyField index corruption.
    Returns the number of reset jobs.
    """
    running_jobs = await AgentSession.query.async_filter(
        project_key=project_key, status="running"
    )
    if not running_jobs:
        return 0

    for job in running_jobs:
        old_id = job.job_id
        logger.info(
            f"[{project_key}] Resetting in-flight job {old_id} to pending for next startup"
        )
        fields = _extract_job_fields(job)
        await job.async_delete()
        fields["status"] = "pending"
        fields["priority"] = "high"
        new_job = await AgentSession.async_create(**fields)
        logger.info(f"[{project_key}] Reset job {old_id} -> {new_job.job_id}")

    return len(running_jobs)


def _recover_orphaned_jobs(project_key: str) -> int:
    """
    Scan for AgentSession objects stranded by past index corruption.

    Orphans exist in the Redis class set but not in any status KeyField index.
    This can happen when a crash occurs between delete and recreate, or when
    KeyField.on_save() adds to the new index but the old index entry was never
    cleaned up (leaving the object visible in the class set but invisible to
    status-based queries).

    Re-creates orphans with status 'pending' and priority 'high'.
    """
    from popoto.models.db_key import DB_key
    from popoto.redis_db import POPOTO_REDIS_DB

    # Get all AgentSession keys from the class set
    class_set_key = AgentSession._meta.db_class_set_key.redis_key
    all_keys = POPOTO_REDIS_DB.smembers(class_set_key)
    if not all_keys:
        return 0

    # Get all keys in status index sets
    # KeyField index pattern: $KeyF:AgentSession:status:{value}
    indexed_keys: set[bytes] = set()
    for status in ["pending", "running", "completed", "failed"]:
        index_key = DB_key(
            AgentSession._meta.fields["status"].get_special_use_field_db_key(
                AgentSession, "status"
            ),
            status,
        ).redis_key
        indexed_keys.update(POPOTO_REDIS_DB.smembers(index_key))

    # Find orphans (in class set but not in any status index)
    orphan_keys = all_keys - indexed_keys
    if not orphan_keys:
        return 0

    recovered = 0
    for key in orphan_keys:
        try:
            # Use errors='replace' to handle corrupted UTF-8 data in Redis keys
            # gracefully. Corrupted bytes get replaced with U+FFFD rather than
            # crashing the recovery loop.
            key_str = key.decode(errors="replace") if isinstance(key, bytes) else key

            # Load the object data from Redis hash
            data = POPOTO_REDIS_DB.hgetall(key_str)
            if not data:
                # Hash was deleted, just a stale class set entry -- clean it up
                POPOTO_REDIS_DB.srem(class_set_key, key)
                continue

            # Check if this belongs to our project. Use errors='replace' to
            # handle corrupted field values without crashing.
            pk_bytes = data.get(b"project_key", b"")
            pk = (
                pk_bytes.decode(errors="replace")
                if isinstance(pk_bytes, bytes)
                else pk_bytes
            )
            if pk != project_key:
                continue

            # Try to load as an AgentSession object for proper field extraction
            try:
                from popoto.models.encoding import decode_popoto_model_hashmap

                orphan_job = decode_popoto_model_hashmap(AgentSession, data)
                if orphan_job is None:
                    continue
                fields = _extract_job_fields(orphan_job)
            except Exception as decode_err:
                # Log the specific decode error and the raw key for forensics,
                # then skip this orphan rather than crashing the whole recovery.
                logger.warning(
                    f"[{project_key}] Could not decode orphan {key_str}: "
                    f"{decode_err} (raw key bytes: {key!r}), skipping"
                )
                continue

            # Delete the orphan hash and class set entry
            POPOTO_REDIS_DB.delete(key_str)
            POPOTO_REDIS_DB.srem(class_set_key, key)

            # Create new properly-indexed job
            fields["status"] = "pending"
            fields["priority"] = "high"
            new_job = AgentSession.create(**fields)
            recovered += 1
            logger.warning(
                f"[{project_key}] Recovered orphaned job from key {key_str} -> {new_job.job_id}"
            )
        except Exception as e:
            logger.error(f"[{project_key}] Failed to recover orphan {key!r}: {e}")

    if recovered:
        logger.warning(
            f"[{project_key}] Recovered {recovered} orphaned job(s) from index corruption"
        )
    return recovered


# === Job Health Monitor ===


def _get_job_timeout(job) -> int:
    """Return the timeout in seconds for a job based on its message_text.

    Build jobs (containing '/do-build') get a longer timeout since they
    involve full SDLC cycles. All other jobs get the standard timeout.
    """
    message_text = getattr(job, "message_text", "") or ""
    if "/do-build" in message_text:
        return JOB_TIMEOUT_BUILD
    return JOB_TIMEOUT_DEFAULT


async def _job_health_check() -> None:
    """Check all running jobs for liveness and timeout, recovering stuck ones.

    For each running AgentSession:
    1. If the worker asyncio.Task is dead/missing AND the job has been running
       longer than JOB_HEALTH_MIN_RUNNING seconds, recover it.
    2. If the job has exceeded its timeout (from started_at), recover it
       regardless of worker state.
    3. Legacy jobs without started_at and no worker are also recovered.

    Recovery follows the same delete-and-recreate pattern as
    _recover_interrupted_jobs(): delete the stuck AgentSession, create a new
    one as pending with high priority, then ensure a worker is running.
    """
    running_jobs = list(AgentSession.query.filter(status="running"))
    if not running_jobs:
        logger.debug("[job-health] No running jobs found")
        return

    now = time.time()
    checked = 0
    recovered = 0

    for job in running_jobs:
        checked += 1
        project_key = job.project_key

        # Check if the worker for this project is alive
        worker = _active_workers.get(project_key)
        worker_alive = worker is not None and not worker.done()

        started_at = getattr(job, "started_at", None)
        running_seconds = (now - started_at) if started_at else None

        # Determine if this job should be recovered
        should_recover = False
        reason = ""

        if not worker_alive:
            if started_at is None:
                # Legacy job without started_at and no worker -- recover
                should_recover = True
                reason = "worker dead/missing, no started_at (legacy job)"
            elif (
                running_seconds is not None and running_seconds > JOB_HEALTH_MIN_RUNNING
            ):
                should_recover = True
                reason = (
                    f"worker dead/missing, running for "
                    f"{int(running_seconds)}s (>{JOB_HEALTH_MIN_RUNNING}s guard)"
                )
            else:
                # Worker is dead but job started recently -- race condition guard
                logger.debug(
                    "[job-health] Skipping job %s (project=%s) - worker dead but "
                    "running only %ss (under %ss guard)",
                    job.job_id,
                    project_key,
                    int(running_seconds) if running_seconds else "?",
                    JOB_HEALTH_MIN_RUNNING,
                )
        elif started_at is not None:
            # Worker is alive, but check for timeout
            timeout = _get_job_timeout(job)
            if running_seconds is not None and running_seconds > timeout:
                should_recover = True
                reason = f"exceeded timeout ({int(running_seconds)}s > {timeout}s)"

        if should_recover:
            logger.warning(
                "[job-health] Recovering stuck job %s (project=%s, session=%s): %s",
                job.job_id,
                project_key,
                job.session_id,
                reason,
            )
            # Delete-and-recreate as pending (same pattern as _recover_interrupted_jobs)
            fields = _extract_job_fields(job)
            job.delete()
            fields["status"] = "pending"
            fields["priority"] = "high"
            fields["started_at"] = None  # Reset started_at for re-processing
            new_job = AgentSession.create(**fields)
            logger.info(
                "[job-health] Recovered job %s -> %s (project=%s)",
                job.job_id,
                new_job.job_id,
                project_key,
            )
            _ensure_worker(project_key)
            recovered += 1

    logger.info(
        "[job-health] Health check complete: %d job(s) checked, %d recovered",
        checked,
        recovered,
    )


async def _job_health_loop() -> None:
    """Periodically check running jobs for liveness and timeout."""
    logger.info(
        "[job-health] Job health monitor started (interval=%ds)",
        JOB_HEALTH_CHECK_INTERVAL,
    )
    while True:
        try:
            await _job_health_check()
        except Exception as e:
            logger.error("[job-health] Error in health check: %s", e, exc_info=True)
        await asyncio.sleep(JOB_HEALTH_CHECK_INTERVAL)


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

_active_workers: dict[str, asyncio.Task] = {}

# Project configs registered by the bridge (for auto_merge lookup etc.)
_project_configs: dict[str, dict] = {}

# Callbacks registered by the bridge for sending messages and reactions
SendCallback = Callable[
    [str, str, int, Any], Awaitable[None]
]  # (chat_id, text, reply_to, session)
ReactionCallback = Callable[[str, int, str | None], Awaitable[None]]
ResponseCallback = Callable[[object, str, str, int], Awaitable[None]]

_send_callbacks: dict[str, SendCallback] = {}
_reaction_callbacks: dict[str, ReactionCallback] = {}
_response_callbacks: dict[str, ResponseCallback] = {}


def register_project_config(project_key: str, config: dict) -> None:
    """Register a project's config for use by the job queue."""
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


def _truncate_to_limit(text: str, label: str) -> str:
    """Truncate text to MSG_MAX_CHARS, keeping the tail (most recent context)."""
    if len(text) <= MSG_MAX_CHARS:
        return text
    original_len = len(text)
    text = "...[truncated]\n" + text[-(MSG_MAX_CHARS - 15) :]
    logger.warning(
        f"Truncated {label}: {original_len} -> {len(text)} chars (kept last {MSG_MAX_CHARS} chars)"
    )
    return text


# === Restart Flag (written by remote-update.sh) ===

_RESTART_FLAG = Path(__file__).parent.parent / "data" / "restart-requested"


def _check_restart_flag() -> bool:
    """Check if a restart has been requested and no jobs are running across all projects."""
    if not _RESTART_FLAG.exists():
        return False

    # Check all projects for running jobs
    for pkey in list(_active_workers.keys()):
        running = AgentSession.query.filter(project_key=pkey, status="running")
        if running:
            logger.info(
                f"[{pkey}] Restart requested but {len(running)} job(s) still running — deferring"
            )
            return False

    flag_content = _RESTART_FLAG.read_text().strip()
    logger.info(
        f"Restart flag found ({flag_content}), no running jobs — restarting bridge"
    )
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


async def enqueue_job(
    project_key: str,
    session_id: str,
    working_dir: str,
    message_text: str,
    sender_name: str,
    chat_id: str,
    message_id: int,
    chat_title: str | None = None,
    priority: str = "high",
    revival_context: str | None = None,
    sender_id: int | None = None,
    workflow_id: str | None = None,
    work_item_slug: str | None = None,
    task_list_id: str | None = None,
    has_media: bool = False,
    media_type: str | None = None,
    youtube_urls: str | None = None,
    non_youtube_urls: str | None = None,
    reply_to_msg_id: int | None = None,
    chat_id_for_enrichment: str | None = None,
    classification_type: str | None = None,
    auto_continue_count: int = 0,
) -> int:
    """
    Add a job to Redis and ensure worker is running.
    Returns queue depth after push.
    """
    message_text = _truncate_to_limit(message_text, "message_text")
    if revival_context:
        revival_context = _truncate_to_limit(revival_context, "revival_context")

    depth = await _push_job(
        project_key=project_key,
        session_id=session_id,
        working_dir=working_dir,
        message_text=message_text,
        sender_name=sender_name,
        sender_id=sender_id,
        chat_id=chat_id,
        message_id=message_id,
        chat_title=chat_title,
        workflow_id=workflow_id,
        priority=priority,
        revival_context=revival_context,
        work_item_slug=work_item_slug,
        task_list_id=task_list_id,
        has_media=has_media,
        media_type=media_type,
        youtube_urls=youtube_urls,
        non_youtube_urls=non_youtube_urls,
        reply_to_msg_id=reply_to_msg_id,
        chat_id_for_enrichment=chat_id_for_enrichment,
        classification_type=classification_type,
        auto_continue_count=auto_continue_count,
    )
    _ensure_worker(project_key)
    logger.info(f"[{project_key}] Enqueued job (priority={priority}, depth={depth})")
    return depth


def _ensure_worker(project_key: str) -> None:
    """Start a worker for this project if one isn't already running."""
    existing = _active_workers.get(project_key)
    if existing and not existing.done():
        return
    task = asyncio.create_task(_worker_loop(project_key))
    _active_workers[project_key] = task
    logger.info(f"[{project_key}] Started job queue worker")


async def _worker_loop(project_key: str) -> None:
    """
    Process jobs sequentially for one project.
    Runs until queue is empty, then exits (restarted on next enqueue).
    After each job, checks for a restart flag written by remote-update.sh.

    Includes a drain guard: when the queue appears empty, the worker yields
    to the event loop (sleep 0.1s) and re-checks once before exiting. This
    prevents losing jobs created between async_create index writes.
    """
    try:
        while True:
            job = await _pop_job(project_key)
            if job is None:
                # Drain guard: yield to event loop, let in-flight creates finish
                await asyncio.sleep(0.1)
                job = await _pop_job(project_key)
                if job is None:
                    logger.info(f"[{project_key}] Queue empty, worker exiting")
                    if _check_restart_flag():
                        _trigger_restart()
                    break
                logger.info(
                    f"[{project_key}] Drain guard caught job that would have been lost"
                )

            try:
                await _execute_job(job)
            except Exception as e:
                logger.error(f"[{project_key}] Job {job.job_id} failed: {e}")
            finally:
                await _complete_job(job)

            # Check restart flag after each completed job
            if _check_restart_flag():
                _trigger_restart()
                break

    finally:
        _active_workers.pop(project_key, None)


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


# Interval between calendar heartbeats during long-running jobs
CALENDAR_HEARTBEAT_INTERVAL = 25 * 60  # 25 minutes (fits within 30-min segments)


async def _enqueue_continuation(
    job: "Job",
    branch_name: str,
    task_list_id: str,
    auto_continue_count: int,
    output_msg: str,
    coaching_source: str = "stage_aware",
) -> None:
    """Enqueue a continuation job for stage-aware auto-continue.

    Builds a coaching message and re-enqueues the job with the same
    session_id so the SDK resumes the conversation. Used by both the
    stage-aware path and the classifier path.

    Args:
        job: The current Job being executed.
        branch_name: Git branch name for the session.
        task_list_id: Task list ID for sub-agent isolation.
        auto_continue_count: Current auto-continue count (already incremented).
        output_msg: The agent output that triggered auto-continue.
        coaching_source: Label for logging ("stage_aware" or "classifier").
    """
    from bridge.coach import build_coaching_message
    from bridge.summarizer import ClassificationResult, OutputType

    # For stage-aware continuations, build a minimal classification
    # so the coach can still provide skill-aware coaching
    classification = ClassificationResult(
        output_type=OutputType.STATUS_UPDATE,
        confidence=1.0,
        reason=f"Stage-aware auto-continue ({coaching_source})",
    )

    # Resolve plan_file from WorkflowState if available
    _plan_file = None
    if job.workflow_id:
        try:
            from agent.workflow_state import WorkflowState

            ws = WorkflowState.load(job.workflow_id)
            if ws.data and ws.data.plan_file:
                _plan_file = ws.data.plan_file
        except Exception:
            pass  # Degrade gracefully

    coaching_message = build_coaching_message(
        classification=classification,
        plan_file=_plan_file,
        job_message_text=job.message_text,
    )

    logger.info(
        f"[{job.project_key}] Coaching message ({coaching_source}) "
        f"({len(coaching_message)} chars): {coaching_message[:120]!r}"
    )

    # Propagate classification_type so continuation jobs retain the original
    # session's classification (e.g. "sdlc"). Without this, the continuation
    # job gets classification_type=None and is_sdlc_job() fails on the new
    # AgentSession record, losing stage progress and structured template rendering.
    await enqueue_job(
        project_key=job.project_key,
        session_id=job.session_id,
        working_dir=job.working_dir,
        message_text=coaching_message,
        sender_name="System (auto-continue)",
        chat_id=job.chat_id,
        message_id=job.message_id,
        priority="high",
        work_item_slug=job.work_item_slug,
        task_list_id=task_list_id,
        auto_continue_count=auto_continue_count,
        classification_type=job.classification_type,
    )


async def _execute_job(job: Job) -> None:
    """
    Execute a single job:
    1. Log calendar heartbeat (start)
    2. Run agent work via BackgroundTask + BossMessenger (in project working dir)
    3. Periodic calendar heartbeats during long-running work
    4. Set reaction based on result
    """
    from agent import BackgroundTask, BossMessenger, get_agent_response_sdk

    working_dir = Path(job.working_dir)
    branch_name = _session_branch_name(job.session_id)

    # Compute task list ID for sub-agent task isolation
    # Tier 2: planned work uses the slug directly
    # Tier 1: ad-hoc sessions use thread-{chat_id}-{root_msg_id}
    if job.work_item_slug:
        task_list_id = job.work_item_slug
    elif job.task_list_id:
        task_list_id = job.task_list_id
    else:
        # Derive from session_id which encodes chat_id and root message
        parts = job.session_id.split("_")
        root_id = parts[-1] if "_" in job.session_id else job.message_id
        task_list_id = f"thread-{job.chat_id}-{root_id}"

    logger.info(
        f"[{job.project_key}] Executing job {job.job_id} "
        f"(session={job.session_id}, branch={branch_name}, cwd={working_dir})"
    )

    # Save session snapshot at job start
    save_session_snapshot(
        session_id=job.session_id,
        event="resume",
        project_key=job.project_key,
        branch_name=branch_name,
        task_summary=f"Job {job.job_id} starting",
        extra_context={
            "job_id": job.job_id,
            "sender": job.sender_name,
            "message_preview": job.message_text[:200] if job.message_text else "",
        },
        working_dir=str(working_dir),
    )

    # Update the AgentSession (already created at enqueue time) with session-phase fields
    agent_session = None
    try:
        sessions = list(
            AgentSession.query.filter(project_key=job.project_key, status="running")
        )
        for s in sessions:
            if s.session_id == job.session_id:
                agent_session = s
                break
        if agent_session:
            agent_session.last_activity = time.time()
            agent_session.branch_name = branch_name
            # Persist task_list_id so hooks can resolve this session
            agent_session.task_list_id = task_list_id
            agent_session.save()
            agent_session.append_history("user", (job.message_text or "")[:200])
            # Force SDLC mode when classification says so (issue #246).
            # This guarantees is_sdlc_job() returns True from the start,
            # even if sub-skills fail to call session_progress.
            if agent_session.classification_type == "sdlc":
                agent_session.append_history("stage", "SDLC_MODE activated")
                logger.info(
                    f"[{job.project_key}] Forced SDLC mode for session "
                    f"{job.session_id} (classification=sdlc)"
                )
    except Exception as e:
        logger.debug(f"AgentSession update failed (non-fatal): {e}")

    # Calendar heartbeat at session start
    asyncio.create_task(_calendar_heartbeat(job.project_key, project=job.project_key))

    # Create messenger with bridge callbacks
    send_cb = _send_callbacks.get(job.project_key)
    react_cb = _reaction_callbacks.get(job.project_key)

    # Explicit state object replaces fragile nonlocal closures (_defer_reaction,
    # _completion_sent, auto_continue_count). State is passed as a mutable object
    # rather than mutated through shared closure references.
    chat_state = SendToChatResult(
        auto_continue_count=job.auto_continue_count or 0,
    )

    async def send_to_chat(msg: str) -> None:
        if not send_cb:
            return

        # If this session was already completed (e.g., by a prior duplicate job),
        # deliver the output but skip auto-continue to prevent chain reactions.
        if agent_session and agent_session.status == "completed":
            logger.info(
                f"[{job.project_key}] Session already completed — "
                f"delivering without auto-continue ({len(msg)} chars)"
            )
            await send_cb(job.chat_id, msg, job.message_id, agent_session)
            chat_state.completion_sent = True
            return

        # If we already sent a completion, drop all subsequent outputs.
        # The work is done — further messages are noise that spams the chat.
        if chat_state.completion_sent:
            logger.info(
                f"[{job.project_key}] Dropping suppressed output "
                f"(completion sent or auto-continued) "
                f"({len(msg)} chars): {msg[:100]!r}"
            )
            return

        # === Stage-aware auto-continue for SDLC jobs ===
        # Decision matrix (see docs/plans/stage_aware_auto_continue.md):
        #
        # | Pipeline state      | Output classification | Action            |
        # |---------------------|-----------------------|-------------------|
        # | Stages remaining    | (skipped)             | Auto-continue     |
        # | All stages done     | Completion            | Deliver to user   |
        # | All stages done     | Status (no evidence)  | Coach + continue  |
        # | Any stage failed    | Error/blocker         | Deliver to user   |
        # | No stages (non-SDLC)| Question              | Deliver to user   |
        # | No stages (non-SDLC)| Status                | Auto-continue     |
        #
        # For SDLC jobs, stage progress is the primary termination signal.
        # The classifier is only consulted when all stages are done or for
        # non-SDLC jobs.

        _is_sdlc = False
        _sdlc_has_remaining = False
        _sdlc_has_failed = False
        if agent_session:
            _is_sdlc = agent_session.is_sdlc_job()
            if _is_sdlc:
                _sdlc_has_remaining = agent_session.has_remaining_stages()
                _sdlc_has_failed = agent_session.has_failed_stage()

        # Determine the effective auto-continue cap for this job
        effective_max = MAX_AUTO_CONTINUES_SDLC if _is_sdlc else MAX_AUTO_CONTINUES

        if _is_sdlc and _sdlc_has_failed:
            # SDLC job with a failed stage — deliver to user immediately.
            # Don't auto-continue; the failure needs human attention.
            logger.info(f"[{job.project_key}] SDLC stage failed — delivering to user")
            await send_cb(job.chat_id, msg, job.message_id, agent_session)
            chat_state.completion_sent = True
            return

        if (
            _is_sdlc
            and _sdlc_has_remaining
            and chat_state.auto_continue_count < effective_max
        ):
            # Stage-aware error guard: before auto-continuing, check if the
            # output looks like an error. Error prose should reach the user
            # even when stage history says "still in progress".
            from bridge.summarizer import OutputType, _classify_with_heuristics

            quick_check = _classify_with_heuristics(msg[:500])
            if quick_check.output_type in (OutputType.ERROR, OutputType.BLOCKER):
                logger.warning(
                    f"[{job.project_key}] Stage-aware path detected "
                    f"{quick_check.output_type.value} in prose, "
                    f"routing to classifier instead of auto-continuing"
                )
                # Fall through to classifier-based routing below
            else:
                # SDLC job with stages remaining — auto-continue without classifier.
                # Stage progress is a stronger signal than prose classification.
                chat_state.auto_continue_count += 1
                progress = agent_session.get_stage_progress()
                logger.info(
                    f"[{job.project_key}] Stage-aware auto-continue "
                    f"({chat_state.auto_continue_count}/{effective_max}), "
                    f"progress: {progress}"
                )

                save_session_snapshot(
                    session_id=job.session_id,
                    event="auto_continue",
                    project_key=job.project_key,
                    branch_name=branch_name,
                    task_summary=(
                        f"Stage-aware auto-continue "
                        f"({chat_state.auto_continue_count}/{effective_max})"
                    ),
                    extra_context={
                        "routing": "stage_aware",
                        "stage_progress": str(progress),
                        "message_preview": msg[:200],
                    },
                    working_dir=str(working_dir),
                )

                await _enqueue_continuation(
                    job,
                    branch_name,
                    task_list_id,
                    chat_state.auto_continue_count,
                    msg,
                    coaching_source="stage_aware",
                )

                chat_state.completion_sent = True
                chat_state.defer_reaction = True
                return

        # === Classifier-based routing ===
        # Used for: non-SDLC jobs, SDLC jobs with all stages done,
        # SDLC jobs that hit the safety cap, and SDLC jobs where the
        # stage-aware error guard detected error prose.
        from bridge.summarizer import OutputType, classify_output

        classification = await classify_output(msg)
        logger.info(
            f"[{job.project_key}] Output classified as {classification.output_type.value} "
            f"(confidence={classification.confidence:.2f}): {classification.reason}"
        )

        if classification.output_type == OutputType.ERROR:
            # CRASH GUARD: Error-classified outputs bypass auto-continue entirely.
            # Without this, SDK crashes would be misclassified as status updates
            # and re-enqueued indefinitely, creating an infinite crash loop.
            # See docs/features/coaching-loop.md "Error-Classified Output Bypass".
            logger.info(
                f"[{job.project_key}] Error classified — skipping auto-continue"
            )
            # Fall through to send error to chat

        elif (
            classification.output_type == OutputType.STATUS_UPDATE
            and chat_state.auto_continue_count < effective_max
            and (_is_sdlc or _is_planning_language(msg))
        ):
            # Status update -- don't send to chat, re-enqueue job to continue session
            # For non-SDLC jobs, only auto-continue if the output contains planning
            # language (agent sharing its approach before executing). Substantive
            # answers and informational content should be delivered immediately.
            # See issue #232 for the cross-wire bug this prevents.
            chat_state.auto_continue_count += 1
            logger.info(
                f"[{job.project_key}] Auto-continuing via job re-enqueue "
                f"({chat_state.auto_continue_count}/{effective_max})"
            )

            # Log a session snapshot for audit trail
            save_session_snapshot(
                session_id=job.session_id,
                event="auto_continue",
                project_key=job.project_key,
                branch_name=branch_name,
                task_summary=(
                    f"Auto-continued ({chat_state.auto_continue_count}/{effective_max})"
                ),
                extra_context={
                    "routing": "classifier",
                    "classification": classification.output_type.value,
                    "confidence": classification.confidence,
                    "reason": classification.reason,
                    "message_preview": msg[:200],
                    "coaching_context": "pending",
                },
                working_dir=str(working_dir),
            )

            # Build coaching message and re-enqueue via shared helper
            await _enqueue_continuation(
                job=job,
                branch_name=branch_name,
                task_list_id=task_list_id,
                auto_continue_count=chat_state.auto_continue_count,
                output_msg=msg,
                coaching_source="classifier",
            )

            # Suppress BackgroundTask's final messenger.send(result) call.
            # Without this, _run_work() re-sends the SDK result through
            # send_to_chat after we already auto-continued, causing duplicate
            # messages in chat.
            chat_state.completion_sent = True

            # Signal that this job should NOT set a reaction
            # (defer to the continuation job)
            chat_state.defer_reaction = True
            return

        # For all other types (question, completion, blocker, error,
        # or max auto-continues reached), send to chat normally
        if (
            chat_state.auto_continue_count >= effective_max
            and classification.output_type == OutputType.STATUS_UPDATE
        ):
            logger.info(
                f"[{job.project_key}] Max auto-continues reached ({effective_max}), sending to chat"
            )

        await send_cb(job.chat_id, msg, job.message_id, agent_session)

        # After sending a COMPLETION, check for workarounds then close the gate
        if classification.output_type == OutputType.COMPLETION:
            if classification.has_workarounds:
                # Fire one more auto-continue to post GitHub issues for problems
                logger.info(
                    f"[{job.project_key}] Completion has workarounds — "
                    f"enqueuing issue-posting continuation"
                )
                issue_prompt = (
                    "You discovered issues. Post them as GitHub issues! "
                    "Create one issue per distinct problem you encountered "
                    "and worked around during this session. Include: what "
                    "failed, how you worked around it, and what the fix "
                    "should be."
                )
                await enqueue_job(
                    project_key=job.project_key,
                    session_id=job.session_id,
                    working_dir=job.working_dir,
                    message_text=issue_prompt,
                    sender_name="System (auto-continue)",
                    chat_id=job.chat_id,
                    message_id=job.message_id,
                    priority="high",
                    work_item_slug=job.work_item_slug,
                    task_list_id=job.task_list_id,
                    auto_continue_count=MAX_AUTO_CONTINUES,  # No further auto-continues
                )
                chat_state.defer_reaction = True

            chat_state.completion_sent = True
            logger.info(
                f"[{job.project_key}] Completion sent — suppressing further outputs"
            )

    messenger = BossMessenger(
        _send_callback=send_to_chat,
        chat_id=job.chat_id,
        session_id=job.session_id,
    )

    # Deferred enrichment: process media, YouTube, links, reply chain
    enriched_text = job.message_text
    if job.has_media or job.youtube_urls or job.non_youtube_urls or job.reply_to_msg_id:
        try:
            from bridge.enrichment import enrich_message, get_telegram_client

            tg_client = get_telegram_client()
            enriched_text = await enrich_message(
                telegram_client=tg_client,
                message_text=job.message_text,
                has_media=job.has_media,
                media_type=job.media_type,
                raw_media_message_id=job.message_id,
                youtube_urls=job.youtube_urls,
                non_youtube_urls=job.non_youtube_urls,
                reply_to_msg_id=job.reply_to_msg_id,
                chat_id=job.chat_id_for_enrichment or job.chat_id,
                sender_name=job.sender_name,
                message_id=job.message_id,
            )
        except Exception as e:
            logger.warning(
                f"[{job.project_key}] Enrichment failed, using raw text: {e}"
            )

    # Run agent work directly in the project working directory
    project_config = {
        "_key": job.project_key,
        "working_directory": str(working_dir),
        "name": job.project_key,
    }

    async def do_work() -> str:
        return await get_agent_response_sdk(
            enriched_text,
            job.session_id,
            job.sender_name,
            job.chat_title,
            project_config,
            job.chat_id,
            job.sender_id,
            job.workflow_id,
            task_list_id,
        )

    task = BackgroundTask(messenger=messenger, acknowledgment_timeout=180.0)
    await task.run(do_work(), send_result=True)

    # Wait for the background task to complete, with periodic calendar heartbeats
    last_heartbeat = time.time()
    while task.is_running:
        await asyncio.sleep(2)
        if time.time() - last_heartbeat >= CALENDAR_HEARTBEAT_INTERVAL:
            asyncio.create_task(
                _calendar_heartbeat(job.project_key, project=job.project_key)
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
                complete_transcript(job.session_id, status=final_status)
            else:
                agent_session.last_activity = time.time()
                agent_session.save()
        except Exception as e:
            logger.debug(f"AgentSession update failed (non-fatal): {e}")

    # Save session snapshot for error cases
    if task.error:
        save_session_snapshot(
            session_id=job.session_id,
            event="error",
            project_key=job.project_key,
            branch_name=branch_name,
            task_summary=f"Job {job.job_id} failed: {task.error}",
            extra_context={
                "job_id": job.job_id,
                "error": str(task.error),
                "sender": job.sender_name,
            },
            working_dir=str(working_dir),
        )

    # Clean up steering queue — log content of any unconsumed messages
    try:
        from agent.steering import pop_all_steering_messages

        leftover = pop_all_steering_messages(job.session_id)
        if leftover:
            texts = [
                f"  [{m.get('sender', '?')}]: {m.get('text', '')[:120]}"
                for m in leftover
            ]
            logger.warning(
                f"[{job.project_key}] {len(leftover)} unconsumed steering "
                f"message(s) dropped for session {job.session_id}:\n" + "\n".join(texts)
            )
    except Exception as e:
        logger.debug(f"Steering queue cleanup failed (non-fatal): {e}")

    # Set reaction based on result and delivery state
    # Skip if a continuation job was enqueued (defer reaction to that job)
    if react_cb and not chat_state.defer_reaction:
        if task.error:
            emoji = REACTION_ERROR
        elif messenger.has_communicated():
            emoji = REACTION_COMPLETE
        else:
            emoji = REACTION_SUCCESS
        try:
            await react_cb(job.chat_id, job.message_id, emoji)
        except Exception as e:
            logger.warning(f"Failed to set reaction: {e}")

    # Auto-mark session as done after successful completion
    # Skip when auto-continue deferred — continuation job will handle cleanup
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
                f"[{job.project_key}] Auto-marked session done and cleaned up branch {branch_name}"
            )
        except Exception as e:
            logger.warning(f"[{job.project_key}] Failed to auto-mark session done: {e}")

        # Save session snapshot on successful completion
        save_session_snapshot(
            session_id=job.session_id,
            event="complete",
            project_key=job.project_key,
            branch_name=branch_name,
            task_summary=f"Job {job.job_id} completed successfully",
            extra_context={
                "job_id": job.job_id,
                "sender": job.sender_name,
            },
            working_dir=str(working_dir),
        )
    elif chat_state.defer_reaction:
        logger.info(
            f"[{job.project_key}] Skipping session cleanup — "
            f"continuation job enqueued (auto-continue {chat_state.auto_continue_count})"
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
    except Exception:
        pass
    return {}


def _save_cooldowns(cooldowns: dict[str, float]) -> None:
    """Persist revival cooldowns to disk."""
    try:
        import json

        _COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COOLDOWN_FILE.write_text(json.dumps(cooldowns))
    except Exception:
        pass


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

    # Find sessions belonging to this chat via Redis (pending + running jobs)
    chat_id_str = str(chat_id)
    branches = []
    try:
        for status in ("pending", "running"):
            jobs = AgentSession.query.filter(project_key=project_key, status=status)
            for job in jobs:
                if str(job.chat_id) == chat_id_str:
                    branch = _session_branch_name(job.session_id)
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
            except Exception:
                pass
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


async def queue_revival_job(
    revival_info: dict,
    chat_id: str,
    message_id: int,
    additional_context: str | None = None,
) -> int:
    """
    Queue a revival job (low priority) when user reacts/replies to revival notification.
    Returns queue depth.
    """
    revival_text = f"Continue the unfinished work on branch `{revival_info['branch']}`."
    if additional_context:
        revival_text += f"\n\nAsked user whether to resume and user responded with: {additional_context}"

    return await enqueue_job(
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


async def cleanup_stale_branches(
    working_dir: str, max_age_hours: float = 72
) -> list[str]:
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
        branches = [
            b.strip().lstrip("* ")
            for b in result.stdout.strip().split("\n")
            if b.strip()
        ]

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


# === CLI Entry Point ===


def _cli_show_status() -> None:
    """Show current queue state grouped by project and status."""
    all_jobs = list(AgentSession.query.all())
    if not all_jobs:
        print("Queue is empty.")
        return

    # Group by project_key
    by_project: dict[str, list] = {}
    for job in all_jobs:
        key = job.project_key
        if key not in by_project:
            by_project[key] = []
        by_project[key].append(job)

    now = time.time()
    for project_key, jobs in sorted(by_project.items()):
        print(f"\n=== {project_key} ===")
        worker = _active_workers.get(project_key)
        worker_status = "alive" if (worker and not worker.done()) else "DEAD/missing"
        print(f"  Worker: {worker_status}")

        for job in sorted(jobs, key=lambda j: j.created_at or 0):
            duration = ""
            started = getattr(job, "started_at", None)
            if job.status == "running" and isinstance(started, int | float):
                duration = f" (running {format_duration(now - started)})"
            elif job.created_at:
                duration = f" (queued {format_duration(now - job.created_at)})"

            msg_preview = (job.message_text or "")[:60]
            print(f"  [{job.status:>9}] {job.job_id}{duration} - {msg_preview}")

    # Summary
    status_counts: dict[str, int] = {}
    for job in all_jobs:
        status_counts[job.status] = status_counts.get(job.status, 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(status_counts.items()))
    print(f"\nTotal: {len(all_jobs)} jobs ({summary})")


def _cli_flush_stuck() -> None:
    """Find and recover all stuck running jobs with dead/missing workers."""
    running = list(AgentSession.query.filter(status="running"))
    if not running:
        print("No running jobs found.")
        return

    recovered = 0
    for job in running:
        worker = _active_workers.get(job.project_key)
        is_alive = worker and not worker.done()

        if not is_alive:
            print(f"Recovering orphaned job {job.job_id} (project={job.project_key})")
            _cli_recover_single_job(job)
            recovered += 1
        else:
            print(f"Skipping {job.job_id} - worker still alive")

    print(f"\nRecovered {recovered}/{len(running)} running jobs.")


def _cli_flush_job(job_id: str) -> None:
    """Recover a specific job by ID."""
    import sys

    try:
        job = AgentSession.query.get(job_id)
    except Exception:
        job = None

    if not job:
        print(f"Job {job_id} not found.")
        sys.exit(1)

    if job.status != "running":
        print(f"Job {job_id} is '{job.status}', not 'running'. Nothing to recover.")
        return

    print(f"Recovering job {job_id} (project={job.project_key})")
    _cli_recover_single_job(job)
    print("Done.")


def _cli_recover_single_job(job: AgentSession) -> None:
    """Delete a stuck job and recreate as pending."""
    fields = _extract_job_fields(job)

    # Delete the stuck job
    job.delete()

    # Re-create as pending with high priority
    fields["status"] = "pending"
    fields["priority"] = "high"
    fields["started_at"] = None
    new_job = AgentSession.create(**fields)
    print(f"  Re-enqueued as pending (new id: {new_job.job_id})")


def _cli_main() -> None:
    """CLI entry point for job queue management.

    Usage:
        python -m agent.job_queue --status          # Show queue state
        python -m agent.job_queue --flush-stuck      # Recover all stuck running jobs
        python -m agent.job_queue --flush-job ID     # Recover specific job
    """
    import argparse

    parser = argparse.ArgumentParser(description="Job queue management CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show current queue state")
    group.add_argument(
        "--flush-stuck", action="store_true", help="Recover all stuck running jobs"
    )
    group.add_argument(
        "--flush-job", metavar="JOB_ID", help="Recover a specific job by ID"
    )

    args = parser.parse_args()

    if args.status:
        _cli_show_status()
    elif args.flush_stuck:
        _cli_flush_stuck()
    elif args.flush_job:
        _cli_flush_job(args.flush_job)


if __name__ == "__main__":
    _cli_main()
