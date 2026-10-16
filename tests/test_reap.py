"""A turn's processes end with the turn, daemonized or not, and the ledger
says which were stopped.

The incident: a replay turn ran a test setup that daemonized two
redis-servers (one on *:6379, one on a socket in /tmp); they re-parented to
launchd and outlived the turn. Here a stand-in turn daemonizes a child the
classic way (fork, setsid, fork), and a cancelled turn's harness, children
and daemon are all killed. A process the turn did not start is left alone.

Live spend: none.
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest

from core import db, ledger, runs, serve, tasks
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


def _wait_for(path: Path) -> int:
    for _ in range(100):
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"{path} never written")


run = asyncio.run


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
        assert not runs.alive(daemon)
        assert bystander.poll() is None
        reaped = [r["payload"] for r in rows if r["type"] == "turn.reaped"]
        assert len(reaped) == 1 and reaped[0]["turn_id"] == ended["turn_id"]
        assert [p["pid"] for p in reaped[0]["processes"]] == [daemon]
        assert reaped[0]["processes"][0]["signal"] == "SIGTERM"
        assert [r["type"] for r in rows][-2:] == ["turn.reaped", "turn.ended"]
    finally:
        bystander.kill()
        bystander.wait()


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


async def _gone(*pids: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not any(runs.alive(p) for p in pids):
            return True
        await asyncio.sleep(0.05)
    return not any(runs.alive(p) for p in pids)


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
        assert runs.alive(daemon_pid)  # the reap is still in its grace
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


# -- the process table --------------------------------------------------------------
#
# Read in-process, since a sandboxed caller may not exec the setuid `/bin/ps`:
# this suite runs under the turn's profile in the verify check on macOS, and
# from `/proc` in the verification VM. The tests below hold on both; the
# `/proc` reader is also run against a planted tree, so the Mac exercises it.

SLEEP = "import time; time.sleep(30)"


def _child(env: dict[str, str] | None = None, *, own_session: bool = True) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", SLEEP],
        env={"PATH": os.environ["PATH"], **(env or {})},
        start_new_session=own_session,
    )


def _ended(*procs: subprocess.Popen) -> None:
    for p in procs:
        p.kill()
        p.wait()


def test_the_process_table_reads_a_childs_group_and_owner_and_lists_only_this_users_others():
    leader, member = _child(), _child(own_session=False)
    try:
        table = runs.own_processes()
        assert table[leader.pid] == os.getpgid(leader.pid) == leader.pid
        assert table[member.pid] == os.getpgid(member.pid) == os.getpgrp()
        assert runs.owner_and_group(leader.pid) == (os.getuid(), leader.pid)
        others = [
            p for p in runs._all_pids() if (runs.owner_and_group(p) or (os.getuid(),))[0] != os.getuid()
        ]
        assert not set(others) & set(table) and os.getpid() not in table
    finally:
        _ended(leader, member)


def test_reap_finds_a_turns_process_by_its_exact_environment_entry_alone():
    """A process in a session of its own, under no turn sandbox, carrying
    `VALOR_TURN=<turn>`: found by that entry. A turn whose id has this one as
    a prefix, and a variable whose value holds the entry, are left alone."""
    turn = ledger.new_id()
    marked = _child({runs.TURN_ENV: turn})
    longer = _child({runs.TURN_ENV: turn + "0"})
    quoted = _child({"OTHER": f"{runs.TURN_ENV}={turn}"})
    try:
        time.sleep(0.3)  # the interpreter re-executes itself once on macOS
        reaped = runs.reap(turn)
        assert [r["pid"] for r in reaped] == [marked.pid]
        assert reaped[0]["signal"] == "SIGTERM"
        assert reaped[0]["command"].split(" ", 1)[1] == f"-c {SLEEP}"
        assert marked.wait(5) == -15
        assert longer.poll() is None and quoted.poll() is None
    finally:
        _ended(marked, longer, quoted)


def test_reap_finds_a_turns_process_by_its_group_alone():
    child = _child()
    try:
        reaped = runs.reap(ledger.new_id(), child.pid)
        assert [(r["pid"], r["signal"]) for r in reaped] == [(child.pid, "SIGTERM")]
        assert child.wait(5) == -15
    finally:
        _ended(child)


def test_a_zombie_or_a_collected_pid_is_not_alive_and_has_no_command():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 5
    while runs.alive(child.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not runs.alive(child.pid) and child.returncode is None  # exited, not collected
    assert child.pid not in runs.own_processes()
    child.wait()
    assert not runs.alive(child.pid)
    assert runs.command(child.pid) == "" and runs.commands([child.pid]) == {child.pid: ""}
    assert runs.commands([]) == {} and runs.reap(ledger.new_id()) == []
    running = _child()
    try:
        assert runs.alive(running.pid)
    finally:
        _ended(running)


def test_the_proc_reader_takes_group_owner_arguments_and_environment_and_reads_a_zombie_as_gone(
    tmp_path, monkeypatch
):
    def plant(pid: int, stat: str, cmdline: bytes = b"", environ: bytes = b"") -> None:
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "stat").write_text(stat)
        (d / "cmdline").write_bytes(cmdline)
        (d / "environ").write_bytes(environ)

    turn = ledger.new_id()
    environ = f"{runs.TURN_ENV}={turn}\0A=b\0".encode()
    plant(41, "41 (a) b) c) S 1 4100 4100 0 -1", b"python3\0-c\0pass\0", environ)
    plant(42, "42 (z) Z 1 4200 4200 0 -1", b"", b"")
    (tmp_path / "self").mkdir()
    monkeypatch.setattr(runs, "_DARWIN", False)
    monkeypatch.setattr(runs, "PROC", tmp_path)
    assert sorted(runs._all_pids()) == [41, 42]
    assert runs.owner_and_group(41) == (os.getuid(), 4100)
    assert runs.own_processes() == {41: 4100}
    assert not runs.alive(42) and not runs.alive(43)
    assert runs.command(41) == "python3 -c pass" and runs.command(43) == ""
    assert runs._arguments(41)[1] == [f"{runs.TURN_ENV}={turn}", "A=b"]
    assert runs._turn_processes(turn, None) == [41]
    assert runs._turn_processes(turn + "0", 4100) == [41] and runs._turn_processes(turn + "0", None) == []
