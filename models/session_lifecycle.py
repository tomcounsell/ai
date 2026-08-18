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

import json
import logging
import os
import socket
import time
from typing import NamedTuple

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

# Statuses from which a session can be resumed (TERMINAL_STATUSES minus "cancelled").
# "cancelled" is an intentional human stop and must never be auto-resumed.
RESUMABLE_STATUSES: frozenset[str] = frozenset({"completed", "killed", "failed", "abandoned"})

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
        "paused_budget",  # paused by the per-tool budget backstop (#1821); human-only recovery
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
    # paused_budget is a NON-drip status by design (#1821). The per-tool budget
    # backstop moves a runaway session here; recovery is HUMAN-only. It is
    # deliberately excluded from session_recovery_drip.run() (which drips only
    # "paused"/"paused_circuit" back to pending) so no
    # pending→denied→paused→pending runaway can form — tool_call_count /
    # total_cost_usd are cumulative and never reset, so an auto-drip would loop.
    "paused_budget": "human",
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
    reject_from_terminal: bool = True,
    emit_telemetry: bool = True,
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

    Terminal-state guard (kill-is-terminal invariant): When the session is
    already in a terminal state and the caller wants to transition it to a
    *different* terminal state (e.g., killed -> completed), raises
    StatusConflictError by default. This mirrors the symmetric guard on
    transition_status() and enforces the rule that "once terminal, always
    terminal -- unless the caller has explicitly documented why they need to
    re-classify." Callers with a legitimate re-classification need (rare)
    must pass reject_from_terminal=False.

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
        reject_from_terminal: If True (default), raise StatusConflictError when the
            session is already terminal and the caller is trying to transition it to
            a different terminal status. Pass False for intentional terminal->terminal
            re-classification (e.g., escalating abandoned->failed on timeout).

    Raises:
        ValueError: If session is None or status is not terminal.
        StatusConflictError: If CAS detects a concurrent status change, or if
            reject_from_terminal is True and the session is already in a
            different terminal state.
    """
    if session is None:
        raise ValueError("session must not be None")

    if status not in TERMINAL_STATUSES:
        raise ValueError(
            f"finalize_session() requires a terminal status "
            f"({', '.join(sorted(TERMINAL_STATUSES))}), got {status!r}. "
            f"Use transition_status() for non-terminal transitions."
        )

    # Additive telemetry tap — no behavior change
    if emit_telemetry:
        try:
            from agent.session_telemetry import record_telemetry_event

            _sid = getattr(session, "session_id", None) or getattr(session, "id", None)
            record_telemetry_event(
                _sid,
                {
                    "type": "status_transition",
                    "from": getattr(session, "status", None),
                    "to": status,
                    "reason": reason or "",
                    "kill": None,
                },
            )
            # Terminal transition: reap the session's in-memory telemetry state.
            # This is the last telemetry event a session ever emits, so it is the
            # correct hook to evict per-session locks/counters/handles and prevent
            # the maps from growing unbounded over the worker's lifetime.
            from agent.session_telemetry import (
                finalize_session as _finalize_telemetry,
            )

            _finalize_telemetry(_sid)
        except Exception:
            pass

    # AC4 Seat A: reset the self-draft attempt counter on every terminal finalize,
    # unconditionally (regardless of emit_telemetry).  Health-checker callers pass
    # emit_telemetry=False, so a reset inside the emit_telemetry block would be
    # skipped on exactly the failed/abandoned paths that need cleanup.  Placing
    # the reset here covers completed (happy path) and all health-checker terminals.
    # Best-effort: a Redis failure never blocks the terminal transition.
    try:
        _ac4_sid = getattr(session, "session_id", None) or getattr(session, "id", None)
        if _ac4_sid:
            from agent.steering import reset_self_draft_attempts as _reset_attempts

            _reset_attempts(_ac4_sid)
    except Exception:
        pass

    # Idempotency: if already in this terminal state, skip side effects
    current_status = getattr(session, "status", None)
    if current_status == status:
        logger.debug(
            f"[lifecycle] Session {getattr(session, 'session_id', '?')} "
            f"already in terminal state {status!r}, skipping finalize"
        )
        return

    # Terminal-state guard: refuse to re-classify a terminal session unless explicitly opted out.
    # Mirrors transition_status(reject_from_terminal=True). See docs/features/session-lifecycle.md
    # ("kill-is-terminal invariant") and issue #1208 for rationale.
    if reject_from_terminal and current_status in TERMINAL_STATUSES:
        raise StatusConflictError(
            session_id=getattr(session, "session_id", "?") or "?",
            expected_status=current_status,
            actual_status=status,
            reason=(
                f"finalize_session({status!r}) blocked: session already terminal "
                f"({current_status!r}). Pass reject_from_terminal=False if intentional."
            ),
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
                        reason=f"finalize_session to {status!r}: {reason}",
                    )
            cas_elapsed = (time.monotonic() - cas_start) * 1000
            logger.debug("[lifecycle-cas] CAS overhead: %.1fms (finalize)", cas_elapsed)
        except StatusConflictError:
            raise
        except Exception as e:
            logger.debug(f"[lifecycle-cas] CAS re-read failed (non-fatal, proceeding): {e}")

    # 0. Deferred self-draft chokepoint flush (telegram).
    # A reply deferred for self-draft that is never redrafted must be delivered on
    # EVERY terminal path. This is the single chokepoint that all terminal writes
    # funnel through, so wiring the flush here covers completed, failed, abandoned,
    # and any future terminal status by construction — replacing the fragile
    # per-branch wiring in session_health.py.
    #
    # Placement invariant: this runs ONLY on a legitimate first-time terminal
    # transition. It sits AFTER the idempotency early-return (already-terminal
    # sessions returned above) AND AFTER the reject_from_terminal guard (illegal
    # terminal->terminal re-transitions raised above), so a rejected re-transition
    # never triggers a flush. The flush reads the FRESH authoritative session
    # internally, so it is unaffected by the caller's possibly-stale extra_context.
    #
    # Synchronous by necessity: the completed path has no running event loop, so
    # the async _deliver_deferred_self_draft_fallback cannot be awaited here.
    # Exception-isolated: a flush failure (even an import error) must NEVER prevent
    # the status write below — losing a reply is bad, but failing to finalize the
    # session is worse. Lazy import avoids an import cycle (session_lifecycle is
    # imported very early).
    try:
        from agent.session_health import flush_deferred_self_draft_sync

        flush_deferred_self_draft_sync(session, status)
    except Exception as e:
        logger.warning(f"[lifecycle] Deferred self-draft flush failed (non-fatal): {e}")

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
    # KEEP (B1, see #2083 ledger): NOT subsumed by Popoto 1.8.0 INDEX_SWAP_LUA.
    # The atomic Lua swap needs the server-side `{field}\x00idxset` pointer to
    # SREM the old set; a pointer-less legacy row falls back to the client hint
    # sourced from `_saved_field_values`, so this backfill is the sole channel
    # keeping old-set removal correct for pointer-less rows. Shares a single
    # disposition with the B2 backfill in transition_status.
    if hasattr(session, "_saved_field_values"):
        session._saved_field_values["status"] = current_status
    session.status = status
    session.completed_at = time.time()
    # Durability plan #2494: the execution fence (``exec_pid`` /
    # ``pid_create_time``) is deliberately NOT cleared on terminal transition.
    # ``find_live_session_by_pid`` only scans NON_TERMINAL statuses, so a
    # terminal row is already excluded from ownership resolution; and the orphan
    # reaper WANTS to find a still-alive detached harness whose session went
    # terminal, then reap it under the fence compare (a recycled pid reads as
    # "not ours"). Retaining the fence keeps that reap possible.
    session.save()

    # 5.1. Defensive srem: remove session from ALL non-target status index sets.
    # This cleans up orphan index entries left by stale-object full saves that
    # clobbered the status before this finalize ran. See #950 for root cause analysis.
    # KEEP (B3, see #2083 ledger): NOT subsumed by Popoto 1.8.0 INDEX_SWAP_LUA.
    # The Lua swap guarantees pointer atomicity, NOT value freshness — a stale
    # in-memory object doing a full save() writes a superseded status value
    # atomically, and the index faithfully follows that stale value. In the
    # compound legacy scenario (pointer-less row, no _saved_field_values hint)
    # this srem is the only path that scrubs the row out of the wrong set.
    # NOTE: Two Popoto coupling points that must be re-verified on Popoto upgrade:
    #   1. _saved_field_values["status"] backfill above (finalize_session/transition_status)
    #   2. Defensive srem index key construction below (get_special_use_field_db_key + DB_key)
    try:
        from popoto.models.db_key import DB_key
        from popoto.redis_db import POPOTO_REDIS_DB

        member_key = session.db_key.redis_key
        status_field = session._meta.fields["status"]
        field_cls = type(status_field)
        for other_status in ALL_STATUSES:
            if other_status == status:
                continue
            idx_key = DB_key(
                field_cls.get_special_use_field_db_key(session, "status"),
                other_status,
            )
            POPOTO_REDIS_DB.srem(idx_key.redis_key, member_key)
    except Exception as e:
        logger.debug(f"[lifecycle] Defensive srem failed (non-fatal): {e}")

    # 5.5. Update TaskTypeProfile (after auto_tag sets task_type AND after status is saved)
    # Runs only for completed sessions — profile is now authoritative after the Redis save above.
    if not skip_auto_tag and status == "completed":
        try:
            from models.task_type_profile import update_task_type_profile

            _profile_session_id = getattr(session, "session_id", None)
            if _profile_session_id:
                update_task_type_profile(_profile_session_id)
        except Exception as e:
            logger.debug(f"[lifecycle] TaskTypeProfile update failed (non-fatal): {e}")

    # Analytics: record session completion
    try:
        from analytics.collector import record_metric

        record_metric(
            "session.completed",
            1,
            {
                "session_type": getattr(session, "session_type", None),
                "status": status,
            },
        )
    except Exception:
        pass

    # Durable secondary archive: upsert this session's terminal snapshot into
    # SQLite. Exception-isolated -- an archive failure must never break the
    # terminal transition itself. See docs/plans/session-archive-sqlite.md.
    try:
        from agent import session_archive  # lazy import to avoid import cycles

        session_archive.export_session(session)
    except Exception:
        logger.warning("session_archive export_session failed for %s", session.id, exc_info=True)

    # 6. Single-owner lease release (issue #2026, WS1). On EVERY terminal
    # transition — run completion AND graceful failure — the supervising run
    # frees its issue lease immediately and clears the supervised-run signal,
    # so the happy path never waits out the (now 30-min) TTL and a subsequent
    # bare `session-ensure` falls back to standalone semantics instead of
    # inheriting a dead run_id. `release_issue_lock` is COMPARE-AND-DELETE:
    # only the run whose `active_run_id` still owns the lock actually releases
    # it, so a child/foreign session finalizing here is a harmless no-op.
    # Best-effort and exception-isolated — a Redis error must never break the
    # terminal transition (fail-open, per the module's lock-op contract).
    try:
        _iss = getattr(session, "issue_number", None)
        _rid = getattr(session, "active_run_id", None)
        if _iss and _rid:
            release_issue_lock(_iss, _rid)
            try:
                from agent.supervised_run import clear_supervised_run_signal

                clear_supervised_run_signal(
                    _iss, _rid, working_dir=getattr(session, "working_dir", None)
                )
            except Exception as e:
                logger.debug("[lifecycle] supervised-run signal clear failed (non-fatal): %s", e)
    except Exception as e:
        logger.debug("[lifecycle] issue-lease release on finalize failed (non-fatal): %s", e)


def transition_status(
    session,
    new_status: str,
    reason: str = "",
    *,
    reject_from_terminal: bool = True,
    emit_telemetry: bool = True,
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

    # Additive telemetry tap — no behavior change
    if emit_telemetry:
        try:
            from agent.session_telemetry import record_telemetry_event

            _sid = getattr(session, "session_id", None) or getattr(session, "id", None)
            record_telemetry_event(
                _sid,
                {
                    "type": "status_transition",
                    "from": getattr(session, "status", None),
                    "to": new_status,
                    "reason": reason or "",
                    "kill": None,
                },
            )
        except Exception:
            pass

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
    # KEEP (B2, see #2083 ledger): NOT subsumed by Popoto 1.8.0 INDEX_SWAP_LUA —
    # same legacy pointer-less mechanism as B1 (finalize_session). Both backfill
    # sites share one disposition; do not remove one without the other.
    if hasattr(session, "_saved_field_values"):
        session._saved_field_values["status"] = current_status
    session.status = new_status
    session.save()

    # Analytics: record session start when transitioning to running
    if new_status == "running" and current_status != "running":
        try:
            from analytics.collector import record_metric

            record_metric(
                "session.started",
                1,
                {
                    "session_type": getattr(session, "session_type", None),
                    "project_key": getattr(session, "project_key", None),
                },
            )
        except Exception:
            pass


# Short-TTL SETNX gate protecting ONLY the pending->running acquisition
# (issue #1817, workstream B2). GRAIN OF SALT for future maintainers:
#
# This is a narrow, ADDITIVE gate -- it is not, and must never become, a
# replacement for the generic optimistic-concurrency CAS inside
# transition_status() above (the `on_disk_status != current_status` compare).
# That CAS governs EVERY non-terminal status edge in the system -- paused,
# superseded, kill, finalize, and this same plan's own C1 fix, which
# transitions a parent out of waiting_for_children via transition_status().
# An earlier critique round flagged deleting/replacing that CAS as a
# BLOCKER: it would strip optimistic-concurrency protection from every
# other edge, a correctness regression far worse than the bug this claim
# fixes. Do not delete it, and do not fold this claim's logic into it.
#
# Why pending->running specifically needs an extra gate: multiple
# independent actors (the worker's pop loop, the valor-session CLI resume
# path, catchup/reflections drip) can each read the SAME pending session
# and race to become the one that runs it. The run-claim SETNX makes the
# read-then-transition sequence atomic from the caller's perspective: only
# the actor that wins the SETNX proceeds to call
# transition_status(session, "running", ...); the loser skips the
# candidate entirely. This guarantees at most one actor ever attempts the
# transition for a given session_id -- the CAS above remains as the
# second line of defense for the transition the winner performs, and as
# the ONLY defense for every other status edge.
RUN_CLAIM_TTL_SECONDS = 30  # short: only needs to cover the query -> transition_status window


def claim_pending_run(session_id: str, worker_id: str, ttl: int = RUN_CLAIM_TTL_SECONDS) -> bool:
    """Atomically claim the right to transition ``session_id`` from pending to running.

    Returns ``True`` if this caller won the claim -- it must proceed to call
    ``transition_status(session, "running", ...)``. Returns ``False`` if
    another actor already holds the claim -- the caller must skip this
    session (a peer is handling it, or already did).

    Backed by a plain (non-Popoto-managed) Redis key
    ``session:runclaim:{session_id}``, matching the existing ``SET NX``
    idiom used elsewhere for short-lived coordination locks (see
    ``agent/session_health.py`` and ``agent/session_pickup.py``). This is
    NOT a general-purpose lock manager -- it exists solely to gate this one
    transition.

    Fails OPEN (returns ``True``) on Redis errors: a Redis hiccup degrades
    to today's CAS-only protection rather than starving the pending queue.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        key = f"session:runclaim:{session_id}"
        acquired = _R.set(key, worker_id, nx=True, ex=ttl)
        return bool(acquired)
    except Exception as e:
        logger.warning(
            "[session-lifecycle] run-claim acquisition failed for %s (failing open): %s",
            session_id,
            e,
        )
        return True


