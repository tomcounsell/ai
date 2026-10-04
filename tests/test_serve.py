"""The resident kernel on real Postgres: a kill at every point of a turn and
of an effect, recovery, the turn slot, services kept between steps, one
step per event, a stop under the kernel, a missed notification, the
kernel gateway's OpenAI key, and the same Brief from the same store in
any process.

Each test runs on a database of its own, since the kernel acts on every
task in it. The kill tests run the kernel as a process of its own
(`tests/kernel_child.py`) and SIGKILL it by its pid. No model call.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core import broker, db, ledger, machine, router, runs, serve, session, signals, slot, spending, tasks
from core import workspace as kws
from core.__main__ import _performers
from core.gateway import Gateway
from core.machine import State
from tests import bridges, scripted
from tests.bridges import OPERATOR, OPERATOR_CHAT, new_task, rows
from tests.conftest import TEST_DB
from tests.scripted import commit
from tests.test_pipeline import _dangling, drive
from tests.test_session import assert_large_turn, large_turn
from tests.test_workspace import _service_pids

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fresh() -> str:
    """A database of this test's own."""
    return db.migrate(f"{TEST_DB}_serve", fresh=True)


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


def typed(written, type_, **match) -> list[dict]:
    return [
        r["payload"]
        for r in written
        if r["type"] == type_ and all(r["payload"].get(k) == v for k, v in match.items())
    ]


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


async def gateway_for(dsn) -> Gateway:
    g = Gateway(dsn)
    await g.start()
    return g


