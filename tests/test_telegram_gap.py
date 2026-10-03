"""Gap fill: what the live handler missed is received once, oldest first."""

import asyncio
import base64

import pytest

from tests.telegram_emulator import Emulator
from tests.telegram_kernel import StandIn, connected, until

DM = "42"
OTHER = "43"
GROUP = "-1003"


@pytest.fixture
def emu():
    e = Emulator(6532).start()
    e.control(
        chats=[
            {"id": int(DM), "kind": "dm"},
            {"id": int(OTHER), "kind": "dm"},
            {"id": int(GROUP), "kind": "supergroup"},
        ]
    )
    yield e
    e.stop()


def run(coro):
    return asyncio.run(coro)


def record(chat: str, mid: int) -> dict:
    return {"received_id": f"old{mid}", "channel": "telegram", "chat_id": chat, "message_id": str(mid)}


def test_messages_sent_while_down_are_received_once_oldest_first(emu):
    last_seen = emu.inject(int(DM), "seen before the bridge went down", live=False)
    expected = []
    for i in range(250):
        expected.append(emu.inject(int(DM), f"while down {i}", live=False))
        if i % 3 == 0:
            emu.inject(int(OTHER), "a gap in DM's ids", live=False)
    store = StandIn(owned=[DM])
    store.received.append(record(DM, last_seen))

    async def go():
        async with connected(emu.url, [DM], store=store) as (bridge, _):
            got = [int(i) for i in store.ids(DM)[1:]]
            assert got == expected
            assert got != list(range(got[0], got[0] + len(got)))  # the ids have gaps
            await bridge.tick()
            assert len(store.ids(DM)) == 251

    run(go())


def test_a_chat_with_no_rows_backfills_nothing(emu):
    for i in range(5):
        emu.inject(int(DM), f"old {i}", live=False)

    async def go():
        async with connected(emu.url, [DM]) as (bridge, store):
            await bridge.tick()
            assert store.received == []

    run(go())


def test_live_and_gap_fill_at_once_write_one_row(emu):
    async def go():
        async with connected(emu.url, [DM]) as (bridge, store):
            emu.inject(int(DM), "both paths")
            await asyncio.gather(bridge.tick(), bridge.tick())
            await until(lambda: store.received)
            await asyncio.sleep(0.2)
            await bridge.tick()
            assert len(store.received) == 1

    run(go())


def test_a_dropped_live_update_is_recorded_on_the_next_tick_without_downloading_again(emu, tmp_path):
    async def go():
        async with connected(emu.url, [GROUP], inbound_dir=str(tmp_path)) as (bridge, store):
            downloads = []
            real = bridge.wire.download

            async def counting(msg):
                downloads.append(msg.id)
                return await real(msg)

            bridge.wire.download = counting
            dropped = emu.inject(int(GROUP), "the update for this one is lost", live=False)
            after = emu.inject(
                int(GROUP),
                "",
                media={"kind": "photo", "name": "", "mime": "image/jpeg"},
                data_b64=base64.b64encode(b"img").decode(),
            )
            await until(lambda: store.received)
            assert store.ids(GROUP) == [str(after)]
            await bridge.tick()
            assert sorted(store.ids(GROUP), key=int) == [str(dropped), str(after)]
            assert downloads == [after]

    run(go())


def test_a_failed_receive_drops_the_connection_and_the_reconnect_records_it(emu):
    async def go():
        async with connected(emu.url, [DM]) as (bridge, store):
            store.fail_receive = 1
            mid = emu.inject(int(DM), "while the database is down")
            await until(lambda: not bridge.wire.connected())
            assert store.received == []
            await bridge.connect()
            assert store.ids(DM) == [str(mid)]

    run(go())


def test_a_flood_wait_in_gap_fill_holds_later_passes(emu):
    async def go():
        async with connected(emu.url, [DM]) as (bridge, store):
            emu.control(flood_history=30)
            await bridge.tick()
            assert bridge.sender.flood.active()
            emu.inject(int(DM), "missed", live=False)
            await bridge.tick()
            assert store.received == []

    run(go())
