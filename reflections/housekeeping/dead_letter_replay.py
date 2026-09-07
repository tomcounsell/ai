"""reflections/housekeeping/dead_letter_replay.py — Replay and age out dead letters.

What it does: walks every dead-letter stage that has a replay handler
    (bridge.dead_letters.HANDLERS), hands each replayable row to it, then
    trims each stage back to DEAD_LETTER_STAGE_CAP through the eviction index.
    The telegram_send stage is deliberately skipped here: its replay needs a
    live Telethon client, which the bridge connect sequence supplies.
Cadence: 300s (replay is recovery, not delivery; the bridge's own eager pass
    covers the restart case)
Failure modes:
    - A handler raises -> caught per row, attempts incremented, replayable
      flipped off after MAX_REPLAY_ATTEMPTS; the row then ages out on its TTL
    - Redis unreachable -> the whole tick reports an error and retries in 300s
Related reflections:
    - side_effect_drain: the producer of the extraction stage's rows
See also: config/reflections.yaml (declaration), docs/features/pipeline-dead-letters.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger("reflections.housekeeping")


async def run() -> dict:
    """Replay the replayable dead letters and evict per-stage overflow."""
    try:
        import asyncio

        from bridge import dead_letters

        replayed: dict[str, int] = {}
        for stage in sorted(dead_letters.HANDLERS):
            count = await dead_letters.replay_stage(stage)
            if count:
                replayed[stage] = count

        evicted = await asyncio.to_thread(dead_letters.evict_overflow)

        total = sum(replayed.values())
        summary = f"Dead-letter replay: {total} replayed, {evicted} evicted"
        if replayed:
            summary += " (" + ", ".join(f"{k}={v}" for k, v in sorted(replayed.items())) + ")"
        logger.info(summary)
        return {"summary": summary, "replayed": total, "evicted": evicted}
    except Exception as e:
        logger.error("Dead-letter replay failed: %s", e, exc_info=True)
        return {"summary": f"Dead-letter replay failed: {e}"}