def gone(pid: int) -> bool:
    out = subprocess.run(
        ["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout
    return out.strip() == "" or out.strip().startswith("Z")


def send(text: str) -> dict:
    return {"action_type": "telegram.send_message", "target": OPERATOR_CHAT, "payload": {"text": text}}


# -- a kill at every point ----------------------------------------------------------------


def kernel_child(tmp_path, dsn, runners, log) -> subprocess.Popen:
    env = {
        **os.environ,
        "KERNEL_DSN": dsn,
        "KERNEL_RUNNERS": runners,
        "KERNEL_PIDFILE": str(tmp_path / "sleeper.pid"),
        "VALOR_MACHINE": "m-test",
        "VALOR_DEFAULT_MACHINE": "m-test",
        "VALOR_SERVE_TICK_S": "0.2",
        "VALOR_OPERATOR_TELEGRAM_ID": OPERATOR,
        "VALOR_OPERATOR_CHAT": OPERATOR_CHAT,
        "VALOR_PROJECTS": str(tmp_path / "projects"),
        "VALOR_WORK": str(tmp_path / "work"),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "tests.kernel_child"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
    )


@pytest.mark.macos
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
            assert not gone(sleeper)
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
    assert gone(sleeper)
    assert started[0]["brief_sha256"] == started[1]["brief_sha256"]

    async def audit():
        async with await db.connect(fresh) as conn:
            return tasks.audit(await tasks.status(conn, task))

    assert run(audit()) == []


def _plan_signals(ws: Path, turn: str, *effects: str) -> Path:
    commit(ws, "docs/plan.md", "plan\n", "Plan")
    v = ws / ".valor"
    (v / "effects").mkdir(parents=True, exist_ok=True)
    plan = {
        "path": "docs/plan.md",
        "stakes": "a toy change",
        "critique_rounds": 0,
        "review_rounds": 1,
        "scope": [],
    }
    (v / "plan.json").write_text(json.dumps(plan))
    for name in effects:
        (v / "effects" / name).write_text(json.dumps(send(name)))
    return v


async def _ended(dsn, task, turn, state_) -> None:
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "turn.started", {"turn_id": turn, "state": state_})
        await ledger.append(
            conn, task, "turn.ended", {"turn_id": turn, "outcome": "done", "returncode": 0, "result": {}}
        )


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
            await broker.request(
                conn, _performers(b), task, broker.Action(**send("a.json")), request_id=f"{turn}/a.json"
            )
            done = await serve.recover(conn, _performers)
            again = await serve.recover(conn, _performers)
        return task, turn, done, again

    task, turn, done, again = run(go())
    written = run(rows(fresh, task))
    assert done["recollected"] == [turn] and again["recollected"] == []
    assert len(typed(written, "turn.collected", turn_id=turn)) == 1
    assert len(typed(written, "effect.held", request_id=f"{turn}/a.json")) == 1
    assert machine.fold(written).state is State.CRITIQUE


@pytest.mark.parametrize("field", ["action_type", "critique_rounds"])
def test_recover_collects_a_large_turn_field(fresh, op, tmp_path, field):
    """recover collects a turn left with a 90 MiB action type or a 140 MiB
    critique_rounds, the field refused and the clean send beside it held,
    and a second recover has nothing left."""
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(fresh, ws)
        turn = ledger.new_id()
        await _ended(fresh, task, turn, "plan")
        large_turn(ws, field)
        async with await bridges.connect(fresh) as conn:
            done = await serve.recover(conn, _performers)
            again = await serve.recover(conn, _performers)
        return task, turn, done, again

    task, turn, done, again = run(go())
    assert done["recollected"] == [turn] and again["recollected"] == []
    assert not done.get("uncollected") and not again.get("uncollected")
    assert_large_turn(field, turn, run(rows(fresh, task)))


def test_recover_reads_a_turns_signals_off_the_loop_thread(fresh, op, tmp_path, monkeypatch):
    """Reading a turn's files again runs in a worker thread, as a live
    turn's collection does, so the kernel's loop runs meanwhile."""
    ws, _ = scripted.workspace(tmp_path)
    read_on, recollect = [], signals.recollect

    def recording(*args):
        read_on.append(threading.current_thread() is threading.main_thread())
        return recollect(*args)

    monkeypatch.setattr(signals, "recollect", recording)

    async def go():
        task = await scripted.start(fresh, ws)
        turn = ledger.new_id()
        await _ended(fresh, task, turn, "plan")
        _plan_signals(ws, turn)
        async with await db.connect(fresh) as conn:
            return turn, await serve.recover(conn, _performers)

    turn, done = run(go())
    assert done["recollected"] == [turn] and read_on == [False]


def test_a_turn_that_cannot_be_collected_leaves_serve_running(fresh, op, tmp_path, monkeypatch):
    """A recover over a turn whose collection raises logs it and the kernel
    starts: another task is stepped. The turn is collected by a job on the
    task's next row once its collection can be made, before the task is
    stepped."""
    ws, _ = scripted.workspace(tmp_path)
    steps, broken = [], [True]
    record = session.record

    async def failing_record(*args, **kw):
        if broken[0]:
            raise RuntimeError("cannot collect")
        return await record(*args, **kw)

    async def judging(ctx):
        steps.append(ctx.task_id)
        return {"status": "idle"}

    async def collected(conn, task, turn):
        return typed(await ledger.read(conn, task), "turn.collected", turn_id=turn)

    async def go():
        task = await scripted.start(fresh, ws)
        turn = ledger.new_id()
        await _ended(fresh, task, turn, "plan")
        (ws / ".valor").mkdir(exist_ok=True)
        (ws / ".valor" / "question.md").write_text("which one?")
        other = await new_task(fresh)
        monkeypatch.setattr(session, "record", failing_record)
        gateway = await gateway_for(fresh)
        probe = await db.connect(fresh)
        with bridges.configure(serve_tick_s=3600):
            kernel = asyncio.create_task(serve.serve({State.JUDGE: judging}, dsn=fresh, gateway=gateway))
            try:

                async def stepped():
                    return other in steps

                await until(stepped, 30)
                running, before = not kernel.done(), await collected(probe, task, turn)
                broken[0] = False
                await ledger.append(probe, task, "test.poke", {})

                async def recollected():
                    return await collected(probe, task, turn)

                (after,) = await until(recollected, 30)
            finally:
                kernel.cancel()
                await asyncio.gather(kernel, return_exceptions=True)
                await probe.close()
                await gateway.close()
        return task, running, before, after

    _task, running, before, after = run(go())
    assert running and before == []
    assert after["verdict"] == "asked"


def test_live_call_not_charged(fresh, op):
    async def go():
        task = await new_task(fresh)
        holder = await db.connect(fresh)  # a `core run`, its call open
        await holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"run:{task}",))
        try:
            async with await db.connect(fresh) as conn:
                call = await spending.open_call(
                    conn,
                    task,
                    {"call_id": ledger.new_id(), "turn_id": "t", "model": "claude-haiku-4-5", "route": "gateway",
                     "estimated_input": 10, "max_tokens": 10, "estimate_usd_micros": 99,
                     "holder": f"run:{task}"},
                )  # fmt: skip
                first = await serve.recover(conn)
                await spending.charge(conn, task, call, 7, {"turn_id": "t"})
                second = await serve.recover(conn)
        finally:
            await holder.close()
        return task, call, first, second

    task, call, first, second = run(go())
    assert first["charged"] == [] and second["charged"] == []
    assert [c["usd_micros"] for c in typed(run(rows(fresh, task)), "gateway.charged", call_id=call)] == [7]


class Hanging:
    """A performer whose perform never returns, and whose lookup answers
    what the test says, by effect."""

    usage = None

    def __init__(self, action_type: str = "push_branch", effect_class: str = "act"):
        self.action_type, self.effect_class = action_type, effect_class
        self.started = asyncio.Event()
        self.answers: dict[str, object] = {}
        self.otherwise: object = None

    async def perform(self, action, key):
        self.started.set()
        await asyncio.Event().wait()

    async def lookup(self, action, key):
        found = self.answers.get(key.rsplit(":", 1)[1], self.otherwise)
        if isinstance(found, Exception):
            raise found
        return found


