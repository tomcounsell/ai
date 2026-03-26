"""Tests for last-connected timestamp persistence in bridge/telegram_bridge.py.

Covers: write/read round-trip, missing file returns None,
invalid content returns None, future timestamp clamped.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture
def last_connected_file(tmp_path):
    """Create a temporary last_connected file path."""
    return tmp_path / "last_connected"


@pytest.fixture
def _patch_last_connected_file(last_connected_file):
    """Patch the last_connected file path to use tmp_path."""
    with patch("bridge.telegram_bridge._LAST_CONNECTED_FILE", last_connected_file):
        yield last_connected_file


class TestReadLastConnected:
    """Tests for _read_last_connected()."""

    def test_missing_file_returns_none(self, _patch_last_connected_file):
        from bridge.telegram_bridge import _read_last_connected

        assert _read_last_connected() is None

    def test_empty_file_returns_none(self, _patch_last_connected_file):
        from bridge.telegram_bridge import _read_last_connected

        _patch_last_connected_file.write_text("")
        assert _read_last_connected() is None

    def test_whitespace_only_returns_none(self, _patch_last_connected_file):
        from bridge.telegram_bridge import _read_last_connected

        _patch_last_connected_file.write_text("   \n  ")
        assert _read_last_connected() is None

    def test_invalid_timestamp_returns_none(self, _patch_last_connected_file):
        from bridge.telegram_bridge import _read_last_connected

        _patch_last_connected_file.write_text("not-a-timestamp")
        assert _read_last_connected() is None

    def test_valid_iso_timestamp(self, _patch_last_connected_file):
        from bridge.telegram_bridge import _read_last_connected

        ts = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
        _patch_last_connected_file.write_text(ts.isoformat())
        result = _read_last_connected()
        assert result is not None
        assert result == ts

    def test_naive_timestamp_gets_utc(self, _patch_last_connected_file):
        from bridge.telegram_bridge import _read_last_connected

        # Write a naive timestamp (no timezone)
        _patch_last_connected_file.write_text("2026-03-25T12:00:00")
        result = _read_last_connected()
        assert result is not None
        assert result.tzinfo is not None

    def test_future_timestamp_clamped(self, _patch_last_connected_file):
        from bridge.telegram_bridge import _read_last_connected

        future = datetime.now(UTC) + timedelta(hours=2)
        _patch_last_connected_file.write_text(future.isoformat())
        result = _read_last_connected()
        assert result is not None
        # Should be clamped to approximately now
        assert result <= datetime.now(UTC) + timedelta(seconds=5)


class TestWriteLastConnected:
    """Tests for _write_last_connected()."""

    def test_write_creates_file(self, _patch_last_connected_file):
        from bridge.telegram_bridge import _write_last_connected

        _write_last_connected()
        assert _patch_last_connected_file.exists()
        content = _patch_last_connected_file.read_text().strip()
        # Should be a valid ISO timestamp
        ts = datetime.fromisoformat(content)
        assert ts is not None

    def test_write_read_roundtrip(self, _patch_last_connected_file):
        from bridge.telegram_bridge import (
            _read_last_connected,
            _write_last_connected,
        )

        _write_last_connected()
        result = _read_last_connected()
        assert result is not None
        # Should be approximately now
        diff = abs((datetime.now(UTC) - result).total_seconds())
        assert diff < 5.0

    def test_write_overwrites_existing(self, _patch_last_connected_file):
        from bridge.telegram_bridge import (
            _read_last_connected,
            _write_last_connected,
        )

        # Write old timestamp
        old_ts = datetime(2026, 1, 1, tzinfo=UTC)
        _patch_last_connected_file.write_text(old_ts.isoformat())

        # Overwrite with current
        _write_last_connected()
        result = _read_last_connected()
        assert result is not None
        assert result > old_ts
