"""The dispatch half of the lane: admits a proposed action, materializes it
through the create-or-bind seam, and activates it once a worker is alive.

``tick()`` is called from the sync ``improvement-controller-tick`` reflection
(``reflections/improvement_controller_tick.py``) and does its own
``asyncio.run`` around the one async call it needs (``_push_agent_session``).
Per-case exception isolation: one case's failure never stops the tick from
reaching the rest (Risk 2's mitigation for this module).

A case with an already-``materialized`` intent is re-activated on every
tick until it moves past ``materialized`` (Race 7): a crash between the
create-or-bind call and the activation writes must not strand the session
at ``admitted`` forever, so the retry path and the fresh-dispatch path share
one :func:`_activate` after their own admit/materialize step.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

from tools.improvement_control import keys
from tools.improvement_control.intents import (
    DispatchIntent,
    admit,
    list_intents,
    on_session_terminal,
    record_materialized,
    record_running,
)
from tools.improvement_control.journal import read_head

logger = logging.getLogger(__name__)

#: Action types the adapter will admit. Anything else is left on the head
#: (the CLI's own `propose` validation is the first gate; this is the
#: adapter's own allow-list, kept narrow on purpose).
ALLOWED_ACTION_TYPES: frozenset[str] = frozenset({"investigate", "experiment", "release"})


@dataclass
class TickResult:
    admitted: list[str] = field(default_factory=list)
    materialized: list[str] = field(default_factory=list)
    activated: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # case_id -> reason
    errors: dict[str, str] = field(default_factory=dict)  # case_id -> error class name


def _control_redis():
    from utils.redis_client import text_redis

    return text_redis()


def _project_root() -> str:
    """The repo checkout this process is running from, the same pattern
    ``agent/reflection_scheduler.py:781`` uses for its own dispatch. Tech
    debt fix (#3315 review): the adapter previously passed ``working_dir="."``,
    which the worker resolves against its own cwd rather than the checkout
    the case was proposed from."""
    from pathlib import Path

    return str(Path(__file__).resolve().parent.parent.parent)


def _open_case_rows(project_key: str):
    """One filter call per state (Popoto's IndexedField filter is exact-match,
    not IN) -- the same pattern ``ui/data/improvement.py``'s goals partial uses."""
    from models.improvement_case import OPEN_CASE_STATES, ImprovementCase

    rows = []
    for state in OPEN_CASE_STATES:
        rows.extend(ImprovementCase.query.filter(project_key=project_key, state=state))
    return rows


def _unadmitted_proposal(project_key: str, case_id: str) -> tuple[str, str, str] | None:
    """The case's last journal event, if it is an un-admitted `action_proposed`.

    Returns ``(action_id, action_type, request_digest)`` or ``None``.
    "Un-admitted" means no intent row exists yet for that action id.
    """
    tail = _control_redis().lrange(keys.journal_key(project_key, case_id), -1, -1)
    if not tail:
        return None
    entry = json.loads(tail[0])
    if entry.get("event") != "action_proposed":
        return None
    action_id = entry.get("action_id")
    if not action_id:
        return None
    existing = _control_redis().hget(keys.intent_key(project_key, case_id, action_id), "state")
    if existing is not None:
        return None  # already admitted (or further along) on a prior tick
    # Tech debt fix (#3315 review): `action_type` is now carried on the
    # journal entry (`journal.transition`'s own fix); the fallback stays for
    # any entry written before that change landed.
    action_type = entry.get("action_type") or "investigate"
    request_digest = entry.get("payload_digest") or ""
    return action_id, action_type, request_digest


def _default_push():
    from agent.agent_session_queue import _push_agent_session

    return _push_agent_session


def tick(project_key: str = "valor", *, lease=None, push=None, now=None) -> TickResult:
    """One controller pass: admit, materialize, activate, per open case."""
    del now  # reserved for a future deterministic-clock test seam; unused today
    from config.settings import settings

    if lease is None:
        from tools.improvement_control.lease import default_lease

        lease = default_lease()
    if push is None:
        push = _default_push()

    result = TickResult()
    for case in _open_case_rows(project_key):
        case_id = case.id
        try:
            _tick_one_case(project_key, case, lease, push, settings, result)
        except Exception as e:
            logger.warning(
                "[improvement-controller] tick failed for case=%s: %s: %s",
                case_id,
                type(e).__name__,
                e,
            )
            result.errors[case_id] = type(e).__name__
    return result


def _tick_one_case(project_key, case, lease, push, settings, result: TickResult) -> None:
    case_id = case.id
    case_intents = list_intents(project_key, case_id)

    # A case with any reconciliation_required intent is skipped, reason
    # journaled once by the caller of mark_reconciliation_required, not here.
    if any(i.state == "reconciliation_required" for i in case_intents):
        result.skipped[case_id] = "reconciliation_required"
        return

    lease_key = f"improve:{project_key}:{case_id}:lease"
    generation = lease.acquire(lease_key, ttl=settings.improvement.lease_ttl_seconds)
    if generation is None:
        result.skipped[case_id] = "lease_busy"
        return
    try:
        # Retry path first (Race 7): a `materialized` intent from a prior
        # tick that never finished activating gets another attempt before
        # this tick looks for anything new to admit.
        stalled = next((i for i in case_intents if i.state == "materialized"), None)
        if stalled is not None:
            _activate(project_key, case_id, stalled, generation, result)
            return
        _admit_and_dispatch(project_key, case, generation, push, settings, result)
    finally:
        lease.release(lease_key, generation)


def _admit_and_dispatch(project_key, case, generation, push, settings, result: TickResult) -> None:
    case_id = case.id
    proposal = _unadmitted_proposal(project_key, case_id)
    if proposal is None:
        return
    action_id, action_type, request_digest = proposal
    if action_type not in ALLOWED_ACTION_TYPES:
        result.skipped[case_id] = f"action_type_not_allowed:{action_type}"
        return

    charter_digest = getattr(case, "charter_digest", None) or ""
    # Tech debt fix (#3315 review): Data Flow step 6 lists a "charter digest
    # pinned" check among the adapter's admission checks; only the CLI's own
    # propose-time check ran. Permissive when either side is absent (a case
    # with no `charter_digest` recorded, or a project with nothing pinned) so
    # this stays a real drift check rather than a hard dependency every
    # caller -- including every existing unit test's bare `new_case()` -- must
    # now satisfy; `propose` already refuses a proposal with no matching
    # pinned charter before it is ever journaled.
    if charter_digest:
        from models.improvement_charter import ImprovementCharter

        pinned = ImprovementCharter.pinned(project_key)
        if pinned is not None and charter_digest != pinned.digest:
            result.skipped[case_id] = "charter_digest_stale"
            return

    head = read_head(project_key, case_id)
    expected_revision = head.revision if head is not None else 0
    admit_result = admit(
        project_key,
        case_id,
        action_id,
        expected_revision=expected_revision,
        generation=generation,
        action_type=action_type,
        request_digest=request_digest,
        charter_digest=charter_digest,
        max_concurrent=settings.improvement.max_concurrent_research_sessions,
    )
    if not admit_result.accepted:
        result.skipped[case_id] = admit_result.reason
        return
    result.admitted.append(case_id)

    idempotency_key = f"improve:{project_key}:{case_id}:{action_id}"
    # Tech debt fix (#3315 review): the message previously named no skill and
    # no brief reference (Data Flow step 9). `request_digest` (the proposal's
    # own payload digest) stands in for lane 5's not-yet-built `brief_ref`
    # until that lane exists -- it is the only concrete "what to look at"
    # reference this lane has today.
    message_text = f"/improve-research case={case_id} action={action_id} type={action_type}"
    if request_digest:
        message_text += f" brief_ref={request_digest}"
    depth, agent_session_id = asyncio.run(
        push(
            project_key=project_key,
            session_id=str(uuid.uuid4()),
            working_dir=_project_root(),
            message_text=message_text,
            sender_name="improvement-controller",
            chat_id="0",
            telegram_message_id=0,
            session_type="eng",
            idempotency_key=idempotency_key,
            status="admitted",
            extra_context_overrides={
                "research_case_id": case_id,
                "experiment_id": action_id,
                "action_id": action_id,
                "idempotency_key": idempotency_key,
            },
        )
    )
    _ = depth  # queue depth, not needed by the adapter
    materialize_result = record_materialized(
        project_key,
        case_id,
        action_id,
        expected_revision=admit_result.revision,
        generation=generation,
        agent_session_id=agent_session_id,
    )
    if not materialize_result.accepted:
        result.skipped[case_id] = materialize_result.reason
        return
    result.materialized.append(case_id)

    stalled = DispatchIntent(
        action_id=action_id,
        state="materialized",
        action_type=action_type,
        agent_session_id=agent_session_id,
        attempts=0,
        stale_sweeps=0,
        generation=generation,
        created_ts=0.0,
        updated_ts=0.0,
        session_terminal_at=None,
        result_digest=None,
        reason="",
    )
    _activate(
        project_key, case_id, stalled, generation, result, revision=materialize_result.revision
    )


def _activate(
    project_key,
    case_id,
    intent: DispatchIntent,
    generation,
    result: TickResult,
    *,
    revision: int | None = None,
) -> None:
    """Data Flow step 8 / Race 7: re-read the row fresh and branch on what a
    retry actually finds, never on what this tick assumed at its start."""
    from agent.session_health import any_worker_alive
    from models.session_lifecycle import (
        TERMINAL_STATUSES,
        get_authoritative_session,
        transition_status,
    )

    action_id = intent.action_id
    agent_session_id = intent.agent_session_id
    if revision is None:
        head = read_head(project_key, case_id)
        revision = head.revision if head is not None else 0

    if not any_worker_alive():
        result.skipped[case_id] = "no_live_worker"
        return

    fresh = get_authoritative_session(agent_session_id) if agent_session_id else None
    if fresh is None:
        result.skipped[case_id] = "session_row_missing"
        return

    if fresh.status == "admitted":
        transition_status(fresh, "pending", reason="improvement-controller activate")
        _publish(fresh)
        record_running(
            project_key, case_id, action_id, expected_revision=revision, generation=generation
        )
        result.activated.append(case_id)
    elif fresh.status == "pending":
        _publish(fresh)
        record_running(
            project_key, case_id, action_id, expected_revision=revision, generation=generation
        )
        result.activated.append(case_id)
    elif fresh.status in TERMINAL_STATUSES:
        record_running(
            project_key, case_id, action_id, expected_revision=revision, generation=generation
        )
        on_session_terminal(fresh, fresh.status)
    else:
        record_running(
            project_key, case_id, action_id, expected_revision=revision, generation=generation
        )


def _publish(session) -> None:
    from agent.agent_session_queue import publish_session_notify

    publish_session_notify(session)
