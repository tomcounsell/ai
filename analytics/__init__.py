"""
Analytics and Metrics Dashboard System

Collect, store, and report on system metrics.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

# Default database location
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "metrics.db")


class MetricsCollector:
    """Collect and store metrics."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database and tables exist."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                value REAL NOT NULL,
                tags TEXT,
                timestamp REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp)")
        conn.commit()
        conn.close()

    def track(self, name: str, value: float, tags: dict | None = None) -> bool:
        """
        Record a metric.

        Args:
            name: Metric name (e.g., 'tool_execution_time', 'memory_usage')
            value: Numeric value
            tags: Optional tags (e.g., {'tool': 'search', 'status': 'success'})

        Returns:
            bool: Whether recording succeeded
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO metrics (name, value, tags, timestamp) VALUES (?, ?, ?, ?)",
                (name, value, json.dumps(tags) if tags else None, time.time()),
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            return False

    def get_metrics(
        self,
        name: str,
        time_range_hours: float = 24,
        aggregation: Literal["avg", "sum", "min", "max", "count"] = "avg",
        group_by_tag: str | None = None,
    ) -> dict:
        """
        Query metrics.

        Args:
            name: Metric name to query
            time_range_hours: Hours of data to include
            aggregation: Aggregation function
            group_by_tag: Tag key to group by

        Returns:
            dict with aggregated metrics
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cutoff = time.time() - (time_range_hours * 3600)

            if aggregation == "avg":
                agg_func = "AVG"
            elif aggregation == "sum":
                agg_func = "SUM"
            elif aggregation == "min":
                agg_func = "MIN"
            elif aggregation == "max":
                agg_func = "MAX"
            else:
                agg_func = "COUNT"

            cursor.execute(
                f"SELECT {agg_func}(value) as result, COUNT(*) as count FROM metrics WHERE name = ? AND timestamp > ?",
                (name, cutoff),
            )
            row = cursor.fetchone()

            result = {
                "name": name,
                "time_range_hours": time_range_hours,
                "aggregation": aggregation,
                "value": row[0] if row[0] is not None else 0,
                "count": row[1],
            }

            conn.close()
            return result

        except Exception as e:
            return {"error": str(e), "name": name}

    def get_recent(self, name: str, limit: int = 100) -> list:
        """Get recent metric values."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value, tags, timestamp FROM metrics WHERE name = ? ORDER BY timestamp DESC LIMIT ?",
                (name, limit),
            )
            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "value": row[0],
                    "tags": json.loads(row[1]) if row[1] else None,
                    "timestamp": row[2],
                }
                for row in rows
            ]
        except Exception:
            return []

    def cleanup(self, retention_days: int = 30) -> int:
        """Remove old metrics."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cutoff = time.time() - (retention_days * 24 * 3600)
            cursor.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            return deleted
        except Exception:
            return 0


class DashboardGenerator:
    """Generate dashboard reports from metrics."""

    def __init__(self, collector: MetricsCollector | None = None):
        self.collector = collector or MetricsCollector()

    def daily_summary(self) -> dict:
        """Generate daily summary report."""
        metrics = {
            "tool_execution_time": self.collector.get_metrics("tool_execution_time", 24, "avg"),
            "tool_success_rate": self.collector.get_metrics("tool_success", 24, "avg"),
            "message_count": self.collector.get_metrics("message_processed", 24, "count"),
            "error_count": self.collector.get_metrics("error", 24, "count"),
            "memory_usage_avg": self.collector.get_metrics("memory_usage", 24, "avg"),
            "memory_usage_max": self.collector.get_metrics("memory_usage", 24, "max"),
        }

        return {
            "report_type": "daily_summary",
            "generated_at": datetime.now().isoformat(),
            "period": "last_24_hours",
            "metrics": metrics,
        }

    def weekly_trends(self) -> dict:
        """Generate weekly trends report."""
        days = []
        for i in range(7):
            start_hours = (6 - i) * 24 + 24
            end_hours = (6 - i) * 24

            # This is a simplification - actual implementation would query each day
            day_metrics = {
                "day": (datetime.now() - timedelta(days=6-i)).strftime("%Y-%m-%d"),
                "tool_executions": self.collector.get_metrics("tool_execution_time", start_hours, "count"),
                "errors": self.collector.get_metrics("error", start_hours, "count"),
            }
            days.append(day_metrics)

        return {
            "report_type": "weekly_trends",
            "generated_at": datetime.now().isoformat(),
            "period": "last_7_days",
            "daily_breakdown": days,
        }

    def to_text(self, report: dict) -> str:
        """Convert report to text format."""
        lines = [
            f"=== {report.get('report_type', 'Report').upper()} ===",
            f"Generated: {report.get('generated_at', 'Unknown')}",
            f"Period: {report.get('period', 'Unknown')}",
            "",
        ]

        if "metrics" in report:
            lines.append("Metrics:")
            for name, data in report["metrics"].items():
                value = data.get("value", "N/A")
                count = data.get("count", 0)
                lines.append(f"  {name}: {value:.2f} ({count} samples)" if isinstance(value, (int, float)) else f"  {name}: {value}")

        if "daily_breakdown" in report:
            lines.append("\nDaily Breakdown:")
            for day in report["daily_breakdown"]:
                lines.append(f"  {day.get('day', 'Unknown')}")

        return "\n".join(lines)

    def to_json(self, report: dict) -> str:
        """Convert report to JSON format."""
        return json.dumps(report, indent=2, default=str)


# Convenience functions
_default_collector: MetricsCollector | None = None


def get_collector() -> MetricsCollector:
    """Get or create default collector."""
    global _default_collector
    if _default_collector is None:
        _default_collector = MetricsCollector()
    return _default_collector


def track_metric(name: str, value: float, tags: dict | None = None) -> bool:
    """Track a metric using default collector."""
    return get_collector().track(name, value, tags)


def get_metrics(
    name: str,
    time_range_hours: float = 24,
    aggregation: Literal["avg", "sum", "min", "max", "count"] = "avg",
) -> dict:
    """Query metrics using default collector."""
    return get_collector().get_metrics(name, time_range_hours, aggregation)


def generate_daily_report() -> str:
    """Generate and format daily report."""
    generator = DashboardGenerator(get_collector())
    report = generator.daily_summary()
    return generator.to_text(report)


if __name__ == "__main__":
    # Demo usage
    collector = MetricsCollector()

    # Track some sample metrics
    collector.track("tool_execution_time", 150, {"tool": "search"})
    collector.track("tool_execution_time", 200, {"tool": "code_execution"})
    collector.track("memory_usage", 45.5)
    collector.track("message_processed", 1)
    collector.track("tool_success", 1.0, {"tool": "search"})

    # Generate report
    generator = DashboardGenerator(collector)
    report = generator.daily_summary()
    print(generator.to_text(report))
