"""Shared guarded index reconstruction for the rename-style AgentSession migrations.

Five migrations rename AgentSession hash fields or Redis keys through raw Redis
and must then reconstruct the indexes:

    scripts/migrate_agent_session_keyfield_rename.py    (live registry entry)
    scripts/migrate_unify_parent_session_field.py       (live registry entry)
    scripts/migrate_parent_session_field.py             (unregistered)
    scripts/migrate_session_type_pm_to_eng.py           (unregistered)
    scripts/migrate_session_type_chat_to_pm.py          (unregistered)

They all write a **KeyField** value (``id``, ``parent_agent_session_id``,
``session_type``) outside the ORM, so no index entry is ever created for the new
value and the class set may still point at the old key. Reconstruction is
therefore LOAD-BEARING here, unlike the strip migrations' trailing sweep
(#2524): ``clean_indexes()`` is removal-only, so it would drop the stale
pointers and never add the new ones, leaving the renamed records unqueryable.
It is NOT a substitute. See #2544 and
``docs/features/popoto-index-hygiene.md`` "Migration Guards".

This module exists so that guard has ONE copy. #2524 consolidated the strip
family for exactly this reason -- "what keeps the guard, the sweep and the exit
codes from drifting apart across three copies" -- and five hand-copies of a
fail-closed branch is the same hazard in a new place.

**Why the guarded repair, not popoto's raw rebuild.** ``repair_indexes()``
calls ``rebuild_indexes()`` internally, so it is equally load-bearing and pays
the same #1720 class-set window (~22s on a 4006-row keyspace, #2549). What it
adds is what makes paying that window survivable:

- ``assert_popoto_floor()`` runs BEFORE any teardown (#2536). A below-floor
  popoto cannot decode the index-pointer fields an at-or-above-floor popoto
  writes; the raw rebuild deletes every index before discovering that, so it
  destroys the index and rebuilds nothing -- the 2026-07-14 silent-empty
  incident.
- The ``$IndexF`` stale-pointer cleanup the raw rebuild never enumerates.
- The A1 identity-less shim (#2101, #2207), so phantom hashes are not
  re-inflated into the indexes on the way through.
"""

from __future__ import annotations

import logging


def reconstruct_agent_session_indexes(
    stats: dict,
    logger: logging.Logger,
    *,
    wrote: str,
) -> None:
    """Rebuild the AgentSession indexes, recording an error if that did not happen.

    Call this only when the migration actually wrote something. Every caller
    gates on its own "did I change any record" counter, so a run that renamed
    nothing (a fresh install, or an already-migrated machine) never reaches
    here and never pays the rebuild window.

    Args:
        stats: The caller's stats dict. ``stats["errors"]`` is incremented when
            the reconstruction did not run, which every caller's ``main()``
            turns into a non-zero exit -- and ``run_pending_migrations`` only
            records a migration complete when its helper returns ``None``, so a
            non-zero exit withholds the completion record and the next
            ``/update`` retries.
        logger: The calling script's logger, so ``logs/update.log`` says WHICH
            migration failed to reindex.
        wrote: Short phrase naming what this migration wrote raw, e.g.
            ``"Keys were renamed"``. Used to make the failure message specific
            about which records are now unreachable.

    FAILS CLOSED in both directions, because the renames have already landed by
    the time this runs. A swallowed failure would record the migration complete
    with the index still describing the pre-rename state:

    - ``repair_indexes()`` raising -- which is how ``assert_popoto_floor()``
      surfaces -- counts an error.
    - ``repair_indexes()`` returning ``(0, 0)`` counts an error. It returns
      that WITHOUT rebuilding when its non-reentrant lock is already held.

    ON THAT ``(0, 0)`` BRANCH, precisely, because the obvious readings of it are
    both wrong:

    **It cannot currently fire from these scripts.** The lock is a per-class
    ``threading.Lock`` (``models/agent_session.py:2437-2440``), so it is
    per-PROCESS. Migrations run as their own subprocess via
    ``scripts/update/migrations.py::_run_migration_script``, and each
    ``migrate()`` is straight-line single-threaded, so nothing in that process
    can be holding it. This check is defense-in-depth against a branch that is
    unreachable on today's call path, not a live hazard being handled.

    **It is not the cross-process interlock the `/update` scenario would want.**
    ``/update`` runs migrations at Step 3.6, before the service restart, so they
    are genuinely concurrent with a live worker's index repair -- but that race
    is cross-process and a ``threading.Lock`` does not span processes. Both
    processes take their own lock, install their own shims, and rebuild. The
    state converges (whichever rebuild finishes last does a full pass), so this
    is not a correctness break, but nothing here interlocks it.

    **If it did fire, the damage would not self-heal from the in-flight repair.**
    ``repair_indexes()`` deletes every ``$IndexF:AgentSession:*`` key at
    ``models/agent_session.py:2424-2433`` -- BEFORE the
    ``lock.acquire(blocking=False)`` at ``:2442``. So a ``(0, 0)`` return leaves
    the field indexes already torn down and not rebuilt, and a same-process
    in-flight repair is past its own ``$IndexF`` phase and would only re-SADD
    the tail of its remaining records. The actual healer is the worker's startup
    ``repair_indexes()`` (``worker/__main__.py``), which ``/update`` triggers at
    the service restart immediately after Step 3.6.
    """
    logger.info("Repairing Popoto indexes...")
    try:
        from models.agent_session import AgentSession

        _stale, rebuilt = AgentSession.repair_indexes()
    except Exception as e:  # noqa: BLE001 -- fail closed; includes the popoto floor assertion
        stats["errors"] += 1
        logger.error("Failed to repair indexes: %s", e)
        return

    if rebuilt:
        logger.info("Index repair complete (%s records reindexed).", rebuilt)
        return

    stats["errors"] += 1
    logger.error(
        "Index repair was SKIPPED (repair_indexes() returned (0, 0), meaning its "
        "lock was held and no rebuild ran). %s raw, so the affected records are "
        "not reachable through the ORM until a repair completes; not reporting "
        "success. The worker's startup repair at the post-/update restart is the "
        "healer -- see scripts/_migration_index_repair.py for why.",
        wrote,
    )
