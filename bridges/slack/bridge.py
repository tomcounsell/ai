"""`SlackBridge`: the `Bridge` the port's `serve` runs.

One bridge serves every workspace in `slack-keys`, each over its own
`SlackWire` (its own app, socket, clients and gap-fill budget); every
chat id starts with its team, which picks the wire. `run(outbox)`
connects each (`auth.test`, then the socket), and iterates the outbox
once one has connected: a `Release` goes to `outbox.perform`, and a
`ReactionDue` is placed here. A workspace whose connect fails keeps
trying, one `serve_tick_s` apart, while the others serve.
`tick()`, which `serve` calls on every `serve_tick_s` wake, fills the
gap of each connected workspace; so does each new socket session of a
workspace (Slack's `hello`), for that workspace. `slack_sdk`'s session
monitor keeps each socket up.

Receiving: every envelope that is not a message, or is a message that is
not a person's in an owned chat, is acked on arrival, so Slack never
turns event delivery off. A wanted message runs as its own task and is
acked once its `intake.receive` commits. An envelope redelivered while
the first delivery of its `(chat, ts)` is still being received waits for
it: it is acked once that commits, and received itself if that fails. A
receive that fails is not acked, so Slack retries it; its `ts` holds the
chat's seen `ts` and its thread root joins the next pass's thread list,
so gap fill takes it once Slack's retries run out.

Gap fill, per owned chat in a connected workspace: history from the
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

    def __init__(self, wires: SlackWire | list[SlackWire], kernel, *, seen: Path | None = None):
        """`wires`, one per workspace, each knowing its `team_id`."""
        wires = wires if isinstance(wires, list) else [wires]
        self.wires = {w.team_id: w for w in wires}
        self.kernel = kernel
        self.senders = {t: Sender(w, kernel) for t, w in self.wires.items()}
        self.budgets = {t: gap.Budget() for t in self.wires}
        self._filling = {t: asyncio.Lock() for t in self.wires}
        self._seen = Seen(seen)
        self._passes: dict[str, gap.Pass] = {}
        self._busy = False
        # (chat, ts) -> the first receive's result: true once it commits
        self._inflight: dict[tuple[str, str], asyncio.Future] = {}
        # (chat, ts) -> thread root (or its own ts) of a receive that failed
        self._failed: dict[tuple[str, str], str] = {}
        self._tasks: set[asyncio.Task] = set()

    def wire(self, chat: str) -> SlackWire | None:
        """The wire of `chat`'s workspace (`<team>/<id>`), or None."""
        return self.wires.get(chat.partition("/")[0].upper())

    def _sender(self, action) -> Sender:
        team = action.target.partition("/")[0].upper()
        if team not in self.senders:
            raise self.kernel.Failed(f"{team} is not a connected workspace")
        return self.senders[team]

    def performers(self):
        async def send(action, key):
            return await self._sender(action).perform(action, key)

        async def canvas(action, key):
            return await self._sender(action).write_canvas(action, key)

        async def lookup(action, key, since):
            try:
                sender = self._sender(action)
            except self.kernel.Failed:
                return None  # never posted: the target was refused at perform
            return await sender.lookup(action, key, since)

        async def canvas_lookup(action, key, since):
            try:
                sender = self._sender(action)
            except self.kernel.Failed:
                return None
            return await sender.canvas_lookup(action, key, since)

        return {
            "slack.send_message": (send, lookup),
            "slack.write_canvas": (canvas, canvas_lookup),
        }

    async def read(self, chat: str, q: str | None, limit: int, before: str | None) -> list[dict]:
        """A chat's messages for a turn (`core.bridge.reads`), newest first.
        A search pages by number on Slack, so `before` applies only without
        one."""
        ident = chat.partition("/")[2]
        if not ident:
            raise ValueError("a Slack chat is <team>/<channel>, or <team>/<user> for a DM")
        wire = self.wire(chat)
        if wire is None:
            raise ValueError(f"{chat.partition('/')[0]} is not a connected workspace")
        dm = inbound.is_dm(chat)
        channel = await wire.open_im(ident) if dm else ident
        if q is None:
            found, _ = await wire.history(channel, latest=before, limit=limit)
        elif before is not None:
            raise ValueError("before does not apply to a Slack search")
        else:
            where = f"<@{ident}>" if dm else f"<#{channel}>"
            matches = await wire.search(f"{q} in:{where}", count=limit)
            found = [m for m in matches if (m.get("channel") or {}).get("id") == channel]
        return [await self._shown(wire, m) for m in found]

    async def _shown(self, wire: SlackWire, m: dict[str, Any]) -> dict:
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
            "sender_name": (await wire.user_name(user) if user else "") or m.get("username", ""),
            "out": bool(user) and user == wire.user_id,
            "reply_to": thread if thread != m["ts"] else None,
            "text": m.get("text") or "",
            "media": {"kind": "document", "name": files[0].get("name", "")} if files else None,
        }

    # -- the loop ----------------------------------------------------------

    async def run(self, outbox) -> None:
        up = asyncio.Event()
        for w in self.wires.values():
            self._spawn(self.connect(w, up))
        await up.wait()
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
            for w in self.wires.values():
                await w.close()

    async def _consume(self, outbox) -> None:
        async for item in outbox:
            self._busy = True
            try:
                if isinstance(item, self.kernel.Release):
                    await outbox.perform(item)
                elif isinstance(item, self.kernel.ReactionDue):
                    await self._react(item, outbox)
            finally:
                self._busy = False

    async def _react(self, item, outbox) -> None:
        team = item.chat_id.partition("/")[0].upper()
        if team not in self.senders:
            await outbox.reacted(item, f"{team} is not a connected workspace")
            return
        await self.senders[team].react(item, outbox)

    async def connected(self) -> dict[str, bool]:
        """Whether each workspace's socket is up, by team (`Outbox.report`)."""
        return {t: await w.connected() for t, w in self.wires.items()}

    async def tick(self) -> None:
        for t, w in self.wires.items():
            if await w.connected():
                await self.fill(t)

    async def connect(self, wire: SlackWire, up: asyncio.Event) -> None:
        """`auth.test` and the socket of one workspace, setting `up` once
        it connects; after a failure each try waits one `serve_tick_s`.
        Each socket session's `hello` starts a gap fill of its workspace."""
        team = wire.team_id

        async def on_message(envelope_id: str, event: dict[str, Any]) -> None:
            await self.handle(envelope_id, event, wire)

        while True:
            try:
                await wire.connect(on_message, lambda: self._spawn(self.fill(team)))
                log.info("connected to %s as %s", wire.team_id, wire.user_id)
                up.set()
                return
            except WireError as e:
                log.warning("connect to %s failed (%s); next try in %s s", team, e, self.kernel.serve_tick_s)
            except Exception:
                log.exception("connect to %s failed; next try in %s s", team, self.kernel.serve_tick_s)
            await asyncio.sleep(self.kernel.serve_tick_s)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- receiving ---------------------------------------------------------

    async def handle(self, envelope_id: str, event: dict[str, Any], wire: SlackWire) -> None:
        """One `message` or `reaction_added` envelope from `wire`'s
        workspace, run as its own task; acked once its receive commits, or
        at once when it is not wanted."""
        if event.get("type") == "reaction_added":
            await self._reaction(envelope_id, event, wire)
            return
        channel = event.get("channel") or ""
        dm = event.get("channel_type") == "im"
        if not inbound.wanted(event, wire.user_id):
            await wire.ack(envelope_id)
            return
        chat = inbound.chat_of(wire.team_id, channel, event, dm=dm)
        if not self.kernel.owns("slack", chat):
            await wire.ack(envelope_id)
            return
        if await self._take(chat, channel, event, dm=dm, wire=wire):
            await wire.ack(envelope_id)

    async def _reaction(self, envelope_id: str, event: dict[str, Any], wire: SlackWire) -> None:
        """Tom's reaction on Valor's post in an owned chat, recorded once (a
        redelivery is the same record) and acked; any other is acked at
        once. A receive that fails is not acked, so Slack retries it; gap
        fill reads no reactions."""
        fields = await inbound.reaction(wire, event, self.kernel.operators)
        if fields is None or not self.kernel.owns("slack", fields["chat_id"]):
            await wire.ack(envelope_id)
            return
        try:
            async with self.kernel.conn() as conn:
                await self.kernel.receive(conn, self.kernel.Inbound(**fields))
        except Exception:
            log.exception("receive of the reaction %s failed; Slack retries it", fields["message_id"])
            return
        log.info("reaction %s by %s in %s recorded", fields["text"], fields["sender_id"], fields["chat_id"])
        await wire.ack(envelope_id)

    async def _take(self, chat: str, channel: str, msg: dict[str, Any], *, dm: bool, wire: SlackWire) -> bool:
        """Receive one message of `wire`'s workspace once in this process:
        while its `(chat, ts)` is being received, wait for that receive,
        and take it again only if it failed. True once it is recorded."""
        where = (chat, msg["ts"])
        while where in self._inflight:
            if await asyncio.shield(self._inflight[where]):
                return True
        done = asyncio.get_running_loop().create_future()
        self._inflight[where] = done
        ok = False
        try:
            await self._receive(chat, channel, msg, dm=dm, wire=wire)
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

    async def _receive(
        self, chat: str, channel: str, msg: dict[str, Any], *, dm: bool, wire: SlackWire
    ) -> None:
        fields = await inbound.build(wire, chat, channel, msg, Path(self.kernel.inbound_dir), dm=dm)
        fields["attachments"] = [
            await asyncio.to_thread(self.kernel.transcribe, a) for a in fields["attachments"]
        ]
        async with self.kernel.conn() as conn:
            await self.kernel.receive(conn, self.kernel.Inbound(**fields))

    # -- gap fill ----------------------------------------------------------

    async def fill(self, team: str) -> bool:
        """One gap-fill pass over the owned chats of workspace `team`; true
        when it completed. A pass of that workspace already running is not
        doubled."""
        if self._filling[team].locked():
            return False
        async with self._filling[team]:
            try:
                for chat in self.kernel.owned("slack"):
                    if chat.partition("/")[0].upper() != team:
                        continue  # another workspace's, or one this bridge does not serve
                    try:
                        await self._fill_chat(chat)
                    except Refused as e:
                        # Valor was taken out of the channel, or it is gone:
                        # this chat's pass ends, and the next tick tries it again.
                        self._passes.pop(chat, None)
                        log.warning("gap fill skipped %s: %s", chat, e)
            except gap.OutOfBudget as e:
                log.info("gap fill of %s paused: the %s budget is spent; the next tick resumes it", team, e)
                return False
            except WireError as e:
                log.warning("gap fill of %s stopped: %s", team, e)
                return False
            except Exception:
                log.exception("gap fill of %s failed; the next tick tries again", team)
                return False
            return True

    def _held(self, chat: str) -> list[str]:
        return [ts for c, ts in [*self._inflight, *self._failed] if c == chat]

    async def _fill_chat(self, chat: str) -> None:
        dm = inbound.is_dm(chat)
        wire = self.wire(chat)
        budget = self.budgets[wire.team_id]
        p = self._passes.get(chat)
        if p is None:
            p = await self._begin(chat, dm, wire, budget)
            if p is None:
                return
            self._passes[chat] = p
        # The pass's place (cursor, top, history done, the thread list) moves
        # only once what it read is recorded: a failure leaves it where the
        # next tick reads the same page again.
        while not p.history_done:
            budget.take("history")
            page, cursor = await wire.history(
                p.channel, oldest=p.start, inclusive=p.inclusive, cursor=p.cursor
            )
            await self._receive_found(chat, p, dm, page, wire)
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
            budget.take("replies")
            try:
                page, cursor = await wire.replies(
                    p.channel, p.threads[0], oldest=p.start, inclusive=p.inclusive, cursor=p.thread_cursor
                )
            except Refused as e:
                # The root was deleted: nothing in its thread can be read.
                log.warning("gap fill dropped thread %s in %s: %s", p.threads.pop(0), chat, e)
                p.thread_cursor = None
                continue
            await self._receive_found(chat, p, dm, page, wire)
            p.thread_cursor = cursor
            if cursor is None:
                p.threads.pop(0)
        del self._passes[chat]
        top, inclusive = p.top, False
        held = self._held(chat)
        if held and min(held) <= top:
            top, inclusive = min(held), True
        self._seen.set(chat, top, inclusive)

    async def _begin(self, chat: str, dm: bool, wire: SlackWire, budget: gap.Budget) -> gap.Pass | None:
        """A new pass for `chat`, or None after a first pass: with no seen
        entry and no rows, the chat's seen `ts` is set to its newest."""
        channel = await wire.open_im(chat.partition("/")[2]) if dm else chat.partition("/")[2]
        start = self._seen.get(chat)
        if start is None:
            async with self.kernel.conn() as conn:
                lowest = await self.kernel.lowest_ts(conn, "slack", chat)
            if lowest is not None:
                start = (lowest, True)
        if start is None:
            budget.take("history")
            page, _ = await wire.history(channel, limit=1)
            top = page[0]["ts"] if page else f"{datetime.now(UTC).timestamp():.6f}"
            held = self._held(chat)
            if held and min(held) <= top:
                self._seen.set(chat, min(held), True)
            else:
                self._seen.set(chat, top, False)
            return None
        return gap.Pass(channel=channel, start=start[0], inclusive=start[1], top=start[0])

    async def _receive_found(
        self, chat: str, p: gap.Pass, dm: bool, page: list[dict], wire: SlackWire
    ) -> None:
        """Receive the page's wanted messages that the pass reads and the
        ledger lacks, oldest first, each through `_take`."""
        msgs = [m for m in page if p.after(m["ts"]) and inbound.wanted(m, wire.user_id)]
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
            await self._take(chat, p.channel, m, dm=dm, wire=wire)
