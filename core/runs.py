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
of those lands whole or not at all. A turn that is cancelled (a rollout or
a shutdown cancels the running job), or that fails in the kernel after its
`turn.started`, kills its harness's group, retires its grant, reaps, and
ends `interrupted` with why.

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
import contextlib
import ctypes
import functools
import os
import platform
import signal
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core import db, git, ledger, machine, memory, slot, spending, tasks, transcripts
from core.settings import settings

if TYPE_CHECKING:
    from core.gateway import Gateway  # gateway -> tasks -> workspace -> runs: a cycle at import

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
    # The turn's TMPDIR: emptied before it starts, removed once it ends.
    tmpdir: str | None = None


async def run_turn(
    gateway: Gateway,
    task_id: str,
    build: Callable[[str, str], TurnCommand],
    dsn: str | None = None,
    state: str | None = None,
    fresh: str | None = None,
    offered: Sequence[str] = (),
    compaction: bool = False,
) -> dict[str, Any]:
    """Run one turn of the task to its end or its stop, in `state` (the
    state machine's state the turn works in, recorded on `turn.started`;
    None for a turn outside the machine). `fresh` names the stage of a fresh
    session (critique, review, docs): its Brief carries the verdict channel,
    and `turn.started` says `fresh: true`, so the fold never resumes its
    session. `offered` is the usage lines of the task's performers, which
    the working session's Brief lists. `compaction` marks a chat session's
    `/compact` turn on `turn.started` (`core/session.py`). Returns the
    `turn.ended` payload.

    The turn holds a turn slot (`core/slot.py`) from before its Brief is
    rendered until `turn.ended` is written, except a clarify turn (a
    question to Tom), which never waits on one. Memory ingests once the slot
    is released, so no other turn waits on it.

    The turn's TMPDIR (`TurnCommand.tmpdir`) is emptied before the turn
    starts, which also clears what a killed kernel or a failed removal
    left, and removed once the slot is released, however the turn ended.
    Nothing in it is kept: a later turn of the session resumes from its
    harness's config directory, never from TMPDIR. A failed removal leaves
    the turn's result as it is."""
    held = contextlib.nullcontext() if state == "clarify" else slot.held(task_id, dsn or gateway.dsn)
    tmpdirs: list[str] = []
    try:
        async with held:
            ended = await _run_turn(gateway, task_id, build, dsn, state, fresh, offered, tmpdirs, compaction)
    finally:
        for tmpdir in tmpdirs:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(_clear, tmpdir)
    await memory.after_turn(dsn or gateway.dsn)
    return ended


def _clear(tmpdir: str, make: bool = False) -> None:
    """Remove the turn's TMPDIR, never following a link, and make it empty
    again when `make`."""
    from core.workspace import rmtree  # core.workspace imports this module

    rmtree(Path(tmpdir))
    if make:
        Path(tmpdir).mkdir(parents=True)


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


async def _run_turn(
    gateway, task_id, build, dsn, state, fresh, offered, tmpdirs, compaction
) -> dict[str, Any]:
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
                        **({"compaction": True} if compaction else {}),
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
        if command.tmpdir:
            tmpdirs.append(command.tmpdir)
        out_path, err_path = output_paths(task_id, turn_id)
        proc = pumped = None
        waiters: list[asyncio.Future] = []
        # From here on the turn has a `turn.started` row and a grant; one
        # handler ends it however it is cut short. The files close after it.
        with contextlib.ExitStack() as files:
            try:
                if command.tmpdir:
                    await asyncio.to_thread(_clear, command.tmpdir, True)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out = files.enter_context(out_path.open("wb"))
                err = files.enter_context(err_path.open("wb"))
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
                finished = asyncio.create_task(proc.wait())
                stopped = asyncio.create_task(_stop_heard(listener, task_id))
                waiters += [finished, stopped]
                done, _ = await asyncio.wait(set(waiters), return_when=asyncio.FIRST_COMPLETED)
                if stopped in done:
                    gateway.revoke(task_id)
                    _kill_group(proc.pid)
                    await finished
                    outcome = "stopped"
                    stopped.cancel()
                else:
                    stopped.cancel()
                    gateway.retire(task_id)
                    gateway.cut(task_id)  # its process has exited: no client is left
                    outcome = "done" if proc.returncode == 0 else "failed"
                await gateway.drain(task_id)
                reaped = await asyncio.to_thread(reap, turn_id, proc.pid)
                # Every process of the turn has ended, so both pipes are at EOF.
                await pumped
            except BaseException as exc:
                await _cut_short(gateway, task_id, turn_id, dsn, proc, pumped, waiters, exc)
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
    transcript = command.transcript
    if transcript is not None and transcript.session_id is None:
        # The harness named the session (Codex): its output says which.
        transcript = replace(transcript, session_id=ended["result"].get("session_id"))
    elif transcript is not None and ended["result"].get("session_id"):
        ended["result"]["session_id"] = transcript.session_id
    async with await db.connect(dsn) as conn:
        if transcript is not None:
            ended.update(await transcripts.copy(conn, task_id, turn_id, transcript))
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


