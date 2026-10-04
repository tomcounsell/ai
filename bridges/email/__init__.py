"""The email bridge: Valor's Gmail mailbox over IMAP and SMTP.

`run` watches INBOX for owned senders' mail and performs each `email.send`
the outbox releases. Governed by `docs/bridges/email.md`.
"""

import asyncio
import logging
import signal
from typing import Any

import psycopg

from core import bridge, broker, db, notices, tasks

from . import imap, smtp
from .config import Config
from .stop import Ends, in_thread

log = logging.getLogger("valor.email")


class Stopped(broker.Unknown):
    """Tom stopped the task while a server call ran: the call ended, no
    outcome is written, and the effect stays in flight."""


class EmailBridge:
    channel = "email"
    limits = bridge.LIMITS["email"]

    def __init__(self, cfg: Config | None = None, dsn: str | None = None):
        """`dsn` is the database the watch records into: the one `serve`
        is given, the kernel's when both are None."""
        self.cfg = cfg or Config.from_settings()
        self.dsn = dsn
        self._retry = asyncio.Event()
        self._wakes: set[asyncio.Future] = set()
        self._running: set[str] = set()
        self.stopped: set[str] = set()  # effects whose last call a stop ended
        self._tasks: asyncio.TaskGroup | None = None

    async def perform(self, action: broker.Action, key: str) -> dict[str, Any]:
        """The SMTP call, ended when Tom stops the effect's task. A stop
        that ends the send before its end of data line went leaves nothing
        sent: the thread's own `SendRefused` is raised, so the effect
        settles `failed`. A stop after that line leaves the effect in
        flight for Sent Mail to settle."""
        ends = Ends()
        try:
            return await self.until_stopped(
                key, lambda: ends.call(smtp.perform, self.cfg, action, key, ends), check=True
            )
        except Stopped:
            if isinstance(ends.error, broker.Failed):
                raise ends.error from None
            raise
        finally:
            ends.close()

    async def lookup(self, action: broker.Action, key: str, since: str) -> dict[str, Any] | None:
        """The Sent Mail read, ended by a stop that arrives while it runs."""
        return await self.until_stopped(
            key, lambda: in_thread(smtp.lookup, self.cfg, action, key, since), check=False
        )

    def performers(self):
        return {"email.send": (self.perform, self.lookup)}

    async def watches(self) -> None:
        """The IMAP watch (`imap.watch`) on its own database connection,
        replaced on the next wake when it drops."""

        def connect():
            return db.connect(self.dsn, application_name="valor-email-watch")

        await imap.watch(self.cfg, None, self._retry, connect)

    async def run(self, outbox) -> None:
        """The watch beside the outbox. Each `Release` is performed as its
        own task on its own connection, and so is each Sent Mail lookup
        (`reconcile`), so a server that never answers holds only its own
        effect, and a stop ends it. Notices are Telegram's
        (`operator_channel`), so email ignores them."""
        async with asyncio.TaskGroup() as tg:
            self._tasks = tg
            tg.create_task(self.watches())
            await self.reconcile(outbox)
            async for item in outbox:
                if isinstance(item, bridge.Release):
                    self.start(item.effect_id, self.release(outbox, item))

    def start(self, effect_id: str, work) -> None:
        """`work` as a task of its own, unless one already runs for this
        effect."""
        if effect_id in self._running:
            work.close()
            return
        self._running.add(effect_id)
        self._tasks.create_task(self.own(effect_id, work))

    async def own(self, effect_id: str, work) -> None:
        try:
            await work
        except Exception:
            log.exception("email effect %s failed; it is asked again on the next wake", effect_id)
        finally:
            self._running.discard(effect_id)

    async def release(self, outbox, item: bridge.Release) -> None:
        conn = await db.connect(self.dsn, application_name="valor-email-perform")
        self.stopped.discard(item.effect_id)
        try:
            outcome = await outbox.perform(item, conn)
            if outcome.kind == "unknown" and item.effect_id not in self.stopped:
                await self.in_doubt(conn, item.effect_id)
        finally:
            await conn.close()

    async def reconcile(self, outbox) -> None:
        """Each wake: every dangling send asked of Sent Mail as its own
        task, on its own connection."""
        for effect_id in await outbox.dangling():
            self.start(effect_id, self.settle(outbox, effect_id))

    async def settle(self, outbox, effect_id: str) -> None:
        conn = await db.connect(self.dsn, application_name="valor-email-settle")
        self.stopped.discard(effect_id)
        try:
            settled = await outbox.settle(effect_id, conn)
            if settled is None and effect_id not in self.stopped:
                await self.in_doubt(conn, effect_id)
        finally:
            await conn.close()

    async def until_stopped(self, key: str, call, *, check: bool) -> Any:
        """`call()` (a coroutine on its own thread, `stop.Ends`), ended when
        Tom stops the effect's task: the cancel reaches the call blocked on
        its server, which shuts the connection down, and `Stopped` is
        raised. A listener that drops is replaced on the next outbox wake, and
        the new one reads at once whether the task was stopped. `check` reads whether the task was already stopped once
        listening; a Sent Mail read does not, so a later wake's read runs
        for a task stopped earlier."""
        effect_id = key.rsplit(":", 1)[-1]
        listener = await db.connect(self.dsn, application_name="valor-email-stop")
        running = heard = None
        try:
            await listener.execute(f"LISTEN {tasks.STOP_CHANNEL}")
            row = await (
                await listener.execute(
                    "SELECT task_id FROM events WHERE type = 'effect.held' AND payload->>'effect_id' = %s",
                    (effect_id,),
                )
            ).fetchone()
            if row is None:
                return await call()
            running = asyncio.ensure_future(call())
            heard = asyncio.ensure_future(self.stop_heard(listener, row[0], check))
            while True:
                await asyncio.wait({running, heard}, return_when=asyncio.FIRST_COMPLETED)
                if running.done():
                    return running.result()
                if heard.exception() is None:
                    self.stopped.add(effect_id)
                    raise Stopped(f"task {row[0]} was stopped")
                # The listener failed; nobody stopped the task. The call runs
                # on. As the outbox does, wait for the next wake, then listen
                # on a new connection. `check` is set so a stop that landed in
                # the gap (the durable `task.stopped` row) is caught.
                await listener.close()
                listener = None
                while listener is None:
                    woke = asyncio.ensure_future(self.woken())
                    try:
                        await asyncio.wait({running, woke}, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        woke.cancel()
                    if running.done():
                        return running.result()
                    try:
                        listener = await db.connect(self.dsn, application_name="valor-email-stop")
                        await listener.execute(f"LISTEN {tasks.STOP_CHANNEL}")
                    except psycopg.OperationalError:
                        if listener is not None:
                            await listener.close()
                        listener = None
                heard = asyncio.ensure_future(self.stop_heard(listener, row[0], True))
        finally:
            pending = [t for t in (running, heard) if t is not None]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if listener is not None:
                await listener.close()

    async def woken(self) -> None:
        """Until the next outbox wake (`tick`)."""
        wake = asyncio.get_running_loop().create_future()
        self._wakes.add(wake)
        try:
            await wake
        finally:
            self._wakes.discard(wake)

    @staticmethod
    async def stop_heard(listener, task_id: str, check: bool) -> None:
        if check and await tasks.is_stopped(listener, task_id):
            return
        async for note in listener.notifies():
            if note.payload == task_id:
                return

    async def in_doubt(self, conn, effect_id: str) -> None:
        """Tom is told, once, the moment a send is first in doubt: no wait.
        The effect stays in flight and is asked again on each wake."""
        row = await (
            await conn.execute(
                "SELECT task_id, payload FROM events WHERE type = 'effect.held' AND payload->>'effect_id' = %s",
                (effect_id,),
            )
        ).fetchone()
        if row is None or await effect_settled(conn, effect_id):
            return
        task_id, held = row
        await notices.request(
            conn,
            task_id,
            kind="send_in_doubt",
            about_key=f"send-in-doubt:{effect_id}",
            text=f"The email to {held['target']} (effect {effect_id}) may or may not have gone: it was "
            "cut off or not confirmed after the server could have taken it, and Sent Mail does not "
            "show it yet. It stays in flight and Sent Mail is asked again on each wake.",
        )

    async def tick(self) -> None:
        """Each outbox wake: a watch that failed reconnects."""
        self._retry.set()
        for wake in self._wakes:
            if not wake.done():
                wake.set_result(None)


async def effect_settled(conn, effect_id: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE type = 'effect.outcome' AND payload->>'effect_id' = %s", (effect_id,)
        )
    ).fetchone()
    return row is not None


async def serve(email: EmailBridge, dsn: str | None = None) -> None:
    """`bridge.serve`, with launchd's SIGTERM ending it as a stop does: the
    cancel runs `stop.Ends` on every blocked call, so the process exits
    with no thread left holding it."""
    main = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, main.cancel)
    await bridge.serve(email, dsn)
