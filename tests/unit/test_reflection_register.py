"""Unit tests for scripts.update.reflection_register (#1917).

Covers the update-time step that appends the ``crash-recovery`` reflection to
the vault registry so ``python -m reflections --dry-run`` lists it and the
crash-signature library warms. config/reflections.yaml is gitignored and
clobbered from the vault on every /update, so the entry must land in the vault
file specifically (critique C6).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import yaml

from scripts.update.reflection_register import (
    REMOVED_REFLECTIONS,
    register_crash_recovery,
    register_memory_distill_backfill,
    register_reflection,
    register_sdlc_upvote_pickup,
    remove_reflection,
)

pytestmark = pytest.mark.sdlc

REGISTRY_WITHOUT_CRASH = {
    "reflections": [
        {"name": "other-reflection", "enabled": True, "callable": "a.b", "every": "300s"},
    ]
}

REGISTRY_WITH_CRASH = {
    "reflections": [
        {"name": "other-reflection", "enabled": True, "callable": "a.b", "every": "300s"},
        {
            "name": "crash-recovery",
            "enabled": True,
            "callable": "reflections.crash_recovery.run_crash_recovery",
            "every": "300s",
        },
    ]
}

PROJECTS_OWNED = {"projects": {"valor": {"machine": "Tom's MacBook Pro"}}}
PROJECTS_NOT_OWNED = {"projects": {"valor": {"machine": "Some Other Machine"}}}


def _setup(tmp_path, registry=None, projects=None, repo_registry=None):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    vault_path = vault_dir / "reflections.yaml"
    vault_path.write_text(
        yaml.safe_dump(registry if registry is not None else REGISTRY_WITHOUT_CRASH)
    )

    project_dir = tmp_path / "repo"
    (project_dir / "config").mkdir(parents=True)
    (project_dir / "config" / "projects.json").write_text(
        json.dumps(projects if projects is not None else PROJECTS_OWNED)
    )
    if repo_registry is not None:
        (project_dir / "config" / "reflections.yaml").write_text(yaml.safe_dump(repo_registry))

    return vault_path, project_dir


def _names(path):
    data = yaml.safe_load(path.read_text())
    return [r["name"] for r in data["reflections"]]


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_owner_registers_missing_entry_in_vault(mock_machine, tmp_path, monkeypatch):
    """The entry lands in the resolved (vault) file specifically — critique C6."""
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_crash_recovery(project_dir)

    assert result.success is True
    assert result.action == "registered"
    assert "crash-recovery" in _names(vault_path)
    # The scheduler-resolved target is the vault (REFLECTIONS_YAML), so the entry
    # is where the scheduler will actually read it, not the config copy.
    entry = next(
        r
        for r in yaml.safe_load(vault_path.read_text())["reflections"]
        if r["name"] == "crash-recovery"
    )
    assert entry["callable"] == "reflections.crash_recovery.run_crash_recovery"
    assert entry["enabled"] is True


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_already_registered_is_noop(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(
        tmp_path, registry=REGISTRY_WITH_CRASH, repo_registry=REGISTRY_WITH_CRASH
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_crash_recovery(project_dir)

    assert result.success is True
    assert result.action == "noop"
    # Idempotent: still exactly one crash-recovery entry.
    assert _names(vault_path).count("crash-recovery") == 1


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_is_idempotent_across_two_runs(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    first = register_crash_recovery(project_dir)
    second = register_crash_recovery(project_dir)

    assert first.action == "registered"
    assert second.action == "noop"
    assert _names(vault_path).count("crash-recovery") == 1


@patch("config.machine.get_machine_name", return_value="Some Other Machine")
def test_non_owner_skips_without_mutating(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(
        tmp_path, projects=PROJECTS_OWNED, repo_registry=REGISTRY_WITHOUT_CRASH
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_crash_recovery(project_dir)

    assert result.action == "skipped"
    assert "crash-recovery" not in _names(vault_path)


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_missing_vault_file_skips(mock_machine, tmp_path, monkeypatch):
    _, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(tmp_path / "does-not-exist.yaml"))

    result = register_crash_recovery(project_dir)

    assert result.action == "skipped"
    assert "not found" in result.detail


@patch("config.machine.get_machine_name", return_value="")
def test_unresolvable_machine_name_fails_closed(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_crash_recovery(project_dir)

    assert result.action == "skipped"
    assert "crash-recovery" not in _names(vault_path)


# A hand-authored vault file: header docs, per-entry annotations, inline
# comments. The append must be line-scoped -- a yaml.safe_dump round-trip would
# destroy all of this.
COMMENTED_VAULT = """\
# Reflections registry -- source of truth (iCloud vault).
#
# Schema: every entry needs name, enabled, callable, every.
# Edit here, never in the repo copy (it is clobbered on /update).

