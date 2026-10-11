"""The strategy meeting's agenda, held in the ledger (docs/routines.md,
Watched conditions; docs/business.md, items 3 and 7).

An item is an `agenda.item` row on the `agenda` stream: what Valor will raise
at the next strategy meeting with Tom. The `conditions` routine puts one there
when a committed decision's watched condition is met, carrying the decision's
original reasoning, the condition, and what changed. Valor reads the open items
when it books the meeting (`python -m core agenda`); no agenda, no meeting. An
`agenda.raised` row naming an item's id takes it off the agenda once the
meeting has heard it. Nothing here sends Tom anything.
"""

from typing import Any

from psycopg.rows import dict_row

from core import ledger

STREAM = "agenda"


async def put(conn, item: dict[str, Any]) -> int | None:
    """Put `item` on the agenda unless an item with its `key` is already
    there, raised or not: one met condition is raised once. Returns the new
    row's id, or None when the key was already put."""
    async with conn.transaction():
        await ledger.lock(conn, f"agenda:{item['key']}")
        found = await (
            await conn.execute(
                "SELECT id FROM events WHERE task_id = %s AND type = 'agenda.item' AND payload->>'key' = %s",
                (STREAM, item["key"]),
            )
        ).fetchone()
        if found:
            return None
        return await ledger.append(conn, STREAM, "agenda.item", item)


async def find(conn, key: str) -> int | None:
    """The id of the item put under `key`, or None."""
    found = await (
        await conn.execute(
            "SELECT id FROM events WHERE task_id = %s AND type = 'agenda.item' AND payload->>'key' = %s",
            (STREAM, key),
        )
    ).fetchone()
    return found[0] if found else None


async def open_items(conn, business: str | None = None) -> list[dict[str, Any]]:
    """The items no `agenda.raised` row names, in the order they were put:
    `id`, `at`, and the item's fields. `business` keeps one business's."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, type, payload, at FROM events WHERE task_id = %s ORDER BY id",
            (STREAM,),
        )
        rows = await cur.fetchall()
    raised = {i for r in rows if r["type"] == "agenda.raised" for i in r["payload"].get("items") or []}
    return [
        {"id": r["id"], "at": r["at"], **r["payload"]}
        for r in rows
        if r["type"] == "agenda.item"
        and r["id"] not in raised
        and (business is None or r["payload"].get("business") == business)
    ]


async def raised(conn, items: list[int], meeting: str) -> int:
    """Record that `meeting` heard `items`; they leave the agenda."""
    return await ledger.append(conn, STREAM, "agenda.raised", {"items": list(items), "meeting": meeting})
