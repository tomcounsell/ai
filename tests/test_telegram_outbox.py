"""Notices: sent to the row's chat, once, and marked through the outbox."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from bridges.telegram.send import random_id
from tests.telegram_emulator import Emulator
from tests.telegram_kernel import NoticeDue, Outbox, connected

OPERATOR = "-1006"
PROJECT = "-1007"


@pytest.fixture
def emu():
    e = Emulator(6534).start()
    e.control(chats=[{"id": int(OPERATOR), "kind": "supergroup"}, {"id": int(PROJECT), "kind": "supergroup"}])
    yield e
    e.stop()


def run(coro):
    return asyncio.run(coro)


def due(notice_id: str, chat: str = PROJECT, text: str | None = None) -> NoticeDue:
    return NoticeDue(
        notice_id=notice_id,
        task_id="task-1",
        chat_id=chat,
        text=text or f"[{notice_id}] task-1 is waiting on approval",
        reply_to=None,
        at=datetime.now(UTC).isoformat(),
    )


class Dies(Outbox):
    """The process dies between the send and `notice.sent`."""

    async def sent(self, item, sent):
        raise SystemExit("killed")


def test_a_notice_goes_to_the_rows_chat_and_is_marked(emu):
    async def go():
        async with connected(emu.url, [OPERATOR, PROJECT]) as (bridge, store):
            outbox = Outbox(store, bridge)
            await bridge.sender.notice(due("n1"), outbox)
            [m] = emu.own(int(PROJECT))
            assert emu.own(int(OPERATOR)) == []
            assert outbox.notices["n1"] == [
                {"channel": "telegram", "chat_id": PROJECT, "message_id": str(m["id"])}
            ]

    run(go())


def test_killed_between_send_and_sent_the_restart_finds_it_and_sends_nothing(emu):
    item = due("n2")

    async def go():
        async with connected(emu.url, [PROJECT]) as (bridge, store):
            with pytest.raises(SystemExit):
                await bridge.sender.notice(item, Dies(store, bridge))
        async with connected(emu.url, [PROJECT]) as (bridge, store):
            outbox = Outbox(store, bridge)
            await bridge.sender.notice(item, outbox)
            [m] = emu.own(int(PROJECT))
            assert outbox.notices["n2"][0]["message_id"] == str(m["id"])

    run(go())


def test_a_duplicate_random_id_with_nothing_on_screen_sends_it_under_a_new_id(emu):
    item = due("n3")

    async def go():
        async with connected(emu.url, [PROJECT]) as (bridge, store):
            # Telegram holds the notice's random_id for a message the scan cannot match.
            await bridge.wire.send_text(
                int(PROJECT),
                "something else",
                random_id=random_id("notice:n3", 0),
                reply_to=None,
                topic_id=None,
            )
            outbox = Outbox(store, bridge)
            await bridge.sender.notice(item, outbox)
            notices = [m for m in emu.own(int(PROJECT)) if m["text"] == item.text]
            assert len(notices) == 1
            assert outbox.notices["n3"][0]["message_id"] == str(notices[0]["id"])

    run(go())


def test_a_notice_failing_every_time_logs_its_reason_once(emu, caplog):
    async def go():
        async with connected(emu.url, [PROJECT]) as (bridge, store):
            outbox = Outbox(store, bridge)
            emu.control(down=True)
            with caplog.at_level(logging.WARNING, logger="valor.telegram"):
                for _ in range(3):
                    await bridge.sender.notice(due("n4"), outbox)
            emu.control(down=False)
            assert "n4" not in outbox.notices
            assert len([r for r in caplog.records if "n4" in r.getMessage()]) == 1

    run(go())


def test_the_bridge_consumes_releases_and_notices_from_the_outbox(emu):
    from tests.telegram_kernel import Action

    async def go():
        async with connected(emu.url, [PROJECT]) as (bridge, store):
            outbox = Outbox(store, bridge)
            outbox.release(
                "e1",
                Action("telegram.send_message", PROJECT, {"text": "released"}),
                datetime.now(UTC).isoformat(),
            )
            outbox.notice(due("n5"))
            consume = asyncio.create_task(bridge._consume(outbox))
            for _ in range(200):
                if "e1" in outbox.outcomes and "n5" in outbox.notices:
                    break
                await asyncio.sleep(0.02)
            consume.cancel()
            assert outbox.outcomes["e1"][0] == "done"
            assert sorted(m["text"] for m in emu.own(int(PROJECT))) == sorted(["released", due("n5").text])

    run(go())
