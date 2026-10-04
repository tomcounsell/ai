"""The bridge, the real port, and the kernel's binding, end to end: the
emulator is Telegram and Tom; notices go out through the bridge and Tom's
replies come back through it."""

import asyncio
import signal
import sys

import pytest

from core import bridge as port
from core import broker, db, intake, machine, notices, tasks
from core.machine import State
from tests import bridges, scripted
from tests.bridges import OPERATOR, new_task, of_type, rows
from tests.telegram_emulator import Emulator
from tests.telegram_port import (
    SEND,
    chat,
    child_env,
    connected,
    due,
    emu_chat,
    ids,
    release,
    send,
    until,
)
from tests.telegram_port import (
    machine as this_machine,
)
from tests.test_pipeline import drive, to_checks

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture
def emu():
    e = Emulator().start()
    yield e
    e.stop()


@pytest.fixture
def op(emu, tmp_path):
    operator = chat()
    emu.control(chats=[emu_chat(operator)])
    with this_machine(tmp_path, [operator]):
        yield operator


def run(coro):
    return asyncio.run(coro)


async def child(tmp_path, op, *args: str):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.telegram_child", *args, env=child_env(tmp_path, [op])
    )


async def deliver(dsn, bridge, task) -> dict[str, str]:
    """Owe the task's notices and send them through the bridge: notice
    text's first line -> its message id."""
    async with await db.connect(dsn) as conn:
        made = await notices.owe(conn, task)
    out = {}
    async with bridges.outbox(dsn, bridge) as box:
        for nid in made:
            await bridge.sender.notice(await due(box, nid), box)
            (sent,) = await of_type(dsn, "notice.sent", notice_id=nid)
            (req,) = await of_type(dsn, "notice.requested", notice_id=nid)
            out[req["kind"]] = sent["sent"][0]["message_id"]
    return out


async def tom(emu, dsn, chat_id, text, reply_to=None) -> dict:
    """Tom writes in the chat; the bridge records it; the kernel binds it."""
    mid = emu.inject(int(chat_id), text, sender_id=int(OPERATOR), sender_name="Tom", reply_to=reply_to)
    await until(lambda: _has(dsn, chat_id, mid))
    (got,) = [
        r for r in await of_type(dsn, "message.received", chat_id=chat_id) if r["message_id"] == str(mid)
    ]
    async with await db.connect(dsn) as conn:
        await intake.bind(conn)
    (bound,) = await of_type(dsn, "message.bound", received_id=got["received_id"])
    return bound


async def _has(dsn, chat_id, mid) -> bool:
    return str(mid) in await ids(dsn, chat_id)


async def replies_to(dsn, received_id) -> list[dict]:
    return [
        r for r in await of_type(dsn, "notice.requested") if r["about_key"].startswith(f"reply:{received_id}")
    ]


def test_a_message_is_recorded_once_through_intake(emu, dsn, tmp_path, op):
    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            mid = emu.inject(int(op), "hello from Tom", sender_id=7, sender_name="Tom")
            await until(lambda: _has(dsn, op, mid))
            await bridge.tick()
            [r] = await of_type(dsn, "message.received", chat_id=op)
            assert r["message_id"] == str(mid) and r["verified"] is True
            async with bridge.kernel.conn() as c:
                assert await bridge.kernel.lowest(c, "telegram", op) == mid
                assert await bridge.kernel.recorded(c, "telegram", op, [str(mid), "999"]) == {str(mid)}

    run(go())


def test_the_ports_split_and_limits_are_what_the_bridge_sends_by(emu, dsn, tmp_path, op):
    assert port.LIMITS["telegram"].max_text == 4096
    assert port.LIMITS["telegram"].text_units == "utf16"

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            perform, _ = bridge.performers()[SEND]
            assert len((await perform(send(op, "a" * 4097), "task-1:e1"))["sent"]) == 2

    run(go())


def test_approve_by_reply_to_the_effect_notice_and_the_send_goes_out(emu, dsn, tmp_path, op):
    async def go():
        task = await new_task(dsn)
        async with connected(emu.url, dsn, tmp_path) as bridge:
            async with await db.connect(dsn) as conn:
                held = await broker.request(conn, bridges.declared(), task, send(op, "the approved words"))
            target = (await deliver(dsn, bridge, task))["effect"]

            near = await tom(emu, dsn, op, "Approve.", reply_to=int(target))
            assert near["as"] == "steer"
            assert any("Not an approval" in n["text"] for n in await replies_to(dsn, near["received_id"]))
            assert not await of_type(dsn, "release.requested", effect_id=held.effect_id)

            assert (await tom(emu, dsn, op, "approve", reply_to=int(target)))["as"] == "approve"
            async with bridges.outbox(dsn, bridge) as box:
                assert (await box.perform(await due(box, held.effect_id))).kind == "done"
            assert "the approved words" in [m["text"] for m in emu.own(int(op))]
            [granted] = await of_type(dsn, "approval.granted", effect_id=held.effect_id)
            assert granted["provenance"]["via"] == "telegram"

            again = await tom(emu, dsn, op, "approve", reply_to=int(target))
            assert again["as"] == "none" and await replies_to(dsn, again["received_id"])
            assert (await tom(emu, dsn, op, "thanks", reply_to=int(target)))["as"] == "steer"

            started = await tom(emu, dsn, op, "write a haiku")
            assert started["as"] == "start" and started["task_id"] not in (task, None)
            async with await db.connect(dsn) as conn:
                assert (await tasks.status(conn, started["task_id"]))["spent_usd_micros"] == 0

    run(go())