async def _killed(dsn, task, fake: Hanging, *, approve: bool) -> str:
    """Perform one push with `fake` and kill it after its intent: the
    perform's session closes, freeing its lock. Returns the effect."""
    conn = await db.connect(dsn)
    fakes = broker.Performers(fake)
    action = broker.Action("push_branch", "valor/feature", {"head_sha": "a" * 40})
    fake.started.clear()
    if approve:
        held = await broker.request(conn, fakes, task, action)
        await broker.approve(conn, held.effect_id, note="push it")
        job = asyncio.create_task(broker.release(conn, fakes, held.effect_id))
    else:
        job = asyncio.create_task(broker.request(conn, fakes, task, action))
    await fake.started.wait()
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    await conn.close()
    return typed(await rows(dsn, task), "effect.intent")[-1]["effect_id"]


def test_kill_between_intent_and_outcome(fresh, op):
    async def go():
        task = await new_task(fresh)
        fake = Hanging()
        landed = await _killed(fresh, task, fake, approve=True)
        missing = await _killed(fresh, task, fake, approve=True)
        built = lambda b: broker.Performers(fake)
        out = []
        async with await db.connect(fresh) as conn:
            fake.otherwise = broker.Unknown("the remote did not answer")
            out.append(await serve.recover(conn, built))
            fake.otherwise = None
            fake.answers[landed] = {"sha": "a" * 40}
            out.append(await serve.recover(conn, built))
        return task, landed, missing, out

    task, landed, missing, out = run(go())
    assert [o["reconciled"] for o in out] == [[], [landed, missing]]
    written = run(rows(fresh, task))
    assert [o["kind"] for o in typed(written, "effect.outcome", effect_id=landed)] == ["done"]
    assert [o["kind"] for o in typed(written, "effect.outcome", effect_id=missing)] == ["failed"]


@pytest.mark.macos
def test_merge_restarts_its_kernel(fresh, op, tmp_path):
    ws, origin = scripted.workspace(tmp_path)

    async def go():
        task, effect, sha = await _dangling(fresh, ws)
        scripted.git(ws, "push", "-q", str(origin), f"{sha}:refs/heads/main")
        async with await db.connect(fresh) as conn:
            done = await serve.recover(conn, _performers)
        return task, effect, done

    task, effect, done = run(go())
    assert done["reconciled"] == [effect]
    (outcome,) = typed(run(rows(fresh, task)), "effect.outcome", effect_id=effect)
    assert outcome["kind"] == "done" and outcome["reconciled"] is True


def test_dangling_propose_intent(fresh, op):
    async def go():
        task = await new_task(fresh)
        fake = Hanging(effect_class="propose")
        effect = await _killed(fresh, task, fake, approve=False)
        fake.otherwise = {"sha": "a" * 40}
        async with await db.connect(fresh) as conn:
            done = await serve.recover(conn, lambda b: broker.Performers(fake))
        return task, effect, done

    task, effect, done = run(go())
    written = run(rows(fresh, task))
    assert typed(written, "effect.held", effect_id=effect) == []
    (intent,) = typed(written, "effect.intent", effect_id=effect)
    assert intent["action_type"] == "push_branch" and intent["target"] == "valor/feature"
    assert intent["effect_class"] == "propose" and intent["approval_id"] is None
    assert done["reconciled"] == [effect]
    assert [o["kind"] for o in typed(written, "effect.outcome", effect_id=effect)] == ["done"]


