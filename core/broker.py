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

Whether an action adds governance (a check, gate, hook, validator, review
round, or approval step) is the broker's to compute, never the requester's
to say: a `merge` adds governance when the review or docs verdict for the
candidate it names answered the governance boolean yes. Such a merge is
refused while any instance it names lacks Tom's tap (`guard.granted`); the
Brief's `governance_grant` does not stand in for the tap.

A `merge` leaves only when the merge predicate holds
(`machine.merge_predicate`): `release` evaluates it, with the git facts it
reads from the workspace, in the same transaction, under the task's lock,
that writes the intent, so nothing can land between the check and the
intent.

Performing follows intent, then outcome: the intent row commits before the
performer runs, so a kill between the two leaves a findable dangling intent,
never a silent effect. The performing process holds a session lock on the
effect from before its intent to its outcome; `reconcile` settles an intent
whose lock is free (its process died) by asking the target through the
performer's `lookup`, and the router does so for a task's merge.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from core import git, ledger, machine, tasks
from core.tasks import EFFECT_RANK


class Performer(Protocol):
    """`usage` is the line a turn's Brief lists for this action, or None
    when turns are not offered it. A performer may define `refuse(action)`,
    returning why it will not take an action, checked at request."""

    action_type: str
    effect_class: str
    usage: str | None

    def perform(self, action: Action, key: str) -> dict[str, Any]: ...

    def lookup(self, action: Action, key: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class Action:
    action_type: str
    target: str
    payload: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.action_type}:{self.target}:{ledger.digest(self.payload)[:16]}"

    def describe(self, effect_class: str, adds_governance: bool) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "effect_class": effect_class,
            "target": self.target,
            "payload": self.payload,
            "adds_governance": adds_governance,
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


class Refused(RuntimeError):
    """A release the performer will not take (`refuse`), checked before the
    intent, so nothing is written and the approval stays unused."""


class MergeRefused(Refused):
    """A merge whose predicate does not hold; `terms` names each failing
    term."""

    def __init__(self, terms: list[str]):
        super().__init__("the merge predicate does not hold: " + "; ".join(terms))
        self.terms = terms


PERFORMERS: dict[str, Performer] = {}


def register(performer: Performer) -> None:
    if performer.effect_class not in EFFECT_RANK:
        raise ValueError(f"unknown effect class {performer.effect_class!r}")
    PERFORMERS[performer.action_type] = performer


def offered() -> list[str]:
    """The usage line of every registered performer a turn may request."""
    return [p.usage for _, p in sorted(PERFORMERS.items()) if getattr(p, "usage", None)]


def _governance(f: machine.Fold, action: Action) -> tuple[bool, list[machine.Instance]]:
    """Whether the action adds governance, and the instances still lacking
    Tom's tap. Only a merge carries a diff onto the target branch, so only
    a merge can; it does when the review or docs verdict for the candidate
    it names answered the governance boolean yes."""
    if action.action_type != "merge":
        return False, []
    named = action.payload.get("candidate") or {}
    if f.candidate is None or named != {"sha": f.candidate.sha, "turn_id": f.candidate.turn_id}:
        return False, []
    adds = any(
        (v.payload.get("governance") or {}).get("adds")
        for c, v in f.checks.items()
        if c in (machine.Check.REVIEW, machine.Check.DOCS)
    )
    return adds, f.ungranted() if adds else []


@asynccontextmanager
async def _performing(conn, effect_id: str):
    """A session lock on one effect, held from before its intent until its
    outcome is written. A process that dies mid-perform loses its session,
    and the lock with it, which is how `reconcile` knows no one is still
    performing a dangling intent."""
    key = f"effect:{effect_id}"
    await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
    try:
        yield
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


async def request(conn, task_id: str, action: Action) -> Outcome:
    effect_id = ledger.new_id()
    async with _performing(conn, effect_id):
        return await _request(conn, task_id, action, effect_id)


