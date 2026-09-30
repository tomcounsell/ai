#!/usr/bin/env python3
"""One-shot migration: re-tag Memory records to the canonical 'valor' project_key.

Issue #1171 unified the canonical project_key across the recovery and
AgentSession-writer subsystems on ``valor``. The memory subsystem already
honored ``VALOR_PROJECT_KEY``, but historical writes that fell through to
in-code defaults left the production memory store split across three
namespaces:

  - ``valor``:   correct (writes from sessions where cwd or env resolved properly)
  - ``default``: writes from SDK-spawned sessions with no env override and no
                 cwd hint (``config/memory_defaults.py:DEFAULT_PROJECT_KEY``)
  - ``dm``:      legacy writes from Claude Code hooks between 2026-03-24 and
                 2026-04-07 (per #811). Genuine Telegram-DM records (source=human
                 AND agent_id=dm) are kept under ``dm`` — only mislabeled
                 hook-source records are migrated.

With ``VALOR_PROJECT_KEY=valor`` baked into the worker plist (issue #1171),
recall queries now see only ``valor:*`` records — leaving the historical
``default``- and (mislabeled) ``dm``-tagged records unreachable. This script
re-tags those records via the Popoto-supported KeyField migration path
(``save(migrate_key=True)``), which atomically rewrites the storage key,
deletes the old record, and rebuilds all indexes. NO raw Redis DEL/HSET on
Popoto-managed keys (per CLAUDE.md ``feedback_never_raw_delete_popoto.md``).

Usage:
    python scripts/migrate_memory_project_key.py --dry-run   # default — preview only
    python scripts/migrate_memory_project_key.py --apply     # actually migrate

Run BEFORE the worker restart in the deploy task so post-restart recall
queries find the migrated records (otherwise users see ~45% memory
regression for the duration of the migration window).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Project keys we re-tag to ``valor``.
# - ``default`` is purely mislabeled (writers without env/cwd hint).
# - ``dm`` requires the genuine-DM check (source=human AND agent_id=dm) — those
#   records stay under ``dm`` to preserve Telegram-DM semantics.
OBSOLETE_PROJECT_KEYS = ("default", "dm")
TARGET_PROJECT_KEY = "valor"

# A genuine Telegram-DM Memory record has these markers; do NOT migrate.
SOURCE_HUMAN = "human"
DM_AGENT_ID = "dm"


def _is_genuine_dm(memory) -> bool:
    """A Memory record is a genuine Telegram-DM if source=human AND agent_id=dm."""
    return (memory.source == SOURCE_HUMAN) and (memory.agent_id == DM_AGENT_ID)


def _count_by_project_key() -> Counter:
    from models.memory import Memory

    counts: Counter = Counter()
    for m in Memory.query.all():
        counts[m.project_key or "(none)"] += 1
    return counts


def migrate(dry_run: bool = True) -> dict:
    """Re-tag obsolete project_key Memory records to ``valor``.

    Args:
        dry_run: If True (default), prints planned changes without writing.

    Returns:
        Stats dict with per-key migrated counts and totals.
    """
    from models.memory import Memory

    stats = {
        "scanned": 0,
        "migrated": 0,
        "kept_genuine_dm": 0,
        "errors": 0,
        "by_project_key": {},
    }

    before = _count_by_project_key()
    logger.info("=" * 60)
    logger.info("BEFORE migration:")
    for pk in sorted(before):
        logger.info(f"  {pk!r}: {before[pk]}")
    logger.info("=" * 60)

    for old_pk in OBSOLETE_PROJECT_KEYS:
        records = list(Memory.query.filter(project_key=old_pk))
        stats["scanned"] += len(records)
        stats["by_project_key"][old_pk] = {"records": len(records), "migrated": 0, "kept": 0}

        if not records:
            logger.info(f"  {old_pk!r}: 0 records — skipping")
            continue

        logger.info(f"  {old_pk!r}: {len(records)} record(s) to evaluate")

        for m in records:
            try:
                # Genuine Telegram-DM records keep their dm tag (only relevant
                # for the dm bucket; default never holds genuine-DM records).
                if old_pk == "dm" and _is_genuine_dm(m):
                    logger.debug(f"    KEEP (genuine DM): id={m.memory_id} agent_id={m.agent_id}")
                    stats["kept_genuine_dm"] += 1
                    stats["by_project_key"][old_pk]["kept"] += 1
                    continue

                if dry_run:
                    logger.info(
                        f"    [DRY RUN] would migrate: id={m.memory_id} "
                        f"agent_id={m.agent_id} source={m.source}"
                    )
                else:
                    m.project_key = TARGET_PROJECT_KEY
                    # save(migrate_key=True) is Popoto's supported path for
                    # KeyField transitions: atomically rewrites the storage
                    # key, deletes the old record, rebuilds all indexes.
                    m.save(migrate_key=True)

                stats["migrated"] += 1
                stats["by_project_key"][old_pk]["migrated"] += 1
            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    f"    [ERROR] failed to migrate id={getattr(m, 'memory_id', '?')}: {e}"
                )

        logger.info(
            f"    {old_pk!r}: migrated={stats['by_project_key'][old_pk]['migrated']} "
            f"kept={stats['by_project_key'][old_pk]['kept']}"
        )

    if not dry_run:
        # Brief pause so any deferred index writes settle before we re-read.
        time.sleep(0.5)
        after = _count_by_project_key()
        logger.info("=" * 60)
        logger.info("AFTER migration:")
        for pk in sorted(after):
            logger.info(f"  {pk!r}: {after[pk]}")
        logger.info("=" * 60)

        # Sanity-check post-condition.
        residual_default = after.get("default", 0)
        residual_dm = after.get("dm", 0)
        if residual_default:
            logger.warning(
                f"{residual_default} record(s) still tagged 'default'. "
                "These should all be migrated; investigate."
            )
        if residual_dm > 0 and stats["kept_genuine_dm"] == 0:
            logger.warning(
                f"{residual_dm} record(s) still tagged 'dm' but kept_genuine_dm=0. "
                "Inspect for unexpected 'dm' records."
            )
        elif residual_dm == stats["kept_genuine_dm"]:
            logger.info(
                f"OK: residual 'dm' count ({residual_dm}) matches expected "
                f"genuine-DM preserved count ({stats['kept_genuine_dm']})."
            )

        if not residual_default:
            logger.info("OK: zero 'default'-tagged Memory records remain.")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview without writing (default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the migration.",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info(
        "=== Memory project_key Migration: %s -> %r (%s) ===",
        OBSOLETE_PROJECT_KEYS,
        TARGET_PROJECT_KEY,
        mode,
    )
    if dry_run:
        logger.info("Run with --apply to execute the migration.")

    stats = migrate(dry_run=dry_run)

    logger.info("=== Migration Results ===")
    logger.info(f"  scanned:         {stats['scanned']}")
    logger.info(f"  migrated:        {stats['migrated']}")
    logger.info(f"  kept (genuine):  {stats['kept_genuine_dm']}")
    logger.info(f"  errors:          {stats['errors']}")
    for pk, sub in stats["by_project_key"].items():
        logger.info(
            f"    {pk!r}: scanned={sub['records']} migrated={sub['migrated']} kept={sub['kept']}"
        )

    if stats["errors"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