@pytest.mark.macos
def test_recollect_mid_move(fresh, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(fresh, ws)
        await drive(fresh, task)
        await scripted.critique(fresh, task)
        assert await state(fresh, task) is State.BUILD
        turn = ledger.new_id()
        await _ended(fresh, task, turn, "build")
        commit(ws, "greeting.txt", "hello\n", "build")
        v = ws / ".valor"
        (v / "effects").mkdir(parents=True, exist_ok=True)
        (v / "done.md").write_text("build turn: greeting.txt, checked by reading it.")
        for name in ("a.json", "b.json"):
            (v / "effects" / name).write_text(json.dumps(send(name)))
        # The kill came between moves: some are in handled/, some not.
        handled = v / "handled" / turn
        (handled / "effects").mkdir(parents=True)
        (v / "done.md").replace(handled / "done.md")
        (v / "effects" / "a.json").replace(handled / "effects" / "a.json")
        async with await db.connect(fresh) as conn:
            done = await serve.recover(conn, _performers)
            again = await serve.recover(conn, _performers)
        return task, turn, done, again

    task, turn, done, again = run(go())
    written = run(rows(fresh, task))
    assert done["recollected"] == [turn] and again["recollected"] == []
    assert len(typed(written, "turn.collected", turn_id=turn)) == 1
    for name in ("a.json", "b.json"):
        assert len(typed(written, "effect.held", request_id=f"{turn}/{name}")) == 1
    assert machine.fold(written).state is State.CHECKS
    left = signals.collect(ws, ledger.new_id())  # what the next turn would read
    assert left.done is None and left.effects == []


class RefusedAtRelease:
    action_type = "push_branch"
    effect_class = "act"
    usage = None

    def __init__(self):
        self.releasing = False

    async def refuse(self, conn, action):
        return "the remote is gone" if self.releasing else None

    async def perform(self, action, key):
        raise AssertionError("never performed")

    async def lookup(self, action, key):
        return None


async def _asked(dsn, task, fakes) -> str:
    """A held push Tom approved by message: `release.requested`, owner the kernel."""
    async with await db.connect(dsn) as conn:
        held = await broker.request(
            conn, fakes, task, broker.Action("push_branch", "valor/feature", {"head_sha": "b" * 40})
        )
        approval = await broker.approve(conn, held.effect_id, note="approve")
        await ledger.append(
            conn,
            task,
            "release.requested",
            {"effect_id": held.effect_id, "approval_id": approval, "owner": "kernel"},
        )
    return held.effect_id


def test_refused_kernel_release(fresh, op):
    async def go():
        task = await new_task(fresh)
        fake = RefusedAtRelease()
        fakes = broker.Performers(fake)
        effect = await _asked(fresh, task, fakes)
        fake.releasing = True
        kernel = only(serve.Kernel(None, {}, lambda b: fakes, fresh), task)
        await tick(kernel, fresh)
        await settled(kernel)
        async with await db.connect(fresh) as conn:
            retried = await kernel._kernel_release(conn, task)
        # A stopped task's release, asked for before the stop.
        stopped = await new_task(fresh)
        other = await _asked(fresh, stopped, broker.Performers(RefusedAtRelease()))
        async with await db.connect(fresh) as conn:
            await tasks.stop(conn, stopped, reason="test")
            with pytest.raises(tasks.TaskStopped):
                await broker.release(conn, fakes, other)
            second = await broker.release(conn, fakes, other)
        return task, effect, retried, stopped, other, second

    task, effect, retried, stopped, other, second = run(go())
    assert retried is None and second.kind == "refused"
    for t, e in ((task, effect), (stopped, other)):
        written = run(rows(fresh, t))
        (refused,) = typed(written, "effect.refused", effect_id=e)
        assert refused["at"] == "release"
        assert len(typed(written, "notice.requested", about_key=f"effect-refused:{e}")) == 1


# -- the turn slot ------------------------------------------------------------------------


@pytest.mark.macos
def test_slot_reentrant(fresh, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def taken(conn) -> bool:
        got = await (
            await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (slot.key(),))
        ).fetchone()
        if got[0]:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (slot.key(),))
        return not got[0]

    async def go():
        task = await scripted.start(fresh, ws)
        gateway = await gateway_for(fresh)
        probe = await db.connect(fresh)
        try:
            async with await db.connect(fresh) as conn:
                b = await tasks.brief(conn, task)

            async def check():  # a check that runs a turn inside its slot
                async with slot.held(task, fresh):
                    during = await taken(probe)
                    ended = await runs.run_turn(
                        gateway, task, scripted.turn_for("plan it", None, b), dsn=fresh, state="plan"
                    )
                    return during, ended

            during, ended = await asyncio.wait_for(check(), 60)
            return during, ended, await taken(probe)
        finally:
            await probe.close()
            await gateway.close()

    during, ended, after = run(go())
    assert during is True and after is False
    assert ended["outcome"] == "done"


@pytest.mark.parametrize("which", ["test", "review", "docs"])
def test_a_check_holds_the_slot(fresh, op, which, monkeypatch):
    """A turn and a check do not run at once: a check waits while a turn
    holds the slot, and holds it itself while it runs."""
    from core import checks
    from core import fresh as fresh_runs

    async def taken(conn) -> bool:
        got = await (
            await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (slot.key(),))
        ).fetchone()
        if got[0]:
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (slot.key(),))
        return not got[0]

    async def go():
        task = await new_task(fresh)
        probe = await db.connect(fresh)
        seen: list[bool] = []
        real = ledger.read

        async def read(conn, task_id, *a, **k):
            seen.append(await taken(probe))
            return await real(conn, task_id, *a, **k)

        monkeypatch.setattr(ledger, "read", read)
        runner = {
            "test": lambda: checks.test_runner(None),
            "review": lambda: fresh_runs.review_runner(None, None),
            "docs": lambda: fresh_runs.docs_runner(None, None),
        }[which]()
        entered, release = asyncio.Event(), asyncio.Event()

        async def turn():  # what `runs.run_turn` holds around a turn
            async with slot.held("another-task", fresh):
                entered.set()
                await release.wait()

        async def waiting() -> bool:
            row = await (
                await probe.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
                )
            ).fetchone()
            return row[0] > 0

        try:
            holder = asyncio.create_task(turn())
            await entered.wait()
            ctx = router.Context(None, task, fresh, alive=lambda: asyncio.sleep(0, True))
            check = asyncio.create_task(runner(ctx))
            await until(waiting)
            before = (check.done(), list(seen))
            release.set()
            await holder
            out = await check
            return before, seen, out, await taken(probe)
        finally:
            await probe.close()

    before, seen, out, after = run(go())
    assert before == (False, [])
    assert seen and all(seen) and after is False
    assert out["status"] == "moved"


