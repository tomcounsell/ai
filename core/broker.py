"""The broker: every effect on the world passes through here.

An effect's class is a fact about its action type, declared by the performer
that carries it out, never a field the requester fills:

- `read`: no effect.
- `propose`: reversible; a draft, a branch, a file in the task's workspace.
- `act`: irreversible or money; a send, a merge, a payment.

`request` refuses anything above the task's ceiling, returns the earlier
outcome for a repeated request (the idempotency key is derived from the
action, never supplied), performs `read` and `propose` at once, and holds
every `act` pending. A held effect leaves only through `release`, which
needs an `approval.granted` row from Tom bound to that effect's digest, and
consumes it: one tap, one effect.

Adding governance (a check, gate, hook, validator, review round, or approval
step) is `act` whatever the performer declares, and is refused outright when
the task's Brief carries no `governance_grant`.

Performing follows intent, then outcome: the intent row commits before the
performer runs, so a kill between the two leaves a findable dangling intent,
never a silent effect. A performer can `lookup` its idempotency key on the
target, which is how a dangling intent is reconciled.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from core import ledger, tasks
from core.tasks import EFFECT_RANK


class Performer(Protocol):
    action_type: str
    effect_class: str

    def perform(self, action: Action, key: str) -> dict[str, Any]: ...

    def lookup(self, action: Action, key: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class Action:
    action_type: str
    target: str
    payload: dict[str, Any] = field(default_factory=dict)
    adds_governance: bool = False

    def key(self) -> str:
        return f"{self.action_type}:{self.target}:{ledger.digest(self.payload)[:16]}"

    def describe(self, effect_class: str) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "effect_class": effect_class,
            "target": self.target,
            "payload": self.payload,
            "adds_governance": self.adds_governance,
            "idempotency_key": self.key(),
            "payload_sha256": ledger.digest(self.payload),
        }


@dataclass(frozen=True)
class Outcome:
    effect_id: str
    kind: str  # done, failed, pending, refused
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class NotApproved(RuntimeError):
    pass


PERFORMERS: dict[str, Performer] = {}


def register(performer: Performer) -> None:
    if performer.effect_class not in EFFECT_RANK:
        raise ValueError(f"unknown effect class {performer.effect_class!r}")
    PERFORMERS[performer.action_type] = performer


async def request(conn, task_id: str, action: Action) -> Outcome:
    performer = PERFORMERS.get(action.action_type)
    effect_class = "act" if action.adds_governance else getattr(performer, "effect_class", "act")
    described = action.describe(effect_class)
    effect_id = ledger.new_id()
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        brief = await tasks.brief(conn, task_id)
        prior = await _prior(conn, task_id, described["idempotency_key"])
        if prior is not None:
            return prior
        reason = None
        if performer is None:
            reason = f"no performer for {action.action_type}"
        elif await tasks.is_stopped(conn, task_id):
            reason = "task stopped"
        elif EFFECT_RANK[effect_class] > EFFECT_RANK[brief.max_effect_class]:
            reason = f"{effect_class} is above the task's ceiling {brief.max_effect_class}"
        elif action.adds_governance and brief.governance_grant is None:
            reason = "adds governance and the Brief carries no governance_grant"
        if reason is not None:
            await ledger.append(
                conn, task_id, "effect.refused", {"effect_id": effect_id, **described, "reason": reason}
            )
            return Outcome(effect_id, "refused", error=reason)
        if effect_class == "act":
            await ledger.append(conn, task_id, "effect.held", {"effect_id": effect_id, **described})
            return Outcome(effect_id, "pending")
    return await _perform(conn, task_id, effect_id, action, described, approval_id=None)


async def approve(
    conn,
    effect_id: str,
    *,
    note: str,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> str:
    """Tom's tap. Binds to the held effect's digest; `note` is his literal
    message. `provenance` says who tapped (`by`), through what (`via`),
    when, and whether someone stood in for Tom (`role_played`), the shape
    answers and feedback carry."""
    async with conn.transaction():
        held = await _held(conn, effect_id)
        approval_id = ledger.new_id()
        await ledger.append(
            conn,
            held["task_id"],
            "approval.granted",
            {
                "approval_id": approval_id,
                "effect_id": effect_id,
                "payload_sha256": held["payload"]["payload_sha256"],
                "note": note,
                "provenance": ledger.provenance(by, via, role_played),
            },
        )
    return approval_id


async def release(conn, effect_id: str) -> Outcome:
    """Perform a held `act` effect that Tom approved. Raises `NotApproved`
    when no unused approval matches it."""
    async with conn.transaction():
        held = await _held(conn, effect_id)
        task_id, described = held["task_id"], held["payload"]
        await ledger.lock(conn, f"task:{task_id}")
        prior = await _prior(conn, task_id, described["idempotency_key"])
        if prior is not None and prior.kind != "pending":
            return prior
        if await tasks.is_stopped(conn, task_id):
            raise tasks.TaskStopped(task_id)
        row = await (
            await conn.execute(
                "SELECT a.payload->>'approval_id' FROM events a "
                "WHERE a.type = 'approval.granted' AND a.payload->>'effect_id' = %s "
                "AND a.payload->>'payload_sha256' = %s AND NOT EXISTS ("
                "  SELECT 1 FROM events i WHERE i.type = 'effect.intent' "
                "  AND i.payload->>'approval_id' = a.payload->>'approval_id') "
                "ORDER BY a.id LIMIT 1",
                (effect_id, described["payload_sha256"]),
            )
        ).fetchone()
    if row is None:
        raise NotApproved(f"effect {effect_id} has no approval from Tom")
    action = Action(
        described["action_type"],
        described["target"],
        described["payload"],
        described["adds_governance"],
    )
    return await _perform(conn, task_id, effect_id, action, described, approval_id=row[0])


async def pending(conn) -> list[dict[str, Any]]:
    """Held effects with no intent yet: what waits for Tom."""
    rows = await (
        await conn.execute(
            "SELECT h.task_id, h.payload FROM events h WHERE h.type = 'effect.held' "
            "AND NOT EXISTS (SELECT 1 FROM events i WHERE i.type = 'effect.intent' "
            "AND i.payload->>'effect_id' = h.payload->>'effect_id') ORDER BY h.id"
        )
    ).fetchall()
    return [{"task_id": t, **p} for t, p in rows]


async def _perform(conn, task_id, effect_id, action, described, *, approval_id) -> Outcome:
    performer = PERFORMERS[action.action_type]
    key = described["idempotency_key"]
    async with conn.transaction():
        await ledger.append(
            conn,
            task_id,
            "effect.intent",
            {"effect_id": effect_id, "idempotency_key": key, "approval_id": approval_id},
        )
    try:
        result, kind, error = performer.perform(action, key), "done", None
    except Exception as exc:  # noqa: BLE001  the target said no, or its state is in doubt
        found = performer.lookup(action, key)
        result, kind, error = found or {}, ("done" if found else "failed"), repr(exc)
    async with conn.transaction():
        await ledger.append(
            conn,
            task_id,
            "effect.outcome",
            {"effect_id": effect_id, "idempotency_key": key, "kind": kind, "result": result, "error": error},
        )
    return Outcome(effect_id, kind, result, error)


async def _held(conn, effect_id: str) -> dict[str, Any]:
    row = await (
        await conn.execute(
            "SELECT task_id, payload FROM events WHERE type = 'effect.held' AND payload->>'effect_id' = %s",
            (effect_id,),
        )
    ).fetchone()
    if row is None:
        raise KeyError(f"no held effect {effect_id}")
    return {"task_id": row[0], "payload": row[1]}


async def _prior(conn, task_id: str, key: str) -> Outcome | None:
    """The standing answer for this key on this task: a finished effect, one
    already held for Tom, or one in flight."""
    rows = await (
        await conn.execute(
            "SELECT type, payload FROM events WHERE task_id = %s "
            "AND type IN ('effect.held', 'effect.intent', 'effect.outcome') "
            "AND payload->>'idempotency_key' = %s ORDER BY id",
            (task_id, key),
        )
    ).fetchall()
    if not rows:
        return None
    kind, payload = rows[-1]
    if kind == "effect.outcome" and payload["kind"] == "done":
        return Outcome(payload["effect_id"], "done", payload["result"])
    if kind == "effect.held":
        return Outcome(payload["effect_id"], "pending")
    if kind == "effect.intent":
        return Outcome(payload["effect_id"], "refused", error="in flight")
    return None
