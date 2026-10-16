"""The resident kernel on real Postgres: it runs a task's scripted turns,
recovers a kill mid-turn, collects a turn ended before its collect once, runs
one step per event, stops a turn under it, and leaves serve on a restart. Each
test has a database of its own; the kill test SIGKILLs `tests/kernel_child.py`.
"""

import asyncio
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import core.gateway
from core import broker, db, ledger, machine, notices, rollout, router, runs, serve, session, signals, tasks
from core.__main__ import _performers
from core.machine import State
from tests import bridges, scripted
from tests.bridges import OPERATOR, OPERATOR_CHAT, new_task, rows, typed
from tests.scripted import commit

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent


run = asyncio.run


@pytest.fixture
def op(tmp_path):
    with bridges.operator(tmp_path) as s:
        yield s


async def until(check, timeout: float = 60, every: float = 0.1):
    end = time.monotonic() + timeout
    while True:
        got = await check()
        if got:
            return got
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting on {check.__name__}")
        await asyncio.sleep(every)


async def state(dsn, task) -> State:
    return machine.fold(await rows(dsn, task)).state


def only(kernel: serve.Kernel, *tasks_: str) -> serve.Kernel:
    async def active(conn):
        return list(tasks_)

    kernel.active = active
    return kernel


async def tick(kernel: serve.Kernel, dsn: str) -> None:
    async with await db.connect(dsn) as conn:
        await kernel.tick(conn)


async def settled(kernel: serve.Kernel) -> None:
    while kernel.jobs:
        await asyncio.gather(*list(kernel.jobs.values()), return_exceptions=True)


async def ticks(kernel: serve.Kernel, dsn: str, n: int) -> None:
    for _ in range(n):
        await tick(kernel, dsn)
        await settled(kernel)


def send(text: str) -> dict:
    return {"action_type": "telegram.send_message", "target": OPERATOR_CHAT, "payload": {"text": text}}


# -- a kill mid-turn ----------------------------------------------------------------


