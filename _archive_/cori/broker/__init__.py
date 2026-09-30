"""The action broker: the one door every effect leaves the system through.
Architecture §4, §5, §8; tech stack §7; spike 02.

The protocol is three steps and never two: an `intent` row committed, the
action performed outside the sandbox with a credential the sandbox never
holds, and a closing row. The intent is committed first so that a kill
between the intent and the outcome leaves a record of an effect that may or
may not have landed, which is the only state a restart can reconcile. Spike
02 measured it: reconcile by key duplicated nothing and lost nothing in 49 of
49 killed runs, where at-least-once duplicated in 22 per cent.

`perform` is the door for an action a Brief requested. `reconcile_dangling`
is the door a restart comes through. Nothing else writes `effect_ledger`, and
nothing here decides what an effect is worth asking about: an `unknown` is
returned to the caller, and the card for it is `kernel/__main__.py`'s
(seams Round two).

`space_for`, `credential_for`, and `check_generation` are bound here as
module-level names so the property tests can replace them per example with a
generated manifest, a generated credential table, and a stub generation.
"""

from __future__ import annotations

from typing import Any

from broker import actions, ledger
from broker.credentials import credential_for
from broker.errors import EffectRefused
from broker.manifest import space_for
from kernel.tree import StaleGeneration, check_generation
from schemas.brief import BriefToken
from schemas.effect import Action, EffectOutcome
from schemas.space import EFFECT_RANK

__all__ = [
    "EffectRefused",
    "check_generation",
    "credential_for",
    "perform",
    "reconcile_dangling",
    "space_for",
]

LOCK_NAMESPACE = "effect:"
SHIPPED_CLASSES = ("read", "propose")


def _fields(action: Action, token: BriefToken | None) -> dict[str, Any]:
    """The descriptive columns of every row this effect writes.

    `target()` is called once here rather than at each row, so an intent and
    its close can never disagree about where the effect went.
    """
    return {
        "action_type": action.action_type,
        "effect_class": action.effect_class,
        "space": action.space,
        "target": action.target(),
        "idempotency_key": action.idempotency_key,
        "payload": action.model_dump(mode="json"),
        "objective_id": action.objective_id,
        "brief_id": action.brief_id,
        "generation": token.generation if token is not None else None,
    }


async def _refuse(conn, fields: dict[str, Any], reason: str) -> EffectOutcome:
    """One `refused` row and nothing else. Every refusal in `perform` ends
    here, so a refusal always leaves exactly one row and never a target
    call."""
    async with conn.transaction():
        return await ledger.refused(conn, error=reason, **fields)


async def _take_the_connection(conn) -> None:
    """`perform` opens its own transactions, so it refuses to run inside
    someone else's.

    The intent has to be committed before the action starts (spike 02), and
    inside an enclosing transaction that commit would be a savepoint: the
    intent would vanish with the caller's rollback and a kill would leave no
    record at all. This is a programming error rather than a refusal, so it
    raises and writes no row.

    Decided in build: an idle connection that is not in autocommit is put
    into it here, because `conn.transaction()` on an implicit transaction is
    a savepoint for the same reason. `kernel/api.py::request_effect` already
    hands over an autocommit connection; this makes the rest of the callers
    agree with it rather than fail obscurely.
    """
    status = conn.info.transaction_status
    if status.name != "IDLE":
        raise RuntimeError(
            f"perform needs a connection with no open transaction, got {status.name}: "
            "the intent row must commit before the action runs"
        )
    if not conn.autocommit:
        await conn.set_autocommit(True)


