"""The workspace sandbox-exec profiles under the real `sandbox-exec`: the
demonstration's, as `scripts/demo_workspace.sh` writes it, and a replay's,
as `scripts/replay_workspace.py` builds it.

A turn's gateway listens on whatever port the OS hands it, so a profile has
to admit the gateway at every port, not only the one a first turn happened to
get. It must still keep the machine's Postgres (5432, TCP and socket), its
Redis (6379), and the rest of loopback out of reach, while a replay reaches
the replays' own Postgres (5439) and Redis (6390). A replay's turn reads and
writes its own run and nothing else in the replay directory, and reads and
runs the shared binaries in its `bin/`.

Neither profile lets a turn read or write the kernel's password file, the
machine cluster's data directory (its `pg_hba.conf` and heap files), or the
backup disk, each named by its setting.

A turn listens only on 8000 to 8009 and on unix sockets inside its own
directory: never on 6379 or 5432, on any address, nor on an OS-assigned
port. sandbox-exec matches a bind by port alone, so 0.0.0.0:8001 passes
like 127.0.0.1:8001; what keeps a dev port off the LAN for long is that the
kernel reaps whatever a turn leaves running (tests/test_reap.py).

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

from core.settings import settings

PROBE = """
import os, socket, sys
for target in sys.argv[1:]:
    if target.startswith("bind:"):
        addr = target[5:]
        if addr.startswith("/"):
            s = socket.socket(socket.AF_UNIX)
        else:
            host, port = addr.rsplit(":", 1)
            host = host.strip("[]")
            s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
            addr = (host, int(port))
        try:
            s.bind(addr)
            s.listen()
            print("open")
        except PermissionError:
            print("denied")
        except OSError:
            print("open")
        finally:
            s.close()
            if isinstance(addr, str) and os.path.exists(addr):
                os.unlink(addr)
        continue
    if target.startswith(("stat:", "list:")):
        mode, path = target.split(":", 1)
        try:
            os.stat(path) if mode == "stat" else os.listdir(path)
            print("open")
        except PermissionError:
            print("denied")
        continue
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


def _kernel(tmp_path: Path) -> dict[str, Path]:
    """Stand-ins for the kernel's paths, one file in each."""
    paths = {
        "passdir": tmp_path / "kernel" / "valor-kernel",
        "pgdata": tmp_path / "kernel" / "pgdata",
        "backup": tmp_path / "kernel" / "backup",
    }
    for d in paths.values():
        d.mkdir(parents=True, exist_ok=True)
    (paths["passdir"] / "pgpass").write_text("*:*:valor_rebuild:valor_kernel:x\n")
    (paths["pgdata"] / "pg_hba.conf").write_text("local all all trust\n")
    (paths["backup"] / "valor_rebuild-20261001T030000Z.dump").write_text("x")
    return paths


def _kernel_probes(paths: dict[str, Path]) -> list[str]:
    files = [
        paths["passdir"] / "pgpass",
        paths["pgdata"] / "pg_hba.conf",
        paths["backup"] / "valor_rebuild-20261001T030000Z.dump",
    ]
    return [f"{mode}:{f}" for f in files for mode in ("read", "write")] + [f"list:{paths['backup']}"]


def _profile(tmp_path: Path) -> Path:
    """The profile text from the script's heredoc, expanded by bash with the
    script's own variables pointed at `tmp_path`."""
    heredoc = re.search(r"cat > home/sandbox\.sb <<EOF\n(.*?)\nEOF\n", SCRIPT.read_text(), re.DOTALL)
    assert heredoc
    out = tmp_path / "sandbox.sb"
    kernel = _kernel(tmp_path)
    env = {
        "PATH": "/usr/bin:/bin",
        "H": str(tmp_path / "home"),
        "DEMO": str(tmp_path / "demo"),
        "PG": str(tmp_path / "demo" / "pg"),
        "PG_PORT": "5439",
        "PG_SOCKET": settings.pg_socket_real,
        "KERNEL_SOCKET": settings.pg_socket,
        "TRANSCRIPTS": str(tmp_path / "transcripts"),
        "KERNEL_PASSDIR": str(kernel["passdir"]),
        "KERNEL_PGDATA": str(kernel["pgdata"]),
        "BACKUP_DIR": str(kernel["backup"]),
    }
    subprocess.run(
        ["bash", "-c", f'cat > "$0" <<EOF\n{heredoc.group(1)}\nEOF', str(out)], env=env, check=True
    )
    return out


