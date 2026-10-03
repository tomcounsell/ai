"""Whether any process may still be performing an effect, read as a fact.

`broker._performing` opens `<effect_id>.lock` in `settings.performing_dir`
with a shared lock (`flock`) before the effect's intent is written and
holds it until the perform's coroutine ends. A performer that runs its
work in a worker thread does so through `in_thread`, which hands the
thread a duplicate of that descriptor, taken while the coroutine still
holds it, and the thread closes it when its work returns. Every kernel git
call made in that thread passes the duplicate to git (`held`), so git and
every process git starts hold the lock until the last of them exits. A
`flock` belongs to the open file and is released when the last descriptor
to it is closed, however its holder ends (flock(2)), so a perform
cancelled while its thread runs, or a kernel that died leaving git
running, still holds it; no PID or age is read.

`settled(effect_id)` takes the exclusive lock without waiting: it gets it
only when no process holds the file, and then removes the file. A missing
file is settled too: it is created before the intent, so it is missing
only when its perform ended and removed it, or for an intent written
before these files existed. A process that opens the file checks, once
locked, that the path still names what it opened (a remover may have
unlinked it in between), and opens again when it does not.

The directory (`settings.performing_dir`) is by default in the kernel key
directory, which every turn's sandbox profile denies, so no turn can hold,
remove, or plant a lock.

Imports the standard library and `core.settings`.
"""

import asyncio
import contextlib
import contextvars
import errno
import fcntl
import os
import threading
from pathlib import Path

from core.settings import settings

# The perform's own descriptor, and a worker thread's duplicate of it (the
# one git is given: it stays open for as long as the thread runs).
_HELD: contextvars.ContextVar[int | None] = contextvars.ContextVar("performing_fd", default=None)
_THREAD: contextvars.ContextVar[int | None] = contextvars.ContextVar("performing_thread_fd", default=None)


def path(effect_id: str) -> Path:
    return Path(settings.performing_dir) / f"{effect_id}.lock"


def hold(p: str | Path) -> int:
    """An open descriptor on `p` holding a shared lock, `p` made if
    missing; waits while a remover holds it exclusively."""
    while True:
        fd = os.open(p, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            st, now = os.fstat(fd), os.stat(p, follow_symlinks=False)
            if (st.st_dev, st.st_ino) == (now.st_dev, now.st_ino):
                return fd
        except FileNotFoundError:
            pass
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)


def _take(p: Path) -> bool:
    """Remove `p` if no process holds it; whether it is gone."""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return True
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                return False
            raise
        with contextlib.suppress(FileNotFoundError):
            os.unlink(p)
        return True
    finally:
        os.close(fd)


def settled(effect_id: str) -> bool:
    """Whether no process can still be performing the effect."""
    return _take(path(effect_id))


@contextlib.contextmanager
def performing(effect_id: str):
    """Hold the effect's lock for the block. On exit the file is removed
    when nothing else holds it; a thread or git still running keeps it,
    for `settled` to read."""
    p = path(effect_id)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = hold(p)
    token = _HELD.set(fd)
    try:
        yield
    finally:
        _HELD.reset(token)
        os.close(fd)
        _take(p)


def held() -> int | None:
    """The descriptor this worker thread holds on its effect's lock
    (`in_thread`), if any, for a git call to pass on."""
    return _THREAD.get()


async def in_thread(fn, *args):
    """`fn(*args)` in a worker thread that holds the effect's lock for as
    long as it runs. The thread owns its duplicate descriptor and closes it
    in its own `finally`; when the caller is cancelled before the thread
    starts, the caller closes it and the thread never runs `fn`."""
    outer = _HELD.get()
    if outer is None:
        return await asyncio.to_thread(fn, *args)
    fd = os.dup(outer)
    gate = threading.Lock()
    state = {"started": False, "abandoned": False}

    def run():
        with gate:
            if state["abandoned"]:
                return None
            state["started"] = True
        token = _THREAD.set(fd)
        try:
            return fn(*args)
        finally:
            _THREAD.reset(token)
            os.close(fd)

    try:
        return await asyncio.to_thread(run)
    except BaseException:
        with gate:
            if not state["started"]:
                state["abandoned"] = True
                os.close(fd)
        raise
