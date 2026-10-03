"""The bridge in a child process, for tests that kill it by its pid.

    python -m tests.telegram_child perform URL CHAT TEXT KEY
        perform one send; the test sets the emulator to pause after it
        accepts the send, and kills this process during the pause.
    python -m tests.telegram_child receive URL CHAT STORE MARK before|after|none
        run the bridge on a file-backed store and write MARK once connected
        and the gap filled; on the first message, write MARK.paused and
        pause before `receive` or after it (before the read
        acknowledgement), so the test kills it there.
    python -m tests.telegram_child perform-live SESSION CHAT TEXT KEY MARK
        perform one send on the real wire; write MARK and pause once
        `SendMessageRequest` has returned, so the test kills it there.
    python -m tests.telegram_child run URL CHAT STORE MARK
        run the bridge with one release queued, for SIGTERM during a
        perform; the outcome is written to STORE.outcome.

The pauses live here, in wrappers around the wire and the store; the
bridge has no test hook.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from bridges.telegram.bridge import TelegramBridge
from tests.telegram_emulator import EmulatorWire
from tests.telegram_kernel import Action, Outbox, StandIn


async def perform(url: str, chat: str, text: str, key: str) -> None:
    wire = EmulatorWire(url)
    await wire.connect()
    bridge = TelegramBridge(wire, StandIn(owned=[chat]).kernel())
    perform_fn, _ = bridge.performers()["telegram.send_message"]
    print(
        json.dumps(await perform_fn(Action("telegram.send_message", chat, {"text": text}), key)), flush=True
    )


async def perform_live(session: str, chat: str, text: str, key: str, mark: str) -> None:
    from bridges.telegram.__main__ import credentials
    from bridges.telegram.wire import TelethonWire

    api_id, api_hash = credentials()
    wire = TelethonWire(session, api_id, api_hash)
    real = wire.send_text

    async def pausing(*args, **kw):
        out = await real(*args, **kw)
        Path(mark).write_text("sent")
        await asyncio.sleep(3600)
        return out

    wire.send_text = pausing
    await wire.connect()
    bridge = TelegramBridge(wire, StandIn(owned=[chat]).kernel())
    perform_fn, _ = bridge.performers()["telegram.send_message"]
    await perform_fn(Action("telegram.send_message", chat, {"text": text}), key)


async def receive(url: str, chat: str, store: str, mark: str, where: str) -> None:
    s = StandIn(Path(store), owned=[chat])
    real = s.receive

    async def pausing(conn, inbound):
        if where == "before":
            Path(mark + ".paused").write_text("paused")
            await asyncio.sleep(3600)
        out = await real(conn, inbound)
        if where == "after":
            Path(mark + ".paused").write_text("paused")
            await asyncio.sleep(3600)
        return out

    if where != "none":
        s.receive = pausing
    bridge = TelegramBridge(EmulatorWire(url), s.kernel(), seen=Path(store + ".seen"))
    await bridge.connect()
    Path(mark).write_text("connected")
    await asyncio.sleep(3600)


async def run(url: str, chat: str, store: str, mark: str) -> None:
    s = StandIn(owned=[chat])
    wire = EmulatorWire(url)
    bridge = TelegramBridge(wire, s.kernel())
    outbox = Outbox(s, bridge)
    real = outbox.perform

    async def marking(item):
        Path(mark).write_text("performing")
        out = await real(item)
        Path(store + ".outcome").write_text(json.dumps(out))
        return out

    outbox.perform = marking
    outbox.release(
        "e1", Action("telegram.send_message", chat, {"text": "slow send"}), "2026-01-01T00:00:00+00:00"
    )
    await bridge.run(outbox)


def main(argv: list[str]) -> None:
    mode, *rest = argv
    if mode == "perform":
        asyncio.run(perform(*rest))
    elif mode == "perform-live":
        asyncio.run(perform_live(*rest))
    elif mode == "receive":
        asyncio.run(receive(*rest))
    elif mode == "run":
        asyncio.run(run(*rest))


if __name__ == "__main__":
    main(sys.argv[1:])
