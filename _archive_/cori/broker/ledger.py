"""The only writer of `effect_ledger`. Plan 07 task 3; seams §5.3.

One row per event and no status column, so a row is never updated: an
effect's state is a fold over its rows, `intent` then at most one of
`outcome` or `reconciled`, or exactly one `refused`. The unique constraint on
`(effect_id, event)` is what makes the fold total rather than hopeful.

Every writer takes the row's fields rather than an `Action`, because the
`read` actions the kernel itself performs have no Brief and their rows carry
`objective_id`, `brief_id`, and `generation` null (seams §5.3, amendment B).
`broker.perform` unpacks its `Action` into these fields; `read_recent` and
`fetch_body` pass the `ConnectorRead` dict with the two ids None.

Nothing here opens or commits a transaction. The caller decides what lands
together, because the protocol of spike 02 turns on the intent being
committed before the action starts.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from schemas.effect import EffectOutcome, canonical_sha256
from schemas.ids import BriefId, EffectId, ObjectiveId, SpaceId, new_id

__all__ = [
    "closed_for_key",
    "dangling",
    "intent",
    "open_intent_for_key",
    "outcome",
    "reconciled",
    "refused",
]

TABLE = "effect_ledger"

DESCRIPTIVE = (
    "effect_id",
    "space_id",
    "objective_id",
    "brief_id",
    "generation",
    "action_type",
    "effect_class",
    "idempotency_key",
    "target",
    "payload_sha256",
    "payload",
)

CLOSES = ("outcome", "reconciled")


async def _insert(conn, row: dict[str, Any]) -> None:
    columns = ", ".join(row)
    holders = ", ".join(["%s"] * len(row))
    await conn.execute(
        f"INSERT INTO {TABLE} ({columns}) VALUES ({holders})", tuple(row.values())
    )


def _descriptive(
    *,
    effect_id: EffectId,
    action_type: str,
    effect_class: str,
    space: SpaceId,
    target: str,
    idempotency_key: str,
    payload: dict,
    objective_id: ObjectiveId | None,
    brief_id: BriefId | None,
    generation: int | None,
) -> dict[str, Any]:
    return {
        "effect_id": effect_id,
        "space_id": space,
        "objective_id": objective_id,
        "brief_id": brief_id,
        "generation": generation,
        "action_type": action_type,
        "effect_class": effect_class,
        "idempotency_key": idempotency_key,
        "target": target,
        "payload_sha256": canonical_sha256(payload),
        "payload": Jsonb(payload),
    }


# ---------------------------------------------------------------------------
# The writers


async def intent(
    conn,
    *,
    action_type: str,
    effect_class: str,
    space: SpaceId,
    target: str,
    idempotency_key: str,
    payload: dict,
    objective_id: ObjectiveId | None = None,
    brief_id: BriefId | None = None,
    generation: int | None = None,
    approval_id: str | None = None,
    effect_id: EffectId | None = None,
) -> EffectId:
    """The row that says an effect is about to happen. Committed before the
    action starts, so a kill between the two leaves it dangling and findable
    rather than lost (spike 02)."""
    effect_id = effect_id or new_id()
    row = _descriptive(
        effect_id=effect_id,
        action_type=action_type,
        effect_class=effect_class,
        space=space,
        target=target,
        idempotency_key=idempotency_key,
        payload=payload,
        objective_id=objective_id,
        brief_id=brief_id,
        generation=generation,
    )
    await _insert(conn, {**row, "event": "intent", "approval_id": approval_id})
    return effect_id


async def outcome(
    conn,
    *,
    effect_id: EffectId,
    kind: str,
    result: dict | None = None,
    error: str | None = None,
    action_type: str,
    effect_class: str,
    space: SpaceId,
    target: str,
    idempotency_key: str,
    payload: dict,
    objective_id: ObjectiveId | None = None,
    brief_id: BriefId | None = None,
    generation: int | None = None,
    approval_id: str | None = None,
) -> EffectOutcome:
    """What the action did. `done`, `failed` (the target confirms the key is
    absent), or `unknown` (its state there is in doubt)."""
    return await _close(
        conn,
        event="outcome",
        effect_id=effect_id,
        kind=kind,
        result=result,
        error=error,
        action_type=action_type,
        effect_class=effect_class,
        space=space,
        target=target,
        idempotency_key=idempotency_key,
        payload=payload,
        objective_id=objective_id,
        brief_id=brief_id,
        generation=generation,
        approval_id=approval_id,
    )


async def reconciled(
    conn,
    *,
    effect_id: EffectId,
    kind: str,
    result: dict | None = None,
    error: str | None = None,
    action_type: str,
    effect_class: str,
    space: SpaceId,
    target: str,
    idempotency_key: str,
    payload: dict,
    objective_id: ObjectiveId | None = None,
    brief_id: BriefId | None = None,
    generation: int | None = None,
    approval_id: str | None = None,
) -> EffectOutcome:
    """What the target said about a dangling intent at restart. `recovered`
    (the target had the key), `done` (the re-run succeeded), or `unknown`."""
    return await _close(
        conn,
        event="reconciled",
        effect_id=effect_id,
        kind=kind,
        result=result,
        error=error,
        action_type=action_type,
        effect_class=effect_class,
        space=space,
        target=target,
        idempotency_key=idempotency_key,
        payload=payload,
        objective_id=objective_id,
        brief_id=brief_id,
        generation=generation,
        approval_id=approval_id,
    )


async def refused(
    conn,
    *,
    error: str,
    action_type: str,
    effect_class: str,
    space: SpaceId,
    target: str,
    idempotency_key: str,
    payload: dict,
    objective_id: ObjectiveId | None = None,
    brief_id: BriefId | None = None,
    generation: int | None = None,
    approval_id: str | None = None,
) -> EffectOutcome:
    """A refusal before the target was touched. It carries a fresh effect id
    of its own, so the Verifier's slice and the person's audit read one table
    with one shape (plan 07, `perform`)."""
    effect_id = new_id()
    row = _descriptive(
        effect_id=effect_id,
        action_type=action_type,
        effect_class=effect_class,
        space=space,
        target=target,
        idempotency_key=idempotency_key,
        payload=payload,
        objective_id=objective_id,
        brief_id=brief_id,
        generation=generation,
    )
    await _insert(
        conn,
        {
            **row,
            "event": "refused",
            "outcome_kind": "refused",
            "error": error,
            "approval_id": approval_id,
        },
    )
    return EffectOutcome(effect_id=effect_id, kind="refused", error=error)


async def _close(
    conn,
    *,
    event: str,
    effect_id: EffectId,
    kind: str,
    result: dict | None,
    error: str | None,
    approval_id: str | None,
    **descriptive,
) -> EffectOutcome:
    row = _descriptive(effect_id=effect_id, **descriptive)
    await _insert(
        conn,
        {
            **row,
            "event": event,
            "outcome_kind": kind,
            "result": Jsonb(result) if result is not None else None,
            "error": error,
            "approval_id": approval_id,
        },
    )
    return EffectOutcome(
        effect_id=effect_id, kind=kind, result=result or {}, error=error
    )


# ---------------------------------------------------------------------------
# The readers


async def dangling(conn) -> list[dict[str, Any]]:
    """Every `intent` with no closing row, oldest first. What
    `reconcile_dangling` walks at process start."""
    rows = await (
        await conn.execute(
            f"SELECT {', '.join(DESCRIPTIVE)}, approval_id FROM {TABLE} i "
            "WHERE i.event = 'intent' AND NOT EXISTS ("
            f"  SELECT 1 FROM {TABLE} c WHERE c.effect_id = i.effect_id "
            "    AND c.event IN ('outcome', 'reconciled')) "
            "ORDER BY i.id"
        )
    ).fetchall()
    names = (*DESCRIPTIVE, "approval_id")
    return [dict(zip(names, row)) for row in rows]


async def closed_for_key(conn, idempotency_key: str) -> EffectOutcome | None:
    """The last `done` or `recovered` close for this key, or None.

    A repeated request for the same effect is the same effect: `perform`
    returns this without writing a row. A `failed` or `unknown` close is not
    one, because neither says the effect landed.
    """
    row = await (
        await conn.execute(
            f"SELECT effect_id, outcome_kind, result, error FROM {TABLE} "
            "WHERE idempotency_key = %s AND event IN ('outcome', 'reconciled') "
            "AND outcome_kind IN ('done', 'recovered') ORDER BY id DESC LIMIT 1",
            (idempotency_key,),
        )
    ).fetchone()
    if row is None:
        return None
    effect_id, kind, result, error = row
    return EffectOutcome(
        effect_id=effect_id, kind=kind, result=result or {}, error=error
    )


async def open_intent_for_key(conn, idempotency_key: str) -> EffectId | None:
    """The effect id of an intent for this key that nothing has closed.

    A second request while one is open is refused with `in_flight` (seams
    §5.3): the first is either running in this process or waiting for the
    reconcile pass, and either way a second target call would be the
    duplicate spike 02 exists to prevent.
    """
    row = await (
        await conn.execute(
            f"SELECT i.effect_id FROM {TABLE} i "
            "WHERE i.event = 'intent' AND i.idempotency_key = %s AND NOT EXISTS ("
            f"  SELECT 1 FROM {TABLE} c WHERE c.effect_id = i.effect_id "
            "    AND c.event IN ('outcome', 'reconciled')) "
            "ORDER BY i.id DESC LIMIT 1",
            (idempotency_key,),
        )
    ).fetchone()
    return row[0] if row else None
