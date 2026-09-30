"""The tree's invariants as Hypothesis properties against the local
database with the fakes bound, one root per example. Plan 02, Properties.
Sync tests drive the async kernel on a private loop, as spike 01 did."""

import asyncio
import uuid
from typing import get_args

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import kernel.tree as tree
from kernel.events import read_for
from schemas.objective import ObjectiveState, StateReason
from tests.conftest import dsn, requires_postgres
from tests.tree_fakes import (
    APPROVING,
    NotAnApproval,
    approval,
    bind,
    contract,
    force_state,
    make_space,
    mint,
    request as build_request,
    terminal,
    token as token_of,
    verdict,
)

pytestmark = requires_postgres

SPACE = f"space-{uuid.uuid4().hex[:8]}"
SPACES = {SPACE: make_space(SPACE, max_effect_class="act")}
STATES = get_args(ObjectiveState)
REASONS = get_args(StateReason)
DB_SETTINGS = dict(
    deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)


@pytest.fixture(scope="module", autouse=True)
def bound():
    with pytest.MonkeyPatch.context() as mp:
        issuer, profile_for = bind(mp, SPACES)
        yield issuer, profile_for


def run(coro):
    return asyncio.run(coro)


async def connect():
    return await psycopg.AsyncConnection.connect(dsn("kernel_rw"))


async def open_root(conn, **over):
    return await tree.open_objective(
        conn, space=SPACE, conversation_id="c1", contract=contract(**over)
    )


# ---------------------------------------------------------------------------
# Revisions and approvals


@settings(max_examples=30, **DB_SETTINGS)
@given(data=st.data())
def test_no_stale_revision_is_honored(data):
    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn)
            state, current, approved = "FRAMED", 1, None
            for _ in range(data.draw(st.integers(0, 8))):
                if data.draw(st.booleans()):
                    if state in tree.REVISABLE:
                        assert await tree.revise_contract(conn, oid, contract()) == (
                            current + 1
                        )
                        current, approved = current + 1, None
                        assert (await tree.project(conn, oid)).approved_revision is None
                    else:
                        with pytest.raises(tree.IllegalTransition):
                            await tree.revise_contract(conn, oid, contract())
                else:
                    r = data.draw(st.integers(1, current + 1))
                    admits = (state, "APPROVED") in tree.TRANSITIONS
                    if r != current:
                        with pytest.raises(tree.StaleRevision):
                            await tree.approve(conn, oid, approval("approved", oid, r))
                    elif not admits:
                        with pytest.raises(tree.IllegalTransition):
                            await tree.approve(conn, oid, approval("approved", oid, r))
                    else:
                        await tree.approve(conn, oid, approval("approved", oid, r))
                        state, approved = "APPROVED", r
                node = await tree.project(conn, oid)
                assert (node.state, node.contract_revision, node.approved_revision) == (
                    state,
                    current,
                    approved,
                )
            await conn.commit()
        finally:
            await conn.close()

    run(go())


# ---------------------------------------------------------------------------
# The state machine

steps = st.lists(
    st.one_of(
        st.tuples(
            st.just("transition"), st.sampled_from(STATES), st.sampled_from(REASONS)
        ),
        st.tuples(
            st.just("approve"),
            st.sampled_from(sorted(APPROVING) + ["rejected", "expired"]),
        ),
        st.tuples(st.just("force"), st.sampled_from(["RUNNING", "VERIFYING"])),
        st.tuples(st.just("land"), st.sampled_from(["pass", "fail", "abstain"])),
    ),
    max_size=12,
)

VERDICT_AFTER = {
    "pass": "SUCCEEDED",
    "fail": "FAILED",
    "abstain": "AWAITING_APPROVAL",
}


def model_accepts(state: str, step: tuple) -> tuple[bool, str | None]:
    """Whether the table admits `step` from `state`, and the state after."""
    kind = step[0]
    if kind == "transition":
        _, to, reason = step
        pair = (state, to)
        ok = (
            pair in tree.TRANSITIONS
            and reason in tree.TRANSITIONS[pair]
            and pair not in tree.ONLY
        )
        return ok, (to if ok else state)
    if kind == "approve":
        approval_kind = step[1]
        if approval_kind not in APPROVING:
            return False, state
        if (state, "APPROVED") not in tree.TRANSITIONS:
            return False, state
        if state == "FAILED" and approval_kind != "approved":
            return False, state
        return True, "APPROVED"
    if kind == "land":
        # a real Verifier Brief and its verdict; only a VERIFYING node takes one
        ok = state == "VERIFYING"
        return ok, (VERDICT_AFTER[step[1]] if ok else state)
    target = step[1]
    needed = "APPROVED" if target == "RUNNING" else "RUNNING"
    return state == needed, (target if state == needed else state)