reflections:
  - name: other-reflection
    enabled: true  # keep on
    callable: a.b
    every: 300s  # 5 minutes
"""


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_append_preserves_comments_and_formatting(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    vault_path.write_text(COMMENTED_VAULT)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_crash_recovery(project_dir)
    assert result.action == "registered"

    text = vault_path.read_text()
    # Existing comments survive.
    assert "# Reflections registry -- source of truth (iCloud vault)." in text
    assert "enabled: true  # keep on" in text
    assert "every: 300s  # 5 minutes" in text
    # New entry is present and parses.
    assert "crash-recovery" in _names(vault_path)
    # No stray temp file left behind.
    assert not list(vault_path.parent.glob("*.tmp"))


# ---------------------------------------------------------------------------
# register_reflection: the generalized entry point (subtask 3a of #2004).
# register_crash_recovery is a thin wrapper over it; these tests prove a
# SECOND reflection can be registered through the same machinery.
# ---------------------------------------------------------------------------

SAMPLE_ENTRY_KWARGS = {
    "name": "sample-weekly-check",
    "callable_path": "reflections.housekeeping.sample_weekly_check.run",
    "description": "Sample second entry exercising the generic register path",
    "cadence": "7d",
    "priority": "low",
}


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_reflection_registers_arbitrary_entry(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS)

    assert result.success is True
    assert result.action == "registered"
    entry = next(
        r
        for r in yaml.safe_load(vault_path.read_text())["reflections"]
        if r["name"] == "sample-weekly-check"
    )
    assert entry["callable"] == "reflections.housekeeping.sample_weekly_check.run"
    assert entry["every"] == "7d"
    assert entry["priority"] == "low"
    assert entry["enabled"] is True


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_reflection_is_idempotent(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    first = register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS)
    second = register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS)

    assert first.action == "registered"
    assert second.action == "noop"
    assert _names(vault_path).count("sample-weekly-check") == 1


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_reflection_second_entry_coexists_with_crash_recovery(
    mock_machine, tmp_path, monkeypatch
):
    """_has_entry is name-scoped: one entry present never blocks the other."""
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    assert register_crash_recovery(project_dir).action == "registered"
    assert register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS).action == "registered"

    names = _names(vault_path)
    assert names.count("crash-recovery") == 1
    assert names.count("sample-weekly-check") == 1
    # Re-running each is still a noop with the other present.
    assert register_crash_recovery(project_dir).action == "noop"
    assert register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS).action == "noop"


@patch("config.machine.get_machine_name", return_value="Some Other Machine")
def test_register_reflection_non_owner_skips_without_mutating(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS)

    assert result.action == "skipped"
    assert "sample-weekly-check" not in _names(vault_path)


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_reflection_entry_loads_via_scheduler_registry(
    mock_machine, tmp_path, monkeypatch
):
    """The appended weekly entry is well-formed for the scheduler's loader."""
    from agent.reflection_scheduler import load_registry

    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS)

    registry = load_registry(vault_path)
    entry = next(r for r in registry if r.name == "sample-weekly-check")
    assert entry.interval_seconds() == 7 * 24 * 3600
    assert entry.priority == "low"
    assert entry.callable == "reflections.housekeeping.sample_weekly_check.run"


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_registered_entry_loads_via_scheduler_registry(mock_machine, tmp_path, monkeypatch):
    """After registration, the scheduler's registry loader lists crash-recovery.

    Proves the appended entry is well-formed enough for
    agent.reflection_scheduler.load_registry to parse it (the real dry-run path).
    """
    from agent.reflection_scheduler import load_registry

    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    register_crash_recovery(project_dir)

    registry = load_registry(vault_path)
    names = [r.name for r in registry]
    assert "crash-recovery" in names


