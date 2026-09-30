"""End-to-end integration test for pm-briefings slot dispatch.

Configures three projects:
- Project A (owned, two slots: morning + daily_log)
- Project B (foreign machine; must produce zero deliveries)
- Project C (disabled; must produce zero deliveries)

Asserts: only Project A produces slot dispatches; both of A's matching
slots run; foreign + disabled projects are silently skipped.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from reflections import pm_briefings as briefing

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _project(slug, machine, *, enabled=True, slots=None):
    return {
        "slug": slug,
        "machine": machine,
        "telegram": {"groups": {"PM: Test": {"chat_id": -1}}},
        "pm_briefing": {
            "enabled": enabled,
            "schedule": "08:30" if not slots else None,
            "timezone": "UTC",
            "target_groups": ["PM: Test"],
            "angles": {"include": ["merges"]},
            "slots": slots,
        },
    }


async def test_three_projects_dispatch_only_owned_enabled():
    project_a = _project(
        "alpha",
        "MachineA",
        slots=[
            {
                "name": "morning",
                "type": "morning",
                "schedule": "08:30",
                "target_groups": ["PM: Test"],
            },
            {
                "name": "evening_recap",
                "type": "daily_log",
                "schedule": "08:30",  # also matches in this test
                "target_groups": ["PM: Test"],
            },
        ],
    )
    project_b = _project("beta", "MachineB")  # foreign
    project_c = _project("gamma", "MachineA", enabled=False)  # disabled

    dispatched: list[tuple[str, str]] = []

    def fake_run_slot(project, slot_config, *, dry_run):
        dispatched.append((project["slug"], slot_config["name"]))
        return {
            "status": "ok",
            "slot": slot_config["name"],
            "date_iso": "2026-04-30",
        }

    with (
        patch.object(briefing, "_resolve_machine", return_value="MachineA"),
        patch.object(
            briefing,
            "load_local_projects",
            return_value=[project_a, project_b, project_c],
        ),
        patch.object(briefing, "_run_slot", side_effect=fake_run_slot),
    ):
        result = await briefing.run()

    # Only project_a's slots should have dispatched.
    assert {(s, n) for s, n in dispatched} == {
        ("alpha", "morning"),
        ("alpha", "evening_recap"),
    }
    assert result["summary"]["considered"] == 1  # one owned-and-enabled project
    assert result["summary"]["succeeded"] == 2
    assert result["summary"]["failed"] == 0

    # Aggregate per-project record carries date_iso so cross-tz Wednesday-LA
    # vs Tuesday-LA rows don't overwrite each other (per critique).
    project_records = result["projects"]
    assert all("date_iso" in rec for rec in project_records)
    slugs_slots = {(rec["slug"], rec["slot"]) for rec in project_records}
    assert slugs_slots == {("alpha", "morning"), ("alpha", "evening_recap")}


async def test_one_slot_failure_does_not_abort_others():
    project = _project(
        "alpha",
        "MachineA",
        slots=[
            {
                "name": "morning",
                "type": "morning",
                "schedule": "08:30",
                "target_groups": ["PM: Test"],
            },
            {
                "name": "evening_recap",
                "type": "daily_log",
                "schedule": "08:30",
                "target_groups": ["PM: Test"],
            },
        ],
    )

    calls: list[str] = []

    def fake_run_slot(project, slot_config, *, dry_run):
        calls.append(slot_config["name"])
        if slot_config["name"] == "morning":
            raise RuntimeError("morning blew up")
        return {"status": "ok", "slot": slot_config["name"], "date_iso": "2026-04-30"}

    with (
        patch.object(briefing, "_resolve_machine", return_value="MachineA"),
        patch.object(briefing, "load_local_projects", return_value=[project]),
        patch.object(briefing, "_run_slot", side_effect=fake_run_slot),
    ):
        result = await briefing.run()

    # Both slots were attempted -- one failure didn't abort the other.
    assert calls == ["morning", "evening_recap"]
    assert result["summary"]["succeeded"] == 1
    assert result["summary"]["failed"] == 1
    assert result["status"] == "partial"
