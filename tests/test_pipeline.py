"""The pipeline end to end on real Postgres and real git, with scripted
turns (`tests/scripted.py`): a task built, its checks passed, merged onto
its origin; and a red test sent to patch, the patched candidate merged.

No model call. Live spend: none.
"""

import asyncio
import json

import pytest

from core import (
    broker,
    db,
    ledger,
    machine,
    router,
    session,
)
from core.gateway import Gateway
from core.machine import State
from tests import scripted
from tests.ports import listen
from tests.scripted import git

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


async def drive(dsn, task, runners=None) -> dict:
    gateway = Gateway(dsn)
    await gateway.start(port=listen())
    try:
        return await scripted.route(gateway, task, runners or scripted.RUNNERS, dsn=dsn)
    finally:
        await gateway.close()


async def to_checks(dsn, ws, **kw) -> str:
    """A task with a candidate waiting on its three checks."""
    task = await scripted.start(dsn, ws, **kw)
    await drive(dsn, task)
    await scripted.critique(dsn, task)
    out = await drive(dsn, task)
    assert out["missing"] == ["test", "review", "docs"], out
    return task


async def rows(dsn, task) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task)


async def fold(dsn, task) -> machine.Fold:
    return machine.fold(await rows(dsn, task))


async def merged(dsn, task) -> broker.Outcome:
    async with await db.connect(dsn) as conn:
        return await scripted.merged(conn, task)


def test_a_passing_candidate_merges_onto_the_recorded_origin(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        return await merged(dsn, task), await fold(dsn, task)

    done, f = run(go())
    assert done.kind == "done" and git(origin, "rev-parse", "main") == f.candidate.sha
    assert f.state is State.MERGED


def test_a_red_test_goes_to_patch_and_the_patched_candidate_merges(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        first = (await fold(dsn, task)).candidate
        await scripted.checks(dsn, task, test="red")  # join row 3: the plan's round
        patching = await fold(dsn, task)
        await drive(dsn, task)
        await scripted.checks(dsn, task)
        return first, patching, await merged(dsn, task), await fold(dsn, task)

    first, patching, done, f = run(go())
    assert patching.state is State.PATCH and patching.join.row == 3
    assert f.candidate != first and f.state is State.MERGED
    assert done.kind == "done" and git(origin, "rev-parse", "main") == f.candidate.sha


# -- an idle turn ends its step; stop.md hands in from any working state -------------


async def at(dsn, ws, stage: str) -> str:
    task = await scripted.start(dsn, ws, judge="thin" if stage == "clarify" else "precise")
    if stage == "build":
        scripted.steer(ws)
        await drive(dsn, task)
        await scripted.critique(dsn, task)
    assert (await fold(dsn, task)).state.value == stage
    return task


@pytest.mark.parametrize("stage", ["clarify", "plan", "build"])
def test_stop_ends_the_work_from_clarify_plan_and_build_and_done_says_so(dsn, tmp_path, stage):
    from core import notices

    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await at(dsn, ws, stage)
        scripted.steer(ws, **{stage: "stop"}, note="Done: sent the greeting.")
        out = await drive(dsn, task)
        return out, await fold(dsn, task)

    out, f = run(go())
    assert out["status"] == "ended" and f.state is State.ENDED
    assert f.entry["payload"] == {**f.entry["payload"], "kind": "decided", "by": "valor"}
    assert notices.ended_text("t", f, None).startswith("This is done; nothing needed merging.\n")
    f.entry["payload"]["text"] = "Not worth it."
    assert notices.ended_text("t", f, None).startswith("I stopped this work without merging it.")


def test_a_stop_beside_a_question_lets_the_question_count(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await at(dsn, ws, "clarify")
        scripted.steer(ws, clarify="stop", beside="question.md")
        await drive(dsn, task)
        return await rows(dsn, task)

    written = run(go())
    (collected,) = [r["payload"] for r in written if r["type"] == "turn.collected"]
    assert collected["verdict"] == "asked" and session.STOP_BESIDE in collected["errors"]
    assert machine.fold(written).state is State.WAITING


def test_an_idle_turn_ends_its_step_and_the_next_step_continues(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await at(dsn, ws, "plan")
        scripted.steer(ws, plan="nothing", turns=1)
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        try:
            one = await router.step(gateway, task, scripted.RUNNERS, dsn, scripted.performers)
        finally:
            await gateway.close()
        rest = await drive(dsn, task)  # `python -m core run` steps on after an idle one
        return one, rest

    one, rest = run(go())
    turns = [json.loads(x) for x in (ws / ".git" / "valor-turns.jsonl").read_text().splitlines()]
    assert one["status"] == "idle" and one["state"]["state"] == "plan"
    assert rest["missing"] == ["critique"] and [t["prompt"] for t in turns][-1] == "Continue."


def test_an_idle_turn_that_asked_the_advisor_goes_on_in_the_same_run(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    asked = []

    async def advise(gateway, task, dsn_, alive, question):
        asked.append(question["advice"])
        async with await db.connect(dsn_) as conn:
            await ledger.append(conn, task, session.ADVICE_GIVEN,
                                {"asked_turn_id": question["turn_id"], "answer": "Yes.", "seat": "s"})  # fmt: skip
        return {"status": "advised"}

    async def working(ctx):
        return await session.run(ctx.gateway, ctx.task_id, scripted.turn_for, dsn=ctx.dsn,
                                 alive=ctx.alive, performers=ctx.performers, advise=advise)  # fmt: skip

    async def go():
        task = await at(dsn, ws, "plan")
        scripted.steer(ws, advise=[{"text": "Should I?", "act": "nothing"}])
        return await drive(dsn, task, {**scripted.RUNNERS, State.PLAN: working})

    out = run(go())
    assert asked == ["Should I?"] and out["missing"] == ["critique"]
