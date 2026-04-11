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
        Dict with keys: sessions_today, sessions_7d, cost_today_usd,
        cost_7d_usd, daily_sessions. Returns empty/zero values if
        analytics database is missing or empty.
    """
    try:
        from analytics.query import query_daily_summary, query_metric_count, query_metric_total

        sessions_today = query_metric_count("session.started", days=1)
        sessions_7d = query_metric_count("session.started", days=7)
        cost_today = query_metric_total("session.cost_usd", days=1)
        cost_7d = query_metric_total("session.cost_usd", days=7)
        daily_sessions = query_daily_summary("session.started", days=30)

        return {
            "sessions_today": sessions_today,
            "sessions_7d": sessions_7d,
            "cost_today_usd": cost_today,
            "cost_7d_usd": cost_7d,
            "daily_sessions": daily_sessions,
        }
    except Exception as e:
        logger.warning("[analytics-dashboard] Failed to get analytics summary: %s", e)
        return {
            "sessions_today": 0,
            "sessions_7d": 0,
            "cost_today_usd": 0.0,
            "cost_7d_usd": 0.0,
            "daily_sessions": [],
        }
