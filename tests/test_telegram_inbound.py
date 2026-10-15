"""From a Telegram message to a `message.received` row, through the
emulator and the real intake."""

import asyncio
import base64
from types import SimpleNamespace

import pytest

from tests.telegram_emulator import Emulator
from tests.telegram_port import chat, connected, emu_chat, machine, received, until

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture(scope="module")
def emu():
    e = Emulator().start()
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

            # The walk goes to the root, however long the chain.
            root = prev = emu.inject(int(c.group), "root", live=False)
            for i in range(40):
                prev = emu.inject(int(c.group), f"hop {i}", reply_to=prev, live=False)
            leaf = emu.inject(int(c.group), "leaf", reply_to=prev)
            await until(lambda: _is(newest, leaf))
            chain = (await newest())["thread"]
            assert len(chain) == 41 and chain[0]["id"] == str(root)
            assert third

    run(go())


async def _is(newest, mid: int) -> bool:
    row = await newest()
    return row is not None and row["message_id"] == str(mid)