def kernel_child(tmp_path, dsn, runners, log) -> subprocess.Popen:
    env = {
        **os.environ, "KERNEL_DSN": dsn, "KERNEL_RUNNERS": runners,
        "KERNEL_PIDFILE": str(tmp_path / "sleeper.pid"), "VALOR_MACHINE": "m-test",
        "VALOR_DEFAULT_MACHINE": "m-test", "VALOR_SERVE_TICK_S": "0.2",
        "VALOR_OPERATOR_TELEGRAM_ID": OPERATOR, "VALOR_OPERATOR_CHAT": OPERATOR_CHAT,
        "VALOR_PROJECTS": str(tmp_path / "projects"), "VALOR_WORK": str(tmp_path / "work"),
    }  # fmt: skip
    return subprocess.Popen(
        [sys.executable, "-m", "tests.kernel_child"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )


def test_kill_mid_turn(fresh, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    task = run(scripted.start(fresh, ws))
    pidfile = tmp_path / "sleeper.pid"

    async def call_open():
        return typed(await rows(fresh, task), "gateway.opened", route="gateway") and pidfile.exists()

    async def critiqued():
        return await state(fresh, task) is State.CRITIQUE

    with (tmp_path / "kernel.log").open("w") as log:
        kernel = kernel_child(tmp_path, fresh, "hang", log)
        try:
            run(until(call_open, 60))
            os.kill(kernel.pid, signal.SIGKILL)
            kernel.wait()
            sleeper = int(pidfile.read_text())
            assert runs.alive(sleeper)
            kernel = kernel_child(tmp_path, fresh, "scripted", log)
            run(until(critiqued, 90))
        finally:
            kernel.kill()
            kernel.wait()

    written = run(rows(fresh, task))
    (opened,) = typed(written, "gateway.opened", route="gateway")
    (charged,) = typed(written, "gateway.charged", call_id=opened["call_id"])
    assert charged["estimated"] is True and charged["usd_micros"] == opened["estimate_usd_micros"]
    started = typed(written, "turn.started")
    first = typed(written, "turn.ended", turn_id=started[0]["turn_id"])
    assert first[0]["outcome"] == "interrupted" and first[0]["reason"] == "kernel restarted"
    assert not runs.alive(sleeper)
    assert started[0]["brief_sha256"] == started[1]["brief_sha256"]

    async def audit():
        async with await db.connect(fresh) as conn:
            return tasks.audit(await tasks.status(conn, task))

    assert run(audit()) == []


def _plan_signals(ws: Path, turn: str, *effects: str) -> Path:
    commit(ws, "docs/plan.md", "plan\n", "Plan")
    v = ws / ".valor"
    (v / "effects").mkdir(parents=True, exist_ok=True)
    plan = {"path": "docs/plan.md", "stakes": "a toy change", "scope": []}
    plan.update(critique_rounds=0, review_rounds=1)
    (v / "plan.json").write_text(json.dumps(plan))
    for name in effects:
        (v / "effects" / name).write_text(json.dumps(send(name)))
    return v


async def _ended(dsn, task, turn, state_) -> None:
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "turn.started", {"turn_id": turn, "state": state_})
        ended = {"turn_id": turn, "outcome": "done", "returncode": 0, "result": {}}
        await ledger.append(conn, task, "turn.ended", ended)


def test_kill_after_ended_before_collected(fresh, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(fresh, ws)
        turn = ledger.new_id()
        await _ended(fresh, task, turn, "plan")
        _plan_signals(ws, turn, "a.json")
        signals.collect(ws, turn)
        async with await db.connect(fresh) as conn:
            b = await tasks.brief(conn, task)
            # The effect was requested before the kill.
            action = broker.Action(**send("a.json"))
            await broker.request(conn, _performers(b), task, action, request_id=f"{turn}/a.json")
            done = await serve.recover(conn, _performers)
            again = await serve.recover(conn, _performers)
        return task, turn, done, again

    task, turn, done, again = run(go())
    written = run(rows(fresh, task))
    assert done["recollected"] == [turn] and again["recollected"] == []
    assert len(typed(written, "turn.collected", turn_id=turn)) == 1
    assert len(typed(written, "effect.held", request_id=f"{turn}/a.json")) == 1
    assert machine.fold(written).state is State.CRITIQUE


# -- scheduling ---------------------------------------------------------------------------


def test_one_step_per_event(fresh, op):
    calls = []

    async def counting(ctx):
        calls.append(ctx.task_id)
        if len(calls) == 1:
            async with await db.connect(ctx.dsn) as conn:
                await ledger.append(conn, ctx.task_id, "test.moved", {})
            return {"status": "moved"}
        return {"status": "idle"}

    async def go():
        task = await new_task(fresh)
        kernel = only(serve.Kernel(None, {State.JUDGE: counting}, None, fresh), task)
        await ticks(kernel, fresh, 5)
        after_moved = len(calls)
        async with await db.connect(fresh) as conn:
            await notices.request(conn, task, kind="test", about_key="test:quiet", text="quiet")
        await ticks(kernel, fresh, 3)
        after_quiet = len(calls)
        async with await db.connect(fresh) as conn:
            await ledger.append(conn, task, "test.poke", {})
        await ticks(kernel, fresh, 3)
        return after_moved, after_quiet, len(calls)

    assert run(go()) == (2, 2, 3)


def test_an_idle_task_gives_the_slot_to_other_ready_work(fresh, op, monkeypatch):
    """The incident's shape: one turn slot, a task whose turns go idle, and
    another task ready meanwhile."""
    monkeypatch.setattr(serve, "settings", dataclasses.replace(serve.settings, max_turns=1))
    calls = []

    async def idling(ctx):
        calls.append(ctx.task_id)
        async with await db.connect(ctx.dsn) as conn:
            await ledger.append(conn, ctx.task_id, "test.turn", {})  # the turn's own rows
        return {"status": "idle"}

    async def go():
        a = await new_task(fresh)
        await scripted.run_judge(fresh, a, "precise")
        kernel = only(serve.Kernel(None, {State.PLAN: idling}, None, fresh), a)
        await ticks(kernel, fresh, 2)  # alone, it is stepped again on every wake: no bound
        b = await new_task(fresh)
        await scripted.run_judge(fresh, b, "precise")
        only(kernel, a, b)
        await ticks(kernel, fresh, 4)
        return a, b

    a, b = run(go())
    # b arrived after a's last turn: b goes ahead of the idle a, then they alternate.
    assert calls == [a, a, b, a, b, a]


def test_stop_mid_turn_under_serve(fresh, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    scripted.steer(ws, sleep_once=30)

    async def go():
        task = await scripted.start(fresh, ws)
        gateway = await bridges.gateway(fresh)
        kernel = only(serve.Kernel(gateway, scripted.RUNNERS, None, fresh), task)
        try:
            await tick(kernel, fresh)

            async def turning():
                return typed(await rows(fresh, task), "turn.started")

            await until(turning)
            began = time.monotonic()
            async with await db.connect(fresh) as conn:
                await tasks.stop(conn, task, reason="test")
            await asyncio.wait_for(settled(kernel), 20)
            took = time.monotonic() - began
            kernel.active = serve.Kernel.active.__get__(kernel)
            await tick(kernel, fresh)
            return task, took, set(kernel.jobs)
        finally:
            await kernel.close()
            await gateway.close()

    task, took, jobs = run(go())
    written = run(rows(fresh, task))
    (ended,) = typed(written, "turn.ended")
    assert ended["outcome"] == "stopped" and took < 15
    assert machine.fold(written).state is State.STOPPED and jobs == set()


def test_a_restart_leaves_serve_and_a_failed_wake_does_not(fresh, monkeypatch, capsys):
    ticks, closed = [], []

    async def tick(self, conn):
        ticks.append(conn)
        if len(ticks) == 1:
            raise RuntimeError("one wake failed")
        raise rollout.Restart("a" * 40)

    async def close(self):
        closed.append("kernel")

    class Stand:
        def __init__(self, *args, **kw):
            pass

        async def start(self):
            pass

        async def close(self):
            closed.append("gateway")

    for where, name, stand_in in ((serve.Kernel, "tick", tick), (serve.Kernel, "close", close),
                                  (serve, "Gateway", Stand), (core.gateway, "ClaudeLogin", Stand),
                                  (core.gateway, "OpenAIKey", Stand)):  # fmt: skip
        monkeypatch.setattr(where, name, stand_in)
    monkeypatch.setattr(serve, "settings", dataclasses.replace(serve.settings, serve_tick_s=0.05))
    with pytest.raises(rollout.Restart):
        run(serve.serve({}, dsn=fresh, checkout=None))
    assert len(ticks) == 2 and closed == ["kernel", "gateway"]
    assert "kernel wake failed: RuntimeError('one wake failed')" in capsys.readouterr().err


# -- queue order (docs/plans/ask-reconciliation.md) ---------------------------------


def test_ready_work_runs_foreground_first_then_in_start_order_moved_by_placements(fresh, op, monkeypatch):
    calls = []

    async def counting(ctx):
        calls.append(ctx.task_id)
        return {"status": "waiting"}  # neither moved nor idle: stepped again only on a new row

    async def go():
        a = await new_task(fresh, routine="r", origin_url="/repos/alpha.git")
        b = await new_task(fresh, routine="r", origin_url="/repos/beta.git")
        c = await new_task(fresh, routine="r", origin_url="/repos/alpha.git")
        tom = await new_task(fresh)
        for t in (a, b, c, tom):
            await scripted.run_judge(fresh, t, "precise")
        async with await db.connect(fresh) as conn:
            await session.shape(conn, a, after=c, by="valor")
        monkeypatch.setattr(serve, "settings", dataclasses.replace(serve.settings, max_turns=1))
        kernel = only(serve.Kernel(None, {State.PLAN: counting}, None, fresh), a, b, c, tom)
        await ticks(kernel, fresh, 4)
        ran = list(calls)
        # A label or a placement moves no task: nothing steps again.
        async with await db.connect(fresh) as conn:
            await session.shape(conn, b, confirm=True, before=a, by="valor")
        await ticks(kernel, fresh, 2)
        return ran, len(calls), (a, b, c, tom)

    ran, after, (a, b, c, tom) = run(go())
    assert ran == [tom, b, c, a] and after == 4


def test_a_placement_after_a_failed_step_keeps_the_failure_and_its_backoff():
    at = datetime.now(UTC)
    rows_ = [
        {"id": 1, "type": "task.started", "payload": {"sdlc": 1}, "at": at},
        {"id": 2, "type": "step.failed", "payload": {"reason": "x"}, "at": at},
        {"id": 3, "type": "task.placed", "payload": {"anchor": "t", "where": "before"}, "at": at},
    ]
    assert not router.retry_due(rows_, now=at) and router.retry_due(rows_, now=at + timedelta(days=1))
    assert tasks.failed_step(rows_) is not None


def test_a_finished_tasks_workspace_is_removed_a_day_after_it_finished(dsn, tmp_path):
    from core import workspace

    async def go():
        async with await db.connect(dsn) as conn:
            (tmp_path / "w" / "kernel.git").mkdir(parents=True)
            task = await tasks.start(
                conn, tasks.Brief(instruction="x", mirror=str(tmp_path / "w" / "kernel.git"))
            )
            running = await workspace.reclaim(conn, task, by="t", via="t")
            await tasks.stop(conn, task, reason="done")
            day = datetime.now(UTC) - timedelta(seconds=serve.RECLAIM_AFTER_S)
            fresh = await workspace.reclaim(conn, task, by="t", via="t", before=day)
            return (
                running,
                fresh,
                await workspace.reclaim(conn, task, by="t", via="t", before=day + timedelta(days=2)),
            )

    assert run(go()) == (False, False, True) and not (tmp_path / "w").exists()
