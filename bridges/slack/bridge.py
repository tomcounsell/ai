"""`SlackBridge`: the `Bridge` the port's `serve` runs.

`run(outbox)` connects (`auth.test`, then the socket), and iterates the
outbox: a `Release` goes to `outbox.perform`, a `NoticeDue` is sent here,
and a `ReactionDue` is placed here.
`tick()`, which `serve` calls on every `serve_tick_s` wake, fills the
gap; so does each new socket session (Slack's `hello`). `slack_sdk`'s
session monitor keeps the socket up.

Receiving: every envelope that is not a message, or is a message that is
not a person's in an owned chat, is acked on arrival, so Slack never
turns event delivery off. A wanted message runs as its own task and is
acked once its `intake.receive` commits. An envelope redelivered while
the first delivery of its `(chat, ts)` is still being received waits for
it: it is acked once that commits, and received itself if that fails. A
receive that fails is not acked, so Slack retries it; its `ts` holds the
chat's seen `ts` and its thread root joins the next pass's thread list,
so gap fill takes it once Slack's retries run out.

Gap fill, per owned chat in the connected workspace: history from the
chat's seen `ts`, then the replies from that `ts` in every thread with
recent activity (a parent in the history whose `latest_reply` is newer,
the threads of the chat's ledger rows from the last 14 days, and the
roots of failed receives). The chat's seen `ts` moves only once every
listed thread is read, and is held at the `ts` of any receive still
running or failed, read again with `inclusive`. A chat with no seen
entry starts at its lowest recorded `ts`; with no rows either, its seen
`ts` is set to its newest and nothing is recorded. The pass is budgeted
per method (`gap.Budget`) and resumes where it stopped. Its place in a
chat moves only once the page it read is recorded, so a failed ledger
read leaves the page to be read again. Slack refusing a read (a deleted
thread root, a channel Valor was taken out of) drops that thread, or
that chat's pass, and the pass goes on.

On SIGTERM an in-flight perform and its outcome finish before `run`
returns; receives still running are stopped, unacked, and Slack or the
next gap fill delivers them again.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from bridges.slack import gap, inbound
from bridges.slack.send import Sender
from bridges.slack.state import Seen
from bridges.slack.wire import Refused, SlackWire, WireError

log = logging.getLogger("valor.slack")


class SlackBridge:
    channel = "slack"

    def __init__(
        self,
        wire: SlackWire,
        kernel,
        *,
        seen: Path | None = None,
        budget: gap.Budget | None = None,
        sender: Sender | None = None,
    ):
        self.wire = wire
        self.kernel = kernel
        self.sender = sender or Sender(wire, kernel)
        self.budget = budget or gap.Budget()
        self._seen = Seen(seen)
        self._passes: dict[str, gap.Pass] = {}
        self._filling = asyncio.Lock()
        self._busy = False
        # (chat, ts) -> the first receive's result: true once it commits
        self._inflight: dict[tuple[str, str], asyncio.Future] = {}
        # (chat, ts) -> thread root (or its own ts) of a receive that failed
        self._failed: dict[tuple[str, str], str] = {}
        self._tasks: set[asyncio.Task] = set()

    def performers(self):
        return {"slack.send_message": (self.sender.perform, self.sender.lookup)}

    async def read(self, chat: str, q: str | None, limit: int, before: str | None) -> list[dict]:
        """A chat's messages for a turn (`core.bridge.reads`), newest first.
        A search pages by number on Slack, so `before` applies only without
        one."""
        ident = chat.partition("/")[2]
        if not ident:
            raise ValueError("a Slack chat is <team>/<channel>, or <team>/<user> for a DM")
        dm = inbound.is_dm(chat)
        channel = await self.wire.open_im(ident) if dm else ident
        if q is None:
            found, _ = await self.wire.history(channel, latest=before, limit=limit)
        elif before is not None:
            raise ValueError("before does not apply to a Slack search")
        else:
            where = f"<@{ident}>" if dm else f"<#{channel}>"
            matches = await self.wire.search(f"{q} in:{where}", count=limit)
            found = [m for m in matches if (m.get("channel") or {}).get("id") == channel]
        return [await self._shown(m) for m in found]

    async def _shown(self, m: dict[str, Any]) -> dict:
        """One message as `valor-slack read` prints it; a search match
        carries its thread only in its permalink."""
        thread = (
            m.get("thread_ts")
            or (parse_qs(urlsplit(m.get("permalink") or "").query).get("thread_ts") or [None])[0]
        )
        user = m.get("user") or ""
        files = m.get("files") or []
        return {
            "id": m["ts"],
            "sent_at": datetime.fromtimestamp(float(m["ts"]), UTC).isoformat(),
            "sender_id": user,
            "sender_name": (await self.wire.user_name(user) if user else "") or m.get("username", ""),
            "out": bool(user) and user == self.wire.user_id,
            "reply_to": thread if thread != m["ts"] else None,
            "text": m.get("text") or "",
            "media": {"kind": "document", "name": files[0].get("name", "")} if files else None,
        }

    # -- the loop ----------------------------------------------------------

    async def run(self, outbox) -> None:
        await self.connect()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, stop.set)
        except NotImplementedError, RuntimeError:
            pass
        consume = asyncio.create_task(self._consume(outbox))
        stopping = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait({consume, stopping}, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t is not stopping and t.exception():
                    raise t.exception()
            while self._busy:
                await asyncio.sleep(0.05)
        finally:
            for t in (consume, stopping, *self._tasks):
                t.cancel()
            await self.wire.close()

    async def _consume(self, outbox) -> None:
        async for item in outbox:
            self._busy = True
            try:
                if isinstance(item, self.kernel.Release):
                    await outbox.perform(item)
                elif isinstance(item, self.kernel.NoticeDue):
                    await self.sender.notice(item, outbox)
                elif isinstance(item, self.kernel.ReactionDue):
                    await self.sender.react(item, outbox)
            finally:
                self._busy = False

    async def tick(self) -> None:
        if await self.wire.connected():
            await self.fill()

    async def connect(self) -> None:
        """`auth.test` and the socket; after a failure each try waits one
        `serve_tick_s`. Each socket session's `hello` starts a gap fill."""
        while True:
            try:
                await self.wire.connect(self.handle, self._kick)
                log.info("connected to %s as %s", self.wire.team_id, self.wire.user_id)
                return
            except WireError as e:
                log.warning("connect failed (%s); next try in %s s", e, self.kernel.serve_tick_s)
            except Exception:
                log.exception("connect failed; next try in %s s", self.kernel.serve_tick_s)
            await asyncio.sleep(self.kernel.serve_tick_s)

    def _kick(self) -> None:
        self._spawn(self.fill())

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- receiving ---------------------------------------------------------

    async def handle(self, envelope_id: str, event: dict[str, Any]) -> None:
        """One `message` envelope, run as its own task; acked once its
        receive commits, or at once when it is not wanted."""
        channel = event.get("channel") or ""
        dm = event.get("channel_type") == "im"
        if not inbound.wanted(event, self.wire.user_id):
            await self.wire.ack(envelope_id)
            return
        chat = inbound.chat_of(self.wire.team_id, channel, event, dm=dm)
        if not self.kernel.owns("slack", chat):
            await self.wire.ack(envelope_id)
            return
        if await self._take(chat, channel, event, dm=dm):
            await self.wire.ack(envelope_id)

    async def _take(self, chat: str, channel: str, msg: dict[str, Any], *, dm: bool) -> bool:
        """Receive one message once in this process: while its `(chat,
        ts)` is being received, wait for that receive, and take it again
        only if it failed. True once it is recorded."""
        where = (chat, msg["ts"])
        while where in self._inflight:
            if await asyncio.shield(self._inflight[where]):
                return True
        done = asyncio.get_running_loop().create_future()
        self._inflight[where] = done
        ok = False
        try:
            await self._receive(chat, channel, msg, dm=dm)
            ok = True
            self._failed.pop(where, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("receive of %s in %s failed; Slack or the gap fill retakes it", msg["ts"], chat)
            self._failed[where] = inbound.root(msg) or msg["ts"]
        finally:
            del self._inflight[where]
            done.set_result(ok)
        return ok

    async def _receive(self, chat: str, channel: str, msg: dict[str, Any], *, dm: bool) -> None:
        fields = await inbound.build(self.wire, chat, channel, msg, Path(self.kernel.inbound_dir), dm=dm)
        fields["attachments"] = [
            await asyncio.to_thread(self.kernel.transcribe, a) for a in fields["attachments"]
        ]
        async with self.kernel.conn() as conn:
            await self.kernel.receive(conn, self.kernel.Inbound(**fields))

    # -- gap fill ----------------------------------------------------------

    async def fill(self) -> bool:
        """One gap-fill pass over the owned chats; true when it completed.
        A pass already running is not doubled."""
        if self._filling.locked():
            return False
        async with self._filling:
            try:
                for chat in self.kernel.owned("slack"):
                    if chat.partition("/")[0] != self.wire.team_id:
                        continue  # a chat in a workspace this bridge does not serve
                    try:
                        await self._fill_chat(chat)
                    except Refused as e:
                        # Valor was taken out of the channel, or it is gone:
                        # this chat's pass ends, and the next tick tries it again.
                        self._passes.pop(chat, None)
                        log.warning("gap fill skipped %s: %s", chat, e)
            except gap.OutOfBudget as e:
                log.info("gap fill paused: the %s budget is spent; the next tick resumes it", e)
                return False
            except WireError as e:
                log.warning("gap fill stopped: %s", e)
                return False
            except Exception:
                log.exception("gap fill failed; the next tick tries again")
                return False
            return True

    def _held(self, chat: str) -> list[str]:
        return [ts for c, ts in [*self._inflight, *self._failed] if c == chat]

    async def _fill_chat(self, chat: str) -> None:
        dm = inbound.is_dm(chat)
        p = self._passes.get(chat)
        if p is None:
            p = await self._begin(chat, dm)
            if p is None:
                return
            self._passes[chat] = p
        # The pass's place (cursor, top, history done, the thread list) moves
        # only once what it read is recorded: a failure leaves it where the
        # next tick reads the same page again.
        while not p.history_done:
            self.budget.take("history")
            page, cursor = await self.wire.history(
                p.channel, oldest=p.start, inclusive=p.inclusive, cursor=p.cursor
            )
            await self._receive_found(chat, p, dm, page)
            for m in sorted(page, key=lambda m: m["ts"]):
                if not p.after(m["ts"]):
                    continue
                p.top = max(p.top, m["ts"])
                if m.get("latest_reply") and m["latest_reply"] > p.start and m["ts"] not in p.threads:
                    p.threads.append(m["ts"])
            if cursor is None:
                since = (datetime.now(UTC) - timedelta(days=gap.THREAD_DAYS)).isoformat()
                async with self.kernel.conn() as conn:
                    listed = await self.kernel.threads(conn, "slack", chat, since)
                roots = {r for (c, _), r in self._failed.items() if c == chat}
                p.threads += sorted((listed | roots) - set(p.threads))
                p.history_done = True
            p.cursor = cursor
        while p.threads:
            self.budget.take("replies")
            try:
                page, cursor = await self.wire.replies(
                    p.channel, p.threads[0], oldest=p.start, inclusive=p.inclusive, cursor=p.thread_cursor
                )
            except Refused as e:
                # The root was deleted: nothing in its thread can be read.
                log.warning("gap fill dropped thread %s in %s: %s", p.threads.pop(0), chat, e)
                p.thread_cursor = None
                continue
            await self._receive_found(chat, p, dm, page)
            p.thread_cursor = cursor
            if cursor is None:
                p.threads.pop(0)
        del self._passes[chat]
        top, inclusive = p.top, False
        held = self._held(chat)
        if held and min(held) <= top:
            top, inclusive = min(held), True
        self._seen.set(chat, top, inclusive)

    async def _begin(self, chat: str, dm: bool) -> gap.Pass | None:
        """A new pass for `chat`, or None after a first pass: with no seen
        entry and no rows, the chat's seen `ts` is set to its newest."""
        channel = await self.wire.open_im(chat.partition("/")[2]) if dm else chat.partition("/")[2]
        start = self._seen.get(chat)
        if start is None:
            async with self.kernel.conn() as conn:
                lowest = await self.kernel.lowest_ts(conn, "slack", chat)
            if lowest is not None:
                start = (lowest, True)
        if start is None:
            self.budget.take("history")
            page, _ = await self.wire.history(channel, limit=1)
            top = page[0]["ts"] if page else f"{datetime.now(UTC).timestamp():.6f}"
            held = self._held(chat)
            if held and min(held) <= top:
                self._seen.set(chat, min(held), True)
            else:
                self._seen.set(chat, top, False)
            return None
        return gap.Pass(channel=channel, start=start[0], inclusive=start[1], top=start[0])

    async def _receive_found(self, chat: str, p: gap.Pass, dm: bool, page: list[dict]) -> None:
        """Receive the page's wanted messages that the pass reads and the
        ledger lacks, oldest first, each through `_take`."""
        msgs = [m for m in page if p.after(m["ts"]) and inbound.wanted(m, self.wire.user_id)]
        if not msgs:
            return
        async with self.kernel.conn() as conn:
            have = await self.kernel.recorded(conn, "slack", chat, [m["ts"] for m in msgs])
        for m in sorted(msgs, key=lambda m: m["ts"]):
            if m["ts"] in have:
                self._failed.pop((chat, m["ts"]), None)
                continue
            if (chat, m["ts"]) in self._inflight:
                continue
            await self._take(chat, p.channel, m, dm=dm)