async def perform(
    conn,
    action: Action,
    approval: Any | None = None,
    *,
    token: BriefToken,
) -> EffectOutcome:
    """Perform one typed action and record what happened. Seams §3.10.

    `approval` is an `ApprovalRecord` (seams §1.12) or None. At M0 it is
    always None: every `act` is refused as unshipped, and the proposals that
    leave the space arrive at M2 with the card binding that reads it.

    Every refusal below writes one `refused` row and returns
    `EffectOutcome(kind="refused")`; nothing after a refusal touches the
    target.
    """
    await _take_the_connection(conn)
    fields = _fields(action, token)

    # 1. The Brief this action claims, and the fence on it.
    if action.objective_id is None or action.brief_id is None:
        return await _refuse(conn, fields, "worker_action_without_brief")
    try:
        await check_generation(conn, token)
    except StaleGeneration as exc:
        return await _refuse(conn, fields, f"stale_generation: {exc}")
    if token.brief_id != action.brief_id:
        return await _refuse(conn, fields, "brief_mismatch")

    # 2. What is shipped at M0.
    module = actions.MODULES.get(action.action_type)
    if action.effect_class not in SHIPPED_CLASSES or module is None:
        return await _refuse(conn, fields, f"class_not_shipped: {action.action_type}")

    # 3. The space's ceiling and its target list, read from the manifest now.
    try:
        space = space_for(action.space)
    except EffectRefused as exc:
        return await _refuse(conn, fields, str(exc))
    if EFFECT_RANK[action.effect_class] > EFFECT_RANK[space.max_effect_class]:
        return await _refuse(conn, fields, "above_space_ceiling")
    if fields["target"] not in space.allowed_targets:
        return await _refuse(conn, fields, "target_not_allowed")

    # 4. The credential for this space on this target, by the pair.
    try:
        credential = credential_for(space.id, fields["target"])
    except EffectRefused:
        return await _refuse(conn, fields, "no_credential")

    # 5. What the module can refuse without touching the target.
    try:
        module.check(action)
    except EffectRefused as exc:
        return await _refuse(conn, fields, str(exc))

    # 6. The fence and the intent in one committed transaction.
    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (LOCK_NAMESPACE + action.idempotency_key,),
        )
        try:
            await check_generation(conn, token)
        except StaleGeneration as exc:
            return await ledger.refused(
                conn, error=f"stale_generation: {exc}", **fields
            )
        closed = await ledger.closed_for_key(conn, action.idempotency_key)
        if closed is not None:
            return closed
        if await ledger.open_intent_for_key(conn, action.idempotency_key) is not None:
            return await ledger.refused(conn, error="in_flight", **fields)
        effect_id = await ledger.intent(conn, **fields)

    # 7 and 8. The action, and what the target says if it raised.
    try:
        result = await module.run(conn, action, credential)
    except Exception as exc:
        return await _close_after_failure(
            conn, module, action, credential, fields, effect_id, exc
        )
    async with conn.transaction():
        return await ledger.outcome(
            conn, effect_id=effect_id, kind="done", result=result, **fields
        )


async def _close_after_failure(
    conn,
    module,
    action: Action,
    credential,
    fields: dict[str, Any],
    effect_id: str,
    exc: Exception,
) -> EffectOutcome:
    """The action raised, so ask the target what it holds before deciding.

    `absent` is `failed`: the target answered and the key is not there, so
    the effect did not land and nobody needs to be asked about it (seams
    §1.10). `present` is `recovered` and closes as `reconciled`, because the
    effect did land and the raise was on the way back. `differs` and
    `unreachable` are `unknown`, the one kind that earns a card, because the
    state on the target is in doubt.
    """
    error = str(exc)
    try:
        state = await module.query(conn, action, credential)
    except Exception as asked:
        state = "unreachable"
        error = f"{error}; the target could not be asked: {asked}"
    async with conn.transaction():
        if state == "present":
            return await ledger.reconciled(
                conn, effect_id=effect_id, kind="recovered", error=error, **fields
            )
        kind = "failed" if state == "absent" else "unknown"
        return await ledger.outcome(
            conn, effect_id=effect_id, kind=kind, error=error, **fields
        )


# ---------------------------------------------------------------------------
# The restart door


