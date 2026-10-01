"""A turn's processes end with the turn, daemonized or not, and the ledger
says which were stopped.

The incident: a replay turn ran a test setup that daemonized two
redis-servers (one on *:6379, one on a socket in /tmp); they re-parented to
launchd and outlived the turn. Here a stand-in turn daemonizes a child the
classic way (fork, setsid, fork), and another, under the replay sandbox,
daemonizes a real redis-server, which retitles itself over its environment
so only the sandbox's mark names it. A process the turn did not start is
left alone.

Live spend: none.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from core import db, ledger, runs, tasks
from core.gateway import Gateway

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import replay_workspace

pytestmark = pytest.mark.spend(usd=0)

DAEMON = """
import os, sys, time
pidfile = sys.argv[1]
child = os.fork()
if child:
    os.waitpid(child, 0)
    while not os.path.exists(pidfile):
        time.sleep(0.01)
    sys.exit(0)
os.setsid()
if os.fork():
    os._exit(0)
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
with open(pidfile + ".tmp", "w") as f:
    f.write(str(os.getpid()))
os.rename(pidfile + ".tmp", pidfile)
time.sleep(60)
"""


def _alive(pid: int) -> bool:
    """Running, not merely a zombie waiting for launchd to collect it."""
    ps = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return ps.stdout.strip() not in ("", "Z")


@pytest.fixture
def short_dir():
    """A run directory short enough for a unix socket path (under 104
    bytes); pytest's tmp_path is not."""
    path = Path(tempfile.mkdtemp(prefix="reap-")).resolve()
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _wait_for(path: Path) -> int:
    for _ in range(100):
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"{path} never written")


def _turn(dsn, build) -> tuple[dict, list[dict]]:
    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="test", budget_usd_micros=0))
        gateway = Gateway(dsn)
        await gateway.start()
        try:
            ended = await runs.run_turn(gateway, task, build, dsn=dsn)
        finally:
            await gateway.close()
        async with await db.connect(dsn) as conn:
            return ended, await ledger.read(conn, task)

    return asyncio.run(go())


def test_a_daemon_the_turn_leaves_behind_is_reaped_and_ledgered(dsn, tmp_path):
    pidfile = tmp_path / "daemon.pid"
    bystander = subprocess.Popen(["sleep", "30"])
    try:
        build = lambda url, brief, turn_id: runs.TurnCommand(
            argv=[sys.executable, "-c", DAEMON, str(pidfile)],
            env={"PATH": os.environ["PATH"]},
            cwd=str(tmp_path),
            harness="daemonizer",
        )
        ended, rows = _turn(dsn, build)
        daemon = _wait_for(pidfile)
        assert ended["outcome"] == "done"
        assert not _alive(daemon)
        assert bystander.poll() is None
        reaped = [r["payload"] for r in rows if r["type"] == "turn.reaped"]
        assert len(reaped) == 1 and reaped[0]["turn_id"] == ended["turn_id"]
        assert [p["pid"] for p in reaped[0]["processes"]] == [daemon]
        assert reaped[0]["processes"][0]["signal"] == "SIGTERM"
        assert [r["type"] for r in rows][-2:] == ["turn.reaped", "turn.ended"]
    finally:
        bystander.kill()
        bystander.wait()


@pytest.mark.skipif(
    shutil.which("sandbox-exec") is None or shutil.which("redis-server") is None,
    reason="needs macOS sandbox-exec and redis-server",
)
def test_a_redis_server_daemonized_inside_the_replay_sandbox_is_reaped(dsn, tmp_path, short_dir):
    run = short_dir
    workdir = run / "toy"
    workdir.mkdir()
    profile = run / "sandbox.sb"
    profile.write_text(
        replay_workspace.sandbox_profile(
            run=run, workdir=workdir, ports=replay_workspace.ports_for([]), home=tmp_path / "home"
        )
    )
    redis = shutil.which("redis-server")
    socket_path = str(run / "redis.sock")
    build = lambda url, brief, turn_id: runs.TurnCommand(
        argv=[
            "sandbox-exec",
            "-D",
            "GATEWAY_PORT=1",
            "-D",
            f"VALOR_TURN={turn_id}",
            "-f",
            str(profile),
            redis,
            "--port",
            "0",
            "--unixsocket",
            socket_path,
            "--save",
            "",
            "--daemonize",
            "yes",
            "--pidfile",
            str(run / "redis.pid"),
            "--logfile",
            str(run / "redis.log"),
        ],
        env={"PATH": os.environ["PATH"]},
        cwd=str(workdir),
        harness="redis-daemonizer",
    )
    ended, rows = _turn(dsn, build)
    reaped = [r["payload"] for r in rows if r["type"] == "turn.reaped"]
    assert ended["outcome"] == "done", ended
    assert len(reaped) == 1, (run / "redis.log").read_text()
    assert [Path(p["command"].split()[0]).name for p in reaped[0]["processes"]] == ["redis-server"]
    assert not _alive(reaped[0]["processes"][0]["pid"])
    assert subprocess.run(["pgrep", "-f", socket_path], capture_output=True, check=False).returncode == 1


@pytest.mark.skipif(shutil.which("sandbox-exec") is None, reason="needs macOS sandbox-exec")
def test_the_sandbox_mark_names_only_the_turns_own_processes(tmp_path):
    """The mark the reaper trusts for daemons: a process under the turn's
    profile carries it; this process, an unsandboxed one, and a process
    under another turn's profile do not."""
    profile = tmp_path / "turn.sb"
    profile.write_text(
        '(version 1)(allow default)(deny mach-lookup (global-name (string-append "valor.turn." (param "VALOR_TURN"))))\n'
    )
    sandboxed = subprocess.Popen(
        ["sandbox-exec", "-D", "VALOR_TURN=abc123", "-f", str(profile), "sleep", "30"]
    )
    plain = subprocess.Popen(["sleep", "30"])
    try:
        time.sleep(0.3)
        assert runs._sandbox_marked(sandboxed.pid, "abc123")
        assert not runs._sandbox_marked(sandboxed.pid, "def456")
        assert not runs._sandbox_marked(plain.pid, "abc123")
        assert not runs._sandbox_marked(os.getpid(), "abc123")
    finally:
        for proc in (sandboxed, plain):
            proc.kill()
            proc.wait()
