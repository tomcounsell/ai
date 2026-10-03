"""`TelegramBridge`: the `Bridge` the port's `serve` runs.

`run(outbox)` connects, registers the live handler, fills the gap, and
iterates the outbox: a `Release` goes to `outbox.perform`, a `NoticeDue`
is sent here. `tick()`, which `serve` calls on every `serve_tick_s` wake,
fills the gap again and drops the record of every send the ledger has
settled. The bridge keeps the connection up itself and fills the gap on
every connect.

Reconnecting: a dropped connection is reconnected at once. When a connect
fails, or a receive or a gap-fill pass fails, the next connect waits one
`serve_tick_s` first, the same wake the outbox runs on; the wait ends
when a gap-fill pass completes. A flood wait on connect is waited out for
exactly the seconds Telegram gives.

A message with a file is received off the update stream: its download
runs as its own task, so a slow download never holds up the messages
behind it. A download ends when the file is in, when the connection fails
(Telethon pings every 60 s and drops a connection whose ping went
unanswered), or when the bridge stops. A download from another data
centre has no ping of its own, so on each `tick` a download that received
no bytes since the previous `tick` is cancelled. A download that ends
without the file is retaken by the next gap-fill pass: a pass never
records a chat as seen past a message whose receive is still running.

On SIGTERM an in-flight perform and its outcome finish before `run`
returns; downloads still running are stopped and retaken on the next
start.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from bridges.telegram import gap, inbound
from bridges.telegram.send import Sender, notice_key
from bridges.telegram.state import State
from bridges.telegram.wire import FloodWait, Msg, Wire, WireError

log = logging.getLogger("valor.telegram")


class TelegramBridge:
    channel = "telegram"

    def __init__(
        self,
        wire: Wire,
        kernel,
        *,
        seen: Path | None = None,
        sends: Path | None = None,
    ):
        self.wire = wire
        self.kernel = kernel
        self.sender = Sender(wire, kernel, State(sends))
        # chat -> newest id its last completed pass saw. A lost file
        # starts empty: each chat's pass then starts at its lowest row in
        # the ledger.
        self._seen = State(seen)
        self._busy = False
        self._filling = asyncio.Lock()
        self._failing = False  # a connect, receive or pass failed since the last completed pass
        self._inflight: dict[tuple[int, int], asyncio.Task] = {}
        # message -> [bytes received so far, bytes received at the last tick or None]
        self._received: dict[tuple[int, int], list] = {}
        self._outbox = None
        wire.on_message(self.handle)

    def performers(self):
        return {"telegram.send_message": (self.sender.perform, self.sender.lookup)}

    # -- the loop ----------------------------------------------------------

    async def run(self, outbox) -> None:
        self._outbox = outbox
        await self.tidy()
        await self.connect()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, stop.set)
        except NotImplementedError, RuntimeError:
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
            for t in (consume, keep, stopping, *self._inflight.values()):
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
        """On every wake: drop settled send records, and fill the gap; a
        pass under a flood wait waits for the next wake."""
        await self.tidy()
        self._cancel_stalled()
        if self.wire.connected() and not self.sender.flood.active():
            await self.fill()

    def _cancel_stalled(self) -> None:
        """Cancel each download that received no bytes since the last tick;
        the gap fill retakes it."""
        for where, task in list(self._inflight.items()):
            got = self._received.get(where)
            if got is None:
                continue
            if got[1] is not None and got[0] == got[1]:
                log.warning(
                    "download of message %s in chat %s received nothing since the last tick",
                    where[1],
                    where[0],
                )
                task.cancel()
            got[1] = got[0]

    async def tidy(self) -> None:
        """Keep the record of each send still in flight: an effect with an
        intent and no outcome, or a notice not yet marked sent."""
        if self._outbox is None:
            return
        try:
            async with self.kernel.conn() as conn:
                effects = await self.kernel.dangling(conn, self._outbox.types())
            notices = [i for i in await self._outbox.due() if isinstance(i, self.kernel.NoticeDue)]
        except Exception:
            log.exception("could not read the sends in flight; their records stay")
            return
        self.sender.keep_only(set(effects) | {notice_key(n.notice_id) for n in notices})

    async def settled(self) -> None:
        """Until every receive running off the update stream has ended."""
        while self._inflight:
            await asyncio.gather(*self._inflight.values(), return_exceptions=True)

    # -- connection --------------------------------------------------------

    async def connect(self) -> None:
        """Connect, then fill the gap. After a failure each try waits one
        `serve_tick_s`; a flood wait is waited out as Telegram gives it."""
        while True:
            await self.sender.flood.wait()
            if self._failing:
                await asyncio.sleep(self.kernel.serve_tick_s)
            try:
                await self.wire.connect()
                break
            except FloodWait as e:
                self.sender.flood.hit(e.seconds)
                log.warning("connect: %s", e)
            except WireError as e:
                self._failing = True
                log.warning("connect failed (%s); next try in %s s", e, self.kernel.serve_tick_s)
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
        message again. A message with a file is received off the stream."""
        if not inbound.wanted(msg) or not self.kernel.owns("telegram", str(msg.chat_id)):
            return
        if msg.media is not None:
            self._receive_aside(msg)
            return
        try:
            await self._receive(msg)
        except Exception:
            log.exception("receive failed; dropping the connection so the gap fill retries")
            self._failing = True
            await self.wire.disconnect()

    def _receive_aside(self, msg: Msg) -> None:
        where = (msg.chat_id, msg.id)
        if where in self._inflight:
            return

        got = self._received[where] = [0, None]

        def progress(received: int, total: int) -> None:
            got[0] = received

        async def receive():
            try:
                await self._receive(msg, progress)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "receive of message %s in chat %s failed; the gap fill retakes it", msg.id, msg.chat_id
                )
            finally:
                self._inflight.pop(where, None)
                self._received.pop(where, None)

        self._inflight[where] = asyncio.create_task(receive())

    async def _receive(self, msg: Msg, progress=None) -> None:
        fields = await inbound.build(self.wire, msg, Path(self.kernel.inbound_dir), progress)
        async with self.kernel.conn() as conn:
            await self.kernel.receive(conn, self.kernel.Inbound(**fields))
        try:
            await self.wire.mark_read(msg.chat_id, msg.id)
        except WireError as e:
            log.info("mark read failed: %s", e)

    async def fill(self) -> bool:
        """One gap-fill pass over the owned chats; true when it completed."""
        async with self._filling:
            try:
                for chat in self.kernel.owned("telegram"):
                    try:
                        chat_id = int(chat)
                    except ValueError:
                        log.warning("owned chat %r is not a Telegram chat id", chat)
                        continue
                    await self._fill_chat(chat_id)
            except WireError as e:
                if isinstance(e, FloodWait):
                    self.sender.flood.hit(e.seconds)
                log.warning("gap fill stopped: %s", e)
                self._failing = True
                return False
            except Exception:
                log.exception("gap fill failed; dropping the connection so the next connect retries")
                self._failing = True
                await self.wire.disconnect()
                return False
            self._failing = False
            return True

    async def _fill_chat(self, chat: int) -> None:
        stop_id = self._seen.get(str(chat))
        if stop_id is None:
            async with self.kernel.conn() as conn:
                stop_id = await self.kernel.lowest(conn, "telegram", str(chat))
        if stop_id is None:
            # No rows: nothing before this connect is wanted.
            page = await self.wire.history(chat, limit=1)
            top = page[0].id if page else 0
            held = [i for c, i in self._inflight if c == chat]
            if held:
                top = min(top, min(held) - 1)
            self._seen.set(str(chat), top)
            return

        async def recorded(ids: list[str]) -> set[str]:
            async with self.kernel.conn() as conn:
                return await self.kernel.recorded(conn, "telegram", str(chat), ids)

        found, top = await gap.missing(self.wire, chat, stop_id=stop_id, recorded=recorded)
        held = []
        for msg in found:
            if not inbound.wanted(msg):
                continue
            if (chat, msg.id) in self._inflight:
                held.append(msg.id)
            elif msg.media is not None:
                self._receive_aside(msg)
                held.append(msg.id)
            else:
                await self._receive(msg)
        held += [i for c, i in self._inflight if c == chat]
        if held:
            top = min(top, min(held) - 1)
        self._seen.set(str(chat), max(top, stop_id))
