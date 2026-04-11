"""Daily rollup -- aggregate raw metrics and purge old events.

Designed to run as reflections unit 18. Aggregates raw metric events
into daily summaries in Redis and purges raw SQLite events older than
30 days.
"""

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "data" / "analytics.db"
_SQLITE_TIMEOUT = 5
_RETENTION_DAYS = 30
_DAILY_TTL = 30 * 86400


def rollup_daily() -> dict:
    """Aggregate raw events into Redis daily summaries and purge old data.

    Returns:
        Dict with keys: aggregated_days, purged_rows, errors.
    """
    result = {"aggregated_days": 0, "purged_rows": 0, "errors": []}

    if not _DB_PATH.exists():
        logger.info("[analytics-rollup] No analytics database found, nothing to roll up")
        return result

    # Step 1: Aggregate into Redis daily keys
    try:
        conn = sqlite3.connect(str(_DB_PATH), timeout=_SQLITE_TIMEOUT)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT
                    date(timestamp, 'unixepoch') as date,
                    name,
                    COUNT(*) as count,
                    SUM(value) as total
                FROM metrics
                WHERE timestamp >= ?
                GROUP BY date(timestamp, 'unixepoch'), name
                """,
                (time.time() - (_RETENTION_DAYS * 86400),),
            ).fetchall()

            if rows:
                try:
                    from popoto.redis_db import POPOTO_REDIS_DB

                    dates_seen = set()
                    for row in rows:
                        date_str = row["date"]
                        dates_seen.add(date_str)
                        daily_key = f"analytics:daily:{date_str}"
                        # Set the aggregated total
                        POPOTO_REDIS_DB.hset(daily_key, row["name"], str(round(row["total"], 4)))
                        POPOTO_REDIS_DB.hset(daily_key, f"{row['name']}:count", str(row["count"]))
                        POPOTO_REDIS_DB.expire(daily_key, _DAILY_TTL)

                    result["aggregated_days"] = len(dates_seen)
                    logger.info(
                        "[analytics-rollup] Aggregated %d metrics across %d days",
                        len(rows),
                        len(dates_seen),
                    )
                except Exception as e:
                    result["errors"].append(f"Redis aggregation failed: {e}")
                    logger.warning("[analytics-rollup] Redis aggregation failed: %s", e)
        finally:
            conn.close()
    except Exception as e:
        result["errors"].append(f"SQLite read failed: {e}")
        logger.warning("[analytics-rollup] SQLite read failed: %s", e)

    # Step 2: Purge raw events older than retention period
    try:
        conn = sqlite3.connect(str(_DB_PATH), timeout=_SQLITE_TIMEOUT)
        try:
            cutoff = time.time() - (_RETENTION_DAYS * 86400)
            cursor = conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
            purged = cursor.rowcount
            conn.commit()
            result["purged_rows"] = purged
            if purged > 0:
                logger.info(
                    "[analytics-rollup] Purged %d raw events older than %d days",
                    purged,
                    _RETENTION_DAYS,
                )
        finally:
            conn.close()
    except Exception as e:
        result["errors"].append(f"Purge failed: {e}")
        logger.warning("[analytics-rollup] Purge failed: %s", e)

    return result