def _probe(profile: Path, gateway_port: int, *targets) -> list[str]:
    sandbox = ["sandbox-exec", "-D", f"GATEWAY_PORT={gateway_port}", "-D", "VALOR_TURN=t", "-f", str(profile)]
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
            assert _probe(profile, port, port, 5439, settings.pgport, 6379, settings.pg_socket, other) == [
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
    for d in (workdir, run / "home", run / "origin.git", other, demo / "items", demo / "bin"):
        d.mkdir(parents=True)
    files = {
        "own": workdir / "app.py",
        "key": demo / "items" / "answer-key.md",
        "other": other / "app.py",
        "sandbox": run / "home" / "sandbox.sb",
        "origin": run / "origin.git" / "HEAD",
        "tool": demo / "bin" / "uv",
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
            tools=demo / "bin",
        )
    )
    with socket.socket() as gateway:
        gateway.bind(("127.0.0.1", 0))
        gateway.listen()
        port = gateway.getsockname()[1]
        assert _probe(
            profile, port, port, 5439, 6390, 8003, settings.pgport, 6379, settings.pg_socket, 6391
        ) == [
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
        f"read:{files['tool']}",
        f"write:{files['tool']}",
        f"stat:{demo / 'runs'}",
        f"stat:{home / 'src'}",
        f"list:{demo / 'runs'}",
        f"list:{home / 'src'}",
    ) == [
        *["open", "open", "open", "denied", "denied", "denied", "denied", "open", "denied"],
        *["open", "open", "denied", "denied"],
    ]


def test_a_replay_without_services_reaches_neither_service():
    ports = replay_workspace.ports_for([])
    assert 5439 not in ports and 6390 not in ports and 8000 in ports


def _binds(own_dir: Path) -> dict[str, str]:
    """Each bind a turn might try, and whether the profile must allow it."""
    return {
        "bind:0.0.0.0:6379": "denied",
        "bind:127.0.0.1:6379": "denied",
        "bind:[::]:6379": "denied",
        "bind:0.0.0.0:5432": "denied",
        "bind:127.0.0.1:5432": "denied",
        "bind:0.0.0.0:0": "denied",
        "bind:127.0.0.1:0": "denied",
        "bind:0.0.0.0:9000": "denied",
        "bind:127.0.0.1:8010": "denied",
        "bind:/tmp/popoto-redis-probe.sock": "denied",
        "bind:127.0.0.1:8001": "open",
        "bind:[::1]:8009": "open",
        f"bind:{own_dir / 'redis.sock'}": "open",
    }


def test_the_demo_turn_listens_only_on_dev_ports_and_its_own_sockets(tmp_path):
    profile = _profile(tmp_path)
    (tmp_path / "demo").mkdir()
    binds = _binds(tmp_path / "demo")
    assert dict(zip(binds, _probe(profile, 1, *binds))) == binds


def test_a_replay_turn_listens_only_on_dev_ports_and_its_own_sockets(tmp_path):
    run = tmp_path / "home" / "src" / "valor-demo" / "runs" / "toy-1-bare"
    (run / "toy").mkdir(parents=True)
    profile = tmp_path / "replay.sb"
    profile.write_text(
        replay_workspace.sandbox_profile(
            run=run,
            workdir=run / "toy",
            ports=replay_workspace.ports_for(["postgres", "redis"]),
            home=tmp_path / "home",
        )
    )
    binds = _binds(run)
    assert dict(zip(binds, _probe(profile, 1, *binds))) == binds


def test_the_demo_turn_cannot_reach_the_kernels_credential_data_or_dumps(tmp_path):
    profile = _profile(tmp_path)
    (tmp_path / "demo").mkdir(exist_ok=True)
    (tmp_path / "demo" / "own.txt").write_text("x")
    probes = _kernel_probes(_kernel(tmp_path))
    found = _probe(profile, 1, *probes, f"read:{tmp_path / 'demo' / 'own.txt'}")
    assert found == ["denied"] * len(probes) + ["open"]


def test_a_replay_turn_cannot_reach_the_kernels_credential_data_or_dumps(tmp_path):
    run = tmp_path / "home" / "src" / "valor-demo" / "runs" / "toy-1-bare"
    (run / "toy").mkdir(parents=True)
    (run / "toy" / "own.txt").write_text("x")
    kernel = _kernel(tmp_path)
    profile = tmp_path / "replay.sb"
    profile.write_text(
        replay_workspace.sandbox_profile(
            run=run,
            workdir=run / "toy",
            ports=replay_workspace.ports_for([]),
            home=tmp_path / "home",
            kernel=list(kernel.values()),
        )
    )
    probes = _kernel_probes(kernel)
    found = _probe(profile, 1, *probes, f"read:{run / 'toy' / 'own.txt'}")
    assert found == ["denied"] * len(probes) + ["open"]


def test_by_default_a_replay_profile_denies_the_kernel_paths_its_settings_name(tmp_path):
    """The real paths, from settings: the password file's directory, the
    machine cluster's data directory, and the backup disk (whose name is a
    private-use character). Probed read-only where they exist."""
    run = tmp_path / "home" / "src" / "valor-demo" / "runs" / "toy-1-bare"
    (run / "toy").mkdir(parents=True)
    text = replay_workspace.sandbox_profile(
        run=run, workdir=run / "toy", ports=replay_workspace.ports_for([]), home=tmp_path / "home"
    )
    for path in (Path(settings.pg_passfile).parent, Path(settings.pg_data_dir), Path(settings.backup_dir)):
        assert f'(subpath "{path}")' in text
    profile = tmp_path / "replay.sb"
    profile.write_text(text)
    probes = [f"read:{p}" for p in [Path(settings.pg_data_dir) / "PG_VERSION"] if p.exists()]
    probes += [f"list:{p}" for p in [Path(settings.backup_dir)] if p.is_dir()]
    assert _probe(profile, 1, *probes) == ["denied"] * len(probes)
