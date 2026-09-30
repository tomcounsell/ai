"""Unit tests for skip-when-empty behavior in the slot dispatcher.

A slot whose ``build()`` returns ``("", "", {})`` should:
- be reported with ``status="noop"`` and ``reason="skip_when_empty"``
- mark the per-(project x slot) Reflection record completed (no error)
- NOT release the lock (the lock holds for the day; tomorrow's lock has a
  different date suffix)
- NOT enqueue any Telegram payload (no side effects)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from reflections import pm_briefings as briefing

pytestmark = [pytest.mark.unit]


def _project():
    return {
        "slug": "test-proj",
        "machine": "TestMachine",
        "telegram": {"groups": {"PM: Test": {"chat_id": -1}}},
        "pm_briefing": {
            "enabled": True,
            "schedule": "08:30",
            "timezone": "UTC",
            "target_groups": ["PM: Test"],
            "angles": {"include": ["merges"]},
            "skip_when_empty": True,
        },
    }


def _slot(slot_type="morning"):
    return {
        "name": slot_type,
        "type": slot_type,
        "schedule": "08:30",
        "skip_when_empty": True,
    }


def test_empty_build_returns_noop():
    from models.reflection import Reflection

    with (
        patch.object(briefing, "_now_in_project_tz") as _now,
        patch.object(briefing, "_try_acquire_lock", return_value=True),
        patch.object(briefing, "_release_lock") as _release,
        patch.dict(briefing._SLOT_BUILDERS, {"morning": MagicMock(return_value=("", "", {}))}),
        patch(
            "reflections.pm_briefings.delivery._get_redis_connection",
            return_value=MagicMock(),
        ),
        patch(
            "reflections.pm_briefings.delivery.send",
        ) as _send,
        patch.object(Reflection, "get_or_create") as _gc,
    ):
        _now.return_value = datetime(2026, 4, 30, 8, 30)
        mock_rec = MagicMock()
        mock_rec.ran_at = None
        _gc.return_value = mock_rec

        result = briefing._run_slot(_project(), _slot(), dry_run=False)

    assert result["status"] == "noop"
    assert result["reason"] == "skip_when_empty"
    # No delivery
    assert not _send.called
    # Reflection completed (no error)
    assert mock_rec.mark_completed.called
    completed_kwargs = mock_rec.mark_completed.call_args.kwargs
    assert completed_kwargs.get("error") is None
    # Lock NOT released -- holds for the day
    assert not _release.called


def test_log_audit_empty_findings_returns_noop():
    """log_audit slot returns ("", "", {}) when scan finds nothing AND
    skip_when_empty is True -- must be classified noop, not error.
    """
    from models.reflection import Reflection

    with (
        patch.object(briefing, "_now_in_project_tz") as _now,
        patch.object(briefing, "_try_acquire_lock", return_value=True),
        patch.dict(briefing._SLOT_BUILDERS, {"log_audit": MagicMock(return_value=("", "", {}))}),
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

        slot = _slot("log_audit")
        result = briefing._run_slot(_project(), slot, dry_run=False)

    assert result["status"] == "noop"
