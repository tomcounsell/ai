"""Tom's corrections: rows in the ledger, rendered into every turn.

Serves the constraint "Reliable stop, recovery, and correction": corrections
are first-class, carry provenance, and reach every session and agent. This is
the corrections ledger the plan's Evidence names, not a check or a gate:
nothing here refuses anything.

A correction is one `correction.recorded` event on the `corrections` stream,
numbered from one in the order Tom gave them. Like every ledger row it is
never changed. Nothing reads a copy: each turn renders the corrections in
force from the ledger when it starts, so a correction recorded now reaches
the next turn of every task, including tasks started before it.

`scope` is `global` for now; a narrower scope is a new value here and a
filter in `in_force`. `source_class` is `direct` for a correction Tom gave;
`exemplar` names the exemplar ledger, which shares this store. Withdrawing or
superseding a correction is not built yet.
"""

from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row

from core import ledger

STREAM = "corrections"
SCOPES = ("global",)
SOURCE_CLASSES = ("direct", "exemplar")


async def record(
    conn,
    text: str,
    *,
    by: str,
    via: str,
    scope: str = "global",
    source_class: str = "direct",
) -> dict[str, Any]:
    """Append the next numbered correction. Returns its ledger id and payload."""
    text = text.strip()
    if not text:
        raise ValueError("a correction has text")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if source_class not in SOURCE_CLASSES:
        raise ValueError(f"unknown source class {source_class!r}")
    async with conn.transaction():
        await ledger.lock(conn, STREAM)
        row = await (
            await conn.execute(
                "SELECT COALESCE(max((payload->>'number')::int), 0) FROM events "
                "WHERE task_id = %s AND type = 'correction.recorded'",
                (STREAM,),
            )
        ).fetchone()
        payload = {
            "number": row[0] + 1,
            "scope": scope,
            "source_class": source_class,
            "text": text,
            "provenance": {"by": by, "via": via, "at": datetime.now(UTC).isoformat()},
        }
        event_id = await ledger.append(conn, STREAM, "correction.recorded", payload)
    return {"event_id": event_id, **payload}


async def in_force(conn) -> list[dict[str, Any]]:
    """Every correction that applies to a turn, in number order. With only
    the global scope, that is all of them."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, payload FROM events WHERE task_id = %s AND type = 'correction.recorded' "
            "ORDER BY (payload->>'number')::int",
            (STREAM,),
        )
        return [{"event_id": row["id"], **row["payload"]} for row in await cur.fetchall()]


def render(corrections: list[dict[str, Any]]) -> str:
    """The corrections as the text a turn reads."""
    if not corrections:
        return "# Corrections from Tom\n\nNone recorded."
    parts = ["# Corrections from Tom\n\nIn force for this turn, in the order Tom gave them."]
    for c in corrections:
        p = c["provenance"]
        parts.append(
            f"{c['number']}. ({c['scope']}, {c['source_class']}; {p['by']}, {p['at'][:10]}, via {p['via']})\n"
            f"{c['text']}"
        )
    return "\n\n".join(parts)