async def _cut_short(gateway, task_id, turn_id, dsn, proc, pumped, waiters, exc: BaseException) -> None:
    """End a turn cancelled, or failed in the kernel, after `turn.started`.

    First, with no await, so a second cancellation cannot skip it: kill the
    harness's group while its leader is unreaped (its pid is the group id;
    once reaped, the number may be another's), retire the grant and cut its
    calls, and drop the waiters. Then reap what left the group, wait for the
    pipes, charge the calls, and write `turn.reaped` and `turn.ended`
    (`interrupted`, with why). A second cancellation before the rows are
    written leaves the turn with no end, which recovery ends at the next
    start; the reap's thread still runs to its end. A failure to write the
    rows is noted on `exc`, which the caller raises."""
    if proc is not None and proc.returncode is None:
        _kill_group(proc.pid)
    gateway.retire(task_id)
    gateway.cut(task_id)
    for waiter in waiters:
        waiter.cancel()
    reaped = await asyncio.to_thread(reap, turn_id, proc.pid if proc is not None else None)
    if proc is not None:
        await proc.wait()
    if pumped is not None and not pumped.done():
        await pumped
    out_path, err_path = output_paths(task_id, turn_id)
    why = "cancelled" if isinstance(exc, asyncio.CancelledError) else f"{type(exc).__name__}: {exc}"
    try:
        await gateway.drain(task_id)
        async with await db.connect(dsn) as conn:
            metered = await spending.turn_spent(conn, task_id, turn_id)
            async with conn.transaction():
                if reaped:
                    await ledger.append(
                        conn, task_id, "turn.reaped", {"turn_id": turn_id, "processes": reaped}
                    )
                await ledger.append(
                    conn,
                    task_id,
                    "turn.ended",
                    {
                        "turn_id": turn_id,
                        "outcome": "interrupted",
                        "reason": why,
                        "returncode": proc.returncode if proc is not None else None,
                        "result": {},
                        "stdout": str(out_path),
                        "stderr": str(err_path),
                        "metered_usd_micros": metered,
                    },
                )
    except Exception as failed:  # noqa: BLE001  the turn's own exception is the one raised; recovery ends it
        exc.add_note(f"turn {turn_id} was not ended: {failed!r}")


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
    is trusted to honor a gentler signal. macOS answers EPERM when the
    group's only member is its leader exiting or a zombie: nothing is left
    to kill, as with ESRCH."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError, PermissionError:
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
    return [pid for pid in own_processes() if denies(pid, name) and not denies(pid, control)]


def commands(pids: list[int]) -> dict[int, str]:
    return {pid: command(pid) for pid in pids}


def marked_services(task_ids: list[str]) -> set[str]:
    """Which of these tasks have a process of this user running under their
    service mark (`valor.service.<task>`), from one process listing."""
    denies = _sandbox_check()
    if not denies or not task_ids:
        return set()
    found: set[str] = set()
    for pid in own_processes():
        if not denies(pid, "valor.service.none"):
            found.update(t for t in task_ids if t not in found and denies(pid, f"valor.service.{t}"))
    return found


def _stop(pids: list[int]) -> list[dict[str, Any]]:
    if not pids:
        return []
    names = commands(pids)
    for pid in pids:
        _signal(pid, signal.SIGTERM)
    deadline = time.monotonic() + settings.reap_grace_s
    left = set(pids)
    while left and time.monotonic() < deadline:
        time.sleep(0.05)
        left = {pid for pid in left if alive(pid)}
    for pid in left:
        _signal(pid, signal.SIGKILL)
    return [
        {"pid": pid, "command": names.get(pid, ""), "signal": "SIGKILL" if pid in left else "SIGTERM"}
        for pid in sorted(pids)
    ]


def _turn_processes(turn_id: str, pgid: int | None) -> list[int]:
    """This user's processes in `pgid`, carrying the turn's mark as an entry
    of their initial environment, or under the turn's sandbox mark."""
    mark = f"{TURN_ENV}={turn_id}"
    return [
        pid
        for pid, group in own_processes().items()
        if group == pgid or mark in _arguments(pid)[1] or _sandbox_marked(pid, turn_id)
    ]


