"""Dashboard data provider for analytics.

Queries the analytics store and returns data structured for the
dashboard.json endpoint and HTMX partials.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_analytics_summary() -> dict[str, Any]:
    """Get analytics summary for dashboard.json.

    Returns:
        Dict with keys for sessions (started/completed), cost, turns,
        and memory metrics — each with today and 7d variants.
        Returns zero values if analytics database is missing or empty.
    """
    try:
        from analytics.query import query_metric_count, query_metric_total

        sessions_started_today = query_metric_count("session.started", days=1)
        sessions_started_7d = query_metric_count("session.started", days=7)
        sessions_completed_today = query_metric_count("session.completed", days=1)
        sessions_completed_7d = query_metric_count("session.completed", days=7)

        cost_today = query_metric_total("session.cost_usd", days=1)
        cost_7d = query_metric_total("session.cost_usd", days=7)

        turns_today = query_metric_total("session.turns", days=1)
        turns_7d = query_metric_total("session.turns", days=7)

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
            "turns_today": turns_today,
            "turns_7d": turns_7d,
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
