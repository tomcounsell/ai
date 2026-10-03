"""The workspace sandbox-exec profiles under the real `sandbox-exec`: a
kernel task's working session's, as `core/workspace.py` writes it, and the
emulator's verification profile built from it.

A turn's gateway listens on whatever port the OS hands it, so a profile has
to admit the gateway at every port, not only the one a first turn happened to
get. It must still keep the machine's Postgres (5432, TCP and socket), its
Redis (6379), and the rest of loopback out of reach, while a task reaches
its own Postgres and Redis. A task's turn reads and writes its own clone,
caches, and state and nothing else in the work directory, and reads and
runs the shared binaries in its `bin/`. It writes no shared temp directory
(`/private/tmp`, `/private/var/tmp`, `/private/var/folders`); its own
`TMPDIR` lives in its state.

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

from core import workspace as kws
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


def _probe(profile: Path, gateway_port: int, *targets) -> list[str]:
    sandbox = ["sandbox-exec", "-D", f"GATEWAY_PORT={gateway_port}", "-D", "VALOR_TURN=t", "-f", str(profile)]
    run = subprocess.run(
        [*sandbox, sys.executable, "-c", PROBE, *(str(t) for t in targets)],
        capture_output=True,
        text=True,
        check=True,
    )
    return run.stdout.split()


def _task(tmp_path: Path, name: str = "abcdef000001") -> kws.Layout:
    """A task's directory laid out as `core/workspace.py` provisions one."""
    lay = kws.Layout(tmp_path / "home" / "valor-tasks" / name)
    for d in (lay.repo, lay.home, lay.origin, lay.mirror, lay.cache, lay.work_state, lay.checks, lay.pg / "data",
              lay.root.parent / "bin"):  # fmt: skip
        d.mkdir(parents=True, exist_ok=True)
    return lay


def _turn_profile(tmp_path: Path, lay: kws.Layout, ports: list[int], kernel=None) -> Path:
    profile = tmp_path / f"{lay.root.name}.sb"
    profile.write_text(kws.turn_profile(lay, ports, home=tmp_path / "home", kernel=kernel))
    return profile


def test_a_task_turn_reaches_its_own_services_and_clone_and_nothing_else(tmp_path):
    lay, other = _task(tmp_path), _task(tmp_path, "abcdef000002")
    key = tmp_path / "home" / "src" / "valor-demo" / "items" / "answer-key.md"
    key.parent.mkdir(parents=True)
    files = {
        "own": lay.repo / "app.py",
        "key": key,
        "other": other.repo / "app.py",
        "home": lay.home / "gitconfig",
        "origin": lay.origin / "HEAD",
        "mirror": lay.mirror / "HEAD",
        "pgdata": lay.pg / "data" / "pg_hba.conf",
        "tool": lay.root.parent / "bin" / "uv",
        "state": lay.work_state / "tmp.txt",
    }
    for f in files.values():
        f.write_text("x")
    profile = _turn_profile(tmp_path, lay, [5545, 6445])
    with socket.socket() as gateway:
        gateway.bind(("127.0.0.1", 0))
        gateway.listen()
        port = gateway.getsockname()[1]
        assert _probe(
            profile, port, port, 5545, 6445, 8003, settings.pgport, 6379, settings.pg_socket, 5546, 5439
        ) == ["open", "open", "open", "open", "denied", "denied", "denied", "denied", "denied"]
    assert _probe(
        profile,
        port,
        f"read:{files['own']}",
        f"write:{files['own']}",
        f"read:{files['home']}",
        f"write:{files['home']}",
        f"read:{files['origin']}",
        f"write:{files['origin']}",
        f"read:{files['mirror']}",
        f"read:{files['pgdata']}",
        f"read:{files['key']}",
        f"read:{files['other']}",
        f"read:{files['tool']}",
        f"write:{files['tool']}",
        f"write:{files['state']}",
        f"stat:{lay.root.parent}",
        f"list:{lay.root.parent}",
        f"list:{tmp_path / 'home' / 'src'}",
    ) == [
        *["open", "open", "open", "denied", "open", "denied", "denied", "denied", "denied", "denied"],
        *["open", "denied", "open", "open", "denied", "denied"],
    ]


def test_a_task_turn_reaches_the_gateway_at_any_port(tmp_path):
    profile = _turn_profile(tmp_path, _task(tmp_path), [5545])
    listeners = []
    for _ in range(24):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        listeners.append(s)
    try:
        ports = [s.getsockname()[1] for s in listeners]
        for i, port in enumerate(ports):
            assert _probe(profile, port, port, 5545, settings.pgport, ports[i - 1]) == [
                "open", "open", "denied", "denied"
            ], f"gateway port {port}"  # fmt: skip
    finally:
        for s in listeners:
            s.close()


