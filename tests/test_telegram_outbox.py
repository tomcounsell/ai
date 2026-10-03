"""Notices: sent to the row's chat, once, and marked through the outbox."""

import asyncio
import logging

import pytest

from bridges.telegram.send import random_id
from tests.bridges import of_type, outbox
from tests.telegram_emulator import Emulator
from tests.telegram_port import (
    Only,
    chat,
    connected,
    due,
    emu_chat,
    machine,
    notice,
    outcome,
    release,
    send,
    until,
)

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture
def emu():
    e = Emulator(6534).start()
    yield e
    e.stop()


@pytest.fixture
def c(emu, tmp_path):
    operator, project = chat(), chat()
    emu.control(chats=[emu_chat(operator), emu_chat(project)])
    with machine(tmp_path, [operator, project]):
        yield operator, project


def run(coro):
    return asyncio.run(coro)


async def marked(dsn, notice_id):
    return await of_type(dsn, "notice.sent", notice_id=notice_id)


class Dies:
    """The process dies between the send and `notice.sent`."""

    async def sent(self, item, sent):
        raise SystemExit("killed")


def test_a_notice_goes_to_the_rows_chat_and_is_marked(emu, dsn, tmp_path, c):
    operator, project = c

    async def go():
        _, nid = await notice(dsn, project)
        async with connected(emu.url, dsn, tmp_path) as bridge, outbox(dsn, bridge) as box:
            await bridge.sender.notice(await due(box, nid), box)
        [m] = emu.own(int(project))
        assert m["text"].endswith(f"[n:{nid}]")
        assert emu.own(int(operator)) == []
        [row] = await marked(dsn, nid)
        assert row["sent"] == [{"channel": "telegram", "chat_id": project, "message_id": str(m["id"])}]

    run(go())


def test_killed_between_send_and_sent_the_restart_finds_it_and_sends_nothing(emu, dsn, tmp_path, c):
    _, project = c

    async def go():
        _, nid = await notice(dsn, project)
        async with connected(emu.url, dsn, tmp_path) as bridge, outbox(dsn, bridge) as box:
            with pytest.raises(SystemExit):
                await bridge.sender.notice(await due(box, nid), Dies())
        async with connected(emu.url, dsn, tmp_path) as bridge, outbox(dsn, bridge) as box:
            await bridge.sender.notice(await due(box, nid), box)
        [m] = emu.own(int(project))
        assert (await marked(dsn, nid))[0]["sent"][0]["message_id"] == str(m["id"])

    run(go())


def test_a_duplicate_random_id_with_nothing_on_screen_sends_it_under_a_new_id(emu, dsn, tmp_path, c):
    _, project = c

    async def go():
        _, nid = await notice(dsn, project)
        async with connected(emu.url, dsn, tmp_path) as bridge, outbox(dsn, bridge) as box:
            # Telegram holds the notice's random_id for a message the scan cannot match.
            await bridge.wire.send_text(
                int(project),
                "something else",
                random_id=random_id(f"notice:{nid}", 0),
                reply_to=None,
                topic_id=None,
            )
            item = await due(box, nid)
            await bridge.sender.notice(item, box)
        shown = [m for m in emu.own(int(project)) if m["text"] == item.text]
        assert len(shown) == 1
        assert (await marked(dsn, nid))[0]["sent"][0]["message_id"] == str(shown[0]["id"])

    run(go())


def test_a_notice_failing_every_time_logs_its_reason_once(emu, dsn, tmp_path, c, caplog):
    _, project = c

    async def go():
        _, nid = await notice(dsn, project)
        async with connected(emu.url, dsn, tmp_path) as bridge, outbox(dsn, bridge) as box:
            item = await due(box, nid)
            emu.control(down=True)
            with caplog.at_level(logging.WARNING, logger="valor.telegram"):
                for _ in range(3):
                    await bridge.sender.notice(item, box)
            emu.control(down=False)
        assert await marked(dsn, nid) == []
        assert len([r for r in caplog.records if nid in r.getMessage()]) == 1

    run(go())


def test_the_bridge_consumes_releases_and_notices_from_the_outbox(emu, dsn, tmp_path, c):
    _, project = c

    async def go():
        effect = await release(dsn, send(project, "released"))
        _, nid = await notice(dsn, project, "a notice")
        async with connected(emu.url, dsn, tmp_path) as bridge, outbox(dsn, bridge) as box:
            consume = asyncio.create_task(bridge._consume(Only(box, {effect, nid})))

            async def both():
                return await outcome(dsn, effect) is not None and await marked(dsn, nid)

            await until(both)
            consume.cancel()
        assert (await outcome(dsn, effect))["result"]["sent"]
        assert sorted(m["text"].split("\n")[0] for m in emu.own(int(project))) == ["a notice", "released"]

    run(go())
