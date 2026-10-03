"""Gap fill: receive what the live handler missed.

Telethon drops updates on pts gaps while connected, and nothing arrives
while the bridge is down. Each pass pages back from the newest message in
a chat to a stop id, keeps every id above it that `intake.recorded` does
not list, and hands them to the handler's own path, oldest first. Message
ids within a Telegram chat only grow, so a message the last pass did not
see has an id above the newest one it did see.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from bridges.telegram.wire import Msg, Wire

PAGE = 100  # Telegram returns at most 100 messages per history request


async def missing(
    wire: Wire,
    chat_id: int,
    *,
    stop_id: int,
    recorded: Callable[[list[str]], Awaitable[set[str]]],
) -> tuple[list[Msg], int]:
    """Messages with ids above `stop_id` that are not recorded, oldest
    first, and the newest id the pass saw."""
    keep: list[Msg] = []
    top = stop_id
    offset = 0
    while True:
        page = await wire.history(chat_id, offset_id=offset, limit=PAGE)
        if not page:
            break
        top = max(top, page[0].id)
        batch, done = [], False
        for m in page:
            if m.id <= stop_id:
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
    return keep, top
