"""Metrics collector -- dual-write to SQLite (historical) and Redis (live).

All public functions are best-effort: failures are logged and never propagated.
This module is a pure sink with no reverse dependencies.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# SQLite database path
_DB_DIR = Path(__file__).parent.parent / "data"
_DB_PATH = _DB_DIR / "analytics.db"

# Redis key prefixes
_REDIS_LIVE_PREFIX = "analytics:live:"
_REDIS_DAILY_PREFIX = "analytics:daily:"

# Connection timeout for SQLite (seconds)
_SQLITE_TIMEOUT = 5

# TTL for daily Redis keys (30 days in seconds)
_DAILY_TTL = 30 * 86400


def _get_db_path() -> Path:
    """Return the SQLite database path, creating the directory if needed."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize the database schema if it does not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            name TEXT NOT NULL,
            value REAL NOT NULL,
            dimensions TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_metrics_name_ts
        ON metrics (name, timestamp)
        """
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()


def _write_sqlite(name: str, value: float, dimensions: dict[str, Any] | None, ts: float) -> None:
    """Write a metric event to SQLite. Best-effort."""
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(str(db_path), timeout=_SQLITE_TIMEOUT)
        try:
            _init_db(conn)
            dims_json = json.dumps(dimensions) if dimensions else None
            conn.execute(
                "INSERT INTO metrics (timestamp, name, value, dimensions) VALUES (?, ?, ?, ?)",
                (ts, name, value, dims_json),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[analytics] SQLite write failed for %s: %s", name, e)


def _write_redis(name: str, value: float, dimensions: dict[str, Any] | None, ts: float) -> None:
    """Increment Redis live counter and daily rollup. Best-effort."""
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        # Live counter: HINCRBYFLOAT on analytics:live:{name}
        live_key = f"{_REDIS_LIVE_PREFIX}{name}"
        dim_key = json.dumps(dimensions, sort_keys=True) if dimensions else "_total"
        POPOTO_REDIS_DB.hincrbyfloat(live_key, dim_key, value)

        # Daily rollup: HINCRBYFLOAT on analytics:daily:{date}
        date_str = time.strftime("%Y-%m-%d", time.gmtime(ts))
        daily_key = f"{_REDIS_DAILY_PREFIX}{date_str}"
        POPOTO_REDIS_DB.hincrbyfloat(daily_key, name, value)
        POPOTO_REDIS_DB.expire(daily_key, _DAILY_TTL)
    except Exception as e:
        logger.warning("[analytics] Redis write failed for %s: %s", name, e)


def record_metric(
    name: str,
    value: float,
    dimensions: dict[str, Any] | None = None,
) -> None:
    """Record a metric event to both SQLite and Redis.

    Best-effort: all writes are wrapped in try/except. A failure in
    one storage backend does not affect the other.

    Args:
        name: Dotted metric name (e.g., "session.cost_usd").
        value: Numeric metric value.
        dimensions: Optional dict of dimension key-value pairs.
    """
    # Validate inputs
    if not name or not isinstance(name, str):
        logger.warning("[analytics] record_metric called with invalid name: %r", name)
        return

    if value is None:
        logger.warning("[analytics] record_metric called with None value for %s", name)
        return

    try:
        value = float(value)
    except (TypeError, ValueError):
        logger.warning("[analytics] record_metric: non-numeric value %r for %s", value, name)
        return

    ts = time.time()

    # Write to both backends independently -- each wrapped so one failure
    # does not prevent the other from succeeding
    try:
        _write_sqlite(name, value, dimensions, ts)
    except Exception as e:
        logger.warning("[analytics] SQLite write raised unexpectedly for %s: %s", name, e)

    try:
        _write_redis(name, value, dimensions, ts)
    except Exception as e:
        logger.warning("[analytics] Redis write raised unexpectedly for %s: %s", name, e)
