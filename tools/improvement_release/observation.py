"""Observation-window measurement for a release (#3218, lane 6).

An exposed release is watched for ``window_days`` and compared against the
``baseline_window_days`` before its merge. Both spans are measured over
``ImprovementEvidence`` rows with :func:`measure`; :func:`compare_windows`
scores the pair as ``held``, ``regressed``, or ``undetermined``.

The rate is architectural corrections per coverage tick. Charter §11 says a
falling correction count is no improvement when detection fell with it, so a
window whose coverage ticks per day dropped below
:data:`DETECTION_DECLINE_RATIO` of the baseline's is ``undetermined``
(``DETECTION_DECLINED``), as is a window with no coverage at all
(``ZERO_DENOMINATOR``). Raw counts and denominators travel beside every rate.

**Truncation.** ``ImprovementEvidence.recent`` is capped and newest-first
(:data:`READ_LIMIT`), so a busy span can lose its oldest rows without any
error. :func:`measure` reports ``truncated=True`` when the read returned
``limit`` rows and the oldest of them is still inside the window; a truncated
span is never scored (``EVIDENCE_TRUNCATED``), because an undercounted
baseline would read as ``regressed``.

**Expiry.** Evidence rows expire after :data:`EVIDENCE_TTL_DAYS` days
(``ImprovementEvidence.Meta.ttl``; a test pins the two together). The
lifecycle refuses to freeze a baseline whose oldest rows have expired and
scores a window whose early rows have expired as ``undetermined``
(``EVIDENCE_EXPIRED``); this module measures what is there and says how
much of the span the read reached.

**Noise band (Resolved Question 3).** The baseline rate carries a Wilson
score interval at 95% derived from its own counts, treating the rate as the
proportion of coverage ticks that produced an architectural correction. The
window is ``held`` when its rate is at or under the baseline's Wilson upper
bound and ``regressed`` when it rises past it. A ratio above 1 (more
architectural corrections than ticks) is clamped to 1 for the band, which
makes every rate ``held`` against it; that is the honest answer, since the
proportion model has no noise estimate for such a span. The constants below
are the interval's inputs.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

from models.improvement_evidence import ImprovementEvidence
from tools.improvement_release.rows import aware

logger = logging.getLogger(__name__)

#: The metrics an observation plan may name, and the keys :func:`measure` returns.
OBSERVATION_METRICS: tuple[str, ...] = (
    "corrections_total",
    "corrections_architectural",
    "coverage_ticks",
    "architectural_correction_rate",
)

#: How many evidence rows one measurement reads, newest first. Pinned equal
#: to ``ui/data/improvement.py::READ_LIMIT`` by a test rather than imported
#: from it, so the dashboard can import this module without a cycle.
READ_LIMIT = 1000

#: Evidence rows expire after this many days. Pinned to
#: ``ImprovementEvidence.Meta.ttl`` by ``test_evidence_ttl_days_matches_model``.
EVIDENCE_TTL_DAYS = 30

#: A window whose coverage ticks per day fall below this fraction of the
#: baseline's is scored ``undetermined`` rather than ``held`` (charter §11).
DETECTION_DECLINE_RATIO = 0.8

#: Wilson score interval inputs: two-sided 95% confidence.
WILSON_CONFIDENCE = 0.95
WILSON_Z = 1.959963984540054

#: The evidence kind counted as a correction and the classification counted
#: as architectural (``models/improvement_evidence.py``).
CORRECTION_KIND = "correction"
ARCHITECTURAL_CLASSIFICATION = "architectural"

#: ``source_ref`` prefix of a coverage tick (``ui/data/improvement.py::get_coverage``).
COVERAGE_PREFIX = "coverage:"

VERDICT_HELD = "held"
VERDICT_REGRESSED = "regressed"
VERDICT_UNDETERMINED = "undetermined"

REASON_TRUNCATED = "EVIDENCE_TRUNCATED"
REASON_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
REASON_ZERO_DENOMINATOR = "ZERO_DENOMINATOR"
REASON_DETECTION_DECLINED = "DETECTION_DECLINED"


def _span_days(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds(), 0.0) / 86400.0


def _rate(numerator: int, denominator: int) -> float | None:
    return (numerator / denominator) if denominator else None


def _empty(start: datetime, end: datetime, *, unavailable: bool, truncated: bool) -> dict:
    return {
        "start": start,
        "end": end,
        "days": _span_days(start, end),
        "corrections_total": 0,
        "corrections_architectural": 0,
        "coverage_ticks": 0,
        "architectural_correction_rate": None,
        "truncated": truncated,
        "unavailable": unavailable,
        "rows_read": 0,
    }


def measure(project_key: str, start: datetime, end: datetime, *, limit: int = READ_LIMIT) -> dict:
    """Count corrections and coverage ticks for ``project_key`` over ``[start, end)``.

    Reads ``ImprovementEvidence.recent(project_key, limit=limit)`` (the read
    the dashboard performs) and filters by ``created_at``. Returns the four
    :data:`OBSERVATION_METRICS` plus ``start``, ``end``, ``days``,
    ``truncated`` (the read hit ``limit`` before reaching ``start``), and
    ``unavailable`` (the read raised; logged, never propagated).
    """
    start, end = aware(start), aware(end)
    try:
        rows = ImprovementEvidence.recent(project_key, limit=limit)
    except Exception as exc:  # noqa: BLE001 -- the dashboard pattern: unavailable, never a crash
        logger.warning(
            "observation.measure: evidence read failed for %s: %s: %s",
            project_key,
            type(exc).__name__,
            exc,
        )
        return _empty(start, end, unavailable=True, truncated=False)

    result = _empty(start, end, unavailable=False, truncated=False)
    result["rows_read"] = len(rows)
    for row in rows:
        stamp = aware(getattr(row, "created_at", None))
        if stamp is None or not (start <= stamp < end):
            continue
        if getattr(row, "kind", None) == CORRECTION_KIND:
            result["corrections_total"] += 1
            if getattr(row, "classification", None) == ARCHITECTURAL_CLASSIFICATION:
                result["corrections_architectural"] += 1
        if (getattr(row, "source_ref", None) or "").startswith(COVERAGE_PREFIX):
            result["coverage_ticks"] += 1
    result["architectural_correction_rate"] = _rate(
        result["corrections_architectural"], result["coverage_ticks"]
    )
    if rows and len(rows) >= limit:
        oldest = aware(getattr(rows[-1], "created_at", None))
        result["truncated"] = oldest is not None and oldest > start
    return result


def wilson_upper(successes: int, trials: int, *, z: float = WILSON_Z) -> float | None:
    """Upper bound of the Wilson score interval for ``successes / trials``.

    ``None`` on zero trials. A ratio above 1 is clamped to 1 (see the module
    docstring).
    """
    if trials <= 0:
        return None
    p = min(successes / trials, 1.0)
    if p >= 1.0:
        return 1.0
    z2 = z * z
    denominator = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denominator
    half = (z / denominator) * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))
    return min(center + half, 1.0)


def _side(window: dict, days: float) -> dict:
    """One side of ``deltas``: the raw counts, the rate, and per-day normalizations."""
    counts = {name: window.get(name) for name in OBSERVATION_METRICS}
    per_day = {
        name: (counts[name] / days if days > 0 and counts[name] is not None else None)
        for name in ("corrections_total", "corrections_architectural", "coverage_ticks")
    }
    return {**counts, "days": days, "per_day": per_day, "truncated": bool(window.get("truncated"))}


def compare_windows(
    baseline: dict, window: dict, *, window_days: float, baseline_days: float
) -> dict:
    """Score ``window`` against ``baseline``.

    Returns ``verdict`` (``held`` / ``regressed`` / ``undetermined``), ``reason``
    (``EVIDENCE_TRUNCATED``, ``EVIDENCE_UNAVAILABLE``, ``ZERO_DENOMINATOR``,
    ``DETECTION_DECLINED``, or ``None``), ``detection_declined``, ``deltas``
    (both sides with counts, denominators, and per-day rates, plus
    ``rate_delta``), and ``baseline_band`` (the Wilson interval inputs and
    upper bound). ``held`` needs the window rate at or under the band's upper
    bound and detection not declined.
    """
    baseline_span = float(baseline.get("days") or baseline_days or 0)
    window_span = float(window.get("days") or window_days or 0)
    baseline_side = _side(baseline, baseline_span)
    window_side = _side(window, window_span)
    baseline_rate = baseline_side["architectural_correction_rate"]
    window_rate = window_side["architectural_correction_rate"]
    baseline_ticks = int(baseline.get("coverage_ticks") or 0)
    baseline_arch = int(baseline.get("corrections_architectural") or 0)
    band_upper = wilson_upper(baseline_arch, baseline_ticks)

    baseline_per_day = baseline_side["per_day"]["coverage_ticks"]
    window_per_day = window_side["per_day"]["coverage_ticks"]
    detection_declined = (
        baseline_per_day is not None
        and window_per_day is not None
        and window_per_day < DETECTION_DECLINE_RATIO * baseline_per_day
    )

    result = {
        "verdict": VERDICT_UNDETERMINED,
        "reason": None,
        "detection_declined": bool(detection_declined),
        "deltas": {
            "baseline": baseline_side,
            "window": window_side,
            "rate_delta": (
                window_rate - baseline_rate
                if window_rate is not None and baseline_rate is not None
                else None
            ),
            "coverage_per_day_ratio": (
                window_per_day / baseline_per_day
                if baseline_per_day and window_per_day is not None
                else None
            ),
        },
        "baseline_band": {
            "method": "wilson",
            "confidence": WILSON_CONFIDENCE,
            "z": WILSON_Z,
            "successes": baseline_arch,
            "trials": baseline_ticks,
            "rate": baseline_rate,
            "upper": band_upper,
        },
    }

    if baseline.get("unavailable") or window.get("unavailable"):
        result["reason"] = REASON_UNAVAILABLE
        return result
    if baseline.get("truncated") or window.get("truncated"):
        result["reason"] = REASON_TRUNCATED
        return result
    if baseline_rate is None or window_rate is None or band_upper is None:
        result["reason"] = REASON_ZERO_DENOMINATOR
        return result
    if detection_declined:
        result["reason"] = REASON_DETECTION_DECLINED
        return result
    result["verdict"] = VERDICT_HELD if window_rate <= band_upper else VERDICT_REGRESSED
    return result


def falsifier(window_days: int | float) -> str:
    """The observation that would overturn a ``held`` claim."""
    return (
        f"architectural correction rate over a later {window_days}-day window "
        "exceeds the baseline band with coverage at or above baseline"
    )


__all__ = [
    "ARCHITECTURAL_CLASSIFICATION",
    "CORRECTION_KIND",
    "COVERAGE_PREFIX",
    "DETECTION_DECLINE_RATIO",
    "EVIDENCE_TTL_DAYS",
    "OBSERVATION_METRICS",
    "READ_LIMIT",
    "REASON_DETECTION_DECLINED",
    "REASON_TRUNCATED",
    "REASON_UNAVAILABLE",
    "REASON_ZERO_DENOMINATOR",
    "VERDICT_HELD",
    "VERDICT_REGRESSED",
    "VERDICT_UNDETERMINED",
    "WILSON_CONFIDENCE",
    "WILSON_Z",
    "compare_windows",
    "falsifier",
    "measure",
    "wilson_upper",
]
