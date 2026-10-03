"""`telegram.send_message`: perform, lookup, and what the broker writes."""

import asyncio
import hashlib
import time
from datetime import UTC, datetime, timedelta

import pytest

from bridges.telegram.send import random_id
from core.broker import Unknown
from tests.telegram_emulator import Emulator
from tests.telegram_kernel import MAX_TEXT, Action, Outbox, connected, split_text, utf16_len

CHAT = "-1004"
FORUM = "-1005"
SEND = "telegram.send_message"


@pytest.fixture
def emu():
    e = Emulator(6533).start()
    e.control(
        chats=[
            {"id": int(CHAT), "kind": "supergroup"},
            {"id": int(FORUM), "kind": "supergroup", "forum": True},
        ]
    )
    yield e
    e.stop()


def run(coro):
    return asyncio.run(coro)


def now(delta_s: float = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delta_s)).isoformat()


def send(text: str = "", chat: str = CHAT, **payload) -> Action:
    return Action(SEND, chat, {"text": text, "reply_to": None, "topic_id": None, "files": [], **payload})


def test_random_id_repeats_for_one_effect_and_differs_between_two():
    assert random_id("task-1:e1", 0) == random_id("task-1:e1", 0)
    assert random_id("task-1:e1", 0) != random_id("task-1:e2", 0)
    assert random_id("task-1:e1", 0) != random_id("task-1:e1", 1)
    assert -(2**63) <= random_id("task-1:e1", 0) < 2**63


def test_reply_and_topic_are_honoured_and_each_message_is_one_entry(emu):
    async def go():
        async with connected(emu.url, [CHAT, FORUM]) as (bridge, _):
            perform, _ = bridge.performers()[SEND]
            target = emu.inject(int(CHAT), "the message replied to", live=False)
            out = await perform(send("an answer", reply_to=str(target)), "t:e1")
            [entry] = out["sent"]
            assert entry["channel"] == "telegram" and entry["chat_id"] == CHAT
            [m] = emu.own(int(CHAT))
            assert (str(m["id"]), m["reply_to"], m["text"]) == (entry["message_id"], target, "an answer")

            topic = emu.inject(int(FORUM), "topic", service=True, live=False)
            await perform(send("in the topic", chat=FORUM, topic_id=str(topic)), "t:e2")
            [m] = emu.own(int(FORUM))
            assert (m["forum_topic"], m["reply_to"]) == (True, topic)

    run(go())


def test_long_text_goes_as_the_ports_parts(emu):
    async def go():
        async with connected(emu.url, [CHAT]) as (bridge, _):
            perform, _ = bridge.performers()[SEND]
            assert len((await perform(send("a" * MAX_TEXT), "t:e1"))["sent"]) == 1
            long = "word " * 900 + "\n" + "b" * 200
            out = await perform(send(long), "t:e2")
            texts = [m["text"] for m in emu.own(int(CHAT))[1:]]
            assert len(out["sent"]) == len(texts) == len(split_text(long)) == 2
            assert texts == [p.strip() for p in split_text(long)]

    run(go())


def test_the_ports_split_counts_utf16_units():
    assert split_text("a" * 4096) == ["a" * 4096]
    parts = split_text("a" * 4097)
    assert len(parts) == 2 and "".join(parts) == "a" * 4097
    emoji = "\U0001f600" * 2049
    parts = split_text(emoji)
    assert len(parts) == 2 and all(utf16_len(p) <= 4096 for p in parts) and "".join(parts) == emoji
    parts = split_text("x" * 3000 + "\n" + "y" * 3000)
    assert parts == ["x" * 3000 + "\n", "y" * 3000]


def test_a_changed_file_raises_before_anything_is_sent(emu, tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"approved bytes")
    digest = hashlib.sha256(b"approved bytes").hexdigest()

    async def go():
        async with connected(emu.url, [CHAT]) as (bridge, _):
            perform, _ = bridge.performers()[SEND]
            f.write_bytes(b"changed bytes")
            with pytest.raises(ValueError, match="changed after approval"):
                await perform(send("with a file", files=[{"path": str(f), "sha256": digest}]), "t:e1")
            assert emu.own(int(CHAT)) == []

            f.write_bytes(b"approved bytes")
            out = await perform(send("with a file", files=[{"path": str(f), "sha256": digest}]), "t:e2")
            assert len(out["sent"]) == 2
            m = emu.own(int(CHAT))[1]
            assert (m["media"]["name"], m["media"]["size"]) == ("report.pdf", len(b"approved bytes"))

    run(go())


