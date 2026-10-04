"""One turn: a harness subprocess, metered by the gateway, stoppable at any
instant.

The harness port is `TurnCommand`: the argv, environment, and working
directory that run one turn against a given gateway base URL, the
dispatched Brief, and the turn's id, plus how to read the result from
stdout. `core` never knows which harness it runs. The dispatched text is
rendered as the turn starts, the persona first and Tom's corrections from
the ledger, and recorded whole in `turn.started` with its digest and the
persona's, so the ledger shows exactly what the
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
`turn.reaped` lists them.

A turn's whole stdout and stderr go to `<work_dir>/<task>/turns/<turn>.stdout`
and `.stderr`, outside every path a turn can write; `turn.ended` names both
files, and the harness reads its result from the stdout file. The harness
holds pipes, never the files: the kernel copies both pipes into the files at
once, so neither fills while the other is read, until EOF, which comes once
the reap has ended every process of the turn. A sandbox denies every path
under the work dir, and node aborts at startup when its stdout or stderr is
a file at a path it cannot read. The sandbox mark is the one a daemon cannot shed:
it survives setsid, re-parenting to launchd, and a process retitling
itself over its environment (redis-server does), and platform binaries hide
their environment from other processes altogether.

A turn with its own Claude Code config directory names its session
(`TurnCommand.transcript`); once its processes are reaped, its transcript
is copied into the store (`core.transcripts`) and `turn.ended` records the
copy, or why there is none. A resumed or new session's id is the kernel's,
so `turn.ended` records it in place of the one stdout names.
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import binaries, db, git, ledger, machine, slot, spending, tasks, transcripts
from core.gateway import Gateway
from core.settings import settings

TURN_ENV = "VALOR_TURN"
Transcript = transcripts.Transcript


def output_paths(task_id: str, turn_id: str) -> tuple[Path, Path]:
    """Where a turn's stdout and stderr are written."""
    turns = Path(settings.work_dir).expanduser() / task_id / "turns"
    return turns / f"{turn_id}.stdout", turns / f"{turn_id}.stderr"


@dataclass
class TurnCommand:
    argv: list[str]
    env: dict[str, str]
    cwd: str
    harness: str
    parse: Callable[[bytes], dict[str, Any]] = field(default=lambda out: {})
    harness_version: str | None = None
    # What the harness reads on standard input, when the prompt travels
    # there; None closes stdin with nothing in it.
    stdin: bytes | None = None
    transcript: Transcript | None = None


async def run_turn(
    gateway: Gateway,
    task_id: str,
    build: Callable[[str, str], TurnCommand],
    dsn: str | None = None,
    state: str | None = None,
    fresh: str | None = None,
    offered: Sequence[str] = (),
) -> dict[str, Any]:
    """Run one turn of the task to its end or its stop, in `state` (the
    state machine's state the turn works in, recorded on `turn.started`;
    None for a turn outside the machine). `fresh` names the stage of a fresh
    session (critique, review, docs): its Brief carries the verdict channel,
    and `turn.started` says `fresh: true`, so the fold never resumes its
    session. `offered` is the usage lines of the task's performers, which
    the working session's Brief lists. Returns the `turn.ended` payload.

    The turn holds the turn slot (`core/slot.py`) from before its Brief is
    rendered until `turn.ended` is written."""
    async with slot.held(task_id, dsn or gateway.dsn):
        return await _run_turn(gateway, task_id, build, dsn, state, fresh, offered)


# The kernel's checkout: its persona and stage files are read from it.
CHECKOUT = Path(__file__).resolve().parent.parent


def kernel_commit() -> str | None:
    """The commit the kernel's checkout is at, read on every turn: a
    resident kernel's checkout moves under it, and each turn reads the
    persona and stage files afresh, so `turn.started` records the commit
    those files were read from. The same store and the same commit give
    the same Brief and prompt."""
    try:
        return git.head(CHECKOUT)
    except Exception:  # noqa: BLE001  no trusted git, or not a checkout: nothing to record
        return None


