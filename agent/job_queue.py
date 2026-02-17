"""
Job Queue - FILO stack with per-project sequential workers.

Serializes agent work per project working directory so git operations
never conflict. Agent runs directly in the project's working directory.

Architecture:
- RedisJob: popoto Model persisted atomically in Redis (replaces JSON files)
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
from pathlib import Path

from popoto import AutoKeyField, Field, KeyField, Model, SortedField

from agent.branch_manager import (
    get_branch_state,
    get_plan_context,
    sanitize_branch_name,
)
from bridge.response import REACTION_COMPLETE, REACTION_ERROR, REACTION_SUCCESS
from bridge.session_logs import save_session_snapshot

logger = logging.getLogger(__name__)


MSG_MAX_CHARS = 20_000  # ~5k tokens — reasonable context limit for agent input
MAX_AUTO_CONTINUES = 3  # Max status updates to auto-continue before sending to chat


class RedisJob(Model):
    """A queued unit of work, persisted atomically in Redis via popoto."""

    job_id = AutoKeyField()
    project_key = KeyField()
    status = KeyField(default="pending")  # pending | running | completed | failed
    priority = Field(default="high")  # "high" (top of stack) or "low" (bottom)
    created_at = SortedField(type=float, partition_by="project_key")
    session_id = Field()
    working_dir = Field()
    message_text = Field(max_length=MSG_MAX_CHARS)
    sender_name = Field()
    sender_id = Field(type=int, null=True)  # Telegram user ID for permission checking
    chat_id = Field()
    message_id = Field(type=int)
    chat_title = Field(null=True)
    revival_context = Field(null=True, max_length=MSG_MAX_CHARS)
    workflow_id = Field(null=True)  # 8-char unique workflow identifier for tracked work
    work_item_slug = Field(null=True)  # Named work item slug (tier 2)
    task_list_id = Field(null=True)  # Computed CLAUDE_CODE_TASK_LIST_ID value

    # Deferred enrichment fields (added for fast enqueue)
    has_media = Field(type=bool, default=False)
    media_type = Field(null=True)  # "photo", "voice", "document", etc.
    youtube_urls = Field(null=True)  # JSON string of [(url, video_id), ...]
    non_youtube_urls = Field(null=True)  # JSON string of [url, ...]
    reply_to_msg_id = Field(type=int, null=True)
    chat_id_for_enrichment = Field(null=True)  # Telegram chat ID for API calls
    classification_type = Field(null=True)  # Auto-classified type (bug/feature/chore)
    auto_continue_count = Field(
        type=int, default=0
    )  # Persisted across re-enqueued jobs


class Job:
    """Convenience wrapper around RedisJob for the worker interface."""

    def __init__(self, redis_job: RedisJob):
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


# Fields to extract from RedisJob for delete-and-recreate pattern.
# Excludes job_id (AutoKeyField, auto-generated on create).
_REDIS_JOB_FIELDS = [
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
]


def _extract_job_fields(redis_job: RedisJob) -> dict:
    """Extract all non-auto fields from a RedisJob instance.

    Returns a dict suitable for RedisJob.create(**fields) or
    RedisJob.async_create(**fields). Excludes job_id since that is
    an AutoKeyField and will be auto-generated on create.
    """
    return {field: getattr(redis_job, field) for field in _REDIS_JOB_FIELDS}


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
    await RedisJob.async_create(
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
    return await RedisJob.query.async_count(project_key=project_key, status="pending")


async def _pop_job(project_key: str) -> Job | None:
    """
    Pop the highest priority pending job for a project.

    Order: high priority first, then within same priority FILO (newest first).

    Uses delete-and-recreate instead of field mutation to avoid KeyField
    index corruption. Popoto's KeyField.on_save() only ADDs to the new
    status index set but never REMOVEs from the old one, so mutating
    status and calling save() leaves a stale entry in the pending index.
    """
    pending = await RedisJob.query.async_filter(
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
    new_job = await RedisJob.async_create(**fields)
    return Job(new_job)


async def _pending_depth(project_key: str) -> int:
    """Count of pending jobs for a project."""
    return await RedisJob.query.async_count(project_key=project_key, status="pending")


async def _remove_by_session(project_key: str, session_id: str) -> bool:
    """Remove all pending jobs for a session. Returns True if any removed."""
    jobs = await RedisJob.query.async_filter(project_key=project_key, status="pending")
    removed = False
    for j in jobs:
        if j.session_id == session_id:
            await j.async_delete()
            removed = True
    return removed


async def _complete_job(job: Job) -> None:
    """Mark a running job as completed and delete it from Redis."""
    await job._rj.async_delete()


def _get_pending_jobs_sync(project_key: str) -> list[RedisJob]:
    """Synchronous helper for startup: get pending jobs for a project."""
    return RedisJob.query.filter(project_key=project_key, status="pending")


def _recover_interrupted_jobs(project_key: str) -> int:
    """
    Reset any jobs stuck in 'running' status back to 'pending' with high priority.

    Called at startup to recover jobs orphaned by a previous crash or restart.
    Uses delete-and-recreate to avoid KeyField index corruption.
    Returns the number of recovered jobs.
    """
    running_jobs = list(
        RedisJob.query.filter(project_key=project_key, status="running")
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
        new_job = RedisJob.create(**fields)
        logger.info(f"[{project_key}] Recovered job {old_id} -> {new_job.job_id}")

    logger.warning(f"[{project_key}] Recovered {count} interrupted job(s)")
    return count


async def _reset_running_jobs(project_key: str) -> int:
    """
    Async version: reset running jobs back to pending during graceful shutdown.
    Uses delete-and-recreate to avoid KeyField index corruption.
    Returns the number of reset jobs.
    """
    running_jobs = await RedisJob.query.async_filter(
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
        new_job = await RedisJob.async_create(**fields)
        logger.info(f"[{project_key}] Reset job {old_id} -> {new_job.job_id}")

    return len(running_jobs)


def _recover_orphaned_jobs(project_key: str) -> int:
    """
    Scan for RedisJob objects stranded by past index corruption.

    Orphans exist in the Redis class set but not in any status KeyField index.
    This can happen when a crash occurs between delete and recreate, or when
    KeyField.on_save() adds to the new index but the old index entry was never
    cleaned up (leaving the object visible in the class set but invisible to
    status-based queries).

    Re-creates orphans with status 'pending' and priority 'high'.
    """
    from popoto.models.db_key import DB_key
    from popoto.redis_db import POPOTO_REDIS_DB

    # Get all RedisJob keys from the class set
    class_set_key = RedisJob._meta.db_class_set_key.redis_key
    all_keys = POPOTO_REDIS_DB.smembers(class_set_key)
    if not all_keys:
        return 0

    # Get all keys in status index sets
    # KeyField index pattern: $KeyF:RedisJob:status:{value}
    indexed_keys: set[bytes] = set()
    for status in ["pending", "running", "completed", "failed"]:
        index_key = DB_key(
            RedisJob._meta.fields["status"].get_special_use_field_db_key(
                RedisJob, "status"
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
            key_str = key.decode() if isinstance(key, bytes) else key

            # Load the object data from Redis hash
            data = POPOTO_REDIS_DB.hgetall(key_str)
            if not data:
                # Hash was deleted, just a stale class set entry -- clean it up
                POPOTO_REDIS_DB.srem(class_set_key, key)
                continue

            # Check if this belongs to our project
            pk_bytes = data.get(b"project_key", b"")
            pk = pk_bytes.decode() if isinstance(pk_bytes, bytes) else pk_bytes
            if pk != project_key:
                continue

            # Try to load as a RedisJob object for proper field extraction
            try:
                from popoto.models.encoding import decode_popoto_model_hashmap

                orphan_job = decode_popoto_model_hashmap(RedisJob, data)
                if orphan_job is None:
                    continue
                fields = _extract_job_fields(orphan_job)
            except Exception:
                # If decoding fails, skip this orphan
                logger.warning(
                    f"[{project_key}] Could not decode orphan {key_str}, skipping"
                )
                continue

            # Delete the orphan hash and class set entry
            POPOTO_REDIS_DB.delete(key_str)
            POPOTO_REDIS_DB.srem(class_set_key, key)

            # Create new properly-indexed job
            fields["status"] = "pending"
            fields["priority"] = "high"
            new_job = RedisJob.create(**fields)
            recovered += 1
            logger.warning(
                f"[{project_key}] Recovered orphaned job from key {key_str} "
                f"-> {new_job.job_id}"
            )
        except Exception as e:
            logger.error(f"[{project_key}] Failed to recover orphan {key}: {e}")

    if recovered:
        logger.warning(
            f"[{project_key}] Recovered {recovered} orphaned job(s) from index corruption"
        )
    return recovered


# === Per-project worker ===

_active_workers: dict[str, asyncio.Task] = {}

# Project configs registered by the bridge (for auto_merge lookup etc.)
_project_configs: dict[str, dict] = {}

# Callbacks registered by the bridge for sending messages and reactions
SendCallback = Callable[[str, str, int], Awaitable[None]]
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
        f"Truncated {label}: {original_len} -> {len(text)} chars "
        f"(kept last {MSG_MAX_CHARS} chars)"
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
        running = RedisJob.query.filter(project_key=pkey, status="running")
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
    logger.info(
        f"[{project_key}] Enqueued job " f"(priority={priority}, depth={depth})"
    )
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

    # Track session in Redis
    agent_session = None
    try:
        from models.sessions import AgentSession

        agent_session = await AgentSession.async_create(
            session_id=job.session_id,
            project_key=job.project_key,
            status="active",
            chat_id=str(job.chat_id),
            sender=job.sender_name,
            started_at=time.time(),
            last_activity=time.time(),
            tool_call_count=0,
            branch_name=branch_name,
            work_item_slug=job.work_item_slug,
            message_text=job.message_text[:20_000] if job.message_text else None,
            classification_type=job.classification_type,
        )
    except Exception as e:
        logger.debug(f"AgentSession create failed (non-fatal): {e}")

    # Calendar heartbeat at session start
    asyncio.create_task(_calendar_heartbeat(job.project_key, project=job.project_key))

    # Create messenger with bridge callbacks
    send_cb = _send_callbacks.get(job.project_key)
    react_cb = _reaction_callbacks.get(job.project_key)

    # Auto-continue counter (max 3 per session, persisted across re-enqueued jobs)
    auto_continue_count = job.auto_continue_count or 0
    # Flag to skip reaction when a continuation job is enqueued
    _defer_reaction = False
    # Gate: once a COMPLETION is sent, suppress all further outputs
    _completion_sent = False

    async def send_to_chat(msg: str) -> None:
        nonlocal auto_continue_count
        nonlocal _defer_reaction
        nonlocal _completion_sent

        if not send_cb:
            return

        # If we already sent a completion, drop all subsequent outputs.
        # The work is done — further messages are noise that spams the chat.
        if _completion_sent:
            logger.info(
                f"[{job.project_key}] Dropping suppressed output "
                f"(completion sent or auto-continued) "
                f"({len(msg)} chars): {msg[:100]!r}"
            )
            return

        # Classify the output to decide routing
        from bridge.summarizer import OutputType, classify_output

        classification = await classify_output(msg)
        logger.info(
            f"[{job.project_key}] Output classified as {classification.output_type.value} "
            f"(confidence={classification.confidence:.2f}): {classification.reason}"
        )

        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            # Status update -- don't send to chat, re-enqueue job to continue session
            auto_continue_count += 1
            logger.info(
                f"[{job.project_key}] Auto-continuing via job re-enqueue "
                f"({auto_continue_count}/{MAX_AUTO_CONTINUES})"
            )

            # Log a session snapshot for audit trail
            save_session_snapshot(
                session_id=job.session_id,
                event="auto_continue",
                project_key=job.project_key,
                branch_name=branch_name,
                task_summary=f"Auto-continued ({auto_continue_count}/{MAX_AUTO_CONTINUES})",
                extra_context={
                    "classification": classification.output_type.value,
                    "confidence": classification.confidence,
                    "reason": classification.reason,
                    "message_preview": msg[:200],
                    "coaching_context": "pending",  # Updated below after coaching builds
                },
                working_dir=str(working_dir),
            )

            # Build context-aware coaching message instead of bare "continue"
            from bridge.coach import build_coaching_message

            # Resolve plan_file from WorkflowState if available
            _plan_file = None
            if job.workflow_id:
                try:
                    from agent.workflow_state import WorkflowState

                    ws = WorkflowState.load(job.workflow_id)
                    if ws.data and ws.data.plan_file:
                        _plan_file = ws.data.plan_file
                except Exception:
                    pass  # Degrade gracefully — coach without plan context

            coaching_message = build_coaching_message(
                classification=classification,
                plan_file=_plan_file,
                job_message_text=job.message_text,
            )

            logger.info(
                f"[{job.project_key}] Coaching message "
                f"({len(coaching_message)} chars): {coaching_message[:120]!r}"
            )

            # Re-enqueue a continuation job with the same session_id
            # This uses the same mechanism as user reply-to messages:
            # SDK creates session with continue_conversation=True, resume=session_id
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
                task_list_id=job.task_list_id,
                auto_continue_count=auto_continue_count,
            )

            # Suppress BackgroundTask's final messenger.send(result) call.
            # Without this, _run_work() re-sends the SDK result through
            # send_to_chat after we already auto-continued, causing duplicate
            # messages in chat.
            _completion_sent = True

            # Signal that this job should NOT set a reaction
            # (defer to the continuation job)
            _defer_reaction = True
            return

        # For all other types (question, completion, blocker, error,
        # or max auto-continues reached), send to chat normally
        if (
            auto_continue_count >= MAX_AUTO_CONTINUES
            and classification.output_type == OutputType.STATUS_UPDATE
        ):
            logger.info(
                f"[{job.project_key}] Max auto-continues reached "
                f"({MAX_AUTO_CONTINUES}), sending to chat"
            )

        await send_cb(job.chat_id, msg, job.message_id)

        # After sending a COMPLETION, close the gate — no more outputs
        if classification.output_type == OutputType.COMPLETION:
            _completion_sent = True
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

    # Update session status in Redis
    # When auto-continue deferred, session is still active (not completed)
    if agent_session:
        try:
            if _defer_reaction:
                agent_session.status = "active"  # Continuation job pending
            else:
                agent_session.status = "completed" if not task.error else "failed"
            agent_session.last_activity = time.time()
            await agent_session.async_save()
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
    if react_cb and not _defer_reaction:
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
    if not task.error and not _defer_reaction:
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
    elif _defer_reaction:
        logger.info(
            f"[{job.project_key}] Skipping session cleanup — "
            f"continuation job enqueued (auto-continue {auto_continue_count}/{MAX_AUTO_CONTINUES})"
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
    Lightweight check for existing session branches with unmerged work.
    Returns revival info dict if found, None otherwise.
    Does NOT spawn an SDK agent.
    """
    wd = Path(working_dir)

    # Check cooldown (persisted to disk so it survives restarts)
    cooldowns = _load_cooldowns()
    last_notified = cooldowns.get(chat_id, 0)
    if time.time() - last_notified < REVIVAL_COOLDOWN_SECONDS:
        return None

    # Check for session branches
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
    except Exception:
        branches = []

    # Also check legacy state (ACTIVE-*.md or non-main branch)
    state = get_branch_state(wd)

    if not branches and state.work_status != "IN_PROGRESS":
        return None

    # Build context
    plan_context = ""
    if state.active_plan:
        plan_context = get_plan_context(state.active_plan)

    branch_info = branches[0] if branches else state.current_branch

    return {
        "branch": branch_info,
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
        revival_text += (
            "\n\nAsked user whether to resume and user "
            f"responded with: {additional_context}"
        )

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
