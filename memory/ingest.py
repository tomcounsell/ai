"""Turning ledger rows into records.

The kernel's port hands each row over as plain data (a dict, with the
ledger it came from, the task's client and project, the row's time, and the
bytes of any transcript the row names); nothing here reads the ledger. A
row is one unit: its records are saved, then its id is marked taken, so a
unit cut short is taken again whole and overwrites the same keys.

A `memory.marked` row makes no record: it marks the record it names a
standard of working. A correction that names a record (`memory_record`)
confines that record to its client.
"""

import json
from typing import Any

from memory.records import Confined, Ingested, Record, Standard, use

# The row that marks a standard of working.
MARKED = "memory.marked"

# The ledger rows memory takes records from, and the origin each one's
# records carry.
ORIGINS = {
    "task.started": "instruction",
    "question.answered": "answer",
    "feedback.given": "feedback",
    "correction.recorded": "correction",
    "turn.ended": "transcript",
}


def taken(dsn: str, ledger: str) -> set[int]:
    """The ids of `ledger`'s rows already taken."""
    use(dsn)
    return {int(i.ledger_id) for i in Ingested.query.filter(ledger=ledger)}


def entries(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """The records one row makes: its text and the meta its label is
    rendered from. A `turn.ended` row makes one record per text entry of
    its transcript files, in file-name order. A NUL character is dropped:
    Postgres text holds none."""
    payload = unit["payload"]
    origin = ORIGINS[unit["type"]]
    meta: dict[str, Any] = {"task_id": unit["task_id"], "ledger_at": float(unit.get("at") or 0)}
    if origin == "transcript":
        meta["turn_id"] = payload.get("turn_id", "")
        texts = [t for name in sorted(unit.get("files", {})) for t in transcript_texts(unit["files"][name])]
    else:
        provenance = payload.get("provenance") or {}
        meta.update(
            by=str(provenance.get("by", "")),
            via=str(provenance.get("via", "")),
            at=str(provenance.get("at", "")),
            role_played=bool(provenance.get("role_played", False)),
        )
        if origin == "correction":
            meta.update(number=payload.get("number"), source_class=payload.get("source_class", ""))
        texts = [payload.get("instruction" if origin == "instruction" else "text") or ""]
    texts = [t.replace("\x00", "") for t in texts]
    return [
        {"key": f"{unit['id']}.{n}", "origin": origin, "text": text, "meta": meta}
        for n, text in enumerate(t for t in texts if t.strip())
    ]


def transcript_texts(data: bytes) -> list[str]:
    """The text of each user and assistant entry of a Claude Code session
    file, but the first user entry that holds text (the turn's prompt):
    text blocks only, never a tool use or a tool result. A line that is not
    an entry is passed over."""
    out: list[str] = []
    prompt_seen = False
    for line in data.decode("utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") not in ("user", "assistant"):
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            continue
        if not text.strip():
            continue
        if entry["type"] == "user" and not prompt_seen:
            prompt_seen = True
            continue
        out.append(text)
    return out


def save(dsn: str, unit: dict[str, Any]) -> int:
    """Save one row's records, then mark the row taken. Returns the number
    of records."""
    use(dsn)
    payload = unit["payload"]
    made = [] if unit["type"] == MARKED else entries(unit)
    for e in made:
        Record.create(
            ledger=unit["ledger"],
            client=unit.get("client") or "",
            project=unit["project"],
            ledger_id=unit["id"],
            **e,
        )
    if unit["type"] == MARKED and payload.get("record"):
        Standard.create(record=str(payload["record"]))
    if unit["type"] == "correction.recorded" and payload.get("memory_record"):
        Confined.create(record=str(payload["memory_record"]))
    Ingested.create(ledger=unit["ledger"], ledger_id=str(unit["id"]))
    return len(made)