# -- the process table ------------------------------------------------------------
#
# Read in-process, not through `/bin/ps`: it is setuid, and a sandboxed
# caller (a turn, the check's suite) may not exec it. On macOS through
# libproc and sysctl, elsewhere (the verification VM) from `/proc`; for this
# user's processes neither needs a privilege. A process that has exited, a
# zombie included, reads as absent.

_SHORTBSDINFO = 13  # PROC_PIDT_SHORTBSDINFO: struct proc_bsdshortinfo, 64 bytes
_KERN_PROCARGS2 = (1, 49)  # CTL_KERN, KERN_PROCARGS2


@functools.cache
def _libc():
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.proc_listallpids.restype = ctypes.c_int
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    lib.sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p, ctypes.c_size_t,
    ]  # fmt: skip
    lib.sysctl.restype = ctypes.c_int
    return lib


PROC = Path("/proc")
_DARWIN = sys.platform == "darwin"


def _all_pids() -> list[int]:
    if not _DARWIN:
        return [int(n) for n in os.listdir(PROC) if n.isdigit()]
    lib = _libc()
    count = lib.proc_listallpids(None, 0)
    buf = (ctypes.c_int * (count + 64))()
    count = lib.proc_listallpids(buf, ctypes.sizeof(buf))
    return [pid for pid in buf[: max(count, 0)] if pid > 0]


def owner_and_group(pid: int) -> tuple[int, int] | None:
    """The process's uid and process group, or None when it is gone."""
    if not _DARWIN:
        return _proc_owner_and_group(pid)
    buf = ctypes.create_string_buffer(64)
    if _libc().proc_pidinfo(pid, _SHORTBSDINFO, 0, buf, 64) != 64:
        return None
    pgid = int.from_bytes(buf.raw[8:12], "little")  # pbsi_pid, pbsi_ppid, pbsi_pgid
    uid = int.from_bytes(buf.raw[36:40], "little")  # after pbsi_status, pbsi_comm[16], pbsi_flags
    return uid, pgid


def own_processes() -> dict[int, int]:
    """This user's running processes but the caller, each with its process
    group."""
    me, uid = os.getpid(), os.getuid()
    found = {}
    for pid in _all_pids():
        info = owner_and_group(pid) if pid != me else None
        if info and info[0] == uid:
            found[pid] = info[1]
    return found


def alive(pid: int) -> bool:
    """Running: neither gone nor a zombie waiting to be collected."""
    return owner_and_group(pid) is not None


def _arguments(pid: int) -> tuple[list[str], list[str]]:
    """The process's argv and initial environment entries (`KERN_PROCARGS2`:
    argc, the executable path, NUL padding, argv, the environment up to an
    empty entry, then the kernel's own strings), or two empty lists when it
    is gone or unreadable. A platform binary shows no environment to
    another process."""
    if not _DARWIN:
        return _proc_arguments(pid)
    lib = _libc()
    mib = (ctypes.c_int * 3)(*_KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if lib.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value < 4:
        return [], []
    buf = ctypes.create_string_buffer(size.value)
    if lib.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return [], []
    raw = buf.raw[: size.value]
    argc = int.from_bytes(raw[:4], "little")
    at = raw.find(b"\0", 4)
    if at < 0:
        return [], []
    while at < len(raw) and raw[at] == 0:
        at += 1
    strings = []
    while at < len(raw):
        end = raw.find(b"\0", at)
        end = len(raw) if end < 0 else end
        strings.append(raw[at:end].decode(errors="replace"))
        at = end + 1
    argv = strings[:argc]
    env = []
    for entry in strings[argc:]:
        if not entry:
            break
        env.append(entry)
    return argv, env


def _proc_owner_and_group(pid: int) -> tuple[int, int] | None:
    """From `/proc/<pid>/stat`: the fields after the command's closing
    parenthesis (the command may hold one) are state, ppid, pgrp; the owner
    is the directory's. A zombie (`Z`) or a dead task (`X`) is gone."""
    try:
        stat = (PROC / str(pid) / "stat").read_text()
        uid = (PROC / str(pid)).stat().st_uid
    except OSError:
        return None
    fields = stat.rpartition(")")[2].split()
    if len(fields) < 3 or fields[0] in ("Z", "X"):
        return None
    return uid, int(fields[2])


def _proc_arguments(pid: int) -> tuple[list[str], list[str]]:
    def entries(name: str) -> list[str]:
        try:
            raw = (PROC / str(pid) / name).read_bytes()
        except OSError:
            return []
        return [e.decode(errors="replace") for e in raw.split(b"\0") if e]

    return entries("cmdline"), entries("environ")


def command(pid: int) -> str:
    """The process's argv joined by spaces, as `ps -o command` shows it; empty
    when it is gone or unreadable."""
    return " ".join(_arguments(pid)[0])


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
