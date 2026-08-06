"""Shared engine for the AgentSession "strip removed hash fields" migrations.

Three migrations reclaim orphaned hash fields left behind when the
AgentSession model dropped a batch of fields:

    scripts/migrate_strip_pty_fields.py     (plan #1924 task 5)
    scripts/migrate_schema_diet_fields.py   (plan #1927)
    scripts/migrate_strip_pid_fields.py     (durability M1, #2518)

They differ only in WHICH field names they strip. Everything else -- the scan,
the terminal-only atomic rewrite, the zero-record guard, the trailing index
sweep, and the exit-code contract -- lives here, in one copy.

That consolidation is the point of issue #2524. The three scripts were
near-verbatim clones; #2518 hardened one of them and the other two drifted,
which is precisely the failure mode a copy-paste fix would have reproduced.

Mechanism, common to all three:

Popoto ignores unknown hash fields on load, so pre-cutover records remain fully
readable without any migration -- the stale hash entries are orphaned data, not
a crash hazard. This engine reclaims them via **ORM-safe operations only** (no
raw ``hdel``/``hset``): for each terminal record still carrying a stale field,
it queues ``instance.delete()`` + ``Model.save(instance)`` on ONE transactional
Redis pipeline (MULTI/EXEC), so the record is atomically rewritten with only the
current model fields -- a crash mid-migration can never lose a record.

Safety properties:

- **Idempotent**: re-running finds zero records with stale fields -> no-op.
- **Atomicity, not quiescence**: only records whose ``status`` is in
  ``models.session_lifecycle.TERMINAL_STATUSES`` are rewritten, but terminal
  rows are **not** quiescent.
  ``agent.session_health.cleanup_corrupted_agent_sessions`` re-saves every
  hydrated record -- terminal ones included -- as its "no-op save" corruption
  probe, and ``/update`` invokes it at Step 5.5, as does worker startup and the
  ``agent-session-cleanup`` reflection. Because ``AgentSession.save()``
  restamps ``updated_at``, that pass moves every record's timestamp in one batch
  at ``/update`` time. So the safety property here is **not** "nobody else
  writes terminal rows"; it is that the delete + recreate is queued on ONE
  transactional Redis pipeline (MULTI/EXEC), so a crash or an interleaved
  writer can never lose a record. A concurrent write that lands between the
  read and the pipeline is lost, which is why the scope stays terminal-only:
  those rows carry no in-flight state worth racing for. Non-terminal records
  are skipped and reported -- they hydrate fine (Popoto ignores the stale
  fields on load). This is the [DESTRUCTIVE] No-Go boundary from the durability
  plan: rewriting a running session's hash risks clobbering concurrent writes,
  so it is out of scope by design. The base ``popoto.Model.save`` is used
  directly so ``updated_at`` is preserved as loaded (the AgentSession override
  would restamp it and falsify freshness on old records).
- **Deferred rows do not age out**: every popoto ``save()`` re-issues
  ``EXPIRE`` with ``Meta.ttl`` (popoto ``base.py:1186-1190``), so the 30-day
  backstop only fires on a record nothing writes for 30 days. Any record that
  keeps being written holds a perpetually-refreshed TTL -- true of
  ``is_ledger=True`` SDLC anchors, which are re-saved continuously while their
  pipeline is open, and true of every record on every tick of the cleanup pass
  above. A deferred row therefore keeps its stale fields until a later run
  finds it terminal.
- **TTL note**: the atomic rewrite refreshes the record's ``Meta.ttl`` (30-day
  backstop) -- acceptable for a one-time migration; stale terminal sessions
  remain subject to the cleanup CLI.

Exit-code contract (``strip_migration_main``):

    0  scan saw records and hit no per-record errors
    1  at least one per-record error -- migration is NOT recorded complete
    2  the zero-record guard fired -- deliberately distinct from 1 so the two
       are separable in logs/update.log
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Iterable

#: The zero-record guard's own message. Kept as a module constant so the three
#: scripts and their tests all anchor on the same string.
ZERO_RECORDS_MESSAGE = (
    "ZERO RECORDS SCANNED: AgentSession.query.all() returned nothing. "
    "Refusing to report success -- an empty scan is indistinguishable "
    "from an index-rebuild window (#1720). Not recording completion; "
    "the next /update retries. On a genuinely empty keyspace this "
    "repeats every run and is expected."
)


def raw_field_names(instance, logger: logging.Logger) -> set[str]:
    """Field names present in the record's raw hash.

    Detection-only read of hash FIELD NAMES via ``HKEYS`` against the
    ORM-provided key (``instance._redis_key`` / ``db_key``). This reads no
    values, so the binary-field decode hazard that bans raw value reads
    (``hgetall``/``hget`` -- issue #1038) does not apply; Popoto itself
    exposes no ORM API for orphaned-hash-field discovery (its migration
    cookbook prescribes raw access for exactly this). All WRITES here remain
    ORM-only (``instance.delete()`` + ``Model.save()``).
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


def run_strip_migration(
    stale_fields: Iterable[str],
    *,
    apply: bool,
    logger: logging.Logger,
    field_names: Callable[[object], set[str]],
) -> dict:
    """Strip ``stale_fields`` from terminal AgentSession records.

    Args:
        stale_fields: Hash field names the model no longer declares.
        apply: If False, report what would happen without writing.
        logger: The calling script's logger, so log lines carry that script's
            name and its tests can capture on it.
        field_names: Detection function returning the raw hash field names for
            one instance -- normally the caller's module-level
            ``_raw_field_names``, which wraps ``raw_field_names`` below.

            REQUIRED, deliberately, with no default. A default would make the
            argument omittable, and a caller that omitted it would still run
            correctly while its module-level ``_raw_field_names`` stopped being
            consulted -- silently turning every
            ``patch.object(mod, "_raw_field_names", ...)`` in the test suite
            into a no-op. That is the same class of vacuous-assertion bug this
            consolidation exists to clean up, so the argument is mandatory and
            ``tests/unit/test_strip_migration_shared.py`` asserts each script
            passes it by name. It also makes this engine unit-testable without
            Redis.

    Returns:
        Dict with migration stats.
    """
    import popoto
    from popoto.redis_db import POPOTO_REDIS_DB

    from models.agent_session import AgentSession
    from models.session_lifecycle import TERMINAL_STATUSES

    stale_fields = frozenset(stale_fields)

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
            stale_present = field_names(instance) & stale_fields
            if not stale_present:
                stats["clean"] += 1
                continue

            status = getattr(instance, "status", None)
            if status not in TERMINAL_STATUSES:
                # Live rows are actively written by the worker -- do not
                # rewrite them out from under it (the plan's [DESTRUCTIVE]
                # No-Go). Popoto ignores the stale fields on load, so deferral
                # is safe; a later run reclaims the row once it is terminal.
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
        # migrations at Step 3.6, before the service restart, so these scripts
        # and a live worker's index repair are genuinely concurrent. Nothing
        # establishes that this window actually fired on any machine; the
        # guard is here because failing closed on the ambiguous observation is
        # cheap and the alternative is recording a migration that saw nothing.
        #
        # ACCEPTED CONSEQUENCE, by design: on a machine whose AgentSession
        # keyspace is legitimately empty (a fresh install), every migration
        # routed through this engine exits non-zero on EVERY `/update`,
        # indefinitely, and `run_pending_migrations` never records them
        # complete. The recurring `FAIL:` lines are EXPECTED OUTPUT, not a live
        # regression. Distinguishing a genuinely empty keyspace from a blinded
        # scan is possible (a detection-only SCAN for `AgentSession:*` key
        # names) but reverses a decision taken in #2518's critique, so it is
        # deliberately left alone here and tracked separately.
        logger.error(ZERO_RECORDS_MESSAGE)
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
        # investigate, do not blind-purge). THIS FILE and the three delegate
        # scripts are grepped for those two identifiers and must contain zero
        # matches (tests/unit/test_strip_migration_shared.py), so do not name
        # them anywhere here -- not even in a comment.
        logger.info("Cleaning AgentSession index orphans...")
        try:
            AgentSession.clean_indexes()
            logger.info("Index cleanup complete.")
        except Exception as e:  # noqa: BLE001
            logger.error("Index cleanup failed: %s", e)

    return stats


def strip_migration_main(
    *,
    script_name: str,
    description: str,
    migrate: Callable[..., dict],
    logger: logging.Logger,
) -> int:
    """Argparse + banner + stats line + exit codes, shared by all three scripts.

    Args:
        script_name: Bare script name, used in the mode banner.
        description: ``--help`` text.
        migrate: The calling script's ``migrate(apply=...)``. Taken as an
            argument rather than called directly so a test patching the
            script's module-level names still sees its own function run.
        logger: The calling script's logger.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes (default is dry-run)",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    logger.info("%s: %s", script_name, mode)
    stats = migrate(apply=args.apply)
    logger.info("Stats: %s", stats)

    missing = {"total_records", "errors"} - set(stats)
    if missing:
        # Fail closed with an attributed reason. Bare subscripting would raise
        # KeyError out of main() and leave a naked traceback in update.log; a
        # `.get(..., 0)` default would be worse still, silently converting a
        # malformed stats dict into the exit-2 "blinded scan" diagnosis.
        logger.error("malformed stats from %s: missing %s", script_name, sorted(missing))
        return 1

    if stats["total_records"] == 0:
        # Distinct exit code so the empty-scan case is separable from a
        # per-record failure in logs/update.log. See the guard above.
        return 2
    return 1 if stats["errors"] else 0
