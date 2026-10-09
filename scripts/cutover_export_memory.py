"""Read every old Memory record from the old Redis and write JSON lines.

Read only: SCAN, TYPE, HGETALL. Run with the old system's interpreter:
  PYTHONDONTWRITEBYTECODE=1 ~/src/ai/.venv/bin/python -I scripts/cutover_export_memory.py OUT.jsonl
"""

import json
import sys

import msgpack
import redis

DERIVED = {b"embedding", b"bm25", b"bloom"}


def plain(v):
    try:
        return msgpack.unpackb(v, raw=False)
    except msgpack.exceptions.ExtraData, msgpack.exceptions.FormatError, ValueError:
        return v.decode("utf-8", "replace")


r = redis.Redis(host="127.0.0.1", port=6379)
count = 0
with open(sys.argv[1], "w") as out:
    for key in r.scan_iter(match=b"Memory:*", count=2000):
        if r.type(key) != b"hash":
            continue
        h = r.hgetall(key)
        if b"content" not in h:
            continue
        rec = {k.decode(): plain(v) for k, v in h.items() if k not in DERIVED}
        rec["_key"] = key.decode("utf-8", "replace")
        out.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
        count += 1
print(count, "records written to", sys.argv[1])