async def apply(conn, oid, state, step):
    """Run one step through the public API; returns (accepted, new_state)."""
    expected, after = model_accepts(state, step)
    kind = step[0]
    try:
        if kind == "transition":
            await tree.transition(conn, oid, step[1], step[2])
        elif kind == "approve":
            await tree.approve(conn, oid, approval(step[1], oid, 1))
        elif kind == "land":
            verifier = await tree.delegate(
                conn,
                build_request(
                    oid, "Verifier", space=SPACE, budget=10, effect_class="read"
                ),
                issuer=frozenset(),
                parent_brief="turn-1",
            )
            await tree.land_report(
                conn, token_of(verifier), terminal(report=verdict(step[1]))
            )
        else:
            by = "delegate" if step[1] == "RUNNING" else "land_report"
            reason = "running" if step[1] == "RUNNING" else "verifying"
            await force_state(conn, oid, step[1], reason, by=by)
    except tree.IllegalTransition, tree.NotLive, NotAnApproval:
        assert not expected, (state, step)
        return False, state
    assert expected, (state, step)
    return True, after


async def drive(conn, oid, sequence):
    state, accepted, path = "FRAMED", 0, ["FRAMED"]
    for step in sequence:
        ok, state = await apply(conn, oid, state, step)
        if ok:
            accepted += 1
            path.append(state)
        node = await tree.project(conn, oid)
        assert node.state == state, (step, node.state, state)
    return accepted, path


@settings(max_examples=40, **DB_SETTINGS)
@given(sequence=steps)
def test_state_machine_only_legal_paths(sequence):
    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn, max_effect_class="propose")
            accepted, path = await drive(conn, oid, sequence)
            for a, b in zip(path, path[1:]):
                assert (a, b) in tree.TRANSITIONS, (a, b)
                assert a not in ("SUCCEEDED", "CANCELLED")
                if a == "FAILED":
                    assert b == "APPROVED"
            events = await read_for(conn, space_id=SPACE, key="objective_id", value=oid)
            changes = [e for e in events if e.type == "objective.state_changed"]
            assert len(changes) == accepted
            assert [e.payload["to"] for e in changes] == path[1:]
            await conn.commit()
        finally:
            await conn.close()

    run(go())


async def test_state_machine_named_edges(kernel, space, monkeypatch):
    """The pairs the plan names by hand, and the one it refuses."""
    bind(monkeypatch, {space: make_space(space)})
    for to, reason, via in (
        ("FAILED", "budget_exhausted", "RUNNING"),
        ("FAILED", "worker_failed", "VERIFYING"),
        ("FAILED", "budget_exhausted", "VERIFYING"),
    ):
        oid = await tree.open_objective(
            kernel, space=space, conversation_id="c", contract=contract()
        )
        await tree.approve(kernel, oid, approval("approved", oid, 1))
        await force_state(kernel, oid, "RUNNING", "running", by="delegate")
        if via == "VERIFYING":
            await force_state(kernel, oid, "VERIFYING", "verifying", by="land_report")
        await tree.transition(kernel, oid, to, reason)
        assert (await tree.project(kernel, oid)).state_reason == reason
    oid = await tree.open_objective(
        kernel, space=space, conversation_id="c", contract=contract()
    )
    with pytest.raises(tree.IllegalTransition):
        await tree.transition(kernel, oid, "AWAITING_APPROVAL", "budget_increase")
    await kernel.commit()


# ---------------------------------------------------------------------------
# Replay


@settings(max_examples=30, **DB_SETTINGS)
@given(sequence=steps)
def test_replay_equals_projection(sequence):
    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn, max_effect_class="propose")
            await drive(conn, oid, sequence)
            row = await tree.objective_row(conn, oid)
            events = await read_for(conn, space_id=SPACE, key="objective_id", value=oid)
            ledger = await tree.ledger_rows(conn, oid)
            children = await tree.child_ids(conn, oid)
            once = tree.fold(row, events, ledger, children)
            twice = tree.fold(row, events, ledger, children)
            assert once == twice
            assert once == await tree.project(conn, oid)
            await conn.commit()
        finally:
            await conn.close()

    run(go())


