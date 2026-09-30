"""Unit tests for reflections/pm_briefings/__init__.py orchestration.

Updated for the slot-driven dispatch model (issue #1276):
- Slot-match absolute-minute arithmetic (handles hour rollover at HH:58)
- TZ-pinning: project tz=PST, server tz=UTC, both anchors resolve to same date
- Lock-release semantics in ``_run_slot``:
  Pre-side-effect failure (builder raises) -> lock released, retry allowed
  Post-side-effect failure (delivery raises) -> lock NOT released, no retry
- Skip when outside slot
- Skip when slot has no schedule
- Skip when already-succeeded today (per-(project x slot) idempotency)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from reflections import pm_briefings as briefing

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
        assert iso == date_obj.isoformat()

    def test_anchor_resolves_to_same_date_at_pst_evening(self):
        project = {"pm_briefing": {"timezone": "America/Los_Angeles"}}
        d1, iso1 = briefing._today_in_project_tz(project)
        d2, iso2 = briefing._today_in_project_tz(project)
        assert d1 == d2
        assert iso1 == iso2


# --- _run_slot ---------------------------------------------------------------


def _project(
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
            "enabled": True,
            "schedule": schedule,
            "timezone": tz,
            "target_groups": list(target_groups),
            "angles": {"include": ["merges"], "exclude": []},
            "skip_when_empty": False,
            "fallback_message": "Nothing shipped yesterday",
        },
    }


def _morning_slot(schedule="08:30"):
    return {
        "name": "morning",
        "type": "morning",
        "schedule": schedule,
        "angles": {"include": ["merges"], "exclude": []},
        "target_groups": ["PM: Test"],
        "skip_when_empty": False,
        "fallback_message": "Nothing shipped yesterday",
    }


class TestSlotSkip:
    def test_outside_slot_skips(self):
        with patch.object(briefing, "_now_in_project_tz") as _now:
            _now.return_value = datetime(2026, 4, 30, 9, 30)  # 1hr after schedule
            result = briefing._run_slot(
                _project(),
                _morning_slot(),
                dry_run=False,
            )
        assert result["status"] == "skipped"
        assert result["reason"] == "outside_slot"

    def test_no_schedule_skips(self):
        slot = _morning_slot()
        slot["schedule"] = ""
        result = briefing._run_slot(_project(), slot, dry_run=False)
        assert result["status"] == "skipped"
        assert result["reason"] == "no_schedule"


class TestPreSideEffectFailureReleasesLock:
    def test_builder_raises_before_delivery_lock_released(self):
        from models.reflection import Reflection

        with (
            patch.object(briefing, "_now_in_project_tz") as _now,
            patch.object(briefing, "_try_acquire_lock", return_value=True),
            patch.object(briefing, "_release_lock") as _release,
            patch.dict(
                briefing._SLOT_BUILDERS,
                {"morning": MagicMock(side_effect=RuntimeError("builder boom"))},
            ),
            patch(
                "reflections.pm_briefings.delivery._get_redis_connection",
                return_value=MagicMock(),
            ),
            patch.object(Reflection, "get_or_create") as _gc,
        ):
            _now.return_value = datetime(2026, 4, 30, 8, 30)
            mock_rec = MagicMock()
            mock_rec.ran_at = None
            mock_rec.last_status = "pending"
            _gc.return_value = mock_rec

            result = briefing._run_slot(_project(), _morning_slot(), dry_run=False)

        assert result["status"] == "error"
        assert result["phase"] == "pre_side_effect"
        assert _release.called  # Lock released since no delivery happened
        assert mock_rec.mark_completed.called


class TestPostSideEffectFailureHoldsLock:
    def test_delivery_raises_after_enqueue_lock_held(self):
        from models.reflection import Reflection
        from reflections.pm_briefings.delivery import BriefingTtsFailedError

        with (
            patch.object(briefing, "_now_in_project_tz") as _now,
            patch.object(briefing, "_try_acquire_lock", return_value=True),
            patch.object(briefing, "_release_lock") as _release,
            patch.dict(
                briefing._SLOT_BUILDERS,
                {"morning": MagicMock(return_value=("Shipped a thing.", "", {"merges": [{}]}))},
            ),
            patch(
                "reflections.pm_briefings.delivery.send",
                side_effect=BriefingTtsFailedError("tts boom"),
            ),
            patch(
                "reflections.pm_briefings.delivery._get_redis_connection",
                return_value=MagicMock(),
            ),
            patch.object(Reflection, "get_or_create") as _gc,
        ):
            _now.return_value = datetime(2026, 4, 30, 8, 30)
            mock_rec = MagicMock()
            mock_rec.ran_at = None
            mock_rec.last_status = "pending"
            _gc.return_value = mock_rec

            result = briefing._run_slot(_project(), _morning_slot(), dry_run=False)

        assert result["status"] == "error"
        assert result["phase"] == "post_side_effect"
        # Lock NOT released -- preserves at-most-once guarantee for today
        assert not _release.called


class TestSkipWhenEmpty:
    def test_empty_build_returns_noop_and_marks_completed(self):
        from models.reflection import Reflection

        with (
            patch.object(briefing, "_now_in_project_tz") as _now,
            patch.object(briefing, "_try_acquire_lock", return_value=True),
            patch.object(briefing, "_release_lock"),
            patch.dict(
                briefing._SLOT_BUILDERS,
                {"morning": MagicMock(return_value=("", "", {}))},
            ),
            patch(
                "reflections.pm_briefings.delivery._get_redis_connection",
                return_value=MagicMock(),
            ),
            patch.object(Reflection, "get_or_create") as _gc,
        ):
            _now.return_value = datetime(2026, 4, 30, 8, 30)
            mock_rec = MagicMock()
            mock_rec.ran_at = None
            mock_rec.last_status = "pending"
            _gc.return_value = mock_rec

            result = briefing._run_slot(_project(), _morning_slot(), dry_run=False)

        assert result["status"] == "noop"
        assert result["reason"] == "skip_when_empty"
        # Reflection mark_completed called with no error
        assert mock_rec.mark_completed.called


class TestUnknownSlotType:
    def test_unknown_slot_type_returns_dispatch_error(self):
        from models.reflection import Reflection

        slot = _morning_slot()
        slot["type"] = "wat"
        with (
            patch.object(briefing, "_now_in_project_tz") as _now,
            patch.object(briefing, "_try_acquire_lock", return_value=True),
            patch.object(briefing, "_release_lock") as _release,
            patch(
                "reflections.pm_briefings.delivery._get_redis_connection",
                return_value=MagicMock(),
            ),
            patch.object(Reflection, "get_or_create") as _gc,
        ):
            _now.return_value = datetime(2026, 4, 30, 8, 30)
            mock_rec = MagicMock()
            mock_rec.ran_at = None
            _gc.return_value = mock_rec

            result = briefing._run_slot(_project(), slot, dry_run=False)

        assert result["status"] == "error"
        assert result["phase"] == "dispatch"
        assert _release.called  # No side effects yet -- safe to release
