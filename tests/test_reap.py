"""A turn's processes end with the turn, daemonized or not, and the ledger
says which were stopped.

The incident: a replay turn ran a test setup that daemonized two
redis-servers (one on *:6379, one on a socket in /tmp); they re-parented to
launchd and outlived the turn. Here a stand-in turn daemonizes a child the
classic way (fork, setsid, fork), and another, under a task turn's sandbox,
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

import aiohttp
import pytest

from core import db, ledger, runs, serve, tasks
from core import workspace as kws
from core.gateway import Gateway
from tests.ports import listen

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


def run(coro):
    return asyncio.run(coro)


def _turn(dsn, build) -> tuple[dict, list[dict]]:
    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="test"))
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        try:
            ended = await runs.run_turn(gateway, task, build, dsn=dsn)
        finally:
            await gateway.close()
        async with await db.connect(dsn) as conn:
            return ended, await ledger.read(conn, task)

    return asyncio.run(go())


@pytest.mark.macos
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
def test_a_redis_server_daemonized_inside_a_task_turn_sandbox_is_reaped(dsn, tmp_path, short_dir):
    lay = kws.Layout(short_dir / "abcdef000001")
    for d in (lay.repo, lay.home, lay.cache, lay.work_state):
        d.mkdir(parents=True)
    run = workdir = lay.repo
    profile = lay.home / "turn.sb"
    profile.write_text(kws.turn_profile(lay, [], home=tmp_path / "home"))
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


def test_a_reaped_processs_command_line_is_recorded_whole():
    arg = "a" * 500
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", arg])
    try:
        time.sleep(0.5)
        assert runs.commands([proc.pid])[proc.pid].endswith(arg)
    finally:
        proc.kill()
        proc.wait()


# -- a cancelled turn -------------------------------------------------------------

# A harness that starts a child in its own group and a daemon that leaves the
# group by setsid, writes the child's pid, then waits to be stopped.
HARNESS = """
import os, subprocess, sys, time
d, daemon = sys.argv[1], sys.argv[2]
child = subprocess.Popen(['sleep', '60'])
subprocess.run([sys.executable, '-c', daemon, os.path.join(d, 'daemon.pid')], check=True)
with open(os.path.join(d, 'harness.pid'), 'w') as f:
    f.write(str(os.getpid()))
with open(os.path.join(d, 'child.pid.tmp'), 'w') as f:
    f.write(str(child.pid))
