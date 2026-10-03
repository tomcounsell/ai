"""Gap fill: what the live handler missed is recorded once, oldest first."""

import asyncio
import base64
import dataclasses
from datetime import UTC, datetime

import pytest

from core import db, intake
from tests.telegram_emulator import Emulator
from tests.telegram_port import chat, connected, emu_chat, ids, machine, received, until

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture
def emu():
    e = Emulator(6532).start()
    yield e
    e.stop()


def run(coro):
    return asyncio.run(coro)


async def record(dsn: str, chat_id: str, mid: int) -> None:
    """A message recorded before the bridge went down."""
    async with await db.connect(dsn) as conn:
        await intake.receive(
            conn,
            intake.Inbound(
                channel="telegram",
                chat_id=chat_id,
                chat_kind="dm",
                message_id=str(mid),
                sender_id="7",
                sender_name="Tom",
                sent_at=datetime.now(UTC).isoformat(),
                text="seen before the bridge went down",
            ),
        )


def test_messages_sent_while_down_are_received_once_oldest_first(emu, dsn, tmp_path):
    dm, other = chat("dm"), chat("dm")
    emu.control(chats=[emu_chat(dm), emu_chat(other)])
    last_seen = emu.inject(int(dm), "seen before the bridge went down", live=False)
    expected = []
    for i in range(250):
        expected.append(emu.inject(int(dm), f"while down {i}", live=False))
        if i % 3 == 0:
            emu.inject(int(other), "a gap in the DM's ids", live=False)

    async def go():
        await record(dsn, dm, last_seen)
        async with connected(emu.url, dsn, tmp_path) as bridge:
            got = [int(i) for i in (await ids(dsn, dm))[1:]]
            assert got == expected
            assert got != list(range(got[0], got[0] + len(got)))  # the ids have gaps
            await bridge.tick()
            assert len(await ids(dsn, dm)) == 251

    with machine(tmp_path, [dm]):
        run(go())


def test_a_chat_with_rows_and_no_seen_entry_takes_an_update_dropped_below_the_newest_row(emu, dsn, tmp_path):
    """No seen entry: the pass stops at the lowest recorded id, so a
    message below the newest recorded one, whose update was dropped
    before the chat's first pass, is still received."""
    dm = chat("dm")
    emu.control(chats=[emu_chat(dm)])
    before = emu.inject(int(dm), "history before the bridge", live=False)
    first = emu.inject(int(dm), "recorded", live=False)
    dropped = emu.inject(int(dm), "the update for this one is lost", live=False)
    newest = emu.inject(int(dm), "recorded too", live=False)

    async def go():
        await record(dsn, dm, first)
        await record(dsn, dm, newest)
        async with connected(emu.url, dsn, tmp_path):
            got = await ids(dsn, dm)
            assert sorted(got, key=int) == [str(first), str(dropped), str(newest)]
            assert str(before) not in got

    with machine(tmp_path, [dm]):
        run(go())


def test_a_chat_with_no_rows_backfills_nothing(emu, dsn, tmp_path):
    dm = chat("dm")
    emu.control(chats=[emu_chat(dm)])
    for i in range(5):
        emu.inject(int(dm), f"old {i}", live=False)

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            await bridge.tick()
            assert await received(dsn, dm) == []

    with machine(tmp_path, [dm]):
        run(go())


def test_live_and_gap_fill_at_once_write_one_row(emu, dsn, tmp_path):
    dm = chat("dm")
    emu.control(chats=[emu_chat(dm)])

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            emu.inject(int(dm), "both paths")
            await asyncio.gather(bridge.tick(), bridge.tick())
            await until(lambda: received(dsn, dm))
            await asyncio.sleep(0.2)
            await bridge.tick()
            assert len(await received(dsn, dm)) == 1

    with machine(tmp_path, [dm]):
        run(go())


def test_a_dropped_live_update_is_recorded_on_the_next_tick_without_downloading_again(emu, dsn, tmp_path):
    group = chat()
    emu.control(chats=[emu_chat(group)])

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            downloads = []
            real = bridge.wire.download

            async def counting(msg):
                downloads.append(msg.id)
                return await real(msg)

            bridge.wire.download = counting
            dropped = emu.inject(int(group), "the update for this one is lost", live=False)
            after = emu.inject(
                int(group),
                "",
                media={"kind": "photo", "name": "", "mime": "image/jpeg"},
                data_b64=base64.b64encode(b"img").decode(),
            )
            await until(lambda: received(dsn, group))
            assert await ids(dsn, group) == [str(after)]
            await bridge.tick()
            assert sorted(await ids(dsn, group), key=int) == [str(dropped), str(after)]
            assert downloads == [after]

    with machine(tmp_path, [group]):
        run(go())


def test_a_failed_receive_drops_the_connection_and_the_reconnect_records_it(emu, dsn, tmp_path):
    dm = chat("dm")
    emu.control(chats=[emu_chat(dm)])

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            real = bridge.kernel.receive
            failures = [1]

            async def down_once(conn, inbound):
                if failures:
                    failures.pop()
                    raise RuntimeError("the database is down")
                return await real(conn, inbound)

            bridge.kernel = dataclasses.replace(bridge.kernel, receive=down_once)
            mid = emu.inject(int(dm), "while the database is down")
            await until(lambda: not bridge.wire.connected())
            assert await received(dsn, dm) == []
            await bridge.connect()
            assert await ids(dsn, dm) == [str(mid)]

    with machine(tmp_path, [dm]):
        run(go())


def test_a_restart_takes_up_where_the_last_pass_stopped(emu, dsn, tmp_path):
    dm = chat("dm")
    emu.control(chats=[emu_chat(dm)])

    async def go():
        async with connected(emu.url, dsn, tmp_path):
            pass
        missed = [emu.inject(int(dm), f"while down {i}", live=False) for i in range(3)]
        async with connected(emu.url, dsn, tmp_path):
            assert await ids(dsn, dm) == [str(m) for m in missed]

    with machine(tmp_path, [dm]):
        run(go())


def test_a_flood_wait_in_gap_fill_holds_later_passes(emu, dsn, tmp_path):
    dm = chat("dm")
    emu.control(chats=[emu_chat(dm)])

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            emu.control(flood_history=30)
            await bridge.tick()
            assert bridge.sender.flood.active()
            emu.inject(int(dm), "missed", live=False)
            await bridge.tick()
            assert await received(dsn, dm) == []

    with machine(tmp_path, [dm]):
        run(go())
