"""Session selection, pop locking, startup steering drain, and dependency readiness checks."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import agent.steering as _steering
from models.agent_session import AgentSession
from models.session_lifecycle import TERMINAL_STATUSES as _TERMINAL_STATUSES

logger = logging.getLogger(__name__)


# 4-tier priority ranking: lower number = higher priority (copied from residual for local use)
_PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}

_POP_LOCK_TTL_SECONDS = 5  # Long enough to cover transition_status write; short enough to self-heal


def dependency_status(session: AgentSession) -> dict[str, str]:
    """Return the status of each dependency for a session.

    Dependencies were removed in issue #609. This returns an empty dict
    for backward compatibility.
    """
    return {}


def _acquire_pop_lock(worker_key: str) -> bool:
    """Acquire a Redis SETNX lock for the pop-and-transition block.

    Returns True if the lock was acquired, False if already held.
    Uses Popoto's underlying Redis client to avoid new dependencies.
    TTL=5s ensures self-healing if the process crashes while holding the lock.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        lock_key = f"worker:pop_lock:{worker_key}"
        # SETNX returns True if key was set (lock acquired), False if already exists
        acquired = POPOTO_REDIS_DB.set(lock_key, "1", nx=True, ex=_POP_LOCK_TTL_SECONDS)
        return bool(acquired)
    except Exception as e:
        logger.warning(f"[worker:{worker_key}] Pop lock acquisition failed (non-fatal): {e}")
        # Fail open: allow the pop to proceed without the lock rather than blocking workers
        return True


def _release_pop_lock(worker_key: str) -> None:
    """Release the Redis pop lock for the given worker_key."""
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        POPOTO_REDIS_DB.delete(f"worker:pop_lock:{worker_key}")
    except Exception as e:
        logger.warning(f"[worker:{worker_key}] Pop lock release failed (non-fatal): {e}")


async def _maybe_inject_resume_hydration(chosen, worker_key: str) -> None:
    """Prepend resume context to a PM session's message_text if this is a resume.

    Detects resume by checking for 2+ *_resume.json files in the session's log
    directory (1 file = first start only). If resume detected and the session is
    a PM session with a valid working_dir, prepends a <resumed-session-context>
    block containing recent branch commits so the agent can skip completed work.

    Silent on any failure -- session start must never crash due to hydration.
    """
    try:
        # Gate: only PM sessions benefit from resume hydration
        if getattr(chosen, "session_type", None) != "pm":
            return

        # Gate: working_dir must be set to avoid wrong-directory git summary
        if not getattr(chosen, "working_dir", None):
            logger.debug(
                f"[worker:{worker_key}] Skipping resume hydration for session "
                f"{chosen.id}: no working_dir set"
            )
            return

        # Check for prior resume snapshots
        from agent.session_logs import (
            SESSION_LOGS_DIR,  # noqa: N811
            _get_git_summary,
        )

        session_log_dir = SESSION_LOGS_DIR / chosen.session_id
        if not session_log_dir.exists():
            return

        resume_files = list(session_log_dir.glob("*_resume.json"))
        if len(resume_files) < 2:
            return

        # This is a genuine resume -- inject context
        git_summary = _get_git_summary(working_dir=chosen.working_dir, log_depth=10)

        hydration_block = (
            "<resumed-session-context>\n"
            "This session is resuming. The following commits already exist on the branch:\n"
            f"{git_summary}\n"
            "If any of these commits satisfy a stage in your current plan, skip that stage\n"
            "and proceed to the next uncompleted stage. Do not re-dispatch work that is\n"
            "already committed.\n"
            "</resumed-session-context>"
        )

        original = chosen.message_text or ""
        chosen.message_text = f"{hydration_block}\n\n{original}" if original else hydration_block
        await chosen.async_save(update_fields=["initial_telegram_message", "updated_at"])
        logger.info(
            f"[worker:{worker_key}] Injected resume hydration into session {chosen.id} "
            f"({len(resume_files)} prior resume files found)"
        )

    except Exception as e:
        logger.warning(
            f"[worker:{worker_key}] Failed to inject resume hydration for session "
            f"{chosen.id} (non-fatal): {e}"
        )


