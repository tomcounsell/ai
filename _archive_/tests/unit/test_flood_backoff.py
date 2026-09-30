"""Tests for flood-backoff file persistence in bridge/telegram_bridge.py.

Covers: write/read round-trip, expired file ignored, missing file returns None,
corrupt JSON returns None, stale file (>24h) ignored.
"""

import json
import time
from unittest.mock import patch

import pytest


# We need to import the helpers from telegram_bridge.
# Since they use module-level _FLOOD_BACKOFF_FILE, we patch that.
@pytest.fixture
def flood_backoff_file(tmp_path):
    """Create a temporary flood-backoff file path."""
    return tmp_path / "flood-backoff"


@pytest.fixture
def _patch_flood_file(flood_backoff_file):
    """Patch the flood-backoff file path to use tmp_path."""
    with patch("bridge.telegram_bridge._FLOOD_BACKOFF_FILE", flood_backoff_file):
        yield flood_backoff_file


class TestReadFloodBackoff:
    """Tests for _read_flood_backoff()."""

    def test_missing_file_returns_none(self, _patch_flood_file):
        from bridge.telegram_bridge import _read_flood_backoff

        assert _read_flood_backoff() is None

    def test_empty_file_returns_none(self, _patch_flood_file):
        from bridge.telegram_bridge import _read_flood_backoff

        _patch_flood_file.write_text("")
        assert _read_flood_backoff() is None

    def test_corrupt_json_returns_none(self, _patch_flood_file):
        from bridge.telegram_bridge import _read_flood_backoff

        _patch_flood_file.write_text("{bad json")
        assert _read_flood_backoff() is None

    def test_missing_expiry_ts_returns_none(self, _patch_flood_file):
        from bridge.telegram_bridge import _read_flood_backoff

        _patch_flood_file.write_text(json.dumps({"seconds": 60}))
        assert _read_flood_backoff() is None

    def test_expired_entry_returns_none(self, _patch_flood_file):
        from bridge.telegram_bridge import _read_flood_backoff

        # Write an already-expired timestamp
        data = {"expiry_ts": time.time() - 100, "seconds": 60}
        _patch_flood_file.write_text(json.dumps(data))
        assert _read_flood_backoff() is None
        # File should be cleaned up
        assert not _patch_flood_file.exists()

    def test_valid_future_expiry_returns_timestamp(self, _patch_flood_file):
        from bridge.telegram_bridge import _read_flood_backoff

        future_ts = time.time() + 300
        data = {"expiry_ts": future_ts, "seconds": 300}
        _patch_flood_file.write_text(json.dumps(data))
        result = _read_flood_backoff()
        assert result is not None
        assert abs(result - future_ts) < 1.0

    def test_stale_file_ignored(self, _patch_flood_file):
        """Files older than 24h should be ignored even if expiry_ts is in the future."""
        from bridge.telegram_bridge import _read_flood_backoff

        future_ts = time.time() + 300
        data = {"expiry_ts": future_ts, "seconds": 300}
        _patch_flood_file.write_text(json.dumps(data))

        # Make file appear 25 hours old by patching the staleness check
        with patch("bridge.telegram_bridge._FLOOD_BACKOFF_MAX_AGE_SECONDS", 0):
            result = _read_flood_backoff()
            assert result is None


class TestWriteFloodBackoff:
    """Tests for _write_flood_backoff()."""

    def test_write_creates_file(self, _patch_flood_file):
        from bridge.telegram_bridge import _write_flood_backoff

        _write_flood_backoff(120)
        assert _patch_flood_file.exists()
        data = json.loads(_patch_flood_file.read_text())
        assert data["seconds"] == 120
        assert data["expiry_ts"] > time.time()

    def test_write_read_roundtrip(self, _patch_flood_file):
        from bridge.telegram_bridge import (
            _read_flood_backoff,
            _write_flood_backoff,
        )

        _write_flood_backoff(600)
        result = _read_flood_backoff()
        assert result is not None
        # Should be roughly now + 600
        assert abs(result - (time.time() + 600)) < 5.0


class TestClearFloodBackoff:
    """Tests for _clear_flood_backoff()."""

    def test_clear_removes_file(self, _patch_flood_file):
        from bridge.telegram_bridge import (
            _clear_flood_backoff,
            _write_flood_backoff,
        )

        _write_flood_backoff(300)
        assert _patch_flood_file.exists()
        _clear_flood_backoff()
        assert not _patch_flood_file.exists()

    def test_clear_noop_when_missing(self, _patch_flood_file):
        from bridge.telegram_bridge import _clear_flood_backoff

        # Should not raise
        _clear_flood_backoff()
