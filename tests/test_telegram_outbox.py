"""The outbox: Telegram gets what a chat session sends, and nothing the
kernel wrote of its own (Tom, 2026-11-01: "No routine or system should be
impersonating you.")."""

import asyncio

import pytest

from tests.bridges import outbox
from tests.telegram_port import (
    Only,
    chat,
    connected,
    emu_chat,
    machine,
    outcome,
    release,
    report,
    send,
    until,
)

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture
def c(emu, tmp_path):
    operator, project = chat(), chat()
    emu.control(chats=[emu_chat(operator), emu_chat(project)])
    with machine(tmp_path, [operator, project]):
        yield operator, project


run = asyncio.run


def test_the_bridge_sends_a_release_and_nothing_for_a_kernel_report(emu, dsn, tmp_path, c):
    operator, project = c

    async def go():
        effect = await release(dsn, send(project, "released"))
        await report(dsn, "a report")
        async with connected(emu.url, dsn, tmp_path) as bridge, outbox(dsn, bridge) as box:
            assert all(hasattr(i, "effect_id") or hasattr(i, "reaction_id") for i in await box.due())
            consume = asyncio.create_task(bridge._consume(Only(box, {effect})))
            await until(lambda: outcome(dsn, effect))
            consume.cancel()
        assert (await outcome(dsn, effect))["result"]["sent"]
        assert [m["text"] for m in emu.own(int(project))] == ["released"] and emu.own(int(operator)) == []

    run(go())
