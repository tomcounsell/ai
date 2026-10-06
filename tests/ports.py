"""Ports the tests' own services listen on.

`VALOR_TEST_PORTS` (`LOW-HIGH`) moves every server the tests start into one
span: test runs side by side on one machine never share a port, and in a
task's workspace the span is the dev ports, the only ones the check and
turn profiles let a process listen on. Unset, `span` gives each call site
its own default span and `listen` gives 0, so the OS chooses.

A port `service` gives a task's service is reserved, in the file
`VALOR_TEST_RESERVED_PORTS` names (set for the session in
`tests/conftest.py`, so every process the tests start reads it), until the
test ends: the service starts later, often in another process whose
`listen` starts at the span's low end, and no server takes its port first.
"""

import os
import socket
from pathlib import Path

_last: int | None = None


def span(default: tuple[int, int]) -> tuple[int, int]:
    value = os.environ.get("VALOR_TEST_PORTS")
    if not value:
        return default
    low, high = value.split("-")
    return int(low), int(high)


def _bindable(port: int) -> bool:
    """A bind the way the servers bind (`SO_REUSEADDR`), so a port in
    TIME_WAIT is free and one a live listener holds is not."""
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _reserved_file() -> Path | None:
    value = os.environ.get("VALOR_TEST_RESERVED_PORTS")
    return Path(value) if value else None


def reserved() -> set[int]:
    """The ports reserved for task services until the test ends."""
    f = _reserved_file()
    try:
        return {int(line) for line in f.read_text().split()} if f else set()
    except FileNotFoundError:
        return set()


def release() -> None:
    """The test ended and its tasks' services stopped: nothing is reserved."""
    f = _reserved_file()
    if f:
        f.unlink(missing_ok=True)


def listen(taken=()) -> int:
    """The port a test server binds: 0 with `VALOR_TEST_PORTS` unset; else
    the first port after the one this returned last, wrapping round the
    span, that is not in `taken`, not reserved, and is bindable. The moving start keeps
    back-to-back calls distinct while the servers they name start, and
    leaves a port just released for last. Raises naming the span when no
    port is left."""
    global _last
    if not os.environ.get("VALOR_TEST_PORTS"):
        return 0
    low, high = span((0, 0))
    taken = set(taken) | reserved()
    size = high - low + 1
    start = 0 if _last is None or not low <= _last <= high else _last - low + 1
    for i in range(size):
        port = low + (start + i) % size
        if port not in taken and _bindable(port):
            _last = port
            return port
    raise RuntimeError(f"VALOR_TEST_PORTS {low}-{high} has no free port left")


def service(default: tuple[int, int], taken=()) -> int:
    """A port for a task service a test provisions, reserved until the test
    ends: `listen(taken)` when `VALOR_TEST_PORTS` is set, else the kernel's
    own choice over the call site's default span."""
    from core import workspace

    port = listen(taken) or workspace.choose_port(default, set(taken) | reserved())
    f = _reserved_file()
    if f:
        with f.open("a") as out:
            out.write(f"{port}\n")
    return port
