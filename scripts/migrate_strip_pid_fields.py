#!/usr/bin/env python3
"""Strip removed pid fields (+expectations) from existing AgentSession records.

The durability-room-job-agentrun plan (Milestone 1) replaced the raw pid
fields with a fenced execution record and deleted these hash fields from the
AgentSession model:

    claude_pid, pm_pid, harness_pid, expectations

Popoto ignores unknown hash fields on load, so pre-cutover records remain
fully readable without this migration -- the stale hash entries are orphaned
data, not a crash hazard. This migration reclaims them via **ORM-safe
operations only** (no raw ``hdel``/``hset``): for each terminal record still
carrying a stale field, it queues ``instance.delete()`` + ``Model.save(
instance)`` on ONE transactional Redis pipeline (MULTI/EXEC), so the record is
atomically rewritten with only the current model fields -- a crash
mid-migration can never lose a record.

Safety properties:

- **Idempotent**: re-running finds zero records with stale fields -> no-op.
- **Atomicity, not quiescence**: only records whose ``status`` is in
  ``models.session_lifecycle.TERMINAL_STATUSES`` are rewritten, but terminal
  rows are **not** quiescent.
  ``agent.session_health.cleanup_corrupted_agent_sessions`` re-saves every
  hydrated record -- terminal ones included -- as its "no-op save" corruption
  probe, and ``/update`` invokes it at Step 5.5
  (``scripts/update/run.py:1853-1856``), as does worker startup and the
  ``agent-session-cleanup`` reflection. Because ``AgentSession.save()``
  restamps ``updated_at``, that pass moves every record's timestamp in one
  batch at ``/update`` time. So the safety property here is **not** "nobody
  else writes terminal rows"; it is that the delete + recreate is queued on
  ONE transactional Redis pipeline (MULTI/EXEC), so a crash or an interleaved
  writer can never lose a record. A concurrent write that lands between this
  script's read and its pipeline is lost, which is why the scope stays
  terminal-only: those rows carry no in-flight state worth racing for.
  Non-terminal records are skipped and reported -- they hydrate fine (Popoto
  ignores the stale fields on load). This is the [DESTRUCTIVE] No-Go boundary
  from the plan: rewriting a running session's hash risks clobbering
  concurrent writes, so it is out of scope by design. The base
  ``popoto.Model.save`` is used directly so ``updated_at`` is preserved as
  loaded (the AgentSession override would restamp it and falsify freshness on
  old records).
- **Deferred rows do not age out**: every popoto ``save()`` re-issues
  ``EXPIRE`` with ``Meta.ttl`` (popoto ``base.py:1186-1190``), so the 30-day
  backstop only fires on a record nothing writes for 30 days. Any record that
  keeps being written holds a perpetually-refreshed TTL -- true of
  ``is_ledger=True`` SDLC anchors, which are re-saved continuously while their
  pipeline is open, and true of every record on every tick of the cleanup pass
  above. A deferred row therefore keeps its stale fields until a later run of
  this migration finds it terminal.
- **TTL note**: the atomic rewrite refreshes the record's ``Meta.ttl``
  (30-day backstop) -- acceptable for the one-time migration; stale terminal
  sessions remain subject to the cleanup CLI.

Usage:
  python scripts/migrate_strip_pid_fields.py            # dry-run (default)
  python scripts/migrate_strip_pid_fields.py --apply    # commit changes
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# stream=sys.stdout is load-bearing: Python's default StreamHandler writes to
# stderr, and scripts/update/migrations.py captures this script's streams so
# logs/update.log records what the migration actually did. Keeping the record
# on stdout makes "did it strip anything?" answerable from the log instead of
# by forensics.
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

#: Hash fields removed from the AgentSession model by the durability plan.
STALE_FIELDS = frozenset(
    {
        "claude_pid",
        "pm_pid",
        "harness_pid",
        "expectations",
    }
)


def _raw_field_names(instance) -> set[str]:
    """Field names present in the record's raw hash.

    Detection-only read of hash FIELD NAMES via ``HKEYS`` against the
    ORM-provided key (``instance._redis_key`` / ``db_key``). This reads no
    values, so the binary-field decode hazard that bans raw value reads
    (``hgetall``/``hget`` -- issue #1038) does not apply; Popoto itself
    exposes no ORM API for orphaned-hash-field discovery (its migration
    cookbook prescribes raw access for exactly this). All WRITES in this
    script remain ORM-only (``instance.delete()`` + ``Model.save()``).
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    redis_key = getattr(instance, "_redis_key", None) or instance.db_key.redis_key
    names: set[str] = set()
    try:
        for key in POPOTO_REDIS_DB.hkeys(redis_key):
            names.add(key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key))
    except Exception as e:  # noqa: BLE001 -- detection failure = treat as clean
        logger.warning("hkeys failed for %s: %s", redis_key, e)
    return names


