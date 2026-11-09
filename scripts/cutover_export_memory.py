"""Read every old Memory record from the old Redis and write JSON lines,
each with the best date the dump holds for it.

Read only: SCAN, TYPE, HGETALL. Run with the old system's interpreter on
the live old Redis at cutover, or later on a dump restored into a
throwaway Redis, never a live one:
  PYTHONDONTWRITEBYTECODE=1 ~/src/ai/.venv/bin/python -I scripts/cutover_export_memory.py OUT.jsonl [PORT...] [--telegram]
The first PORT's (default 6379) Memory records are written; the
TelegramMessage, AgentSession and KnowledgeDocument records of every PORT
date them (`occurred_at`, ISO UTC, and `dated_by`, which source answered;
an upper bound ends "(no later than)"; neither when none did), and a
record matched to a Telegram message gains its `chat_id`. With
`--telegram`, a record the dump cannot date exactly is looked up in the
chat's own history with `valor-telegram read`: an extraction's message by
id, a person's message by its words in the chats the dump holds for its
project.
"""

import json
import re
import subprocess
import sys
from datetime import UTC, datetime

DERIVED = {b"embedding", b"bm25", b"bloom"}
TG = re.compile(r"^extraction-tg_[^_]+_(-?\d+)_(\d+)")
MS = re.compile(r"^extraction-0_(\d{13})")
UUID = re.compile(r"(?:extraction-|tui-)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
ISO = re.compile(r"\b(20\d\d-[01]\d-[0-3]\d)\b")
BOUND = " (no later than)"


def plain(v):
    import msgpack

    try:
        return msgpack.unpackb(v, raw=False)
    except msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError, ValueError:
        return v.decode("utf-8", "replace")


def hashes(r, prefix: bytes):
    for key in r.scan_iter(match=prefix + b":*", count=2000):
        if r.type(key) == b"hash":
            yield key, {k.decode(): plain(v) for k, v in r.hgetall(key).items() if k not in DERIVED}


def epoch(v) -> float | None:
    """Seconds from a float, or from popoto's encoded datetime."""
    if isinstance(v, dict) and v.get("__datetime__"):
        d = datetime.fromisoformat(v["as_encodable"])
        return (d if d.tzinfo else d.replace(tzinfo=UTC)).timestamp()
    try:
        return float(v) if v not in (None, "") else None
    except TypeError, ValueError:
        return None


def text_key(text) -> str:
    return " ".join(str(text or "").split())


def tables(rs) -> dict:
    """What dates a record, from every Redis in `rs`: Telegram messages by
    chat and message id and by text, sessions by id, docs by path."""
    t: dict = {"message": {}, "text": {}, "text_chat": {}, "session": {}, "doc": {}, "chats": {}}

    def keep(table, key, at):
        if key and at and (key not in t[table] or at < t[table][key]):
            t[table][key] = at

    for r in rs:
        for _, m in hashes(r, b"TelegramMessage"):
            at = epoch(m.get("timestamp"))
            keep("message", (str(m.get("chat_id")), str(m.get("message_id"))), at)
            words = text_key(m.get("content"))
            if at and (words not in t["text"] or at < t["text"][words]):
                t["text_chat"][words] = str(m.get("chat_id"))
            keep("text", words, at)
            t["chats"].setdefault(str(m.get("project_key") or ""), set()).add(str(m.get("chat_id")))
        for _, s in hashes(r, b"AgentSession"):
            at = epoch(s.get("created_at")) or epoch(s.get("started_at"))
            for k in ("session_id", "claude_session_uuid", "id"):
                keep("session", str(s.get(k) or ""), at)
        for _, d in hashes(r, b"KnowledgeDocument"):
            keep("doc", str(d.get("file_path") or ""), epoch(d.get("last_modified")))
    return t


def exact(rec: dict, t: dict) -> tuple[float | None, str]:
    """The record's own time, and which source gave it."""
    agent = str(rec.get("agent_id") or "")
    if rec.get("source") == "human" and (at := t["text"].get(text_key(rec.get("content")))):
        rec["chat_id"] = t["text_chat"][text_key(rec.get("content"))]
        return at, "telegram message"
    if m := TG.match(agent):
        rec["chat_id"] = m.group(1)
        if at := t["message"].get((m.group(1), m.group(2))):
            return at, "telegram message"
        if at := t["session"].get(agent.removeprefix("extraction-").split("_cont")[0]):
            return at, "session start"
    if m := MS.match(agent):
        return int(m.group(1)) / 1000, "session id"
    if (m := UUID.match(agent)) and (at := t["session"].get(m.group(1))):
        return at, "session start"
    meta = rec.get("metadata") or {}
    if at := t["doc"].get(str(meta.get("file_path") or "")):
        return at, "document file date"
    found = ISO.findall(
        str(rec.get("reference") or "")
        + json.dumps({k: v for k, v in meta.items() if k != "outcome_history"})
    )
    if found:
        return datetime.fromisoformat(min(found)).replace(tzinfo=UTC).timestamp(), "date in metadata"
    return None, ""


def read(chat: str, *args: str) -> list[dict]:
    """`valor-telegram read CHAT ARGS --json`, empty when it fails."""
    out = subprocess.run(
        ["valor-telegram", "read", chat, *args, "--json"], capture_output=True, text=True, check=False
    )
    try:
        return json.loads(out.stdout) if out.returncode == 0 else []
    except ValueError:
        return []


def history(recs: list[dict], t: dict) -> None:
    """Add to `t` what the chats' own history says of the records the dump
    cannot date: an extraction's message by id, a person's by its words."""
    for r in recs:
        if exact(r, t)[0] is not None:
            continue
        if m := TG.match(str(r.get("agent_id") or "")):
            chat, msg = m.group(1), m.group(2)
            for hit in read(chat, "--before", str(int(msg) + 1), "--limit", "1"):
                if str(hit.get("id")) == msg:
                    t["message"][(chat, msg)] = datetime.fromisoformat(hit["sent_at"]).timestamp()
        elif r.get("source") == "human" and (words := text_key(r.get("content"))):
            for chat in sorted(t["chats"].get(str(r.get("project_key") or ""), ())):
                hits = read(chat, "--search", " ".join(words.split()[:8]), "--limit", "20")
                found = [h for h in hits if text_key(h.get("text")) == words]
                if found:
                    t["text"][words] = min(datetime.fromisoformat(h["sent_at"]).timestamp() for h in found)
                    t["text_chat"][words] = chat
                    break


def date_all(recs: list[dict], t: dict) -> None:
    """Set `occurred_at` and `dated_by` on every record that has a date:
    its own, else an upper bound: the record that superseded it, or its
    first recorded outcome."""
    own = {r["memory_id"]: exact(r, t) for r in recs}
    for r in recs:
        at, by = own[r["memory_id"]]
        if at is None and (later := own.get(str(r.get("superseded_by") or ""), (None, ""))[0]):
            at, by = later, "superseding record" + BOUND
        if at is None:
            ts = [o.get("ts") for o in (r.get("metadata") or {}).get("outcome_history") or [] if o.get("ts")]
            if ts:
                at, by = float(min(ts)), "first outcome" + BOUND
        if at is not None:
            r["occurred_at"] = datetime.fromtimestamp(at, UTC).isoformat()
            r["dated_by"] = by


def main(argv: list[str]) -> None:
    import redis

    out, *ports = [a for a in argv if a != "--telegram"]
    rs = [redis.Redis(host="127.0.0.1", port=int(p)) for p in ports or ["6379"]]
    recs = []
    for key, rec in hashes(rs[0], b"Memory"):
        if "content" in rec:
            rec["_key"] = key.decode("utf-8", "replace")
            recs.append(rec)
    t = tables(rs)
    if "--telegram" in argv:
        history(recs, t)
    date_all(recs, t)
    with open(out, "w") as f:
        f.writelines(json.dumps(r, default=str, ensure_ascii=False) + "\n" for r in recs)
    dated = sum("occurred_at" in r for r in recs)
    print(len(recs), "records written to", out, f"({dated} dated)")


if __name__ == "__main__":
    main(sys.argv[1:])
