"""From a Telegram message to a `message.received` row, through the
emulator and the real intake."""

import asyncio
import base64
import hashlib
from types import SimpleNamespace

import pytest

from bridges.telegram import inbound
from tests.telegram_emulator import Emulator
from tests.telegram_port import chat, connected, emu_chat, machine, received, until

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture(scope="module")
def emu():
    e = Emulator(6531).start()
    yield e
    e.stop()


@pytest.fixture
def c(emu, tmp_path):
    ns = SimpleNamespace(group=chat(), forum=chat(), dm=chat("dm"), other=chat())
    emu.control(
        chats=[emu_chat(ns.group), emu_chat(ns.forum, forum=True), emu_chat(ns.dm), emu_chat(ns.other)]
    )
    with machine(tmp_path, [ns.group, ns.forum, ns.dm]):
        yield ns


def run(coro):
    return asyncio.run(coro)


def test_text_arrives_verbatim_and_tom_is_a_number(emu, dsn, tmp_path, c):
    async def go():
        async with connected(emu.url, dsn, tmp_path):
            mid = emu.inject(int(c.group), "**not bold** _x_ [a](b)", sender_id=7, sender_name="Tom")
            await until(lambda: received(dsn, c.group))
            [r] = await received(dsn, c.group)
            assert r["text"] == "**not bold** _x_ [a](b)"
            assert (r["message_id"], r["chat_id"], r["sender_id"]) == (str(mid), c.group, "7")
            assert r["chat_kind"] == "group" and r["verified"] is True
            assert r["sent_at"].endswith("+00:00")

    run(go())


def test_outgoing_service_and_unowned_write_nothing(emu, dsn, tmp_path, c):
    async def go():
        async with connected(emu.url, dsn, tmp_path):
            emu.inject(int(c.group), "mine", out=True, sender_id=1000)
            emu.inject(int(c.group), "", service=True)
            emu.inject(int(c.other), "not owned here")
            last = emu.inject(int(c.group), "from someone else", sender_id=55, sender_name="Ann")
            await until(lambda: received(dsn, c.group))
            await asyncio.sleep(0.2)
            assert [(r["message_id"], r["sender_id"]) for r in await received(dsn, c.group)] == [
                (str(last), "55")
            ]
            assert await received(dsn, c.other) == []

    run(go())


def test_topic_and_reply(emu, dsn, tmp_path, c):
    async def go():
        async with connected(emu.url, dsn, tmp_path):
            root = emu.inject(int(c.forum), "topic created", service=True)
            in_topic = emu.inject(int(c.forum), "in topic", forum_topic=True, reply_to=root)
            emu.inject(int(c.forum), "reply in topic", forum_topic=True, reply_to=in_topic, top_id=root)
            emu.inject(int(c.forum), "in general")

            async def three():
                return len(await received(dsn, c.forum)) == 3

            await until(three)
            got = {r["text"]: (r["topic_id"], r["reply_to"]) for r in await received(dsn, c.forum)}
            assert got["in topic"] == (str(root), None)
            assert got["reply in topic"] == (str(root), str(in_topic))
            assert got["in general"] == (None, None)

    run(go())


def test_sender_file_name_never_reaches_disk(emu, dsn, tmp_path, c):
    data = b"#!/bin/sh\necho hi\n"
    where = tmp_path / "inbound" / "telegram"

    async def go():
        async with connected(emu.url, dsn, tmp_path):
            emu.inject(
                int(c.dm),
                "",
                media={"kind": "document", "name": "../../x.sh", "mime": "text/x-sh"},
                data_b64=base64.b64encode(data).decode(),
            )
            await until(lambda: received(dsn, c.dm))
            [r] = await received(dsn, c.dm)
            [att] = r["attachments"]
            digest = hashlib.sha256(data).hexdigest()
            assert att["path"] == str(where / digest)
            assert att["name"] == "../../x.sh" and att["bytes"] == len(data)
            assert (where / digest).read_bytes() == data
            assert oct(where.stat().st_mode & 0o777) == "0o700"
            assert r["chat_kind"] == "dm"

    run(go())


