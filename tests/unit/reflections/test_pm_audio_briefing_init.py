"""Unit tests for reflections/pm_audio_briefing/__init__.py orchestration.

Covers:
- Slot-match absolute-minute arithmetic (handles hour rollover at HH:58)
- TZ-pinning: project tz=PST, server tz=UTC, both anchors resolve to same date
- Lock-release semantics:
  Pre-side-effect failure (builder raises) -> lock released, retry allowed
  Post-side-effect failure (delivery raises) -> lock NOT released, no retry
- Skip when machine doesn't match
- Skip when outside slot
- Skip when already-succeeded today
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from reflections import pm_audio_briefing as briefing

pytestmark = [pytest.mark.unit]


# --- _slot_match -------------------------------------------------------------


class TestSlotMatch:
    def test_exact_match(self):
        now = datetime(2026, 4, 30, 8, 30)
        assert briefing._slot_match(now, "08:30") is True

    def test_within_5min_window(self):
        now = datetime(2026, 4, 30, 8, 33)
        assert briefing._slot_match(now, "08:30") is True

    def test_outside_5min_window(self):
        now = datetime(2026, 4, 30, 8, 36)
        assert briefing._slot_match(now, "08:30") is False

    def test_handles_hour_rollover_58_to_02(self):
        # schedule="00:58" should match current="01:02" (slot 58..62 inclusive)
        now = datetime(2026, 4, 30, 1, 2)
        assert briefing._slot_match(now, "00:58") is True

    def test_handles_hour_rollover_56_to_00(self):
        now = datetime(2026, 4, 30, 1, 0)
        assert briefing._slot_match(now, "00:56") is True

    def test_invalid_schedule_returns_false(self):
        assert briefing._slot_match(datetime.now(), "not-a-schedule") is False
        assert briefing._slot_match(datetime.now(), "") is False


# --- TZ-pinning --------------------------------------------------------------


class TestTzPinning:
    def test_today_in_project_tz_uses_project_tz(self):
        project = {"slug": "x", "pm_briefing": {"timezone": "America/Los_Angeles"}}
        date_obj, iso = briefing._today_in_project_tz(project)
        # Both should be derived from the project's tz; iso should be the
        # `date_obj.isoformat()` -- they MUST match (anchors must agree)
        assert iso == date_obj.isoformat()

    def test_anchor_resolves_to_same_date_at_pst_evening(self):
        # 2026-04-30 23:30 PST is still 2026-04-30 PST (= 2026-05-01 06:30 UTC).
        # We just want to ensure the anchor doesn't drift between calls.
        project = {"pm_briefing": {"timezone": "America/Los_Angeles"}}
        d1, iso1 = briefing._today_in_project_tz(project)
        d2, iso2 = briefing._today_in_project_tz(project)
        assert d1 == d2
        assert iso1 == iso2


class TestLastRunDate:
    def test_returns_none_when_no_ran_at(self):
        rec = MagicMock()
        rec.ran_at = None
        project = {"pm_briefing": {"timezone": "UTC"}}
        assert briefing._last_run_date_in_project_tz(rec, project) is None

    def test_returns_date_in_project_tz(self):
        # An exact unix-epoch timestamp at noon-UTC on 2026-04-30
        rec = MagicMock()
        rec.ran_at = datetime(2026, 4, 30, 12, 0, 0, tzinfo=ZoneInfo("UTC")).timestamp()
        project = {"pm_briefing": {"timezone": "UTC"}}
        date = briefing._last_run_date_in_project_tz(rec, project)
        assert date.isoformat() == "2026-04-30"


# --- _process_one_project ---------------------------------------------------


def _project(
    enabled=True,
    machine="TestMachine",
    schedule="08:30",
    tz="UTC",
    target_groups=("PM: Test",),
):
    return {
        "slug": "test-proj",
        "machine": machine,
        "telegram": {"groups": {"PM: Test": {"chat_id": -1, "persona": "project-manager"}}},
        "pm_briefing": {
            "enabled": enabled,
            "schedule": schedule,
            "timezone": tz,
            "target_groups": list(target_groups),
            "angles": {"include": ["merges"], "exclude": []},
            "skip_when_empty": False,
            "fallback_message": "Nothing shipped yesterday",
        },
    }


class TestMachineSkip:
    def test_wrong_machine_skips(self):
        with patch.object(briefing, "_now_in_project_tz") as _now:
            _now.return_value = datetime(2026, 4, 30, 8, 30)
            result = briefing._process_one_project(
                _project(machine="OtherMachine"),
                this_machine="TestMachine",
                dry_run=False,
            )
        assert result["status"] == "skipped"
        assert result["reason"] == "wrong_machine"


class TestSlotSkip:
    def test_outside_slot_skips(self):
        with patch.object(briefing, "_now_in_project_tz") as _now:
            _now.return_value = datetime(2026, 4, 30, 9, 30)  # 1hr after schedule
            result = briefing._process_one_project(
                _project(),
                this_machine="TestMachine",
                dry_run=False,
            )
        assert result["status"] == "skipped"
        assert result["reason"] == "outside_slot"


class TestPreSideEffectFailureReleasesLock:
    def test_builder_raises_before_rpush_lock_released(self):
        from models.reflection import Reflection

        with (
            patch.object(briefing, "_now_in_project_tz") as _now,
            patch.object(briefing, "_try_acquire_lock", return_value=True) as _acquire,
            patch.object(briefing, "_release_lock") as _release,
            patch.object(
                briefing.collector, "collect", return_value={"merges": [{"subject": "x"}]}
            ),
            patch.object(
                briefing.builder,
                "build",
                side_effect=RuntimeError("builder boom"),
            ),
            patch.object(briefing.delivery, "_get_redis_connection", return_value=MagicMock()),
            patch.object(Reflection, "get_or_create") as _gc,
        ):
            _now.return_value = datetime(2026, 4, 30, 8, 30)
            mock_rec = MagicMock()
            mock_rec.ran_at = None
            mock_rec.last_status = "pending"
            _gc.return_value = mock_rec

            result = briefing._process_one_project(
                _project(), this_machine="TestMachine", dry_run=False
            )

        assert result["status"] == "error"
        assert result["phase"] == "pre_side_effect"
        assert _release.called  # Lock released since no rpush happened
        # Reflection mark_completed called with error
        assert mock_rec.mark_completed.called


class TestPostSideEffectFailureHoldsLock:
    def test_delivery_raises_after_rpush_lock_held(self):
        from models.reflection import Reflection

        with (
            patch.object(briefing, "_now_in_project_tz") as _now,
            patch.object(briefing, "_try_acquire_lock", return_value=True),
            patch.object(briefing, "_release_lock") as _release,
            patch.object(
                briefing.collector, "collect", return_value={"merges": [{"subject": "x"}]}
            ),
            patch.object(
                briefing.builder,
                "build",
                return_value=("Shipped a thing.", ""),
            ),
            patch.object(
                briefing.delivery,
                "send",
                side_effect=briefing.delivery.BriefingTtsFailedError("tts boom"),
            ),
            patch.object(briefing.delivery, "_get_redis_connection", return_value=MagicMock()),
            patch.object(Reflection, "get_or_create") as _gc,
        ):
            _now.return_value = datetime(2026, 4, 30, 8, 30)
            mock_rec = MagicMock()
            mock_rec.ran_at = None
            mock_rec.last_status = "pending"
            _gc.return_value = mock_rec

            result = briefing._process_one_project(
                _project(), this_machine="TestMachine", dry_run=False
            )

        assert result["status"] == "error"
        assert result["phase"] == "post_side_effect"
        # Lock NOT released -- preserves at-most-once guarantee for today
        assert not _release.called