# ── Issue-level SDLC ownership lock (issues #1954, #2003) ───────────────
#
# Two independent SDLC entry points -- a local CLI session and the
# standalone worker process -- can each resolve the IDENTICAL deterministic
# session_id (``sdlc-local-{issue_number}``) for the same GitHub issue. If
# ownership were compared by session_id, the lock would be a no-op: both
# live processes would see "I already own this" and duplicate SDLC work
# would proceed unchecked -- the root cause of the #1915 duplicate-PR
# incident. Ownership is therefore compared by a per-RUN ``run_id`` (issue
# #2003): one uuid-hex identity per logical pipeline run, minted exclusively
# by ``tools.sdlc_session_ensure.ensure_session()`` when it wins the SET NX
# contest, and passed EXPLICITLY to every state-mutating call (``--run-id``
# on the sdlc-tool CLIs; ``AgentSession.active_run_id`` read-back on the two
# in-process renewal paths). session_id is carried in the stored payload for
# human-readable display only -- it must never be used to decide ownership.
#
# Modeled directly on claim_pending_run() above: same plain (non-Popoto-
# managed) SET NX EX Redis idiom, same fail-open behavior on Redis errors.
# The lock key is NOT Popoto-managed, so raw Redis GET/SET/EXPIRE/EVAL here
# is fine and already the established pattern.

