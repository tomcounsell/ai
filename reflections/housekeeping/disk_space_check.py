"""reflections/housekeeping/disk_space_check.py — Warn when free disk space is low.

What it does: Reads shutil.disk_usage on the project volume and records a finding
    when free space drops below 10 GB (read-only; no writes).
Cadence: 86400s (daily) (early warning before the volume fills)
Failure modes:
    - disk_usage raises -> caught, status="error" with the exception in summary
Related reflections:
    - redis_ttl_cleanup: reclaims space this check monitors
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging
import shutil

from reflections.utilities import PROJECT_ROOT

logger = logging.getLogger("reflections.maintenance")


async def run() -> dict:
    """Check available disk space on the project volume.

    Records a finding when free space drops below 10 GB.
    """
    findings: list[str] = []

    try:
        usage = shutil.disk_usage(PROJECT_ROOT)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)

        if free_gb < 10:
            finding = (
                f"Low disk space: {free_gb:.1f} GB free "
                f"of {total_gb:.1f} GB total on project volume"
            )
            findings.append(finding)
            logger.warning(finding)
        else:
            logger.info(f"Disk space OK: {free_gb:.1f} GB free of {total_gb:.1f} GB total")
    except Exception as e:
        logger.exception(f"Failed to check disk space: {e}")
        return {"status": "error", "findings": [], "summary": f"Disk space check error: {e}"}

    summary = findings[0] if findings else "Disk space OK"
    return {"status": "ok", "findings": findings, "summary": summary}
