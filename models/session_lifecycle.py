"""Consolidated session lifecycle management -- **status authority with CAS**.

All session status mutations should go through this module:
- finalize_session() for terminal transitions (completed, failed, killed, abandoned, cancelled)
- transition_status() for non-terminal transitions (pending, running, active, dormant,
  waiting_for_children, superseded, paused_circuit, paused)
- update_session() for atomic re-read + field application + status transition
- get_authoritative_session() for centralized tie-break re-read

This module owns the full mutation path: callers hand in a session_id (or instance),
the module re-reads from Redis, applies changes, and CAS-saves. A StatusConflictError
is raised when the on-disk status has changed since the caller's in-memory snapshot,
preventing silent stomps from concurrent writers.

Design constraints:
- Must be importable from .claude/hooks/stop.py subprocess context (limited sys.path)
- Uses lazy imports for heavy dependencies (tools.session_tags, agent.agent_session_queue)
- All side effects are optional and fail-safe (catch and log, never block status save)
- CAS uses Python-level compare-and-set (re-read + status compare), not Redis WATCH/MULTI/EXEC
"""

import logging
import time

logger = logging.getLogger(__name__)


class StatusConflictError(Exception):
    """Raised when CAS detects a status conflict during lifecycle transition.

    The on-disk session status differs from what the caller expected,
    indicating a concurrent writer has already changed the status.

    Attributes:
        session_id: The session_id of the conflicting session.
        expected_status: The status the caller expected to find on disk.
        actual_status: The actual status found on disk after re-read.
        reason: Human-readable context for the conflict.
    """

    def __init__(
        self,
        session_id: str,
        expected_status: str,
        actual_status: str,
        reason: str = "",
    ):
        self.session_id = session_id
        self.expected_status = expected_status
        self.actual_status = actual_status
        self.reason = reason
        detail = f" ({reason})" if reason else ""
        super().__init__(
            f"Status conflict for session {session_id}: "
            f"expected {expected_status!r} on disk, "
            f"found {actual_status!r}{detail}"
        )


# Terminal statuses -- sessions in these states are "done"
TERMINAL_STATUSES = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})

# Non-terminal statuses -- sessions in these states are still active or paused
NON_TERMINAL_STATUSES = frozenset(
    {
        "pending",
        "running",
        "active",
        "dormant",
        "waiting_for_children",
        "superseded",
        "paused_circuit",  # paused by api-health-gate when Anthropic circuit is OPEN
        "paused",  # paused mid-execution due to auth/API failure; resumed by session-resume-drip
    }
)

# Recovery ownership -- maps every non-terminal status to the process responsible
# for detecting stuck sessions in that state and recovering them.
# This is an informational constant; it is not used for runtime routing.
# See docs/features/session-recovery-mechanisms.md for the full rationale.
RECOVERY_OWNERSHIP: dict[str, str] = {
    "pending": "worker",  # _agent_session_health_check
    "running": "worker",  # _agent_session_health_check + startup recovery
    "active": "bridge-watchdog",  # session_watchdog check_all/stalled
    "dormant": "bridge-watchdog",  # session_watchdog activity check
    "waiting_for_children": "worker",  # _agent_session_hierarchy_health_check
    "superseded": "none",  # transitional; finalized immediately
    "paused_circuit": "bridge-watchdog",  # sustainability.py circuit drip
    "paused": "bridge-watchdog",  # hibernation.py session-resume-drip
}

# All known statuses
ALL_STATUSES = TERMINAL_STATUSES | NON_TERMINAL_STATUSES