@pytest.mark.macos
def test_one_turn_slot(fresh, op, tmp_path):
    spaces = {name: scripted.workspace(tmp_path / name)[0] for name in "abjc"}
    for name in "ab":
        scripted.steer(spaces[name], sleep_once=2)

    async def go():
        a = await scripted.start(fresh, spaces["a"])
        b = await scripted.start(fresh, spaces["b"])
        j = await scripted.start(fresh, spaces["j"], judge=None)
        c = await scripted.start(fresh, spaces["c"])
        gateway = await gateway_for(fresh)
        kernel = only(serve.Kernel(gateway, scripted.RUNNERS, None, fresh), a, b, j)
        probe = await db.connect(fresh)
        try:
            await tick(kernel, fresh)
            first = (kernel.harness, set(kernel.jobs))

            async def a_turning():
                return typed(await rows(fresh, a), "turn.started")

            await until(a_turning)
            got = await (
                await probe.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (slot.key(),))
            ).fetchone()
            core_run = asyncio.create_task(drive(fresh, c))

            async def all_planned():
                await tick(kernel, fresh)
                return core_run.done() and all([await state(fresh, t) is State.CRITIQUE for t in (a, b, j)])

            await until(all_planned, 120, every=0.2)
            await settled(kernel)
            await core_run
            # A second kernel on this machine waits on `kernel:<machine>`.
            await probe.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (serve.kernel_key(),))
            second = asyncio.create_task(serve.serve({}, dsn=fresh, gateway=gateway))

            async def waiting():
                found = await (
                    await probe.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                        "AND application_name = 'valor-kernel' AND wait_event_type = 'Lock'"
                    )
                ).fetchone()
                return found[0] == 1

            await until(waiting, 10)
            blocked = not second.done()
            second.cancel()
            await asyncio.gather(second, return_exceptions=True)
            await kernel.close()
            return a, b, j, c, first, got[0], blocked
        finally:
            await probe.close()
            await gateway.close()

    a, b, j, c, first, slot_free, blocked = run(go())
    assert first == (a, {a, j})
    assert slot_free is False and blocked

    def turn_ids(task):
        written = run(rows(fresh, task))
        return [r["id"] for r in written if r["type"] == "turn.started"], [
            r["id"] for r in written if r["type"] == "turn.ended"
        ]

    (a_start,), (a_end,) = turn_ids(a)
    (b_start,), _ = turn_ids(b)
    (c_start,), _ = turn_ids(c)
    assert a_start < a_end < c_start < b_start


def own_ports(monkeypatch):
    """A task's service ports from this run's own block, so concurrent
    suites never share a port."""
    if os.environ.get("VALOR_TEST_PORTS"):
        low, high = (int(p) for p in os.environ["VALOR_TEST_PORTS"].split("-"))
        choose = kws.choose_port
        monkeypatch.setattr(kws, "choose_port", lambda span, taken: choose((low, high), taken))


@pytest.mark.macos
def test_services_survive_between_steps(fresh, op, tmp_path, monkeypatch):
    own_ports(monkeypatch)

    async def go():
        a, _ = await scripted.provisioned(fresh, tmp_path / "a", services=["redis"])
        b, _ = await scripted.provisioned(fresh, tmp_path / "b")
        gateway = await gateway_for(fresh)
        kernel = only(serve.Kernel(gateway, scripted.RUNNERS, None, fresh), a)
        try:

            async def planned():
                await tick(kernel, fresh)
                return not kernel.jobs and await state(fresh, a) is State.CRITIQUE

            await until(planned, 90, every=0.2)
            up = _service_pids(a)
            # Another task's kernel step, then a `core run`: each sweeps.
            await router.step(gateway, b, {}, fresh, None, sweep=True)
            await router.run(gateway, b, {}, dsn=fresh)
            kept = _service_pids(a)
        finally:
            await kernel.close()
            await gateway.close()
        return a, up, kept

    a, up, kept = run(go())
    assert up and set(up) <= set(kept)
    assert not _service_pids(a)


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

        async def ticks(n):
            for _ in range(n):
                await tick(kernel, fresh)
                await settled(kernel)

        await ticks(5)
        after_moved = len(calls)
        from core import notices

        async with await db.connect(fresh) as conn:
            await notices.request(conn, task, kind="test", about_key="test:quiet", text="quiet")
        await ticks(3)
        after_quiet = len(calls)
        async with await db.connect(fresh) as conn:
            await ledger.append(conn, task, "test.poke", {})
        await ticks(3)
        return after_moved, after_quiet, len(calls)

    assert run(go()) == (2, 2, 3)


