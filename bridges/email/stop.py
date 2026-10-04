"""Ending a blocked connection from the stopping side.

Nothing in the email bridge sets a timer on a read: a command waits until
the server answers or a stop ends it. A thread blocked in a read cannot be
cancelled, so the connection is what ends: `Ends` holds a duplicate of each
socket a call opens, and `end` shuts them down, which returns every read
blocked on them. A connection is closed only after its thread has
returned: closing it under a thread still waiting leaves that thread
waiting. A duplicate follows the connection through STARTTLS and
the TLS wrap, which move the descriptor to a new socket object.
"""

import asyncio
import os
import socket
import threading
from collections.abc import Callable
from typing import Any


class Ends:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dups: list[socket.socket] = []
        self._ended = False
        self.error: BaseException | None = None  # what the call's thread raised, once it returned

    def register(self, sock: socket.socket) -> None:
        """Called by the thread that opens `sock`; a connection opened after
        `end` is shut down at once."""
        dup = socket.socket(fileno=os.dup(sock.fileno()))
        with self._lock:
            self._dups.append(dup)
            ended = self._ended
        if ended:
            self._shutdown(dup)
            dup.close()

    def end(self) -> None:
        with self._lock:
            self._ended = True
            dups = list(self._dups)
        for d in dups:
            self._shutdown(d)

    def close(self) -> None:
        """Releases the duplicates; the connections are the caller's own."""
        with self._lock:
            dups, self._dups = self._dups, []
        for d in dups:
            d.close()

    @staticmethod
    def _shutdown(dup: socket.socket) -> None:
        try:
            dup.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    async def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        """`fn(*args)` on a thread of its own: no pool, so no call waits
        behind another, and a cancelled call always has a started thread to
        end. A cancel (a stop, a shutdown) ends the connections and waits for
        the thread to return, so the caller may close what the thread was
        using, and no thread outlives the stop."""
        loop = asyncio.get_running_loop()
        result: asyncio.Future[Any] = loop.create_future()
        gone: asyncio.Future[None] = loop.create_future()

        def settle(value: Any, error: BaseException | None) -> None:
            if not result.done():
                result.set_exception(error) if error else result.set_result(value)
            gone.set_result(None)

        def work() -> None:
            try:
                value, error = fn(*args), None
            except BaseException as e:  # noqa: BLE001  handed to the awaiting task
                value, error = None, e
            self.error = error
            loop.call_soon_threadsafe(settle, value, error)

        threading.Thread(target=work, name="email-call").start()
        try:
            return await result
        except asyncio.CancelledError:
            self.end()
            await gone
            raise


async def in_thread(fn: Callable[..., Any], *args: Any) -> Any:
    """`fn(*args, ends)` in a worker thread, with its own `Ends`."""
    ends = Ends()
    try:
        return await ends.call(fn, *args, ends)
    finally:
        ends.close()
