"""Compatibility re-exports for maintenance reflections relocated to one file each.

Each maintenance reflection now lives in its own self-contained module under
``reflections/audits/`` or ``reflections/housekeeping/`` (one file per reflection
— see issue #1028). The reflections registry (config/reflections.yaml, vault)
references the historical ``reflections.maintenance.<fn>`` dotted paths below, so
each is re-exported here under its original name and the scheduler's importlib
resolution keeps working with no registry edit.

New code should import the reflection's ``run`` from its per-reflection module.
"""

from reflections.audits.redis_quality_audit import run as run_redis_data_quality
from reflections.audits.tech_debt_scan import run as run_legacy_code_scan
from reflections.housekeeping.analytics_rollup import run as run_analytics_rollup
from reflections.housekeeping.disk_space_check import run as run_disk_space_check
from reflections.housekeeping.merged_branch_cleanup import run as run_branch_plan_cleanup
from reflections.housekeeping.redis_ttl_cleanup import run as run_redis_ttl_cleanup

__all__ = [
    "run_legacy_code_scan",
    "run_redis_ttl_cleanup",
    "run_redis_data_quality",
    "run_branch_plan_cleanup",
    "run_disk_space_check",
    "run_analytics_rollup",
]