def test_held_services_park_the_task(fresh, op, monkeypatch):
    """Another process holds the task's services: one claim, no spin, and
    the task is picked up on its next row or the next tick once free."""
    claims, steps = [], []
    claim = router._Services.claim

    async def counted(self):
        claims.append(self.task_id)
        return await claim(self)

    monkeypatch.setattr(router._Services, "claim", counted)

    async def judging(ctx):
        steps.append(ctx.task_id)
        return {"status": "idle"}

    async def go():
        task = await new_task(fresh)
        kernel = only(serve.Kernel(None, {State.JUDGE: judging}, None, fresh), task)
        holder = await db.connect(fresh)
        try:
            await holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"services:{task}",))
            with bridges.configure(serve_tick_s=3600):
                for _ in range(20):
                    await tick(kernel, fresh)
                    await settled(kernel)
                held = (len(claims), len(steps))
                await holder.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (f"services:{task}",)
                )
                await tick(kernel, fresh)
                await settled(kernel)
                quiet = (len(claims), len(steps))
                # The other run exits writing nothing: the next tick picks it up.
                kernel.parked_at -= 3600
                await tick(kernel, fresh)
                await settled(kernel)
                by_tick = (len(claims), len(steps))
                # Held again, then a row on its stream picks it up.
                await kernel.settle(task)
                await holder.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"services:{task}",)
                )
                async with await db.connect(fresh) as conn:
                    await ledger.append(conn, task, "test.poke", {})
                await tick(kernel, fresh)
                await settled(kernel)
                await holder.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (f"services:{task}",)
                )
                async with await db.connect(fresh) as conn:
                    await ledger.append(conn, task, "test.poke", {})
                await tick(kernel, fresh)
                await settled(kernel)
                by_row = (len(claims), len(steps))
        finally:
            await kernel.close()
            await holder.close()
        return held, quiet, by_tick, by_row

    held, quiet, by_tick, by_row = run(go())
    assert held == (1, 0)
    assert quiet == (1, 0)
    assert by_tick == (2, 1)
    assert by_row == (4, 2)


def test_core_run_refuses_while_services_are_held(fresh, op):
    """`core run` on a task whose services another process holds returns
    `already running` and runs nothing."""
    ran = []

    async def judging(ctx):
        ran.append(ctx.task_id)
        return {"status": "idle"}

    async def go():
        task = await new_task(fresh)
        before = len(await rows(fresh, task))
        holder = await db.connect(fresh)
        try:
            await holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"services:{task}",))
            out = await router.run(None, task, {State.JUDGE: judging}, dsn=fresh)
        finally:
            await holder.close()
        return out, before, len(await rows(fresh, task))

    out, before, after = run(go())
    assert out["status"] == "already running"
    assert ran == [] and after == before


def test_a_failed_job_parks_the_task(fresh, op, monkeypatch):
    """A release that fails for a reason other than a refusal, and services
    that cannot be opened: each tried once, then again on the next tick."""
    built, opened = [], []

    def broken(brief):
        built.append(brief.id)
        raise RuntimeError("no performers")

    async def no_services(dsn, task_id):
        opened.append(task_id)
        raise RuntimeError("no services")

    async def judging(ctx):
        raise AssertionError("never stepped")

    async def go():
        task = await new_task(fresh)
        await _asked(fresh, task, broker.Performers(RefusedAtRelease()))
        other = await new_task(fresh)
        releasing = only(serve.Kernel(None, {}, broken, fresh), task)
        stepping = only(serve.Kernel(None, {State.JUDGE: judging}, None, fresh), other)
        monkeypatch.setattr(router._Services, "open", no_services)
        try:
            with bridges.configure(serve_tick_s=3600):
                for _ in range(10):
                    for kernel in (releasing, stepping):
                        await tick(kernel, fresh)
                        await settled(kernel)
                parked = (len(built), len(opened), task in releasing.parked, other in stepping.parked)
                for kernel in (releasing, stepping):
                    kernel.parked_at -= 3600
                    await tick(kernel, fresh)
                    await settled(kernel)
        finally:
            await releasing.close()
            await stepping.close()
        return parked, (len(built), len(opened))

    parked, retried = run(go())
    assert parked == (1, 1, True, True)
    assert retried == (2, 2)


