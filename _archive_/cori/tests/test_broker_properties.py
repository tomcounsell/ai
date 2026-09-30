"""The broker's invariants as Hypothesis properties. Plan 07 task 9;
spike 02, spike 05's style.

Harness: Hypothesis runs a synchronous `@given` body and a psycopg async
connection is bound to one event loop, so each example runs under
`asyncio.run` on a fresh `kernel_rw` connection opened inside the example and
closed with it. `space_for`, `credential_for`, and `check_generation` are the
module-level names `broker/__init__.py` binds at import, replaced per example
the way the tree plan patches its seat file.
"""

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import ClassVar

import psycopg
import pytest
from hypothesis import given, settings, strategies as st

import broker
from broker import actions, gmail, ledger, push_branch
from broker.credentials import Credential
from broker.errors import EffectRefused
from kernel.tree import StaleGeneration
from schemas.brief import BriefToken
from schemas.effect import Action, ConnectorRead
from schemas.ids import new_id
from schemas.space import EFFECT_RANK, EffectClass, RoutingRule, Space
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres

DB = settings(max_examples=200, deadline=None)
TARGET = "fake://target"
OTHER_TARGETS = ["github.com/yudame", "mailto:tom@yuda.me", "fake://elsewhere"]


# ---------------------------------------------------------------------------
# The fake target


class FakeAction(Action):
    effect_class: ClassVar[EffectClass] = "propose"
    action_type: ClassVar[str] = "fake_effect"

    name: str
    reach: str = TARGET

    def derive_key(self) -> str:
        return f"fake:{self.name}"

    def target(self) -> str:
        return self.reach


class FakeTarget:
    """A dict of keys the target holds, and a crash point.

    `crash` is where the process dies: `before_write` is a kill after the
    intent commits and before the action lands, `after_write` a kill after it
    lands and before the outcome commits. Both are raised as a dedicated
    exception so the test can tell a kill from a target failure.
    """

    model = FakeAction

    def __init__(self):
        self.held: dict[str, str] = {}
        self.writes: list[str] = []
        self.crash: str | None = None
        self.lock_seen: list[bool] = []
        self.watch_lock: str | None = None

    def check(self, action) -> None:
        return None

    async def run(self, conn, action, credential):
        if self.watch_lock is not None:
            self.lock_seen.append(await _lock_is_held(self.watch_lock))
        if self.crash == "before_write":
            raise Killed("killed before the action landed")
        self.held[action.idempotency_key] = action.name
        self.writes.append(action.idempotency_key)
        if self.crash == "after_write":
            raise Killed("killed after the action landed")
        return {"name": action.name}

    async def query(self, conn, action, credential):
        return "present" if action.idempotency_key in self.held else "absent"


class Killed(BaseException):
    """A kill, not a failure of the target.

    A `BaseException` on purpose: `perform` step 8 catches `Exception` and
    turns a target failure into a `failed` or `unknown` close, which is the
    opposite of what a kill leaves behind. A kill leaves the intent open with
    no close at all, and that is the only state `reconcile_dangling` has to
    work from (spike 02).
    """


async def _lock_is_held(key: str) -> bool:
    """Whether the `effect:` advisory lock is held right now, asked from a
    second connection so the answer is not this transaction's own."""
    async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as other:
        row = await (
            await other.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND classid::bigint = (hashtextextended(%s, 0) >> 32) & 4294967295 "
                "AND objid::bigint = hashtextextended(%s, 0) & 4294967295",
                (key, key),
            )
        ).fetchone()
    return row[0] > 0


def manifest(space_id: str, ceiling: str = "propose", targets=None) -> Space:
    return Space(
        id=space_id,
        kind="client",
        roots=[f"/{space_id}"],
        max_effect_class=ceiling,
        allowed_targets=[TARGET] if targets is None else list(targets),
    )


def wire(mp, space: Space, target: FakeTarget, generation=None):
    """The module-level names `perform` reads, for one example."""
    mp.setitem(actions.MODULES, FakeAction.action_type, target)
    mp.setattr(broker, "space_for", lambda _: space)
    mp.setattr(
        broker,
        "credential_for",
        lambda s, t: Credential(space=s, target=t, names=()),
    )

    async def check(conn, token):
        if generation is not None:
            generation(token)

    mp.setattr(broker, "check_generation", check)


