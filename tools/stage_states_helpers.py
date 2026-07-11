"""Helpers for safely updating ``AgentSession.stage_states`` JSON field.

The ``stage_states`` field on PM sessions stores stage statuses and internal
metadata keys (``_verdicts``, ``_sdlc_dispatches``, ``_patch_cycle_count``,
``_critique_cycle_count``). Multiple writers (the verdict recorder, the
oscillation-counter writer in ``agent/sdlc_router.py``, and
``classify_outcome()`` in ``agent/pipeline_state.py``) can race on this single
JSON blob. A naive read-modify-write dropped concurrent writes.

This module exposes ``update_stage_states(session, update_fn)`` — a helper that
implements read-modify-write with optimistic retry. The update function is
applied to a snapshot, the session is saved, and the result is verified. On
conflict or mismatch, the helper reloads the session and retries (up to
``max_retries`` attempts). Exhaustion is logged as a WARNING with session_id,
update_fn name, and the retry count so sustained contention is observable.

This is not a replacement for a true distributed lock (Redis WATCH/MULTI) —
it is a practical mitigation that closes the common lost-write window without
cross-process coordination. Defer to a lock-based implementation only if
optimistic retry proves insufficient in production.

Usage:
    def add_verdict(states: dict) -> dict:
        verdicts = states.setdefault("_verdicts", {})
        verdicts["CRITIQUE"] = {"verdict": "NEEDS REVISION", "recorded_at": "..."}
        return states

    success = update_stage_states(session, add_verdict)
    if not success:
        # caller can decide whether to retry or accept the lost write
        ...

Metrics:
    Exhaustion increments ``sdlc_stage_states_retry_exhausted_total`` via the
    analytics collector when available. Missing analytics is logged at DEBUG
    and does not affect the helper's return value.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# Default number of retry attempts. Tuned to absorb routine contention while
# still surfacing sustained lost writes as WARNINGs.
DEFAULT_MAX_RETRIES = 3


def _load_states(record, field: str = "stage_states") -> dict:
    """Load the JSON stage-state blob from ``field`` on ``record`` as a plain dict.

    Returns an empty dict if the field is missing or malformed. ``record`` is
    historically always an ``AgentSession`` (``field="stage_states"``, the
    default); issue #2012 widens this to also accept a ``PipelineLedger``
    instance via ``field="stage_states_json"``.
    """
    raw = getattr(record, field, None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        # Copy so caller can safely mutate without touching the record
        return dict(raw)
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.debug(
                f"update_stage_states: malformed {field} JSON on record "
                f"{_record_label(record)}, treating as empty"
            )
            return {}
        if isinstance(data, dict):
            return data
        return {}
    return {}


def _record_label(record) -> str:
    """Human-readable identifier of ``record`` for logs -- session_id for an
    AgentSession, ledger_key for a PipelineLedger, ``"?"`` otherwise."""
    return str(getattr(record, "session_id", None) or getattr(record, "ledger_key", None) or "?")


def _reload_session(session: AgentSession):
    """Reload a session from Redis to get the latest stage_states.

    Returns the refreshed session (which may be the same object or a new one).
    On failure, returns the original session so the caller can still proceed.
    """
    try:
        from models.agent_session import AgentSession

        session_id = getattr(session, "session_id", None)
        if not session_id:
            return session
        matches = list(AgentSession.query.filter(session_id=session_id))
        if not matches:
            return session
        # Prefer a PM session (canonical owner of stage_states)
        for candidate in matches:
            if getattr(candidate, "session_type", None) == "eng":
                return candidate
        return matches[0]
    except Exception as e:
        logger.debug(f"update_stage_states: reload failed: {e}")
        return session


def _reload_ledger(ledger):
    """Reload a ``PipelineLedger`` from Redis to get the latest ``stage_states_json``.

    Mirrors ``_reload_session`` for the issue-keyed ledger backing store
    (issue #2012). Returns the refreshed ledger, or the original on any
    failure (never raises) so a Redis hiccup degrades to "retry against the
    stale copy" rather than crashing the write.
    """
    try:
        from agent.pipeline_ledger import PipelineLedger

        ledger_key = getattr(ledger, "ledger_key", None)
        if not ledger_key:
            return ledger
        fresh = PipelineLedger.load(ledger_key=ledger_key)
        return fresh if fresh is not None else ledger
    except Exception as e:
        logger.debug(f"update_stage_states: ledger reload failed: {e}")
        return ledger


def _record_exhaustion_metric(session_id: str, update_fn_name: str) -> None:
    """Best-effort metric emit on retry exhaustion."""
    try:
        from analytics.collector import record_metric

        record_metric(
            "sdlc_stage_states_retry_exhausted_total",
            1,
            {"update_fn": update_fn_name, "session_id": session_id},
        )
    except Exception as e:
        logger.debug(f"update_stage_states: metric emit skipped: {e}")


def update_stage_states(
    session: AgentSession,
    update_fn: Callable[[dict], dict],
    max_retries: int = DEFAULT_MAX_RETRIES,
    field: str = "stage_states",
    reload_fn: Callable[[object], object] | None = None,
) -> bool:
    """Apply ``update_fn`` to a JSON stage-state blob with optimistic retry.

    Loads the current blob, applies ``update_fn`` to a copy, writes the
    result back, and verifies the write by reloading and comparing. If the
    post-save reload differs from the locally applied dict, the write is
    assumed to have been clobbered by a concurrent writer and is retried up to
    ``max_retries`` times.

    Args:
        session: The record to modify. Historically always an
            ``AgentSession`` (``field="stage_states"``, the default); issue
            #2012 widens this to also accept a ``PipelineLedger`` instance by
            passing ``field="stage_states_json"`` (and, if the default
            reload doesn't fit, a ledger-aware ``reload_fn``). Must be a
            live, saveable Popoto model.
        update_fn: Callable taking the current stage-state dict and
            returning the updated dict. Must be idempotent / deterministic
            for retry safety — it is re-invoked on every retry with the
            freshly-reloaded state.
        max_retries: Maximum number of attempts before giving up. Default is
            ``DEFAULT_MAX_RETRIES`` (3). Values <1 are coerced to 1.
        field: The attribute name holding the JSON stage-state blob.
            Defaults to ``"stage_states"`` (the ``AgentSession`` field).
            Ledger callers pass ``"stage_states_json"``.
        reload_fn: Callable that reloads ``session`` from its backing store
            by key, returning a fresh instance (or the original on
            failure). Defaults to the ``AgentSession``-specific
            ``_reload_session`` when ``field == "stage_states"`` (preserving
            byte-identical behavior for every existing caller), else the
            ``PipelineLedger``-specific ``_reload_ledger``.

    Returns:
        ``True`` if the update was successfully written and verified.
        ``False`` if retries were exhausted, the record was unsavable, or
        any other failure occurred. Exhaustion emits a WARNING and increments
        the ``sdlc_stage_states_retry_exhausted_total`` metric when analytics
        is available.

    The helper never raises — failures return ``False`` so callers can
    continue gracefully (the write is metadata, not correctness-critical
    state).
    """
    if max_retries < 1:
        max_retries = 1
    if reload_fn is None:
        reload_fn = _reload_session if field == "stage_states" else _reload_ledger

    record_label = _record_label(session)
    update_fn_name = getattr(update_fn, "__name__", repr(update_fn))
    current_record = session

    for attempt in range(1, max_retries + 1):
        try:
            before = _load_states(current_record, field)
            # Apply update to a copy so update_fn mutations don't leak if the
            # save fails.
            snapshot = json.loads(json.dumps(before))
            updated = update_fn(snapshot)
            if not isinstance(updated, dict):
                logger.debug(
                    f"update_stage_states: update_fn {update_fn_name} "
                    f"returned non-dict on record {record_label}; aborting"
                )
                return False

            # Persist as JSON string (same shape as PipelineStateMachine._save).
            serialized = json.dumps(updated)
            setattr(current_record, field, serialized)
            current_record.save()

            # Verify by reloading
            verify_record = reload_fn(current_record)
            verify_states = _load_states(verify_record, field)

            if verify_states == updated:
                return True

            logger.debug(
                f"update_stage_states: verify mismatch on record {record_label} "
                f"attempt {attempt}/{max_retries} — retrying with reloaded state"
            )
            current_record = verify_record
        except Exception as e:
            logger.debug(
                f"update_stage_states: attempt {attempt}/{max_retries} "
                f"failed on record {record_label}: {e}"
            )
            current_record = reload_fn(current_record)

    logger.warning(
        f"update_stage_states: retries exhausted after {max_retries} attempts "
        f"(record={record_label}, update_fn={update_fn_name}). "
        f"Write may be lost."
    )
    _record_exhaustion_metric(session_id=record_label, update_fn_name=update_fn_name)
    return False
