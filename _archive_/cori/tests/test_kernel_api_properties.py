"""The door's invariants as Hypothesis properties against the local
database with the tree fakes bound. Plan 05, Properties. Sync tests drive
the async kernel on a private loop, as the tree properties do."""

import asyncio
import random
import uuid
from itertools import permutations
from typing import get_args

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import kernel.tree as tree
from kernel import api
from kernel.api import Door, Refused
from schemas.capability import Capability, CapabilityName, EffectClass
from schemas.records import ToolLogRecord
from schemas.sandbox import SandboxProfile, SandboxProfileName
from tests.conftest import dsn, requires_postgres
from tests.tree_fakes import (
    approval,
    bind,
    contract,
    make_space,
    receipt,
    request,
    token,
)

pytestmark = requires_postgres

SPACE = f"space-{uuid.uuid4().hex[:8]}"
OTHER = f"space-{uuid.uuid4().hex[:8]}"
SPACES = {
    SPACE: make_space(SPACE, max_effect_class="act"),
    OTHER: make_space(OTHER, max_effect_class="act"),
}
DB_SETTINGS = dict(
    deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
NAMES = get_args(CapabilityName)
CLASSES = get_args(EffectClass)
PROFILES = get_args(SandboxProfileName)
TOOLS = ("read", "write", "edit", "bash", "ask", "write_episode", "propose_belief")
EXECUTOR_TOOLS = ("read", "write", "edit", "bash", "ask")


@pytest.fixture(scope="module", autouse=True)
def bound():
    with pytest.MonkeyPatch.context() as mp:
        yield bind(mp, SPACES)


def run(coro):
    return asyncio.run(coro)


async def connect():
    return await psycopg.AsyncConnection.connect(dsn("kernel_rw"))


async def open_root(conn):
    oid = await tree.open_objective(
        conn, space=SPACE, conversation_id="c1", contract=contract()
    )
    await tree.approve(conn, oid, approval("approved", oid, 1))
    return oid


async def executor(conn, oid, names=("read", "write", "bash", "ask")):
    brief = await tree.delegate(
        conn,
        request(oid, "Executor", space=SPACE, names=names),
        issuer=frozenset(),
        parent_brief="turn-1",
    )
    await conn.commit()
    return brief


def row(brief, seq, event, tool="read", **over) -> ToolLogRecord:
    fields = dict(
        brief_id=brief.id,
        generation=brief.generation,
        space_id=brief.space,
        seq=seq,
        event=event,
        tool=tool,
        input={"seq": seq},
    )
    if event == "tool.end":
        fields["exit_status"] = 0
    fields.update(over)
    return ToolLogRecord(**fields)


def terminal_row(brief) -> ToolLogRecord:
    return ToolLogRecord(
        brief_id=brief.id,
        generation=brief.generation,
        space_id=brief.space,
        event="terminal",
        outcome="aborted",
    )


async def count_rows(conn, brief) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM tool_log WHERE brief_id = %s", (brief.id,)
    )
    return (await cur.fetchone())[0]


# --- Pairing is order-independent ------------------------------------------


@st.composite
def interleavings(draw):
    n = draw(st.integers(min_value=0, max_value=6))
    ended = [draw(st.booleans()) for _ in range(n)]
    with_terminal = draw(st.booleans())
    return n, ended, with_terminal


def _shuffles(n, ended, with_terminal, seed):
    """A few permutations of the multiset of rows, each obeying start
    before end per seq and the terminal last."""
    rng = random.Random(seed)
    base = [("start", s) for s in range(1, n + 1)] + [
        ("end", s) for s in range(1, n + 1) if ended[s - 1]
    ]
    out = []
    for _ in range(4):
        order = base[:]
        rng.shuffle(order)
        # Pull each end after its start.
        fixed = []
        pending = {}
        for kind, s in order:
            if kind == "start":
                fixed.append((kind, s))
                if s in pending:
                    fixed.append(("end", s))
            elif s in [k for _, k in fixed if _ == "start"]:
                fixed.append((kind, s))
            else:
                pending[s] = True
        if with_terminal:
            fixed.append(("terminal", None))
        out.append(fixed)
    return out


