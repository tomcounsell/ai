#!/usr/bin/env python3
"""One-shot reconciliation of orphaned Memory embedding ``.npy`` files.

Walks ``~/.popoto/content/.embeddings/Memory/`` and removes ``.npy`` files
whose name is not in the SHA-256-hashed expected-keep set computed from
the canonical Popoto class set ``$Class:Memory``. ``tmp*.npy`` files are
swept separately (older than ``--tempfile-age`` seconds, default 1 hour).

Defaults are conservative:
- ``--dry-run`` is the default behavior; ``--apply`` is required to delete.
- ``--min-age-seconds`` (default 300) skips files whose mtime is younger
  than the cutoff, protecting in-flight saves from accidental removal.
- A **positive-assertion safety check** asserts the to-delete set has empty
  intersection with the expected-keep set. If a single live filename
  appears in the to-delete set the script refuses to apply with a
  non-zero exit code.
- A **pre-flight regression guard** asserts the canonical class set is
  non-empty. If ``$Class:Memory`` returns empty (e.g., because someone
  copy-pasted a regression that reads the wrong key), the script exits
  non-zero with a "REFUSE: ... data-destruction guard" message — even
  if the upstream helper is somehow wired wrong, this script cannot
  delete every file.

Examples:
    python scripts/embedding_orphan_reconcile.py --dry-run
    python scripts/embedding_orphan_reconcile.py --apply
    python scripts/embedding_orphan_reconcile.py --apply --min-age-seconds 600

See ``docs/plans/memory_embedding_orphan_cleanup.md`` (#1214) for the
full design rationale.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

logger = logging.getLogger("embedding_orphan_reconcile")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")


def _resolve_memory_class():
    """Import the Memory model. Returns the class or exits with an error."""
    try:
        from models.memory import Memory  # noqa: WPS433 — runtime import on purpose

        return Memory
    except Exception as e:  # pragma: no cover — surface clearly to operator
        sys.exit(f"REFUSE: could not import Memory: {e}")


def _classify_orphans(memory_class, min_age_seconds: int):
    """Return (expected_keep, to_delete, recent_skipped, tmp_skipped) sets.

    Uses the shared Popoto helper as the single source of truth for the
    expected-keep set — never inlines the SHA-256 / class-set-key logic.
    """
    from popoto.fields.embedding_field import (
        _TMP_NPY_RE,
        _compute_expected_keep,
        _get_embeddings_dir,
    )

    expected_keep = _compute_expected_keep(memory_class)

    emb_dir = os.path.join(_get_embeddings_dir(), memory_class.__name__)
    if not os.path.isdir(emb_dir):
        return expected_keep, set(), set(), set(), emb_dir

    try:
        disk_files = os.listdir(emb_dir)
    except OSError as e:
        sys.exit(f"REFUSE: cannot list {emb_dir}: {e}")

    now = time.time()
    to_delete: set[str] = set()
    recent_skipped: set[str] = set()
    tmp_skipped: set[str] = set()

    for filename in disk_files:
        if not filename.endswith(".npy"):
            continue
        if _TMP_NPY_RE.match(filename):
            tmp_skipped.add(filename)
            continue
        if filename in expected_keep:
            continue

        path = os.path.join(emb_dir, filename)
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        if (now - mtime) < min_age_seconds:
            recent_skipped.add(filename)
            continue
        to_delete.add(filename)

    return expected_keep, to_delete, recent_skipped, tmp_skipped, emb_dir


def reconcile(*, dry_run: bool, min_age_seconds: int, verbose: bool) -> int:
    """Run the reconcile flow. Returns the number of files actually removed."""
    _setup_logging(verbose)

    memory_cls = _resolve_memory_class()

    # ------------------------------------------------------------------
    # Pre-flight banner: live record count, disk state
    # ------------------------------------------------------------------
    from popoto.redis_db import POPOTO_REDIS_DB

    class_set_key = memory_cls._meta.db_class_set_key.redis_key
    live_count = POPOTO_REDIS_DB.scard(class_set_key)
    logger.info("Class-set key: %s (live records: %d)", class_set_key, live_count)

    expected_keep, to_delete, recent_skipped, tmp_skipped, emb_dir = _classify_orphans(
        memory_cls, min_age_seconds
    )

    if not os.path.isdir(emb_dir):
        logger.info("No embedding directory at %s — nothing to reconcile.", emb_dir)
        return 0

    # ------------------------------------------------------------------
    # Pre-flight regression guard (B-A defense-in-depth)
    # ------------------------------------------------------------------
    if len(expected_keep) == 0:
        sys.exit(
            f"REFUSE: {class_set_key} returned empty — refusing to treat "
            "all .npy files as orphans (data-destruction guard)"
        )

    # ------------------------------------------------------------------
    # Positive-assertion safety check (C5)
    # ------------------------------------------------------------------
    collision = expected_keep & to_delete
    if collision:
        sample = list(collision)[:5]
        sys.exit(f"REFUSE: would delete {len(collision)} live-record files (sample: {sample})")

    # ------------------------------------------------------------------
    # Report counts
    # ------------------------------------------------------------------
    logger.info("Embedding directory: %s", emb_dir)
    logger.info("  Expected to keep:   %d", len(expected_keep))
    logger.info("  To delete:          %d", len(to_delete))
    logger.info("  Recent (mtime<%ds): %d (skipped)", min_age_seconds, len(recent_skipped))
    logger.info("  tmp*.npy:           %d (sweep separately)", len(tmp_skipped))

    if dry_run:
        logger.info("[DRY RUN] No files removed. Re-run with --apply to delete.")
        return 0

    # ------------------------------------------------------------------
    # APPLY: actually remove orphans
    # ------------------------------------------------------------------
    removed = 0
    failed = 0
    for filename in to_delete:
        path = os.path.join(emb_dir, filename)
        try:
            os.unlink(path)
            removed += 1
        except FileNotFoundError:
            # Concurrent delete — converged on the same end state
            pass
        except OSError as e:
            logger.warning("unlink(%s) failed: %s", path, e)
            failed += 1

    logger.info("[APPLIED] Removed %d / %d orphans (%d failures).", removed, len(to_delete), failed)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile orphan Memory embedding .npy files (#1214).",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report orphans without deleting (default).",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually delete orphan files (overrides --dry-run).",
    )
    parser.add_argument(
        "--min-age-seconds",
        type=int,
        default=300,
        help="Skip files newer than this (mtime guard). Default 300 (5min).",
    )
    parser.add_argument(
        "--tempfile-age",
        type=int,
        default=3600,
        help="(Reserved) tmp*.npy sweep cutoff in seconds. Default 3600 (1hr).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output.")

    args = parser.parse_args(argv)
    dry_run = not args.apply

    return reconcile(
        dry_run=dry_run,
        min_age_seconds=args.min_age_seconds,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
