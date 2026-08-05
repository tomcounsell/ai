"""Dashboard data provider for analytics.

Issue #1245: cost/turn aggregations are derived from AgentSession Popoto
fields rather than the analytics metrics ledger. The legacy
`session.cost_usd` / `session.turns` emit sites lived inside the
in-process SDK path which is unreachable in production (the worker uses
the harness path). Sums now come from `AgentSession.total_cost_usd` and
`AgentSession.turn_count` directly. Session-count metrics
(`session.started`, `session.completed`) and memory metrics still flow
through the metrics ledger.
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def _enumerate_completed_sessions() -> list:
    """Every AgentSession with status="completed", from one class-set scan.

    Enumerates through the shared seam (issue #2519) rather than
    `query.filter(status="completed")`: cost and turn totals computed off the
    `status` secondary index silently under-report whenever the index has lost
    records whose hashes are intact.

    The scan is the expensive part of the summary, so it is paid once and every
    window is cut from the result. Returns an empty list on any Popoto failure.
    """
    try:
        from models.session_enumeration import enumerate_sessions

        return enumerate_sessions(("completed",))
    except Exception as e:
        logger.warning("[analytics-dashboard] Popoto query failed: %s", e)
        return []


def _completed_at(session) -> float | None:
    """Epoch seconds a session completed at, or None when unreadable."""
    try:
        return session.completed_at.timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def _completed_within(sessions: list, days: int) -> list:
    """The subset of ``sessions`` that completed inside the last ``days`` days.

    Filtering by `completed_at` happens in Python because Popoto's
    `query.filter` does not support range comparisons. A record with an
    unreadable timestamp is skipped, matching `_sum_cost_and_turns`.
    """
    if days <= 0:
        return []
    cutoff = time.time() - days * 86400
    return [s for s in sessions if (_completed_at(s) or 0) >= cutoff]


def _sum_cost_and_turns(sessions: list) -> tuple[float, int]:
    """Sum total_cost_usd and turn_count across a list of AgentSession rows.

    Per-record errors (non-numeric fields, missing attrs) are skipped.
    """
    sum_cost = 0.0
    sum_turns = 0
    for s in sessions:
        try:
            sum_cost += float(s.total_cost_usd or 0.0)
            sum_turns += int(s.turn_count or 0)
        except (TypeError, ValueError, AttributeError):
            continue
    return (sum_cost, sum_turns)


def get_analytics_summary() -> dict[str, Any]:
    """Get analytics summary for dashboard.json.

    Returns:
        Dict with keys for sessions (started/completed), cost, turns,
        and memory metrics — each with today and 7d variants.
        Returns zero values if analytics database is missing or empty.

    Issue #1245: cost and turn aggregations now derive from AgentSession
    Popoto fields (`total_cost_usd`, `turn_count`). Session-count and
    memory metrics still come from the analytics ledger via
    `query_metric_count`.
    """
    try:
        from analytics.query import query_metric_count

        sessions_started_today = query_metric_count("session.started", days=1)
        sessions_started_7d = query_metric_count("session.started", days=7)
        sessions_completed_today = query_metric_count("session.completed", days=1)
        sessions_completed_7d = query_metric_count("session.completed", days=7)

        # One scan, two windows: today is a strict subset of the week, so the
        # 1d cut comes off the 7d result rather than off a second scan (#2122).
        week_sessions = _completed_within(_enumerate_completed_sessions(), days=7)
        today_sessions = _completed_within(week_sessions, days=1)
        cost_today, turns_today = _sum_cost_and_turns(today_sessions)
        cost_7d, turns_7d = _sum_cost_and_turns(week_sessions)

        # Average turns per completed session (avoid division by zero)
        turns_avg_today = (
            round(turns_today / sessions_completed_today, 1) if sessions_completed_today else 0.0
        )
        turns_avg_7d = round(turns_7d / sessions_completed_7d, 1) if sessions_completed_7d else 0.0

        memory_recalls_today = query_metric_count("memory.recall_attempt", days=1)
        memory_recalls_7d = query_metric_count("memory.recall_attempt", days=7)
        memory_extractions_today = query_metric_count("memory.extraction", days=1)
        memory_extractions_7d = query_metric_count("memory.extraction", days=7)

        return {
            "sessions_started_today": sessions_started_today,
            "sessions_started_7d": sessions_started_7d,
            "sessions_completed_today": sessions_completed_today,
            "sessions_completed_7d": sessions_completed_7d,
            "cost_today_usd": cost_today,
            "cost_7d_usd": cost_7d,
            "turns_today": float(turns_today),
            "turns_7d": float(turns_7d),
            "turns_avg_today": turns_avg_today,
            "turns_avg_7d": turns_avg_7d,
            "memory_recalls_today": memory_recalls_today,
            "memory_recalls_7d": memory_recalls_7d,
            "memory_extractions_today": memory_extractions_today,
            "memory_extractions_7d": memory_extractions_7d,
        }
    except Exception as e:
        logger.warning("[analytics-dashboard] Failed to get analytics summary: %s", e)
        return {
            "sessions_started_today": 0,
            "sessions_started_7d": 0,
            "sessions_completed_today": 0,
            "sessions_completed_7d": 0,
            "cost_today_usd": 0.0,
            "cost_7d_usd": 0.0,
            "turns_today": 0.0,
            "turns_7d": 0.0,
            "turns_avg_today": 0.0,
            "turns_avg_7d": 0.0,
            "memory_recalls_today": 0,
            "memory_recalls_7d": 0,
            "memory_extractions_today": 0,
            "memory_extractions_7d": 0,
        }