async def _drain_startup_steering(session: AgentSession, *, worker_key: str = "") -> None:
    """Drain any steering messages queued during the pending window into message_text.

    Consolidates the duplicate drain logic that was previously inlined in both
    _pop_agent_session and _pop_agent_session_with_fallback. Always saves when
    steering messages are present (no save_after parameter).

    Function body: pop → prepend → async_save when extra_texts is non-empty.
    """
    try:
        steering_msgs = _steering.pop_all_steering_messages(session.session_id)
        if steering_msgs:
            extra_texts = [m["text"] for m in steering_msgs if m.get("text", "").strip()]
            if extra_texts:
                prepend = "\n\n".join(extra_texts)
                original = session.message_text or ""
                session.message_text = f"{original}\n\n{prepend}" if original else prepend
                await session.async_save(update_fields=["initial_telegram_message", "updated_at"])
                label = f"[worker:{worker_key}]" if worker_key else "[worker]"
                logger.info(
                    f"{label} Drained {len(extra_texts)} steering message(s) "
                    f"into session {session.id} message_text"
                )
    except Exception as e:
        # Drain failure must not crash session start
        label = f"[worker:{worker_key}]" if worker_key else "[worker]"
        logger.warning(
            f"{label} Failed to drain steering messages for session "
            f"{session.id} (non-fatal): {e}"
        )


