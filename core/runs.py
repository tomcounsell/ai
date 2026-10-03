"""One turn: a harness subprocess, metered by the gateway, stoppable at any
instant.

The harness port is `TurnCommand`: the argv, environment, and working
directory that run one turn against a given gateway base URL, the
dispatched Brief, and the turn's id, plus how to read the result from
stdout. `core` never knows which harness it runs. The dispatched Brief is
rendered from the ledger as the turn starts, Tom's corrections included,
and recorded whole in `turn.started`, so the ledger shows exactly what the
turn was given.

Stop is lossless because nothing the turn owns lives only in this process.
The `task.stopped` row fences the task in the database; the notification
wakes this runner, which revokes the gateway (cutting any stream), kills the
harness's whole process group, waits for every in-flight call to be charged,
and writes `turn.ended`. A turn's durable state is its ledger rows, and each
of those lands whole or not at all.

A turn's processes do not outlive it. The turn runs with `VALOR_TURN` set
to its id, and a sandboxed turn's profile denies the mach name
`valor.turn.<id>`. When the turn ends, every process of this user that is
in the turn's process group, carries the marker in its environment, or sits
under a sandbox that denies the turn's name (and not another, which an App
Sandbox denies too) gets SIGTERM, then SIGKILL after `reap_grace_s` (settings), and
`turn.reaped` lists them. The sandbox mark is the one a daemon cannot shed:
it survives setsid, re-parenting to launchd, and a process retitling
itself over its environment (redis-server does), and platform binaries hide
their environment from other processes altogether.
"""

import asyncio
import ctypes
import functools
import os
import platform
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core import binaries, db, ledger, machine, spending, tasks
from core.gateway import Gateway
from core.settings import settings

TURN_ENV = "VALOR_TURN"


@dataclass
class TurnCommand:
    argv: list[str]
    env: dict[str, str]
    cwd: str
    harness: str
    parse: Callable[[bytes], dict[str, Any]] = field(default=lambda out: {})