@given(interleavings(), st.integers(min_value=0, max_value=10_000))
@settings(max_examples=40, **DB_SETTINGS)
def test_invocations_pair_by_seq_under_any_interleaving(shape, seed):
    n, ended, with_terminal = shape

    async def go():
        conn = await connect()
        try:
            results = []
            for order in _shuffles(n, ended, with_terminal, seed):
                brief = await executor(conn, await open_root(conn))
                door = Door(connect)
                door.register(brief)
                for kind, s in order:
                    if kind == "start":
                        await door.record_tool(
                            token(brief), row(brief, s, "tool.start")
                        )
                    elif kind == "end":
                        await door.record_tool(token(brief), row(brief, s, "tool.end"))
                    else:
                        await door.record_tool(token(brief), terminal_row(brief))
                rows = await api.read_tool_log(conn, brief.id, brief.generation)
                invs = api.invocations(rows)
                assert len(invs) == n
                for inv in invs:
                    assert inv.start.event == "tool.start"
                    if inv.end is not None:
                        assert inv.end.seq == inv.start.seq
                    expect_end = ended[inv.start.seq - 1]
                    assert (inv.end is not None) == expect_end
                    assert inv.closed_by == (
                        "end" if expect_end else ("terminal" if with_terminal else None)
                    )
                results.append(
                    sorted((i.start.seq, i.end is not None, i.closed_by) for i in invs)
                )
            assert all(r == results[0] for r in results)
            await conn.commit()
        finally:
            await conn.close()

    run(go())


# --- Terminal closes everything and nothing follows -------------------------


row_shapes = st.lists(
    st.tuples(st.sampled_from(("start", "end")), st.integers(min_value=1, max_value=5)),
    max_size=10,
)


@given(row_shapes, row_shapes)
@settings(max_examples=40, **DB_SETTINGS)
def test_terminal_closes_open_seqs_and_refuses_later_rows(before, after):
    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn)
            brief = await executor(conn, oid)
            door = Door(connect)
            door.register(brief)
            started, ended = set(), set()
            for kind, s in before:
                if kind == "start" and s not in started:
                    await door.record_tool(token(brief), row(brief, s, "tool.start"))
                    started.add(s)
                elif kind == "end" and s in started and s not in ended:
                    await door.record_tool(token(brief), row(brief, s, "tool.end"))
                    ended.add(s)
            await door.record_tool(token(brief), terminal_row(brief))
            n = await count_rows(conn, brief)
            for kind, s in after + [("terminal", None)]:
                offered = (
                    terminal_row(brief)
                    if kind == "terminal"
                    else row(brief, s, f"tool.{kind}")
                )
                with pytest.raises(Refused):
                    await door.record_tool(token(brief), offered)
                with pytest.raises(Refused):
                    await door.raise_question(token(brief), "q?", s or 1)
            assert await count_rows(conn, brief) == n
            rows = await api.read_tool_log(conn, brief.id, brief.generation)
            for inv in api.invocations(rows):
                s = inv.start.seq
                if s in ended:
                    assert inv.closed_by == "end"
                else:
                    assert inv.end is None and inv.closed_by == "terminal"
            assert {i.start.seq for i in api.invocations(rows)} == started
            await conn.commit()
        finally:
            await conn.close()

    run(go())


# --- The generation fence writes nothing ------------------------------------


steps = st.lists(
    st.tuples(
        st.sampled_from(("record", "bump")), st.integers(min_value=0, max_value=3)
    ),
    max_size=12,
)


@given(steps)
@settings(max_examples=30, **DB_SETTINGS)
def test_stale_generation_records_nothing(sequence):
    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn)
            door = Door(connect)
            issued = [await executor(conn, oid)]
            door.register(issued[0])
            expected = {issued[0].id: 0}
            current = issued[0]
            seq = 0
            for kind, pick in sequence:
                if kind == "bump":
                    for b in await tree.stop(conn, oid, "stopped_by_person"):
                        await tree.confirm_stop(conn, b, receipt())
                    await conn.commit()
                    current = await executor(conn, oid)
                    door.register(current)
                    issued.append(current)
                    expected[current.id] = 0
                    seq = 0
                    continue
                brief = issued[pick % len(issued)]
                seq += 1
                before = await count_rows(conn, brief)
                if brief.generation < current.generation:
                    with pytest.raises(tree.StaleGeneration):
                        await door.record_tool(
                            token(brief), row(brief, seq, "tool.start")
                        )
                    with pytest.raises(tree.StaleGeneration):
                        await door.raise_question(token(brief), "q?", seq)
                    assert await count_rows(conn, brief) == before
                else:
                    await door.record_tool(token(brief), row(brief, seq, "tool.start"))
                    expected[brief.id] += 1
            for b in issued:
                assert await count_rows(conn, b) == expected[b.id]
            await conn.commit()
        finally:
            await conn.close()

    run(go())


