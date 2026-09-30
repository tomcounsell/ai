"""One turn: a harness subprocess, metered by the gateway, stoppable at any
instant.

The harness port is `TurnCommand`: the argv, environment, and working
directory that run one turn against a given gateway base URL, plus how to
read the result from stdout. `core` never knows which harness it runs.

Stop is lossless because nothing the turn owns lives only in this process.
The `task.stopped` row fences the task in the database; the notification
wakes this runner, which revokes the gateway (cutting any stream), kills the
harness's whole process group, waits for every in-flight call to be charged,
and writes `turn.ended`. A turn's durable state is its ledger rows, and each
of those lands whole or not at all.
"""

import asyncio
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core import db, ledger, tasks
from core.gateway import Gateway


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
    build: Callable[[str], TurnCommand],
    dsn: str | None = None,
) -> dict[str, Any]:
    """Run one turn of the task to its end or its stop. Returns the
    `turn.ended` payload."""
    turn_id = ledger.new_id()
    dsn = dsn or gateway.dsn
    listener = await db.connect(dsn)
    try:
        await listener.execute(f"LISTEN {tasks.STOP_CHANNEL}")
        command = build(gateway.issue(task_id, turn_id))
        async with await db.connect(dsn) as conn, conn.transaction():
            await ledger.lock(conn, f"task:{task_id}")
            if await tasks.is_stopped(conn, task_id):
                gateway.retire(task_id)
                raise tasks.TaskStopped(task_id)
            await ledger.append(
                conn,
                task_id,
                "turn.started",
                {"turn_id": turn_id, "harness": command.harness, "argv": command.argv},
            )
        proc = await asyncio.create_subprocess_exec(
            *command.argv,
            env=command.env,
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
        row = await (
            await conn.execute(
                "SELECT COALESCE(sum((payload->>'usd_micros')::bigint), 0) FROM events "
                "WHERE task_id = %s AND type = 'gateway.charged' "
                "AND payload->>'turn_id' = %s",
                (task_id, turn_id),
            )
        ).fetchone()
        ended["metered_usd_micros"] = int(row[0])
        async with conn.transaction():
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