async def _run_turn(gateway, task_id, build, dsn, state, fresh, offered) -> dict[str, Any]:
    turn_id = ledger.new_id()
    dsn = dsn or gateway.dsn
    listener = await db.connect(dsn)
    try:
        await listener.execute(f"LISTEN {tasks.STOP_CHANNEL}")
        base_url = gateway.issue(task_id, turn_id)
        # The grant is issued before the dispatch; a turn that never starts
        # (stopped, calibration, a persona that cannot be read) leaves none.
        try:
            async with await db.connect(dsn) as conn, conn.transaction():
                await ledger.lock(conn, f"task:{task_id}")
                if await tasks.is_calibration(conn, task_id):
                    raise tasks.CalibrationTask(f"task {task_id} is a calibration task; it runs no turn")
                if await tasks.is_stopped(conn, task_id):
                    raise tasks.TaskStopped(task_id)
                dispatched = await tasks.dispatch(
                    conn,
                    task_id,
                    state=machine.State(state) if state else None,
                    fresh=fresh,
                    offered=offered,
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
                        "harness_version": command.harness_version,
                        "argv": command.argv,
                        "brief": dispatched["text"],
                        "brief_sha256": dispatched["sha256"],
                        "corrections": dispatched["corrections"],
                        "kernel_commit": kernel_commit(),
                        "offered": dispatched["offered"],
                        "persona_sha256": dispatched["persona_sha256"],
                        "persona_bytes": dispatched["persona_bytes"],
                    },
                )
        except BaseException:
            gateway.retire(task_id)
            raise
        out_path, err_path = output_paths(task_id, turn_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as out, err_path.open("wb") as err:
            proc = await asyncio.create_subprocess_exec(
                *command.argv,
                env={**command.env, TURN_ENV: turn_id},
                cwd=command.cwd,
                stdin=asyncio.subprocess.DEVNULL if command.stdin is None else asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            pumped = asyncio.gather(
                _pump(proc.stdout, out), _pump(proc.stderr, err), _feed(proc, command.stdin)
            )
            try:
                finished = asyncio.create_task(proc.wait())
                stopped = asyncio.create_task(_stop_heard(listener, task_id))
                done, _ = await asyncio.wait({finished, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if stopped in done:
                    gateway.revoke(task_id)
                    _kill_group(proc.pid)
                    await finished
                    outcome = "stopped"
                else:
                    stopped.cancel()
                    gateway.retire(task_id)
                    gateway.cut(task_id)  # its process has exited: no client is left
                    outcome = "done" if proc.returncode == 0 else "failed"
                await gateway.drain(task_id)
                reaped = await asyncio.to_thread(reap, turn_id, proc.pid)
                # Every process of the turn has ended, so both pipes are at EOF.
                await pumped
            except BaseException:
                pumped.cancel()
                raise
    finally:
        await listener.close()

    ended = {
        "turn_id": turn_id,
        "outcome": outcome,
        "returncode": proc.returncode,
        "result": command.parse(out_path.read_bytes()) if outcome != "stopped" else {},
        "stdout": str(out_path),
        "stderr": str(err_path),
    }
    if command.transcript is not None and ended["result"].get("session_id"):
        ended["result"]["session_id"] = command.transcript.session_id
    async with await db.connect(dsn) as conn:
        if command.transcript is not None:
            ended.update(await transcripts.copy(conn, task_id, turn_id, command.transcript))
        ended["metered_usd_micros"] = await spending.turn_spent(conn, task_id, turn_id)
        async with conn.transaction():
            if reaped:
                await ledger.append(conn, task_id, "turn.reaped", {"turn_id": turn_id, "processes": reaped})
            ended = await _ended(conn, task_id, ended)
    return ended


# What a harness's parsed result holds besides the turn's own words.
RESULT_FIELDS = ("is_error", "num_turns", "harness_reported_usd", "session_id")
KERNEL_FIELDS = ("turn_id", "outcome", "returncode", "stdout", "stderr", "metered_usd_micros")


async def _ended(conn, task_id: str, ended: dict[str, Any]) -> dict[str, Any]:
    """Write `turn.ended` and return what was written. The result holds the
    turn's final message, which Postgres jsonb may refuse (a NUL, or a size
    past its limit): then the row is written without the message, and if
    that is refused too, with only the kernel's fields, the result saying
    why."""
    _, why = await ledger.try_append(conn, task_id, "turn.ended", ended)
    if why is None:
        return ended
    unrecorded = f"the turn's result: {ledger.UNSTORABLE}: {why}"
    result = ended["result"]
    kept = {
        **ended,
        "result": {**{k: result[k] for k in RESULT_FIELDS if k in result}, "unrecorded": unrecorded},
    }
    _, again = await ledger.try_append(conn, task_id, "turn.ended", kept)
    if again is None:
        return kept
    bare = {**{k: ended[k] for k in KERNEL_FIELDS if k in ended}, "result": {"unrecorded": unrecorded}}
    await ledger.append(conn, task_id, "turn.ended", bare)
    return bare


async def _stop_heard(listener, task_id: str) -> None:
    async for note in listener.notifies():
        if note.payload == task_id:
            return


async def _pump(pipe: asyncio.StreamReader, file) -> None:
    """Copy one of the turn's pipes into its file until EOF."""
    while chunk := await pipe.read(1 << 16):
        file.write(chunk)


async def _feed(proc: asyncio.subprocess.Process, data: bytes | None) -> None:
    """Write the turn's standard input and close it. A harness that exits
    without reading all of it ends the write."""
    if data is None:
        return
    try:
        proc.stdin.write(data)
        await proc.stdin.drain()
        proc.stdin.close()
    except BrokenPipeError, ConnectionResetError:
        pass


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
    return {int(row[0]): (row[1] if len(row) > 1 else "") for row in rows}


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
