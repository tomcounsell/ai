"""Query API for analytics data.

Provides functions to query historical metrics from SQLite and live
counters from Redis. All functions return sensible defaults (empty
lists, zero counts) when the database is empty or missing.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "data" / "analytics.db"
_SQLITE_TIMEOUT = 5


def _get_connection() -> sqlite3.Connection | None:
    """Get a SQLite connection, or None if the database does not exist."""
    if not _DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(_DB_PATH), timeout=_SQLITE_TIMEOUT)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.warning("[analytics-query] Failed to connect to SQLite: %s", e)
        return None


def query_metrics(
    name: str,
    start_time: float | None = None,
    end_time: float | None = None,
    dimensions_filter: dict[str, Any] | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Query raw metric events from SQLite.

    Args:
        name: Metric name to query.
        start_time: Unix timestamp for range start (inclusive).
        end_time: Unix timestamp for range end (inclusive).
        dimensions_filter: Optional dict to filter by dimension values.
        limit: Maximum rows to return.

    Returns:
        List of dicts with keys: timestamp, name, value, dimensions.
    """
    try:
        conn = _get_connection()
        if conn is None:
            return []

        try:
            query = "SELECT timestamp, name, value, dimensions FROM metrics WHERE name = ?"
            params: list[Any] = [name]

            if start_time is not None:
                query += " AND timestamp >= ?"
                params.append(start_time)
            if end_time is not None:
                query += " AND timestamp <= ?"
                params.append(end_time)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            results = []
            for row in rows:
                dims = json.loads(row["dimensions"]) if row["dimensions"] else None
                # Apply dimensions filter if specified
                if dimensions_filter and dims:
                    if not all(dims.get(k) == v for k, v in dimensions_filter.items()):
                        continue
                elif dimensions_filter and not dims:
                    continue

                results.append(
                    {
                        "timestamp": row["timestamp"],
                        "name": row["name"],
                        "value": row["value"],
                        "dimensions": dims,
                    }
                )
            return results
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[analytics-query] query_metrics failed: %s", e)
        return []


def query_daily_summary(name: str, days: int = 30) -> list[dict]:
    """Query daily aggregated summaries for a metric.

    Args:
        name: Metric name to aggregate.
        days: Number of days to look back.

    Returns:
        List of dicts with keys: date, count, total, avg.
    """
    try:
        conn = _get_connection()
        if conn is None:
            return []

        try:
            cutoff = time.time() - (days * 86400)
            query = """
                SELECT
                    date(timestamp, 'unixepoch') as date,
                    COUNT(*) as count,
                    SUM(value) as total,
                    AVG(value) as avg
                FROM metrics
                WHERE name = ? AND timestamp >= ?
                GROUP BY date(timestamp, 'unixepoch')
                ORDER BY date DESC
            """
            rows = conn.execute(query, (name, cutoff)).fetchall()
            return [
                {
                    "date": row["date"],
                    "count": row["count"],
                    "total": round(row["total"], 4),
                    "avg": round(row["avg"], 4),
                }
                for row in rows
            ]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[analytics-query] query_daily_summary failed: %s", e)
        return []


def query_metric_total(name: str, days: int = 1) -> float:
    """Get the total value of a metric over the last N days.

    Args:
        name: Metric name.
        days: Number of days to look back.

    Returns:
        Sum of values, or 0.0 if no data.
    """
    try:
        conn = _get_connection()
        if conn is None:
            return 0.0

        try:
            cutoff = time.time() - (days * 86400)
            row = conn.execute(
                "SELECT COALESCE(SUM(value), 0) as total "
                "FROM metrics WHERE name = ? AND timestamp >= ?",
                (name, cutoff),
            ).fetchone()
            return round(float(row["total"]), 4)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[analytics-query] query_metric_total failed: %s", e)
        return 0.0


def query_metric_count(name: str, days: int = 1) -> int:
    """Get the count of metric events over the last N days.

    Args:
        name: Metric name.
        days: Number of days to look back.

    Returns:
        Count of events, or 0 if no data.
    """
    try:
        conn = _get_connection()
        if conn is None:
            return 0

        try:
            cutoff = time.time() - (days * 86400)
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM metrics WHERE name = ? AND timestamp >= ?",
                (name, cutoff),
            ).fetchone()
            return int(row["cnt"])
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[analytics-query] query_metric_count failed: %s", e)
        return 0


def list_metric_names() -> list[str]:
    """List all distinct metric names in the database.

    Returns:
        Sorted list of metric names.
    """
    try:
        conn = _get_connection()
        if conn is None:
            return []

        try:
            rows = conn.execute("SELECT DISTINCT name FROM metrics ORDER BY name").fetchall()
            return [row["name"] for row in rows]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[analytics-query] list_metric_names failed: %s", e)
        return []
