"""Tom's work before a routine's: a foreground task waiting for the turn slot
preempts a background turn or check, which writes no verdict and runs again.
Real Postgres, the scripted harness, real processes. No model call."""

import asyncio
import contextvars
import json
import os
from pathlib import Path

import pytest

from core import db, ledger, machine, slot, tasks
from core.gateway import Gateway
from core.machine import State
from tests import scripted
from tests.bridges import rows
from tests.ports import listen
from tests.test_checks import _marked, _wait, drive, runners, to_candidate
from tests.test_checks import rows as check_rows
from tests.test_objective_tree import root, run
from tests.test_serve import gateway_for, only, settled, tick, until

pytestmark = pytest.mark.spend(usd=0)


async def wait_for(check, timeout: float = 30):
    async def got():
        return await check()

    return await until(got, timeout, every=0.05)


def test_background_work_is_a_routine_or_a_replay_and_what_is_under_either(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            plain = await root(conn)
            routine = await root(conn, routine="r")
            replay = await root(conn, replay=True)
            under = await tasks.start_child(conn, routine, instruction="under")
            deeper = await tasks.start_child(conn, under, instruction="deeper")
            beside = await tasks.start_child(conn, plain, instruction="beside")
            return [
                await tasks.background(conn, t) for t in (plain, routine, replay, under, deeper, beside)
            ] + [await tasks.background(conn, "no-such-task")]

    assert run(go()) == [False, True, True, True, True, False, False]


def test_a_foreground_waiter_preempts_a_background_holder_which_then_gives_the_slot_up(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            bg, fg = await root(conn, routine="r"), await root(conn)
        events = []
        async with slot.held(bg, dsn):
            moved = slot.preempting()
            assert moved is not None and not moved.is_set()

            async def foreground():
                async with slot.held(fg, dsn):
                    events.append("fg held")
                    assert slot.preempting() is None  # Tom's work is never preempted

            waiting = asyncio.create_task(
                foreground(), context=contextvars.Context()
            )  # another task's context
            await asyncio.wait_for(moved.wait(), 10)
            events.append("bg told")
            assert not waiting.done()
        await asyncio.wait_for(waiting, 10)
        return events

    assert run(go()) == ["bg told", "fg held"]


def test_a_notice_sent_before_the_background_holder_listened_is_found_in_the_shared_lock(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            bg = await root(conn, routine="r")
        waiter = await db.connect(dsn)
        await waiter.execute(
            "SELECT pg_advisory_lock_shared(hashtextextended(%s, 0))", (slot.foreground_key(),)
        )
        try:
            async with slot.held(bg, dsn):
                await asyncio.wait_for(slot.preempting().wait(), 10)
        finally:
            await waiter.close()

    run(go())


def test_a_replay_run_by_hand_sends_no_notice_and_a_reentrant_hold_sends_none(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            replay, fg = await root(conn, replay=True), await root(conn)
        listener = await db.connect(dsn)
        await listener.execute(f"LISTEN {slot.PREEMPT_CHANNEL}")
        got = []

        async def hear():
            async for note in listener.notifies(timeout=1.5):
                got.append(note)

        hearing = asyncio.create_task(hear())
        async with slot.held(replay, dsn), slot.held(fg, dsn):  # inner: reentrant, does nothing
            pass
        await hearing
        await listener.close()
        return got

    assert run(go()) == []


def test_a_foreground_hold_announces_itself(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            fg = await root(conn)
        listener = await db.connect(dsn)
        await listener.execute(f"LISTEN {slot.PREEMPT_CHANNEL}")
        async with slot.held(fg, dsn):
            pass
        async for note in listener.notifies(timeout=5):
            await listener.close()
            return note.channel
        await listener.close()

    assert run(go()) == slot.PREEMPT_CHANNEL


@pytest.mark.macos  # the fresh critique runner, as every other test that drives it
def test_a_background_critique_preempted_for_tom_reruns_with_no_verdict(dsn, tmp_path):
    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path, brief_kw={"routine": "r"})
        ws = Path(b.workspace)
        scripted.steer(ws)
        out = await _drive(dsn, task, scripted.RUNNERS)
        assert out["missing"] == ["critique"], out
        scripted.steer(ws, fresh_acts=["hang", "sound"])
        async with await db.connect(dsn) as conn:
            fg = await root(conn)
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        try:
            running = asyncio.create_task(scripted.route(gateway, task, scripted.fresh_runners(ws), dsn=dsn))

            async def turning():
                return any(
                    r["type"] == "turn.started" and r["payload"].get("fresh") for r in await rows(dsn, task)
                )

            await wait_for(turning)

            async def acted():  # the stub has taken its act, so a rerun takes the next
                return json.loads((ws / ".git" / "valor-script.json").read_text())["fresh_acts"] == ["sound"]

            await wait_for(acted)

            async def preempted():
                return any(
                    r["type"] == "turn.ended" and r["payload"]["outcome"] == "preempted"
                    for r in await rows(dsn, task)
                )

            async with slot.held(fg, dsn):  # `router.run` takes the step again once Tom's work is done
                await wait_for(preempted, 60)
                held = await rows(dsn, task)
            assert not [r for r in held if r["type"] == "critique.decided"]
            ended = [r["payload"] for r in held if r["type"] == "turn.ended"][-1]
            assert ended["outcome"] == "preempted" and ended["result"] == {}
            out = await asyncio.wait_for(running, 120)
            return [r["type"] for r in await rows(dsn, task)] + [str(out)]
        finally:
            await gateway.close()

    types = run(go())
    assert types.count("critique.decided") == 1, " ".join(types[-14:])


async def _drive(dsn, task, runners_):
    gateway = Gateway(dsn)
    await gateway.start(port=listen())
    try:
        return await scripted.route(gateway, task, runners_, dsn=dsn)
    finally:
        await gateway.close()


@pytest.mark.macos  # the test runner runs the suite under sandbox-exec
def test_a_background_check_preempted_for_tom_is_cancelled_with_no_verdict_and_runs_again(dsn, tmp_path):
    files = {"suite.sh": "exit 0\n"}

    async def go():
        task, _b, ws = await to_candidate(
            dsn,
            tmp_path,
            files=files,
            writes={"suite.sh": "sleep 300 &\nsleep 300\n"},
            suite="/bin/bash suite.sh",
            brief_kw={"routine": "r"},
        )
        async with await db.connect(dsn) as conn:
            fg = await root(conn)
        running = asyncio.create_task(drive(dsn, task, runners(ws)))
        mark = f"test-{task}-head"
        assert await asyncio.to_thread(_wait, lambda: len(_marked(mark)) >= 2), "the suite never started"
        before = _marked(mark)
        async with slot.held(fg, dsn):
            assert await asyncio.to_thread(_wait, lambda: not _marked(mark), 30), _marked(mark)
            assert not [r for r in await check_rows(dsn, task) if r["type"] == "test.decided"]
            assert not running.done()
        for pid in before:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        # The step runs again once Tom's work is done: the suite starts anew.
        assert await asyncio.to_thread(_wait, lambda: len(_marked(mark)) >= 2), "it did not run again"
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test", by="test")
        assert (await running)["status"] == "stopped"

    run(go())


def test_the_kernel_runs_tom_first_and_a_background_turn_gives_way_and_resumes(tmp_path):
    fresh = db.migrate(f"{os.environ.get('VALOR_TEST_DB', 'valor_rebuild_test')}_prio", fresh=True)
    from core import serve

    spaces = {n: scripted.workspace(tmp_path / n)[0] for n in ("bg", "fg")}
    scripted.steer(spaces["bg"], sleep_once=30)

    async def go():
        bg = await scripted.start(fresh, spaces["bg"], routine="r")
        active = [bg]
        gateway = await gateway_for(fresh)
        kernel = only(serve.Kernel(gateway, scripted.RUNNERS, None, fresh), bg)

        async def now_active(conn):
            return list(active)

        kernel.active = now_active
        try:
            await tick(kernel, fresh)

            async def bg_turning():
                return [r for r in await rows(fresh, bg) if r["type"] == "turn.started"]

            await wait_for(bg_turning)
            assert kernel.background is not None
            fg = await scripted.start(fresh, spaces["fg"])
            active.append(fg)

            async def both_planned():
                await tick(kernel, fresh)
                return all([machine.fold(await rows(fresh, t)).state is State.CRITIQUE for t in (bg, fg)])

            await until(both_planned, 120, every=0.2)
            await settled(kernel)
            return bg, fg, await rows(fresh, bg), await rows(fresh, fg)
        finally:
            await gateway.close()

    bg_rows, fg_rows = run(go())[2:]
    ended = [r["payload"]["outcome"] for r in bg_rows if r["type"] == "turn.ended"]
    assert ended[0] == "preempted" and ended[-1] == "done"
    bg_start = [r for r in bg_rows if r["type"] == "turn.started"]
    assert len(bg_start) == 2
    first_fg_turn = next(r for r in fg_rows if r["type"] == "turn.started")
    preempted = next(r for r in bg_rows if r["type"] == "turn.ended")
    assert preempted["id"] < first_fg_turn["id"]


def test_the_emulator_report_counts_the_preempted_turns(dsn):
    from routines.emulator import runner

    async def go():
        async with await db.connect(dsn) as conn:
            t = await root(conn, routine="r")
            for outcome in ("preempted", "done", "preempted"):
                await ledger.append(
                    conn, t, "turn.ended", {"turn_id": ledger.new_id(), "outcome": outcome, "result": {}}
                )
            return await runner._preempted(conn, t), await runner._preempted(conn, None)

    assert run(go()) == (2, 0)


def test_a_stop_of_the_tree_beside_a_preempting_step_deadlocks_nowhere(dsn):
    """The order is the slot's session locks (the foreground notice, then the
    slot), then `tree:<root>`, then `task:<id>`, in transactions that never
    wait for a session lock. A stop of the tree, a new child, and a foreground
    waiter that preempts the background holder all run at once, many times."""

    async def once():
        async with await db.connect(dsn) as conn:
            top = await root(conn, routine="r")
            under = await tasks.start_child(conn, top, instruction="under")
            fg = await root(conn)
        events = []

        async def background():
            async with slot.held(under, dsn):
                async with await db.connect(dsn) as conn, conn.transaction():
                    await tasks.lock_tree(conn, under)  # a step's writers, inside its hold
                    await ledger.lock(conn, f"task:{under}")
                await asyncio.wait_for(slot.preempting().wait(), 30)
                events.append("bg preempted")

        async def foreground():
            await asyncio.sleep(0.05)
            async with slot.held(fg, dsn):
                events.append("fg held")

        async def stopping():
            await asyncio.sleep(0.05)
            async with await db.connect(dsn) as conn:
                await tasks.stop_tree(conn, top, reason="test", by="test")

        async def starting():
            await asyncio.sleep(0.05)
            async with await db.connect(dsn) as conn:
                try:
                    await tasks.start_child(conn, top, instruction="late")
                except Exception as exc:  # noqa: BLE001  a stopped tree refusing is the rule, not a deadlock
                    assert "eadlock" not in str(exc), exc

        await asyncio.wait_for(asyncio.gather(background(), foreground(), stopping(), starting()), 60)
        return events

    for _ in range(5):
        assert run(once()) == ["bg preempted", "fg held"]
