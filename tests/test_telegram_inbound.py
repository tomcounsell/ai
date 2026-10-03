"""From a Telegram message to the port's `Inbound`, through the emulator."""

import asyncio
import base64
import hashlib

import pytest

from bridges.telegram import inbound
from tests.telegram_emulator import Emulator
from tests.telegram_kernel import connected, until

GROUP = "-1001"
FORUM = "-1002"
DM = "42"


@pytest.fixture(scope="module")
def emu():
    e = Emulator(6531).start()
    e.control(
        chats=[
            {"id": int(GROUP), "kind": "supergroup"},
            {"id": int(FORUM), "kind": "supergroup", "forum": True},
            {"id": int(DM), "kind": "dm"},
            {"id": -1009, "kind": "supergroup"},
        ]
    )
    yield e
    e.stop()


def run(coro):
    return asyncio.run(coro)


def test_text_arrives_verbatim_and_tom_is_a_number(emu):
    async def go():
        async with connected(emu.url, [GROUP]) as (_, store):
            mid = emu.inject(int(GROUP), "**not bold** _x_ [a](b)", sender_id=7, sender_name="Tom")
            await until(lambda: store.received)
            r = store.received[0]
            assert r["text"] == "**not bold** _x_ [a](b)"
            assert (r["message_id"], r["chat_id"], r["sender_id"]) == (str(mid), GROUP, "7")
            assert r["chat_kind"] == "group" and r["verified"] is True
            assert r["sent_at"].endswith("+00:00")

    run(go())


def test_outgoing_service_and_unowned_write_nothing(emu):
    async def go():
        async with connected(emu.url, [GROUP]) as (_, store):
            emu.inject(int(GROUP), "mine", out=True, sender_id=1000)
            emu.inject(int(GROUP), "", service=True)
            emu.inject(-1009, "not owned here")
            last = emu.inject(int(GROUP), "from someone else", sender_id=55, sender_name="Ann")
            await until(lambda: store.received)
            await asyncio.sleep(0.2)
            assert [(r["message_id"], r["sender_id"]) for r in store.received] == [(str(last), "55")]

    run(go())


def test_topic_and_reply(emu):
    async def go():
        async with connected(emu.url, [FORUM]) as (_, store):
            root = emu.inject(int(FORUM), "topic created", service=True)
            in_topic = emu.inject(int(FORUM), "in topic", forum_topic=True, reply_to=root)
            reply = emu.inject(int(FORUM), "reply in topic", forum_topic=True, reply_to=in_topic, top_id=root)
            emu.inject(int(FORUM), "in general")
            await until(lambda: len(store.received) == 3)
            got = {r["text"]: (r["topic_id"], r["reply_to"]) for r in store.received}
            assert got["in topic"] == (str(root), None)
            assert got["reply in topic"] == (str(root), str(in_topic))
            assert got["in general"] == (None, None)
            assert reply

    run(go())


def test_sender_file_name_never_reaches_disk(emu, tmp_path):
    data = b"#!/bin/sh\necho hi\n"

    async def go():
        async with connected(emu.url, [DM], inbound_dir=str(tmp_path)) as (_, store):
            emu.inject(
                int(DM),
                "",
                media={"kind": "document", "name": "../../x.sh", "mime": "text/x-sh"},
                data_b64=base64.b64encode(data).decode(),
            )
            await until(lambda: store.received)
            [att] = store.received[0]["attachments"]
            digest = hashlib.sha256(data).hexdigest()
            assert att["path"] == str(tmp_path / "telegram" / digest)
            assert att["name"] == "../../x.sh" and att["bytes"] == len(data)
            assert (tmp_path / "telegram" / digest).read_bytes() == data
            assert oct((tmp_path / "telegram").stat().st_mode & 0o777) == "0o700"
            assert store.received[0]["chat_kind"] == "dm"

    run(go())


def test_media_timeout_scales_with_size():
    assert inbound.media_timeout(0) == 10
    assert inbound.media_timeout(500_000_000) > 500


def test_download_timing_out_twice_is_skipped_with_reason(emu, tmp_path):
    async def go():
        async with connected(emu.url, [GROUP], inbound_dir=str(tmp_path), timeout=lambda size: 0.1) as (
            _,
            store,
        ):
            emu.inject(
                int(GROUP),
                "slow",
                media={"kind": "photo", "name": "", "mime": "image/jpeg", "delay": 1},
                data_b64=base64.b64encode(b"x").decode(),
            )
            await until(lambda: store.received)
            [att] = store.received[0]["attachments"]
            assert att["skipped"].startswith("download timed out")
            assert "path" not in att

    run(go())


def test_a_non_timeout_download_error_is_not_retried(emu, tmp_path):
    async def go():
        async with connected(emu.url, [GROUP], inbound_dir=str(tmp_path)) as (bridge, store):
            calls = []
            real = bridge.wire.download

            async def counting(msg):
                calls.append(msg.id)
                return await real(msg)

            bridge.wire.download = counting
            emu.inject(
                int(GROUP),
                "",
                media={"kind": "document", "name": "a.pdf", "mime": "application/pdf", "fail": True},
                data_b64=base64.b64encode(b"x").decode(),
            )
            await until(lambda: store.received)
            [att] = store.received[0]["attachments"]
            assert att["skipped"] == "download failed: Refused"
            assert len(calls) == 1

    run(go())


def test_reply_chain(emu):
    async def go():
        async with connected(emu.url, [GROUP]) as (_, store):
            first = emu.inject(int(GROUP), "first", live=False)
            second = emu.inject(
                int(GROUP),
                "second",
                reply_to=first,
                live=False,
                media={"kind": "photo", "name": "", "mime": "image/jpeg"},
                data_b64=base64.b64encode(b"img").decode(),
            )
            emu.inject(int(GROUP), "third", reply_to=second)
            await until(lambda: store.received)
            chain = store.received[0]["thread"]
            assert [c["text"] for c in chain] == ["first", "second"]
            assert chain[0] == {"id": str(first), "text": "first", "attachments": []}
            assert chain[1]["attachments"][0]["skipped"] == "earlier message"
            assert "path" not in chain[1]["attachments"][0]

            # A deleted ancestor stops the walk.
            store.received.clear()
            emu.inject(int(GROUP), "to a deleted one", reply_to=99999)
            await until(lambda: store.received)
            assert store.received[0]["thread"] == []

            # The walk stops at CHAIN_HOPS.
            store.received.clear()
            prev = emu.inject(int(GROUP), "root", live=False)
            for i in range(inbound.CHAIN_HOPS + 5):
                prev = emu.inject(int(GROUP), f"hop {i}", reply_to=prev, live=False)
            emu.inject(int(GROUP), "leaf", reply_to=prev)
            await until(lambda: store.received)
            assert len(store.received[0]["thread"]) == inbound.CHAIN_HOPS

    run(go())


def test_album_is_one_record_per_photo_sharing_grouped_id(emu, tmp_path):
    async def go():
        async with connected(emu.url, [GROUP], inbound_dir=str(tmp_path)) as (_, store):
            for i in range(3):
                emu.inject(
                    int(GROUP),
                    "",
                    grouped_id=777,
                    media={"kind": "photo", "name": "", "mime": "image/jpeg"},
                    data_b64=base64.b64encode(bytes([i])).decode(),
                )
            await until(lambda: len(store.received) == 3)
            assert {r["headers"]["grouped_id"] for r in store.received} == {"777"}
            assert len({r["attachments"][0]["path"] for r in store.received}) == 3

    run(go())
