"""Data access layer for the improvement-controller dashboard partials (#3177).

Read-only. All functions are synchronous (``def``, not ``async def``) because
Popoto uses synchronous Redis calls and FastAPI runs sync handlers in a
threadpool.

**Renders only what this build writes.** Two views, both backed by
``ImprovementEvidence``, plus the provisional-assumptions list:

- **Coverage** — how much the system is actually observing, so a rate has a
  denominator. A falling correction count with a falling scan count is not an
  improvement, and this is the panel that makes the difference visible.
- **Intervention burden** — how often a human had to step in, split by
  classification, with the raw counts published beside anything normalized.

Cases, hypotheses, rejected experiments, spend, release lineage, and the
paused/inconclusive/reconciliation-required renderings deliberately do not
appear. Nothing writes those records yet, and six permanently empty tiles is
not a dashboard — each one arrives with the lane that first writes it (3 for
intents and reservations, 5 for cases and hypotheses, 6 for releases).

**Two things this module will never show.** Experiment count and merged-patch
count are activity, not improvement, and presenting either as improvement is
the specific dishonesty ``docs/plans/recursive-self-improvement.md`` names.
There is no function here that returns them.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

#: Default window for the dashboard panels, in days. Shorter than the evidence
#: TTL (30 days) so the panels show a settled window rather than one whose tail
#: is being eaten by expiry while you look at it.
DEFAULT_WINDOW_DAYS = 14

#: How many evidence rows a panel reads. Bounded; the panels are summaries.
READ_LIMIT = 1000

#: Classifications shown in the intervention-burden breakdown, in the order a
#: reader should meet them: the one that matters most first.
BURDEN_ORDER = ("architectural", "scope", "preference", "clarification", "unknown")


def _within(row, cutoff: datetime) -> bool:
    stamp = getattr(row, "created_at", None)
    if not isinstance(stamp, datetime):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp >= cutoff


def _rows(project_key: str, window_days: int) -> list:
    from models.improvement_evidence import ImprovementEvidence

    try:
        rows = ImprovementEvidence.recent(project_key, limit=READ_LIMIT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: evidence read failed for %s: %s", project_key, exc)
        return []
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    return [r for r in rows if _within(r, cutoff)]


def get_coverage(project_key: str = "valor", window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """What the system observed in the window, and how recently it last looked.

    ``last_collected_at`` is the honest health signal for this panel: if the
    collection tick stopped running, every count below is a count of nothing
    and the panel says so rather than showing a comforting zero.
    """
    rows = _rows(project_key, window_days)

    by_kind: dict[str, int] = {}
    for row in rows:
        kind = getattr(row, "kind", None) or "other"
        by_kind[kind] = by_kind.get(kind, 0) + 1

    coverage_rows = [
        r for r in rows if (getattr(r, "source_ref", "") or "").startswith("coverage:")
    ]
    latest_coverage = coverage_rows[0] if coverage_rows else None

    last_collected_at = None
    if rows:
        stamps = [getattr(r, "created_at", None) for r in rows]
        stamps = [s for s in stamps if isinstance(s, datetime)]
        if stamps:
            last_collected_at = max(stamps)

    return {
        "project_key": project_key,
        "window_days": window_days,
        "total": len(rows),
        "by_kind": by_kind,
        "ticks_observed": len(coverage_rows),
        "latest_coverage_detail": getattr(latest_coverage, "detail", None),
        "latest_coverage_text": getattr(latest_coverage, "text", None),
        "last_collected_at": last_collected_at,
        "collector_silent": last_collected_at is None,
    }


def get_intervention_burden(
    project_key: str = "valor", window_days: int = DEFAULT_WINDOW_DAYS
) -> dict:
    """How often a human had to step in, and of what kind.

    The raw counts are published beside the share, deliberately. A share on its
    own hides the case that matters most here: fewer corrections because fewer
    sessions ran, or because the collector stopped, reads identically to fewer
    corrections because the work got better.
    """
    rows = _rows(project_key, window_days)
    corrections = [r for r in rows if getattr(r, "kind", None) == "correction"]

    counts = {name: 0 for name in BURDEN_ORDER}
    for row in corrections:
        name = getattr(row, "classification", None) or "unknown"
        counts[name] = counts.get(name, 0) + 1

    total = len(corrections)
    breakdown = []
    for name in BURDEN_ORDER:
        count = counts.get(name, 0)
        breakdown.append(
            {
                "classification": name,
                "count": count,
                "share": (count / total) if total else None,
            }
        )

    recent = []
    for row in corrections[:10]:
        recent.append(
            {
                "classification": getattr(row, "classification", None) or "unknown",
                "session_id": getattr(row, "source_session_id", None),
                "text": (getattr(row, "text", None) or "")[:180],
                "created_at": getattr(row, "created_at", None),
            }
        )

    return {
        "project_key": project_key,
        "window_days": window_days,
        "total_corrections": total,
        "architectural": counts.get("architectural", 0),
        "unclassified": counts.get("unknown", 0),
        "breakdown": breakdown,
        "recent": recent,
        # An empty window is not evidence of a low burden. Say which it is.
        "no_evidence_yet": total == 0,
    }


def get_provisional_assumptions(
    project_key: str = "valor", window_days: int = DEFAULT_WINDOW_DAYS
) -> list[dict]:
    """Things the system decided to proceed on without resolving.

    The controller asks no human anything, so an unresolved uncertainty becomes
    a recorded assumption rather than a question in someone's queue. Surfacing
    them is the whole compensating control: an assumption nobody can see is
    indistinguishable from a fact.
    """
    from models.improvement_investigation import ImprovementInvestigation

    try:
        rows = list(ImprovementInvestigation.query.filter(project_key=project_key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: investigation read failed: %s", exc)
        return []

    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    out = []
    for row in rows:
        assumption = getattr(row, "provisional_assumption", None)
        if not assumption or not _within(row, cutoff):
            continue
        out.append(
            {
                "assumption": assumption,
                "uncertainty": getattr(row, "uncertainty", None),
                "kind": getattr(row, "kind", None),
                "state": getattr(row, "state", None),
                "expires_at": getattr(row, "expires_at", None),
                "created_at": getattr(row, "created_at", None),
            }
        )
    out.sort(key=lambda d: d["created_at"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out