# ---------------------------------------------------------------------------
# Budget conservation (spike 01, lifted; one integer of money, briefs in
# place of child nodes). The machine grows with the tasks: `delegate` issues
# real Scribe Briefs and `stop` fences them.

from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    invariant,
    rule,
)  # noqa: E402

from schemas.budget import ZERO, Budget  # noqa: E402
from schemas.ids import new_id  # noqa: E402
from tests.tree_fakes import receipt  # noqa: E402

AUDIT_NODES = """
SELECT node_id,
       SUM(CASE WHEN kind = 'allocate' THEN usd_micros ELSE 0 END)
     - SUM(CASE WHEN kind = 'release' THEN usd_micros ELSE 0 END) AS live
FROM budget_ledger WHERE space_id = %(space)s AND node_id = %(root)s
GROUP BY node_id HAVING
       SUM(CASE WHEN kind = 'allocate' THEN usd_micros ELSE 0 END)
     - SUM(CASE WHEN kind = 'release' THEN usd_micros ELSE 0 END) > %(budget)s
"""

AUDIT_BRIEFS = """
WITH consumed AS (
    SELECT brief_id, SUM(usd_micros) AS spent FROM budget_ledger
    WHERE space_id = %(space)s AND kind = 'consume' GROUP BY brief_id
), allocated AS (
    SELECT brief_id, usd_micros AS held FROM budget_ledger
    WHERE space_id = %(space)s AND kind = 'allocate'
), overrun AS (
    SELECT payload->>'brief_id' AS brief_id,
           SUM((payload->'shortfall'->>'usd_micros')::bigint) AS shortfall
    FROM events WHERE space_id = %(space)s AND type = 'budget.overrun'
    GROUP BY 1
)
SELECT c.brief_id, c.spent, a.held, COALESCE(o.shortfall, 0)
FROM consumed c JOIN allocated a USING (brief_id)
LEFT JOIN overrun o USING (brief_id)
WHERE c.spent > a.held + COALESCE(o.shortfall, 0)
"""

amounts = st.integers(min_value=1, max_value=400_000)


