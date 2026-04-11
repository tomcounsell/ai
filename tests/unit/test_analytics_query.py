"""Tests for analytics.query -- query functions and empty database handling."""

import json
import sqlite3
import time

import pytest


@pytest.fixture
def populated_db(tmp_path, monkeypatch):
    """Create a temp analytics database with test data."""
    db_path = tmp_path / "analytics.db"
    monkeypatch.setattr("analytics.query._DB_PATH", db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            name TEXT NOT NULL,
            value REAL NOT NULL,
            dimensions TEXT
        )
        """
    )
    conn.execute("CREATE INDEX idx_metrics_name_ts ON metrics (name, timestamp)")

    now = time.time()
    # Insert test data across several days
    test_data = [
        (now - 3600, "session.started", 1.0, json.dumps({"session_type": "pm"})),
        (now - 7200, "session.started", 1.0, json.dumps({"session_type": "dev"})),
        (now - 86400, "session.started", 1.0, None),  # yesterday
        (now - 86400 * 3, "session.started", 1.0, None),  # 3 days ago
        (now - 3600, "session.cost_usd", 0.05, json.dumps({"session_id": "s1"})),
        (now - 7200, "session.cost_usd", 0.10, json.dumps({"session_id": "s2"})),
    ]
    conn.executemany(
        "INSERT INTO metrics (timestamp, name, value, dimensions) VALUES (?, ?, ?, ?)",
        test_data,
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    """Point analytics query at a non-existent database."""
    db_path = tmp_path / "nonexistent.db"
    monkeypatch.setattr("analytics.query._DB_PATH", db_path)
    return db_path


class TestQueryMetrics:
    def test_query_by_name(self, populated_db):
        from analytics.query import query_metrics

        results = query_metrics("session.started")
        assert len(results) == 4
        assert all(r["name"] == "session.started" for r in results)

    def test_query_with_time_range(self, populated_db):
        from analytics.query import query_metrics

        now = time.time()
        results = query_metrics("session.started", start_time=now - 86400)
        # Should only get today's events (2), not yesterday or 3 days ago
        assert len(results) == 2

    def test_query_with_dimensions_filter(self, populated_db):
        from analytics.query import query_metrics

        results = query_metrics(
            "session.started",
            dimensions_filter={"session_type": "pm"},
        )
        assert len(results) == 1

    def test_query_empty_database(self, empty_db):
        from analytics.query import query_metrics

        results = query_metrics("session.started")
        assert results == []


class TestQueryDailySummary:
    def test_daily_summary(self, populated_db):
        from analytics.query import query_daily_summary

        results = query_daily_summary("session.started", days=7)
        assert len(results) >= 1
        # Each result should have date, count, total, avg
        for r in results:
            assert "date" in r
            assert "count" in r
            assert "total" in r
            assert "avg" in r

    def test_daily_summary_empty(self, empty_db):
        from analytics.query import query_daily_summary

        results = query_daily_summary("session.started", days=7)
        assert results == []


class TestQueryMetricTotal:
    def test_total(self, populated_db):
        from analytics.query import query_metric_total

        total = query_metric_total("session.cost_usd", days=1)
        assert total == 0.15  # 0.05 + 0.10

    def test_total_empty(self, empty_db):
        from analytics.query import query_metric_total

        total = query_metric_total("session.cost_usd", days=1)
        assert total == 0.0


class TestQueryMetricCount:
    def test_count(self, populated_db):
        from analytics.query import query_metric_count

        count = query_metric_count("session.started", days=1)
        assert count == 2  # 2 events today

    def test_count_empty(self, empty_db):
        from analytics.query import query_metric_count

        count = query_metric_count("session.started", days=1)
        assert count == 0


class TestListMetricNames:
    def test_list_names(self, populated_db):
        from analytics.query import list_metric_names

        names = list_metric_names()
        assert "session.started" in names
        assert "session.cost_usd" in names

    def test_list_names_empty(self, empty_db):
        from analytics.query import list_metric_names

        names = list_metric_names()
        assert names == []
