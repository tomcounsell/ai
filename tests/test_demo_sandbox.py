"""The workspace sandbox-exec profiles under the real `sandbox-exec`: the
demonstration's, as `scripts/demo_workspace.sh` writes it, and a replay's,
as `scripts/replay_workspace.py` builds it.

A turn's gateway listens on whatever port the OS hands it, so a profile has
to admit the gateway at every port, not only the one a first turn happened to
get. It must still keep the machine's Postgres (5432, TCP and socket), its
Redis (6379), and the rest of loopback out of reach, while a replay reaches
the replays' own Postgres (5439) and Redis (6390). A replay's turn reads and
writes its own run and nothing else in the replay directory.

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

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "demo_workspace.sh"
sys.path.insert(0, str(SCRIPTS))

import replay_workspace

PROBE = """
import socket, sys
for target in sys.argv[1:]:
    if target.startswith(("read:", "write:")):
        mode, path = target.split(":", 1)
        try:
            open(path, "a" if mode == "write" else "r").close()
            print("open")
        except PermissionError:
            print("denied")
        continue
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
    heredoc = re.search(r"cat > home/sandbox\.sb <<EOF\n(.*?)\nEOF\n", SCRIPT.read_text(), re.DOTALL)
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
            assert _probe(profile, port, port, 5439, 5432, 6379, "/tmp/.s.PGSQL.5432", other) == [
                "open",
                "open",
                "denied",
                "denied",
                "denied",
                "denied",
            ], f"gateway port {port}"
    finally:
        for s in listeners:
            s.close()


def test_a_replay_reaches_its_own_services_and_run_and_nothing_else(tmp_path):
    home = tmp_path / "home"
    demo = home / "src" / "valor-demo"
    run = demo / "runs" / "toy-1-bare"
    workdir = run / "toy"
    other = demo / "runs" / "toy-1-clarify" / "toy"
    for d in (workdir, run / "home", run / "origin.git", other, demo / "items"):
        d.mkdir(parents=True)
    files = {
        "own": workdir / "app.py",
        "key": demo / "items" / "answer-key.md",
        "other": other / "app.py",
        "sandbox": run / "home" / "sandbox.sb",
        "origin": run / "origin.git" / "HEAD",
    }
    for f in files.values():
        f.write_text("x")
    profile = tmp_path / "replay.sb"
    profile.write_text(
        replay_workspace.sandbox_profile(
            run=run,
            workdir=workdir,
            ports=replay_workspace.ports_for(["postgres", "redis"]),
            home=home,
        )
    )
    with socket.socket() as gateway:
        gateway.bind(("127.0.0.1", 0))
        gateway.listen()
        port = gateway.getsockname()[1]
        assert _probe(profile, port, port, 5439, 6390, 8003, 5432, 6379, "/tmp/.s.PGSQL.5432", 6391) == [
            "open",
            "open",
            "open",
            "open",
            "denied",
            "denied",
            "denied",
            "denied",
        ]
    assert _probe(
        profile,
        port,
        f"read:{files['own']}",
        f"write:{files['own']}",
        f"read:{files['sandbox']}",
        f"write:{files['sandbox']}",
        f"write:{files['origin']}",
        f"read:{files['key']}",
        f"read:{files['other']}",
    ) == ["open", "open", "open", "denied", "denied", "denied", "denied"]


def test_a_replay_without_services_reaches_neither_service():
    ports = replay_workspace.ports_for([])
    assert 5439 not in ports and 6390 not in ports and 8000 in ports