def action_for(space_id: str, name: str, **over) -> FakeAction:
    fields = {
        "space": space_id,
        "objective_id": new_id(),
        "brief_id": new_id(),
        "name": name,
    }
    fields.update(over)
    return FakeAction(**fields)


async def fresh_conn():
    conn = await psycopg.AsyncConnection.connect(dsn("kernel_rw"))
    await conn.set_autocommit(True)
    return conn


async def rows(conn, space_id: str) -> list[tuple]:
    return await (
        await conn.execute(
            "SELECT effect_id, event, outcome_kind, idempotency_key "
            "FROM effect_ledger WHERE space_id = %s ORDER BY id",
            (space_id,),
        )
    ).fetchall()


# ---------------------------------------------------------------------------
# Exactly once per key under kills


CRASHES = st.sampled_from([None, "before_write", "after_write"])
STEPS = st.lists(
    st.tuples(st.integers(min_value=0, max_value=2), CRASHES) | st.just("reconcile"),
    min_size=1,
    max_size=6,
)


@DB
@given(steps=STEPS)
def test_exactly_once_per_key_under_kills(steps):
    """Spike 02's reconcile column as a property.

    Every key lands on the target at most once, whatever the kills; a key
    whose sequence holds a clean call, or a kill followed later by a
    reconcile, lands exactly once. The ledger holds one `intent` per effect
    id and at most one close, and the effect lock is never held while the
    target runs.
    """
    asyncio.run(_exactly_once(steps))


async def _exactly_once(steps):
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    pool = [action_for(space_id, f"{space_id}-a{i}") for i in range(3)]
    target = FakeTarget()
    target.watch_lock = broker.LOCK_NAMESPACE + pool[0].idempotency_key
    landed_clean: set[str] = set()
    killed: set[str] = set()
    reconciled_after: set[str] = set()

    conn = await fresh_conn()
    try:
        with pytest.MonkeyPatch.context() as mp:
            wire(mp, manifest(space_id), target)
            for step in steps:
                if step == "reconcile":
                    # A reconcile pass is a restart, so the target that the
                    # kill interrupted is answering normally again.
                    target.crash = None
                    await broker.reconcile_dangling(conn)
                    reconciled_after |= killed
                    continue
                index, crash = step
                action = pool[index]
                target.crash = crash
                try:
                    outcome = await broker.perform(
                        conn,
                        action,
                        None,
                        token=BriefToken(brief_id=action.brief_id, generation=1),
                    )
                except Killed:
                    killed.add(action.idempotency_key)
                else:
                    # `done` is the only kind that says the action landed on
                    # this call. A key whose intent an earlier kill left open
                    # comes back `in_flight` and touches nothing.
                    if outcome.kind == "done":
                        landed_clean.add(action.idempotency_key)

        # At most once on the target, for every key.
        for key in {a.idempotency_key for a in pool}:
            assert target.writes.count(key) <= 1, key
        # Exactly once for every key that was asked for cleanly, or killed
        # and then reconciled.
        for key in landed_clean | (killed & reconciled_after):
            assert target.writes.count(key) == 1, key

        # The ledger: one intent per effect id, at most one close.
        by_effect: dict[str, list[str]] = {}
        for effect_id, event, _kind, _key in await rows(conn, space_id):
            by_effect.setdefault(effect_id, []).append(event)
        for effect_id, events in by_effect.items():
            assert events.count("intent") <= 1, effect_id
            assert sum(1 for e in events if e in ("outcome", "reconciled")) <= 1

        # The effect lock is transaction scoped and released with the intent,
        # never held across the call on the target.
        assert not any(target.lock_seen)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# The second lock holds before the target


@DB
@given(
    ceiling=st.sampled_from(["read", "propose", "act"]),
    effect_class=st.sampled_from(["read", "propose"]),
    reach=st.sampled_from([TARGET, *OTHER_TARGETS]),
    allowed=st.lists(st.sampled_from([TARGET, *OTHER_TARGETS]), max_size=4),
)
def test_broker_refuses_outside_the_space_before_touching_the_target(
    ceiling, effect_class, reach, allowed
):
    """The manifest is the second lock, after the kernel's capability check.
    The target is called only for an action at or below the space's ceiling
    and on a target the space is allowed to reach; every other example
    leaves exactly one `refused` row and no intent."""
    asyncio.run(_second_lock(ceiling, effect_class, reach, allowed))


