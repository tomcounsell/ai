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
  ``agent.session_health.cleanup_corrupted_agent_sessions`` sweeps every
  hydrated record -- terminal ones included -- and ``/update`` invokes it at
  Step 5.5, as does worker startup and the ``agent-session-cleanup``
  reflection. That sweep classifies with ``is_valid()`` and issues only a
  targeted ``EXPIRE`` keepalive per healthy row, so it writes no field value
  and moves no record's ``updated_at`` (issue #2660). Other writers may still
  touch a terminal row, so the safety property here is **not** "nobody else
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
  pipeline is open. The cleanup pass above holds every healthy record's TTL at
  the ceiling too, but deliberately and without a field write, via
  ``AgentSession.refresh_ttl()`` (issue #2698 owns the decision to stop). A
  deferred row therefore keeps its stale fields until a later run finds it
  terminal.
- **TTL note**: the atomic rewrite refreshes the record's ``Meta.ttl`` (30-day
  backstop) -- acceptable for a one-time migration; stale terminal sessions
  remain subject to the cleanup CLI.

Exit-code contract (``strip_migration_main``):

    0  scan saw records and hit no per-record errors, OR it saw none and a
       bounded SCAN confirmed the keyspace holds no AgentSession hashes at all
       (a fresh install -- there is nothing to strip, so the migration is
       complete and is recorded as such)
    1  at least one per-record error -- migration is NOT recorded complete
    2  the zero-record guard fired on a BLINDED scan: nothing was queryable but
       raw AgentSession:* hashes exist, so an index rebuild was hiding them.
       Deliberately distinct from 1 so the two are separable in
       logs/update.log
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Iterable

#: The zero-record guard's own message, emitted when the scan was BLINDED --
#: ``query.all()`` saw nothing while raw ``AgentSession:*`` hashes still exist.
#: Kept as a module constant so the three scripts and their tests all anchor on
#: the same string.
ZERO_RECORDS_MESSAGE = (
    "ZERO RECORDS SCANNED: AgentSession.query.all() returned nothing while raw "
    "AgentSession:* hashes still exist in Redis. The class set is blinding the "
    "query (#1720, observed in #2549), so the keyspace is NOT empty. Refusing "
    "to report success -- not recording completion; the next /update retries."
)

#: Emitted on the other side of the same fork: the scan saw nothing AND a
#: bounded SCAN proves there is nothing to see. Not an error -- this is the
#: fresh-install path, and it records the migration complete.
EMPTY_KEYSPACE_MESSAGE = (
    "Zero records scanned, and a bounded SCAN found no AgentSession:* hashes: "
    "the keyspace is genuinely empty (fresh install). Nothing to strip; "
    "recording the migration complete."
)


def agent_session_hash_count() -> tuple[int, bool]:
    """Detection-only count of raw ``AgentSession:*`` record hashes.

    Delegates to the drift registry's bounded SCAN
    (``agent.index_drift``), which already counts exactly the right thing:
    ``hash``-typed base keys only, with ``::``-suffixed capped-list companion
    keys and non-hash keys excluded, so the result is apples-to-apples with
    ``len(AgentSession.query.all())``.

    Reads key NAMES and types only, never values, so the binary-field decode
    hazard that bans raw value reads (#1038) does not apply -- the same
    discipline :func:`raw_field_names` already uses for ``HKEYS``.

    Returns:
        ``(hash_count, exhaustive)``. ``exhaustive`` is ``False`` when the
        bounded SCAN hit its iteration cap, in which case ``hash_count`` is a
        partial undercount and MUST NOT be read as proof of emptiness.
    """
    from agent.index_drift import DRIFT_COVERED_MODELS

    return DRIFT_COVERED_MODELS["AgentSession"].count_hashes()