async def run_turn(
    gateway: Gateway,
    task_id: str,
    build: Callable[[str, str], TurnCommand],
    dsn: str | None = None,
    state: str | None = None,
    fresh: str | None = None,
) -> dict[str, Any]:
    """Run one turn of the task to its end or its stop, in `state` (the
    state machine's state the turn works in, recorded on `turn.started`;
    None for a turn outside the machine). `fresh` names the stage of a fresh
    session (critique, review, docs): its Brief carries the verdict channel,
    and `turn.started` says `fresh: true`, so the fold never resumes its
    session. Returns the `turn.ended` payload."""
    turn_id = ledger.new_id()
    dsn = dsn or gateway.dsn
    listener = await db.connect(dsn)
    try:
        await listener.execute(f"LISTEN {tasks.STOP_CHANNEL}")
        base_url = gateway.issue(task_id, turn_id)
        async with await db.connect(dsn) as conn, conn.transaction():
            await ledger.lock(conn, f"task:{task_id}")
            if await tasks.is_calibration(conn, task_id):
                gateway.retire(task_id)
                raise tasks.CalibrationTask(f"task {task_id} is a calibration task; it runs no turn")
            if await tasks.is_stopped(conn, task_id):
                gateway.retire(task_id)
                raise tasks.TaskStopped(task_id)
            dispatched = await tasks.dispatch(
                conn, task_id, state=machine.State(state) if state else None, fresh=fresh
            )
            command = build(base_url, dispatched["text"], turn_id)
            await ledger.append(
                conn,
                task_id,
                "turn.started",
                {
                    "turn_id": turn_id,
                    "state": state,
                    **({"fresh": True, "stage": fresh} if fresh else {}),
                    "harness": command.harness,
                    "argv": command.argv,
                    "brief": dispatched["text"],
                    "brief_sha256": dispatched["sha256"],
                    "corrections": dispatched["corrections"],
                },
            )
        proc = await asyncio.create_subprocess_exec(
            *command.argv,
            env={**command.env, TURN_ENV: turn_id},
            cwd=command.cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        finished = asyncio.create_task(proc.communicate())
        stopped = asyncio.create_task(_stop_heard(listener, task_id))
        done, _ = await asyncio.wait({finished, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if stopped in done:
            gateway.revoke(task_id)
            _kill_group(proc.pid)
            stdout, stderr = await finished
            outcome = "stopped"
        else:
            stopped.cancel()
            gateway.retire(task_id)
            stdout, stderr = finished.result()
            outcome = "done" if proc.returncode == 0 else "failed"
        await gateway.drain(task_id)
        reaped = await asyncio.to_thread(reap, turn_id, proc.pid)
    finally:
        await listener.close()

    ended = {
        "turn_id": turn_id,
        "outcome": outcome,
        "returncode": proc.returncode,
        "result": command.parse(stdout) if outcome != "stopped" else {},
        "stderr_tail": stderr.decode(errors="replace")[-400:],
    }
    async with await db.connect(dsn) as conn:
        ended["metered_usd_micros"] = await spending.turn_spent(conn, task_id, turn_id)
        async with conn.transaction():
            if reaped:
                await ledger.append(conn, task_id, "turn.reaped", {"turn_id": turn_id, "processes": reaped})
            await ledger.append(conn, task_id, "turn.ended", ended)
    return ended


async def _stop_heard(listener, task_id: str) -> None:
    async for note in listener.notifies():
        if note.payload == task_id:
            return


def _kill_group(pid: int) -> None:
    """SIGKILL the harness and everything it spawned. Nothing inside a turn
    is trusted to honor a gentler signal."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def reap(turn_id: str, pgid: int | None = None) -> list[dict[str, Any]]:
    """Stop every process the turn left behind (marked with `turn_id`, or in
    process group `pgid`); return what was stopped."""
    return _stop(_turn_processes(turn_id, pgid))


def reap_sandboxed(name: str, control: str) -> list[dict[str, Any]]:
    """Stop every process of this user under a sandbox that denies the mach
    name `name` and not `control` (an App Sandbox denies every such name):
    how a task's services are found, also after the kernel died."""
    return _stop(sandboxed_pids(name, control))


def sandboxed_pids(name: str, control: str) -> list[int]:
    """Every process of this user under a sandbox that denies `name` and
    not `control`."""
    denies = _sandbox_check()
    if not denies:
        return []
    listing = subprocess.run(
        [binaries.require(binaries.PS), "-A", "-o", "pid=,uid="], capture_output=True, text=True, check=True
    ).stdout
    pids = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 2 or int(fields[1]) != os.getuid() or int(fields[0]) == os.getpid():
            continue
        pid = int(fields[0])
        if denies(pid, name) and not denies(pid, control):
            pids.append(pid)
    return pids


def commands(pids: list[int]) -> dict[int, str]:
    return _commands(pids) if pids else {}


def marked_services(task_ids: list[str]) -> set[str]:
    """Which of these tasks have a process of this user running under their
    service mark (`valor.service.<task>`), from one process listing."""
    denies = _sandbox_check()
    if not denies or not task_ids:
        return set()
    listing = subprocess.run(
        [binaries.require(binaries.PS), "-A", "-o", "pid=,uid="], capture_output=True, text=True, check=True
    ).stdout
    found: set[str] = set()
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 2 or int(fields[1]) != os.getuid() or denies(int(fields[0]), "valor.service.none"):
            continue
        found.update(t for t in task_ids if t not in found and denies(int(fields[0]), f"valor.service.{t}"))
    return found


def _stop(pids: list[int]) -> list[dict[str, Any]]:
    if not pids:
        return []
    names = _commands(pids)
    for pid in pids:
        _signal(pid, signal.SIGTERM)
    deadline = time.monotonic() + settings.reap_grace_s
    alive = set(pids)
    while alive and time.monotonic() < deadline:
        time.sleep(0.05)
        alive = {pid for pid in alive if _signal(pid, 0)}
    for pid in alive:
        _signal(pid, signal.SIGKILL)
    return [
        {"pid": pid, "command": names.get(pid, ""), "signal": "SIGKILL" if pid in alive else "SIGTERM"}
        for pid in sorted(pids)
    ]


def _turn_processes(turn_id: str, pgid: int | None) -> list[int]:
    mark = f"{TURN_ENV}={turn_id}"
    listing = subprocess.run(
        [binaries.require(binaries.PS), "-A", "-E", "-ww", "-o", "pid=,pgid=,uid=,command="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) < 3 or int(fields[2]) != os.getuid() or int(fields[0]) == os.getpid():
            continue
        pid = int(fields[0])
        if int(fields[1]) == pgid or mark in fields[3:] or _sandbox_marked(pid, turn_id):
            found.append(pid)
    return found


def _commands(pids: list[int]) -> dict[int, str]:
    listing = subprocess.run(
        [binaries.require(binaries.PS), "-ww", "-o", "pid=,command=", "-p", ",".join(map(str, pids))],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    rows = (line.strip().split(None, 1) for line in listing.splitlines() if line.strip())
    return {int(row[0]): (row[1] if len(row) > 1 else "")[:200] for row in rows}


def _signal(pid: int, sig: int) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError, PermissionError:
        return False


@functools.cache
def _sandbox_check():
    """libsandbox's `sandbox_check`, or None off macOS. It is variadic, and
    on arm64 a variadic argument goes on the stack past the eight argument
    registers, so five placeholders put the name there."""
    if sys.platform != "darwin":
        return None
    check = ctypes.CDLL("/usr/lib/libSystem.B.dylib").sandbox_check
    pad = 5 if platform.machine() == "arm64" else 0
    check.restype = ctypes.c_int
    check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, *[ctypes.c_void_p] * pad, ctypes.c_char_p]
    return lambda pid, name: check(pid, b"mach-lookup", 2 | 0x40000000, *[None] * pad, name.encode()) == 1


def _sandbox_marked(pid: int, turn_id: str) -> bool:
    """Whether the process's sandbox denies the turn's mach name and not
    another: GLOBAL_NAME filter, without logging the check."""
    denies = _sandbox_check()
    return bool(denies) and denies(pid, f"valor.turn.{turn_id}") and not denies(pid, "valor.turn.none")