def get_authoritative_session(session_id: str, project_key: str | None = None):
    """Re-read a session from Redis with tie-break logic.

    Centralizes the pattern of querying AgentSession by session_id and
    choosing the best record when duplicates exist. Tie-break logic:
    1. Prefer records with status="running" (active execution)
    2. Fall back to most recent by created_at

    This replaces the blind ``list(...)[0]`` pattern used in 15+ call sites.

    Args:
        session_id: The session_id to look up.
        project_key: Optional project_key filter for scoped queries.

    Returns:
        The authoritative AgentSession instance, or None if not found.
    """
    if not session_id:
        logger.warning("[lifecycle-cas] get_authoritative_session called with empty session_id")
        return None

    from models.agent_session import AgentSession

    try:
        filters = {"session_id": session_id}
        if project_key:
            filters["project_key"] = project_key
        sessions = list(AgentSession.query.filter(**filters))
    except Exception as e:
        logger.warning(
            "[lifecycle-cas] Redis query failed for session_id=%s: %s",
            session_id,
            e,
        )
        return None

    if not sessions:
        logger.warning(
            "[lifecycle-cas] No session found for session_id=%s",
            session_id,
        )
        return None

    if len(sessions) == 1:
        return sessions[0]

    # Tie-break: prefer running, then most recent by created_at
    running = [s for s in sessions if getattr(s, "status", None) == "running"]
    if running:
        # Among running records, pick most recent
        running.sort(key=lambda s: getattr(s, "created_at", 0) or 0, reverse=True)
        return running[0]

    # No running record -- pick most recent by created_at
    sessions.sort(key=lambda s: getattr(s, "created_at", 0) or 0, reverse=True)
    return sessions[0]


def update_session(
    session_id: str,
    new_status: str | None = None,
    fields: dict | None = None,
    expected_status: str | None = None,
    reason: str = "",
) -> None:
    """Atomic re-read + field application + status transition.

    This is the preferred API for callers that need to set companion fields
    (e.g., priority, started_at) alongside a status transition. The module
    handles re-read, CAS check, field application, and save in one call.

    Args:
        session_id: The session_id to update.
        new_status: Optional new status to transition to. If terminal,
            delegates to finalize_session(). If non-terminal, delegates
            to transition_status().
        fields: Optional dict of field names to values to apply to the
            session object before saving.
        expected_status: If provided, the on-disk status must match this
            value or StatusConflictError is raised.
        reason: Human-readable reason for the transition.

    Raises:
        ValueError: If session_id is empty, or both new_status and fields are None.
        StatusConflictError: If expected_status is provided and doesn't match on-disk.
    """
    if not session_id:
        raise ValueError("session_id must not be empty")
    if new_status is None and not fields:
        raise ValueError("At least one of new_status or fields must be provided")

    session = get_authoritative_session(session_id)
    if session is None:
        raise ValueError(f"No session found for session_id={session_id!r}")

    # CAS check: if caller specified expected_status, verify on-disk matches
    actual_status = getattr(session, "status", None)
    if expected_status is not None and actual_status != expected_status:
        raise StatusConflictError(
            session_id=session_id,
            expected_status=expected_status,
            actual_status=actual_status or "unknown",
            reason=reason,
        )

    # Apply companion fields
    if fields:
        for field_name, value in fields.items():
            setattr(session, field_name, value)

    # Delegate to the appropriate transition function
    if new_status is not None:
        if new_status in TERMINAL_STATUSES:
            finalize_session(session, new_status, reason=reason)
        else:
            transition_status(session, new_status, reason=reason)
    else:
        # Field-only update (no status change)
        session.save()


