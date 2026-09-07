"""Unit tests for the no-slots-configured branch of the pm-briefings dispatcher.

After issue #1306, the ``_load_slots()`` shim that synthesized a single
``morning`` slot from legacy ``pm_briefing.angles + pm_briefing.schedule``
keys is gone. The dispatcher now reads ``pm_briefing.slots`` directly. A
project with ``pm_briefing.enabled = true`` but no ``slots`` (or an empty
list) must:

- log a warning naming the project slug
- record a ``skipped`` result with ``reason="no_slots"``
- continue to the next project (not crash, not loop)

This file replaces the deleted
``tests/unit/reflections/test_pm_briefings_legacy_config_migration.py`` --
the legacy-shape synthesis path no longer exists, so its tests are dead.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from reflections import pm_briefings as briefing

pytestmark = [pytest.mark.unit]


def _enabled_no_slots_project():
    return {
        "slug": "test-no-slots",
        "machine": "TestMachine",
        "telegram": {"groups": {"PM: Test": {"chat_id": -1}}},
        "pm_briefing": {
            "enabled": True,
            "timezone": "UTC",
            "target_groups": ["PM: Test"],
            # No "slots" key -- this is the case the deleted shim used to
            # paper over.
        },
    }


def _enabled_empty_slots_project():
    return {
        "slug": "test-empty-slots",
        "machine": "TestMachine",
        "telegram": {"groups": {"PM: Test": {"chat_id": -1}}},
        "pm_briefing": {
            "enabled": True,
            "timezone": "UTC",
            "target_groups": ["PM: Test"],
            "slots": [],  # explicit empty list
        },
    }


def _enabled_legacy_shape_project():
    return {
        "slug": "test-legacy-shape",
        "machine": "TestMachine",
        "telegram": {"groups": {"PM: Test": {"chat_id": -1}}},
        "pm_briefing": {
            "enabled": True,
            "timezone": "UTC",
            "target_groups": ["PM: Test"],
            # Legacy shape that the deleted shim used to synthesize from.
            # Post-cutover, this is treated as "no slots configured".
            "schedule": "08:30",
            "angles": {"include": ["merges"]},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "project_factory",
    [
        _enabled_no_slots_project,
        _enabled_empty_slots_project,
        _enabled_legacy_shape_project,
    ],
    ids=["no_slots_key", "empty_slots_list", "legacy_angles_schedule_shape"],
)
async def test_no_slots_logs_warning_and_skips(project_factory, caplog):
    """A pm-briefing-enabled project without slots is warned + skipped."""
    project = project_factory()

    with (
        patch.object(briefing, "_resolve_machine", return_value="TestMachine"),
        patch.object(briefing, "load_local_projects", return_value=[project]),
    ):
        with caplog.at_level("WARNING", logger="reflections.pm_briefings"):
            result = await briefing.run()

    # Warning was logged with the project slug.
    warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any(project["slug"] in msg and "no slots" in msg for msg in warning_messages), (
        f"expected a 'no slots' warning naming {project['slug']!r}, got {warning_messages}"
    )

    # Result is recorded as a skipped no_slots entry.
    assert result["results"][project["slug"]] == {
        "status": "skipped",
        "reason": "no_slots",
    }

    # Summary counts: not a success, not a failure (skipped).
    assert result["summary"]["succeeded"] == 0
    assert result["summary"]["failed"] == 0


def test_load_slots_function_is_gone():
    """The legacy ``_load_slots`` shim must be removed (no synthesis path)."""
    assert not hasattr(briefing, "_load_slots"), (
        "reflections.pm_briefings._load_slots must be removed -- the legacy "
        "angles+schedule synthesis path is gone (issue #1306)"
    )
