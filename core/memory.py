"""The port `core/` reads memory through (docs/plans/b1-memory.md,
docs/memory.md).

The only importer of `memory/`, which it imports only when
`settings.memory` is `on`. It reads the ledger as `valor_kernel` and hands
`memory/` plain data and memory's own DSN, never a kernel connection. Every
call into `memory/` runs in a worker thread: popoto's search is
synchronous, and the kernel's event loop carries its stop notices and
bridges.

The store is `settings.memory_dsn` when set: the Postgres the four Macs
share (docs/machine.md, The shared memory store). Unset, it is the schema
`memory` of the kernel database `conn` is connected to. Either way every
record names its ledger (`<machine>/<database>`), its client, and its
project, so each Mac's kernel ingests its own ledger into a shared store
and a rebuild is every Mac ingesting its own again.

- `ingest(conn)` takes the ledger rows memory has not taken, found by set
  difference with memory's `Ingested` set for this ledger, under a session
  advisory lock.
- `recall(conn, task_id, fresh)` returns the Remembered section, the empty
  string, `Memory: unavailable: <reason>`, or, when the store cannot be
  reached, `Memory: recall skipped: ...`, without waiting for it.
- `search(conn, spec, query, limit, offset)` is a turn's search of the
  same memory recall reads (`valor-record memory`, `core/record.py`).
- `mark(conn, record, by=..., via=...)` records Valor's judgement that a
  record is a standard of working, recalled for any client's task.
- `import_old(conn, lines)` appends one `memory.imported` row per line of
  `scripts/memory_import_prep.py`'s `import.jsonl` whose project this Mac
  serves, once per old `memory_id`.
- A correction naming a record (`corrections.record(..., record=...)`)
  confines it to its own client.
- `after_turn(dsn)` is `ingest` on a connection of its own, run by
  `run_turn` once the turn slot is released; its failure goes to stderr.

Memory grants nothing and nothing waits on it: a failure never stops a
turn.
"""

import asyncio
import json
import socket
import sys
import threading
from typing import Any

from psycopg.conninfo import conninfo_to_dict

from core import db, ledger, transcripts
from core.settings import settings

# The rows memory takes (`memory.ingest.ORIGINS`, and `memory.ingest.MARKED`).
TYPES = (
    "task.started",
    "question.answered",
    "feedback.given",
    "correction.recorded",
    "turn.ended",
    "memory.marked",
    "memory.imported",
)
CORRECTIONS = "corrections"
# The stream Valor's standard-of-working marks, and the old system's
# imported records, are written on.
MARKS = "memory"
STREAMS = (CORRECTIONS, MARKS)
LOCK = "memory:ingest"
# How long the store has to answer before recall goes without it.
REACH_SECONDS = 2.0


def on() -> bool:
    return settings.memory == "on"


def dsn_for(conn) -> str:
    """Memory's DSN: the shared store's when set, else the database `conn`
    is connected to, as memory's role."""
    if settings.memory_dsn:
        return settings.memory_dsn
    info = conn.info
    passfile = info.get_parameters().get("passfile")
    return settings.dsn(memory=True, database=info.dbname, host=info.host, port=info.port, passfile=passfile)


def ledger_name(conn) -> str:
    """The ledger `conn` reads, as memory names it: this machine and the
    kernel database."""
    return f"{settings.machine}/{conn.info.dbname}"


def unreachable(mdsn: str, timeout: float = REACH_SECONDS) -> str | None:
    """Why the store at `mdsn` cannot be reached, or None when its server
    accepts a connection within `timeout` seconds. Only the transport is
    tried: a store that answers but refuses the login is reached, and its
    failure is the recall's own."""
    params = conninfo_to_dict(mdsn)
    host = str(params.get("host") or "/tmp").split(",")[0]
    port = int(str(params.get("port") or "5432").split(",")[0])
    # create_connection's timeout does not cover the name lookup, so the
    # probe runs in a thread the caller stops waiting for.
    found: list[str | None] = []
    probe = threading.Thread(target=lambda: found.append(_probe(host, port, timeout)), daemon=True)
    probe.start()
    probe.join(timeout)
    if not found:
        return f"{host}:{port}: no answer within {timeout:g} s"
    return found[0]


