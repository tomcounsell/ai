"""reflections/housekeeping/test_baseline_refresh_check.py — Age-only staleness detector.

What it does: Reads ``data/main_test_baseline.json``'s ``generated_at`` field
    and compares its age against the merge-gate's ``STALENESS_THRESHOLD``
    (currently 14 days, defined in ``scripts/baseline_gate.py``). Records a
    warning-status finding when the baseline is older than that threshold.
    Read-only; runs no tests -- it never invokes pytest itself, so it carries
    none of the Redis-collision / memory-thrash hazard that a scheduled
    full-suite regen would (see docs/plans/merge-gate-baseline-stale-refresh.md
    Staleness Answer, issue #1933).
Cadence: weekly (surfaces staleness between merges instead of letting it
    silently drift, as it did to ~60 days before PR #1930)
Failure modes:
    - baseline file missing/unparseable -> caught, benign "no baseline" status
Related reflections: (none yet -- this is the anti-recurrence mechanism for
    the staleness gap; the operator runs `python scripts/refresh_test_baseline.py`
    manually once this fires)
See also: config/reflections.yaml (declaration, per-machine opt-in via
    ~/Desktop/Valor/reflections.yaml), docs/features/merge-gate-baseline.md
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from scripts._baseline_common import SCHEMA_VERSION
from scripts.baseline_gate import STALENESS_THRESHOLD, load_baseline
from scripts.refresh_test_baseline import DEFAULT_BASELINE_PATH

logger = logging.getLogger("reflections.maintenance")


async def run() -> dict:
    """Check the age of ``data/main_test_baseline.json`` against STALENESS_THRESHOLD.

    Records a warning finding when ``generated_at`` is older than the
    threshold. Missing or unparseable baselines are benign (nothing to warn
    about yet -- the merge gate's own bootstrap path handles that case).
    """
    try:
        baseline = load_baseline(DEFAULT_BASELINE_PATH)
    except Exception as e:
        logger.exception(f"Failed to load test baseline: {e}")
        return {
            "status": "ok",
            "findings": [],
            "summary": f"No baseline available (load error: {e})",
        }

    tests = baseline.get("tests")
    if baseline.get("schema_version") != SCHEMA_VERSION or not isinstance(tests, dict):
        return {"status": "ok", "findings": [], "summary": "No baseline available yet"}

    generated_at_raw = baseline.get("generated_at")
    if not isinstance(generated_at_raw, str):
        return {
            "status": "ok",
            "findings": [],
            "summary": "Baseline has no generated_at -- treating as no baseline",
        }

    try:
        generated_at = datetime.fromisoformat(generated_at_raw)
    except ValueError:
        return {
            "status": "ok",
            "findings": [],
            "summary": f"Baseline generated_at is unparseable ({generated_at_raw!r})",
        }

    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    age = now - generated_at

    if age > STALENESS_THRESHOLD:
        finding = (
            f"Test baseline is {age.days} days old (> {STALENESS_THRESHOLD.days}) -- "
            "run `python scripts/refresh_test_baseline.py` on a quiescent main checkout"
        )
        logger.warning(finding)
        return {"status": "warning", "findings": [finding], "summary": finding}

    summary = f"Test baseline is fresh ({age.days} days old)"
    logger.info(summary)
    return {"status": "ok", "findings": [], "summary": summary}