def test_a_task_without_services_reaches_neither_service(tmp_path):
    lay = _task(tmp_path)
    profile = _turn_profile(tmp_path, lay, [])
    assert _probe(profile, 1, 5545, 6445, 8000) == ["denied", "denied", "open"]


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


def test_a_task_turn_listens_only_on_dev_ports_and_its_own_sockets(tmp_path):
    lay = _task(tmp_path)
    profile = _turn_profile(tmp_path, lay, [5545, 6445])
    binds = _binds(lay.repo)
    assert dict(zip(binds, _probe(profile, 1, *binds), strict=True)) == binds


def test_a_task_turn_cannot_reach_the_kernels_credential_data_or_dumps(tmp_path):
    lay = _task(tmp_path)
    (lay.repo / "own.txt").write_text("x")
    kernel = _kernel(tmp_path)
    profile = _turn_profile(tmp_path, lay, [], kernel=list(kernel.values()))
    probes = _kernel_probes(kernel)
    found = _probe(profile, 1, *probes, f"read:{lay.repo / 'own.txt'}")
    assert found == ["denied"] * len(probes) + ["open"]


def test_by_default_a_task_profile_denies_the_kernel_paths_its_settings_name(tmp_path):
    """The real paths, from settings: the password file's directory, the
    machine cluster's data directory, and the backup disk (whose name is a
    private-use character). Probed read-only where they exist."""
    text = kws.turn_profile(_task(tmp_path), [], home=tmp_path / "home")
    for path in (Path(settings.pg_passfile).parent, Path(settings.pg_data_dir), Path(settings.backup_dir)):
        assert f'(subpath "{path}")' in text
    profile = tmp_path / "replay.sb"
    profile.write_text(text)
    probes = [f"read:{p}" for p in [Path(settings.pg_data_dir) / "PG_VERSION"] if p.exists()]
    probes += [f"list:{p}" for p in [Path(settings.backup_dir)] if p.is_dir()]
    assert _probe(profile, 1, *probes) == ["denied"] * len(probes)


def test_a_task_turn_writes_no_shared_temp_directory_and_its_own_tmpdir(tmp_path):
    lay = _task(tmp_path)
    profile = _turn_profile(tmp_path, lay, [])
    own = lay.work_state / "tmp"
    own.mkdir()
    name = f"valor-tmp-probe-{lay.root.name}"
    folders = Path(subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True,
                                  check=True).stdout.strip()).resolve()  # fmt: skip
    found = _probe(
        profile,
        1,
        f"write:/private/tmp/{name}",
        f"write:/private/var/tmp/{name}",
        f"write:{folders / name}",
        "list:/private/tmp",
        f"write:{own / name}",
    )
    assert found == ["denied", "denied", "denied", "denied", "open"]
    assert not Path(f"/private/tmp/{name}").exists()


def test_two_tasks_cannot_meet_in_tmp(tmp_path):
    """Neither of two tasks' turns can write the same `/tmp` name, so
    neither can read what the other left there."""
    one, two = _task(tmp_path), _task(tmp_path, "abcdef000002")
    name = Path(f"/private/tmp/valor-shared-{one.root.name}")
    name.write_text("left by something outside both tasks")
    try:
        assert _probe(_turn_profile(tmp_path, one, []), 1, f"write:{name}") == ["denied"]
        assert _probe(_turn_profile(tmp_path, two, []), 1, f"read:{name}") == ["denied"]
    finally:
        name.unlink()


def test_the_verification_profile_shares_tmp_and_adds_its_tree(tmp_path):
    """The emulator's verification runs under the working profile as the
    baseline ran it, with the temp directories shared, plus its own tree
    under `checks/`; the working profile itself cannot write that tree."""
    lay = _task(tmp_path)
    tree = lay.checks / "verify-run"
    tree.mkdir(parents=True)
    working = _turn_profile(tmp_path, lay, [])
    verifying = tmp_path / "verify.sb"
    verifying.write_text(kws.turn_profile(lay, [], home=tmp_path / "home", tmp=True, rw=[tree]))
    name = f"/private/tmp/valor-verify-{lay.root.name}"
    try:
        assert _probe(working, 1, f"write:{tree / 'x'}") == ["denied"]
        assert _probe(verifying, 1, f"write:{tree / 'x'}", f"write:{name}") == ["open", "open"]
    finally:
        Path(name).unlink(missing_ok=True)