class BudgetConservation(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.conn = self.loop.run_until_complete(connect())
        self.budget = 1_000_000
        self.grants = 0
        self.root = self.run(open_root(self.conn, budget=self.budget))
        self.run(self.conn.commit())
        # per Brief: held, consumed, released, overrun shortfall
        self.briefs: dict[str, dict[str, int]] = {}
        self.live: list[str] = []
        self.unconfirmed: set[str] = set()

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    briefs = Bundle("briefs")

    # the model

    @property
    def B(self) -> int:
        return self.budget + self.grants

    @property
    def allocated_live(self) -> int:
        return sum(b["held"] - b["released"] for b in self.briefs.values())

    def brief_remaining(self, brief) -> int:
        b = self.briefs[brief]
        return max(b["held"] - b["consumed"] - b["released"], 0)

    # the rules

    @rule(target=briefs, amount=amounts)
    def delegate(self, amount):
        expected = amount <= self.B - self.allocated_live
        req = build_request(self.root, "Scribe", space=SPACE, budget=amount)
        try:
            brief = self.run(
                tree.delegate(self.conn, req, issuer=frozenset(), parent_brief="turn-1")
            )
            ok = True
        except tree.Unconfirmed:
            self.run(self.conn.commit())
            assert self.unconfirmed
            return hypothesis_multiple()
        except tree.BudgetExceeded:
            ok = False
        self.run(self.conn.commit())
        assert not self.unconfirmed
        assert ok == expected, (amount, ok, expected)
        if not ok:
            return hypothesis_multiple()
        self.briefs[brief.id] = dict(held=amount, consumed=0, released=0, overrun=0)
        self.live.append(brief.id)
        assert (
            self.run(tree.remaining(self.conn, self.root)).usd_micros
            == self.B - self.allocated_live
        )
        return brief.id

    @rule(brief=briefs, amount=amounts, incurred=st.booleans())
    def consume(self, brief, amount, incurred):
        left = self.brief_remaining(brief)
        expected = amount <= left
        try:
            got = self.run(
                tree.consume(
                    self.conn, brief, Budget(usd_micros=amount), incurred=incurred
                )
            )
            ok = True
        except tree.BudgetExceeded:
            ok = False
        self.run(self.conn.commit())
        assert ok == (expected or incurred), (brief, amount, incurred, ok)
        if not ok:
            return
        b = self.briefs[brief]
        b["consumed"] += amount
        if expected:
            assert got.usd_micros == left - amount
        else:
            b["overrun"] += amount - left
            assert got == ZERO

    @rule(by=amounts, kind=st.sampled_from(["approved", "self_approved", "rejected"]))
    def raise_budget(self, by, kind):
        aid = self.run(mint(self.conn, approval(kind, self.root, 1, space=SPACE)))
        try:
            self.run(
                tree.raise_budget(
                    self.conn, self.root, by=Budget(usd_micros=by), approval_id=aid
                )
            )
            ok = True
        except tree.Refused:
            ok = False
        self.run(self.conn.commit())
        assert ok == (kind == "approved")
        if ok:
            self.grants += by

    @rule()
    def stop(self):
        stopped = self.run(tree.stop(self.conn, self.root, "stopped_by_person"))
        self.run(self.conn.commit())
        assert set(stopped) == set(self.live)
        self.unconfirmed.update(stopped)
        self.live.clear()

    @rule(brief=briefs)
    def confirm_stop(self, brief):
        try:
            self.run(tree.confirm_stop(self.conn, brief, receipt()))
            ok = True
        except tree.NotLive:
            ok = False
        self.run(self.conn.commit())
        assert ok == (brief in self.unconfirmed)
        self.unconfirmed.discard(brief)

    @rule(brief=briefs)
    def release(self, brief):
        left = self.brief_remaining(brief)
        got = self.run(tree.release(self.conn, brief))
        self.run(self.conn.commit())
        assert got.usd_micros == left
        self.briefs[brief]["released"] += left

    # the invariants

    @invariant()
    def conserved_in_the_model(self):
        assert self.allocated_live <= self.B
        for b in self.briefs.values():
            assert b["consumed"] <= b["held"] + b["overrun"]
            if b["overrun"]:
                assert max(b["held"] - b["consumed"] - b["released"], 0) == 0

    @invariant()
    def database_agrees(self):
        got = self.run(tree.remaining(self.conn, self.root)).usd_micros
        assert got == self.B - self.allocated_live
        for brief in self.briefs:
            assert self.run(
                tree.remaining(self.conn, brief)
            ).usd_micros == self.brief_remaining(brief)
        params = {"space": SPACE, "root": self.root, "budget": self.B}
        for query in (AUDIT_NODES, AUDIT_BRIEFS):
            cur = self.run(self.conn.execute(query, params))
            assert self.run(cur.fetchall()) == []
        self.run(self.conn.commit())

    def teardown(self):
        self.run(self.conn.close())
        self.loop.close()


def hypothesis_multiple():
    from hypothesis.stateful import multiple

    return multiple()


BudgetConservation.TestCase.settings = settings(
    max_examples=40, stateful_step_count=30, **DB_SETTINGS
)
TestBudgetConservation = BudgetConservation.TestCase


# ---------------------------------------------------------------------------
# Every Brief within its node

from schemas.capability import ceiling as cap_ceiling, within  # noqa: E402
from tests.tree_fakes import (  # noqa: E402
    block,
    fake_root_capabilities,
)

CLASSES = ["Executor", "Verifier", "Scribe"]
NAMES = [
    "read",
    "write",
    "bash",
    "ask",
    "push_branch",
    "connector.read",
    "memory.episodic.write",
    "memory.operator.propose",
]

delegate_requests = st.builds(
    dict,
    agent_class=st.sampled_from(CLASSES),
    names=st.frozensets(st.sampled_from(NAMES), min_size=1, max_size=4),
    effect_class=st.sampled_from(["read", "propose", "act"]),
    sandbox_profile=st.sampled_from(["scratch", "verify", "worktree"]),
    max_data_class=st.sampled_from(["PROJECT", "OPERATOR"]),
    block_class=st.sampled_from(["PROJECT", "OPERATOR"]),
    budget=st.integers(min_value=1, max_value=600_000),
)


@settings(max_examples=40, **DB_SETTINGS)
@given(
    root_ceiling=st.sampled_from(["read", "propose", "act"]),
    requests=st.lists(delegate_requests, min_size=1, max_size=6),
)
def test_every_brief_within_its_node(root_ceiling, requests):
    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn, max_effect_class=root_ceiling)
            await tree.approve(conn, oid, approval("approved", oid, 1))
            node = await tree.project(conn, oid)
            node_caps = cap_ceiling(fake_root_capabilities(SPACES[SPACE]), root_ceiling)
            for r in requests:
                req = build_request(
                    oid,
                    r["agent_class"],
                    space=SPACE,
                    budget=r["budget"],
                    names=tuple(r["names"]),
                    effect_class=r["effect_class"],
                    sandbox_profile=r["sandbox_profile"],
                    max_data_class=r["max_data_class"],
                    blocks=[block(data_class=r["block_class"])],
                )
                before = await tree.remaining(conn, oid)
                rule = tree.CLASS_RULES[r["agent_class"]]
                state = (await tree.project(conn, oid)).state
                try:
                    brief = await tree.delegate(
                        conn, req, issuer=frozenset(), parent_brief="turn-1"
                    )
                except tree.NotLive:
                    assert rule.states is not None and state not in rule.states
                    continue
                except tree.AlreadyOwned:
                    assert r["agent_class"] == "Executor" and state == "RUNNING"
                    continue
                except tree.BudgetExceeded:
                    assert not req.budget.fits_within(before)
                    continue
                except tree.Refused:
                    violated = (
                        not within(req.capabilities, node_caps)
                        or not req.capabilities <= node_caps
                        or not (set(n.name for n in req.capabilities) <= rule.names)
                        or req.sandbox_profile not in rule.profiles
                        or tree.DATA_CLASS_RANK[req.max_data_class]
                        > tree.DATA_CLASS_RANK[rule.max_data_class]
                        or tree.DATA_CLASS_RANK[req.max_data_class]
                        > tree.DATA_CLASS_RANK[node.contract.ceilings.max_data_class]
                        or r["block_class"] == "OPERATOR"
                        and r["max_data_class"] == "PROJECT"
                        or (
                            rule.ceiling is not None
                            and any(
                                tree.EFFECT_RANK[c.effect_class]
                                > tree.EFFECT_RANK[rule.ceiling]
                                for c in req.capabilities
                            )
                        )
                    )
                    assert violated, (r, state)
                    continue
                assert within(brief.capabilities, node_caps)
                assert brief.ceilings.fits_within(node.contract.ceilings)
                assert brief.budget.fits_within(before)
                assert await tree.remaining(conn, oid) == before - brief.budget
            await conn.commit()
        finally:
            await conn.close()

    run(go())


