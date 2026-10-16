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
filter in `in_force`. `source_class` is `direct` for a correction Tom gave
(`python -m core correct`) and `exemplar` for work Tom loved and why
(`python -m core exemplar`): the exemplar ledger shares this store and its
numbering, and renders in its own section after the corrections. Withdrawing
or superseding either is not built yet.

`record` names a memory record (`<ledger>/<key>`, as its Remembered label
shows) a correction is about: a fact about one client that turned up in
another client's Brief. Memory stops recalling that record outside its
client once it ingests the correction (docs/memory.md).
"""

from pathlib import Path
from typing import Any

from psycopg.rows import dict_row

from core import ledger

STREAM = "corrections"
SCOPES = ("global",)
SOURCE_CLASSES = ("direct", "exemplar")

# Correction 1 is the governance paragraph, which the persona carries in
# `persona/governance.md` of the kernel's own checkout; a fresh `migrate`
# records it from there (`core/db.py`).
GOVERNANCE_SOURCE = Path(__file__).resolve().parent.parent / "persona" / "governance.md"


def governance_paragraph() -> str | None:
    """The governance paragraph from `GOVERNANCE_SOURCE`, or None when the
    file is unreadable or holds no line starting `**Governance`: the persona
    still carries it into every turn, so nothing waits on correction 1."""
    try:
        lines = GOVERNANCE_SOURCE.read_text().splitlines()
    except OSError:
        return None
    return next((line for line in lines if line.startswith("**Governance")), None)


def payload(
    number: int,
    text: str,
    *,
    by: str,
    via: str,
    scope: str = "global",
    source_class: str = "direct",
    role_played: bool = False,
    record: str | None = None,
) -> dict[str, Any]:
    """A `correction.recorded` payload: the one place its shape is built."""
    text = text.strip()
    if not text:
        raise ValueError(f"{'an exemplar' if source_class == 'exemplar' else 'a correction'} has text")
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if source_class not in SOURCE_CLASSES:
        raise ValueError(f"unknown source class {source_class!r}")
    made = {
        "number": number,
        "scope": scope,
        "source_class": source_class,
        "text": text,
        "provenance": ledger.provenance(by, via, role_played),
    }
    if record is not None:
        if "/" not in record or not record.rsplit("/", 1)[1]:
            raise ValueError(f"{record!r} is not a record name (<ledger>/<key>)")
        made["memory_record"] = record
    return made


async def record(
    conn,
    text: str,
    *,
    by: str,
    via: str,
    scope: str = "global",
    source_class: str = "direct",
    record: str | None = None,
) -> dict[str, Any]:
    """Append the next numbered correction. Returns its ledger id and payload."""
    async with conn.transaction():
        await ledger.lock(conn, STREAM)
        row = await (
            await conn.execute(
                "SELECT COALESCE(max((payload->>'number')::int), 0) FROM events "
                "WHERE task_id = %s AND type = 'correction.recorded'",
                (STREAM,),
            )
        ).fetchone()
        recorded = payload(
            row[0] + 1, text, by=by, via=via, scope=scope, source_class=source_class, record=record
        )
        event_id = await ledger.append(conn, STREAM, "correction.recorded", recorded)
    return {"event_id": event_id, **recorded}


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


def _line(c: dict[str, Any]) -> str:
    p = c["provenance"]
    return (
        f"{c['number']}. ({c['scope']}, {c['source_class']}; {p['by']}, {p['at'][:10]}, via {p['via']})\n"
        f"{c['text']}"
    )


def render(corrections: list[dict[str, Any]]) -> str:
    """The corrections as the text a turn reads: direct corrections, then
    exemplars in their own section. With no exemplar the text is exactly the
    corrections section."""
    direct = [c for c in corrections if c["source_class"] != "exemplar"]
    exemplars = [c for c in corrections if c["source_class"] == "exemplar"]
    if direct:
        parts = ["# Corrections from Tom\n\nIn force for this turn, in the order Tom gave them."]
        parts += [_line(c) for c in direct]
    else:
        parts = ["# Corrections from Tom\n\nNone recorded."]
    if exemplars:
        parts.append("# Exemplars from Tom\n\nWork Tom loved, and why, in the order he gave them.")
        parts += [_line(c) for c in exemplars]
    return "\n\n".join(parts)
