"""Unit tests for the machine-ownership filter in pm-briefings dispatch.

The ``run()`` entry point filters projects by ``project.machine ==
_resolve_machine()`` BEFORE dispatching slots. Foreign-machine projects must
produce zero slot dispatches; an empty ``_resolve_machine()`` result must
produce zero dispatches (defensive) regardless of project.machine values.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from reflections import pm_audio_briefing as briefing

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _project(slug, machine):
    return {
        "slug": slug,
        "machine": machine,
        "telegram": {"groups": {"PM: Test": {"chat_id": -1}}},
        "pm_briefing": {
            "enabled": True,
            "schedule": "08:30",
            "timezone": "UTC",
            "target_groups": ["PM: Test"],
            "angles": {},
        },
    }


async def test_owned_project_dispatches():
    with (
        patch.object(briefing, "_resolve_machine", return_value="MachineA"),
        patch.object(briefing, "load_local_projects", return_value=[_project("alpha", "MachineA")]),
        patch.object(briefing, "_run_slot") as _rs,
    ):
        _rs.return_value = {"status": "ok", "slot": "morning", "date_iso": "2026-04-30"}
        result = await briefing.run()

    assert _rs.called
    assert result["summary"]["considered"] == 1
    assert result["summary"]["succeeded"] == 1
    assert result["summary"]["failed"] == 0


async def test_foreign_machine_skipped():
    with (
        patch.object(briefing, "_resolve_machine", return_value="MachineA"),
        patch.object(
            briefing,
            "load_local_projects",
            return_value=[_project("beta", "MachineB"), _project("gamma", "MachineC")],
        ),
        patch.object(briefing, "_run_slot") as _rs,
    ):
        result = await briefing.run()

    # No slots dispatched for foreign-machine projects
    assert not _rs.called
    assert result["summary"]["considered"] == 0
    assert result["summary"]["succeeded"] == 0
    assert result["summary"]["failed"] == 0


async def test_empty_machine_skips_all():
    """If scutil ComputerName lookup fails (returns ""), no projects dispatch."""
    with (
        patch.object(briefing, "_resolve_machine", return_value=""),
        patch.object(briefing, "load_local_projects", return_value=[_project("alpha", "MachineA")]),
        patch.object(briefing, "_run_slot") as _rs,
    ):
        result = await briefing.run()

    assert not _rs.called
    assert result["summary"]["considered"] == 0


async def test_disabled_project_skipped():
    proj = _project("alpha", "MachineA")
    proj["pm_briefing"]["enabled"] = False
    with (
        patch.object(briefing, "_resolve_machine", return_value="MachineA"),
        patch.object(briefing, "load_local_projects", return_value=[proj]),
        patch.object(briefing, "_run_slot") as _rs,
    ):
        result = await briefing.run()

    assert not _rs.called
    assert result["summary"]["considered"] == 0
