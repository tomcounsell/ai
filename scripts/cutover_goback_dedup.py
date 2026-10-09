"""GO-BACK ONLY. Write the messages the new system handled into the old dedup.

This is the one step in the cutover runbook that writes the old Redis. At go-back
the old system is being restored, and its own functions write its own record:
`bridge.dedup.record_message_processed` and `record_last_processed`. Without it
the old bridge's catch-up would dispatch every message the new system already
answered. The old functions swallow a Redis failure, so the script reads each id
back and exits 1 when one is missing.

Reads lines `chat_id TAB message_id TAB sent_at` on stdin (oldest first), made by
a read-only query on the new ledger. Run with the old system's interpreter:
  psql ... | PYTHONDONTWRITEBYTECODE=1 ~/src/ai/.venv/bin/python -I scripts/cutover_goback_dedup.py
"""

import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.expanduser("~/src/ai"))

from bridge.dedup import is_duplicate_message, record_last_processed, record_message_processed


async def main() -> int:
    count = 0
    ids = []
    for line in sys.stdin:
        parts = line.rstrip("\n").split("\t")
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        chat, mid, sent_at = parts
        await record_message_processed(chat, int(mid))
        await record_last_processed(chat, int(mid), datetime.fromisoformat(sent_at))
        ids.append((chat, int(mid)))
        count += 1
    # The old functions swallow a Redis failure, so read each id back.
    missing = [i for i in ids if not await is_duplicate_message(*i)]
    print(f"recorded {count} messages, {count - len(missing)} read back")
    return 1 if missing else 0


raise SystemExit(asyncio.run(main()))
