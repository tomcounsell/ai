"""`telegram.send_message`: perform, lookup, and what the broker writes."""

import asyncio
import hashlib
import time

import pytest

from bridges.telegram.send import random_id
from core.bridge import LIMITS, split_text
from core.broker import Unknown
from tests.bridges import of_type
from tests.telegram_emulator import Emulator
from tests.telegram_port import (
    SEND,
    chat,
    connected,
    emu_chat,
    key_of,
    machine,
    outcome,
    perform,
    reconcile,
    release,
    send,
)

pytestmark = pytest.mark.spend(usd=0)
MAX_TEXT = LIMITS["telegram"].max_text


@pytest.fixture
def emu():
    e = Emulator(6533).start()
    yield e
    e.stop()


@pytest.fixture
def c(emu, tmp_path):
    group, forum = chat(), chat()
    emu.control(chats=[emu_chat(group), emu_chat(forum, forum=True)])
    with machine(tmp_path, [group, forum]):
        yield group, forum


def run(coro):
    return asyncio.run(coro)


def test_random_id_repeats_for_one_effect_and_differs_between_two():
    assert random_id("effect:e1", 0) == random_id("effect:e1", 0)
    assert random_id("effect:e1", 0) != random_id("effect:e2", 0)
    assert random_id("effect:e1", 0) != random_id("effect:e1", 1)
    assert -(2**63) <= random_id("effect:e1", 0) < 2**63


def test_reply_and_topic_are_honoured_and_each_message_is_one_entry(emu, dsn, tmp_path, c):
    group, forum = c

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            perform_fn, _ = bridge.performers()[SEND]
            target = emu.inject(int(group), "the message replied to", live=False)
            out = await perform_fn(send(group, "an answer", reply_to=str(target)), "t:e1")
            [entry] = out["sent"]
            assert entry["channel"] == "telegram" and entry["chat_id"] == group
            [m] = emu.own(int(group))
            assert (str(m["id"]), m["reply_to"], m["text"]) == (entry["message_id"], target, "an answer")

            topic = emu.inject(int(forum), "topic", service=True, live=False)
            await perform_fn(send(forum, "in the topic", topic_id=str(topic)), "t:e2")
            [m] = emu.own(int(forum))
            assert (m["forum_topic"], m["reply_to"]) == (True, topic)

    run(go())


def test_long_text_goes_as_the_ports_parts(emu, dsn, tmp_path, c):
    group, _ = c

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            perform_fn, _ = bridge.performers()[SEND]
            assert len((await perform_fn(send(group, "a" * MAX_TEXT), "t:e1"))["sent"]) == 1
            long = "word " * 900 + "\n" + "b" * 200
            out = await perform_fn(send(group, long), "t:e2")
            texts = [m["text"] for m in emu.own(int(group))[1:]]
            parts = split_text("telegram", long)
            assert len(out["sent"]) == len(texts) == len(parts) == 2
            assert texts == [p.strip() for p in parts]

            emoji = "\U0001f600" * 2049  # two UTF-16 units each: 4098 units
            assert len((await perform_fn(send(group, emoji), "t:e3"))["sent"]) == 2

    run(go())


def test_a_changed_file_raises_before_anything_is_sent(emu, dsn, tmp_path, c):
    group, _ = c
    f = tmp_path / "report.pdf"
    f.write_bytes(b"approved bytes")
    digest = hashlib.sha256(b"approved bytes").hexdigest()

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            perform_fn, _ = bridge.performers()[SEND]
            f.write_bytes(b"changed bytes")
            with pytest.raises(ValueError, match="changed after approval"):
                await perform_fn(
                    send(group, "with a file", files=[{"path": str(f), "sha256": digest}]), "t:e1"
                )
            assert emu.own(int(group)) == []

            f.write_bytes(b"approved bytes")
            out = await perform_fn(
                send(group, "with a file", files=[{"path": str(f), "sha256": digest}]), "t:e2"
            )
            assert len(out["sent"]) == 2
            m = emu.own(int(group))[1]
            assert (m["media"]["name"], m["media"]["size"]) == ("report.pdf", len(b"approved bytes"))

    run(go())


def test_a_flood_wait_fails_the_send_and_the_next_waits_it_out(emu, dsn, tmp_path, c):
    group, _ = c

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            first = await release(dsn, send(group, "too fast"))
            emu.control(flood_send=1)
            out = await perform(dsn, bridge, first)
            assert out.kind == "failed" and "flood wait of 1 s" in out.error
            started = time.monotonic()
            later = await release(dsn, send(group, "later"))
            assert (await perform(dsn, bridge, later)).kind == "done"
            assert time.monotonic() - started >= 0.9
            assert [m["text"] for m in emu.own(int(group))] == ["later"]

    run(go())


