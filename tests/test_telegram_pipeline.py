"""The bridge over the real port (`core/bridge.py`, `core/intake.py`) and the
test database, with the emulator as Telegram. Skipped until 2.1's port is
in the tree."""

import asyncio
import dataclasses
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("core.intake")
pytest.importorskip("core.bridge")

from bridges.telegram.bridge import TelegramBridge  # noqa: E402
from bridges.telegram.kernel import from_core  # noqa: E402
from core import db  # noqa: E402
from tests.telegram_emulator import Emulator, EmulatorWire  # noqa: E402
from tests.telegram_kernel import Action  # noqa: E402

CHAT = "-1010"


@pytest.fixture
def emu():
    e = Emulator(6536).start()
    e.control(chats=[{"id": int(CHAT), "kind": "supergroup"}])
    yield e
    e.stop()


def kernel(dsn, tmp_path):
    @asynccontextmanager
    async def conn():
        c = await db.connect(dsn)
        try:
            yield c
        finally:
            await c.close()

    return dataclasses.replace(
        from_core(),
        conn=conn,
        owns=lambda channel, id: id == CHAT,
        owned=lambda channel: [CHAT],
        inbound_dir=str(tmp_path),
    )


async def rows(dsn, kind: str) -> list[dict]:
    async with await db.connect(dsn) as c:
        cur = await c.execute(
            "SELECT payload FROM events WHERE type = %s AND payload->>'chat_id' = %s", (kind, CHAT)
        )
        return [r[0] for r in await cur.fetchall()]


def test_a_message_is_recorded_once_through_intake(emu, dsn, tmp_path):
    async def go():
        k = kernel(dsn, tmp_path)
        wire = EmulatorWire(emu.url)
        bridge = TelegramBridge(wire, k)
        await bridge.connect()
        try:
            mid = emu.inject(int(CHAT), "hello from Tom", sender_id=7, sender_name="Tom")
            got = []

            async def poll():
                got[:] = await rows(dsn, "message.received")

            for _ in range(100):
                await poll()
                if got:
                    break
                await asyncio.sleep(0.05)
            await bridge.tick()
            await poll()
            assert len(got) == 1
            assert got[0]["message_id"] == str(mid) and got[0]["verified"] is True
            async with k.conn() as c:
                assert await k.highest(c, "telegram", CHAT) == mid
                assert await k.recorded(c, "telegram", CHAT, [str(mid), "999"]) == {str(mid)}
        finally:
            await wire.close()

    asyncio.run(go())


def test_the_ports_split_and_limits_are_what_the_bridge_sends_by(emu, dsn, tmp_path):
    from core import bridge as port

    assert port.LIMITS["telegram"].max_text == 4096
    assert port.LIMITS["telegram"].text_units == "utf16"

    async def go():
        k = kernel(dsn, tmp_path)
        wire = EmulatorWire(emu.url)
        b = TelegramBridge(wire, k)
        await b.connect()
        try:
            perform, _ = b.performers()["telegram.send_message"]
            out = await perform(Action("telegram.send_message", CHAT, {"text": "a" * 4097}), "task-1:e1")
            assert len(out["sent"]) == 2
        finally:
            await wire.close()

    asyncio.run(go())
