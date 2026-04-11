"""Tests for tools.analytics CLI -- export, summary, rollup commands."""

import json
import sqlite3
import time
from io import StringIO
from unittest.mock import patch

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
    test_data = [
        (now - 3600, "session.started", 1.0, None),
        (now - 7200, "session.cost_usd", 0.05, None),
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
    """Point at a non-existent database."""
    db_path = tmp_path / "nonexistent.db"
    monkeypatch.setattr("analytics.query._DB_PATH", db_path)
    return db_path


class TestExportCommand:
    def test_export_produces_valid_json(self, populated_db):
        import argparse

        from tools.analytics import cmd_export

        args = argparse.Namespace(days=30)
        output = StringIO()
        with patch("sys.stdout", output):
            cmd_export(args)

        result = json.loads(output.getvalue())
        assert "exported_at" in result
        assert "metrics" in result
        assert "session.started" in result["metrics"]

    def test_export_empty_db_produces_valid_json(self, empty_db):
        import argparse

        from tools.analytics import cmd_export

        args = argparse.Namespace(days=30)
        output = StringIO()
        with patch("sys.stdout", output):
            cmd_export(args)

        result = json.loads(output.getvalue())
        assert result["metrics"] == {}


class TestSummaryCommand:
    def test_summary_with_data(self, populated_db):
        import argparse

        from tools.analytics import cmd_summary

        args = argparse.Namespace()
        output = StringIO()
        with patch("sys.stdout", output):
            cmd_summary(args)

        text = output.getvalue()
        assert "Analytics Summary" in text
        assert "session.started" in text

    def test_summary_no_data(self, empty_db):
        import argparse

        from tools.analytics import cmd_summary

        args = argparse.Namespace()
        output = StringIO()
        with patch("sys.stdout", output):
            cmd_summary(args)

        text = output.getvalue()
        assert "No analytics data" in text