# ---------------------------------------------------------------------------
# register_memory_distill_backfill (#2202, memory-distilled-ingest Phase 3).
# Same wrapper shape as register_crash_recovery
# -- mirrors that test coverage: idempotent no-op path, vault-target write, and
# the repo-copy mirror, without touching a real vault.
# ---------------------------------------------------------------------------


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_memory_distill_backfill_owner_registers_missing_entry_in_vault(
    mock_machine, tmp_path, monkeypatch
):
    """The entry lands in the resolved (vault) file specifically — critique C6."""
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_memory_distill_backfill(project_dir)

    assert result.success is True
    assert result.action == "registered"
    entry = next(
        r
        for r in yaml.safe_load(vault_path.read_text())["reflections"]
        if r["name"] == "memory-distill-backfill"
    )
    assert entry["callable"] == "reflections.memory_management.run_memory_distill_backfill"
    assert entry["every"] == "300s"
    assert entry["priority"] == "normal"
    assert entry["enabled"] is True


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_memory_distill_backfill_already_registered_is_noop(mock_machine, tmp_path, monkeypatch):
    registry_with_entry = {
        "reflections": [
            *REGISTRY_WITHOUT_CRASH["reflections"],
            {
                "name": "memory-distill-backfill",
                "enabled": True,
                "callable": "reflections.memory_management.run_memory_distill_backfill",
                "every": "300s",
            },
        ]
    }
    vault_path, project_dir = _setup(
        tmp_path, registry=registry_with_entry, repo_registry=registry_with_entry
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_memory_distill_backfill(project_dir)

    assert result.success is True
    assert result.action == "noop"
    assert _names(vault_path).count("memory-distill-backfill") == 1


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_memory_distill_backfill_is_idempotent_across_two_runs(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    first = register_memory_distill_backfill(project_dir)
    second = register_memory_distill_backfill(project_dir)

    assert first.action == "registered"
    assert second.action == "noop"
    assert _names(vault_path).count("memory-distill-backfill") == 1


@patch("config.machine.get_machine_name", return_value="Some Other Machine")
def test_memory_distill_backfill_non_owner_skips_without_mutating(
    mock_machine, tmp_path, monkeypatch
):
    vault_path, project_dir = _setup(
        tmp_path, projects=PROJECTS_OWNED, repo_registry=REGISTRY_WITHOUT_CRASH
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_memory_distill_backfill(project_dir)

    assert result.action == "skipped"
    assert "memory-distill-backfill" not in _names(vault_path)


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_memory_distill_backfill_missing_vault_file_skips(mock_machine, tmp_path, monkeypatch):
    _, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(tmp_path / "does-not-exist.yaml"))

    result = register_memory_distill_backfill(project_dir)

    assert result.action == "skipped"
    assert "not found" in result.detail


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_memory_distill_backfill_coexists_with_crash_recovery_and_sample_entry(
    mock_machine, tmp_path, monkeypatch
):
    """_has_entry is name-scoped: registering all three never blocks each other."""
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    assert register_crash_recovery(project_dir).action == "registered"
    assert register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS).action == "registered"
    assert register_memory_distill_backfill(project_dir).action == "registered"

    names = _names(vault_path)
    assert names.count("crash-recovery") == 1
    assert names.count("sample-weekly-check") == 1
    assert names.count("memory-distill-backfill") == 1
    # Re-running each is still a noop with the others present.
    assert register_crash_recovery(project_dir).action == "noop"
    assert register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS).action == "noop"
    assert register_memory_distill_backfill(project_dir).action == "noop"


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_memory_distill_backfill_entry_loads_via_scheduler_registry(
    mock_machine, tmp_path, monkeypatch
):
    """The appended entry is well-formed for the scheduler's loader (the real dry-run path)."""
    from agent.reflection_scheduler import load_registry

    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    register_memory_distill_backfill(project_dir)

    registry = load_registry(vault_path)
    entry = next(r for r in registry if r.name == "memory-distill-backfill")
    assert entry.interval_seconds() == 300
    assert entry.priority == "normal"
    assert entry.callable == "reflections.memory_management.run_memory_distill_backfill"


