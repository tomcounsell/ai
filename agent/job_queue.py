"""
Job Queue - FILO stack with per-project workers and session branching.

Serializes agent work per project working directory so git operations
never conflict. Each session gets its own feature branch.

Architecture:
- ProjectJobQueue: FILO stack persisted to JSON, one per project
- Worker loop: one asyncio.Task per project, processes jobs sequentially
- Revival detection: lightweight git state check, no SDK agent call
"""

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

from agent.branch_manager import (
    get_branch_state,
    return_to_main,
    has_uncommitted_changes,
    sanitize_branch_name,
    get_plan_context,
)

logger = logging.getLogger(__name__)

QUEUE_DIR = Path(__file__).parent.parent / "data" / "job_queue"


@dataclass
class Job:
    """A queued unit of work."""

    session_id: str
    project_key: str
    working_dir: str
    message_text: str
    sender_name: str
    chat_id: str
    message_id: int
    chat_title: str | None = None
    priority: str = "high"  # "high" (top of stack) or "low" (bottom)
    revival_context: str | None = None
    created_at: str = ""
    job_id: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.job_id:
            self.job_id = f"{self.project_key}_{int(time.time() * 1000)}"


class ProjectJobQueue:
    """
    FILO stack for a single project. Persisted to disk as JSON.

    HIGH priority jobs go to top (index 0).
    LOW priority jobs go to bottom (end).
    Worker always pops from index 0.
    """

    def __init__(self, project_key: str):
        self.project_key = project_key
        self._file = QUEUE_DIR / f"{project_key}.json"
        self._lock = asyncio.Lock()
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if not self._file.exists():
            return []
        try:
            return json.loads(self._file.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning(f"Corrupt queue file for {self.project_key}, resetting")
            return []

    def _save(self, jobs: list[dict]) -> None:
        self._file.write_text(json.dumps(jobs, indent=2, default=str))

    async def push(self, job: Job) -> int:
        """Push job onto stack. Returns queue depth after push."""
        async with self._lock:
            jobs = self._load()
            job_dict = asdict(job)
            if job.priority == "low":
                jobs.append(job_dict)
            else:
                jobs.insert(0, job_dict)
            self._save(jobs)
            return len(jobs)

    async def pop(self) -> Job | None:
        """Pop highest priority job (index 0). Returns None if empty."""
        async with self._lock:
            jobs = self._load()
            if not jobs:
                return None
            job_dict = jobs.pop(0)
            self._save(jobs)
            return Job(**job_dict)

    async def depth(self) -> int:
        """Number of jobs in queue."""
        async with self._lock:
            return len(self._load())

    async def remove_by_session(self, session_id: str) -> bool:
        """Remove all jobs for a session. Returns True if any removed."""
        async with self._lock:
            jobs = self._load()
            filtered = [j for j in jobs if j["session_id"] != session_id]
            if len(filtered) < len(jobs):
                self._save(filtered)
                return True
            return False


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


async def enqueue_job(job: Job) -> int:
    """
    Add a job to the appropriate project queue and ensure worker is running.
    Returns queue depth after push.
    """
    queue = ProjectJobQueue(job.project_key)
    depth = await queue.push(job)
    _ensure_worker(job.project_key)
    logger.info(
        f"[{job.project_key}] Enqueued job {job.job_id} "
        f"(priority={job.priority}, depth={depth})"
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
    queue = ProjectJobQueue(project_key)

    try:
        while True:
            job = await queue.pop()
            if job is None:
                logger.info(f"[{project_key}] Queue empty, worker exiting")
                break

            try:
                await _execute_job(job)
            except Exception as e:
                logger.error(f"[{project_key}] Job {job.job_id} failed: {e}")
            finally:
                await asyncio.sleep(1)
    finally:
        _active_workers.pop(project_key, None)


async def _execute_job(job: Job) -> None:
    """
    Execute a single job:
    1. Checkout/create session branch
    2. Run agent work via BackgroundTask + BossMessenger
    3. Wait for completion (serializes work)
    4. Merge branch to main and cleanup
    """
    from agent import get_agent_response_sdk, BossMessenger, BackgroundTask

    working_dir = Path(job.working_dir)
    branch_name = _session_branch_name(job.session_id)

    logger.info(
        f"[{job.project_key}] Executing job {job.job_id} "
        f"(session={job.session_id}, branch={branch_name})"
    )

    # Step 1: Branch management
    if not _checkout_session_branch(working_dir, branch_name):
        logger.error(f"Failed to checkout branch {branch_name}, running on current branch")

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

        # Step 3: Run agent work
        project_config = {
            "_key": job.project_key,
            "working_directory": job.working_dir,
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

        # Wait for the background task to complete (this serializes work)
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
        # Step 5: Merge (or push branch for review) and return to main
        project_cfg = get_project_config(job.project_key)
        auto_merge = project_cfg.get("auto_merge", True)
        _finish_branch(working_dir, branch_name, auto_merge, job.project_key)


def _session_branch_name(session_id: str) -> str:
    """Convert session_id to a git branch name."""
    safe = sanitize_branch_name(session_id)
    return f"session/{safe}"


def _checkout_session_branch(working_dir: Path, branch_name: str) -> bool:
    """Checkout existing session branch or create from main."""
    try:
        result = subprocess.run(
            ["git", "checkout", branch_name],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            logger.info(f"Checked out existing branch: {branch_name}")
            return True

        # Branch doesn't exist — create from main
        subprocess.run(
            ["git", "checkout", "main"],
            cwd=working_dir,
            capture_output=True,
            timeout=10,
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=working_dir,
            capture_output=True,
            timeout=10,
            check=True,
        )
        logger.info(f"Created new branch: {branch_name}")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Branch checkout failed: {e.stderr}")
        return False


def _finish_branch(
    working_dir: Path,
    branch_name: str,
    auto_merge: bool,
    project_key: str,
) -> bool:
    """
    Finish work on a session branch.

    If auto_merge=True: merge to main, delete branch, push.
    If auto_merge=False: push branch to remote for PR/review, return to main.
    """
    try:
        # Commit any uncommitted work on the session branch
        if has_uncommitted_changes(working_dir):
            subprocess.run(
                ["git", "add", "-A"],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", f"Auto-commit session work: {branch_name}"],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
                check=True,
            )

        if auto_merge:
            # === Auto-merge: merge to main, delete branch, push ===
            subprocess.run(
                ["git", "checkout", "main"],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
                check=True,
            )

            result = subprocess.run(
                ["git", "merge", "--no-ff", branch_name, "-m", f"Merge {branch_name}"],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.warning(
                    f"Merge conflict for {branch_name}, leaving branch for manual resolution"
                )
                return False

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

            logger.info(f"[{project_key}] Auto-merged and cleaned up: {branch_name}")

        else:
            # === Awaiting review: push branch to remote, return to main ===
            subprocess.run(
                ["git", "push", "-u", "origin", branch_name],
                cwd=working_dir,
                capture_output=True,
                timeout=30,
            )

            subprocess.run(
                ["git", "checkout", "main"],
                cwd=working_dir,
                capture_output=True,
                timeout=10,
                check=True,
            )

            logger.info(
                f"[{project_key}] Pushed branch {branch_name} for review (auto_merge=false)"
            )

        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Branch finish failed for {branch_name}: {e}")
        return_to_main(working_dir)
        return False


# === Revival Detection ===

# Track revival notifications: {(chat_id, msg_id): revival_info_dict}
_revival_messages: dict[tuple[str, int], dict] = {}

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


def record_revival_notification(
    chat_id: str,
    msg_id: int,
    session_id: str,
    project_key: str,
    branch: str,
    working_dir: str,
) -> None:
    """Record that we sent a revival notification for reaction/reply tracking."""
    _revival_cooldowns[chat_id] = time.time()
    _revival_messages[(chat_id, msg_id)] = {
        "session_id": session_id,
        "branch": branch,
        "project_key": project_key,
        "working_dir": working_dir,
    }


def get_revival_info(chat_id: str, msg_id: int) -> dict | None:
    """Check if a message ID is a revival notification we sent."""
    return _revival_messages.get((chat_id, msg_id))


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
        revival_text += f"\n\nAdditional context from user: {additional_context}"

    job = Job(
        session_id=revival_info["session_id"],
        project_key=revival_info["project_key"],
        working_dir=revival_info["working_dir"],
        message_text=revival_text,
        sender_name="System (Revival)",
        chat_id=chat_id,
        message_id=message_id,
        priority="low",
        revival_context=additional_context,
    )
    return await enqueue_job(job)


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
