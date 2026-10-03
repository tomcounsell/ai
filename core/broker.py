"""The broker: every effect on the world passes through here.

An effect's class is a fact about its action type, declared by the performer
that carries it out, never a field the requester fills:

- `read`: no effect.
- `propose`: reversible; a draft, a branch, a file in the task's workspace.
- `act`: irreversible or money; a send, a merge, a payment.

`request` refuses anything above the task's ceiling, returns the earlier
outcome for a repeated request (one with the same `request_id` and
digest), performs `read` and `propose` at once, and holds every `act`
pending. A held effect leaves only through `release`, which
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
performer's `lookup`, and the router does so for a task's merge. The intent
carries the action, so an intent settles from its own row.

The idempotency key ends in the effect id, so two identical requests are
two effects; a repeated request is matched by its `request_id` (the turn
and the signal file it came from) instead.

A declared performer (`core/bridge.py`'s `Declared`) has no `perform` in
the kernel: its type is a bridge's. `release` runs every check for it,
writes no intent, and appends `release.requested` for the owning bridge,
which releases it again with the performer joined. A release refused after
Tom approved (`release.requested` stands for the effect) appends one
`effect.refused` and owes Tom a notice, so nothing asks for it again.
"""

import inspect
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg

from core import git, ledger, machine, tasks
from core.settings import settings
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

    def key(self, effect_id: str) -> str:
        """Ends in the effect id: a platform id derived from it differs
        between two identical sends and repeats for a retry of one."""
        return f"{self.action_type}:{self.target}:{ledger.digest(self.payload)[:16]}:{effect_id}"

    def describe(self, effect_class: str, adds_governance: bool, effect_id: str) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "effect_class": effect_class,
            "target": self.target,
            "payload": self.payload,
            "adds_governance": adds_governance,
            "idempotency_key": self.key(effect_id),
            "payload_sha256": ledger.digest(self.payload),
        }


@dataclass(frozen=True)
class Outcome:
    effect_id: str
    kind: str  # done, failed, pending, refused, released (to its bridge), unknown (in flight)
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class NotApproved(RuntimeError):
    pass


class Unknown(RuntimeError):
    """A performer could not tell whether the effect happened (the target
    did not answer). Nothing is concluded from it: raised by `perform`, or
    by the `lookup` asked after a failed perform, it leaves the intent in
    flight with no outcome, for `reconcile`."""


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


class Performers:
    """One task's performers, by action type, built from its Brief."""

    def __init__(self, *performers: Performer):
        self._by_type: dict[str, Performer] = {}
        for p in performers:
            if p.effect_class not in EFFECT_RANK:
                raise ValueError(f"unknown effect class {p.effect_class!r}")
            self._by_type[p.action_type] = p

    def get(self, action_type: str) -> Performer | None:
        return self._by_type.get(action_type)

    def offered(self) -> list[str]:
        """The usage line of every performer a turn may request."""
        return [p.usage for _, p in sorted(self._by_type.items()) if getattr(p, "usage", None)]


# The performers of the task a job is advancing (`router.step` sets it for
# the job's own asyncio task); outside a job, the registered ones.
CURRENT: ContextVar[Performers | None] = ContextVar("performers", default=None)


def _performer(action_type: str, performers: Performers | None = None) -> Performer | None:
    chosen = performers or CURRENT.get()
    return chosen.get(action_type) if chosen is not None else PERFORMERS.get(action_type)


def offered(performers: Performers | None = None) -> list[str]:
    """The usage line of every performer a turn may request."""
    chosen = performers or CURRENT.get()
    if chosen is not None:
        return chosen.offered()
    return [p.usage for _, p in sorted(PERFORMERS.items()) if getattr(p, "usage", None)]


def declared(performer: Any) -> bool:
    """A bridge's type, declared to the kernel, which the kernel does not
    perform: it carries an `owner` and no `perform`."""
    return getattr(performer, "owner", None) is not None and not hasattr(performer, "perform")


