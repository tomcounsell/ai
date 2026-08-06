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

The scan, the zero-record guard, the index sweep and the exit codes live in
``scripts/_strip_migration.py`` -- one copy shared with the two sibling strip
migrations (#2524). This script contributes only the field set above.

Usage:
  python scripts/migrate_strip_pid_fields.py            # dry-run (default)
  python scripts/migrate_strip_pid_fields.py --apply    # commit changes
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts._strip_migration import (  # noqa: E402 -- must follow the sys.path insert
    raw_field_names,
    run_strip_migration,
    strip_migration_main,
)

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
    """Raw hash field names for one record (detection-only ``HKEYS``)."""
    return raw_field_names(instance, logger)


def migrate(apply: bool = False) -> dict:
    """Strip stale pid hash fields from terminal AgentSession records."""
    return run_strip_migration(
        STALE_FIELDS,
        apply=apply,
        logger=logger,
        field_names=_raw_field_names,
    )


def main() -> int:
    return strip_migration_main(
        script_name="migrate_strip_pid_fields",
        description="Strip removed pid fields (+expectations) from AgentSession records",
        migrate=migrate,
        logger=logger,
    )


if __name__ == "__main__":
    sys.exit(main())