# --- A row needs a covering capability --------------------------------------


capabilities = st.frozensets(
    st.builds(
        Capability,
        name=st.sampled_from(NAMES),
        effect_class=st.sampled_from(CLASSES),
        scope=st.sampled_from(
            (SPACE, OTHER, f"{SPACE}/src", f"{OTHER}/src", "space-z")
        ),
    ),
    max_size=6,
)


@given(
    capabilities,
    st.sampled_from(TOOLS),
    st.sampled_from(PROFILES),
    st.sampled_from((SPACE, OTHER)),
)
@settings(max_examples=40, **DB_SETTINGS)
def test_record_tool_accepts_iff_a_capability_covers_the_call(
    caps, tool, profile_name, brief_space
):
    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn)
            issued = await executor(conn, oid)
            profile = SandboxProfile(
                name=profile_name,
                space=brief_space,
                mount_source=issued.sandbox_profile.mount_source,
                readonly=profile_name != "worktree",
                network="hostonly",
                key=issued.sandbox_profile.key,
                env={},
            )
            brief = issued.model_copy(
                update={
                    "space": brief_space,
                    "capabilities": caps,
                    "sandbox_profile": profile,
                }
            )
            door = Door(connect)
            door.register(brief)
            needed = api.capability_for(brief, tool)
            assert needed.scope == brief_space
            assert (
                needed.effect_class
                == {
                    "read": "read",
                    "ask": "read",
                    "write": "propose",
                    "edit": "propose",
                    "write_episode": "propose",
                    "propose_belief": "propose",
                    "bash": "propose" if profile_name == "worktree" else "read",
                }[tool]
            )
            covered = any(c.covers(needed) for c in caps)
            offered = row(brief, 1, "tool.start", tool=tool)
            if covered:
                assert await door.record_tool(token(brief), offered) > 0
            else:
                with pytest.raises(Refused):
                    await door.record_tool(token(brief), offered)
            assert await count_rows(conn, brief) == int(covered)
            await conn.commit()
        finally:
            await conn.close()

    run(go())


# --- The door spends nothing -------------------------------------------------


accepted_rows = st.lists(
    st.tuples(st.sampled_from(EXECUTOR_TOOLS), st.booleans()), max_size=8
)


@given(accepted_rows, st.booleans())
@settings(max_examples=30, **DB_SETTINGS)
def test_record_tool_never_touches_the_ledger(shape, with_terminal):
    consumed = []
    real_consume = tree.consume

    async def spy(*a, **k):
        consumed.append((a, k))
        return await real_consume(*a, **k)

    async def go():
        conn = await connect()
        try:
            oid = await open_root(conn)
            brief = await executor(conn, oid)
            before = await tree.remaining(conn, brief.id)
            door = Door(connect)
            door.register(brief)
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(tree, "consume", spy)
                for seq, (tool, end) in enumerate(shape, start=1):
                    await door.record_tool(
                        token(brief), row(brief, seq, "tool.start", tool=tool)
                    )
                    if tool == "ask":
                        await door.raise_question(token(brief), "q?", seq)
                    if end:
                        await door.record_tool(
                            token(brief), row(brief, seq, "tool.end", tool=tool)
                        )
                if with_terminal:
                    await door.record_tool(token(brief), terminal_row(brief))
            assert consumed == []
            assert await tree.remaining(conn, brief.id) == before
            cur = await conn.execute(
                "SELECT count(*) FROM budget_ledger WHERE brief_id = %s", (brief.id,)
            )
            assert (await cur.fetchone())[0] == 1
            await conn.commit()
        finally:
            await conn.close()

    run(go())