# ---------------------------------------------------------------------------
# A root Scribe is its own root (plan 02, Properties)

ROOT_SCRIBE_ROWS = """
SELECT node_id, brief_id, kind, usd_micros FROM budget_ledger
WHERE space_id = %(space)s AND (node_id = %(brief)s OR brief_id = %(brief)s)
ORDER BY id
"""

root_scribe_steps = st.lists(
    st.one_of(
        st.tuples(st.just("delegate"), st.integers(1, 5_000)),
        st.tuples(st.just("consume"), st.integers(0, 8), st.integers(1, 3_000)),
        st.tuples(st.just("release"), st.integers(0, 8)),
    ),
    max_size=16,
)


@settings(max_examples=40, **DB_SETTINGS)
@given(sequence=root_scribe_steps)
def test_root_scribe_is_its_own_root(sequence):
    async def go():
        conn = await connect()
        try:
            briefs: list = []  # (brief_id, B)
            model: dict[str, dict[str, int]] = {}
            for step in sequence:
                if step[0] == "delegate":
                    brief = await tree.delegate(
                        conn,
                        build_request(
                            None,
                            "Scribe",
                            space=SPACE,
                            budget=step[1],
                            max_data_class="OPERATOR",
                        ),
                        issuer=frozenset(),
                        parent_brief="turn-1",
                    )
                    briefs.append((brief.id, step[1]))
                    model[brief.id] = dict(consumed=0, overrun=0)
                elif not briefs:
                    continue
                elif step[0] == "consume":
                    brief_id, budget = briefs[step[1] % len(briefs)]
                    m = model[brief_id]
                    left = max(budget - m["consumed"], 0)
                    got = await tree.consume(
                        conn, brief_id, Budget(usd_micros=step[2]), incurred=True
                    )
                    m["consumed"] += step[2]
                    m["overrun"] += max(step[2] - left, 0)
                    assert got == Budget(usd_micros=max(left - step[2], 0))
                else:
                    brief_id, _ = briefs[step[1] % len(briefs)]
                    cur = await conn.execute(
                        ROOT_SCRIBE_ROWS, {"space": SPACE, "brief": brief_id}
                    )
                    before = await cur.fetchall()
                    assert await tree.release(conn, brief_id) == ZERO
                    cur = await conn.execute(
                        ROOT_SCRIBE_ROWS, {"space": SPACE, "brief": brief_id}
                    )
                    assert await cur.fetchall() == before
                for brief_id, budget in briefs:
                    cur = await conn.execute(
                        ROOT_SCRIBE_ROWS, {"space": SPACE, "brief": brief_id}
                    )
                    rows = await cur.fetchall()
                    # every row is under the Brief's own node and no other
                    assert all(r[0] == brief_id for r in rows), rows
                    assert [r[1:] for r in rows if r[2] == "grant"] == [
                        (None, "grant", budget)
                    ]
                    assert {r[2] for r in rows} <= {"grant", "consume"}
                    consumed = sum(r[3] for r in rows if r[2] == "consume")
                    events = await read_for(
                        conn, space_id=SPACE, key="brief_id", value=brief_id
                    )
                    shortfall = sum(
                        e.payload["shortfall"]["usd_micros"]
                        for e in events
                        if e.type == "budget.overrun"
                    )
                    m = model[brief_id]
                    assert (consumed, shortfall) == (m["consumed"], m["overrun"])
                    assert consumed <= budget + shortfall
                    assert await tree.remaining(conn, brief_id) == Budget(
                        usd_micros=max(budget - consumed, 0)
                    )
            await conn.commit()
        finally:
            await conn.close()

    run(go())


