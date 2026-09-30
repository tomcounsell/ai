"""Every generation ends in exactly one terminal, as a Hypothesis property
over the real tree, events, and door with a scripted worker. Plan 05,
Properties. Sync tests drive the async kernel on a private loop, as the
door and tree properties do."""

import asyncio
import sys
import uuid

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import kernel.tree as tree
from kernel import runs
from kernel.api import Door
from schemas.trace import Terminal
from tests.conftest import dsn, requires_postgres
from tests.test_runs import FakeVerify, RecordingSandbox, ScriptedWorker
from tests.tree_fakes import (
    approval,
    bind,
    contract,
    executor_report,
    make_space,
    request,
    scribe_report,
)

pytestmark = requires_postgres

SPACE = f"space-{uuid.uuid4().hex[:8]}"
SPACES = {SPACE: make_space(SPACE, max_effect_class="act")}
DB_SETTINGS = dict(
    deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
NAMES = ("read", "write", "bash", "ask")
ENDINGS = ("report", "failed", "adapter_abort", "stop")


@pytest.fixture(scope="module", autouse=True)
def bound():
    with pytest.MonkeyPatch.context() as mp:
        bind(mp, SPACES)
        mp.setitem(sys.modules, "kernel.verify", FakeVerify([]))
        mp.setattr(runs, "connect", connect)
        yield mp


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def wire():
    """Fresh collaborators for one example; the module fixture restores the
    originals at the end."""
    door = Door(connect)
    sandbox = RecordingSandbox([])
    worker = ScriptedWorker(door)
    runs.sandbox = sandbox
    runs.worker = worker
    runs.door = door
    runs.live.clear()
    return worker


async def open_brief(conn, with_objective):
    if with_objective:
        oid = await tree.open_objective(
            conn, space=SPACE, conversation_id="c1", contract=contract()
        )
        await tree.approve(conn, oid, approval("approved", oid, 1))
        req = request(oid, "Executor", space=SPACE, names=NAMES)
    else:
        oid = None
        req = request(None, "Scribe", space=SPACE, names=("read", "ask"))
    brief = await tree.delegate(conn, req, issuer=frozenset(), parent_brief="turn-1")
    await conn.commit()
    return oid, brief


def rows(n):
    out = []
    for i in range(1, n + 1):
        out.append(("row", dict(event="tool.start", tool="read", input={"i": i})))
        if i % 2:
            out.append(
                ("row", dict(event="tool.end", seq=i, tool="read", exit_status=0))
            )
    return out


def ending(kind, with_objective):
    report = executor_report() if with_objective else scribe_report()
    if kind == "report":
        return [("terminal", Terminal(outcome="report", report=report, error=None))]
    if kind == "failed":
        return [("terminal", Terminal(outcome="failed", report=None, error="boom"))]
    return [("wait",)]


async def terminals(conn, brief_id):
    cur = await conn.execute(
        "SELECT generation, id, event FROM tool_log WHERE brief_id = %s ORDER BY id",
        (brief_id,),
    )
    return await cur.fetchall()


async def scenario(with_objective, n_rows, mid_ask, kind):
    worker = wire()
    async with await connect() as conn:
        oid, brief = await open_brief(conn, with_objective)
        steps = rows(n_rows)
        if mid_ask and kind in ("adapter_abort", "stop"):
            steps.append(("question", "which?"))
        steps += ending(kind, with_objective)
        worker.script(brief, steps)
        task = asyncio.create_task(runs.run_brief(brief))
        if kind in ("adapter_abort", "stop"):
            await asyncio.wait_for(
                worker.reached.setdefault(brief.id, asyncio.Event()).wait(), 5
            )
        if kind == "adapter_abort":
            await worker.abort(brief.id)
            terminal = await task
            assert terminal.outcome == "aborted"
        elif kind == "stop":
            if with_objective:
                stopped = await tree.stop(conn, oid, "stopped_by_person")
            else:
                await tree.stop_brief(conn, brief.id, "stopped_by_person")
                stopped = [brief.id]
            await conn.commit()
            receipts = await runs.stop(stopped)
            assert len(receipts) == 1
            assert (await task).outcome == "aborted"
        else:
            terminal = await task
            assert terminal.outcome == kind
        log = await terminals(conn, brief.id)
    assert log, "no rows at all"
    by_generation: dict[int, list] = {}
    for generation, id_, event in log:
        by_generation.setdefault(generation, []).append((id_, event))
    assert set(by_generation) == {brief.generation}
    for generation, entries in by_generation.items():
        kinds = [e for _, e in entries]
        assert kinds.count("terminal") == 1, kinds
        assert entries[-1][1] == "terminal", entries
    assert runs.live == {}


@given(
    with_objective=st.booleans(),
    n_rows=st.integers(min_value=0, max_value=4),
    mid_ask=st.booleans(),
    kind=st.sampled_from(ENDINGS),
)
@settings(max_examples=40, **DB_SETTINGS)
def test_every_generation_ends_in_exactly_one_terminal(
    with_objective, n_rows, mid_ask, kind
):
    asyncio.run(scenario(with_objective, n_rows, mid_ask, kind))
