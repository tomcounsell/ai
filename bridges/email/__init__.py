"""The email bridge: Valor's Gmail mailbox over IMAP and SMTP.

`run` polls INBOX for owned senders' mail and performs each `email.send`
the outbox releases. Governed by `docs/bridges/email.md`.
"""

import asyncio
import logging
from typing import Any

from core import bridge, broker, db

from . import imap, smtp
from .config import Config

log = logging.getLogger("valor.email")

# The worker thread a send or a lookup runs in. A send's thread is the one
# that must hold the effect's performing lock while it lives, so a stop that
# abandons it leaves reconcile waiting for the thread, not the coroutine;
# with the performing lock in the kernel this is that lock's thread runner,
# which runs `fn` exactly as `asyncio.to_thread` does when no lock is held.
in_thread = asyncio.to_thread


class EmailBridge:
    channel = "email"
    limits = bridge.LIMITS["email"]

    def __init__(self, cfg: Config | None = None, dsn: str | None = None):
        """`dsn` is the database the poll records into: the one `serve`
        is given, the kernel's when both are None."""
        self.cfg = cfg or Config.from_settings()
        self.dsn = dsn

    async def perform(self, action: broker.Action, key: str) -> dict[str, Any]:
        return await in_thread(smtp.perform, self.cfg, action, key)

    async def lookup(self, action: broker.Action, key: str, since: str) -> dict[str, Any] | None:
        return await in_thread(smtp.lookup, self.cfg, action, key, since)

    def performers(self):
        return {"email.send": (self.perform, self.lookup)}

    async def polls(self) -> None:
        """A poll every `poll_s` seconds on its own connection. A failed
        poll is one line in the log; the next runs on schedule."""
        conn = await db.connect(self.dsn, application_name="valor-email-poll")
        try:
            while True:
                try:
                    await imap.poll(self.cfg, conn)
                except Exception:
                    log.exception("email poll failed")
                await asyncio.sleep(self.cfg.poll_s)
        finally:
            await conn.close()

    async def run(self, outbox) -> None:
        """The poll beside the outbox: each `Release` performed; notices
        are Telegram's (`operator_channel`), so email ignores them."""
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.polls())
            async for item in outbox:
                if isinstance(item, bridge.Release):
                    await outbox.perform(item)

    async def tick(self) -> None:
        return None
