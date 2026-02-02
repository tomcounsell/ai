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

logger = logging.getLogger(__name__)


MSG_MAX_CHARS = 20_000  # ~5k tokens — reasonable context limit for agent input

class RedisJob(Model):
    """A queued unit of work, persisted atomically in Redis via popoto."""

    job_id = AutoKeyField()
    project_key = KeyField()
    status = KeyField(default="pending")  # pending | running | completed | failed
    priority = Field(default="high")  # "high" (top of stack) or "low" (bottom)
    created_at = SortedField(type=float, sort_by="project_key")
    session_id = Field()
    working_dir = Field()
    message_text = Field(max_length=MSG_MAX_CHARS)
    sender_name = Field()
    chat_id = Field()
    message_id = Field(type=int)
    chat_title = Field(null=True)
    revival_context = Field(null=True, max_length=MSG_MAX_CHARS)


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
        chat_id=chat_id,
        message_id=message_id,
        chat_title=chat_title,
        revival_context=revival_context,
    )
    return await RedisJob.query.async_count(project_key=project_key, status="pending")


async def _pop_job(project_key: str) -> Job | None:
    """
    Pop the highest priority pending job for a project.

    Order: high priority first, then within same priority FILO (newest first).
    """
    pending = await RedisJob.query.async_filter(project_key=project_key, status="pending")
    if not pending:
        return None

    # Sort: high priority first, then newest first (FILO)
    def sort_key(j):
        prio = 0 if j.priority == "high" else 1
        return (prio, -(j.created_at or 0))

    pending.sort(key=sort_key)
    chosen = pending[0]
    chosen.status = "running"
    await chosen.async_save()
    return Job(chosen)


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
    Returns the number of recovered jobs.
    """
    running_jobs = RedisJob.query.filter(project_key=project_key, status="running")
    if not running_jobs:
        return 0

    for job in running_jobs:
        logger.warning(
            f"[{project_key}] Recovering interrupted job {job.job_id} "
            f"(session={job.session_id}, msg={job.message_text[:80]!r}...)"
        )
        job.status = "pending"
        job.priority = "high"
        job.save()

    logger.warning(
        f"[{project_key}] Recovered {len(running_jobs)} interrupted job(s)"
    )
    return len(running_jobs)


async def _reset_running_jobs(project_key: str) -> int:
    """
    Async version: reset running jobs back to pending during graceful shutdown.
    Returns the number of reset jobs.
    """
    running_jobs = await RedisJob.query.async_filter(
        project_key=project_key, status="running"
    )
    if not running_jobs:
        return 0

    for job in running_jobs:
        logger.info(
            f"[{project_key}] Resetting in-flight job {job.job_id} to pending for next startup"
        )
        job.status = "pending"
        job.priority = "high"
        await job.async_save()

    return len(running_jobs)


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
    text = "...[truncated]\n" + text[-(MSG_MAX_CHARS - 15):]
    logger.warning(
        f"Truncated {label}: {original_len} -> {len(text)} chars "
        f"(kept last {MSG_MAX_CHARS} chars)"
    )
    return text


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
        chat_id=chat_id,
        message_id=message_id,
        chat_title=chat_title,
        priority=priority,
        revival_context=revival_context,
    )
    _ensure_worker(project_key)
    logger.info(
        f"[{project_key}] Enqueued job "
        f"(priority={priority}, depth={depth})"
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
    """
    try:
        while True:
            job = await _pop_job(project_key)
            if job is None:
                logger.info(f"[{project_key}] Queue empty, worker exiting")
                break

            try:
                await _execute_job(job)
            except Exception as e:
                logger.error(f"[{project_key}] Job {job.job_id} failed: {e}")
            finally:
                await _complete_job(job)

    finally:
        _active_workers.pop(project_key, None)


async def _calendar_heartbeat(slug: str) -> None:
    """Fire-and-forget calendar heartbeat via subprocess."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "valor-calendar", slug,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception as e:
        logger.debug(f"Calendar heartbeat failed for '{slug}': {e}")


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

    logger.info(
        f"[{job.project_key}] Executing job {job.job_id} "
        f"(session={job.session_id}, branch={branch_name}, cwd={working_dir})"
    )

    # Calendar heartbeat at session start
    asyncio.create_task(_calendar_heartbeat(job.project_key))

    # Create messenger with bridge callbacks
    send_cb = _send_callbacks.get(job.project_key)
    react_cb = _reaction_callbacks.get(job.project_key)

    async def send_to_chat(msg: str) -> None:
        if send_cb:
            await send_cb(job.chat_id, msg, job.message_id)

    messenger = BossMessenger(
        _send_callback=send_to_chat,
        chat_id=job.chat_id,
        session_id=job.session_id,
    )

    # Run agent work directly in the project working directory
    project_config = {
        "_key": job.project_key,
        "working_directory": str(working_dir),
        "name": job.project_key,
    }

    async def do_work() -> str:
        return await get_agent_response_sdk(
            job.message_text,
            job.session_id,
            job.sender_name,
            job.chat_title,
            project_config,
            job.chat_id,
        )

    task = BackgroundTask(messenger=messenger, acknowledgment_timeout=180.0)
    await task.run(do_work(), send_result=True)

    # Wait for the background task to complete, with periodic calendar heartbeats
    last_heartbeat = time.time()
    while task.is_running:
        await asyncio.sleep(2)
        if time.time() - last_heartbeat >= CALENDAR_HEARTBEAT_INTERVAL:
            asyncio.create_task(_calendar_heartbeat(job.project_key))
            last_heartbeat = time.time()

    # Set reaction based on result
    if react_cb:
        emoji = "\U0001f44d" if not task.error else "\u274c"
        try:
            await react_cb(job.chat_id, job.message_id, emoji)
        except Exception as e:
            logger.warning(f"Failed to set reaction: {e}")

    # Auto-mark session as done after successful completion
    # This prevents false "Unfinished work detected" revival notifications
    if not task.error:
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
            logger.warning(
                f"[{job.project_key}] Failed to auto-mark session done: {e}"
            )


def _session_branch_name(session_id: str) -> str:
    """Convert session_id to a git branch name."""
    safe = sanitize_branch_name(session_id)
    return f"session/{safe}"


# === Revival Detection ===

# Cooldown: {chat_id: timestamp} — one revival prompt per chat per 24h
_revival_cooldowns: dict[str, float] = {}
REVIVAL_COOLDOWN_SECONDS = 86400


def check_revival(project_key: str, working_dir: str, chat_id: str) -> dict | None:
    """
    Lightweight check for existing session branches with unmerged work.
    Returns revival info dict if found, None otherwise.
    Does NOT spawn an SDK agent.
    """
    wd = Path(working_dir)

    # Check cooldown
    last_notified = _revival_cooldowns.get(chat_id, 0)
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
    _revival_cooldowns[chat_id] = time.time()


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
            f"\n\nAsked user whether to resume and user responded with: {additional_context}"
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


