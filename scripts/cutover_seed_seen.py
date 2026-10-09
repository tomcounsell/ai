"""Print the newest Telegram message id the old system recorded, per chat.

Read only: SCAN, TYPE, HGET. Run with the old system's interpreter:
  PYTHONDONTWRITEBYTECODE=1 ~/src/ai/.venv/bin/python -I scripts/cutover_seed_seen.py CHAT_ID...
"""

import json
import sys

import msgpack
import redis

r = redis.Redis(host="127.0.0.1", port=6379)
want, top = set(sys.argv[1:]), {}
for key in r.scan_iter(match=b"TelegramMessage:*", count=2000):
    if r.type(key) != b"hash":
        continue
    chat, mid = (
        msgpack.unpackb(v, raw=False) if v else None
        for v in (r.hget(key, "chat_id"), r.hget(key, "message_id"))
    )
    chat = str(chat).lstrip("/")
    if chat in want and mid is not None:
        top[chat] = max(top.get(chat, 0), int(mid))
print(json.dumps(top))
