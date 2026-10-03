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

from core import db
from core.settings import settings

_HELD: ContextVar[str | None] = ContextVar("turn_slot", default=None)


def key() -> str:
    return f"turn-slot:{settings.machine}"


@asynccontextmanager
async def held(holder: str, dsn: str | None = None) -> AsyncIterator[None]:
    """Hold the turn slot for `holder` (a task id), waiting for it."""
    if _HELD.get() is not None:
        yield
        return
    conn = await db.connect(dsn, application_name="valor-slot")
    awake = None
    try:
        await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key(),))
        token = _HELD.set(holder)
        awake = _caffeinate()
        try:
            yield
        finally:
            _HELD.reset(token)
    finally:
        if awake is not None:
            awake.terminate()
            await asyncio.to_thread(awake.wait)
        # Closing the session frees the lock.
        await conn.close()


def holder() -> str | None:
    """Who holds the slot in this context, if anyone."""
    return _HELD.get()


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
