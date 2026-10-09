"""Close one unsent notice without sending it, with the row the bridge itself writes.

Used when cutting over again: a notice queued while the bridges were down that
is no longer wanted. Writes one `notice.sent` row with an empty `sent` list, so
no bridge yields the notice again. Run from the kernel checkout:
  PYTHONPATH=. .venv/bin/python scripts/cutover_close_notice.py TASK_ID NOTICE_ID
The notice id is the `notice_id` in the `notice.requested` row's payload.
"""

import asyncio
import sys

from core import db, ledger


async def main(task_id: str, notice_id: str) -> None:
    async with await db.connect() as conn:
        await ledger.append(conn, task_id, "notice.sent", {"notice_id": notice_id, "sent": []})
    print(f"closed {notice_id}")


if len(sys.argv) != 3:
    raise SystemExit(__doc__)
asyncio.run(main(sys.argv[1], sys.argv[2]))
