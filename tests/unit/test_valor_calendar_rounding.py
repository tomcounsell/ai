"""Tests for valor_calendar rounding functions: round_down_10, round_up_10, current_segment."""

from datetime import UTC, datetime

import pytest

from tools.valor_calendar import (
    _MIN_BLOCK_MINUTES,
    _SEGMENT_MINUTES,
    current_segment,
    round_down_10,
    round_up_10,
)


def dt(hour: int, minute: int, second: int = 0) -> datetime:
    """Helper: build a UTC datetime for a given hour/minute/second."""
    return datetime(2026, 5, 19, hour, minute, second, tzinfo=UTC)


class TestRoundDown10:
    @pytest.mark.parametrize(
        "minute,expected_minute",
        [
            (0, 0),  # already on boundary
            (10, 10),  # exact boundary
            (11, 10),  # 1 past boundary
            (19, 10),  # 9 past boundary, not yet at 20
            (20, 20),  # exact 20-min boundary
            (30, 30),  # half-hour boundary
            (35, 30),  # 5 past 30
            (59, 50),  # near end of hour
        ],
    )
    def test_round_down_10(self, minute, expected_minute):
        result = round_down_10(dt(10, minute, 30))
        assert result.minute == expected_minute
        assert result.second == 0
        assert result.microsecond == 0

    def test_preserves_hour(self):
        result = round_down_10(dt(14, 7))
        assert result.hour == 14

    def test_preserves_date(self):
        d = datetime(2026, 5, 19, 14, 7, 33, tzinfo=UTC)
        result = round_down_10(d)
        assert result.year == 2026
        assert result.month == 5
        assert result.day == 19


class TestRoundUp10:
    @pytest.mark.parametrize(
        "hour,minute,second,expected_hour,expected_minute",
        [
            (10, 0, 0, 10, 0),  # exact boundary: no-op
            (10, 10, 0, 10, 10),  # exact 10-min boundary: no-op
            (10, 11, 0, 10, 20),  # 1 past boundary: ceil to 20
            (10, 19, 0, 10, 20),  # 9 past boundary
            (10, 20, 0, 10, 20),  # exact 20-min boundary: no-op
            (10, 30, 0, 10, 30),  # exact 30: no-op
            (10, 31, 0, 10, 40),  # 1 past 30: ceil to 40
            (10, 59, 1, 11, 0),  # hour rollover (59 + non-zero second)
            (
                10,
                59,
                0,
                11,
                0,
            ),  # 59 with zero second: 59 / 10 = 5 * 10 = 50, not boundary → 60 → rollover
        ],
    )
    def test_round_up_10(self, hour, minute, second, expected_hour, expected_minute):
        result = round_up_10(dt(hour, minute, second))
        assert result.hour == expected_hour, (
            f"Expected hour {expected_hour}, got {result.hour} for {hour}:{minute:02d}:{second:02d}"
        )
        assert result.minute == expected_minute, (
            f"Expected minute {expected_minute}, "
            f"got {result.minute} for {hour}:{minute:02d}:{second:02d}"
        )
        assert result.second == 0

    def test_hour_rollover_no_overflow(self):
        """Hour rollover must not increment beyond 23 (that's midnight, not our concern here)."""
        result = round_up_10(dt(10, 58, 5))
        assert result.hour == 11
        assert result.minute == 0

    def test_returns_datetime_on_boundary_second_zero(self):
        """Exact boundary with second=0 returns the same time."""
        d = dt(14, 20, 0)
        result = round_up_10(d)
        assert result.hour == 14
        assert result.minute == 20
        assert result.second == 0


class TestCurrentSegment:
    def test_segment_length_is_20_minutes(self):
        start, end = current_segment(dt(10, 7))
        assert (end - start).seconds == _MIN_BLOCK_MINUTES * 60

    def test_start_is_rounded_down_to_10(self):
        start, end = current_segment(dt(10, 7))
        assert start.minute == 0

    def test_end_is_start_plus_20(self):
        start, end = current_segment(dt(10, 7))
        assert end.minute == start.minute + 20

    def test_segment_at_hour_boundary(self):
        """Segment starting at exact hour: 10:00 -> (10:00, 10:20)."""
        start, end = current_segment(dt(10, 0))
        assert start.hour == 10
        assert start.minute == 0
        assert end.hour == 10
        assert end.minute == 20

    def test_segment_near_hour_boundary(self):
        """Segment at 10:50 -> (10:50, 11:10) — end crosses the hour."""
        start, end = current_segment(dt(10, 50))
        assert start.hour == 10
        assert start.minute == 50
        assert end.hour == 11
        assert end.minute == 10

    def test_constants(self):
        assert _SEGMENT_MINUTES == 10
        assert _MIN_BLOCK_MINUTES == 20
