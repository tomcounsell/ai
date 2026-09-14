"""The reconcile pass: reads intents, not a status map (Task 7).

Two independent counters, two owners, never confused: ``attempts`` belongs
to ``record_materialized`` (a materialize *retry* counter) and is never
read or written here; ``stale_sweeps`` belongs to this module alone (how
many 300s passes have found an ``admitted``/``materialized`` intent stuck
past its age threshold). The "Two counters, two owners" Verification row
greps the write (``HINCRBY.*attempts``), not the word, so this docstring is
free to say "attempts" without tripping it.

The reconcile pass is a controller (Decision 12): every case whose intents
it touches gets its own case-lease acquisition and presents that generation
to ``mark_reconciliation_required``/``cancel``, exactly like the scheduler
adapter's tick. A case whose lease is busy this sweep is simply left for
the next one -- a 300s cadence has no urgency a single skipped sweep costs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from tools.improvement_control import keys
from tools.improvement_control.intents import cancel, list_intents, mark_reconciliation_required

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    stale_sweeps: list[str] = field(default_factory=list)  # action_ids incremented
    forced_reconciliation: list[str] = field(default_factory=list)  # action_ids moved
    forced_terminal: list[str] = field(default_factory=list)  # action_ids dead-lettered
    released_slots: list[str] = field(default_factory=list)  # action_ids
    unit2_receipted_unknown: list[str] = field(default_factory=list)  # reservation_ids
    errors: dict[str, str] = field(default_factory=dict)  # action_id -> error class name


def _control_redis():
    from utils.redis_client import text_redis

    return text_redis()


def _bound_row(agent_session_id: str | None):
    if not agent_session_id:
        return None
    from models.session_lifecycle import get_authoritative_session

    return get_authoritative_session(agent_session_id)


def _head_revision(project_key: str, case_id: str) -> int:
    from tools.improvement_control.journal import read_head

    head = read_head(project_key, case_id)
    return head.revision if head is not None else 0


def reconcile(
    project_key: str = "valor",
    *,
    now: float | None = None,
    lease_ttl: int | None = None,
    lease=None,
) -> ReconcileResult:
    """One 300s sweep over every case's intents. Never raises to the scheduler."""
    import time

    from config.settings import settings
    from models.improvement_case import OPEN_CASE_STATES, ImprovementCase
    from models.session_lifecycle import TERMINAL_STATUSES

    if now is None:
        now = time.time()
    if lease_ttl is None:
        lease_ttl = settings.improvement.lease_ttl_seconds
    if lease is None:
        from tools.improvement_control.lease import default_lease

        lease = default_lease()
    threshold = 4 * lease_ttl
    max_attempts = settings.improvement.max_dispatch_attempts

    result = ReconcileResult()
    case_ids: list[str] = []
    for state in OPEN_CASE_STATES:
        case_ids.extend(
            c.id for c in ImprovementCase.query.filter(project_key=project_key, state=state)
        )

    for case_id in case_ids:
        intents = [
            i for i in list_intents(project_key, case_id) if _is_actionable(i, now, threshold)
        ]
        if not intents:
            continue
        lease_key = f"improve:{project_key}:{case_id}:lease"
        generation = lease.acquire(lease_key, ttl=lease_ttl)
        if generation is None:
            continue
        try:
            for intent in intents:
                try:
                    _reconcile_one_intent(
                        project_key,
                        case_id,
                        intent,
                        now,
                        threshold,
                        max_attempts,
                        generation,
                        TERMINAL_STATUSES,
                        result,
                    )
                except Exception as e:
                    logger.warning(
                        "[improvement-intent-reconcile] sweep failed for action_id=%s: %s: %s",
                        intent.action_id,
                        type(e).__name__,
                        e,
                    )
                    result.errors[intent.action_id] = type(e).__name__
        finally:
            lease.release(lease_key, generation)

    result.unit2_receipted_unknown = _sweep_unit2(project_key, now)
    return result


def _is_actionable(intent, now: float, threshold: float) -> bool:
    """Cheap pre-filter before a case pays a lease acquisition: only
    admitted/materialized/running intents can ever need this sweep, and
    only once they are actually past the age threshold (running's
    liveness check happens inside the sweep itself, so it stays eligible
    regardless of age here)."""
    if intent.state not in ("admitted", "materialized", "running"):
        return False
    if intent.state == "running":
        return True
    return (now - (intent.updated_ts or 0)) > threshold


def _reconcile_one_intent(
    project_key,
    case_id,
    intent,
    now,
    threshold,
    max_attempts,
    generation,
    terminal_statuses,
    result,
) -> None:
    age = now - (intent.updated_ts or 0)

    if intent.state in ("admitted", "materialized"):
        if age <= threshold:
            return
        stale_sweeps = _control_redis().hincrby(
            keys.intent_key(project_key, case_id, intent.action_id), "stale_sweeps", 1
        )
        result.stale_sweeps.append(intent.action_id)
        if stale_sweeps < max_attempts:
            return
        _force_reconciliation_and_terminal(
            project_key,
            case_id,
            intent,
            generation,
            terminal_statuses,
            result,
            reason="max_dispatch_attempts",
        )
        return

    if intent.state == "running":
        row = _bound_row(intent.agent_session_id)
        row_gone_or_terminal = row is None or row.status in terminal_statuses
        if row_gone_or_terminal and age > threshold:
            # Acted on the first sweep that sees it -- no stale_sweeps budget:
            # the session that held the slot is gone, waiting only keeps a
            # single slot held for nothing.
            r = mark_reconciliation_required(
                project_key,
                case_id,
                intent.action_id,
                expected_revision=_head_revision(project_key, case_id),
                generation=generation,
                from_state="running",
                reason="session_gone_or_terminal",
            )
            if r.accepted:
                result.forced_reconciliation.append(intent.action_id)
                result.released_slots.append(intent.action_id)
        return


def _force_reconciliation_and_terminal(
    project_key, case_id, intent, generation, terminal_statuses, result, *, reason: str
) -> None:
    from models.session_lifecycle import finalize_session

    r = mark_reconciliation_required(
        project_key,
        case_id,
        intent.action_id,
        expected_revision=_head_revision(project_key, case_id),
        generation=generation,
        from_state=intent.state,
        reason=reason,
    )
    if not r.accepted:
        return
    result.forced_reconciliation.append(intent.action_id)
    result.released_slots.append(intent.action_id)

    row = _bound_row(intent.agent_session_id)
    if row is not None and row.status not in terminal_statuses:
        finalize_session(row, "abandoned", reason=reason, dead_letter_stage="improve_intent")
        result.forced_terminal.append(intent.action_id)


def _sweep_unit2(project_key: str, now: float) -> list[str]:
    """Receipt any unit-2 reservation whose day window closed unsettled.
    Best-effort: the meter module owns its own schema."""
    try:
        from tools.paid_inference_meter import sweep_unsettled_reservations

        return sweep_unsettled_reservations(project_key, now=now)
    except Exception as e:
        logger.debug("[improvement-intent-reconcile] unit-2 sweep skipped: %s", e)
        return []


def cancel_wedge(project_key: str, case_id: str, action_id: str, *, generation: int, by: str):
    """Thin re-export so callers of this module don't also import intents.py
    for the one write `resume --force` needs."""
    return cancel(
        project_key,
        case_id,
        action_id,
        expected_revision=_head_revision(project_key, case_id),
        generation=generation,
        by=by,
    )