os.rename(os.path.join(d, 'child.pid.tmp'), os.path.join(d, 'child.pid'))
time.sleep(60)
"""

# The same daemon, deaf to SIGTERM, so the reap waits out its grace.
DEAF_DAEMON = DAEMON.replace(
    "os.setsid()\n", "os.setsid()\nimport signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
)


async def _gone(*pids: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not any(_alive(p) for p in pids):
            return True
        await asyncio.sleep(0.05)
    return not any(_alive(p) for p in pids)


async def _cancelled_turn(dsn, tmp_path, daemon: str, cancel_again: bool):
    """Start a turn of the children harness, cancel it once its processes
    are up (and, with `cancel_again`, again once its harness is gone and
    the cleanup is waiting on the reap). Returns what the test checks."""
    async with await db.connect(dsn) as conn:
        task = await tasks.start(conn, tasks.Brief(instruction="test"))
    gateway = Gateway(dsn)
    await gateway.start(port=listen())
    seen: dict = {}

    def build(url, brief, turn_id):
        seen.update(url=url, turn_id=turn_id)
        return runs.TurnCommand(
            argv=[sys.executable, "-c", HARNESS, str(tmp_path), daemon],
            env={"PATH": os.environ["PATH"]},
            cwd=str(tmp_path),
            harness="children",
        )

    turn = asyncio.create_task(runs.run_turn(gateway, task, build, dsn=dsn))
    child = await asyncio.to_thread(_wait_for, tmp_path / "child.pid")
    daemon_pid = int((tmp_path / "daemon.pid").read_text())
    harness_pid = int((tmp_path / "harness.pid").read_text())
    turn.cancel()
    if cancel_again:
        assert await _gone(harness_pid, child, within=5)
        await asyncio.sleep(0.2)
        assert _alive(daemon_pid)  # the reap is still in its grace
        turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    killed = await _gone(harness_pid, child, daemon_pid, within=runs.settings.reap_grace_s + 3)
    async with aiohttp.ClientSession() as http, http.post(seen["url"] + "/v1/messages", json={}) as resp:
        refused = resp.status
    async with await db.connect(dsn) as conn:
        rows = await ledger.read(conn, task)
        recovered = await serve.recover(conn)
        rows_after = await ledger.read(conn, task)
    return task, gateway, build, seen, daemon_pid, killed, refused, rows, recovered, rows_after


@pytest.mark.macos
def test_a_cancelled_turn_kills_its_harness_and_children_and_ends_interrupted(dsn, tmp_path):
    bystander = subprocess.Popen(["sleep", "30"])

    async def go():
        task, gateway, _, seen, daemon, killed, refused, rows, recovered, _ = await _cancelled_turn(
            dsn, tmp_path, DAEMON, cancel_again=False
        )
        # The task's next turn on the same gateway runs: no fence, the slot is free.
        try:
            done = lambda url, brief, turn_id: runs.TurnCommand(
                argv=[sys.executable, "-c", "pass"], env={}, cwd=str(tmp_path), harness="t"
            )
            nxt = await runs.run_turn(gateway, task, done, dsn=dsn)
        finally:
            await gateway.close()
        return seen, daemon, killed, refused, rows, recovered, nxt, gateway

    try:
        seen, daemon, killed, refused, rows, recovered, nxt, gateway = run(go())
        assert killed and bystander.poll() is None
        assert refused == 403
        assert seen["turn_id"] not in recovered["interrupted"]
        assert [r["type"] for r in rows][-2:] == ["turn.reaped", "turn.ended"]
        reaped, ended = rows[-2]["payload"], rows[-1]["payload"]
        assert daemon in [p["pid"] for p in reaped["processes"]]
        assert ended["turn_id"] == seen["turn_id"]
        assert ended["outcome"] == "interrupted" and ended["reason"] == "cancelled"
        assert ended["result"] == {} and "metered_usd_micros" in ended
        assert nxt["outcome"] == "done"
        assert not gateway.grants and not any(gateway.calls.values())
    finally:
        bystander.kill()
        bystander.wait()


@pytest.mark.macos
def test_a_turn_cancelled_again_during_its_cleanup_leaves_nothing_running_and_recovery_ends_it(dsn, tmp_path):
    async def go():
        out = await _cancelled_turn(dsn, tmp_path, DEAF_DAEMON, cancel_again=True)
        await out[1].close()
        return out

    _, _, _, seen, _, killed, _, rows, recovered, rows_after = run(go())
    assert killed
    assert "turn.ended" not in [r["type"] for r in rows]
    assert seen["turn_id"] in recovered["interrupted"]
    assert rows_after[-1]["payload"]["reason"] == "kernel restarted"


@pytest.mark.parametrize("code,outcome", [(0, "done"), (1, "failed")])
def test_a_turn_that_ends_on_its_own_writes_only_its_end(dsn, tmp_path, code, outcome):
    async def go():
        gateway = Gateway(dsn)
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="test"))
        await gateway.start(port=listen())
        try:
            build = lambda url, brief, turn_id: runs.TurnCommand(
                argv=[sys.executable, "-c", f"raise SystemExit({code})"],
                env={},
                cwd=str(tmp_path),
                harness="t",
            )
            ended = await runs.run_turn(gateway, task, build, dsn=dsn)
        finally:
            await gateway.close()
        async with await db.connect(dsn) as conn:
            return ended, await ledger.read(conn, task), gateway

    ended, rows, gateway = run(go())
    types = [r["type"] for r in rows]
    assert types[types.index("turn.started") + 1 :] == ["turn.ended"]
    assert ended["outcome"] == outcome and "reason" not in ended
    assert not gateway.grants


def test_a_turn_the_kernel_fails_after_its_start_ends_with_the_failure_named(dsn, tmp_path, monkeypatch):
    """Not a cancellation: the turn's output directory cannot be made. The
    error is raised, the grant is gone, and `turn.ended` names the error."""
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(runs, "output_paths", lambda task, turn: (blocker / "t.stdout", blocker / "t.stderr"))

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="test"))
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        build = lambda url, brief, turn_id: runs.TurnCommand(
            argv=[sys.executable, "-c", "pass"], env={}, cwd=str(tmp_path), harness="t"
        )
        try:
            with pytest.raises(OSError):
                await runs.run_turn(gateway, task, build, dsn=dsn)
        finally:
            await gateway.close()
        async with await db.connect(dsn) as conn:
            return await ledger.read(conn, task), gateway

    rows, gateway = run(go())
    ended = rows[-1]["payload"]
    assert rows[-1]["type"] == "turn.ended" and ended["outcome"] == "interrupted"
    assert (
        ended["reason"].startswith(("FileExistsError", "NotADirectoryError")) and ended["returncode"] is None
    )
    assert not gateway.grants
