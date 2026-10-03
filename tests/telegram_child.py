"""The bridge in a child process, for tests that kill it by its pid.

Each mode runs over the test database named by DSN, with the bridge's
state files in STATE (a directory), under the settings the test passes in
the environment (`telegram_port.child_env`).

    python -m tests.telegram_child perform URL DSN STATE EFFECT [after|before]
        perform one released send through the outbox. After: the test
        sets the emulator to pause after it accepts the send, and kills
        this process during the pause. Before: the send pauses before it
        reaches the emulator, for the test to kill it there.
    python -m tests.telegram_child receive URL DSN STATE MARK before|after|none
        run the bridge and write MARK once connected and the gap filled;
        on the first message, write MARK.paused and pause before
        `receive` or after it (before the read acknowledgement), so the
        test kills it there.
    python -m tests.telegram_child perform-live SESSION DSN STATE CHAT TEXT KEY MARK
        perform one send on the real wire; write MARK and pause once
        `SendMessageRequest` has returned, so the test kills it there.
    python -m tests.telegram_child run URL DSN STATE MARK TEXT
        `serve` the bridge; write MARK when the send of TEXT starts, for
        SIGTERM during a perform.

The pauses live here, in wrappers around the wire and the port; the
bridge has no test hook.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

from bridges.telegram.bridge import TelegramBridge
from bridges.telegram.kernel import from_core
from core import bridge as port
from core import broker, db
from tests.telegram_emulator import EmulatorWire


def paths(state: str) -> dict[str, Path]:
    return {"seen": Path(state) / "telegram-seen.json", "sends": Path(state) / "telegram-sends.json"}


async def perform(url: str, dsn: str, state: str, effect: str, where: str = "after") -> None:
    wire = EmulatorWire(url)
    if where == "before":
        real = wire.send_text

        async def pausing(*args, **kw):
            await asyncio.sleep(3600)
            return await real(*args, **kw)

        wire.send_text = pausing
    bridge = TelegramBridge(wire, from_core(dsn), **paths(state))
    await bridge.connect()
    conn, perform_conn, listener = [await db.connect(dsn) for _ in range(3)]
    box = port.Outbox(bridge, port.bound_performers(bridge, conn), conn, perform_conn, listener)
    (item,) = [i for i in await box.due() if getattr(i, "effect_id", None) == effect]
    await box.perform(item)


async def perform_live(session: str, dsn: str, state: str, chat: str, text: str, key: str, mark: str) -> None:
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
    bridge = TelegramBridge(wire, from_core(dsn), **paths(state))
    perform_fn, _ = bridge.performers()["telegram.send_message"]
    await perform_fn(broker.Action("telegram.send_message", chat, {"text": text}), key)


async def receive(url: str, dsn: str, state: str, mark: str, where: str) -> None:
    kernel = from_core(dsn)
    real = kernel.receive

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
        kernel = dataclasses.replace(kernel, receive=pausing)
    bridge = TelegramBridge(EmulatorWire(url), kernel, **paths(state))
    await bridge.connect()
    Path(mark).write_text("connected")
    await asyncio.sleep(3600)


async def run(url: str, dsn: str, state: str, mark: str, text: str) -> None:
    wire = EmulatorWire(url)
    real = wire.send_text

    async def marking(chat, part, **kw):
        if part == text:
            Path(mark).write_text("performing")
        return await real(chat, part, **kw)

    wire.send_text = marking
    await port.serve(TelegramBridge(wire, from_core(dsn), **paths(state)), dsn)


def main(argv: list[str]) -> None:
    mode, *rest = argv
    asyncio.run(
        {"perform": perform, "perform-live": perform_live, "receive": receive, "run": run}[mode](*rest)
    )


if __name__ == "__main__":
    main(sys.argv[1:])