async def _request(conn, task_id: str, action: Action, effect_id: str) -> Outcome:
    performer = PERFORMERS.get(action.action_type)
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        brief = await tasks.brief(conn, task_id)
        adds, ungranted = _governance(machine.fold(await ledger.read(conn, task_id)), action)
        effect_class = "act" if adds else getattr(performer, "effect_class", "act")
        described = action.describe(effect_class, adds)
        prior = await _prior(conn, task_id, described["idempotency_key"])
        if prior is not None:
            return prior
        reason = None
        refuse = getattr(performer, "refuse", None)
        if performer is None:
            reason = f"no performer for {action.action_type}"
        elif await tasks.is_stopped(conn, task_id):
            reason = "task stopped"
        elif EFFECT_RANK[effect_class] > EFFECT_RANK[brief.max_effect_class]:
            reason = f"{effect_class} is above the task's ceiling {brief.max_effect_class}"
        elif ungranted:
            reason = "adds governance with no grant from Tom for instance " + ", ".join(
                f"{i.id} ({i.path})" for i in ungranted
            )
        elif refuse is not None and (said := refuse(action)):
            reason = said
        if reason is not None:
            await ledger.append(
                conn, task_id, "effect.refused", {"effect_id": effect_id, **described, "reason": reason}
            )
            return Outcome(effect_id, "refused", error=reason)
        if effect_class == "act":
            await ledger.append(conn, task_id, "effect.held", {"effect_id": effect_id, **described})
            return Outcome(effect_id, "pending")
        await _intent(conn, task_id, effect_id, described, approval_id=None)
    return await _perform(conn, task_id, effect_id, action, described)


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
    when no unused approval matches it, and for a `merge`, `MergeRefused`
    naming every predicate term that does not hold. The checks and the
    intent row are one transaction under the task's lock; nothing is
    written when either refuses, and the approval stays unused."""
    async with _performing(conn, effect_id):
        return await _release(conn, effect_id)


async def reconcile(conn, effect_id: str) -> Outcome | None:
    """Settle a held effect whose intent has no outcome because the process
    performing it died: ask the target through the performer's `lookup`
    and write the outcome it shows, `done` when the effect is there and
    `failed` when it is not. Does nothing while a live process still holds
    the effect (`_performing`), when there is nothing to settle, or when no
    performer for it is registered. Returns the outcome written, if any."""
    key = f"effect:{effect_id}"
    got = await (
        await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
    ).fetchone()
    if not got[0]:
        return None
    try:
        held = await _held(conn, effect_id)
        task_id, described = held["task_id"], held["payload"]
        kinds = {
            r[0]
            for r in await (
                await conn.execute(
                    "SELECT type FROM events WHERE task_id = %s AND payload->>'effect_id' = %s "
                    "AND type IN ('effect.intent', 'effect.outcome')",
                    (task_id, effect_id),
                )
            ).fetchall()
        }
        performer = PERFORMERS.get(described["action_type"])
        if kinds != {"effect.intent"} or performer is None:
            return None
        action = Action(described["action_type"], described["target"], described["payload"])
        found = performer.lookup(action, described["idempotency_key"])
        kind = "done" if found else "failed"
        async with conn.transaction():
            await ledger.append(
                conn,
                task_id,
                "effect.outcome",
                {
                    "effect_id": effect_id,
                    "idempotency_key": described["idempotency_key"],
                    "kind": kind,
                    "result": found or {},
                    "error": None
                    if found
                    else "reconciled: the intent had no outcome and the target holds no effect",
                    "reconciled": True,
                },
            )
        return Outcome(effect_id, kind, found or {})
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))


async def _release(conn, effect_id: str) -> Outcome:
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
        action = Action(described["action_type"], described["target"], described["payload"])
        refuse = getattr(PERFORMERS.get(action.action_type), "refuse", None)
        if refuse is not None and (said := refuse(action)):
            raise Refused(said)
        if described["action_type"] == "merge":
            f = machine.fold(await ledger.read(conn, task_id))
            facts = _git_facts((await tasks.brief(conn, task_id)).workspace, f, described["payload"])
            failed = machine.merge_predicate(
                f, described["payload"], approval_unused=row is not None, facts=facts
            )
            if failed:
                raise MergeRefused(failed)
        if row is None:
            raise NotApproved(f"effect {effect_id} has no approval from Tom")
        await _intent(conn, task_id, effect_id, described, approval_id=row[0])
    return await _perform(conn, task_id, effect_id, action, described)


def _git_facts(workspace: str | None, f: machine.Fold, payload: dict[str, Any]) -> machine.GitFacts | None:
    """What the workspace's history says between the candidate and the head
    being merged, read now, never taken from a row."""
    head = payload.get("head_sha")
    if not workspace or f.candidate is None or not head:
        return None
    try:
        ancestor = git.is_ancestor(workspace, f.candidate.sha, head)
        if not ancestor:
            return machine.GitFacts(False, (), ())
        return machine.GitFacts(
            True,
            tuple(git.merges_between(workspace, f.candidate.sha, head)),
            tuple(git.diff_paths(workspace, f.candidate.sha, head)),
        )
    except git.GitError:
        return None


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


async def _intent(conn, task_id, effect_id, described, *, approval_id) -> None:
    """The intent row, inside the caller's transaction."""
    await ledger.append(
        conn,
        task_id,
        "effect.intent",
        {"effect_id": effect_id, "idempotency_key": described["idempotency_key"], "approval_id": approval_id},
    )


async def _perform(conn, task_id, effect_id, action, described) -> Outcome:
    """Run the performer after its intent committed, then write the outcome."""
    performer = PERFORMERS[action.action_type]
    key = described["idempotency_key"]
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
