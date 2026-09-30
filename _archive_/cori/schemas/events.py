"""The event record and the names of every event type. Seams §1.8 and §4;
tech stack §3, §10.

`EventType` is a copy of the seams §4 table, no more and no fewer;
`tests/test_events_schema.py` parses that table and holds the copy to it.
A name is never removed from the Literal: a retired type keeps its line
with a comment, because a row of that type still exists and `read` must
still parse it (old events are never rewritten).

Payloads stay `dict[str, Any]`. §4 promises the ids each payload carries and
nothing else; the emitting plan validates its own model and hands `append`
the result of `model_dump(mode="json")`. A per-type payload model arrives
with the first type that reaches `schema_version` 2.
"""

from datetime import datetime
from typing import Any, Literal, get_args

from pydantic import Field

from schemas.ids import SpaceId
from schemas.space import Strict

EventType = Literal[
    # tree
    "objective.opened",
    "objective.contract_revised",
    "objective.approved",
    "objective.state_changed",
    "brief.issued",
    "brief.stopped",
    "brief.stop_confirmed",
    "brief.failed",
    "report.landed",
    "assumption.challenged",
    "budget.overrun",
    # spaces
    "inbound.routed",
    "inbound.unassigned",
    "space.destroyed",
    # worker
    "question.raised",
    "snapshot.taken",
    # supervisor
    "turn.started",
    "turn.rendered",
    "turn.completed",
    "message.received",
    "message.sent",
    "correction.recorded",
    "question.answered",
    "scribe.fired",
    # surface
    "session.opened",
    "conversation.opened",
    "space.switched",
    "card.issued",
    "card.expired",
    "approval.minted",
    "approval.consumed",
    # memory
    "episode.written",
    "belief.proposed",
    # verifier
    "verification.sampled",
    "checks.recorded",
    "verdict.recorded",
]

EVENT_TYPES: frozenset[str] = frozenset(get_args(EventType))

# Every type is at version 1 at M0. A shape change bumps the type's entry
# here and registers an upcaster in kernel/events.py (tech stack §10).
CURRENT_VERSION: dict[EventType, int] = {t: 1 for t in get_args(EventType)}


class Event(Strict, frozen=True):
    """One row of the `events` table that migration 0001 created."""

    id: int
    space_id: SpaceId
    type: EventType
    schema_version: int = Field(ge=1)
    occurred_at: datetime
    payload: dict[str, Any]
