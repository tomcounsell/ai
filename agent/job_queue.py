"""
Job Queue - FILO stack with per-project workers and session branching.

Serializes agent work per project working directory so git operations
never conflict. Each session gets its own feature branch.

Architecture:
- RedisJob: popoto Model persisted atomically in Redis (replaces JSON files)
- Worker loop: one asyncio.Task per project, processes jobs sequentially
- Revival detection: lightweight git state check, no SDK agent call
"""

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Callable, Awaitable

from popoto import Model, AutoKeyField, KeyField, SortedField, Field

from agent.branch_manager import (
    get_branch_state,
    return_to_main,
    has_uncommitted_changes,
    sanitize_branch_name,
    get_plan_context,
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
    Process jobs in parallel for one project using git worktrees.
    Runs until queue is empty, then exits (restarted on next enqueue).
    """
    active_tasks = set()
    max_concurrent = 3  # Limit concurrent jobs per project

    try:
        while True:
            # Wait if we're at max concurrency
            while len(active_tasks) >= max_concurrent:
                done, active_tasks = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        await task  # Re-raise any exceptions
                    except Exception as e:
                        logger.error(f"[{project_key}] Job task failed: {e}")

            # Try to pop a job
            job = await _pop_job(project_key)
            if job is None:
                # No more jobs — wait for active tasks to complete
                if active_tasks:
                    logger.info(f"[{project_key}] Queue empty, waiting for {len(active_tasks)} active jobs")
                    await asyncio.gather(*active_tasks, return_exceptions=True)
                else:
                    logger.info(f"[{project_key}] Queue empty, worker exiting")
                break

            # Spawn job execution as background task (non-blocking)
            async def execute_and_complete(j: Job) -> None:
                try:
                    await _execute_job(j)
                except Exception as e:
                    logger.error(f"[{project_key}] Job {j.job_id} failed: {e}")
                finally:
                    await _complete_job(j)

            task = asyncio.create_task(execute_and_complete(job))
            active_tasks.add(task)
            await asyncio.sleep(0.5)  # Brief pause before next pop

    finally:
        _active_workers.pop(project_key, None)


async def _execute_job(job: Job) -> None:
    """
    Execute a single job:
    1. Create git worktree for isolated execution
    2. Run agent work via BackgroundTask + BossMessenger (in worktree)
    3. Wait for completion
    4. Merge branch and remove worktree
    """
    from agent import get_agent_response_sdk, BossMessenger, BackgroundTask

    working_dir = Path(job.working_dir)
    branch_name = _session_branch_name(job.session_id)

    logger.info(
        f"[{job.project_key}] Executing job {job.job_id} "
        f"(session={job.session_id}, branch={branch_name})"
    )

    # Step 1: Create worktree for isolated execution
    worktree_dir = _create_worktree(working_dir, branch_name)
    if not worktree_dir:
        logger.error(f"Failed to create worktree for {branch_name}, aborting job")
        return

    try:
        # Step 2: Create messenger with bridge callbacks
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

        # Step 3: Run agent work (in worktree directory)
        project_config = {
            "_key": job.project_key,
            "working_directory": str(worktree_dir),  # Agent runs in worktree
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

        # Wait for the background task to complete
        while task.is_running:
            await asyncio.sleep(2)

        # Step 4: Set reaction based on result
        if react_cb:
            emoji = "\U0001f44d" if not task.error else "\u274c"
            try:
                await react_cb(job.chat_id, job.message_id, emoji)
            except Exception as e:
                logger.warning(f"Failed to set reaction: {e}")

    finally:
        # Step 5: Merge (or push branch for review) and remove worktree
        project_cfg = get_project_config(job.project_key)
        auto_merge = project_cfg.get("auto_merge", True)
        _finish_worktree(working_dir, worktree_dir, branch_name, auto_merge, job.project_key)


def _session_branch_name(session_id: str) -> str:
    """Convert session_id to a git branch name."""
    safe = sanitize_branch_name(session_id)
    return f"session/{safe}"


def _create_worktree(working_dir: Path, branch_name: str) -> Path | None:
    """
    Create a git worktree for isolated parallel execution.

    Returns the worktree directory path on success, None on failure.
    """
    worktree_dir = working_dir / ".worktrees" / branch_name.replace("/", "-")

    try:
        # Check if branch already exists (resuming previous work)
        branch_check = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=working_dir,
            capture_output=True,
            timeout=5,
        )
        branch_exists = branch_check.returncode == 0

        # Check if worktree already exists
        if worktree_dir.exists():
            logger.info(f"Reusing existing worktree: {worktree_dir}")
            return worktree_dir

        # Create worktree directory parent
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)

        if branch_exists:
            # Worktree for existing branch
            subprocess.run(
                ["git", "worktree", "add", str(worktree_dir), branch_name],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            logger.info(f"Created worktree for existing branch {branch_name}: {worktree_dir}")
        else:
            # Create new branch from main in worktree
            subprocess.run(
                ["git", "worktree", "add", "-b", branch_name, str(worktree_dir), "main"],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            logger.info(f"Created worktree with new branch {branch_name}: {worktree_dir}")

        return worktree_dir

    except subprocess.CalledProcessError as e:
        logger.error(f"Worktree creation failed for {branch_name}: {e.stderr if hasattr(e, 'stderr') else e}")
        return None


def _finish_worktree(
    working_dir: Path,
    worktree_dir: Path,
    branch_name: str,
    auto_merge: bool,
    project_key: str,
) -> bool:
    """
    Finish work on a worktree: commit changes, merge/push, remove worktree.

    If auto_merge=True: merge to main, delete branch, push.
    If auto_merge=False: push branch to remote for PR/review.
    """
    try:
        # Commit any uncommitted work in the worktree
        if has_uncommitted_changes(worktree_dir):
            subprocess.run(
                ["git", "add", "-A"],
                cwd=worktree_dir,
                capture_output=True,
                timeout=10,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", f"Auto-commit session work: {branch_name}"],
                cwd=worktree_dir,
                capture_output=True,
                timeout=10,
                check=True,
            )

        if auto_merge:
            # === Auto-merge: merge from main worktree, then cleanup ===
            result = subprocess.run(
                ["git", "merge", "--no-ff", branch_name, "-m", f"Merge {branch_name}"],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.warning(
                    f"Merge conflict for {branch_name}, leaving worktree for manual resolution"
                )
                return False

            # Remove worktree and branch
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_dir)],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "branch", "-d", branch_name],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
            )

            subprocess.run(
                ["git", "push"],
                cwd=working_dir,
                capture_output=True,
                timeout=30,
            )

            logger.info(f"[{project_key}] Auto-merged and removed worktree: {branch_name}")

        else:
            # === Awaiting review: push branch, keep worktree for now ===
            subprocess.run(
                ["git", "push", "-u", "origin", branch_name],
                cwd=worktree_dir,
                capture_output=True,
                timeout=30,
            )

            logger.info(
                f"[{project_key}] Pushed branch {branch_name} for review (worktree kept)"
            )

        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Worktree finish failed for {branch_name}: {e}")
        return False


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


async def cleanup_orphaned_worktrees(working_dir: str) -> list[str]:
    """
    Clean up orphaned worktrees (left over from crashes).
    Returns list of cleaned worktree paths.
    """
    wd = Path(working_dir)
    cleaned = []

    if not wd.exists():
        return cleaned

    try:
        # Prune stale worktree references
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=wd,
            capture_output=True,
            timeout=10,
        )

        # List existing worktrees
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            return cleaned

        # Parse worktree list (format: "worktree <path>\n...")
        worktrees = []
        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                wt_path = line.split(" ", 1)[1]
                if ".worktrees/" in wt_path:
                    worktrees.append(Path(wt_path))

        # Remove orphaned .worktrees directories
        worktrees_dir = wd / ".worktrees"
        if worktrees_dir.exists():
            for item in worktrees_dir.iterdir():
                if item.is_dir() and item not in worktrees:
                    try:
                        # Force remove if git worktree remove fails
                        remove_result = subprocess.run(
                            ["git", "worktree", "remove", "--force", str(item)],
                            cwd=wd,
                            capture_output=True,
                            timeout=10,
                        )
                        if remove_result.returncode == 0:
                            cleaned.append(str(item))
                            logger.info(f"Cleaned orphaned worktree: {item}")
                    except Exception as e:
                        logger.warning(f"Failed to remove orphaned worktree {item}: {e}")

    except Exception as e:
        logger.error(f"Worktree cleanup error: {e}")

    return cleaned
