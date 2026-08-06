#!/usr/bin/env python3
"""Strip schema-diet fields (#1927) from existing AgentSession records.

Plan #1927 (AgentSession schema diet) pruned an accreted telemetry surface
and applied one precision rename. This migration reclaims the stale hash
fields on Redis records.

Field-by-field disposition (audited starting point -- see the plan's
"Field-by-Field Disposition" table for full rationale):

DELETE -- no live reader AND no live writer:
    self_report_sent_at        -- retired 2026-05-06 self-report frequency cap
    sdk_connection_torn_down_at -- idle-sweeper deleted by plan #2000
    session_mode                -- deprecated no-op, superseded by session_type
    pm_transcript_path           -- no live writer, dashboard-only read (dropped)
    dev_transcript_path          -- no live writer, dashboard-only read (dropped)
    startup_failure_kind          -- historical PTY-era diagnostic; the ENTIRE
                                     plumbing chain (crash_signature.py reader,
                                     pass-through, "ceiling" branches, keyword
                                     param, docstring refs) was removed too
    startup_captured_frame        -- historical PTY-era diagnostic pointer

CUT -- write-only observability counters with no production reader:
    compaction_count
    compaction_skipped_count
    nudge_deferred_count

  (tool_timeout_count_{internal,mcp,default} is NOT in this set -- it is
  written via a dynamic `f"tool_timeout_count_{tier}"` setattr in
  agent/session_health.py and reads as dead to a literal grep only; the
  plan explicitly flags it as a delete-trap and keeps it.)

COLLAPSE -- metered/total accounting split, redirected to `total_*`:
    metered_input_tokens
    metered_output_tokens
    metered_cache_read_tokens
    metered_cost_usd

  `accumulate_session_tokens` no longer branches on a `metered=` flag --
  every caller (including the former `metered=True` session-runner leg)
  now accumulates onto the SAME `total_*` fields. The per-turn "metered-leg
  cost" ledger-metric series that the metered branch used to emit ended at
  this migration; there is no `total_*` replacement (deliberate, matches
  the plan's accepted loss of longitudinal comparability).

RENAME -- the one frozen rename (no open-ended survivor audit):
    watchdog_unhealthy -> unhealthy_reason
        Held a reason string, not a bool; the old name implied a flag.
        `AgentSession._normalize_kwargs` carries a back-alias so
        archive-restore payloads (which route through `__init__`) still
        map the old key -- this migration strips the orphaned old-name
        hash key from live Redis records, where Popoto's lazy-load reads
        the raw hash and bypasses `_normalize_kwargs` (so the pre-rename
        value is NOT copied forward; see the plan's "Rename value-
        preservation stance").

KEPT -- explicitly NOT renamed or deleted (frozen scope, do not touch):
    user_facing_routed -- persisted delivery-confirmation boolean; renaming
        it is unsafe because Popoto's lazy-load bypasses _normalize_kwargs,
        so an in-flight session crossing the deploy boundary would read the
        renamed field as its False default and mis-fire the delivery
        emoji. See the plan's Critique Results concern 2.
    total_input_tokens / total_output_tokens / total_cache_read_tokens /
        total_cost_usd -- high read fan-out (analytics, watchdog,
        tool_budget, pm_briefings); renaming is pure churn.

Popoto ignores unknown hash fields on load, so pre-cutover records remain
fully readable without this migration -- the stale hash entries are orphaned
data, not a crash hazard. This migration reclaims them via **ORM-safe
operations only** (no raw ``hdel``/``hset``): for each terminal record still
carrying a stale field, it queues ``instance.delete()`` + ``Model.save()``
on ONE transactional Redis pipeline, so the record is atomically rewritten
with only the current model fields -- a crash mid-migration can never lose a
record.

The scan, the zero-record guard, the index sweep and the exit codes live in
``scripts/_strip_migration.py`` -- one copy shared with the two sibling strip
migrations. See that module for the full safety-property
discussion: the ORM-safe atomic rewrite, idempotency, why the scope is
terminal-only, why deferred rows do not age out, the zero-record guard, the
index sweep and the exit codes.

Usage:
  python scripts/migrate_schema_diet_fields.py            # dry-run (default)
  python scripts/migrate_schema_diet_fields.py --apply    # commit changes
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

#: Hash fields removed (or renamed away from) the AgentSession model by
#: plan #1927 (AgentSession schema diet).
STALE_FIELDS = frozenset(
    {
        # DELETE -- no live reader and no live writer.
        "self_report_sent_at",
        "sdk_connection_torn_down_at",
        "session_mode",
        "pm_transcript_path",
        "dev_transcript_path",
        "startup_failure_kind",
        "startup_captured_frame",
        # CUT -- write-only observability counters.
        "compaction_count",
        "compaction_skipped_count",
        "nudge_deferred_count",
        # COLLAPSE -- metered/total accounting split.
        "metered_input_tokens",
        "metered_output_tokens",
        "metered_cache_read_tokens",
        "metered_cost_usd",
        # RENAME -- old field name (survivor now lives at unhealthy_reason).
        "watchdog_unhealthy",
    }
)


def _raw_field_names(instance) -> set[str]:
    """Raw hash field names for one record (detection-only ``HKEYS``)."""
    return raw_field_names(instance, logger)


def migrate(apply: bool = False) -> dict:
    """Strip stale schema-diet hash fields from terminal AgentSession records."""
    return run_strip_migration(
        STALE_FIELDS,
        apply=apply,
        logger=logger,
        field_names=_raw_field_names,
    )


def main() -> int:
    return strip_migration_main(
        script_name="migrate_schema_diet_fields",
        description="Strip schema-diet (#1927) fields from AgentSession records",
        migrate=migrate,
        logger=logger,
    )


if __name__ == "__main__":
    sys.exit(main())