def test_stop_by_reply_and_a_released_send_of_that_task_is_refused_once(emu, dsn, tmp_path, op):
    async def go():
        effect = await release(dsn, send(op, "never sent"))
        task = await _task_of(dsn, effect)
        async with await db.connect(dsn) as conn:
            await notices.request(conn, task, kind="test", about_key=f"t:{task}", text="about it", chat_id=op)
        async with connected(emu.url, dsn, tmp_path) as bridge:
            async with bridges.outbox(dsn, bridge) as box:
                (nid,) = [
                    r["notice_id"] for r in await of_type(dsn, "notice.requested", about_key=f"t:{task}")
                ]
                await bridge.sender.notice(await due(box, nid), box)
            (sent,) = await of_type(dsn, "notice.sent", notice_id=nid)
            stop = await tom(emu, dsn, op, " STOP ", reply_to=int(sent["sent"][0]["message_id"]))
            assert stop["as"] == "stop"
            assert machine.fold(await rows(dsn, task)).state is State.STOPPED

            async with bridges.outbox(dsn, bridge) as box:
                assert (await box.perform(await due(box, effect))).kind == "refused"
                assert effect not in [getattr(i, "effect_id", None) for i in await box.due()]
            assert len(await of_type(dsn, "effect.refused", effect_id=effect)) == 1
            assert "never sent" not in [m["text"] for m in emu.own(int(op))]

    run(go())


async def _task_of(dsn, effect_id) -> str:
    async with await db.connect(dsn) as conn:
        (task,) = await (
            await conn.execute(
                "SELECT task_id FROM events WHERE type = 'effect.held' AND payload->>'effect_id' = %s",
                (effect_id,),
            )
        ).fetchone()
    return task


def test_an_answer_by_reply_to_the_question_notice(emu, dsn, tmp_path, op):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        waiting = await scripted.start(dsn, ws, judge="thin")
        await drive(dsn, waiting)
        f = machine.fold(await rows(dsn, waiting))
        assert f.state is State.WAITING and f.open_question
        async with connected(emu.url, dsn, tmp_path) as bridge:
            target = (await deliver(dsn, bridge, waiting))["question"]
            bound = await tom(emu, dsn, op, "a short one", reply_to=int(target))
        assert bound["as"] == "answer" and bound["task_id"] == waiting
        [answered] = [r for r in await rows(dsn, waiting) if r["type"] == "question.answered"]
        prov = answered["payload"]["provenance"]
        assert (prov["by"], prov["via"], prov["role_played"]) == ("tom", "telegram", False)
        assert machine.fold(await rows(dsn, waiting)).state is State.CLARIFY

    run(go())


def test_feedback_by_reply_to_the_delivered_notice(emu, dsn, tmp_path, op):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await to_checks(dsn, ws)
        await scripted.checks(dsn, task)
        assert machine.fold(await rows(dsn, task)).state is State.MERGE
        async with connected(emu.url, dsn, tmp_path) as bridge:
            target = (await deliver(dsn, bridge, task))["delivered"]
            bound = await tom(emu, dsn, op, "approve", reply_to=int(target))
        assert bound["as"] == "feedback" and bound["task_id"] == task

    run(go())


def test_a_released_effect_of_another_owner_is_not_the_bridges(emu, dsn, tmp_path, op):
    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge, bridges.outbox(dsn, bridge) as box:
            owners = {d.owner for d in port.DECLARED.values() if d.action_type in box.types()}
            assert owners == {"telegram"}
            assert box.types() == [SEND]

    run(go())


def test_a_second_bridge_waits_on_the_lock_while_the_first_serves(emu, dsn, tmp_path, op):
    """`serve` holds `bridge:telegram:<machine>`: a second process blocks on
    it, receives nothing, and takes over when the first's session ends."""

    async def holder():
        conn = await db.connect(dsn)
        await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", ("bridge:telegram:m-test",))
        return conn

    async def go():
        async with connected(emu.url, dsn, tmp_path):  # the chat has a row and a seen id
            seed = emu.inject(int(op), "before", sender_id=7)
            await until(lambda: _has(dsn, op, seed))
        first = await holder()
        proc = await child(tmp_path, op, "run", emu.url, dsn, str(tmp_path), str(tmp_path / "m"), "x")
        try:
            mid = emu.inject(int(op), "while the first holds the lock", sender_id=7)
            await asyncio.sleep(2)
            assert proc.returncode is None
            assert await ids(dsn, op) == [str(seed)]
            await first.close()
            await until(lambda: _has(dsn, op, mid), timeout=15)
        finally:
            proc.send_signal(signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), 15)

    run(go())


def test_killed_before_the_send_the_effect_fails_at_reconcile(emu, dsn, tmp_path, op):
    async def go():
        effect = await release(dsn, send(op, "never accepted"))
        proc = await child(tmp_path, op, "perform", emu.url, dsn, str(tmp_path), effect, "before")
        try:
            await until(lambda: of_type(dsn, "effect.intent", effect_id=effect), timeout=15)
        finally:
            proc.kill()
            await asyncio.wait_for(proc.wait(), 10)
        async with connected(emu.url, dsn, tmp_path) as bridge, await db.connect(dsn) as conn:
            performers = port.bound_performers(bridge, conn)
            assert (await broker.reconcile(conn, performers, effect)).kind == "failed"
        assert emu.own(int(op)) == []

    run(go())