def test_media_timeout_scales_with_size():
    assert inbound.media_timeout(0) == 10
    assert inbound.media_timeout(500_000_000) > 500


def test_download_timing_out_twice_is_skipped_with_reason(emu, dsn, tmp_path, c):
    async def go():
        async with connected(emu.url, dsn, tmp_path, timeout=lambda size: 0.1):
            emu.inject(
                int(c.group),
                "slow",
                media={"kind": "photo", "name": "", "mime": "image/jpeg", "delay": 1},
                data_b64=base64.b64encode(b"x").decode(),
            )
            await until(lambda: received(dsn, c.group))
            [att] = (await received(dsn, c.group))[0]["attachments"]
            assert att["skipped"].startswith("download timed out")
            assert "path" not in att

    run(go())


def test_a_non_timeout_download_error_is_not_retried(emu, dsn, tmp_path, c):
    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            calls = []
            real = bridge.wire.download

            async def counting(msg):
                calls.append(msg.id)
                return await real(msg)

            bridge.wire.download = counting
            emu.inject(
                int(c.group),
                "",
                media={"kind": "document", "name": "a.pdf", "mime": "application/pdf", "fail": True},
                data_b64=base64.b64encode(b"x").decode(),
            )
            await until(lambda: received(dsn, c.group))
            [att] = (await received(dsn, c.group))[0]["attachments"]
            assert att["skipped"] == "download failed: Refused"
            assert len(calls) == 1

    run(go())


def test_reply_chain(emu, dsn, tmp_path, c):
    async def newest():
        rows = await received(dsn, c.group)
        return rows[-1] if rows else None

    async def go():
        async with connected(emu.url, dsn, tmp_path):
            first = emu.inject(int(c.group), "first", live=False)
            second = emu.inject(
                int(c.group),
                "second",
                reply_to=first,
                live=False,
                media={"kind": "photo", "name": "", "mime": "image/jpeg"},
                data_b64=base64.b64encode(b"img").decode(),
            )
            third = emu.inject(int(c.group), "third", reply_to=second)
            await until(newest)
            chain = (await newest())["thread"]
            assert [x["text"] for x in chain] == ["first", "second"]
            assert chain[0] == {"id": str(first), "text": "first", "attachments": []}
            assert chain[1]["attachments"][0]["skipped"] == "earlier message"
            assert "path" not in chain[1]["attachments"][0]

            # A deleted ancestor stops the walk.
            orphan = emu.inject(int(c.group), "to a deleted one", reply_to=99999)
            await until(lambda: _is(newest, orphan))
            assert (await newest())["thread"] == []

            # The walk stops at CHAIN_HOPS.
            prev = emu.inject(int(c.group), "root", live=False)
            for i in range(inbound.CHAIN_HOPS + 5):
                prev = emu.inject(int(c.group), f"hop {i}", reply_to=prev, live=False)
            leaf = emu.inject(int(c.group), "leaf", reply_to=prev)
            await until(lambda: _is(newest, leaf))
            assert len((await newest())["thread"]) == inbound.CHAIN_HOPS
            assert third

    run(go())


async def _is(newest, mid: int) -> bool:
    row = await newest()
    return row is not None and row["message_id"] == str(mid)


def test_album_is_one_record_per_photo_sharing_grouped_id(emu, dsn, tmp_path, c):
    async def go():
        async with connected(emu.url, dsn, tmp_path):
            for i in range(3):
                emu.inject(
                    int(c.group),
                    "",
                    grouped_id=777,
                    media={"kind": "photo", "name": "", "mime": "image/jpeg"},
                    data_b64=base64.b64encode(bytes([i])).decode(),
                )

            async def three():
                return len(await received(dsn, c.group)) == 3

            await until(three)
            rows = await received(dsn, c.group)
            assert {r["headers"]["grouped_id"] for r in rows} == {"777"}
            assert len({r["attachments"][0]["path"] for r in rows}) == 3

    run(go())
