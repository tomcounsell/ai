"""The event store. Seams §3.1; tech stack §3, §10.

Stateless compute over a stateful store: every other kernel component appends
here and folds from here. `append` never opens or commits a transaction; the
caller's transaction is the unit of durability, so an emitter that writes two
events and a ledger row in one turn commits them together or loses them
together (spike 02). Readers order by `id` and never by `occurred_at`, which
is the transaction's start time and so is shared by every event committed in
one transaction.

`read` and `read_for` always upcast, so a caller sees the current shape of
every type. There is no raw read.

Advisory locks (seams §0): `single_flight` takes
`pg_advisory_xact_lock(hashtextextended(key, 0))`, released when the
caller's transaction ends. Keys are namespaced strings: `thread:<conversation
id>`, `objective:<objective id>`, `brief:<brief id>`, `approval:<approval
id>`, `effect:<idempotency key>`. Lock order is thread before objective, and
nothing holding an objective lock takes a thread lock.
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from schemas.events import CURRENT_VERSION, EVENT_TYPES, Event, EventType
from schemas.ids import SpaceId


class UnknownEventType(ValueError):
    """A type outside `EventType`, on append or on a row being read."""


class StaleSchemaVersion(ValueError):
    """An append with a version other than `CURRENT_VERSION[type]`."""


class UnknownSchemaVersion(ValueError):
    """A row whose version has no upcast path to the current version."""


class NoTransaction(RuntimeError):
    """`single_flight` on a connection with no open transaction."""


# (type, from_version) -> a function returning the payload at from_version + 1.
# Empty at M0; each entry is added by the plan that owns the type, when that
# type gains a version.
UPCASTERS: dict[tuple[EventType, int], Callable[[dict[str, Any]], dict[str, Any]]] = {}

_COLUMNS = "id, space_id, type, schema_version, occurred_at, payload"


async def append(
    conn: psycopg.AsyncConnection,
    *,
    space_id: SpaceId,
    type: EventType,
    payload: dict[str, Any],
    schema_version: int = 1,
) -> int:
    """One insert on the caller's connection. Returns the row's id.

    `payload` is what the emitter's `model_dump(mode="json")` produced, so the
    JSON encoder sees only strings, numbers, lists, and dicts. `occurred_at`
    is left to the column default.
    """
    if type not in EVENT_TYPES:
        raise UnknownEventType(f"unknown event type {type!r}")
    if schema_version != CURRENT_VERSION[type]:
        raise StaleSchemaVersion(
            f"{type} is at schema_version {CURRENT_VERSION[type]}, "
            f"append offered {schema_version}"
        )
    cur = await conn.execute(
        "INSERT INTO events (space_id, type, schema_version, payload) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (space_id, type, schema_version, Jsonb(payload)),
    )
    row = await cur.fetchone()
    return int(row[0])


async def read(
    conn: psycopg.AsyncConnection,
    *,
    space_id: SpaceId,
    after: int = 0,
    types: Sequence[EventType] | None = None,
    limit: int = 1000,
) -> list[Event]:
    """Events of one space with id above `after`, in id order, upcast.

    `after` is exclusive; the next page starts at `after=events[-1].id`.
    Works under either role: `kernel_rw` sees every row of the space, and
    `context_ro` sees the space its read token names or nothing.
    """
    sql = f"SELECT {_COLUMNS} FROM events WHERE space_id = %s AND id > %s"
    params: list[Any] = [space_id, after]
    if types is not None:
        sql += " AND type = ANY(%s)"
        params.append(list(types))
    sql += " ORDER BY id LIMIT %s"
    params.append(limit)
    return await _fetch(conn, sql, params)


async def read_for(
    conn: psycopg.AsyncConnection, *, space_id: SpaceId, key: str, value: str
) -> list[Event]:
    """Events of one space whose payload has `key` equal to `value` at the
    top level, in id order, upcast. Containment (`@>`) so the GIN index
    serves it; a caller that needs a nested field reads the type and
    filters in Python."""
    sql = (
        f"SELECT {_COLUMNS} FROM events "
        "WHERE space_id = %s AND payload @> jsonb_build_object(%s::text, %s::text) "
        "ORDER BY id"
    )
    return await _fetch(conn, sql, [space_id, key, value])


async def _fetch(
    conn: psycopg.AsyncConnection, sql: str, params: Sequence[Any]
) -> list[Event]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        rows = await cur.fetchall()
    events = []
    for row in rows:
        # The type check comes before validation: `Event.type` is the
        # Literal, and pydantic's error would otherwise hide the row id.
        if row["type"] not in EVENT_TYPES:
            raise UnknownEventType(
                f"event {row['id']} has unknown type {row['type']!r}"
            )
        events.append(upcast(Event.model_validate(row)))
    return events


@asynccontextmanager
async def single_flight(conn: psycopg.AsyncConnection, key: str) -> AsyncIterator[None]:
    """Hold the advisory lock for `key` until the caller's transaction ends.

    Blocks rather than tries: the second trigger's turn runs after the first
    and re-renders from the store; whether it still has work is decided from
    the fold. A process killed while holding the lock releases it with its
    connection (spike 02), so no unlock code runs here.

    Refuses a connection with no open transaction, because the lock would
    already be gone when this returns: the guard reads the transaction
    status after the lock statement, so an autocommit connection inside
    `async with conn.transaction()` passes and a bare one raises.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS:
        raise NoTransaction(
            f"single_flight({key!r}) needs an open transaction; "
            "the lock was released as the statement ended"
        )
    yield


def upcast(event: Event) -> Event:
    """The event at the current schema_version of its type.

    Walks `UPCASTERS` from the row's version one step at a time; a missing
    step or a version above the current one raises `UnknownSchemaVersion`.
    At M0 every type is at version 1 and this is the identity.
    """
    current = CURRENT_VERSION[event.type]
    version, payload = event.schema_version, event.payload
    if version > current:
        raise UnknownSchemaVersion(
            f"event {event.id} of type {event.type} is at schema_version "
            f"{version}; current is {current}"
        )
    while version < current:
        step = UPCASTERS.get((event.type, version))
        if step is None:
            raise UnknownSchemaVersion(
                f"event {event.id} of type {event.type} is at schema_version "
                f"{version}; no upcaster from {version} to {version + 1} "
                f"on the way to {current}"
            )
        payload = step(payload)
        version += 1
    if version == event.schema_version:
        return event
    return event.model_copy(update={"schema_version": version, "payload": payload})
