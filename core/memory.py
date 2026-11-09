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
  serves, once per old `memory_id`, and a later row for a `memory_id`
  already imported here, served or not, when the line brings another
  date, project or client.
- `bench(questions, dumps, out, scratch)` scores recall on a private set of
  dated questions against the stores in `dumps` (`memory/bench.py`), in
  the scratch database `scratch`, never the live store.
- `fact_write(conn, spec, writes)` appends a project's fact, entity,
  link and source rows and takes each at once, so a later write can name
  it; `facts_read(conn, spec, query)` reads them (docs/memory.md, Facts).
  These are the one place memory's port writes the ledger, through a
  turn's gateway (`valor-record`) or the command line, not an effect:
  effects run after the turn, and the writer reads back what it wrote.
- A correction naming a record (`corrections.record(..., record=...)`)
  confines it to its own client.
- `after_turn(dsn)` is `ingest` on a connection of its own, run by
  `run_turn` once the turn slot is released; its failure goes to stderr.

Memory grants nothing and nothing waits on it: a failure never stops a
turn.
"""

import asyncio
import functools
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
# The fact, entity, link and source rows (`memory.facts.TYPES`), written
# on the `memory` stream by `fact_write`.
FACTS = (
    "fact.added",
    "fact.superseded",
    "fact.confirmed",
    "entity.added",
    "entity.aliased",
    "link.added",
    "source.read",
)
TYPES += FACTS
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


# What a later `memory.imported` row of the same `memory_id` may change.
REDATED = ("occurred_at", "dated_by", "project", "client")


async def import_old(conn, lines) -> dict[str, int]:
    """Append a `memory.imported` row on the `memory` stream for each JSON
    line (`text`, `project`, `client`, `by`, `memory_id`, `merged_from`,
    and `occurred_at` and `dated_by` when it was dated) whose project is a
    spec's `repo` on this Mac. A line whose old `memory_id` is already on
    the stream appends a row only when its date, project or client differ
    from that `memory_id`'s latest row, whether or not its project is
    served here: that row moves or removes the record (`memory/ingest.py`).
    Returns the counts appended and skipped."""
    from core.workspace import Spec

    served = {s.repo for s in Spec.here()}
    latest = {
        p["memory_id"]: p
        for (p,) in await (
            await conn.execute(
                "SELECT payload FROM events WHERE task_id = %s AND type = 'memory.imported' ORDER BY id",
                (MARKS,),
            )
        ).fetchall()
    }
    counts = {"appended": 0, "corrected": 0, "already imported": 0, "not served here": 0}
    for line in lines:
        if not line.strip():
            continue
        r = json.loads(line)
        before = latest.get(r["memory_id"])
        if before is not None and all(before.get(k) == r.get(k) for k in REDATED):
            counts["already imported"] += 1
            continue
        if before is None and r["project"] not in served:
            counts["not served here"] += 1
            continue
        keys = ("text", "project", "client", "memory_id", "merged_from", "occurred_at", "dated_by")
        payload = {k: r[k] for k in keys if k in r}
        payload["provenance"] = ledger.provenance(r["by"], "the old system's memory", False)
        await ledger.append(conn, MARKS, "memory.imported", payload)
        latest[r["memory_id"]] = payload
        counts["corrected" if before is not None else "appended"] += 1
    return counts


class Refused(ValueError):
    """A write that cannot be stored as asked; `names` holds what the
    writes before it resolved to."""

    def __init__(self, why: str, names: dict[str, str]):
        super().__init__(why)
        self.names = names


def _labelled(value: Any, names: dict[str, str]) -> Any:
    """`@label` replaced by the name of the earlier write it labels."""
    if isinstance(value, list):
        return [_labelled(v, names) for v in value]
    if isinstance(value, str) and value.startswith("@"):
        if value[1:] not in names:
            raise ValueError(f"no earlier write labelled {value}")
        return names[value[1:]]
    return value


def write_key(spec, write: dict[str, Any]) -> str:
    """A write's own key: the hash of what it asks, in its project, so the
    same write asked again appends nothing."""
    import hashlib

    body = {k: v for k, v in write.items() if k not in ("as", "key")}
    text = json.dumps([spec.repo, spec.client or "", body], sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:32]


async def fact_write(
    conn, spec, writes: list[dict[str, Any]], *, by: str = "valor", via: str = "the command line"
) -> dict[str, str]:
    """Append each write's rows on the `memory` stream for `spec`'s project
    and client, in order, and take each at once (`memory.facts.prepare`
    says what a write may ask). A write carries `key`, else its hash
    (`write_key`): a key already on the stream appends nothing and resolves
    to the same name, so a batch cut short is applied again whole. A write
    labelled `as` is named `@label` by later ones. Returns each write's
    name, by label or position; a write that cannot be stored raises
    `Refused`, the writes before it kept."""
    if not on():
        raise Unavailable("memory is off on this Mac")
    mdsn = dsn_for(conn)
    why = await asyncio.to_thread(unreachable, mdsn)
    if why:
        raise Unavailable(f"the memory store could not be reached ({why})")
    from memory import facts as f
    from memory import ingest as m

    name = ledger_name(conn)
    where = {"project": spec.repo, "client": spec.client or ""}
    names: dict[str, str] = {}
    await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (LOCK,))
    try:
        for n, write in enumerate(writes, 1):
            label = str(write.get("as") or n)
            try:
                asked = {k: _labelled(v, names) for k, v in write.items()}
                key = str(asked.get("key") or write_key(spec, asked))
                row = await (
                    await conn.execute(
                        "SELECT id, type, payload FROM events WHERE task_id = %s AND type = ANY(%s) "
                        "AND payload->>'key' = %s ORDER BY id LIMIT 1",
                        (MARKS, list(FACTS), key),
                    )
                ).fetchone()
                if row is not None:
                    names[label] = f.named(name, row[0], row[1], row[2])
                    continue
                rows, known = await asyncio.to_thread(
                    functools.partial(f.prepare, mdsn, ledger=name, write=asked, **where)
                )
            except ValueError as exc:
                raise Refused(f"write {label}: {exc}", names) from None
            for type_, payload in rows:
                payload = {**payload, **where, "key": key, "provenance": ledger.provenance(by, via, False)}
                row_id = await ledger.append(conn, MARKS, type_, payload)
                unit = await _unit(conn, row_id, {})
                unit["ledger"] = name
                await asyncio.to_thread(m.save, mdsn, unit)
                known = known or f.named(name, row_id, type_, payload)
            names[label] = known or ""
        return names
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK,))


async def facts_read(conn, spec, query: dict[str, Any]) -> dict[str, Any]:
    """`spec`'s project's facts as `memory.facts.read` answers `query`;
    ValueError for what is not in the project."""
    if not on():
        raise Unavailable("memory is off on this Mac")
    mdsn = dsn_for(conn)
    why = await asyncio.to_thread(unreachable, mdsn)
    if why:
        raise Unavailable(f"the memory store could not be reached ({why})")
    from memory import facts as f

    name = ledger_name(conn)
    return await asyncio.to_thread(
        lambda: f.read(mdsn, ledger=name, project=spec.repo, client=spec.client or "", query=query)
    )


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
    more: dict[str, Any] = {}
    if type_ in FACTS:
        project, client = payload["project"], payload["client"]
    if type_ == "memory.imported":
        from core.workspace import Spec

        project, client = payload["project"], payload["client"]
        first = await (
            await conn.execute(
                "SELECT id, payload->>'project' FROM events WHERE task_id = %s AND type = 'memory.imported' "
                "AND payload->>'memory_id' = %s ORDER BY id LIMIT 1",
                (MARKS, payload["memory_id"]),
            )
        ).fetchone()
        if first[0] != ledger_id:
            served = first[1] == project or project in {s.repo for s in Spec.here()}
            more = {"first": first[0], "served": served}
    return {
        **more,
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


def bench(questions: str, dumps: list[str], out: str, scratch: str) -> dict[str, Any]:
    """Score recall on the questions in the JSON file `questions` against
    the memory stores in `dumps` (`pg_dump -Fc -n memory` files), loaded
    into the database `scratch` (its schema `memory` is replaced; never
    give it the live store). Each dump is first restored into a database of
    its own beside `scratch`, read, and dropped. Writes `<out>/<question
    set>-<date>.json`, naming the question set and its sha256, and beside it
    the same records and questions as popoto's `dated` benchmark fixture,
    and returns the scores by type (docs/plans/memory-measure.md)."""
    import hashlib
    import subprocess
    from datetime import UTC, datetime
    from pathlib import Path

    import psycopg
    from psycopg.conninfo import make_conninfo

    from memory import bench as m
    from memory.records import SCHEMA, reset

    raw = Path(questions).read_bytes()
    qset = json.loads(raw)
    name = conninfo_to_dict(scratch)["dbname"]
    stores = []
    reset()
    try:
        with psycopg.connect(scratch, autocommit=True) as conn:
            for i, dump in enumerate(dumps):
                db_ = f"{name}_dump{i}"
                conn.execute(f'DROP DATABASE IF EXISTS "{db_}"')
                conn.execute(f'CREATE DATABASE "{db_}"')
                try:
                    one = make_conninfo(scratch, dbname=db_)
                    subprocess.run(
                        [
                            str(Path(settings.pg_bin) / "pg_restore"),
                            "--no-owner",
                            "--no-privileges",
                            "-d",
                            one,
                            dump,
                        ],
                        check=True,
                        capture_output=True,
                    )
                    stores.append(m.read(one))
                finally:
                    reset()
                    conn.execute(f'DROP DATABASE IF EXISTS "{db_}"')
            conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            conn.execute(f"CREATE SCHEMA {SCHEMA}")
        times = m.load(scratch, stores)
        result = m.score(scratch, times, qset["questions"])
    finally:
        reset()
    result |= {
        "question_set": qset.get("version", Path(questions).stem),
        "question_set_sha256": hashlib.sha256(raw).hexdigest(),
        "records": len(times),
        "undated_records": sum(t is None for t in times),
        "run_at": datetime.now(UTC).isoformat(),
    }
    Path(out).mkdir(parents=True, exist_ok=True)
    path = Path(out) / f"{result['question_set']}-{datetime.now(UTC).date()}.json"
    path.write_text(json.dumps(result, indent=1))
    path.with_name(f"{path.stem}-popoto-fixture.json").write_text(json.dumps(m.fixture(stores, qset)))
    return result["by_type"]