def finalize_session(
    session,
    status: str,
    reason: str = "",
    *,
    skip_auto_tag: bool = False,
    skip_checkpoint: bool = False,
    skip_parent: bool = False,
) -> None:
    """Finalize a session with a terminal status.

    Executes all completion side effects in order:
    1. CAS re-read + status comparison (conflict detection)
    2. Lifecycle transition log
    3. Auto-tag session (unless skip_auto_tag)
    4. Checkpoint branch state (unless skip_checkpoint)
    5. Finalize parent session (unless skip_parent or no parent)
    6. Set status + completed_at + save

    Idempotent: if the session is already in the target terminal state,
    logs and returns without re-executing side effects.

    CAS behavior: Re-reads the session from Redis and compares the on-disk
    status against the caller's in-memory status. If they differ, raises
    StatusConflictError. The caller's object is used for the save (not the
    re-read), preserving any companion fields set before this call.

    Lazy-load safety: Before saving, this function backfills
    session._saved_field_values["status"] with the current status so that
    Popoto's IndexedFieldMixin.on_save() guard can call srem() to remove the
    old index entry.

    Args:
        session: AgentSession instance to finalize.
        status: Terminal status to set (completed, failed, killed, abandoned, cancelled).
        reason: Human-readable reason for the transition.
        skip_auto_tag: Skip auto-tagging (e.g., hooks subprocess context).
        skip_checkpoint: Skip branch checkpointing (e.g., hooks subprocess context).
        skip_parent: Skip parent finalization.

    Raises:
        ValueError: If session is None or status is not terminal.
        StatusConflictError: If CAS detects a concurrent status change.
    """
    if session is None:
        raise ValueError("session must not be None")

    if status not in TERMINAL_STATUSES:
        raise ValueError(
            f"finalize_session() requires a terminal status "
            f"({', '.join(sorted(TERMINAL_STATUSES))}), got {status!r}. "
            f"Use transition_status() for non-terminal transitions."
        )

    # Idempotency: if already in this terminal state, skip side effects
    current_status = getattr(session, "status", None)
    if current_status == status:
        logger.debug(
            f"[lifecycle] Session {getattr(session, 'session_id', '?')} "
            f"already in terminal state {status!r}, skipping finalize"
        )
        return

    # CAS: re-read from Redis and compare on-disk status against caller's snapshot.
    # The re-read is ONLY for the status comparison -- the caller's object is used
    # for the save to preserve any companion fields set before this call.
    session_id = getattr(session, "session_id", None)
    if session_id:
        cas_start = time.monotonic()
        try:
            fresh = get_authoritative_session(session_id)
            if fresh is not None:
                on_disk_status = getattr(fresh, "status", None)
                if on_disk_status != current_status:
                    cas_elapsed = (time.monotonic() - cas_start) * 1000
                    logger.debug(
                        "[lifecycle-cas] CAS overhead: %.1fms (conflict detected)", cas_elapsed
                    )
                    raise StatusConflictError(
                        session_id=session_id,
                        expected_status=current_status or "unknown",
                        actual_status=on_disk_status or "unknown",
                        reason=f"finalize_session to {status!r}: {reason}",
                    )
            cas_elapsed = (time.monotonic() - cas_start) * 1000
            logger.debug("[lifecycle-cas] CAS overhead: %.1fms (finalize)", cas_elapsed)
        except StatusConflictError:
            raise
        except Exception as e:
            logger.debug(f"[lifecycle-cas] CAS re-read failed (non-fatal, proceeding): {e}")

    # 1. Lifecycle transition log
    try:
        session.log_lifecycle_transition(status, reason)
    except Exception as e:
        logger.debug(f"[lifecycle] Lifecycle log failed (non-fatal): {e}")

    # 2. Auto-tag session
    if not skip_auto_tag:
        try:
            from tools.session_tags import auto_tag_session

            session_id = getattr(session, "session_id", None)
            if session_id:
                auto_tag_session(session_id)
        except Exception as e:
            logger.debug(f"[lifecycle] Auto-tagging failed (non-fatal): {e}")

    # 3. Checkpoint branch state
    if not skip_checkpoint:
        try:
            from agent.agent_session_queue import checkpoint_branch_state

            checkpoint_branch_state(session)
        except Exception as e:
            logger.debug(f"[lifecycle] Branch checkpoint failed (non-fatal): {e}")

    # 4. Finalize parent session
    if not skip_parent:
        parent_id = getattr(session, "parent_agent_session_id", None)
        if parent_id:
            try:
                child_status = status
                _finalize_parent_sync(
                    parent_id,
                    completing_child_id=getattr(session, "agent_session_id", None),
                    completing_child_status=child_status,
                )
            except Exception as e:
                logger.debug(f"[lifecycle] Parent finalization failed (non-fatal): {e}")

    # 5. Set status + completed_at + save
    # Backfill _saved_field_values["status"] so Popoto's IndexedFieldMixin.on_save()
    # knows the old status and calls srem() to remove the old index entry. Without
    # this, lazy-loaded sessions (created via _create_lazy_model) start with an empty
    # _saved_field_values dict and the guard `if old_value is not None` is never
    # satisfied, leaving the session stranded in both old and new status index sets.
    # See: popoto/models/encoding.py _create_lazy_model() -- only KeyFields are seeded.
    # NOTE: _saved_field_values is a Popoto internal. If Popoto is upgraded, verify
    # this coupling is still valid by checking on_save() in IndexedFieldMixin.
    if hasattr(session, "_saved_field_values"):
        session._saved_field_values["status"] = current_status
    session.status = status
    session.completed_at = time.time()
    session.save()