async def _second_lock(ceiling, effect_class, reach, allowed):
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    target = FakeTarget()
    conn = await fresh_conn()
    try:
        with pytest.MonkeyPatch.context() as mp:
            wire(mp, manifest(space_id, ceiling, allowed), target)
            mp.setattr(FakeAction, "effect_class", effect_class)
            action = action_for(space_id, uuid.uuid4().hex[:8], reach=reach)
            await broker.perform(
                conn,
                action,
                None,
                token=BriefToken(brief_id=action.brief_id, generation=1),
            )
        allowed_here = (
            EFFECT_RANK[effect_class] <= EFFECT_RANK[ceiling] and reach in allowed
        )
        written = await rows(conn, space_id)
        if allowed_here:
            assert target.writes == [action.idempotency_key]
            assert [r[1] for r in written] == ["intent", "outcome"]
        else:
            assert target.writes == []
            assert [r[1:3] for r in written] == [("refused", "refused")]
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# A stale generation never reaches the target


@DB
@given(
    carried=st.integers(min_value=1, max_value=5),
    current=st.integers(min_value=1, max_value=3),
    bump=st.booleans(),
)
def test_stale_generation_never_reaches_the_target(carried, current, bump):
    """Below the node's generation at either check, one `refused` row, no
    intent, and no call on the target. The second check is the one that
    binds: a stop that commits between the two still refuses (tech stack
    §4)."""
    asyncio.run(_stale(carried, current, bump))


async def _stale(carried, current, bump):
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    target = FakeTarget()
    seen = {"calls": 0, "current": current}

    def generation(token):
        seen["calls"] += 1
        if bump and seen["calls"] == 2:
            seen["current"] += 1
        if token.generation < seen["current"]:
            raise StaleGeneration(
                f"{token.brief_id} carries {token.generation}, "
                f"the node is at {seen['current']}"
            )

    conn = await fresh_conn()
    try:
        with pytest.MonkeyPatch.context() as mp:
            wire(mp, manifest(space_id), target, generation=generation)
            action = action_for(space_id, uuid.uuid4().hex[:8])
            outcome = await broker.perform(
                conn,
                action,
                None,
                token=BriefToken(brief_id=action.brief_id, generation=carried),
            )
        written = await rows(conn, space_id)
        stale_at_either = carried < current or (bump and carried < current + 1)
        if stale_at_either:
            assert outcome.kind == "refused"
            assert outcome.error.startswith("stale_generation")
            assert [r[1] for r in written] == ["refused"]
            assert target.writes == []
        else:
            assert outcome.kind == "done"
            assert [r[1] for r in written] == ["intent", "outcome"]
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# The ledger fold is total


SHAPES = st.sampled_from(
    ["intent", "intent+outcome", "intent+reconciled", "refused", "intent+twice"]
)


@DB
@given(shapes=st.lists(SHAPES, min_size=1, max_size=6))
def test_ledger_fold_is_total(shapes):
    """For every effect id the rows are `[intent]`, `[intent, outcome]`,
    `[intent, reconciled]`, or `[refused]`, and `dangling()` returns exactly
    the ids in the first shape. A second intent for one id is the unique
    violation, which is what makes the fold total rather than hopeful."""
    asyncio.run(_fold(shapes))


