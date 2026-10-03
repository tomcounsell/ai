"""`TelegramBridge`: the `Bridge` the port's `serve` runs.

`run(outbox)` connects, registers the live handler, fills the gap, and
iterates the outbox: a `Release` goes to `outbox.perform`, a `NoticeDue`
is sent here. `tick()`, which `serve` calls on every `serve_tick_s` wake,
fills the gap again. The bridge keeps the connection up itself, with
backoff, and fills the gap on every connect.

On SIGTERM an in-flight perform and its outcome finish before `run`
returns.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bridges.telegram import gap, inbound
from bridges.telegram.send import CLOCK_MARGIN, Sender
from bridges.telegram.wire import FloodWait, Msg, Wire, WireError

log = logging.getLogger("valor.telegram")

BACKOFF_CAP_S = 256  # carried from main's connect loop; it never stops trying


class TelegramBridge:
    channel = "telegram"

    def __init__(self, wire: Wire, kernel, *, timeout=inbound.media_timeout):
        self.wire = wire
        self.kernel = kernel
        self.sender = Sender(wire, kernel)
        self.timeout = timeout
        self._passed: dict[int, datetime] = {}  # chat -> start of its last pass
        self._first: dict[int, datetime] = {}  # chat -> floor of its first pass
        self._connected_at: datetime | None = None
        self._busy = False
        self._filling = asyncio.Lock()
        wire.on_message(self.handle)

    def performers(self):
        return {"telegram.send_message": (self.sender.perform, self.sender.lookup)}

    # -- the loop ----------------------------------------------------------

    async def run(self, outbox) -> None:
        await self.connect()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
        consume = asyncio.create_task(self._consume(outbox))
        keep = asyncio.create_task(self._keep_connected())
        stopping = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait({consume, keep, stopping}, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t is not stopping and t.exception():
                    raise t.exception()
            while self._busy:
                await asyncio.sleep(0.05)
        finally:
            for t in (consume, keep, stopping):
                t.cancel()
            await self.wire.disconnect()

    async def _consume(self, outbox) -> None:
        async for item in outbox:
            self._busy = True
            try:
                if isinstance(item, self.kernel.Release):
                    await outbox.perform(item)
                else:
                    await self.sender.notice(item, outbox)
            finally:
                self._busy = False

    async def tick(self) -> None:
        """Gap fill on every wake; a pass under a flood wait waits for the
        next wake."""
        if self.wire.connected() and not self.sender.flood.active():
            await self.fill()

    # -- connection --------------------------------------------------------

    async def connect(self) -> None:
        """Connect, backing off up to `BACKOFF_CAP_S` with jitter and waiting
        out any flood wait; then fill the gap."""
        n = 0
        while True:
            await self.sender.flood.wait()
            try:
                await self.wire.connect()
                break
            except FloodWait as e:
                self.sender.flood.hit(e.seconds + 5)
                log.warning("connect: %s", e)
            except WireError as e:
                delay = min(2**n, BACKOFF_CAP_S) + random.uniform(0, 1)
                log.warning("connect failed (%s); next try in %.0f s", e, delay)
                await asyncio.sleep(delay)
                n += 1
        self._connected_at = datetime.now(UTC)
        await self.fill()

    async def _keep_connected(self) -> None:
        while True:
            await self.wire.disconnected()
            log.warning("disconnected; reconnecting")
            await self.connect()

    # -- receiving ---------------------------------------------------------

    async def handle(self, msg: Msg) -> None:
        """The live handler. It ends at `intake.receive`; a receive that
        fails drops the connection, so the reconnect's gap fill takes the
        message again."""
        if not inbound.wanted(msg) or not self.kernel.owns("telegram", str(msg.chat_id)):
            return
        try:
            await self._receive(msg)
        except Exception:
            log.exception("receive failed; dropping the connection so the gap fill retries")
            await self.wire.disconnect()

    async def _receive(self, msg: Msg) -> None:
        fields = await inbound.build(self.wire, msg, Path(self.kernel.inbound_dir), timeout=self.timeout)
        async with self.kernel.conn() as conn:
            await self.kernel.receive(conn, self.kernel.Inbound(**fields))
        try:
            await self.wire.mark_read(msg.chat_id, msg.id)
        except WireError as e:
            log.info("mark read failed: %s", e)

    async def fill(self) -> None:
        async with self._filling:
            try:
                for chat in self.kernel.owned("telegram"):
                    await self._fill_chat(int(chat))
            except WireError as e:
                if isinstance(e, FloodWait):
                    self.sender.flood.hit(e.seconds)
                log.warning("gap fill stopped: %s", e)
            except Exception:
                log.exception("gap fill failed; dropping the connection so the next connect retries")
                await self.wire.disconnect()

    async def _fill_chat(self, chat: int) -> None:
        started = datetime.now(UTC)
        floor, stop_id = await self._floor(chat)

        async def recorded(ids: list[str]) -> set[str]:
            async with self.kernel.conn() as conn:
                return await self.kernel.recorded(conn, "telegram", str(chat), ids)

        for msg in await gap.missing(self.wire, chat, floor=floor, stop_id=stop_id, recorded=recorded):
            if inbound.wanted(msg):
                await self._receive(msg)
        self._passed[chat] = started

    async def _floor(self, chat: int) -> tuple[datetime, int | None]:
        if chat in self._passed:
            return max(self._passed[chat] - CLOCK_MARGIN, self._first[chat]), None
        floor, stop_id = await self._first_floor(chat)
        self._first[chat] = floor
        return floor, stop_id

    async def _first_floor(self, chat: int) -> tuple[datetime, int | None]:
        """A chat with no rows starts at the first connect; otherwise the
        pass reaches back past the highest recorded message."""
        async with self.kernel.conn() as conn:
            highest = await self.kernel.highest(conn, "telegram", str(chat))
        if highest is None:
            return self._connected_at or datetime.now(UTC), None
        found = (await self.wire.get(chat, [highest]))[0]
        if found is None:
            return datetime.now(UTC), highest
        floor = found.date - timedelta(seconds=self.kernel.serve_tick_s) - CLOCK_MARGIN
        return floor, highest