def transition_status(
    session,
    new_status: str,
    reason: str = "",
    *,
    reject_from_terminal: bool = True,
) -> None:
    """Transition a session to a non-terminal status.

    Logs the lifecycle transition and updates the status.

    By default, rejects transitions from terminal statuses to prevent accidental
    respawning of completed/failed/killed sessions. Callers that legitimately need
    terminal->non-terminal transitions (e.g., _mark_superseded, revival) must pass
    reject_from_terminal=False explicitly.

    Idempotent: if the session is already in the target state, still saves to
    persist any companion fields set by the caller (fixes #873).

    CAS behavior: Re-reads the session from Redis and compares the on-disk
    status against the caller's in-memory status. If they differ, raises
    StatusConflictError. The caller's object is used for the save (not the
    re-read), preserving any companion fields set before this call.

    Lazy-load safety: Before saving, this function backfills
    session._saved_field_values["status"] with the current status so that
    Popoto's IndexedFieldMixin.on_save() guard can call srem() to remove the
    old index entry.

    Args:
        session: AgentSession instance to transition.
        new_status: Non-terminal status to set.
        reason: Human-readable reason for the transition.
        reject_from_terminal: If True (default), raise ValueError when the session's
            current status is terminal. Pass False for intentional terminal->non-terminal
            transitions (e.g., completed->superseded, revival).

    Raises:
        ValueError: If session is None, new_status is a terminal status,
            or current status is terminal and reject_from_terminal is True.
        StatusConflictError: If CAS detects a concurrent status change.
    """
    if session is None:
        raise ValueError("session must not be None")

    if new_status in TERMINAL_STATUSES:
        raise ValueError(
            f"transition_status() is for non-terminal statuses. "
            f"Got terminal status {new_status!r}. "
            f"Use finalize_session() for terminal transitions."
        )

    if new_status not in NON_TERMINAL_STATUSES:
        raise ValueError(f"Unknown status {new_status!r}. Known: {', '.join(sorted(ALL_STATUSES))}")

    # Guard: reject transitions from terminal statuses unless explicitly allowed
    current_status = getattr(session, "status", None)
    if reject_from_terminal and current_status in TERMINAL_STATUSES:
        raise ValueError(
            f"Cannot transition session {getattr(session, 'session_id', '?')} "
            f"from terminal status {current_status!r} to {new_status!r}. "
            f"Pass reject_from_terminal=False if this is intentional "
            f"(e.g., revival or superseding)."
        )

    # CAS: re-read from Redis and compare on-disk status against caller's snapshot.
    # The re-read is ONLY for the status comparison -- the caller's object is used
    # for the save to preserve any companion fields set before this call.
    session_id = getattr(session, "session_id", None)
    if session_id:
        cas_start = time.monotonic()
        try:
            fresh = get_authoritative_session(session_id)
            if fresh is not None:
                on_disk_status = getattr(fresh, "status", None)
                if on_disk_status != current_status:
                    cas_elapsed = (time.monotonic() - cas_start) * 1000
                    logger.debug(
                        "[lifecycle-cas] CAS overhead: %.1fms (conflict detected)", cas_elapsed
                    )
                    raise StatusConflictError(
                        session_id=session_id,
                        expected_status=current_status or "unknown",
                        actual_status=on_disk_status or "unknown",
                        reason=f"transition_status to {new_status!r}: {reason}",
                    )
            cas_elapsed = (time.monotonic() - cas_start) * 1000
            logger.debug("[lifecycle-cas] CAS overhead: %.1fms (transition)", cas_elapsed)
        except StatusConflictError:
            raise
        except Exception as e:
            logger.debug(f"[lifecycle-cas] CAS re-read failed (non-fatal, proceeding): {e}")

    # Idempotency: if already in this state, still save to persist companion fields
    # (fixes #873 -- companion field edits were silently dropped on idempotent path)
    if current_status == new_status:
        logger.debug(
            f"[lifecycle] Session {getattr(session, 'session_id', '?')} "
            f"already in state {new_status!r}, saving companion fields"
        )
        session.save()
        return

    # Lifecycle transition log
    try:
        session.log_lifecycle_transition(new_status, reason)
    except Exception as e:
        logger.debug(f"[lifecycle] Lifecycle log failed (non-fatal): {e}")

    # Set status + save
    # Backfill _saved_field_values["status"] so Popoto's IndexedFieldMixin.on_save()
    # knows the old status and calls srem() to remove the old index entry. Without
    # this, lazy-loaded sessions (created via _create_lazy_model) start with an empty
    # _saved_field_values dict and the guard `if old_value is not None` is never
    # satisfied, leaving the session stranded in both old and new status index sets.
    # NOTE: _saved_field_values is a Popoto internal. If Popoto is upgraded, verify
    # this coupling is still valid by checking on_save() in IndexedFieldMixin.
    if hasattr(session, "_saved_field_values"):
        session._saved_field_values["status"] = current_status
    session.status = new_status
    session.save()


