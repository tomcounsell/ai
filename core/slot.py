"""The turn slot: one harness turn or check at a time on this machine.

`held(holder)` takes the session lock `turn-slot:<settings.machine>` on a
connection of its own, blocking; Postgres grants waiters in the order they
queued. The kernel (`python -m core serve`) and `python -m core run` take
the same lock, so a turn the command line runs waits for the kernel's and
the other way round.

It is reentrant within a process: a context variable records that the
current task of this process already holds the slot, and an inner `held`
does nothing. A check that runs a turn inside it takes the slot once.

While held, `caffeinate -i -w <pid>` keeps the machine from idle sleep for
as long as this process lives. It does not prevent sleep on closing the lid
(docs/machine.md).
"""

import asyncio
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from core import db, tasks
from core.settings import settings

PREEMPT_CHANNEL = "valor_preempt"

# What a step returns when its background turn or check was preempted: it
# moved nothing, and runs again.
PREEMPTED = {"status": "moved", "preempted": True}


class Hold:
    """What a holder knows about its hold. `preempted` is set on a
    background holder when a foreground task is waiting for the slot."""

    def __init__(self, holder: str, background: bool) -> None:
        self.holder = holder
        self.background = background
        self.preempted = asyncio.Event()


_HELD: ContextVar[Hold | None] = ContextVar("turn_slot", default=None)


def key() -> str:
    return f"turn-slot:{settings.machine}"


def foreground_key() -> str:
    return f"turn-slot-fg:{settings.machine}"


_LOCK = "SELECT pg_advisory_lock(hashtextextended(%s, 0))"
_UNLOCK = "SELECT pg_advisory_unlock(hashtextextended(%s, 0))"


@asynccontextmanager
async def held(holder: str, dsn: str | None = None) -> AsyncIterator[None]:
    """Hold the turn slot for `holder` (a task id), waiting for it.

    A holder for a foreground task takes the shared lock `turn-slot-fg`
    and sends `valor_preempt` before it waits, so a background holder
    learns that a foreground task is waiting. A holder for a background
    task (a routine's, or a replay: `tasks.background`) listens once it
    holds the slot and sets `preempting()` when it hears the notice, or
    finds the shared lock taken; the body it guards ends its work and
    returns."""
    if _HELD.get() is not None:
        yield
        return
    conn = await db.connect(dsn, application_name="valor-slot")
    awake = None
    locked = False
    shared = False
    listener = None
    watcher = None
    try:
        background = await tasks.background(conn, holder)
        if not background:
            await conn.execute("SELECT pg_advisory_lock_shared(hashtextextended(%s, 0))", (foreground_key(),))
            shared = True
            await conn.execute("SELECT pg_notify(%s, '')", (PREEMPT_CHANNEL,))
        await conn.execute(_LOCK, (key(),))
        locked = True
        hold = Hold(holder, background)
        if background:
            listener = await db.connect(dsn, application_name="valor-slot")
            await listener.execute(f"LISTEN {PREEMPT_CHANNEL}")
            watcher = asyncio.create_task(_watch(listener, hold))
        token = _HELD.set(hold)
        awake = _caffeinate()
        try:
            yield
        finally:
            _HELD.reset(token)
    finally:
        if watcher is not None:
            watcher.cancel()
        if listener is not None:
            await listener.close()
        if awake is not None:
            awake.terminate()
            await asyncio.to_thread(awake.wait)
        # Unlocked here, not left to the closing session, so the next
        # waiter is granted the slot before this returns. The foreground
        # notice goes first: a background holder that takes the slot next
        # must not find it.
        try:
            if not conn.closed:
                if shared:
                    await conn.execute(
                        "SELECT pg_advisory_unlock_shared(hashtextextended(%s, 0))", (foreground_key(),)
                    )
                if locked:
                    await conn.execute(_UNLOCK, (key(),))
        finally:
            await conn.close()


async def _watch(listener, hold: Hold) -> None:
    """Set `hold.preempted` when a foreground task is waiting: a notice on
    `valor_preempt`, or the shared lock `turn-slot-fg` held by another
    session (a notice sent before this one listened)."""
    got = await listener.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (foreground_key(),))
    if not (await got.fetchone())[0]:
        hold.preempted.set()
        return
    await listener.execute(_UNLOCK, (foreground_key(),))
    async for _ in listener.notifies():
        hold.preempted.set()
        return


def holder() -> str | None:
    """Who holds the slot in this context, if anyone."""
    hold = _HELD.get()
    return hold.holder if hold else None


def preempting() -> asyncio.Event | None:
    """The event a background holder's body races its work against, or None
    for a body that is never preempted."""
    hold = _HELD.get()
    return hold.preempted if hold and hold.background else None


def _caffeinate() -> subprocess.Popen | None:
    found = shutil.which("caffeinate", path="/usr/bin")
    if found is None:
        return None
    return subprocess.Popen(
        [found, "-i", "-w", str(os.getpid())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
