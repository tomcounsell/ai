"""The port `core/` reads memory through (docs/plans/b1-memory.md).

The only importer of `memory/`, which it imports only when
`settings.memory` is `on`. It reads the ledger as `valor_kernel` and hands
`memory/` plain data and memory's own DSN, never a kernel connection. Every
call into `memory/` runs in a worker thread: popoto's search is
synchronous, and the kernel's event loop carries its stop notices and
bridges.

- `ingest(conn)` takes the ledger rows memory has not taken, found by set
  difference with memory's `Ingested` set, under a session advisory lock.
- `recall(conn, task_id, fresh)` returns the Remembered section, the empty
  string, or `Memory: unavailable: <reason>`.
- `after_turn(dsn)` is `ingest` on a connection of its own, run by
  `run_turn` once the turn slot is released; its failure goes to stderr.

Memory grants nothing and nothing waits on it: a failure never stops a
turn.
"""

import asyncio
import sys
from typing import Any

from core import db, transcripts
from core.settings import settings

# The rows memory takes (`memory.ingest.ORIGINS`).
TYPES = ("task.started", "question.answered", "feedback.given", "correction.recorded", "turn.ended")
CORRECTIONS = "corrections"
LOCK = "memory:ingest"


def on() -> bool:
    return settings.memory == "on"


def dsn_for(conn) -> str:
    """Memory's DSN for the database `conn` is connected to."""
    info = conn.info
    passfile = info.get_parameters().get("passfile")
    return settings.dsn(memory=True, database=info.dbname, host=info.host, port=info.port, passfile=passfile)


def _reason(exc: BaseException) -> str:
    return " ".join(f"{type(exc).__name__}: {exc}".split())


async def recall(conn, task_id: str, fresh: str | None) -> str:
    """The Remembered section for the task's next turn: none for a fresh
    session, with memory off, or for a task with no project spec."""
    if fresh or not on():
        return ""
    try:
        from core import tasks

        b = await tasks.brief(conn, task_id)
        project = (b.project or {}).get("repo")
        row = await (
            await conn.execute(
                "SELECT id FROM events WHERE task_id = %s AND type = 'task.started' ORDER BY id LIMIT 1",
                (task_id,),
            )
        ).fetchone()
        if not project or row is None:
            return ""
        from memory import recall as m

        return await asyncio.to_thread(m.render, dsn_for(conn), project, row[0], b.instruction)
    except Exception as exc:  # noqa: BLE001  rendered in the Brief; the turn runs
        return f"Memory: unavailable: {_reason(exc)}"


async def ingest(conn) -> dict[str, Any]:
    """Take every row memory has not taken. A task's rows are taken once
    the task has a `turn.started`, so its project is settled (a task
    started by message gets its project when provisioned); a calibration
    task runs no turn and is never taken. Returns the count of rows and
    records taken."""
    if not on():
        return {"memory": "off"}
    from memory import ingest as m

    mdsn = dsn_for(conn)
    await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (LOCK,))
    try:
        done = await asyncio.to_thread(m.taken, mdsn)
        ids = await (
            await conn.execute(
                "SELECT e.id FROM events e WHERE e.type = ANY(%s) AND (e.task_id = %s OR EXISTS ("
                "  SELECT 1 FROM events t WHERE t.task_id = e.task_id AND t.type = 'turn.started')) "
                "ORDER BY e.id",
                (list(TYPES), CORRECTIONS),
            )
        ).fetchall()
        projects: dict[str, str] = {}
        rows = records = 0
        for (ledger_id,) in ids:
            if ledger_id in done:
                continue
            unit = await _unit(conn, ledger_id, projects)
            records += await asyncio.to_thread(m.save, mdsn, unit)
            rows += 1
        return {"rows": rows, "records": records}
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK,))


async def _unit(conn, ledger_id: int, projects: dict[str, str]) -> dict[str, Any]:
    task_id, type_, payload = await (
        await conn.execute("SELECT task_id, type, payload FROM events WHERE id = %s", (ledger_id,))
    ).fetchone()
    if task_id not in projects:
        projects[task_id] = await _project(conn, task_id)
    files: dict[str, bytes] = {}
    if type_ == "turn.ended":
        for f in (payload.get("transcript") or {}).get("files", []):
            files[f["name"]] = await transcripts.joined(conn, payload["turn_id"], f["name"])
    return {
        "id": ledger_id,
        "task_id": task_id,
        "type": type_,
        "payload": payload,
        "project": projects[task_id],
        "files": files,
    }


async def _project(conn, task_id: str) -> str:
    if task_id == CORRECTIONS:
        return ""
    from core import tasks

    try:
        b = await tasks.brief(conn, task_id)
    except KeyError:
        return ""
    return (b.project or {}).get("repo") or ""


async def after_turn(dsn: str) -> None:
    """Ingest on a connection of its own; a failure is written to stderr
    and the rows stay untaken until the next ingest."""
    if not on():
        return
    try:
        async with await db.connect(dsn, application_name="valor memory ingest") as conn:
            await ingest(conn)
    except Exception as exc:  # noqa: BLE001  reported; the turn's rows are written
        print(f"memory: ingest failed: {_reason(exc)}", file=sys.stderr)


def reset() -> None:
    """Forget popoto's installed backend, cached tables, and pools, for a
    database that was dropped and made again."""
    if "memory.records" in sys.modules:
        sys.modules["memory.records"].reset()
