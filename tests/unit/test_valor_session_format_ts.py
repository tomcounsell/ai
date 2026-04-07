"""Tests for _format_ts timezone labeling in tools/valor_session.py."""

from tools.valor_session import _format_ts


def test_float_timestamp_ends_with_utc() -> None:
    """Float unix timestamp should format with UTC suffix."""
    result = _format_ts(1700000000.0)
    assert result.endswith(" UTC"), f"Expected UTC suffix, got: {repr(result)}"


def test_float_timestamp_format() -> None:
    """Float unix timestamp should format as YYYY-MM-DD HH:MM:SS UTC."""
    result = _format_ts(1700000000.0)
    assert len(result) == len("2023-11-14 22:13:20 UTC"), f"Unexpected format: {repr(result)}"


def test_iso_string_no_offset_ends_with_utc() -> None:
    """ISO string without timezone offset should be treated as UTC and labeled."""
    result = _format_ts("2026-04-07T05:49:00")
    assert result.endswith(" UTC"), f"Expected UTC suffix, got: {repr(result)}"
    assert result == "2026-04-07 05:49:00 UTC"


def test_iso_string_with_utc_offset_ends_with_utc() -> None:
    """ISO string with +00:00 offset should be labeled UTC."""
    result = _format_ts("2026-04-07T05:49:00+00:00")
    assert result.endswith(" UTC"), f"Expected UTC suffix, got: {repr(result)}"
    assert result == "2026-04-07 05:49:00 UTC"


def test_none_returns_dash() -> None:
    """None input should return the dash placeholder."""
    result = _format_ts(None)
    assert result == "—"


def test_integer_timestamp_ends_with_utc() -> None:
    """Integer unix timestamp should also get UTC suffix."""
    result = _format_ts(1700000000)
    assert result.endswith(" UTC"), f"Expected UTC suffix, got: {repr(result)}"


def test_malformed_string_returns_truncated() -> None:
    """Malformed input falls back to truncated string without UTC label."""
    result = _format_ts("garbage-not-a-date")
    # Falls back to str(ts)[:19] — no UTC label, just raw truncated string
    assert result == "garbage-not-a-date"[:19]