async def _maybe(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _refusal(performer: Any, conn, action: Action) -> str | None:
    """What the performer's `refuse` says: `refuse(action)` or, in the
    shape a declared type has, `refuse(conn, action)`."""
    refuse = getattr(performer, "refuse", None)
    if refuse is None:
        return None
    if len(inspect.signature(refuse).parameters) >= 2:
        return await _maybe(refuse(conn, action))
    return await _maybe(refuse(action))


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
        await _unlock(conn, key)


async def _unlock(conn, key: str) -> None:
    """Release a session lock, never letting a failure here (a dropped
    connection, which releases the lock anyway) hide the error that ended
    the block."""
    try:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
    except Exception:  # noqa: BLE001, S110  the session is gone and its locks with it
        pass


async def request(
    conn,
    task_id: str,
    action: Action,
    *,
    request_id: str | None = None,
    performers: Performers | None = None,
) -> Outcome:
    """Refuse, hold, or perform one action. `request_id` names where the
    request came from (the session passes `<turn>/<signal file>`): a prior
    effect on the task with the same `request_id` and digest is the
    answer, so a re-collected file is never a second effect."""
    effect_id = ledger.new_id()
    async with _performing(conn, effect_id):
        return await _request(conn, task_id, action, effect_id, request_id, performers)


async def _request(conn, task_id, action, effect_id, request_id, performers) -> Outcome:
    performer = _performer(action.action_type, performers)
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        if request_id is not None:
            prior = await _prior(conn, task_id, request_id, ledger.digest(action.payload))
            if prior is not None:
                return prior
        brief = await tasks.brief(conn, task_id)
        adds, ungranted = _governance(machine.fold(await ledger.read(conn, task_id)), action)
        effect_class = "act" if adds else getattr(performer, "effect_class", "act")
        described = action.describe(effect_class, adds, effect_id)
        if request_id is not None:
            described["request_id"] = request_id
        reason = None
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
        else:
            reason = await _refusal(performer, conn, action)
        if reason is not None:
            await ledger.append(
                conn, task_id, "effect.refused", {"effect_id": effect_id, **described, "reason": reason}
            )
            return Outcome(effect_id, "refused", error=reason)
        if effect_class == "act" or declared(performer):
            await ledger.append(conn, task_id, "effect.held", {"effect_id": effect_id, **described})
            return Outcome(effect_id, "pending")
        await _intent(conn, task_id, effect_id, described, approval_id=None)
    return await _perform(conn, task_id, effect_id, action, described, performer)


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


async def release(conn, effect_id: str, *, performers: Performers | None = None) -> Outcome:
    """Perform a held effect Tom approved. Raises `NotApproved` when no
    unused approval matches it, `TaskStopped` for a stopped task,
    `Refused` when its performer refuses, and for a `merge`,
    `MergeRefused` naming every predicate term that does not hold. The
    checks and the intent row are one transaction under the task's lock;
    nothing is written when they refuse and the approval stays unused,
    unless Tom's approval asked for the release (`release.requested`
    stands): then the refusal is final, one `effect.refused` is appended
    and a notice is owed, and the error still reaches the caller. A
    declared type returns `released`: its bridge performs it."""
    async with _performing(conn, effect_id):
        try:
            return await _release(conn, effect_id, performers)
        except (Refused, NotApproved, tasks.TaskStopped) as exc:
            await _refused_release(conn, effect_id, str(exc) or type(exc).__name__)
            raise


async def _refused_release(conn, effect_id: str, reason: str) -> None:
    from core import notices

    held = await _held(conn, effect_id)
    task_id, described = held["task_id"], held["payload"]
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        asked = await (
            await conn.execute(
                "SELECT 1 FROM events WHERE type = 'release.requested' AND payload->>'effect_id' = %s",
                (effect_id,),
            )
        ).fetchone()
        if asked is None or await _effect_rows(conn, task_id, effect_id) & {
            "effect.refused",
            "effect.intent",
        }:
            return
        await ledger.append(
            conn,
            task_id,
            "effect.refused",
            {**described, "effect_id": effect_id, "reason": reason, "at": "release"},
        )
        await notices.request(
            conn,
            task_id,
            kind="effect_refused",
            about_key=f"effect-refused:{effect_id}",
            text=f"{described['action_type']} -> {described['target']} (effect {effect_id}) was not "
            f"released: {reason}",
        )


async def _effect_rows(conn, task_id: str, effect_id: str) -> set[str]:
    return {
        r[0]
        for r in await (
            await conn.execute(
                "SELECT type FROM events WHERE task_id = %s AND payload->>'effect_id' = %s "
                "AND type IN ('effect.held', 'effect.intent', 'effect.outcome', 'effect.refused')",
                (task_id, effect_id),
            )
        ).fetchall()
    }


async def reconcile(
    conn, effect_id: str, settle_after_s: float | None = None, *, performers: Performers | None = None
) -> Outcome | None:
    """Settle an effect whose intent has no outcome because the process
    performing it died: ask the target through the performer's `lookup`,
    with the action the intent carries (a row written before intents
    carried it reads the action from `effect.held`). Present: `done`.
    Absent: `failed`, but only once the intent is older than
    `settle_after_s` (default `settings.reconcile_after_s`, twice the hard
    limit on any git call, a push included), because a performer whose
    database connection dropped frees its lock while its push may still be
    running; before that, nothing. Unknown (the target did not answer):
    nothing; the effect stays in flight. Also nothing while a live process
    holds the effect (`_performing`), when there is nothing to settle, or
    when the task has no performer for it. Returns the outcome written, if
    any."""
    key = f"effect:{effect_id}"
    got = await (
        await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
    ).fetchone()
    if not got[0]:
        return None
    try:
        found_intent = await _intent_row(conn, effect_id)
        if found_intent is None:
            return None
        task_id, described, age = found_intent
        if "effect.outcome" in await _effect_rows(conn, task_id, effect_id):
            return None
        performer = _performer(described["action_type"], performers)
        if performer is None or not hasattr(performer, "lookup"):
            return None
        action = Action(described["action_type"], described["target"], described["payload"])
        try:
            found = await _maybe(performer.lookup(action, described["idempotency_key"]))
        except Exception:  # noqa: BLE001  unknown: conclude nothing
            return None
        if not found:
            limit = settle_after_s if settle_after_s is not None else settings.reconcile_after_s
            if age < limit:
                return None
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
        await _unlock(conn, key)


async def _intent_row(conn, effect_id: str) -> tuple[str, dict[str, Any], float] | None:
    """The intent's task, its action (from the intent, else `effect.held`),
    and its age in seconds."""
    row = await (
        await conn.execute(
            "SELECT i.task_id, i.payload, h.payload, extract(epoch FROM clock_timestamp() - i.at) "
            "FROM events i LEFT JOIN events h ON h.type = 'effect.held' "
            "AND h.payload->>'effect_id' = i.payload->>'effect_id' "
            "WHERE i.type = 'effect.intent' AND i.payload->>'effect_id' = %s",
            (effect_id,),
        )
    ).fetchone()
    if row is None:
        return None
    task_id, intent, held, age = row
    described = {**(held or {}), **intent} if "action_type" in intent else {**intent, **(held or {})}
    if "action_type" not in described:
        return None
    return task_id, described, float(age)


async def dangling(conn, action_types: list[str] | tuple[str, ...]) -> list[str]:
    """Effects of these types whose intent has no outcome, oldest first."""
    rows = await (
        await conn.execute(
            "SELECT i.payload->>'effect_id' FROM events i LEFT JOIN events h ON h.type = 'effect.held' "
            "AND h.payload->>'effect_id' = i.payload->>'effect_id' "
            "WHERE i.type = 'effect.intent' "
            "AND COALESCE(i.payload->>'action_type', h.payload->>'action_type') = ANY(%s) "
            "AND NOT EXISTS (SELECT 1 FROM events o WHERE o.type = 'effect.outcome' "
            "AND o.payload->>'effect_id' = i.payload->>'effect_id') ORDER BY i.id",
            (list(action_types),),
        )
    ).fetchall()
    return [r[0] for r in rows]


async def _release(conn, effect_id: str, performers: Performers | None) -> Outcome:
    async with conn.transaction():
        held = await _held(conn, effect_id)
        task_id, described = held["task_id"], held["payload"]
        await ledger.lock(conn, f"task:{task_id}")
        rows = await _effect_rows(conn, task_id, effect_id)
        if "effect.outcome" in rows or "effect.intent" in rows or "effect.refused" in rows:
            return await _standing(conn, effect_id)
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
        performer = _performer(action.action_type, performers)
        if performer is None:
            raise Refused(f"no performer for {action.action_type}")
        said = await _refusal(performer, conn, action)
        if said:
            raise Refused(said)
        if described["action_type"] == "merge":
            f = machine.fold(await ledger.read(conn, task_id))
            b = await tasks.brief(conn, task_id)
            # A task the kernel provisioned reads its git facts from the
            # kernel mirror, which no turn writes.
            facts = _git_facts(b.mirror or b.workspace, f, described["payload"])
            failed = machine.merge_predicate(
                f, described["payload"], approval_unused=row is not None, facts=facts
            )
            if failed:
                raise MergeRefused(failed)
        if row is None:
            raise NotApproved(f"effect {effect_id} has no approval from Tom")
        if declared(performer):
            asked = await (
                await conn.execute(
                    "SELECT 1 FROM events WHERE type = 'release.requested' AND payload->>'effect_id' = %s",
                    (effect_id,),
                )
            ).fetchone()
            if asked is None:
                await ledger.append(
                    conn,
                    task_id,
                    "release.requested",
                    {"effect_id": effect_id, "approval_id": row[0], "owner": performer.owner},
                )
            return Outcome(effect_id, "released")
        await _intent(conn, task_id, effect_id, described, approval_id=row[0])
    return await _perform(conn, task_id, effect_id, action, described, performer)


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
    """The intent row, inside the caller's transaction. It carries the
    action, so `reconcile` settles it from this row alone."""
    await ledger.append(
        conn,
        task_id,
        "effect.intent",
        {
            "effect_id": effect_id,
            "idempotency_key": described["idempotency_key"],
            "approval_id": approval_id,
            "action_type": described["action_type"],
            "target": described["target"],
            "payload": described["payload"],
            "payload_sha256": described["payload_sha256"],
            "effect_class": described["effect_class"],
            **({"request_id": described["request_id"]} if described.get("request_id") else {}),
        },
    )


async def _perform(conn, task_id, effect_id, action, described, performer) -> Outcome:
    """Run the performer after its intent committed, then write the
    outcome. `Unknown`, from `perform` or from the `lookup` asked after a
    failed perform, writes nothing: the intent stays in flight."""
    key = described["idempotency_key"]
    try:
        result, kind, error = await _maybe(performer.perform(action, key)), "done", None
    except Unknown as exc:
        return Outcome(effect_id, "unknown", error=repr(exc))
    except Exception as exc:  # noqa: BLE001  the target said no, or its state is in doubt
        try:
            found = await _maybe(performer.lookup(action, key))
        except Unknown as unknown:
            return Outcome(
                effect_id, "unknown", error=f"{exc!r}; and whether it happened is unknown: {unknown}"
            )
        except Exception as unknown:  # noqa: BLE001
            found, exc = None, RuntimeError(f"{exc!r}; and the lookup failed: {unknown!r}")
        result, kind, error = found or {}, ("done" if found else "failed"), repr(exc)
    try:
        async with conn.transaction():
            await ledger.append(
                conn,
                task_id,
                "effect.outcome",
                {
                    "effect_id": effect_id,
                    "idempotency_key": key,
                    "kind": kind,
                    "result": result,
                    "error": error,
                },
            )
    except psycopg.errors.UniqueViolation:
        # `reconcile` settled it first (this process had lost its session);
        # the recorded outcome stands.
        return await _standing(conn, effect_id)
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


async def _standing(conn, effect_id: str) -> Outcome:
    """Where one effect stands, from its latest row."""
    row = await (
        await conn.execute(
            "SELECT type, payload FROM events WHERE payload->>'effect_id' = %s "
            "AND type IN ('effect.held', 'effect.intent', 'effect.outcome', 'effect.refused') "
            "ORDER BY CASE type WHEN 'effect.outcome' THEN 3 WHEN 'effect.refused' THEN 2 "
            "WHEN 'effect.intent' THEN 1 ELSE 0 END DESC, id DESC LIMIT 1",
            (effect_id,),
        )
    ).fetchone()
    kind, payload = row
    if kind == "effect.outcome":
        return Outcome(effect_id, payload["kind"], payload.get("result") or {}, payload.get("error"))
    if kind == "effect.refused":
        return Outcome(effect_id, "refused", error=payload.get("reason"))
    if kind == "effect.intent":
        return Outcome(effect_id, "refused", error="in flight")
    return Outcome(effect_id, "pending")


async def _prior(conn, task_id: str, request_id: str, payload_sha256: str) -> Outcome | None:
    """The standing answer for an earlier effect on this task with the same
    `request_id` and digest. A row with no `request_id` matches nothing."""
    row = await (
        await conn.execute(
            "SELECT payload->>'effect_id' FROM events WHERE task_id = %s "
            "AND type IN ('effect.held', 'effect.intent', 'effect.refused') "
            "AND payload->>'request_id' = %s AND payload->>'payload_sha256' = %s ORDER BY id LIMIT 1",
            (task_id, request_id, payload_sha256),
        )
    ).fetchone()
    return None if row is None else await _standing(conn, row[0])