def _finalize_parent_sync(
    parent_id: str,
    completing_child_id: str | None = None,
    completing_child_status: str | None = None,
) -> None:
    """Check if all children of a parent are terminal; if so, finalize the parent.

    Transitions parent from waiting_for_children to completed (all children
    succeeded) or failed (any child failed). Idempotent: no-op if parent is
    already in a terminal state or no longer exists.

    Args:
        parent_id: The id of the parent AgentSession.
        completing_child_id: If provided, the agent_session_id of the child that is
            currently completing. Its Redis status may still be "running",
            so completing_child_status overrides it.
        completing_child_status: The intended terminal status ("completed"
            or "failed") of the completing child.
    """
    from models.agent_session import AgentSession

    try:
        parent = AgentSession.get_by_id(parent_id)
    except Exception as exc:
        logger.warning(
            "[session-hierarchy] Parent session %s lookup raised exception "
            "during finalization (%s) -- treating child as orphaned",
            parent_id,
            exc,
        )
        return

    if parent is None:
        logger.warning(
            f"[session-hierarchy] Parent session {parent_id} not found during "
            f"finalization -- parent may have been deleted or already finalized"
        )
        return

    # If parent is already terminal, nothing to do
    if parent.status in TERMINAL_STATUSES:
        logger.debug(
            f"[session-hierarchy] Parent {parent_id} already terminal "
            f"(status={parent.status}), skipping finalization"
        )
        return

    # Find all children
    children = list(AgentSession.query.filter(parent_agent_session_id=parent_id))
    if not children:
        logger.debug(f"[session-hierarchy] No children found for parent {parent_id}")
        return

    # If parent isn't waiting_for_children yet, set it
    if parent.status != "waiting_for_children":
        transition_status(parent, "waiting_for_children", "child session completing")

    terminal_statuses = TERMINAL_STATUSES

    def effective_status(child):
        """Get effective status, overriding for the currently-completing child."""
        if completing_child_id and getattr(child, "agent_session_id", None) == completing_child_id:
            return completing_child_status
        return child.status

    child_statuses = [effective_status(c) for c in children]
    non_terminal = [s for s in child_statuses if s not in terminal_statuses]

    if non_terminal:
        logger.debug(
            f"[session-hierarchy] Parent {parent_id} has "
            f"{len(non_terminal)} non-terminal children -- waiting"
        )
        return

    # All children are terminal -- determine final parent status
    any_failed = any(s == "failed" for s in child_statuses)
    new_status = "failed" if any_failed else "completed"

    completed_count = sum(1 for s in child_statuses if s == "completed")
    failed_count = sum(1 for s in child_statuses if s == "failed")
    logger.info(
        f"[session-hierarchy] Finalizing parent {parent_id}: "
        f"{completed_count} completed, {failed_count} failed -> {new_status}"
    )

    _transition_parent(parent, new_status)


def _transition_parent(parent, new_status: str) -> None:
    """Transition a parent session to a new status.

    Uses finalize_session() for terminal statuses and transition_status()
    for non-terminal statuses, ensuring consistent lifecycle handling.

    Args:
        parent: AgentSession instance of the parent.
        new_status: The new status to set.
    """
    if new_status in TERMINAL_STATUSES:
        # Use finalize_session for terminal transitions, but skip parent
        # finalization to avoid infinite recursion (this IS the parent finalization)
        finalize_session(
            parent,
            new_status,
            reason="all children terminal",
            skip_parent=True,
        )
    else:
        transition_status(parent, new_status, reason="child session state change")

    logger.info(
        f"[session-hierarchy] Parent {getattr(parent, 'agent_session_id', '?')} "
        f"transitioned to status={new_status}"
    )