def raw_field_names(instance, logger: logging.Logger) -> set[str]:
    """Field names present in the record's raw hash.

    Detection-only read of hash FIELD NAMES via ``HKEYS`` against the
    ORM-provided key (``instance._redis_key`` / ``db_key``). This reads no
    values, so the binary-field decode hazard that bans raw value reads
    (``hgetall``/``hget`` -- issue #1038) does not apply; Popoto itself
    exposes no ORM API for orphaned-hash-field discovery (its migration
    cookbook prescribes raw access for exactly this). All WRITES here remain
    ORM-only (``instance.delete()`` + ``Model.save()``).

    FAILS CLOSED. A failed ``HKEYS`` read propagates, so the caller's
    per-record handler counts it in ``errors`` and the run exits 1 without
    being recorded complete. Swallowing it and returning an empty set --
    which this did before #2524 -- makes a record whose detection failed
    indistinguishable from a genuinely clean one: ``errors`` stays 0, the exit
    is 0, and ``run_pending_migrations`` records the migration permanently
    complete. A transient Redis blip during the scan would then manufacture
    exactly the artifact these re-runs exist to produce, a log line claiming
    the keyspace is clean. The zero-record guard fails closed for the same
    reason; this path must not fail open beside it.

    ACCEPTED CONSEQUENCE, by design, and unlike the zero-record guard's (which
    now disambiguates its two cases and no longer has one):
    the failure is not necessarily transient. ``HKEYS`` against a key that
    exists with the wrong type raises ``WRONGTYPE`` **deterministically**, and
    this repo has a history of phantom ``AgentSession:*`` keys (#2207) and
    phantom index metadata (#2536). One such record pins the migration at exit
    1 on every ``/update``, indefinitely, and it is never recorded complete.
    That recurring ``FAIL:`` line is EXPECTED OUTPUT, not a live regression.
    It is preferred over the alternative: the scan still visits every other
    record, and the ``logger.error`` below names the offending ``redis_key``,
    so an operator gets an actionable pointer instead of a silent "clean".
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    redis_key = getattr(instance, "_redis_key", None) or instance.db_key.redis_key
    names: set[str] = set()
    try:
        for key in POPOTO_REDIS_DB.hkeys(redis_key):
            names.add(key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key))
    except Exception as e:
        logger.error("hkeys failed for %s: %s -- counting as an error, not as clean", redis_key, e)
        raise
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
        # Set only by the zero-record fork below. Stays False on every path
        # that scanned at least one record, and False is the fail-closed
        # answer -- `strip_migration_main` only records completion for a
        # zero-record run when this is affirmatively True.
        "keyspace_confirmed_empty": False,
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
        # A zero-record scan has exactly two causes, and they demand opposite
        # answers. This fork tells them apart instead of assuming the worse one.
        #
        # BLINDED. `AgentSession.query.all()` reads $Class:AgentSession, which
        # popoto's rebuild DELETES outright at the top (base.py:2779) and only
        # re-adds at each `batch_size=1000` pipeline flush (base.py:2870). The
        # class set is therefore empty for the whole rebuild on any keyspace
        # under 1000 rows, and near-empty well past that; a scan landing inside
        # returns 0 rows with no exception (agent/index_drift.py:1-12).
        # `/update` runs migrations at Step 3.6, BEFORE the service restart, so
        # these scripts and a live worker's index repair are genuinely
        # concurrent.
        #
        # This is measured, not theorized. Driving popoto's raw index rebuild
        # against a concurrent `query.all()` poller on an isolated Redis (#2549);
        # the identifier is spelled out in that issue, deliberately not here:
        #
        #     rows    rebuild    polls seeing ZERO
        #      150     0.24s          96.5%
        #     1000     1.17s          99.8%
        #     4006    22.33s          91.8%
        #
        # The window is not a narrow race at a batch boundary -- it is
        # essentially the entire duration of the rebuild, and that duration
        # grows with the keyspace. It matches the production sighting in #2549:
        # a dry run returning total_records=0 on a 4006-row host seconds before
        # a repair_indexes() logging sessions_rebuilt=4006.
        #
        # Without this guard that run would have exited 0 and been recorded
        # permanently complete having stripped nothing.
        #
        # GENUINELY EMPTY. A fresh install has no records and never will have
        # any to strip. Failing closed there is a permanent, unbounded failure:
        # every /update reruns the migration, sees zero, and fails, forever
        # (#2543). Six registry entries route through this engine, so that was
        # six recurring FAIL: lines an operator had to know were fine -- which
        # trains people to ignore the one place a real migration failure shows
        # up.
        #
        # The discriminator is a bounded, detection-only SCAN for raw
        # AgentSession:* hashes. The rebuild only ever touches INDEX keys; the
        # record hashes survive it untouched. Verified on the same isolated
        # rig: while query.all() returned 0 mid-rebuild, the SCAN still saw all
        # 4006 hashes, and on a truly empty keyspace it saw 0.
        #
        # Fails closed on anything short of proof: a truncated SCAN (hit its
        # iteration cap, so the count is a partial undercount) and a SCAN that
        # raises both fall through to the blinded branch. A keyspace holding
        # only phantom AgentSession:* bookkeeping hashes (#2207) also reads as
        # non-empty here and pins the migration at exit 2 -- correctly, since
        # purge_phantom_agent_sessions is the fix for that state, not this.
        try:
            hash_count, exhaustive = agent_session_hash_count()
        except Exception as e:  # noqa: BLE001 -- fail closed, never fail open
            logger.error(
                "AgentSession:* hash SCAN failed (%s) -- cannot prove the keyspace "
                "is empty, so treating the zero-record scan as blinded",
                e,
            )
            hash_count, exhaustive = -1, False

        if exhaustive and hash_count == 0:
            stats["keyspace_confirmed_empty"] = True
            logger.info(EMPTY_KEYSPACE_MESSAGE)
        else:
            logger.error(
                "%s (raw AgentSession:* hashes seen: %s, scan exhaustive: %s)",
                ZERO_RECORDS_MESSAGE,
                "unknown" if hash_count < 0 else hash_count,
                exhaustive,
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
        # investigate, do not blind-purge).
        #
        # The RAW REBUILD identifier must not appear anywhere in this file or in
        # the three delegate scripts -- not even in a comment. Two tests enforce
        # that, and they cover different files: the delegates are checked by
        # tests/unit/test_strip_migration_shared.py, and THIS file by
        # tests/unit/test_migrate_strip_pid_fields.py. Only the raw-rebuild name
        # is asserted zero-match; the repair wrapper's name is a legitimate
        # identifier elsewhere in the repo and is not grepped.
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
        if stats.get("keyspace_confirmed_empty"):
            # Nothing to strip and nothing hidden: a fresh install is complete
            # by definition. Recording it stops the permanent /update failure
            # loop of #2543.
            return 0
        # Distinct exit code so a blinded scan is separable from a per-record
        # failure in logs/update.log. `.get` defaults to False, so a stats dict
        # that predates this key -- or a migrate() that returns its own -- fails
        # closed here rather than being recorded complete. See the guard above.
        return 2
    return 1 if stats["errors"] else 0