# ---------------------------------------------------------------------------
# The generation fence


fence_steps = st.lists(
    st.one_of(
        st.just(("delegate",)),
        st.just(("stop",)),
        st.tuples(st.just("check"), st.integers(0, 8)),
        st.tuples(st.just("confirm"), st.integers(0, 8)),
    ),
    max_size=14,
)


@settings(max_examples=40, **DB_SETTINGS)
@given(sequence=fence_steps)
def test_generation_fence(sequence):
    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn)
            issued: list = []  # (brief, generation at issue, stopped_by_now)
            live: list = []
            unconfirmed: list = []
            generation = 1
            for step in sequence:
                if step[0] == "delegate":
                    req = build_request(oid, "Scribe", space=SPACE, budget=10)
                    if unconfirmed:
                        with pytest.raises(tree.Unconfirmed):
                            await tree.delegate(
                                conn, req, issuer=frozenset(), parent_brief="t"
                            )
                    else:
                        brief = await tree.delegate(
                            conn, req, issuer=frozenset(), parent_brief="t"
                        )
                        assert brief.generation == generation
                        issued.append(brief)
                        live.append(brief.id)
                elif step[0] == "stop":
                    stopped = await tree.stop(conn, oid, "stopped_by_person")
                    assert set(stopped) == set(live)
                    generation += len(stopped)
                    unconfirmed.extend(stopped)
                    live.clear()
                elif step[0] == "confirm":
                    if not issued:
                        continue
                    brief = issued[step[1] % len(issued)]
                    if brief.id in unconfirmed:
                        await tree.confirm_stop(conn, brief.id, receipt())
                        unconfirmed.remove(brief.id)
                    else:
                        with pytest.raises(tree.NotLive):
                            await tree.confirm_stop(conn, brief.id, receipt())
                else:
                    if not issued:
                        continue
                    brief = issued[step[1] % len(issued)]
                    if brief.generation < generation:
                        with pytest.raises(tree.StaleGeneration):
                            await tree.check_generation(conn, token_of(brief))
                    else:
                        await tree.check_generation(conn, token_of(brief))
                node = await tree.project(conn, oid)
                assert node.generation == generation
                events = await read_for(
                    conn, space_id=SPACE, key="objective_id", value=oid
                )
                assert generation == 1 + sum(
                    1 for e in events if e.type == "brief.stopped"
                )
            await conn.commit()
        finally:
            await conn.close()

    run(go())
