"""The turn slots: at most `settings.max_turns` harness turns or checks at
once on this machine.

`held(holder)` takes one of the session locks `turn-slot:<machine>:<i>`,
`i` below `max_turns`, on a connection of its own, and waits until one is
free. The kernel (`python -m core serve`) and `python -m core run` take the
same locks, so the limit is the machine's.

It is reentrant within a process: a context variable records that the
current task of this process already holds a slot, and an inner `held`
does nothing. A check that runs a turn inside it takes one slot.

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

# What a step returns when its background turn or check was preempted.
# Nothing is preempted since the slot became several; the name stays for
# the callers that still test for it.
PREEMPTED = {"status": "moved", "preempted": True}

_HELD: ContextVar[str | None] = ContextVar("turn_slot", default=None)

_TRY = "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))"
_UNLOCK = "SELECT pg_advisory_unlock(hashtextextended(%s, 0))"

# How long a holder waits between tries when every slot is taken.
POLL_S = 0.2


def key(i: int) -> str:
    return f"turn-slot:{settings.machine}:{i}"


@asynccontextmanager
async def held(holder: str, dsn: str | None = None) -> AsyncIterator[None]:
    """Hold a turn slot for `holder` (a task id), waiting for one."""
    if _HELD.get() is not None:
        yield
        return
    conn = await db.connect(dsn, application_name="valor-slot")
    taken = None
    awake = None
    try:
        while taken is None:
            for i in range(max(1, settings.max_turns)):
                got = await (await conn.execute(_TRY, (key(i),))).fetchone()
                if got[0]:
                    taken = key(i)
                    break
            else:
                await asyncio.sleep(POLL_S)
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
        try:
            if taken is not None and not conn.closed:
                await conn.execute(_UNLOCK, (taken,))
        finally:
            await conn.close()


def holder() -> str | None:
    """Who holds the slot in this context, if anyone."""
    return _HELD.get()


def preempting() -> asyncio.Event | None:
    """The event a preemptible holder's body races its work against. No
    holder is preempted, so it is always None."""
    return None


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
