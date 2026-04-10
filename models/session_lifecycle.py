"""Consolidated session lifecycle management.

All session status mutations should go through this module:
- finalize_session() for terminal transitions (completed, failed, killed, abandoned, cancelled)
- transition_status() for non-terminal transitions (pending, running, active, dormant,
  waiting_for_children, superseded, paused_circuit, paused)

This ensures consistent lifecycle logging, auto-tagging, branch checkpointing,
and parent finalization regardless of which code path triggers the transition.

Design constraints:
- Must be importable from .claude/hooks/stop.py subprocess context (limited sys.path)
- Uses lazy imports for heavy dependencies (tools.session_tags, agent.agent_session_queue)
- All side effects are optional and fail-safe (catch and log, never block status save)
"""

import logging
import time

logger = logging.getLogger(__name__)

# Terminal statuses — sessions in these states are "done"
TERMINAL_STATUSES = frozenset({"completed", "failed", "killed", "abandoned", "cancelled"})

# Non-terminal statuses — sessions in these states are still active or paused
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

# Recovery ownership — maps every non-terminal status to the process responsible
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
    1. Lifecycle transition log
    2. Auto-tag session (unless skip_auto_tag)
    3. Checkpoint branch state (unless skip_checkpoint)
    4. Finalize parent session (unless skip_parent or no parent)
    5. Set status + completed_at + save

    Idempotent: if the session is already in the target terminal state,
    logs and returns without re-executing side effects.

    Lazy-load safety: Before saving, this function backfills
    session._saved_field_values["status"] with the current status so that
    Popoto's IndexedFieldMixin.on_save() guard can call srem() to remove the
    old index entry. Lazy-loaded sessions (from _create_lazy_model) only have
    KeyFields pre-populated in _saved_field_values, so without this backfill
    the old status index entry is never removed and the session appears in both
    old and new status index sets simultaneously.

    Args:
        session: AgentSession instance to finalize.
        status: Terminal status to set (completed, failed, killed, abandoned, cancelled).
        reason: Human-readable reason for the transition.
        skip_auto_tag: Skip auto-tagging (e.g., hooks subprocess context).
        skip_checkpoint: Skip branch checkpointing (e.g., hooks subprocess context).
        skip_parent: Skip parent finalization.

    Raises:
        ValueError: If session is None or status is not terminal.
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
    # See: popoto/models/encoding.py _create_lazy_model() — only KeyFields are seeded.
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

    Idempotent: if the session is already in the target state, logs and returns.

    Lazy-load safety: Before saving, this function backfills
    session._saved_field_values["status"] with the current status so that
    Popoto's IndexedFieldMixin.on_save() guard can call srem() to remove the
    old index entry. Lazy-loaded sessions (from _create_lazy_model) only have
    KeyFields pre-populated in _saved_field_values, so without this backfill
    the old status index entry is never removed and the session appears in both
    old and new status index sets simultaneously.

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

    # Idempotency: if already in this state, skip
    if current_status == new_status:
        logger.debug(
            f"[lifecycle] Session {getattr(session, 'session_id', '?')} "
            f"already in state {new_status!r}, skipping transition"
        )
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
            "during finalization (%s) — treating child as orphaned",
            parent_id,
            exc,
        )
        return

    if parent is None:
        logger.warning(
            f"[session-hierarchy] Parent session {parent_id} not found during "
            f"finalization — parent may have been deleted or already finalized"
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
            f"{len(non_terminal)} non-terminal children — waiting"
        )
        return

    # All children are terminal — determine final parent status
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