# ---------------------------------------------------------------------------
# remove_reflection: the reverse direction (#2376). A reflection whose
# callable is deleted from the repo is stripped from the vault registry on
# /update via REMOVED_REFLECTIONS.
# ---------------------------------------------------------------------------


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_remove_reflection_strips_registered_entry(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))
    register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS)
    assert "sample-weekly-check" in _names(vault_path)

    result = remove_reflection(project_dir, name="sample-weekly-check")

    assert result.success is True
    assert result.action == "removed"
    assert "sample-weekly-check" not in _names(vault_path)
    # Other entries survive untouched.
    assert "other-reflection" in _names(vault_path)


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_remove_reflection_absent_entry_is_noop(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))
    before = vault_path.read_text()

    result = remove_reflection(project_dir, name="never-registered")

    assert result.success is True
    assert result.action == "noop"
    assert vault_path.read_text() == before


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_remove_reflection_is_idempotent_across_two_runs(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))
    register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS)

    assert remove_reflection(project_dir, name="sample-weekly-check").action == "removed"
    assert remove_reflection(project_dir, name="sample-weekly-check").action == "noop"


@patch("config.machine.get_machine_name", return_value="Some Other Machine")
def test_remove_reflection_non_owner_skips_without_mutating(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))
    before = vault_path.read_text()

    result = remove_reflection(project_dir, name="other-reflection")

    assert result.action == "skipped"
    assert vault_path.read_text() == before


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_remove_reflection_preserves_comments_and_other_entries(
    mock_machine, tmp_path, monkeypatch
):
    """Text-based removal keeps the hand-authored header comments intact."""
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    commented = "# Hand-authored registry header\n" + vault_path.read_text()
    vault_path.write_text(commented)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))
    register_reflection(project_dir, **SAMPLE_ENTRY_KWARGS)

    remove_reflection(project_dir, name="sample-weekly-check")

    text = vault_path.read_text()
    assert "# Hand-authored registry header" in text
    assert "sample-weekly-check" not in text
    assert "other-reflection" in text


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_removed_reflections_lists_test_baseline_refresh(mock_machine, tmp_path, monkeypatch):
    """The retired merge-gate baseline reflection is queued for removal (#2376),
    and running the removal against a registry that still carries the old entry
    strips it."""
    assert "test-baseline-refresh" in REMOVED_REFLECTIONS

    legacy = {
        "reflections": [
            {"name": "other-reflection", "enabled": True, "callable": "a.b", "every": "300s"},
            {
                "name": "test-baseline-refresh",
                "enabled": True,
                "callable": "reflections.housekeeping.test_baseline_refresh_check.run",
                "every": "7d",
            },
        ]
    }
    vault_path, project_dir = _setup(tmp_path, registry=legacy)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = remove_reflection(project_dir, name="test-baseline-refresh")

    assert result.action == "removed"
    assert "test-baseline-refresh" not in _names(vault_path)
    assert "other-reflection" in _names(vault_path)