# Lease TTL sized to the p99 stage wall time, not to a heartbeat (issue #2026,
# WS1 — single-owner lease). A `claude -p` supervisor is BLOCKED inside the
# synchronous stage call and makes zero sdlc-tool writes mid-stage, so there is
# no in-process executor for a renewal heartbeat; instead the lease default is
# set above the observed p99 stage wall time so it survives any single stage
# without a mid-stage renewal. The happy path frees the lease immediately —
# the supervisor calls `release_issue_lock` on run completion AND graceful
# failure (see `finalize_session`) — so the TTL is only the crash backstop; a
# genuinely dead owner is reclaimed within <= TTL by the existing orphaned_lock
# self-heal.
#
# GRAIN OF SALT: 1800s (30 min) is PROVISIONAL and TUNABLE. It was chosen from
# observed batch stage wall times of 6-25 min (2026-07-13 forensics) — modestly
# above the ceiling, not absurdly high. Revisit if stages routinely run longer,
# or lower it once a mid-stage renewer exists. Override per-environment via the
# ISSUE_LOCK_TTL_SECONDS env var.
ISSUE_LOCK_TTL_SECONDS = int(os.environ.get("ISSUE_LOCK_TTL_SECONDS", "1800"))

# How recently the lease must have been renewed for its owning run to count as
# LIVE (issue #2620). Renewers tick well inside this window: the detached
# heartbeat every ``ISSUE_LOCK_TTL_SECONDS // 3`` (tools/sdlc_lease_heartbeat.py)
# and the worker's in-process renewal every 60s, so two consecutive missed ticks
# are tolerated before a lease reads orphaned -- and the TTL (one tick later)
# frees it outright regardless.
# GRAIN OF SALT: ``2 * (TTL // 3)`` (1200s at the default TTL) is
# PROVISIONAL/TUNABLE -- wide enough to absorb a skipped tick or a Redis
# hiccup, narrow enough that a genuinely dead owner is still reported orphaned
# before its TTL lapses. Override via ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS.
ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS = int(
    os.environ.get("ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS", str(2 * (ISSUE_LOCK_TTL_SECONDS // 3)))
)

# How long a DEAD durable-renewer pid is tolerated while `renewed_at` is still
# fresh (issue #2648). Once a durable renewer has stamped its own identity onto
# the payload the pid evidence becomes checkable, and freshness stops being
# conclusive -- but a renewer can also be legitimately BETWEEN incarnations (a
# `./scripts/valor-service.sh restart` leaves the old worker pid dead until the
# new one's next 60s tick), so a dead renewer pid alone is not proof of death.
# This window is the bound on that gap. Deliberately TTL-INDEPENDENT, unlike
# ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS above: it measures process restart time,
# which does not move when the lease TTL moves. An operator who lowers the TTL
# far should lower this by hand too.
# GRAIN OF SALT: 180s is PROVISIONAL/TUNABLE and its ceiling rests on n=1.
# Floor: three worker renewal ticks (3 x 60s), comfortably above a restart.
# Ceiling: the 210s age at which the #2648 incident lease happened to be
# inspected -- one observation, not a distribution, so the next incident
# observation should re-derive it. Override via
# ISSUE_LOCK_RENEWER_GRACE_SECONDS.
ISSUE_LOCK_RENEWER_GRACE_SECONDS = int(os.environ.get("ISSUE_LOCK_RENEWER_GRACE_SECONDS", "180"))

# Modules whose renewals may record a DURABLE renewer identity on the payload
# (issue #2648). Membership is checked against an explicit `renewer_module`
# token the caller passes -- NEVER derived by introspection: the detached
# heartbeat runs as `python -m tools.sdlc_lease_heartbeat`, so `__name__`,
# `sys._getframe` and `inspect` all resolve to "__main__" there and an
# introspective check would silently never stamp in production while passing
# every import-based unit test. Everything else that renews this lease is a
# short-lived `sdlc-tool` CLI whose pid is dead within seconds; recording one of
# those as the renewer would report every lease orphaned after the grace.
_DURABLE_RENEWER_MODULES = {"tools.sdlc_lease_heartbeat", "agent.session_executor"}

# Compare-and-delete release (issue #2003, cycle-2 CONCERN 2): delete the
# lock key only if its value is still byte-identical to the payload we read
# (which carries our run_id). The standard Lua release pattern -- a raw DEL
# could race a successor's fresh acquisition and delete THEIR lock.
_RELEASE_IF_VALUE_MATCHES_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)

# Compare-and-set renewal (issue #2714, L0): rewrite the lock payload with a
# fresh TTL only if the stored value is still byte-identical to the one this
# call just read (ARGV[1]). ARGV[2] is the self-healed payload, ARGV[3] the
# TTL. A plain read-then-SET renewal has a real TOCTOU window: a release
# landing between the GET and the SET is UNDONE by the SET, which recreates
# the key -- a renewer minting a lease its owner already gave up. A `GET` on
# an absent key returns `false`, never equal to ARGV[1], so this script is
# structurally incapable of minting.
_RENEW_IF_VALUE_MATCHES_LUA = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then "
    "redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3]) return 1 end return 0"
)


class IssueLockResult(NamedTuple):
    """Result of touch_issue_lock().

    Attributes:
        acquired: True if the supplied run_id holds (or now holds, after
            this call) the lock.
        owner_session_id: The session_id recorded in the lock payload --
            for human-readable display/logging only. NEVER compare this to
            a caller's own session_id to determine ownership: two
            independent live processes can resolve the identical
            deterministic session_id for the same issue.
        owner_run_id: The run_id recorded in the lock payload. This IS the
            unit of ownership comparison.
        orphaned_lock: Peek-only signal -- True when the lock payload's
            recorded owner pid is no longer live (see
            ``_lock_owner_is_live``, issue #2305 defect 1): the pid is dead,
            or a live pid's ``create_time`` no longer matches (pid
            recycling). The owning run died between acquiring the lock and
            its next renewal. Bounded by the lock TTL. This is judged from
            the lock payload alone -- NOT from any AgentSession's status.
        target_repo: The GitHub ``owner/name`` slug pinned on the lock
            payload at acquire time (issue #2012) -- the single
            authoritative source writers/readers of the issue-keyed
            ``PipelineLedger`` use to assemble their ``(target_repo,
            issue_number)`` key, so they never re-resolve it per write/read
            via ``gh repo view``. ``None`` on a legacy/pre-#2012 payload
            that has not yet self-healed via a renewal (see
            ``touch_issue_lock``'s same-owner renewal branch), or when the
            lock is unheld.
    """

    acquired: bool
    owner_session_id: str | None
    owner_run_id: str | None = None
    orphaned_lock: bool = False
    target_repo: str | None = None


def _local_machine_id() -> str:
    """This machine's stable hardware identity; ``""`` when unresolvable.

    Thin fail-soft wrapper over :func:`config.machine.get_machine_id` (issue
    #2537). Kept as a module-level seam so lock-liveness tests can patch the
    local identity without shelling out to ``ioreg``.
    """
    try:
        from config.machine import get_machine_id

        return get_machine_id()
    except Exception:
        return ""


def _payload_from_same_machine(payload: dict) -> bool:
    """Return True when an issue-lock payload was stamped on THIS machine.

    Issue #2537: ``hostname`` is a mutable label, not a machine identity --
    renaming the Mac made every pre-rename payload read as foreign, so the
    liveness check skipped its locally-checkable dead-pid/pid-recycling
    guards forever. Same-machine detection therefore keys on the stable
    ``machine_id`` stamped at acquire time whenever BOTH sides are known;
    ``hostname`` remains only a human-readable breadcrumb and the legacy
    fallback:

    - Payload carries ``machine_id`` AND the local id resolves: compare the
      ids. Hostname is ignored -- a rename changes it, the machine id not.
    - Legacy payload (no ``machine_id``) or unresolvable local id: fall back
      to the hostname comparison, today's best available signal.
    """
    payload_machine_id = payload.get("machine_id")
    local_machine_id = _local_machine_id()
    if payload_machine_id and local_machine_id:
        return payload_machine_id == local_machine_id
    return payload.get("hostname") == socket.gethostname()


def _lock_renewal_is_fresh(payload: dict) -> bool | None:
    """Was this lease renewed recently enough to prove its owner is alive?

    Issue #2620. The payload's ``pid`` is stamped by the short-lived
    ``sdlc-tool session-ensure`` CLI at acquire time and that process is dead
    within seconds, while renewal never re-stamps it -- so pid inference alone
    reads EVERY locally-minted lease as orphaned, and a rival run facing a
    genuinely LIVE local owner was told "the owner died, wait out the TTL"
    instead of "stop". The renewers (the detached
    ``tools/sdlc_lease_heartbeat.py`` and the worker's in-process tick) do
    re-stamp ``renewed_at`` on every same-owner renewal, so renewal recency is
    the signal that actually tracks the RUN's life rather than one ephemeral
    process's.

    Returns ``True`` (renewed within ``ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS``
    -- owner alive), ``False`` (renewals stopped long enough ago to count as
    dead), or ``None`` when the payload carries no usable stamp (pre-#2620 or
    malformed): no evidence either way, leaving the pid check to decide.

    A stamp in the future (clock skew between two machines writing the same
    lease) yields a negative age and reads fresh -- fail toward live, the same
    posture every other indeterminate branch of the liveness check takes.
    """
    renewed_at = payload.get("renewed_at")
    if renewed_at is None:
        return None
    try:
        age = time.time() - float(renewed_at)
    except (TypeError, ValueError):
        return None
    return age <= ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS


def _lock_owner_is_live(payload: dict | None) -> bool:
    """Return True when the pid recorded in an issue-lock payload is still live.

    Single authoritative liveness signal for a ``sdlc-local-{N}`` run's issue
    lock (issue #2305, defect 1). Replaces the prior "any non-terminal eng
    session carrying this run_id" inference (the liveness mirage): a hollow
    tracking session with no pid, no heartbeat, and a frozen worktree used to
    read as live forever because SOME non-terminal AgentSession row existed.
    The only evidence trusted here is what ``touch_issue_lock`` stamped into
    the lock payload itself at acquire time -- ``pid`` + ``machine_id`` +
    ``hostname`` + ``create_time`` -- never an AgentSession's status.

    - Cross-machine (see ``_payload_from_same_machine``; keyed on the stable
      ``machine_id``, not the rename-mutable ``hostname``, issue #2537): a
      foreign-machine pid cannot be checked locally -- fails TOWARD True
      (assume live); the lock TTL is the ultimate backstop for a genuinely
      dead foreign owner.
    - Same-machine: the recorded pid is the live owner only if psutil finds it
      AND its current ``create_time()`` matches the recorded value within
      ``1e-3`` seconds (same tolerance as the drain path at
      ``session_health.py`` around line 5398). A pid that is alive but whose
      create_time does NOT match was recycled by the OS to an unrelated
      process -- NOT the lock owner (pid-recycling guard).
    - A legacy payload missing ``create_time``, or a malformed/absent
      payload: indeterminate -- fails TOWARD True. Never hard-kill on an
      unverifiable match; the lock TTL remains the backstop.

    Renewal freshness is consulted FIRST (issue #2620): the stamped pid
    belongs to the ephemeral ``session-ensure`` CLI, so on a locally-minted
    lease the pid is always dead and only ``renewed_at`` tracks whether the
    owning RUN is still alive (see ``_lock_renewal_is_fresh``). A fresh
    renewal is positive proof of life and short-circuits to True; a stale or
    absent stamp is no proof of death on its own, so the pid checks below
    still decide.

    Issue #2648 makes that short-circuit CORROBORATING rather than CONCLUSIVE,
    but ONLY where the payload carries evidence that can contradict it. A
    stamp outlives its writer: a run that acquires the lease, renews once and
    then dies used to read live for the full freshness window (1200s at the
    default TTL), because no pid on the payload was worth checking. The two
    durable renewers now record their own ``renewer_pid`` +
    ``renewer_create_time``, and only when that group is COMPLETE and the
    payload is same-machine may a dead renewer override a fresh stamp -- and
    then only after ``ISSUE_LOCK_RENEWER_GRACE_SECONDS`` of silence, so a
    renewer merely between incarnations (a worker restart) is not mistaken
    for a dead run. Absent, partial, or cross-machine identity leaves #2620's
    behavior byte-for-byte intact, which is what keeps #2703's ``--kill-orphans``
    exemption for a live-but-quiet local run.
    """
    if not payload or not isinstance(payload, dict):
        return True

    if _lock_renewal_is_fresh(payload) is True:
        renewer_pid = payload.get("renewer_pid")
        renewer_create_time = payload.get("renewer_create_time")
        if renewer_pid and renewer_create_time is not None and _payload_from_same_machine(payload):
            # Every early return in this branch is True and only the full
            # conjunction returns False: `reflections/utilities.py::
            # _lock_says_live`'s callers `continue` on True and on None but
            # ACT on False, so a false-dead here starts an autonomous rival
            # SDLC lane with no operator in the loop.
            try:
                # Function-body import, mirroring the one below. Do NOT hoist
                # it to module scope: the lock-liveness tests patch
                # `agent.session_health._psutil_process_for_pid`, and a
                # module-level name here would give them a second, silently
                # ineffective target.
                from agent.session_health import _psutil_process_for_pid

                proc = _psutil_process_for_pid(renewer_pid)
                if (
                    proc is not None
                    and abs(proc.create_time() - float(renewer_create_time)) <= 1e-3
                ):
                    logger.debug(
                        "[session-lifecycle] durable renewer pid=%s is alive -- owner live",
                        renewer_pid,
                    )
                    return True
                # Dead, or the OS recycled the pid to an unrelated process
                # (create_time mismatch). The clock is the AGE OF THE LAST
                # RENEWAL, not the duration of the renewer's death -- the
                # payload carries no such state, and any renewal (including
                # the ephemeral CLI ones) resets it, so this branch can only
                # fire during a stretch of total silence.
                renewed_age = time.time() - float(payload["renewed_at"])
                if renewed_age <= ISSUE_LOCK_RENEWER_GRACE_SECONDS:
                    logger.debug(
                        "[session-lifecycle] durable renewer pid=%s is gone but the lease "
                        "was renewed %.0fs ago (grace %ss) -- assuming live",
                        renewer_pid,
                        renewed_age,
                        ISSUE_LOCK_RENEWER_GRACE_SECONDS,
                    )
                    return True
            except Exception as e:
                logger.debug(
                    "[session-lifecycle] durable-renewer liveness check for pid=%s failed "
                    "(%s: %s) -- assuming live",
                    renewer_pid,
                    type(e).__name__,
                    e,
                )
                return True
            logger.warning(
                "[session-lifecycle] durable renewer pid=%s is dead and the lease has "
                "been quiet for %.0fs (grace %ss) -- reporting the owner DEAD despite a "
                "fresh renewal stamp (issue #2648)",
                renewer_pid,
                renewed_age,
                ISSUE_LOCK_RENEWER_GRACE_SECONDS,
            )
            return False
        return True

    pid = payload.get("pid")
    create_time = payload.get("create_time")

    if not pid or not (payload.get("machine_id") or payload.get("hostname")):
        return True

    if not _payload_from_same_machine(payload):
        return True

    if create_time is None:
        return True

    try:
        from agent.session_health import _psutil_process_for_pid

        proc = _psutil_process_for_pid(pid)
        if proc is None:
            return False
        current_create_time = proc.create_time()
        return abs(current_create_time - float(create_time)) <= 1e-3
    except Exception as e:
        logger.debug(
            "[session-lifecycle] lock-owner liveness check for pid=%s failed "
            "(%s: %s) -- assuming live",
            pid,
            type(e).__name__,
            e,
        )
        return True


def _current_process_create_time() -> float | None:
    """Return this process's psutil ``create_time()``, or None on failure.

    Written into the issue-lock payload at acquire time (issue #2305 defect
    1) so a later same-host liveness check can pair it with the recorded
    pid to detect pid recycling. Fails soft to None (legacy/indeterminate)
    rather than raising -- never blocks lock acquisition.
    """
    try:
        from agent.session_health import _psutil_process_for_pid

        proc = _psutil_process_for_pid(os.getpid())
        return proc.create_time() if proc is not None else None
    except Exception:
        return None


def _healed_renewal_payload(
    payload: dict,
    target_repo: str | None,
    *,
    stamp_renewer_identity: bool = False,
    renewer_module: str | None = None,
) -> dict:
    """Build the payload a same-owner renewal writes back.

    Self-healing renewal (BLOCKER round-2, issue #2012): a bare
    ``_R.expire(key, ttl)`` would renew the TTL without ever rewriting the
    payload -- a lock acquired before ``target_repo`` pinning existed (or
    whose payload otherwise lacks it) would then renew FOREVER without ever
    gaining the field, hard-failing every issue-keyed ledger write across
    cutover. So renewal re-writes the FULL payload: spread the EXISTING
    payload (never reconstruct a subset -- that would silently drop
    ``pid``/``hostname`` even though nothing asked it to) and override only
    the fields below.

    - ``target_repo``: re-pinned from the caller's freshly-resolved value
      when given, else whatever the payload already carried.
    - ``machine_id`` (issue #2537, same self-heal pattern): a pre-#2537
      payload gains the stable identity on its next same-owner renewal.
      Renewal requires a run_id match and run_ids are minted on exactly one
      machine, so the renewer IS on the owner's machine. Identity is
      immutable once stamped: an existing machine_id is never overwritten,
      and an unresolvable local id ("") is never written over the legacy
      hostname signal.
    - ``renewed_at`` (issue #2620): unlike the identity fields above, this
      one is deliberately OVERWRITTEN on every renewal -- the re-stamp IS
      the liveness signal, since the payload's pid (the ephemeral
      ``session-ensure`` CLI) is dead by the first tick. This also
      self-heals a pre-#2620 payload.
    - ``renewer_pid`` / ``renewer_create_time`` (issue #2648): the identity of
      the process performing THIS renewal, written only when the caller is one
      of the two durable renewers and STRIPPED on every other renewal. The
      invariant is a biconditional -- the group is on the payload if and only
      if the most recent renewal was durable -- which is what lets
      ``_lock_owner_is_live`` treat a fresh ``renewed_at`` as corroborating
      rather than conclusive. The renewer is always on the owner's machine
      (renewal requires a run_id match and run_ids are minted on exactly one
      machine), so no separate machine field is recorded. Write rule: both
      keys or neither -- a pid without a create_time is no identity.
    """
    healed_target_repo = target_repo if target_repo is not None else payload.get("target_repo")
    new_payload = {**payload, "target_repo": healed_target_repo}
    # Deliberate, NARROW exception to the "spread the EXISTING payload, never
    # reconstruct a subset" rule above (issue #2648, Key Element 0): the
    # renewer_* group is the one field group that must NOT survive a renewal
    # performed by someone else. The default same-owner branch is a bare
    # non-CAS `_R.set` of this payload, so without the strip an ephemeral CLI
    # renewal would re-carry an identity it read before a durable renewer's
    # deadline-exit clear and silently undo it. Everything else still spreads.
    new_payload.pop("renewer_pid", None)
    new_payload.pop("renewer_create_time", None)
    if stamp_renewer_identity:
        if renewer_module not in _DURABLE_RENEWER_MODULES:
            # Dropped, never raised: nothing on this path may be allowed to
            # break lock acquisition itself.
            logger.warning(
                "[session-lifecycle] renewer identity stamp requested by "
                "non-durable module %r -- dropping the stamp (issue #2648)",
                renewer_module,
            )
        else:
            renewer_create_time = _current_process_create_time()
            if renewer_create_time is not None:
                new_payload["renewer_pid"] = os.getpid()
                new_payload["renewer_create_time"] = renewer_create_time
    if not new_payload.get("machine_id"):
        local_machine_id = _local_machine_id()
        if local_machine_id:
            new_payload["machine_id"] = local_machine_id
    new_payload["renewed_at"] = time.time()
    return new_payload


def touch_issue_lock(
    issue_number: int | None,
    run_id: str | None,
    session_id: str = "",
    ttl: int = ISSUE_LOCK_TTL_SECONDS,
    peek: bool = False,
    target_repo: str | None = None,
    *,
    renew_only: bool = False,
    stamp_renewer_identity: bool = False,
    renewer_module: str | None = None,
    drop_renewer_identity: bool = False,
) -> IssueLockResult:
    """Acquire, renew, or peek the per-issue SDLC ownership lock.

    Backed by a plain (non-Popoto-managed) Redis key
    ``session:issuelock:{issue_number}`` holding a JSON payload
    ``{"run_id", "session_id", "pid", "machine_id", "hostname", "create_time",
    "target_repo"}``. Ownership is decided
    SOLELY by comparing the supplied ``run_id`` against the lock payload's
    ``run_id`` -- a fresh live check on every mutation -- never by
    session_id, since two independent processes can resolve the identical
    deterministic session_id for the same issue (see module note above;
    issues #1954/#2003, incident #1915).

    Behavior (non-peek):
    - No ``run_id`` supplied: never mutates. Minting is exclusive to
      ``ensure_session`` -- a mutation call without an identity must not
      SET NX its way into ownership. Reports the current holder
      (``acquired=False`` when a lock exists, ``True`` when free).
    - No existing key: ``SET NX EX`` claims it carrying ``run_id``.
      Returns ``acquired=True``. **Except under ``renew_only=True``**, which
      never mints: an absent key returns ``acquired=False`` with
      ``owner_run_id=None`` (issue #2714 L0).
    - Existing key, same run_id: renews the full payload with a fresh TTL.
      ``acquired=True``. Under ``renew_only=True`` the write is a
      compare-and-set that only lands if the value is still the one this call
      read.
    - Existing key, different run_id: a foreign run owns it. Returns
      ``acquired=False`` with the owner's run_id + session_id surfaced.
    - Malformed/legacy (non-JSON) value: treated as a foreign, non-matching
      holder -- fails toward "not acquired". Never raises on ``json.loads``.
    - Race: the ``SET NX`` fails because the key existed, but by the time
      of the follow-up ``GET`` the key has since expired -- treated as
      free; this attempt succeeds (``acquired=True``). This branch is also
      skipped under ``renew_only=True``: it is a second minting path, and a
      renewer must have none.

    Stale-owner takeover keeps TTL semantics: an expired lock is claimable
    by the next fresh candidate; no takeover reads ``active_run_id`` as
    authority.

    Args:
        issue_number: The GitHub issue number this lock guards. Falsy
            (``None`` or ``0``) is a no-op: fails open (``acquired=True``)
            without touching Redis, mirroring how other call sites in this
            module guard an absent identity.
        run_id: The caller's run identity -- the unit of ownership
            comparison. Minted only by ``ensure_session``; carried
            explicitly by every state-mutating caller.
        session_id: The caller's session_id, stored in the payload for
            display purposes only.
        ttl: Lock TTL in seconds. Defaults to ``ISSUE_LOCK_TTL_SECONDS``.
        peek: If True, reports the current lock state (same run_id
            comparison) WITHOUT acquiring, renewing, or otherwise mutating
            the lock. An unheld key is reported as ``acquired=True``
            (nothing blocking). A held key whose run_id matches no live
            session's ``active_run_id`` additionally reports
            ``orphaned_lock=True``.
        target_repo: The already-resolved ``owner/name`` GitHub slug to pin
            on the payload (issue #2012). Resolved exactly ONCE by the
            caller (``_acquire_run_lock_and_bind`` in
            ``tools/sdlc_session_ensure.py``, the one place the process env
            is authoritative) and passed through on every acquire/renew
            call so this function never resolves it itself. On a fresh
            acquire or a re-acquire-after-expiry, ``target_repo`` is
            written into the payload verbatim (including ``None`` -- a
            caller that could not resolve it is not this function's
            problem to paper over). On a same-owner renewal, see
            ``_healed_renewal_payload``.
        renew_only: Keyword-only. True marks this call a RENEWAL of a lease
            the caller believes it already holds (the detached lease
            heartbeat, ``tools/sdlc_lease_heartbeat.py``). A renewer must
            never mint, so both minting branches above are skipped and the
            renewal write goes through a compare-and-set (issue #2714 L0).
            The failure mode this closes: the heartbeat's extend landing a
            millisecond after its supervisor released the lease used to
            ``SET NX`` its way back into ownership of a lease nobody held,
            then renew it to the max-lifetime ceiling with no supervisor
            behind it.
        stamp_renewer_identity: Keyword-only (issue #2648). True records THIS
            process's pid + psutil create_time on the renewed payload, which
            is what makes the lease's pid evidence checkable and lets
            ``_lock_owner_is_live`` stop treating a fresh ``renewed_at`` as
            conclusive. Exactly TWO callers may pass it, and both must also
            pass ``renewer_module``: ``tools/sdlc_lease_heartbeat.py`` (the
            detached per-run heartbeat) and ``agent/session_executor.py`` (the
            worker's 60s tick). They are the only renewers that live as long
            as the run does. Every other renewal call site is a short-lived
            ``sdlc-tool`` CLI that is dead within seconds of writing, so
            recording one of those as the renewer would make the predicate
            report every lease orphaned once the grace window lapsed --
            strictly worse than the bug this exists to fix. A renewal that
            does NOT set this flag STRIPS any existing renewer identity (see
            ``_healed_renewal_payload``).
        renewer_module: Keyword-only. The caller's own dotted module path,
            checked against ``_DURABLE_RENEWER_MODULES``. Passed explicitly
            and never introspected -- the heartbeat runs under ``-m``, where
            every introspective route resolves to ``"__main__"``. A token
            outside the allowlist drops the stamp with one warning.
        drop_renewer_identity: Keyword-only (issue #2648, spike-5 shape (a)).
            True performs a same-owner compare-and-set write of the payload
            with the ``renewer_*`` group REMOVED and nothing else changed --
            ``renewed_at`` is preserved and the key keeps its REMAINING
            lifetime, so the clear is a pure key-removal that neither extends
            the lease nor refreshes the liveness clock. Called by the
            heartbeat at its two non-releasing deadline exits: a renewer that
            retired on its own clock must stop asserting an identity whose
            death would otherwise be read as proof the run died, which is the
            same judgement those exits' comments already make in prose.

    Fails OPEN (returns ``acquired=True``) on any Redis exception -- mirrors
    ``claim_pending_run()``'s existing fail-open behavior: a Redis hiccup
    degrades to no cross-process protection rather than wedging the SDLC
    pipeline. Each fail-open logs the swallowed error CLASS explicitly.
    A ``renew_only`` call is the ONE exception and fails CLOSED
    (``acquired=False``): reporting ownership it cannot verify is exactly
    the "renew a lease you do not hold" shape the mode exists to eliminate,
    and the caller's own next peek is a cheap recovery.
    """
    if not issue_number:
        return IssueLockResult(acquired=True, owner_session_id=None)

    key = f"session:issuelock:{issue_number}"

    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        if peek:
            raw = _R.get(key)
            if raw is None:
                return IssueLockResult(acquired=True, owner_session_id=None)
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                return IssueLockResult(acquired=False, owner_session_id=None)
            owner_run_id = payload.get("run_id")
            owner_session_id = payload.get("session_id")
            owner_target_repo = payload.get("target_repo")
            if run_id and owner_run_id == run_id:
                return IssueLockResult(
                    acquired=True,
                    owner_session_id=owner_session_id,
                    owner_run_id=owner_run_id,
                    target_repo=owner_target_repo,
                )
            return IssueLockResult(
                acquired=False,
                owner_session_id=owner_session_id,
                owner_run_id=owner_run_id,
                orphaned_lock=not _lock_owner_is_live(payload),
                target_repo=owner_target_repo,
            )

        if not run_id:
            # Mutation attempted with no identity: never mint (that is
            # ensure_session's exclusive job) -- report the current holder.
            raw = _R.get(key)
            if raw is None:
                return IssueLockResult(acquired=True, owner_session_id=None)
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                return IssueLockResult(acquired=False, owner_session_id=None)
            return IssueLockResult(
                acquired=False,
                owner_session_id=payload.get("session_id"),
                owner_run_id=payload.get("run_id"),
                target_repo=payload.get("target_repo"),
            )

        if drop_renewer_identity:
            # Deadline-exit clear (issue #2648, spike-5 shape (a)). A PURE
            # key-removal, deliberately NOT routed through
            # `_healed_renewal_payload`: that helper re-stamps `renewed_at`
            # unconditionally, and the CAS below would then take the default
            # 1800s TTL. Either would hand a run that is genuinely dead at its
            # deadline exit extra false-live AND extra lease lifetime versus
            # doing nothing at all -- widening the very window this issue
            # narrows, on the one path where the tightening is already given
            # up. So: build the payload inline (a second, narrower exception
            # to the "spread the EXISTING payload" rule), leave `renewed_at`
            # alone, and re-use the key's REMAINING lifetime.
            raw = _R.get(key)
            if raw is None:
                return IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                return IssueLockResult(acquired=False, owner_session_id=None)
            if payload.get("run_id") != run_id:
                # A successor owns the lease; its identity is none of our
                # business (Race 5).
                return IssueLockResult(
                    acquired=False,
                    owner_session_id=payload.get("session_id"),
                    owner_run_id=payload.get("run_id"),
                    target_repo=payload.get("target_repo"),
                )
            remaining_ms = _R.pttl(key)
            remaining = int(remaining_ms) // 1000 if remaining_ms is not None else -1
            if remaining <= 0:
                # `pttl` sentinels: -1 (key has no expiry) and -2 (key is
                # gone), plus sub-second remainders. None of them is a
                # lifetime, and inventing one here is how a clear turns into
                # an extension. Stand down; the TTL is the backstop.
                return IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)
            cleared_payload = {
                k: v for k, v in payload.items() if k not in ("renewer_pid", "renewer_create_time")
            }
            cleared = _R.eval(
                _RENEW_IF_VALUE_MATCHES_LUA,
                1,
                key,
                raw,
                json.dumps(cleared_payload),
                remaining,
            )
            if not cleared:
                return IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)
            return IssueLockResult(
                acquired=True,
                owner_session_id=payload.get("session_id"),
                owner_run_id=run_id,
                target_repo=payload.get("target_repo"),
            )

        if renew_only:
            # Renewal, never minting (issue #2714 L0). Deliberately reached
            # BEFORE the `SET NX` below: that statement acquires on an absent
            # key, and the `raw is None` follow-up after it treats a
            # since-expired key as acquired too. Both are minting paths, and a
            # renewer whose lease was just released must have neither -- it
            # must report the loss so its caller can stand down.
            raw = _R.get(key)
            if raw is None:
                return IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "[session-lifecycle] issue-lock value for issue=%s is not valid JSON "
                    "(malformed or legacy) -- refusing to renew",
                    issue_number,
                )
                return IssueLockResult(acquired=False, owner_session_id=None)
            if payload.get("run_id") != run_id:
                return IssueLockResult(
                    acquired=False,
                    owner_session_id=payload.get("session_id"),
                    owner_run_id=payload.get("run_id"),
                    target_repo=payload.get("target_repo"),
                )
            new_payload = _healed_renewal_payload(
                payload,
                target_repo,
                stamp_renewer_identity=stamp_renewer_identity,
                renewer_module=renewer_module,
            )
            # Compare-and-set against the exact bytes just read. A release
            # landing inside this window makes the script a no-op rather than
            # recreating the key.
            renewed = _R.eval(
                _RENEW_IF_VALUE_MATCHES_LUA, 1, key, raw, json.dumps(new_payload), ttl
            )
            if not renewed:
                return IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)
            return IssueLockResult(
                acquired=True,
                owner_session_id=payload.get("session_id"),
                owner_run_id=run_id,
                target_repo=new_payload.get("target_repo"),
            )

        value = json.dumps(
            {
                "run_id": run_id,
                "session_id": session_id,
                "pid": os.getpid(),
                # Stable hardware identity (issue #2537): what same-machine
                # liveness comparison keys on. `hostname` below is a mutable
                # label kept only as a human-readable breadcrumb -- renaming
                # the machine must not turn this lock foreign.
                "machine_id": _local_machine_id(),
                "hostname": socket.gethostname(),
                # psutil create_time of this process (issue #2305 defect 1,
                # pid-recycling guard): paired with `pid` so a same-host
                # liveness check can distinguish this process from an
                # unrelated one the OS later recycles the same pid to. The
                # self-heal renewal branch below re-spreads this payload
                # verbatim, so create_time survives TTL renewals unchanged.
                "create_time": _current_process_create_time(),
                # Renewal recency (issue #2620): re-stamped by every same-owner
                # renewal below, and the ONLY payload field that tracks the
                # owning RUN's life rather than the acquiring process's -- the
                # `pid` above belongs to the ephemeral `session-ensure` CLI and
                # is dead within seconds. See `_lock_renewal_is_fresh`.
                "renewed_at": time.time(),
                "target_repo": target_repo,
            }
        )
        acquired = _R.set(key, value, nx=True, ex=ttl)
        if acquired:
            return IssueLockResult(
                acquired=True,
                owner_session_id=session_id,
                owner_run_id=run_id,
                target_repo=target_repo,
            )

        raw = _R.get(key)
        if raw is None:
            # Key existed at SET-NX time but expired before this follow-up
            # GET (race window) -- nothing blocks us now; treat this
            # attempt as a successful acquisition.
            return IssueLockResult(
                acquired=True,
                owner_session_id=session_id,
                owner_run_id=run_id,
                target_repo=target_repo,
            )
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(
                "[session-lifecycle] issue-lock value for issue=%s is not valid JSON "
                "(malformed or legacy) -- treating as a foreign, non-matching holder",
                issue_number,
            )
            return IssueLockResult(acquired=False, owner_session_id=None)

        if payload.get("run_id") == run_id:
            # Self-healing renewal (see `_healed_renewal_payload`), re-validated
            # against the payload just fetched by THIS call (not a cached peek
            # from earlier) -- the caller (writers/readers of the issue-keyed
            # ledger) must call this non-peek method immediately before its own
            # ledger write, not trust an earlier peek. The single
            # read-compare-write happens within one function invocation with no
            # intervening peek, so this closes the stale-peek TOCTOU race
            # without any additional locking machinery.
            new_payload = _healed_renewal_payload(
                payload,
                target_repo,
                stamp_renewer_identity=stamp_renewer_identity,
                renewer_module=renewer_module,
            )
            _R.set(key, json.dumps(new_payload), ex=ttl)
            return IssueLockResult(
                acquired=True,
                owner_session_id=payload.get("session_id"),
                owner_run_id=run_id,
                target_repo=new_payload.get("target_repo"),
            )

        return IssueLockResult(
            acquired=False,
            owner_session_id=payload.get("session_id"),
            owner_run_id=payload.get("run_id"),
            target_repo=payload.get("target_repo"),
        )
    except Exception as e:
        if renew_only or drop_renewer_identity:
            # A renewer fails CLOSED (issue #2714 L0). This handler wraps the
            # ENTIRE function, including the CAS EVAL above, so the default
            # fail-open below would hand a renewer `acquired=True` on a
            # transient Redis error -- the exact "report ownership without
            # holding the key" shape the CAS exists to eliminate, letting a
            # heartbeat keep renewing (and keep publishing a supervised-run
            # signal for) a lease its supervisor already released. Reporting
            # the loss costs the caller one cheap re-peek; claiming ownership
            # it cannot verify costs a wedged issue. The deadline-exit clear
            # (issue #2648) fails closed for the same reason: reporting a
            # write that never landed as successful would end its bounded
            # retry on a lie.
            logger.warning(
                "[session-lifecycle] issue-lock renewal failed for issue=%s "
                "(failing closed; error class %s): %s",
                issue_number,
                type(e).__name__,
                e,
            )
            return IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)
        logger.warning(
            "[session-lifecycle] issue-lock acquisition failed for issue=%s "
            "(failing open; error class %s): %s",
            issue_number,
            type(e).__name__,
            e,
        )
        return IssueLockResult(
            acquired=True,
            owner_session_id=session_id,
            owner_run_id=run_id,
            target_repo=target_repo,
        )