def _probe(host: str, port: int, timeout: float) -> str | None:
    try:
        if host.startswith("/"):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(f"{host}/.s.PGSQL.{port}")
        else:
            socket.create_connection((host, port), timeout=timeout).close()
    except OSError as exc:
        return f"{host}:{port}: {_reason(exc)}"
    return None


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
                "SELECT id, at FROM events WHERE task_id = %s AND type = 'task.started' ORDER BY id LIMIT 1",
                (task_id,),
            )
        ).fetchone()
        if not project or row is None:
            return ""
        mdsn = dsn_for(conn)
        why = await asyncio.to_thread(unreachable, mdsn)
        if why:
            return f"Memory: recall skipped: the store could not be reached ({why})"
        from memory import recall as m

        name = ledger_name(conn)
        return await asyncio.to_thread(
            lambda: m.render(
                mdsn,
                ledger=name,
                project=project,
                client=(b.project or {}).get("client") or "",
                cutoff=row[0],
                started_at=row[1].timestamp(),
                query=b.instruction,
            )
        )
    except Exception as exc:  # noqa: BLE001  rendered in the Brief; the turn runs
        return f"Memory: unavailable: {_reason(exc)}"


class Unavailable(Exception):
    """Memory is off, or its store cannot be reached."""


async def search(conn, spec, query: str, limit: int, offset: int) -> dict[str, Any]:
    """A page of the records recall would choose from for a task of
    `spec`'s project and client, everything ingested so far, best match to
    `query` first (`memory.recall.search`). Reads only."""
    if not on():
        raise Unavailable("memory is off on this Mac")
    mdsn = dsn_for(conn)
    why = await asyncio.to_thread(unreachable, mdsn)
    if why:
        raise Unavailable(f"the memory store could not be reached ({why})")
    from memory import recall as m

    name = ledger_name(conn)
    return await asyncio.to_thread(
        lambda: m.search(
            mdsn,
            ledger=name,
            project=spec.repo,
            client=spec.client or "",
            query=query,
            limit=limit,
            offset=offset,
        )
    )


async def mark(conn, record: str, *, by: str = "valor", via: str = "the command line") -> int:
    """Record that `record` (`<ledger>/<key>`, as its Remembered label
    names it) is a standard of working: from the next ingest on, it is
    recalled for any client's task. No fixed rule decides which records are
    standards; Valor judges each one. Returns the ledger row's id."""
    if "/" not in record or not record.rsplit("/", 1)[1]:
        raise ValueError(f"{record!r} is not a record name (<ledger>/<key>)")
    return await ledger.append(
        conn, MARKS, "memory.marked", {"record": record, "provenance": ledger.provenance(by, via, False)}
    )


async def import_old(conn, lines) -> dict[str, int]:
    """Append a `memory.imported` row on the `memory` stream for each JSON
    line (`text`, `project`, `client`, `by`, `memory_id`, `merged_from`)
    whose project is a spec's `repo` on this Mac. A line whose old
    `memory_id` is already on the stream appends nothing. Returns the
    counts appended and skipped."""
    from core.workspace import Spec

    served = {s.repo for s in Spec.here()}
    done = {
        r[0]
        for r in await (
            await conn.execute(
                "SELECT payload->>'memory_id' FROM events WHERE task_id = %s AND type = 'memory.imported'",
                (MARKS,),
            )
        ).fetchall()
    }
    counts = {"appended": 0, "already imported": 0, "not served here": 0}
    for line in lines:
        if not line.strip():
            continue
        r = json.loads(line)
        if r["project"] not in served:
            counts["not served here"] += 1
        elif r["memory_id"] in done:
            counts["already imported"] += 1
        else:
            payload = {k: r[k] for k in ("text", "project", "client", "memory_id", "merged_from")}
            payload["provenance"] = ledger.provenance(r["by"], "the old system's memory", False)
            await ledger.append(conn, MARKS, "memory.imported", payload)
            done.add(r["memory_id"])
            counts["appended"] += 1
    return counts


