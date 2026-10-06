"""`tests/ports.py`: the port a test server binds."""

import socket
import subprocess
import sys

import pytest

from core import binaries
from tests import ports

# A span of the test run's own block, so this test never takes a port the
# rest of the session uses.
LOW, HIGH = ports.span((6620, 6629))


def _span_profile(low: int, high: int) -> str:
    """A profile that binds and connects on loopback only within the span,
    the way the check profile allows only the dev ports."""
    return "\n".join(
        [
            "(version 1)",
            "(allow default)",
            "(deny network-bind network-inbound)",
            "(allow network-bind network-inbound",
            *(f'    (local ip "localhost:{p}")' for p in range(low, high + 1)),
            ")",
            '(deny network-outbound (remote ip "localhost:*"))',
            "(allow network-outbound",
            *(f'    (remote ip "localhost:{p}")' for p in range(low, high + 1)),
            ")",
        ]
    )


def test_listen_draws_from_the_span(monkeypatch, tmp_path):
    monkeypatch.delenv("VALOR_TEST_PORTS", raising=False)
    assert ports.listen() == 0

    # Four free ports in a row: in a workspace the span is the dev ports,
    # and the shared judgement upstream holds one of them.
    low = next(p for p in range(LOW, HIGH - 2) if all(ports._bindable(q) for q in range(p, p + 4)))
    high = low + 3
    monkeypatch.setenv("VALOR_TEST_PORTS", f"{low}-{high}")
    monkeypatch.setattr(ports, "_last", None)
    held = socket.create_server(("127.0.0.1", low))
    try:
        assert ports.listen() == low + 1
        # The cursor moves on, and a port in `taken` is passed over.
        assert ports.listen(taken={low + 2}) == low + 3
        # Wrapping round, the held port is still refused.
        assert ports.listen() == low + 1

        # A port whose server closed after a connection sits in TIME_WAIT
        # on the server's side, and a server binding it the way servers
        # do (SO_REUSEADDR) can have it, so `listen()` returns it.
        server = socket.create_server(("127.0.0.1", low + 2))
        client = socket.create_connection(("127.0.0.1", low + 2))
        conn, _ = server.accept()
        conn.close()
        client.recv(1)
        client.close()
        server.close()
        assert ports.listen(taken={low + 3}) == low + 2

        # Every port held or taken: the error names the span.
        others = [socket.create_server(("127.0.0.1", p)) for p in (low + 1, low + 2)]
        try:
            with pytest.raises(RuntimeError, match=f"{low}-{high}"):
                ports.listen(taken={low + 3})
        finally:
            for s in others:
                s.close()
    finally:
        held.close()

    # A server on a returned port is reachable under a profile that allows
    # only the span.
    profile = tmp_path / "span.sb"
    profile.write_text(_span_profile(low, high))
    probe = (
        "import socket\n"
        "from tests import ports\n"
        "p = ports.listen()\n"
        "s = socket.create_server(('127.0.0.1', p))\n"
        "c = socket.create_connection(('127.0.0.1', p))\n"
        "a, _ = s.accept()\n"
        "c.sendall(b'ok')\n"
        "print(p, a.recv(2).decode())\n"
    )
    out = subprocess.run(
        [binaries.SANDBOX_EXEC, "-f", str(profile), sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={"VALOR_TEST_PORTS": f"{low}-{high}", "PYTHONPATH": "."},
        check=False,
    )
    assert out.returncode == 0, out.stderr
    port, said = out.stdout.split()
    assert low <= int(port) <= high and said == "ok"


def test_a_port_reserved_for_a_task_service_is_never_a_servers(monkeypatch, tmp_path):
    """A task's service starts after the test provisions it, often in a
    process of its own whose `listen()` starts at the span's low end; the
    reservation keeps every server off the port until the test ends."""
    low = next(p for p in range(LOW, HIGH - 2) if all(ports._bindable(q) for q in range(p, p + 4)))
    monkeypatch.setenv("VALOR_TEST_PORTS", f"{low}-{low + 3}")
    monkeypatch.setenv("VALOR_TEST_RESERVED_PORTS", str(tmp_path / "reserved"))
    monkeypatch.setattr(ports, "_last", None)
    reserved = ports.service((0, 0))
    assert reserved == low

    def fresh_listen() -> int:
        out = subprocess.run(
            [sys.executable, "-c", "from tests import ports; print(ports.listen())"],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        return int(out.stdout)

    assert fresh_listen() == low + 1
    assert ports.listen() == low + 1  # and in this process, wrapping round
    ports.release()
    assert fresh_listen() == low