async def _fold(shapes):
    space_id = f"space-{uuid.uuid4().hex[:8]}"
    conn = await fresh_conn()
    expected_dangling: set[str] = set()
    try:
        for i, shape in enumerate(shapes):
            action = action_for(space_id, f"{space_id}-f{i}")
            fields = {
                "action_type": action.action_type,
                "effect_class": action.effect_class,
                "space": space_id,
                "target": action.target(),
                "idempotency_key": action.idempotency_key,
                "payload": action.model_dump(mode="json"),
                "objective_id": action.objective_id,
                "brief_id": action.brief_id,
                "generation": 1,
            }
            if shape == "refused":
                await ledger.refused(conn, error="no", **fields)
                continue
            effect_id = await ledger.intent(conn, **fields)
            if shape == "intent":
                expected_dangling.add(effect_id)
            elif shape == "intent+outcome":
                await ledger.outcome(conn, effect_id=effect_id, kind="done", **fields)
            elif shape == "intent+reconciled":
                await ledger.reconciled(
                    conn, effect_id=effect_id, kind="recovered", **fields
                )
            elif shape == "intent+twice":
                expected_dangling.add(effect_id)
                with pytest.raises(psycopg.errors.UniqueViolation):
                    await ledger.intent(conn, effect_id=effect_id, **fields)

        shaped: dict[str, list[str]] = {}
        for effect_id, event, _kind, _key in await rows(conn, space_id):
            shaped.setdefault(effect_id, []).append(event)
        for effect_id, events in shaped.items():
            assert events in (
                ["intent"],
                ["intent", "outcome"],
                ["intent", "reconciled"],
                ["refused"],
            ), (effect_id, events)

        open_now = {
            row["effect_id"]
            for row in await ledger.dangling(conn)
            if row["space_id"] == space_id
        }
        assert open_now == expected_dangling
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# The branch is the objective's


BRANCHES = st.one_of(
    st.sampled_from(["main", "master", "cori", "cori/", "", "HEAD", "../cori/x"]),
    st.text(alphabet="abcdef0123456789/.-_", min_size=0, max_size=12),
    st.builds(lambda: f"cori/{uuid.uuid4().hex}"),
)


@settings(max_examples=2000, deadline=None)
@given(
    objective_id=st.sampled_from([uuid.uuid4().hex for _ in range(8)]), suffix=BRANCHES
)
def test_push_branch_accepts_only_the_objectives_branch(objective_id, suffix):
    """`check_branch` accepts the objective's own branch and nothing else.
    Tech stack §7 restricts the push to the objective's branch name, and with
    one Executor per leaf that name is a function of the id."""
    for branch in (suffix, f"cori/{objective_id}", f"cori/{objective_id}/x"):
        accepted = True
        try:
            push_branch.check_branch(branch, objective_id)
        except EffectRefused:
            accepted = False
        assert accepted == (branch == f"cori/{objective_id}"), branch


# ---------------------------------------------------------------------------
# A connector query never escapes the routing rule


RULES = st.builds(
    RoutingRule,
    sender_domain=st.one_of(
        st.none(), st.sampled_from(["psyoptimal.com", "a.example"])
    ),
    label=st.one_of(st.none(), st.just("clients")),
    folder=st.one_of(st.none(), st.just("Clients/PsyOptimal")),
    calendar=st.one_of(st.none(), st.just("work")),
)
QUERIES = st.one_of(
    st.builds(lambda: f"id:{uuid.uuid4().hex}"),
    st.text(alphabet="abcdef0123456789:@. ", min_size=0, max_size=14),
    st.sampled_from(["id:", "from:@psyoptimal.com", "id:ZZZ", "*", "id:abc def"]),
)
SINCE = st.datetimes(
    min_value=datetime(2020, 1, 1), max_value=datetime(2030, 1, 1)
).map(lambda d: d.replace(tzinfo=timezone.utc))


@settings(max_examples=2000, deadline=None)
@given(rule=RULES, since=SINCE, query=QUERIES)
def test_connector_query_never_escapes_the_routing_rule(rule, since, query):
    """A composed query always carries the rule's sender domain and a rule
    without one has no query at all, so no M0 read can widen to the whole
    account (architecture §9). A worker names one message and never a
    search."""
    if rule.sender_domain is None:
        with pytest.raises(EffectRefused):
            gmail.compose_query(rule, since)
    else:
        composed = gmail.compose_query(rule, since)
        assert f"from:@{rule.sender_domain}" in composed
        assert f"after:{int(since.timestamp())}" in composed

    action = ConnectorRead(
        space="psyoptimal",
        objective_id=new_id(),
        brief_id=new_id(),
        connector="gmail",
        account="tom@yuda.me",
        query=query,
    )
    accepted = True
    try:
        gmail.check(action)
    except EffectRefused:
        accepted = False
    assert accepted == bool(re.fullmatch(r"id:[0-9a-f]+", query)), query