def migrate(apply: bool = False) -> dict:
    """Strip stale pid hash fields from terminal AgentSession records.

    Args:
        apply: If False (default), report what would happen without writing.

    Returns:
        Dict with migration stats.
    """
    import popoto
    from popoto.redis_db import POPOTO_REDIS_DB

    from models.agent_session import AgentSession
    from models.session_lifecycle import TERMINAL_STATUSES

    stats = {
        "total_records": 0,
        "clean": 0,
        "stripped": 0,
        "deferred_non_terminal": 0,
        "errors": 0,
    }

    for instance in AgentSession.query.all():
        stats["total_records"] += 1
        try:
            stale_present = _raw_field_names(instance) & STALE_FIELDS
            if not stale_present:
                stats["clean"] += 1
                continue

            status = getattr(instance, "status", None)
            if status not in TERMINAL_STATUSES:
                # Live rows are actively written by the worker -- do not
                # rewrite them out from under it (the plan's [DESTRUCTIVE]
                # No-Go). Popoto ignores the stale fields on load, so deferral
                # is safe; the migration runs once per machine, so residual
                # stale fields on then-live rows simply age out via the
                # record's TTL.
                stats["deferred_non_terminal"] += 1
                logger.info(
                    "  DEFER %s (status=%s): stale fields %s left in place",
                    getattr(instance, "agent_session_id", "?"),
                    status,
                    sorted(stale_present),
                )
                continue

            logger.info(
                "  %s %s: stripping %s",
                "STRIP" if apply else "WOULD strip",
                getattr(instance, "agent_session_id", "?"),
                sorted(stale_present),
            )
            if apply:
                # Atomic delete + recreate on one transactional pipeline:
                # the hash is rewritten with only the current model fields.
                # Base-class save preserves the loaded updated_at (the
                # AgentSession override would restamp it to now).
                pipe = POPOTO_REDIS_DB.pipeline()
                pipe = instance.delete(pipeline=pipe)
                pipe = popoto.Model.save(instance, pipeline=pipe)
                pipe.execute()
            stats["stripped"] += 1
        except Exception as e:  # noqa: BLE001 -- per-record isolation
            stats["errors"] += 1
            logger.error(
                "Error stripping %s: %s",
                getattr(instance, "agent_session_id", "?"),
                e,
            )

    if stats["total_records"] == 0:
        # INSURANCE against the #1720 class-set window -- NOT a fix for a
        # proven cause. `AgentSession.query.all()` reads $Class:AgentSession,
        # which popoto's index rebuild deletes and re-adds in batches
        # (base.py:2779, 2846); a scan landing inside that window returns 0
        # rows with no exception (agent/index_drift.py:1-12). `/update` runs
        # migrations at Step 3.6, before the service restart, so this script
        # and a live worker's index repair are genuinely concurrent. Nothing
        # establishes that this window actually fired on any machine; the
        # guard is here because failing closed on the ambiguous observation is
        # cheap and the alternative is recording a migration that saw nothing.
        #
        # ACCEPTED CONSEQUENCE, by design: on a machine whose AgentSession
        # keyspace is legitimately empty (a fresh install), this exits non-zero
        # on EVERY `/update`, indefinitely, and `run_pending_migrations` never
        # records it complete. The recurring `FAIL:` line is EXPECTED OUTPUT,
        # not a live regression. Bounding the retry would need new persisted
        # state beside data/migrations_completed.json for a case no current
        # machine is in, so it is deliberately not bounded.
        logger.error(
            "ZERO RECORDS SCANNED: AgentSession.query.all() returned nothing. "
            "Refusing to report success -- an empty scan is indistinguishable "
            "from an index-rebuild window (#1720). Not recording completion; "
            "the next /update retries. On a genuinely empty keyspace this "
            "repeats every run and is expected."
        )
        return stats

    if apply and stats["stripped"]:
        # Per-record delete()+save() already maintain indexes atomically, so this
        # is a defensive orphan sweep, not a functional requirement.
        # clean_indexes() is the documented production-safe orphan-reference
        # cleanup. Deliberately NOT the full index rebuild, and NOT the repair
        # wrapper around it: that path tears down and rebuilds every index,
        # opening the #1720 class-set window where query.all() returns 0 with no
        # exception, and it currently fails outright with "unpack(b) received
        # extra data" on pre-existing phantom index metadata (tracked as #2536 --
        # investigate, do not blind-purge). The Verification row greps this file
        # for those two identifiers and expects zero matches, so do not name them
        # here even in a comment.
        logger.info("Cleaning AgentSession index orphans...")
        try:
            AgentSession.clean_indexes()
            logger.info("Index cleanup complete.")
        except Exception as e:  # noqa: BLE001
            logger.error("Index cleanup failed: %s", e)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strip removed pid fields (+expectations) from AgentSession records"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes (default is dry-run)",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info("migrate_strip_pid_fields: %s", mode)
    stats = migrate(apply=args.apply)
    logger.info("Stats: %s", stats)
    if stats["total_records"] == 0:
        # Distinct exit code so the empty-scan case is separable from a
        # per-record failure in logs/update.log. See the guard in migrate().
        return 2
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
