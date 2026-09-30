"""Shared paged history fetch for the message-recovery scanners.

Both recovery scanners -- the startup catchup scan (``bridge/catchup.py``) and
the periodic reconciler (``bridge/reconciler.py``) -- extend their per-chat
cutoff back to the last-dispatched cursor, which can reach days into history.
A single fixed-size ``get_messages()`` call silently truncates that reach: a
chat with more messages in the gap than one page loses every older missed
message and emits nothing (issue #2476 diagnosed this in the reconciler;
issue #2477 found the identical defect still live in catchup).

This module is the single implementation both scanners share, so the fetch
mechanism cannot drift apart between them again. Each scanner supplies its own
page size and per-chat ceiling; the ceiling relationship is pinned by
tests/unit/test_catchup_paging.py and the dedup-ordering invariant by
tests/unit/test_dedup.py.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def fetch_messages_back_to(
    client,
    entity,
    cutoff: datetime,
    chat_title: str,
    *,
    page_size: int,
    max_messages: int,
    scanner: str,
) -> list:
    """Page backwards through a chat's history until ``cutoff`` is crossed.

    Returns messages newest-first, exactly as a single ``get_messages()`` call
    would, so the caller's per-message loop (which breaks on the first message
    older than the cutoff) is unchanged.

    ``max_messages`` is the hard per-chat-per-scan ceiling on recovery depth.
    Truncation is logged at WARNING: a recovery scan that stops short must be
    distinguishable from one that found nothing -- that ambiguity is what let
    the original truncation bug survive unnoticed.

    ``scanner`` names the caller ("catchup" / "reconciler") in log lines.
    """
    collected: list = []
    offset_id = 0  # 0 == "from the newest message"

    while len(collected) < max_messages:
        remaining = max_messages - len(collected)
        current_page_size = min(page_size, remaining)
        batch = await client.get_messages(
            entity,
            limit=current_page_size,
            offset_id=offset_id,
        )
        if not batch:
            return collected
        # A short page means history is exhausted. Checking this BEFORE deciding
        # to page again keeps the common quiet-chat case at exactly one API call
        # per chat per scan -- a speculative extra page is not free.
        exhausted = len(batch) < current_page_size

        # Only accept strictly-older ids. Guards against a page that repeats or
        # overlaps the previous one, which would otherwise loop or double-process.
        fresh = [m for m in batch if offset_id == 0 or m.id < offset_id]
        if not fresh:
            return collected

        collected.extend(fresh)
        offset_id = min(m.id for m in fresh)

        if exhausted:
            return collected

        # The oldest message in this page already predates the cutoff, so the
        # caller's loop will break inside it. No further pages can contribute.
        oldest_date = min((m.date for m in fresh if m.date is not None), default=None)
        if oldest_date is not None and oldest_date < cutoff:
            return collected

    logger.warning(
        "[%s] %s: fetch hit per-chat ceiling max_messages=%d before reaching "
        "cutoff %s — recovery TRUNCATED, messages older than msg_id=%s were not scanned",
        scanner,
        chat_title,
        max_messages,
        cutoff.isoformat(),
        offset_id,
    )
    return collected