async def ingest(conn) -> dict[str, Any]:
    """Take every row memory has not taken. A task's rows are taken once
    the task has a `turn.started`, so its project is settled (a task
    started by message gets its project when provisioned); a calibration
    task runs no turn and is never taken. The corrections and marks streams
    are taken whole. Returns the count of rows and records taken, or why
    the store could not be reached."""
    if not on():
        return {"memory": "off"}
    from memory import ingest as m

    mdsn = dsn_for(conn)
    why = await asyncio.to_thread(unreachable, mdsn)
    if why:
        return {"skipped": f"the store could not be reached ({why})"}
    name = ledger_name(conn)
    await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (LOCK,))
    try:
        done = await asyncio.to_thread(m.taken, mdsn, name)
        ids = await (
            await conn.execute(
                "SELECT e.id FROM events e WHERE e.type = ANY(%s) AND (e.task_id = ANY(%s) OR EXISTS ("
                "  SELECT 1 FROM events t WHERE t.task_id = e.task_id AND t.type = 'turn.started')) "
                "ORDER BY e.id",
                (list(TYPES), list(STREAMS)),
            )
        ).fetchall()
        projects: dict[str, tuple[str, str]] = {}
        rows = records = 0
        for (ledger_id,) in ids:
            if ledger_id in done:
                continue
            unit = await _unit(conn, ledger_id, projects)
            unit["ledger"] = name
            records += await asyncio.to_thread(m.save, mdsn, unit)
            rows += 1
        return {"rows": rows, "records": records}
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK,))


async def _unit(conn, ledger_id: int, projects: dict[str, tuple[str, str]]) -> dict[str, Any]:
    task_id, type_, payload, at = await (
        await conn.execute("SELECT task_id, type, payload, at FROM events WHERE id = %s", (ledger_id,))
    ).fetchone()
    if task_id not in projects:
        projects[task_id] = await _project(conn, task_id)
    files: dict[str, bytes] = {}
    if type_ == "turn.ended":
        for f in (payload.get("transcript") or {}).get("files", []):
            files[f["name"]] = await transcripts.joined(conn, payload["turn_id"], f["name"])
    project, client = projects[task_id]
    if type_ == "memory.imported":
        project, client = payload["project"], payload["client"]
    return {
        "id": ledger_id,
        "task_id": task_id,
        "type": type_,
        "payload": payload,
        "project": project,
        "client": client,
        "at": at.timestamp(),
        "files": files,
    }


async def _project(conn, task_id: str) -> tuple[str, str]:
    """The task's project (its spec's `repo`) and client."""
    if task_id in STREAMS:
        return "", ""
    from core import tasks

    try:
        b = await tasks.brief(conn, task_id)
    except KeyError:
        return "", ""
    spec = b.project or {}
    return spec.get("repo") or "", spec.get("client") or ""


async def after_turn(dsn: str) -> None:
    """Ingest on a connection of its own; a failure, or a store that cannot
    be reached, is written to stderr and the rows stay untaken until the
    next ingest."""
    if not on():
        return
    try:
        async with await db.connect(dsn, application_name="valor memory ingest") as conn:
            done = await ingest(conn)
        if "skipped" in done:
            print(f"memory: ingest skipped: {done['skipped']}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001  reported; the turn's rows are written
        print(f"memory: ingest failed: {_reason(exc)}", file=sys.stderr)


def reset() -> None:
    """Forget popoto's installed backend, cached tables, and pools, for a
    database that was dropped and made again."""
    if "memory.records" in sys.modules:
        sys.modules["memory.records"].reset()
