"""Turning ledger rows into records.

The kernel's port hands each row over as plain data (a dict, with the
task's project and the bytes of any transcript the row names); nothing here
reads the ledger. A row is one unit: its records are saved, then its id is
marked taken, so a unit cut short is taken again whole and overwrites the
same keys.
"""

import json
from typing import Any

from memory.records import Ingested, Record, use

# The ledger rows memory takes, and the origin each one's records carry.
ORIGINS = {
    "task.started": "instruction",
    "question.answered": "answer",
    "feedback.given": "feedback",
    "correction.recorded": "correction",
    "turn.ended": "transcript",
}


def taken(dsn: str) -> set[int]:
    """The ids of the ledger rows already taken."""
    use(dsn)
    return {int(i.ledger_id) for i in Ingested.query.all()}


def entries(unit: dict[str, Any]) -> list[dict[str, Any]]:
    """The records one row makes: its text and the meta its label is
    rendered from. A `turn.ended` row makes one record per text entry of
    its transcript files, in file-name order."""
    payload = unit["payload"]
    origin = ORIGINS[unit["type"]]
    meta: dict[str, Any] = {"task_id": unit["task_id"]}
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
    made = entries(unit)
    for e in made:
        Record.create(project=unit["project"], ledger_id=unit["id"], **e)
    Ingested.create(ledger_id=str(unit["id"]))
    return len(made)
