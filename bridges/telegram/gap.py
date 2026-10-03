"""Gap fill: receive what the live handler missed.

Telethon drops updates on pts gaps while connected, and nothing arrives
while the bridge is down, so a high-water mark alone loses messages. Each
pass pages back from the newest message in a chat, keeps every id
`intake.recorded` does not list, and stops at a message older than the
pass's floor (and, on the first pass after connecting, at or below the
chat's highest recorded id). The kept messages go through the handler's
own path, oldest first.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from bridges.telegram.wire import Msg, Wire

PAGE = 100  # Telegram returns at most 100 messages per history request


async def missing(
    wire: Wire,
    chat_id: int,
    *,
    floor: datetime,
    stop_id: int | None,
    recorded: Callable[[list[str]], Awaitable[set[str]]],
) -> list[Msg]:
    """Messages above the floor that are not recorded, oldest first."""
    keep: list[Msg] = []
    offset = 0
    while True:
        page = await wire.history(chat_id, offset_id=offset, limit=PAGE)
        if not page:
            break
        batch, done = [], False
        for m in page:
            if m.date < floor and (stop_id is None or m.id <= stop_id):
                done = True
                break
            batch.append(m)
        if batch:
            have = await recorded([str(m.id) for m in batch])
            keep.extend(m for m in batch if str(m.id) not in have)
        if done or len(page) < PAGE:
            break
        oldest = page[-1].id
        if offset and oldest >= offset:  # only strictly older ids page further
            break
        offset = oldest
    keep.reverse()
    return keep
