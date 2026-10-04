"""The ledger: one append-only events table, written by the kernel.

`append` never opens or commits a transaction. The caller's transaction is
the unit of durability, so rows written together land together or not at
all. Readers order by `id`.
"""

import hashlib
import json
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# The ids of the rows appended in this context, when a caller collects them
# (the kernel, around one step of a task).
WRITTEN: ContextVar[set[int] | None] = ContextVar("ledger_written", default=None)


def provenance(by: str, via: str, role_played: bool) -> dict[str, Any]:
    """Who wrote a row Tom (or someone for him) writes: `by`, the surface
    it came `via`, `at`, and `role_played`, true when someone stood in for
    Tom."""
    return {"by": by, "via": via, "role_played": role_played, "at": datetime.now(UTC).isoformat()}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def canonical(value: Any) -> bytes:
    """One byte string per value: sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


async def append(conn, task_id: str, type: str, payload: dict[str, Any]) -> int:
    cur = await conn.execute(
        "INSERT INTO events (task_id, type, payload) VALUES (%s, %s, %s) RETURNING id",
        (task_id, type, Jsonb(payload)),
    )
    row_id = (await cur.fetchone())[0]
    written = WRITTEN.get()
    if written is not None:
        written.add(row_id)
    return row_id


async def read(conn, task_id: str) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, task_id, type, payload, at FROM events WHERE task_id = %s ORDER BY id",
            (task_id,),
        )
        return await cur.fetchall()


async def lock(conn, key: str) -> None:
    """A transaction-scoped advisory lock. Needs no table privilege, so the
    kernel role keeps its insert-and-select grant."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))


def render(rows: list[dict[str, Any]]) -> str:
    """The ledger as text, one line per row, each payload whole."""
    lines = []
    for row in rows:
        payload = json.dumps(row["payload"], sort_keys=True)
        lines.append(f"{row['id']:>5}  {row['type']:<18} {payload}")
    return "\n".join(lines)