def test_a_failure_in_one_task_leaves_the_others(fresh, op, monkeypatch, capsys):
    """An error in one task's notices or scheduling is logged; the other
    task is still stepped."""
    from core import notices

    steps = []
    owe = notices.owe

    async def judging(ctx):
        steps.append(ctx.task_id)
        return {"status": "idle"}

    async def go():
        bad, good = await new_task(fresh), await new_task(fresh)

        async def failing_owe(conn, task_id):
            if task_id == bad:
                raise RuntimeError("owe broke")
            return await owe(conn, task_id)

        monkeypatch.setattr(notices, "owe", failing_owe)
        kernel = only(serve.Kernel(None, {State.JUDGE: judging}, None, fresh), bad, good)
        ready = kernel._ready

        async def failing_ready(conn, task_id):
            if task_id == bad:
                raise RuntimeError("schedule broke")
            return await ready(conn, task_id)

        kernel._ready = failing_ready
        try:
            await tick(kernel, fresh)
            await settled(kernel)
        finally:
            await kernel.close()
        return bad, good

    bad, good = run(go())
    assert steps == [good]
    err = capsys.readouterr().err
    assert f"task {bad}: notices failed" in err and f"task {bad}: scheduling failed" in err


@pytest.mark.macos
def test_stop_mid_turn_under_serve(fresh, op, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    scripted.steer(ws, sleep_once=30)

    async def go():
        task = await scripted.start(fresh, ws)
        gateway = await gateway_for(fresh)
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


def test_settle_releases_the_lock_when_stopping_the_services_fails():
    closed = []

    class Failing:
        def down(self):
            raise RuntimeError("stop failed")

        async def close(self):
            closed.append(True)

    async def go():
        kernel = serve.Kernel(None, {}, None, "")
        kernel.services["t"] = Failing()
        with pytest.raises(RuntimeError):
            await kernel.settle("t")
        return kernel.services

    assert run(go()) == {} and closed == [True]


@pytest.mark.macos
def test_a_stop_between_steps_settles_the_task(fresh, op, tmp_path, monkeypatch):
    """A task stopped while no step runs: its services stop and
    `services:<task>` is released when the kernel reads the stop."""
    own_ports(monkeypatch)

    async def held(task) -> bool:
        async with await db.connect(fresh) as conn:
            got = await (
                await conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"services:{task}",)
                )
            ).fetchone()
            return not got[0]

    async def go():
        task, _ = await scripted.provisioned(fresh, tmp_path / "a", services=["redis"])
        gateway = await gateway_for(fresh)
        kernel = serve.Kernel(gateway, scripted.RUNNERS, None, fresh)
        try:

            async def planned():
                await tick(kernel, fresh)
                return not kernel.jobs and await state(fresh, task) is State.CRITIQUE

            await until(planned, 90, every=0.2)
            before = (_service_pids(task), await held(task))
            async with await db.connect(fresh) as conn:
                await tasks.stop(conn, task, reason="test")
            await tick(kernel, fresh)
            after = (_service_pids(task), await held(task), task in kernel.services)
        finally:
            await kernel.close()
            await gateway.close()
        return before, after

    (up, held_before), (left, held_after, kept) = run(go())
    assert up and held_before
    assert not left and not held_after and not kept


def test_a_row_written_during_a_step_steps_it_again(fresh, op):
    """A row another writer adds while a step runs steps the task again once
    the step ends without moving; the step's own rows and the gateway's do
    not."""
    calls = []
    began, release = asyncio.Event(), asyncio.Event()

    async def judging(ctx):
        calls.append(ctx.task_id)
        began.set()
        await release.wait()
        async with await db.connect(ctx.dsn) as conn:
            await ledger.append(conn, ctx.task_id, "test.own", {})
        return {"status": "failed"}

    async def go():
        task = await new_task(fresh)
        kernel = only(serve.Kernel(None, {State.JUDGE: judging}, None, fresh), task)

        async def step_with(*types):
            began.clear()
            release.clear()
            await tick(kernel, fresh)
            await asyncio.wait_for(began.wait(), 20)
            async with await db.connect(fresh) as conn:
                for t in types:
                    await ledger.append(conn, task, t, {})
            release.set()
            await settled(kernel)

        async def ticks(n):
            for _ in range(n):
                await tick(kernel, fresh)
                await settled(kernel)

        try:
            await step_with("gateway.charged")
            await ticks(3)
            quiet = len(calls)
            async with await db.connect(fresh) as conn:
                await ledger.append(conn, task, "test.poke", {})
            await step_with("test.steer")
            stepped = len(calls)
            await ticks(3)
        finally:
            await kernel.close()
        return quiet, stepped, len(calls)

    assert run(go()) == (1, 2, 3)