def release_issue_lock(issue_number: int | None, run_id: str | None) -> bool:
    """Release the issue lock via COMPARE-AND-DELETE, never a raw DEL.

    Deletes ``session:issuelock:{issue_number}`` only if the stored payload
    still carries ``run_id`` (Lua value-compare release pattern -- issue
    #2003, cycle-2 CONCERN 2). A delayed cleanup can therefore never delete
    a successor's freshly acquired lock.

    Returns True if the lock was deleted, False otherwise (not held, held by
    a different run, or Redis error -- errors log the swallowed class and
    fail toward "not released").
    """
    if not issue_number or not run_id:
        return False

    key = f"session:issuelock:{issue_number}"
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        raw = _R.get(key)
        if raw is None:
            return False
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if payload.get("run_id") != run_id:
            return False
        return bool(_R.eval(_RELEASE_IF_VALUE_MATCHES_LUA, 1, key, raw))
    except Exception as e:
        logger.warning(
            "[session-lifecycle] issue-lock release failed for issue=%s (error class %s): %s",
            issue_number,
            type(e).__name__,
            e,
        )
        return False


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

    # PM final-delivery coordination (issue #1058): if the completion-turn
    # runner is in flight for this parent, defer finalization. The runner
    # transitions the parent to "completed" after delivering the summary.
    # Only applies to the success path — failed parents finalize immediately.
    if new_status == "completed":
        try:
            from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

            lock_key = f"pipeline_complete_pending:{parent_id}"
            if POPOTO_REDIS_DB.exists(lock_key):
                logger.info(
                    "[session-hierarchy] Completion runner active for %s — "
                    "deferring finalization to runner",
                    parent_id,
                )
                return
        except Exception as redis_err:
            # Redis unavailable: proceed with finalization (old behavior).
            logger.debug(
                "[session-hierarchy] pipeline_complete_pending check failed (%s); "
                "proceeding with finalization",
                redis_err,
            )

    _transition_parent(parent, new_status)


