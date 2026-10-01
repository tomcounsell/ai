"""The demo workspace's sandbox-exec profile, as `scripts/demo_workspace.sh`
writes it, under the real `sandbox-exec`.

A turn's gateway listens on whatever port the OS hands it, so the profile has
to admit the gateway at every port, not only the one a first turn happened to
get. It must still keep the machine's Postgres (5432, TCP and socket) and the
rest of loopback out of reach.

Live spend: none.
"""

import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.spend(usd=0),
    pytest.mark.skipif(shutil.which("sandbox-exec") is None, reason="needs macOS sandbox-exec"),
]

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "demo_workspace.sh"

PROBE = """
import socket, sys
for target in sys.argv[1:]:
    if target.startswith("/"):
        s = socket.socket(socket.AF_UNIX)
        addr = target
    else:
        s = socket.socket()
        addr = ("127.0.0.1", int(target))
    s.settimeout(1)
    try:
        s.connect(addr)
        print("open")
    except PermissionError:
        print("denied")
    except OSError:
        print("open")
    finally:
        s.close()
"""


def _profile(tmp_path: Path) -> Path:
    """The profile text from the script's heredoc, expanded by bash with the
    script's own variables pointed at `tmp_path`."""
    heredoc = re.search(r"cat > home/sandbox\.sb <<EOF\n(.*?)\nEOF\n", SCRIPT.read_text(), re.S)
    assert heredoc
    out = tmp_path / "sandbox.sb"
    env = {
        "PATH": "/usr/bin:/bin",
        "H": str(tmp_path / "home"),
        "DEMO": str(tmp_path / "demo"),
        "PG": str(tmp_path / "demo" / "pg"),
        "PG_PORT": "5439",
        "PG_SOCKET": "/private/tmp/.s.PGSQL.5432",
        "TRANSCRIPTS": str(tmp_path / "transcripts"),
    }
    subprocess.run(
        ["bash", "-c", f'cat > "$0" <<EOF\n{heredoc.group(1)}\nEOF', str(out)], env=env, check=True
    )
    return out


def _probe(profile: Path, gateway_port: int, *targets) -> list[str]:
    sandbox = ["sandbox-exec", "-D", f"GATEWAY_PORT={gateway_port}", "-f", str(profile)]
    run = subprocess.run(
        [*sandbox, sys.executable, "-c", PROBE, *(str(t) for t in targets)],
        capture_output=True,
        text=True,
        check=True,
    )
    return run.stdout.split()


def test_the_gateway_is_reachable_at_any_port_and_the_rest_of_loopback_is_not(tmp_path):
    profile = _profile(tmp_path)
    listeners = []
    for _ in range(24):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        listeners.append(s)
    try:
        ports = [s.getsockname()[1] for s in listeners]
        for i, port in enumerate(ports):
            other = ports[i - 1]
            assert _probe(profile, port, port, 5439, 5432, "/tmp/.s.PGSQL.5432", other) == [
                "open",
                "open",
                "denied",
                "denied",
                "denied",
            ], f"gateway port {port}"
    finally:
        for s in listeners:
            s.close()
