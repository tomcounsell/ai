"""Revival detection, cooldown tracking, and stale branch cleanup for AgentSession."""

import json
import logging
import subprocess
import time
from pathlib import Path

from agent.branch_manager import get_branch_state, get_plan_context, sanitize_branch_name
from models.agent_session import AgentSession
from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

logger = logging.getLogger(__name__)


REVIVAL_COOLDOWN_SECONDS = 86400
_COOLDOWN_FILE = Path(__file__).parent.parent / "data" / "revival_cooldowns.json"


def _session_branch_name(session_id: str) -> str:
    """Convert session_id to a git branch name."""
    safe = sanitize_branch_name(session_id)
    return f"session/{safe}"


def _load_cooldowns() -> dict[str, float]:
    """Load revival cooldowns from disk."""
    try:
        if _COOLDOWN_FILE.exists():
            return json.loads(_COOLDOWN_FILE.read_text())
    except Exception as e:
        logger.warning(f"Failed to load revival cooldowns from {_COOLDOWN_FILE}: {e}")
    return {}


def _save_cooldowns(cooldowns: dict[str, float]) -> None:
    """Persist revival cooldowns to disk."""
    try:
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

    # Terminal-session filter: check if any terminal session exists for the same
    # chat with a matching branch. If so, the work is done — skip revival for
    # that branch to avoid respawning completed/failed work.
    if branches:
        try:
            terminal_branches = set()
            for t_status in _TERMINAL_STATUSES:
                for t_session in AgentSession.query.filter(
                    project_key=project_key, status=t_status
                ):
                    if str(t_session.chat_id) == chat_id_str:
                        t_branch = _session_branch_name(t_session.session_id)
                        terminal_branches.add(t_branch)
            # Remove branches that have a terminal sibling
            filtered = [b for b in branches if b not in terminal_branches]
            if len(filtered) < len(branches):
                removed = set(branches) - set(filtered)
                logger.info(
                    f"[{project_key}] Revival: filtered out {len(removed)} branch(es) "
                    f"with terminal sessions: {removed}"
                )
            branches = filtered
        except Exception as e:
            logger.warning(f"[{project_key}] Terminal-session revival filter failed: {e}")

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


def maybe_send_revival_prompt(project_key: str, working_dir: str, chat_id: str) -> dict | None:
    """
    Check whether a revival prompt should be sent for this chat.

    Combines check_revival (git state inspection) with cooldown recording.
    Returns revival_info dict if a revival is warranted, None otherwise.
    The caller is responsible for actually sending the Telegram message.
    """
    if not project_key or not working_dir:
        return None
    revival_info = check_revival(project_key, working_dir, chat_id)
    if revival_info:
        record_revival_cooldown(chat_id)
    return revival_info


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
    # Deferred import to avoid circular dependency (residual imports from this module)
    from agent.agent_session_queue import enqueue_agent_session

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


async def cleanup_stale_branches_all_projects() -> list[str]:
    """Clean up stale session branches across all registered projects.

    Called by the reflection scheduler as the 'stale-branch-cleanup' reflection.
    Loads project configs directly from projects.json instead of relying on
    a module-level registry.
    Returns list of all cleaned branch names.
    """
    all_cleaned = []
    try:
        from bridge.routing import load_config as _load_projects_config

        all_projects = _load_projects_config().get("projects", {})
    except Exception as e:
        logger.error("[reflection] Failed to load projects.json for branch cleanup: %s", e)
        return all_cleaned

    if not all_projects:
        logger.debug("[reflection] No projects in config, skipping branch cleanup")
        return all_cleaned

    for project_key, config in all_projects.items():
        working_dir = config.get("working_directory", "")
        if not working_dir:
            continue
        try:
            cleaned = await cleanup_stale_branches(working_dir)
            all_cleaned.extend(cleaned)
        except Exception as e:
            logger.error("[reflection] Branch cleanup failed for %s: %s", project_key, e)
    return all_cleaned
