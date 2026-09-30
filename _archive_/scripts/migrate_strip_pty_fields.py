#!/usr/bin/env python3
"""Strip removed PTY fields (+resume_handles) from existing AgentSession records.

Plan #1924 (granite PTY teardown, task 5) removed these fields from the
AgentSession model:

    dev_pid, pty_slot, last_pty_read_loop_at, last_pty_activity_at,
    mid_run_quiescent_since, mid_run_pty_snapshot, role_transports,
    resume_handles

Popoto ignores unknown hash fields on load, so pre-cutover records remain
fully readable without this migration -- the stale hash entries are orphaned
data, not a crash hazard (Risk 5). This migration reclaims them via **ORM-safe
operations only** (no raw ``hdel``/``hset``): for each terminal record still
carrying a stale field, it queues ``instance.delete()`` + ``Model.save(
instance)`` on ONE transactional Redis pipeline (MULTI/EXEC), so the record is
atomically rewritten with only the current model fields -- a crash
mid-migration can never lose a record.

The scan, the zero-record guard, the index sweep and the exit codes live in
``scripts/_strip_migration.py`` -- one copy shared with the two sibling strip
migrations. See that module for the full safety-property discussion:
the ORM-safe atomic rewrite, idempotency, why the scope is terminal-only, why
deferred rows do not age out, the zero-record guard, the index sweep and the
exit codes.

Usage:
  python scripts/migrate_strip_pty_fields.py            # dry-run (default)
  python scripts/migrate_strip_pty_fields.py --apply    # commit changes
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
# logs/update.log records what the migration actually did.
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s", stream=sys.stdout)
logger = logging.getLogger(__name__)

#: Hash fields removed from the AgentSession model by plan #1924 task 5.
STALE_FIELDS = frozenset(
    {
        "dev_pid",
        "pty_slot",
        "last_pty_read_loop_at",
        "last_pty_activity_at",
        "mid_run_quiescent_since",
        "mid_run_pty_snapshot",
        "role_transports",
        "resume_handles",
    }
)


def _raw_field_names(instance) -> set[str]:
    """Raw hash field names for one record (detection-only ``HKEYS``)."""
    return raw_field_names(instance, logger)


def migrate(apply: bool = False) -> dict:
    """Strip stale PTY hash fields from terminal AgentSession records."""
    return run_strip_migration(
        STALE_FIELDS,
        apply=apply,
        logger=logger,
        field_names=_raw_field_names,
    )


def main() -> int:
    return strip_migration_main(
        script_name="migrate_strip_pty_fields",
        description="Strip removed PTY fields (+resume_handles) from AgentSession records",
        migrate=migrate,
        logger=logger,
    )


if __name__ == "__main__":
    sys.exit(main())
