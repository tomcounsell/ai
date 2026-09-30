#!/usr/bin/env python3
"""Strip removed pid fields (+expectations) from existing AgentSession records.

The durability-room-job-agentrun plan (Milestone 1) replaced the raw pid
fields with a fenced execution record and deleted these hash fields from the
AgentSession model:

    claude_pid, pm_pid, harness_pid, expectations

Popoto ignores unknown hash fields on load, so pre-cutover records remain
fully readable without this migration -- the stale hash entries are orphaned
data, not a crash hazard.

The mechanism and every safety property -- ORM-safe atomic rewrite,
idempotency, why the scope is terminal-only despite terminal rows NOT being
quiescent, why deferred rows do not age out, the zero-record guard, the index
sweep and the exit codes -- live in ``scripts/_strip_migration.py``, one copy
shared with the two sibling strip migrations (#2524). Read that module for the
full discussion. This script contributes only the field set above.

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