def test_missed_notification(fresh, op):
    calls = []

    async def counting(ctx):
        calls.append(ctx.task_id)
        return {"status": "idle"}

    async def listeners(conn) -> list[int]:
        found = await (
            await conn.execute(
                "SELECT pid FROM pg_stat_activity WHERE datname = current_database() "
                "AND application_name = 'valor-kernel-listen'"
            )
        ).fetchall()
        return [r[0] for r in found]

    async def go():
        task = await new_task(fresh)
        gateway = await gateway_for(fresh)
        with bridges.configure(serve_tick_s=1.0):
            kernel = asyncio.create_task(serve.serve({State.JUDGE: counting}, dsn=fresh, gateway=gateway))
            probe = await db.connect(fresh)
            try:

                async def once():
                    return len(calls) == 1 and await listeners(probe)

                await until(once, 30)
                (old,) = await listeners(probe)
                await probe.execute("SELECT pg_terminate_backend(%s)", (old,))
                await ledger.append(probe, task, "test.poke", {})

                async def twice():
                    return len(calls) == 2

                await until(twice, 10)

                async def relistened():
                    now = await listeners(probe)
                    return now and old not in now

                await until(relistened, 10)
            finally:
                kernel.cancel()
                await asyncio.gather(kernel, return_exceptions=True)
                await probe.close()
                await gateway.close()

    run(go())


def test_the_kernel_gateway_sends_the_installed_openai_key(fresh, tmp_path, monkeypatch):
    """`serve` builds its own gateway with the kernel's OpenAI key: an
    OpenAI-route call goes upstream with the installed key, not the turn's."""
    import aiohttp

    from core.settings import settings
    from tests.test_gateway_openai import KEY, Upstream, body

    keyfile = tmp_path / "openai-key"
    keyfile.write_text(f"OPENAI_API_KEY={KEY}\n")
    monkeypatch.setattr(type(settings), "openai_keyfile", property(lambda _: str(keyfile)))
    built = []

    class Seen(serve.Kernel):
        def __init__(self, gateway, *rest):
            built.append(gateway)
            super().__init__(gateway, *rest)

    monkeypatch.setattr(serve, "Kernel", Seen)
    upstream = Upstream(data=b"{}")

    async def go():
        url = await upstream.start()
        task = await new_task(fresh)
        with bridges.configure(openai_upstream=url):
            kernel = asyncio.create_task(serve.serve({}, dsn=fresh))
            try:

                async def started():
                    return built

                (gateway,) = await until(started, 30)
                base = gateway.issue(task, "turn-1")
                async with (
                    aiohttp.ClientSession() as http,
                    http.post(
                        base + "/openai/v1/responses",
                        json=body(),
                        headers={"authorization": "Bearer sk-turn-own"},
                    ) as r,
                ):
                    await r.read()
                await gateway.drain(task)
            finally:
                kernel.cancel()
                await asyncio.gather(kernel, return_exceptions=True)
                await upstream.runner.cleanup()

    run(go())
    (seen,) = upstream.seen
    assert seen["path"] == "/v1/responses"
    assert seen["headers"]["authorization"] == f"Bearer {KEY}"


# -- one store, one context ---------------------------------------------------------------

DISPATCH = r"""
import asyncio, json, os, sys
from core import broker, db, tasks
from core.__main__ import _performers

async def main():
    async with await db.connect(os.environ["KERNEL_DSN"]) as conn:
        offered = _performers(await tasks.brief(conn, sys.argv[1])).offered()
        sys.stdout.write(json.dumps(await tasks.dispatch(conn, sys.argv[1], offered=offered)))

asyncio.run(main())
"""


def test_same_store_same_context(fresh, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(fresh, ws)
        async with await db.connect(fresh) as conn:
            offered = _performers(await tasks.brief(conn, task)).offered()
            here = await tasks.dispatch(conn, task, offered=offered)
        return task, here

    task, here = run(go())
    there = subprocess.run(
        [sys.executable, "-c", DISPATCH, task],
        cwd=ROOT,
        env={**os.environ, "KERNEL_DSN": fresh},
        capture_output=True,
        check=True,
    ).stdout
    there = json.loads(there)
    assert there["text"].encode() == here["text"].encode()
    assert there["sha256"] == here["sha256"]
    assert any("push_branch" in line for line in here["offered"])


def test_migrate_twice():
    name = f"{TEST_DB}_twice"
    dsn = db.migrate(name, fresh=True)
    assert db.migrate(name) == dsn

    db.migrate(name)

    async def recorded():
        async with await db.connect(dsn) as conn:
            sql = "SELECT count(*) FROM events WHERE type = 'correction.recorded'"
            return (await (await conn.execute(sql)).fetchone())[0]

    assert run(recorded()) == 1