def _close_fields(row: dict[str, Any]) -> dict[str, Any]:
    """A dangling intent row as the arguments its closing row needs.

    The close is written from the intent's own columns rather than from the
    rebuilt action, so an intent and its close can never disagree about the
    space, the target, or the Brief that asked.
    """
    return {
        "action_type": row["action_type"],
        "effect_class": row["effect_class"],
        "space": row["space_id"],
        "target": row["target"],
        "idempotency_key": row["idempotency_key"],
        "payload": row["payload"],
        "objective_id": row["objective_id"],
        "brief_id": row["brief_id"],
        "generation": row["generation"],
        "approval_id": row["approval_id"],
    }


async def reconcile_dangling(conn) -> list[EffectOutcome]:
    """Close every intent a kill left open, by asking the target. Seams §3.10.

    Runs once at process start, before any replacement Brief runs (tech stack
    §4). The target is the only witness of what happened between the intent
    and the kill, so it is asked by idempotency key before anything is run
    again:

    | The target says | Row | Kind |
    |---|---|---|
    | `present` | `reconciled` | `recovered` |
    | `absent` | re-run, then `reconciled` | `done`, or `outcome/failed` |
    | `differs` | `reconciled` | `unknown` |
    | `unreachable` | `reconciled` | `unknown` |

    `differs` is not a re-run: for a push, running again onto a branch at
    another SHA is the force push blind-spot finding 15 exists to prevent,
    and the person is the right reader of that state.

    A dangling `read` takes the `absent` row by construction, because
    `gmail.query` answers `absent` always: the re-run fetches again and
    closes with a fresh `result`, so no read is ever called recovered without
    its data.

    The returned list is the caller's to act on. `kernel/__main__.py` issues
    one `unknown_outcome` card per `unknown` (seams Round two); the broker
    never issues a card.
    """
    await _take_the_connection(conn)
    closed: list[EffectOutcome] = []
    for row in await ledger.dangling(conn):
        closed.append(await _reconcile_one(conn, row))
    return closed


async def _reconcile_one(conn, row: dict[str, Any]) -> EffectOutcome:
    fields = _close_fields(row)
    effect_id = row["effect_id"]
    module = actions.MODULES.get(row["action_type"])
    if module is None:
        return await _cannot_ask(
            conn,
            effect_id,
            fields,
            f"no module performs {row['action_type']!r}, so the target cannot be asked",
        )
    try:
        action = module.model.model_validate(row["payload"])
        credential = credential_for(row["space_id"], row["target"])
    except Exception as exc:
        return await _cannot_ask(conn, effect_id, fields, str(exc))

    try:
        state = await module.query(conn, action, credential)
    except Exception as exc:
        return await _cannot_ask(
            conn, effect_id, fields, f"the target could not be asked: {exc}"
        )

    if state == "present":
        async with conn.transaction():
            return await ledger.reconciled(
                conn, effect_id=effect_id, kind="recovered", **fields
            )
    if state != "absent":
        return await _cannot_ask(
            conn,
            effect_id,
            fields,
            "the target holds this key in another state",
            state=state,
        )
    try:
        result = await module.run(conn, action, credential)
    except Exception as exc:
        # The target answered that the key is absent, so the effect did not
        # land and the re-run did not either: `failed`, a report and no card.
        async with conn.transaction():
            return await ledger.outcome(
                conn, effect_id=effect_id, kind="failed", error=str(exc), **fields
            )
    async with conn.transaction():
        return await ledger.reconciled(
            conn, effect_id=effect_id, kind="done", result=result, **fields
        )


async def _cannot_ask(
    conn,
    effect_id: str,
    fields: dict[str, Any],
    error: str,
    *,
    state: str = "unreachable",
) -> EffectOutcome:
    """`unknown`: the effect's state on the target is in doubt, which is the
    one kind that earns a card. `result` carries the target's answer, so the
    card can say what was asked and what came back (surface amendment 12)."""
    async with conn.transaction():
        return await ledger.reconciled(
            conn,
            effect_id=effect_id,
            kind="unknown",
            result={"target_state": state},
            error=error,
            **fields,
        )
