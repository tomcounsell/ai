"""Tests for bridge.utc utility module."""

from datetime import UTC, datetime

import pytest

from bridge.utc import to_local, to_unix_ts, utc_iso, utc_now


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


class TestToUnixTs:
    """Regression guard for the naive-datetime/local-time bug.

    Popoto strips tzinfo on save, so every age calculation reads datetimes
    back as naive — but the stored clock values are UTC. Python's default
    datetime.timestamp() on a naive datetime interprets it as machine-local
    time, silently inflating ages by the UTC offset (e.g., 420 min on UTC+7).
    """

    def test_none_returns_none(self):
        assert to_unix_ts(None) is None

    def test_float_passthrough(self):
        assert to_unix_ts(1776683716.5) == 1776683716.5

    def test_int_coerced_to_float(self):
        assert to_unix_ts(1776683716) == 1776683716.0
        assert isinstance(to_unix_ts(1776683716), float)

    def test_aware_datetime(self):
        aware = datetime(2026, 4, 20, 2, 55, 16, tzinfo=UTC)
        assert to_unix_ts(aware) == aware.timestamp()

    def test_naive_datetime_treated_as_utc(self):
        """Naive input must produce the same ts as its UTC-aware twin."""
        naive = datetime(2026, 4, 20, 2, 55, 16)
        aware = naive.replace(tzinfo=UTC)
        assert to_unix_ts(naive) == aware.timestamp()

    def test_naive_is_not_machine_local(self):
        """If this test ever passes on UTC+X host with naive.timestamp(),
        we've regressed: the bug was reporting inflated ages by exactly the
        machine's UTC offset. The helper must return a tz-anchored value."""
        naive = datetime(2026, 4, 20, 2, 55, 16)
        result = to_unix_ts(naive)
        # Compute what a local interpretation would have yielded; on non-UTC
        # hosts this differs from the helper's result.
        local_reading = naive.astimezone().timestamp()
        if naive.astimezone().utcoffset() != naive.replace(tzinfo=UTC).utcoffset():
            assert result != local_reading

    def test_iso_string_with_z(self):
        expected = datetime(2026, 4, 20, 2, 55, 16, tzinfo=UTC).timestamp()
        assert to_unix_ts("2026-04-20T02:55:16Z") == expected

    def test_iso_string_without_tz(self):
        expected = datetime(2026, 4, 20, 2, 55, 16, tzinfo=UTC).timestamp()
        assert to_unix_ts("2026-04-20T02:55:16") == expected

    def test_bad_string_returns_none(self):
        assert to_unix_ts("not a date") is None

    def test_bad_type_returns_none(self):
        assert to_unix_ts(object()) is None