def test_a_flood_wait_fails_the_send_and_the_next_waits_it_out(emu):
    async def go():
        async with connected(emu.url, [CHAT]) as (bridge, store):
            outbox = Outbox(store, bridge)
            emu.control(flood_send=1)
            item = outbox.release("e1", send("too fast"), now())
            assert await outbox.perform(item) == ("failed", "flood wait of 1 s")
            started = time.monotonic()
            item = outbox.release("e2", send("later"), now())
            assert (await outbox.perform(item))[0] == "done"
            assert time.monotonic() - started >= 0.9
            assert [m["text"] for m in emu.own(int(CHAT))] == ["later"]

    run(go())


def test_accepted_then_dropped_is_unknown_until_reconcile_finds_it(emu):
    async def go():
        async with connected(emu.url, [CHAT]) as (bridge, store):
            outbox = Outbox(store, bridge)
            item = outbox.release("e1", send("did it go?"), now())
            emu.control(drop_reply_next=True)
            assert await outbox.perform(item) is None
            assert "e1" in outbox.in_flight and "e1" not in outbox.outcomes
            await outbox.reconcile()
            state, result = outbox.outcomes["e1"]
            [m] = emu.own(int(CHAT))
            assert state == "done" and result["sent"][0]["message_id"] == str(m["id"])

    run(go())


def test_a_duplicate_random_id_on_an_effect_is_settled_by_reconcile(emu):
    async def go():
        async with connected(emu.url, [CHAT]) as (bridge, store):
            outbox = Outbox(store, bridge)
            perform, _ = bridge.performers()[SEND]
            at = now()
            await perform(send("sent before a crash"), outbox.key("e1"))  # no outcome written
            item = outbox.release("e1", send("sent before a crash"), at)
            assert await outbox.perform(item) is None  # DuplicateRandomId: Unknown
            await outbox.reconcile()
            [m] = emu.own(int(CHAT))
            assert outbox.outcomes["e1"] == (
                "done",
                {"sent": [{"channel": "telegram", "chat_id": CHAT, "message_id": str(m["id"])}]},
            )

    run(go())


def test_lookup(emu):
    async def go():
        async with connected(emu.url, [CHAT]) as (bridge, store):
            _, lookup = bridge.performers()[SEND]
            emu.inject(int(CHAT), "same text", out=True, sender_id=1000, ago_s=300, live=False)
            # Dated before `since` less the margin: not adopted.
            assert await lookup(send("same text"), "t:e1", now()) is None

            mid = emu.inject(int(CHAT), "trimmed", out=True, sender_id=1000, live=False)
            # Telegram trims; the payload's trailing newline still matches.
            found = await lookup(send("trimmed\n"), "t:e2", now(-5))
            assert found["sent"][0]["message_id"] == str(mid)

            # A claimed id is skipped.
            store.claim(CHAT, [str(mid)])
            assert await lookup(send("trimmed"), "t:e3", now(-5)) is None

            # A message older than a later claimed one is still found.
            older = emu.inject(int(CHAT), "older one", out=True, sender_id=1000, live=False)
            newer = emu.inject(int(CHAT), "newer one", out=True, sender_id=1000, live=False)
            store.claim(CHAT, [str(newer)])
            found = await lookup(send("older one"), "t:e4", now(-5))
            assert found["sent"][0]["message_id"] == str(older)

            # Two unclaimed matches: Unknown.
            emu.inject(int(CHAT), "twice", out=True, sender_id=1000, live=False)
            emu.inject(int(CHAT), "twice", out=True, sender_id=1000, live=False)
            with pytest.raises(Unknown):
                await lookup(send("twice"), "t:e5", now(-5))

            # Half of a split send: Unknown.
            long = "c" * 3000 + " " + "d" * 3000
            first = split_text(long)[0].strip()
            emu.inject(int(CHAT), first, out=True, sender_id=1000, live=False)
            with pytest.raises(Unknown):
                await lookup(send(long), "t:e6", now(-5))

            # Telegram unreachable: Unknown, never None.
            emu.control(down=True)
            with pytest.raises(Unknown):
                await lookup(send("anything"), "t:e7", now(-5))
            emu.control(down=False)

    run(go())
