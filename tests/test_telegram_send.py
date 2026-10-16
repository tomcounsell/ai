"""`telegram.send_message`: perform, lookup, and what the broker writes."""

import asyncio

import pytest

from bridges.telegram.send import random_id
from tests.bridges import of_type
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


@pytest.fixture
def c(emu, tmp_path):
    group, forum = chat(), chat()
    emu.control(chats=[emu_chat(group), emu_chat(forum, forum=True)])
    with machine(tmp_path, [group, forum]):
        yield group, forum


run = asyncio.run


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
