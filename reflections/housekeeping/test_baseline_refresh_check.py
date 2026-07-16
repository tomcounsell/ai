"""reflections/housekeeping/test_baseline_refresh_check.py — Baseline staleness detector.

What it does: Reads ``data/main_test_baseline.json``'s :class:`ArtifactEnvelope`
    and evaluates it against the ONE shared staleness definition
    (``scripts._baseline_common.staleness()``, also used by the merge gate):
    ``generated_at`` older than the gate's ``STALENESS_THRESHOLD`` (14 days),
    a ``-dirty`` capture commit, or a commit more than ``STALE_COMMIT_DISTANCE``
    commits behind HEAD. Records a warning-status finding when any trigger
    fires. Read-only; runs no tests -- it never invokes pytest itself, so it
    carries none of the Redis-collision / memory-thrash hazard that a scheduled
    full-suite regen would (see docs/plans/merge-gate-baseline-stale-refresh.md
    Staleness Answer, issue #1933; shared definition per issue #2004).
Cadence: weekly (surfaces staleness between merges instead of letting it
    silently drift, as it did to ~60 days before PR #1930)
Failure modes:
    - baseline file missing/unparseable -> caught, benign "no baseline" status
    - git unavailable / unknown commit -> commit-distance trigger skipped
Related reflections: (none yet -- this is the anti-recurrence mechanism for
    the staleness gap; the operator launches `scripts/refresh_baseline_detached.sh`
    — the timeout-safe wrapper — once this fires, since a foreground refresh is
    killed at the 10-min bash cap, #2066)
See also: config/reflections.yaml (declaration; registered by
    scripts/update/reflection_register.py on /update),
    docs/features/merge-gate-baseline.md
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from scripts._baseline_common import (
    SCHEMA_VERSION,
    parse_generated_at,
    read_envelope,
    staleness,
)
from scripts.baseline_gate import commits_behind_head, load_baseline
from scripts.refresh_test_baseline import DEFAULT_BASELINE_PATH

logger = logging.getLogger("reflections.maintenance")


async def run() -> dict:
    """Evaluate ``data/main_test_baseline.json`` against the shared staleness rules.

    Records a warning finding when ``scripts._baseline_common.staleness()``
    reports any reason (age, dirty capture, commit distance). Missing or
    unparseable baselines are benign (nothing to warn about yet -- the merge
    gate's own bootstrap path handles that case).
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

    envelope = read_envelope(baseline)
    if envelope.generated_at is None:
        return {
            "status": "ok",
            "findings": [],
            "summary": "Baseline has no generated_at -- treating as no baseline",
        }

    generated_at = parse_generated_at(envelope.generated_at)
    if generated_at is None:
        return {
            "status": "ok",
            "findings": [],
            "summary": f"Baseline generated_at is unparseable ({envelope.generated_at!r})",
        }

    now = datetime.now(UTC)
    commits_behind = commits_behind_head(envelope.commit)
    reasons = staleness(envelope, now=now, commits_behind=commits_behind)

    if reasons:
        finding = (
            "Test baseline is stale: "
            + "; ".join(reasons)
            + " -- launch `scripts/refresh_baseline_detached.sh` (timeout-safe wrapper "
            "around refresh_test_baseline.py; a foreground refresh is killed at the "
            "10-min bash cap, #2066)"
        )
        logger.warning(finding)
        return {"status": "warning", "findings": [finding], "summary": finding}

    age = now - generated_at
    summary = f"Test baseline is fresh ({age.days} days old)"
    logger.info(summary)
    return {"status": "ok", "findings": [], "summary": summary}
