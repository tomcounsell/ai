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
    register_crash_recovery,
    register_memory_distill_backfill,
    register_reflection,
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

BASELINE_REFRESH_KWARGS = {
    "name": "test-baseline-refresh",
    "callable_path": "reflections.housekeeping.test_baseline_refresh_check.run",
    "description": "Warn when the merge-gate test baseline is stale (#1933/#2004)",
    "cadence": "7d",
    "priority": "low",
}


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_reflection_registers_arbitrary_entry(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_reflection(project_dir, **BASELINE_REFRESH_KWARGS)

    assert result.success is True
    assert result.action == "registered"
    entry = next(
        r
        for r in yaml.safe_load(vault_path.read_text())["reflections"]
        if r["name"] == "test-baseline-refresh"
    )
    assert entry["callable"] == "reflections.housekeeping.test_baseline_refresh_check.run"
    assert entry["every"] == "7d"
    assert entry["priority"] == "low"
    assert entry["enabled"] is True


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_reflection_is_idempotent(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    first = register_reflection(project_dir, **BASELINE_REFRESH_KWARGS)
    second = register_reflection(project_dir, **BASELINE_REFRESH_KWARGS)

    assert first.action == "registered"
    assert second.action == "noop"
    assert _names(vault_path).count("test-baseline-refresh") == 1


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_reflection_second_entry_coexists_with_crash_recovery(
    mock_machine, tmp_path, monkeypatch
):
    """_has_entry is name-scoped: one entry present never blocks the other."""
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    assert register_crash_recovery(project_dir).action == "registered"
    assert register_reflection(project_dir, **BASELINE_REFRESH_KWARGS).action == "registered"

    names = _names(vault_path)
    assert names.count("crash-recovery") == 1
    assert names.count("test-baseline-refresh") == 1
    # Re-running each is still a noop with the other present.
    assert register_crash_recovery(project_dir).action == "noop"
    assert register_reflection(project_dir, **BASELINE_REFRESH_KWARGS).action == "noop"


@patch("config.machine.get_machine_name", return_value="Some Other Machine")
def test_register_reflection_non_owner_skips_without_mutating(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_reflection(project_dir, **BASELINE_REFRESH_KWARGS)

    assert result.action == "skipped"
    assert "test-baseline-refresh" not in _names(vault_path)


@patch("config.machine.get_machine_name", return_value="Tom's MacBook Pro")
def test_register_reflection_entry_loads_via_scheduler_registry(
    mock_machine, tmp_path, monkeypatch
):
    """The appended weekly entry is well-formed for the scheduler's loader."""
    from agent.reflection_scheduler import load_registry

    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    register_reflection(project_dir, **BASELINE_REFRESH_KWARGS)

    registry = load_registry(vault_path)
    entry = next(r for r in registry if r.name == "test-baseline-refresh")
    assert entry.interval_seconds() == 7 * 24 * 3600
    assert entry.priority == "low"
    assert entry.callable == "reflections.housekeeping.test_baseline_refresh_check.run"


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
# Same wrapper shape as register_crash_recovery / register_test_baseline_refresh
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
def test_memory_distill_backfill_coexists_with_crash_recovery_and_baseline_refresh(
    mock_machine, tmp_path, monkeypatch
):
    """_has_entry is name-scoped: registering all three never blocks each other."""
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    assert register_crash_recovery(project_dir).action == "registered"
    assert register_reflection(project_dir, **BASELINE_REFRESH_KWARGS).action == "registered"
    assert register_memory_distill_backfill(project_dir).action == "registered"

    names = _names(vault_path)
    assert names.count("crash-recovery") == 1
    assert names.count("test-baseline-refresh") == 1
    assert names.count("memory-distill-backfill") == 1
    # Re-running each is still a noop with the others present.
    assert register_crash_recovery(project_dir).action == "noop"
    assert register_reflection(project_dir, **BASELINE_REFRESH_KWARGS).action == "noop"
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