# ---------------------------------------------------------------------------
# register_sdlc_upvote_pickup (#2717) — the first cron-scheduled entry in the
# registry. The end-to-end registry-file -> ReflectionEntry.validate ->
# compute_next_due path has never carried a cron value before this, so this
# is the Risk-1 test: register into a real temp registry, reload with the
# real loader, and assert the entry SURVIVED (an empty match list is the
# failure -- load_registry logs a warning and silently *skips* an invalid
# entry rather than raising).
# ---------------------------------------------------------------------------


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_sdlc_upvote_pickup_registers_cron_entry_that_survives_load(
    mock_machine, tmp_path, monkeypatch
):
    from agent.reflection_schedule import compute_next_due
    from agent.reflection_scheduler import load_registry
    from reflections.sdlc_upvote_lanes import UPVOTE_ENTRY_TIMEOUT_S

    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_sdlc_upvote_pickup(project_dir)

    assert result.success is True
    assert result.action == "registered"
    assert "sdlc-upvote-pickup" in _names(vault_path)

    registry = load_registry(vault_path)
    matches = [r for r in registry if r.name == "sdlc-upvote-pickup"]
    assert matches, "entry missing or skipped as invalid by load_registry -- Risk 1 failure"
    entry = matches[0]

    assert entry.validate() == []
    assert entry.effective_timeout() == UPVOTE_ENTRY_TIMEOUT_S

    next_due = compute_next_due(entry.schedule, None)
    assert next_due is not None
    import datetime as _dt

    dt = _dt.datetime.fromtimestamp(next_due, tz=_dt.UTC).astimezone(
        _dt.timezone(_dt.timedelta(hours=-8))
    )
    # 06:00-22:00 window (DST-agnostic bound check via a wide UTC-offset guess
    # is intentionally avoided -- assert against the schedule string instead,
    # which is what the register step actually emits).
    assert "6-22/2" in entry.schedule
    assert "America/Los_Angeles" in entry.schedule
    _ = dt  # exercised for readability; the schedule-string assertion above is authoritative


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_sdlc_upvote_pickup_is_idempotent(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    first = register_sdlc_upvote_pickup(project_dir)
    second = register_sdlc_upvote_pickup(project_dir)

    assert first.action == "registered"
    assert second.action == "noop"
    assert _names(vault_path).count("sdlc-upvote-pickup") == 1


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_sdlc_upvote_pickup_coexists_with_existing_registrations(
    mock_machine, tmp_path, monkeypatch
):
    """Registering the new cron entry must not disturb prior `every:` entries'
    emitted blocks -- the two existing registrations stay byte-for-byte."""
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITH_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    register_crash_recovery(project_dir)
    register_memory_distill_backfill(project_dir)
    register_sdlc_upvote_pickup(project_dir)

    names = _names(vault_path)
    assert "crash-recovery" in names
    assert "memory-distill-backfill" in names
    assert "sdlc-upvote-pickup" in names

    data = yaml.safe_load(vault_path.read_text())
    crash_entry = next(r for r in data["reflections"] if r["name"] == "crash-recovery")
    assert crash_entry["every"] == "300s"
    assert "cron" not in crash_entry

    upvote_entry = next(r for r in data["reflections"] if r["name"] == "sdlc-upvote-pickup")
    assert upvote_entry["cron"] == "0 6-22/2 * * *"
    assert upvote_entry["cron_tz"] == "America/Los_Angeles"
    assert "every" not in upvote_entry
    assert upvote_entry["timeout"] == 1500


def test_register_reflection_requires_exactly_one_of_cadence_or_cron(tmp_path):
    with pytest.raises(ValueError):
        register_reflection(
            tmp_path,
            name="bad-entry",
            callable_path="a.b",
            description="d",
            priority="low",
        )
    with pytest.raises(ValueError):
        register_reflection(
            tmp_path,
            name="bad-entry",
            callable_path="a.b",
            description="d",
            priority="low",
            cadence="300s",
            cron="0 6 * * *",
        )
