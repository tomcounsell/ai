"""Unit tests for the legacy config migration shim in ``_load_slots``.

Existing morning-brief users have ``pm_briefing.angles + pm_briefing.schedule``
but no ``pm_briefing.slots`` key. The shim must synthesize a single
``morning`` slot so they get zero ``projects.json`` edits required.

When ``pm_briefing.slots`` is present, the explicit list MUST take precedence
verbatim.
"""

from __future__ import annotations

import pytest

from reflections import pm_audio_briefing as briefing

pytestmark = [pytest.mark.unit]


def test_legacy_shape_synthesizes_single_morning_slot():
    project = {
        "slug": "legacy",
        "pm_briefing": {
            "enabled": True,
            "schedule": "07:30",
            "timezone": "America/Los_Angeles",
            "angles": {"include": ["merges", "open-bugs"], "exclude": []},
            "target_groups": ["PM: Legacy"],
            "voice": "af_bella",
        },
    }
    slots = briefing._load_slots(project)
    assert len(slots) == 1
    s = slots[0]
    assert s["type"] == "morning"
    assert s["name"] == "morning"
    assert s["schedule"] == "07:30"
    assert s["angles"]["include"] == ["merges", "open-bugs"]
    assert s["target_groups"] == ["PM: Legacy"]
    assert s["voice"] == "af_bella"


def test_explicit_slots_take_precedence():
    project = {
        "slug": "modern",
        "pm_briefing": {
            "enabled": True,
            "schedule": "07:30",  # legacy keys present but ignored
            "angles": {"include": ["merges"]},
            "slots": [
                {
                    "name": "morning",
                    "type": "morning",
                    "schedule": "08:00",
                    "target_groups": ["PM: Modern"],
                },
                {
                    "name": "evening_recap",
                    "type": "daily_log",
                    "schedule": "18:30",
                    "target_groups": ["PM: Modern"],
                    "vault_writer": True,
                },
            ],
        },
    }
    slots = briefing._load_slots(project)
    assert len(slots) == 2
    assert [s["name"] for s in slots] == ["morning", "evening_recap"]
    assert [s["schedule"] for s in slots] == ["08:00", "18:30"]
    assert slots[1]["vault_writer"] is True


def test_no_pm_briefing_returns_empty_list():
    project = {"slug": "x"}
    assert briefing._load_slots(project) == []


def test_pm_briefing_present_but_no_schedule_or_slots_returns_empty():
    project = {"slug": "x", "pm_briefing": {"enabled": True, "angles": {}}}
    assert briefing._load_slots(project) == []