async def _pop_agent_session(
    worker_key: str, is_project_keyed: bool = False
) -> AgentSession | None:
    """Pop the highest priority pending session for a worker.

    Queue is keyed by worker_key (either project_key or chat_id depending on
    session type).  Project-keyed workers filter by project_key and only pop
    pm/dev-without-slug sessions.  Chat-keyed workers filter by chat_id as before.

    Order: urgent > high > normal > low, then within same priority FIFO (oldest first).
    Sessions with scheduled_at in the future are skipped (deferred execution).

    Sustainability guards (checked before acquiring pop lock):
    - If queue_paused flag is set (Anthropic circuit OPEN/HALF_OPEN), return None.
    - If worker:hibernating flag is set (mid-execution API failure), return None.
    - Throttle level affects which priority tiers are eligible.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _project_key = os.environ.get("VALOR_PROJECT_KEY", "default")
        _pause_key = f"{_project_key}:sustainability:queue_paused"
        _hibernating_key = f"{_project_key}:worker:hibernating"
        _throttle_key = f"{_project_key}:sustainability:throttle_level"

        if _R.get(_pause_key):
            logger.debug("[worker:%s] Queue paused (API circuit open) — skipping pop", worker_key)
            return None

        if _R.get(_hibernating_key):
            logger.debug("[worker:%s] Hibernating (worker:hibernating) — skipping pop", worker_key)
            return None

        _throttle_raw = _R.get(_throttle_key)
        _throttle_raw_decoded = (
            _throttle_raw.decode() if isinstance(_throttle_raw, bytes) else _throttle_raw
        )
        _throttle = _throttle_raw_decoded or "none"
    except Exception as _guard_err:
        # Fail open: if Redis is unavailable, allow the pop to proceed normally
        logger.warning(
            "[worker:%s] Sustainability guard failed (proceeding): %s", worker_key, _guard_err
        )
        _throttle = "none"

    if not _acquire_pop_lock(worker_key):
        logger.debug(f"[worker:{worker_key}] Pop lock held by another worker, skipping pop")
        return None

    try:
        if is_project_keyed:
            pending = await AgentSession.query.async_filter(
                project_key=worker_key, status="pending"
            )
            # Only pop sessions that belong to project-keyed workers (pm, dev-without-slug)
            pending = [s for s in pending if s.worker_key == worker_key]
        else:
            pending = await AgentSession.query.async_filter(chat_id=worker_key, status="pending")
        if not pending:
            return None

        # Filter out sessions with scheduled_at in the future
        now = datetime.now(tz=UTC)

        def _is_eligible(j):
            sa = j.scheduled_at
            if not sa:
                return True
            if isinstance(sa, datetime):
                if sa.tzinfo is None:
                    sa = sa.replace(tzinfo=UTC)
                return sa <= now
            if isinstance(sa, int | float):
                return sa <= now.timestamp()
            return True

        eligible = [j for j in pending if _is_eligible(j)]
        if not eligible:
            return None

        # Sort: highest priority first (4-tier), then oldest first (FIFO)
        def _ensure_tz(dt):
            if dt is None:
                return datetime.min.replace(tzinfo=UTC)
            if isinstance(dt, int | float):
                return datetime.fromtimestamp(dt, tz=UTC)
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt

        # Build a lookup of parent statuses for child-priority boost (issue #1004).
        # Sessions whose parent is in waiting_for_children sort before peers at
        # the same priority tier, breaking the deadlock where the parent holds a
        # slot while the child can never start.
        _parent_ids = {
            getattr(j, "parent_agent_session_id", None)
            for j in eligible
            if getattr(j, "parent_agent_session_id", None)
        }
        _parent_waiting: set[str] = set()
        if _parent_ids:
            try:
                from models.agent_session import AgentSession as _ParentAS

                for pid in _parent_ids:
                    _matches = list(_ParentAS.query.filter(id=pid))
                    if _matches and getattr(_matches[0], "status", None) == "waiting_for_children":
                        _parent_waiting.add(pid)
            except Exception:
                pass  # If lookup fails, no boost — safe fallback

        def sort_key(j):
            prio = _PRIORITY_RANK.get(j.priority, 2)  # default to normal
            # Boost children of waiting parents: 0 sorts before 1
            _pid = getattr(j, "parent_agent_session_id", None)
            child_boost = 0 if _pid and _pid in _parent_waiting else 1
            return (prio, child_boost, _ensure_tz(j.created_at))

        eligible.sort(key=sort_key)

        # Guard: index can drift from actual state (e.g. session completed but still
        # in the pending index). Iterate past stale zombie sessions; trigger a one-time
        # rebuild to repair the index. Continue to the next candidate so the zombie
        # doesn't block legitimate pending sessions behind it.
        from models.session_lifecycle import transition_status

        rebuilt = False
        chosen = None
        for candidate in eligible:
            # Sustainability throttle: skip candidates below the allowed priority tier
            if _throttle == "suspended" and candidate.priority in ("normal", "low"):
                logger.debug(
                    "[worker:%s] Throttle=suspended — skipping session %s (priority=%s)",
                    worker_key,
                    candidate.session_id,
                    candidate.priority,
                )
                continue
            if _throttle == "moderate" and candidate.priority == "low":
                logger.debug(
                    "[worker:%s] Throttle=moderate — skipping session %s (priority=low)",
                    worker_key,
                    candidate.session_id,
                )
                continue

            if candidate.status in _TERMINAL_STATUSES:
                logger.warning(
                    f"[worker:{worker_key}] Skipping session {candidate.id} "
                    f"(session {candidate.session_id}): index says pending but actual "
                    f"status={candidate.status!r}. Stale index entry."
                )
                if not rebuilt:
                    rebuilt = True
                    try:
                        # Use repair_indexes() instead of rebuild_indexes() (#1006):
                        # repair_indexes() clears IndexedField ($IndexF:) sets before
                        # rebuilding, ensuring stale entries are actually removed.
                        AgentSession.repair_indexes()
                        logger.info(f"[worker:{worker_key}] Repaired indexes to fix stale entry")
                    except Exception as rebuild_err:
                        logger.warning(f"[worker:{worker_key}] Index repair failed: {rebuild_err}")
                continue
            chosen = candidate
            break

        if chosen is None:
            return None

        # Direct field mutation -- status is an IndexedField, not a KeyField,
        # so save() correctly updates the secondary index.
        logger.info(
            f"[worker:{worker_key}] Transitioning session {chosen.id} "
            f"(session {chosen.session_id}) pending->running"
        )

        chosen.started_at = datetime.now(tz=UTC)
        transition_status(chosen, "running", reason="worker picked up session")
    finally:
        _release_pop_lock(worker_key)

    # Inject resume hydration context BEFORE steering messages so the agent
    # orients itself on prior work before processing new instructions (#874).
    await _maybe_inject_resume_hydration(chosen, worker_key)

    # Drain any steering messages queued during the pending window (#619).
    await _drain_startup_steering(chosen, worker_key=worker_key)

    return chosen


async def _pop_agent_session_with_fallback(
    worker_key: str, is_project_keyed: bool = False
) -> AgentSession | None:
    """Pop a pending session using async_filter first, then sync fallback.

    This is a separate function from _pop_agent_session() to avoid changing the hot path.
    Called only from the drain timeout path and exit-time diagnostic in _worker_loop.

    The sync fallback bypasses to_thread() scheduling, which eliminates the
    thread-pool race between async_create index writes and async_filter reads
    that is the root cause of the pending session drain bug.
    """
    # Try the normal async path first
    session = await _pop_agent_session(worker_key, is_project_keyed)
    if session is not None:
        return session

    # Sync fallback: bypass to_thread() to avoid the index visibility race.
    if not _acquire_pop_lock(worker_key):
        logger.debug(f"[worker:{worker_key}] Sync fallback: pop lock held, skipping")
        return None

    try:
        if is_project_keyed:
            pending = AgentSession.query.filter(project_key=worker_key, status="pending")
            pending = [s for s in pending if s.worker_key == worker_key]
        else:
            pending = AgentSession.query.filter(chat_id=worker_key, status="pending")
        if not pending:
            return None

        # Apply the same filtering as _pop_agent_session: scheduled_at
        now = datetime.now(tz=UTC)

        def _is_eligible(j):
            sa = j.scheduled_at
            if not sa:
                return True
            if isinstance(sa, datetime):
                if sa.tzinfo is None:
                    sa = sa.replace(tzinfo=UTC)
                return sa <= now
            if isinstance(sa, int | float):
                return sa <= now.timestamp()
            return True

        eligible = [j for j in pending if _is_eligible(j)]
        if not eligible:
            return None

        # Sort: highest priority first, then oldest first (FIFO)
        def _ensure_tz(dt):
            if dt is None:
                return datetime.min.replace(tzinfo=UTC)
            if isinstance(dt, int | float):
                return datetime.fromtimestamp(dt, tz=UTC)
            if isinstance(dt, datetime) and dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt

        def sort_key(j):
            prio = _PRIORITY_RANK.get(j.priority, 2)
            return (prio, _ensure_tz(j.created_at))

        eligible.sort(key=sort_key)

        # Guard: same stale-index protection as _pop_agent_session — iterate past
        # zombie sessions rather than blocking on the first one.
        from models.session_lifecycle import transition_status

        rebuilt = False
        chosen = None
        for candidate in eligible:
            if candidate.status in _TERMINAL_STATUSES:
                logger.warning(
                    f"[worker:{worker_key}] Sync fallback: skipping session {candidate.id} "
                    f"(session {candidate.session_id}): index says pending but actual "
                    f"status={candidate.status!r}. Stale index entry."
                )
                if not rebuilt:
                    rebuilt = True
                    try:
                        # Use repair_indexes() instead of rebuild_indexes() (#1006):
                        # repair_indexes() clears IndexedField ($IndexF:) sets before
                        # rebuilding, ensuring stale entries are actually removed.
                        AgentSession.repair_indexes()
                        logger.info(f"[worker:{worker_key}] Repaired indexes to fix stale entry")
                    except Exception as rebuild_err:
                        logger.warning(f"[worker:{worker_key}] Index repair failed: {rebuild_err}")
                continue
            chosen = candidate
            break

        if chosen is None:
            return None

        # Direct field mutation -- status is an IndexedField, not a KeyField.
        logger.info(
            f"[worker:{worker_key}] Sync fallback: transitioning session {chosen.id} "
            f"(session {chosen.session_id}) pending->running"
        )

        chosen.started_at = datetime.now(tz=UTC)
        transition_status(chosen, "running", reason="worker picked up session (sync fallback)")

        # Inject resume hydration context BEFORE steering messages (#874)
        await _maybe_inject_resume_hydration(chosen, worker_key)

        # Drain steering messages using shared helper (#619)
        await _drain_startup_steering(chosen, worker_key=worker_key)

        return chosen
    except Exception:
        logger.exception(f"[worker:{worker_key}] Sync fallback query failed, falling through")
        return None
    finally:
        _release_pop_lock(worker_key)
