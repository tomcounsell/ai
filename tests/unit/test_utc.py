"""Tests for bridge.utc utility module."""

from datetime import UTC, datetime

import pytest

from bridge.utc import to_local, utc_iso, utc_now


class TestUtcNow:
    def test_returns_datetime(self):
        result = utc_now()
        assert isinstance(result, datetime)

    def test_is_tz_aware(self):
        result = utc_now()
        assert result.tzinfo is not None

    def test_is_utc(self):
        result = utc_now()
        assert result.tzinfo == UTC


class TestToLocal:
    def test_converts_utc_to_local(self):
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        result = to_local(ts)
        assert result.tzinfo is not None
        # The UTC offset should differ from +00:00 on most machines,
        # but the absolute time must be the same
        assert result == ts

    def test_raises_on_naive_datetime(self):
        naive = datetime(2026, 1, 15, 12, 0, 0)
        with pytest.raises(ValueError, match="tz-aware"):
            to_local(naive)

    def test_raises_on_none(self):
        with pytest.raises((TypeError, AttributeError)):
            to_local(None)


class TestUtcIso:
    def test_returns_string(self):
        result = utc_iso()
        assert isinstance(result, str)

    def test_ends_with_z(self):
        result = utc_iso()
        assert result.endswith("Z")

    def test_no_plus_offset(self):
        result = utc_iso()
        assert "+00:00" not in result

    def test_is_parseable(self):
        result = utc_iso()
        # Should be parseable back to datetime (replace Z with +00:00 for fromisoformat)
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
