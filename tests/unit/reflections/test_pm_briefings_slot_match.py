"""Unit tests for the slot-match helper used by pm-briefings dispatch.

Covers HH:MM matching within the 5-minute window, TZ awareness via
``_now_in_project_tz``, and edge-of-day boundaries (HH:58 -> next-hour
match).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from reflections import pm_audio_briefing as briefing

pytestmark = [pytest.mark.unit]


def test_match_inclusive_lower_edge():
    assert briefing._slot_match(datetime(2026, 4, 30, 8, 30), "08:30") is True


def test_match_inclusive_three_min_after():
    assert briefing._slot_match(datetime(2026, 4, 30, 8, 33), "08:30") is True


def test_match_inclusive_four_min_after():
    assert briefing._slot_match(datetime(2026, 4, 30, 8, 34), "08:30") is True


def test_no_match_five_min_after():
    # 08:35 is the start of the NEXT 5-min window; no longer matches 08:30.
    assert briefing._slot_match(datetime(2026, 4, 30, 8, 35), "08:30") is False


def test_no_match_one_min_before():
    assert briefing._slot_match(datetime(2026, 4, 30, 8, 29), "08:30") is False


def test_hour_rollover_58_to_02():
    # schedule="00:58" matches now=01:02 because 58 <= 62 < 63 (slot is 58..62)
    assert briefing._slot_match(datetime(2026, 4, 30, 1, 2), "00:58") is True


def test_hour_rollover_56_to_00():
    assert briefing._slot_match(datetime(2026, 4, 30, 1, 0), "00:56") is True


def test_invalid_schedule_returns_false():
    assert briefing._slot_match(datetime.now(), "not-a-schedule") is False
    assert briefing._slot_match(datetime.now(), "") is False


def test_implausible_hour_does_not_crash():
    # "25:00" parses (no validation in slot_match by design; caller config
    # validation should reject it). Just assert it doesn't raise -- the
    # absolute-minute arithmetic returns whatever True/False it computes.
    result = briefing._slot_match(datetime.now(), "25:00")
    assert result in (True, False)


def test_now_in_project_tz_uses_project_tz():
    project = {"pm_briefing": {"timezone": "America/Los_Angeles"}}
    now = briefing._now_in_project_tz(project)
    assert now.tzinfo is not None
    assert str(now.tzinfo) == "America/Los_Angeles"


def test_now_in_project_tz_falls_back_to_utc_on_bad_tz():
    project = {"pm_briefing": {"timezone": "Not/A/Real-Zone"}}
    now = briefing._now_in_project_tz(project)
    assert now.tzinfo == ZoneInfo("UTC")
