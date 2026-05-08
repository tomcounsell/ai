"""When a project has ``pm_briefing.enabled=true`` but no ``slots`` key (or
an empty list), the dispatcher logs a warning and skips the project cleanly
without crashing or attempting to synthesize legacy single-morning shape.

Replaces the deleted ``test_pm_briefings_legacy_config_migration.py`` which
covered the now-removed ``_load_slots()`` shim.
"""

from __future__ import annotations

import asyncio
import logging
from unittest import mock

import reflections.pm_briefings as briefing


def _patched_run(project: dict, caplog_setup) -> dict:
    """Run dispatcher with a single eligible project, with all I/O patched."""
    with (
        mock.patch.object(briefing, "_resolve_machine", return_value="testmachine"),
        mock.patch.object(briefing, "load_local_projects", return_value=[project]),
    ):
        return asyncio.run(briefing.run())


def test_no_slots_key_logs_warning_and_skips(caplog) -> None:
    project = {
        "slug": "noslots",
        "machine": "testmachine",
        "pm_briefing": {"enabled": True},  # no `slots` key at all
    }
    caplog.set_level(logging.WARNING, logger="reflections.pm_briefings")

    result = _patched_run(project, caplog)

    assert result["summary"]["considered"] == 1
    assert result["summary"]["succeeded"] == 0
    assert result["summary"]["failed"] == 0
    assert result["results"]["noslots"] == {"status": "skipped", "reason": "no_slots"}
    assert any("no slots configured" in rec.getMessage() for rec in caplog.records)


def test_explicit_empty_slots_list_logs_warning_and_skips(caplog) -> None:
    project = {
        "slug": "emptyslots",
        "machine": "testmachine",
        "pm_briefing": {"enabled": True, "slots": []},  # explicit empty list
    }
    caplog.set_level(logging.WARNING, logger="reflections.pm_briefings")

    result = _patched_run(project, caplog)

    assert result["results"]["emptyslots"] == {"status": "skipped", "reason": "no_slots"}
    assert any("no slots configured" in rec.getMessage() for rec in caplog.records)


def test_legacy_schedule_only_no_longer_synthesizes_morning(caplog) -> None:
    """Legacy ``angles + schedule`` shape with no ``slots`` is now skipped,
    not auto-migrated to a synthetic morning slot. The shim is gone."""
    project = {
        "slug": "legacy",
        "machine": "testmachine",
        "pm_briefing": {
            "enabled": True,
            "schedule": "08:00",
            "angles": {"include": ["foo"]},
            "target_groups": ["Dev: Valor"],
            # no `slots` key
        },
    }
    caplog.set_level(logging.WARNING, logger="reflections.pm_briefings")

    result = _patched_run(project, caplog)

    assert result["results"]["legacy"] == {"status": "skipped", "reason": "no_slots"}
    assert any("no slots configured" in rec.getMessage() for rec in caplog.records)