def _transition_parent(parent, new_status: str) -> None:
    """Transition a parent session to a new status.

    Uses finalize_session() for terminal statuses and transition_status()
    for non-terminal statuses, ensuring consistent lifecycle handling.

    Catches StatusConflictError at INFO level: post-#1208 the kill-is-terminal
    guard in finalize_session() rejects terminal->different-terminal flips by
    default. When a parent has been killed by an operator while children were
    still progressing, the natural "all children terminal -> finalize parent
    as completed" path tries to overwrite the kill — that is exactly the race
    the guard exists to defend against, and skipping the transition silently
    is the correct response.

    Args:
        parent: AgentSession instance of the parent.
        new_status: The new status to set.
    """
    if new_status in TERMINAL_STATUSES:
        # Use finalize_session for terminal transitions, but skip parent
        # finalization to avoid infinite recursion (this IS the parent finalization)
        try:
            finalize_session(
                parent,
                new_status,
                reason="all children terminal",
                skip_parent=True,
            )
        except StatusConflictError as e:
            logger.info("[session-hierarchy] Skipping parent transition: %s", e)
            return
    else:
        transition_status(parent, new_status, reason="child session state change")

    logger.info(
        f"[session-hierarchy] Parent {getattr(parent, 'agent_session_id', '?')} "
        f"transitioned to status={new_status}"
    )
