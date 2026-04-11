"""Tests for analytics.collector -- record_metric and best-effort pattern."""

import json
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Redirect analytics to a temp directory."""
    db_path = tmp_path / "analytics.db"
    monkeypatch.setattr("analytics.collector._DB_DIR", tmp_path)
    monkeypatch.setattr("analytics.collector._DB_PATH", db_path)
    return db_path


class TestRecordMetric:
    """Test record_metric writes to SQLite."""

    def test_basic_write(self, temp_db):
        """record_metric should write a row to SQLite."""
        from analytics.collector import record_metric

        # Mock Redis to avoid requiring a live connection
        with patch("analytics.collector._write_redis"):
            record_metric("test.metric", 42.0, {"key": "value"})

        # Verify SQLite write
        conn = sqlite3.connect(str(temp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM metrics").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["name"] == "test.metric"
        assert rows[0]["value"] == 42.0
        dims = json.loads(rows[0]["dimensions"])
        assert dims["key"] == "value"

    def test_write_without_dimensions(self, temp_db):
        """record_metric should work without dimensions."""
        from analytics.collector import record_metric

        with patch("analytics.collector._write_redis"):
            record_metric("test.simple", 1.0)

        conn = sqlite3.connect(str(temp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM metrics").fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0]["dimensions"] is None

    def test_multiple_writes(self, temp_db):
        """Multiple record_metric calls should create multiple rows."""
        from analytics.collector import record_metric

        with patch("analytics.collector._write_redis"):
            record_metric("test.a", 1.0)
            record_metric("test.b", 2.0)
            record_metric("test.a", 3.0)

        conn = sqlite3.connect(str(temp_db))
        count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        conn.close()

        assert count == 3

    def test_wal_mode_enabled(self, temp_db):
        """SQLite should use WAL journal mode."""
        from analytics.collector import record_metric

        with patch("analytics.collector._write_redis"):
            record_metric("test.wal", 1.0)

        conn = sqlite3.connect(str(temp_db))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        assert mode == "wal"


class TestBestEffortPattern:
    """Test that failures are swallowed, never propagated."""

    def test_invalid_name_does_not_raise(self, temp_db):
        """Empty name should log and return, not raise."""
        from analytics.collector import record_metric

        record_metric("", 1.0)  # Should not raise
        record_metric(None, 1.0)  # Should not raise

    def test_none_value_does_not_raise(self, temp_db):
        """None value should log and return, not raise."""
        from analytics.collector import record_metric

        record_metric("test.none", None)  # Should not raise

    def test_non_numeric_value_does_not_raise(self, temp_db):
        """Non-numeric value should log and return, not raise."""
        from analytics.collector import record_metric

        record_metric("test.bad", "not_a_number")  # Should not raise

    def test_sqlite_failure_does_not_propagate(self, temp_db):
        """A SQLite failure should not raise to the caller."""
        from analytics.collector import record_metric

        with patch("analytics.collector._write_sqlite", side_effect=Exception("DB gone")):
            with patch("analytics.collector._write_redis"):
                record_metric("test.broken", 1.0)  # Should not raise

    def test_redis_failure_does_not_propagate(self, temp_db):
        """A Redis failure should not prevent SQLite write or raise."""
        from analytics.collector import record_metric

        with patch("analytics.collector._write_redis", side_effect=Exception("Redis gone")):
            record_metric("test.redis_fail", 1.0)  # Should not raise

        # SQLite write should have succeeded
        conn = sqlite3.connect(str(temp_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM metrics WHERE name='test.redis_fail'"
        ).fetchone()[0]
        conn.close()
        assert count == 1


class TestRedisWrite:
    """Test Redis write behavior."""

    def test_redis_hincrbyfloat_called(self, temp_db):
        """_write_redis should call HINCRBYFLOAT on live and daily keys."""
        from analytics.collector import _write_redis

        mock_redis = MagicMock()
        with patch.dict(
            "sys.modules",
            {"popoto": MagicMock(), "popoto.redis_db": MagicMock(POPOTO_REDIS_DB=mock_redis)},
        ):
            _write_redis("test.metric", 1.0, {"k": "v"}, time.time())

        # Should have called hincrbyfloat twice (live + daily)
        assert mock_redis.hincrbyfloat.call_count == 2
        # Should have called expire once (for daily key)
        assert mock_redis.expire.call_count == 1