def test_accepted_then_dropped_is_unknown_until_reconcile_finds_it(emu, dsn, tmp_path, c):
    group, _ = c

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            effect = await release(dsn, send(group, "did it go?"))
            emu.control(drop_reply_next=True)
            assert (await perform(dsn, bridge, effect)).kind == "unknown"
            assert await of_type(dsn, "effect.intent", effect_id=effect)
            assert await outcome(dsn, effect) is None
            assert (await reconcile(dsn, bridge, effect)).kind == "done"
            [m] = emu.own(int(group))
            assert (await outcome(dsn, effect))["result"]["sent"][0]["message_id"] == str(m["id"])

    run(go())


def test_a_duplicate_random_id_on_an_effect_is_settled_by_reconcile(emu, dsn, tmp_path, c):
    group, _ = c

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            perform_fn, _ = bridge.performers()[SEND]
            effect = await release(dsn, send(group, "sent before a crash"))
            # Sent under the effect's key, then the process died before the intent's outcome.
            await perform_fn(send(group, "sent before a crash"), await key_of(dsn, effect))
            assert (await perform(dsn, bridge, effect)).kind == "unknown"  # DuplicateRandomId
            assert (await reconcile(dsn, bridge, effect)).kind == "done"
            [m] = emu.own(int(group))
            assert (await outcome(dsn, effect))["result"] == {
                "sent": [{"channel": "telegram", "chat_id": group, "message_id": str(m["id"])}]
            }

    run(go())


def test_lookup(emu, dsn, tmp_path, c):
    group, _ = c

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            perform_fn, lookup = bridge.performers()[SEND]
            at = "2026-01-01T00:00:00+00:00"

            # Nothing recorded for the key: nothing was sent under it.
            emu.inject(int(group), "same text", out=True, sender_id=1000, live=False)
            assert await lookup(send(group, "same text"), "t:e1", at) is None

            # The same text sent before the key's first send is not adopted.
            await perform_fn(send(group, "first"), "t:e2")
            assert await lookup(send(group, "same text"), "t:e2", at) is None

            # Telegram trims; the payload's trailing newline still matches.
            await perform_fn(send(group, "trimmed\n"), "t:e3")
            [m] = [x for x in emu.own(int(group)) if x["text"] == "trimmed"]
            found = await lookup(send(group, "trimmed\n"), "t:e3", at)
            assert found["sent"][0]["message_id"] == str(m["id"])

            # Two unclaimed matches above the start: Unknown.
            await perform_fn(send(group, "twice"), "t:e4")
            emu.inject(int(group), "twice", out=True, sender_id=1000, live=False)
            with pytest.raises(Unknown):
                await lookup(send(group, "twice"), "t:e4", at)

            # Half of a split send: Unknown.
            long = "c" * 3000 + " " + "d" * 3000
            await perform_fn(send(group, "x"), "t:e5")  # records the start
            emu.inject(
                int(group), split_text("telegram", long)[0].strip(), out=True, sender_id=1000, live=False
            )
            with pytest.raises(Unknown):
                await lookup(send(group, long), "t:e5", at)

            # Telegram unreachable: Unknown, never None.
            emu.control(down=True)
            with pytest.raises(Unknown):
                await lookup(send(group, "trimmed"), "t:e3", at)
            emu.control(down=False)

    run(go())


def test_a_claimed_id_is_skipped(emu, dsn, tmp_path, c):
    group, _ = c

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            _, lookup = bridge.performers()[SEND]
            # The first effect's send is dropped; a second with the same words
            # lands above it and its outcome claims its message.
            first = await release(dsn, send(group, "same words"))
            emu.control(drop_reply_next=True)
            assert (await perform(dsn, bridge, first)).kind == "unknown"
            second = await release(dsn, send(group, "same words"))
            assert (await perform(dsn, bridge, second)).kind == "done"
            mine, theirs = [m for m in emu.own(int(group)) if m["text"] == "same words"]
            found = await lookup(send(group, "same words"), await key_of(dsn, first), "")
            assert found["sent"][0]["message_id"] == str(mine["id"]) != str(theirs["id"])

    run(go())


def test_clock_skew_a_send_dated_before_the_intent_is_found(emu, dsn, tmp_path, c):
    """The Mac's clock runs ahead of Telegram's: the message's date is
    earlier than the intent's `at`, and lookup finds it by id."""
    group, _ = c

    async def go():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            perform_fn, lookup = bridge.performers()[SEND]
            emu.control(drop_reply_next=True)
            with pytest.raises(Unknown):
                await perform_fn(send(group, "skewed"), "t:skew")
            [m] = emu.own(int(group))
            at = "2099-01-01T00:00:00+00:00"  # far after the message's date
            assert m["date"] < at
            found = await lookup(send(group, "skewed"), "t:skew", at)
            assert found["sent"][0]["message_id"] == str(m["id"])

    run(go())
