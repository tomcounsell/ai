"""The bridge, the real port, and the kernel's binding, end to end: the
emulator is Telegram and Tom; notices go out through the bridge and Tom's
replies come back through it."""

import asyncio
import sys

import pytest

from core import broker, db, intake, machine, notices, tasks
from core.machine import State
from tests import bridges
from tests.bridges import OPERATOR, new_task, of_type, rows
from tests.telegram_emulator import Emulator
from tests.telegram_port import (
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


def test_a_requested_send_goes_out_with_no_tap_and_a_reply_to_it_steers(emu, dsn, tmp_path, op):
    async def go():
        task = await new_task(dsn)
        async with connected(emu.url, dsn, tmp_path) as bridge:
            async with await db.connect(dsn) as conn:
                held = await broker.request(conn, bridges.declared(), task, send(op, "the requested words"))
            assert held.kind == "released"
            async with bridges.outbox(dsn, bridge) as box:
                assert (await box.perform(await due(box, held.effect_id))).kind == "done"
            assert "the requested words" in [m["text"] for m in emu.own(int(op))]
            # A send to Tom's own chat is itself the message: no report.
            assert not await of_type(dsn, "notice.requested", about_key=f"report:{held.effect_id}")
            (outcome,) = await of_type(dsn, "effect.outcome", effect_id=held.effect_id)
            target = outcome["result"]["sent"][0]["message_id"]

            near = await tom(emu, dsn, op, "approve", reply_to=int(target))
            assert near["as"] == "steer" and near["task_id"] == task

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
