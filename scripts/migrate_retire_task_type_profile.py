#!/usr/bin/env python3
"""Retire the TaskTypeProfile keyspace after the model was deleted (#3177).

``models/task_type_profile.py`` is gone. The aggregate it maintained,
``rework_rate``, was structurally always zero: its input field
``AgentSession.rework_triggered`` had no production writer, so the recommendation
derived from it was derived from nothing, and its sole reader had no callers.
The whole module went rather than three fields, because
``delegation_recommendation`` was an ``IndexedField`` whose only reader was the
dead recommendation function — removing the reader while keeping the model would
have stranded a live indexed field.

Deleting the class does not delete its data. Left behind in Redis are the
``TaskTypeProfile:*`` hashes and the ``$IndexF:TaskTypeProfile:delegation_recommendation``
index sets, which no surviving code can reach through the ORM once the class is
gone. This script re-declares a minimal stub so the ORM can reach them one last
time and delete them properly, index membership included.

**Why a stub works.** Popoto keys by class name with no override available
(``popoto/models/base.py``), so a class named ``TaskTypeProfile`` with the same
key fields resolves to exactly the same keyspace. The stub carries only what the
key structure and the index need; the retired aggregate fields are irrelevant to
deletion and are deliberately absent.

**Why the ORM and not raw Redis.** ``instance.delete()`` removes the hash *and*
its index-set membership. A raw ``DEL`` over the hash keys would leave the index
sets pointing at keys that no longer exist, which is a worse state than the one
this script is cleaning up — and it would violate the repo's
no-raw-Redis-on-Popoto-keys rule besides.

Idempotent: a second run enumerates zero rows and exits 0. Retryable: a run
in which any row failed to delete exits non-zero, so ``run_pending_migrations``
leaves the migration unmarked and the next ``/update`` runs it again.

Residue bound if this migration never runs on some machine: the retired model
declared ``Meta.ttl = 7776000`` (90 days), so every hash self-expires within 90
days of its last write, and no reader survives to see it in the meantime.

Usage:
  python scripts/migrate_retire_task_type_profile.py            # dry-run (default)
  python scripts/migrate_retire_task_type_profile.py --apply    # commit deletions
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from popoto import AutoKeyField, IndexedField, KeyField, Model, SortedField  # noqa: E402

# stream=sys.stdout is load-bearing: scripts/update/migrations.py captures this
# script's streams into logs/update.log, and Python's default StreamHandler
# writes to stderr. Keeping the record on stdout makes "did it delete anything?"
# answerable from the log rather than by forensics.
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)


class TaskTypeProfile(Model):
    """Minimal stub resolving to the retired model's keyspace.

    The field set is the key structure plus **every field that owns a Redis key
    outside the row hash**, because ``Model.delete()`` de-indexes by iterating
    ``_meta.fields`` and firing each field's ``on_delete`` hook. A field the stub
    does not declare gets no hook, so its key survives the delete with nothing
    left that can ever reach it. Two such fields exist:

    - ``delegation_recommendation`` (``IndexedField``) — its ``$IndexF`` set.
    - ``last_updated`` (``SortedField(type=float, partition_by="project_key")``)
      — its sorted set. Redis cannot expire a single zset member, so an orphaned
      member outlives the 90-day hash TTL forever. This is the one field whose
      omission produces exactly the "index sets pointing at keys that no longer
      exist" state the module docstring above argues against.

    The purely scalar aggregates (``session_count``, ``avg_turns``,
    ``rework_rate``, ``failure_stage_distribution``) live inside the row hash and
    are removed with it, so they stay absent: popoto ignores unknown hash fields
    on load, and re-declaring them would invite a reader to mistake this stub for
    a revival of the model.
    """

    id = AutoKeyField()
    project_key = KeyField()
    task_type = KeyField()
    delegation_recommendation = IndexedField(default="structured")
    last_updated = SortedField(type=float, partition_by="project_key")


def retire(apply: bool = False, rows: list | None = None) -> int:
    """Delete every TaskTypeProfile row. Returns the number of rows handled.

    ``rows`` lets the caller enumerate once and compare the handled count with
    the row count, which is what makes a partial pass exit non-zero instead of
    being recorded as permanently complete.
    """
    if rows is None:
        try:
            rows = list(TaskTypeProfile.query.filter())
        except Exception as exc:  # noqa: BLE001
            logger.error("TaskTypeProfile enumeration failed: %s", exc)
            raise

    if not rows:
        logger.info("TaskTypeProfile keyspace is already empty; nothing to retire.")
        return 0

    if not apply:
        logger.info(
            "DRY RUN: %d TaskTypeProfile row(s) would be deleted. Re-run with --apply.",
            len(rows),
        )
        return len(rows)

    deleted = 0
    for row in rows:
        try:
            row.delete()
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            # One wedged row must not strand the rest — but the shortfall is
            # reported to the caller, which turns it into a non-zero exit.
            logger.warning("TaskTypeProfile row delete failed: %s", exc)
    logger.info("Deleted %d of %d TaskTypeProfile row(s).", deleted, len(rows))
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the deletions. Without it the script only reports what it would do.",
    )
    args = parser.parse_args()
    try:
        rows = list(TaskTypeProfile.query.filter())
    except Exception as exc:  # noqa: BLE001
        logger.error("TaskTypeProfile enumeration failed: %s", exc)
        return 1
    try:
        handled = retire(apply=args.apply, rows=rows)
    except Exception:
        return 1
    if args.apply and handled < len(rows):
        # Exit non-zero so scripts/update/migrations.py returns an error string
        # and run_pending_migrations leaves the name OUT of the completed set.
        # A run that deleted nothing must be retried, not recorded as done:
        # once a migration name is marked complete it never runs again, and the
        # class this stub stands in for no longer exists to be re-declared.
        logger.error(
            "Retired %d of %d TaskTypeProfile row(s); leaving the migration "
            "unmarked so the next /update retries it.",
            handled,
            len(rows),
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
