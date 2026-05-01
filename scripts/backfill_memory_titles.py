#!/usr/bin/env python3
"""One-time backfill: populate Memory.title for pre-existing records.

Iterates Memory.query.all() and for any record with an empty title, fires
the async title generator. Idempotent — records that already have a
title are skipped.

Usage:
    python scripts/backfill_memory_titles.py [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="[backfill_memory_titles] %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Memory.title for old records")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of records to backfill (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without invoking the title generator",
    )
    parser.add_argument(
        "--batch-sleep-ms",
        type=int,
        default=50,
        help="Milliseconds to sleep between records (avoid swamping local LLM)",
    )
    args = parser.parse_args()

    from agent.private_tag import strip_private
    from models.memory import Memory
    from tools.memory_search.title_generator import generate_title_async

    try:
        all_records = list(Memory.query.all())
    except Exception as e:
        logger.error(f"Failed to query Memory.query.all(): {e}")
        return 1

    total = len(all_records)
    needs_title = [r for r in all_records if not getattr(r, "title", "")]
    logger.info(
        f"Total records: {total}; "
        f"needing title: {len(needs_title)}; "
        f"already titled: {total - len(needs_title)}"
    )

    if args.dry_run:
        logger.info("Dry run — no title-gen calls dispatched")
        return 0

    target = needs_title[: args.limit] if args.limit else needs_title
    sleep_s = max(0.0, args.batch_sleep_ms / 1000.0)
    dispatched = 0

    for record in target:
        content = getattr(record, "content", "") or ""
        if not content.strip():
            continue
        memory_id = getattr(record, "memory_id", "") or ""
        if not memory_id:
            continue
        try:
            generate_title_async(memory_id, strip_private(content))
            dispatched += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Skipped {memory_id}: {e}")
            continue
        if sleep_s > 0:
            time.sleep(sleep_s)

    logger.info(f"Dispatched {dispatched} async title-gen jobs")
    logger.info("Note: workers run in daemon threads; allow a few seconds for completion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
