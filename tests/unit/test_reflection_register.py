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

from scripts.update.reflection_register import register_crash_recovery

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


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
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


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
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


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
def test_register_is_idempotent_across_two_runs(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    first = register_crash_recovery(project_dir)
    second = register_crash_recovery(project_dir)

    assert first.action == "registered"
    assert second.action == "noop"
    assert _names(vault_path).count("crash-recovery") == 1


@patch("tools.machine_identity.computer_name", return_value="Some Other Machine")
def test_non_owner_skips_without_mutating(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(
        tmp_path, projects=PROJECTS_OWNED, repo_registry=REGISTRY_WITHOUT_CRASH
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = register_crash_recovery(project_dir)

    assert result.action == "skipped"
    assert "crash-recovery" not in _names(vault_path)


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
def test_missing_vault_file_skips(mock_machine, tmp_path, monkeypatch):
    _, project_dir = _setup(tmp_path, repo_registry=REGISTRY_WITHOUT_CRASH)
    monkeypatch.setenv("REFLECTIONS_YAML", str(tmp_path / "does-not-exist.yaml"))

    result = register_crash_recovery(project_dir)

    assert result.action == "skipped"
    assert "not found" in result.detail


@patch("tools.machine_identity.computer_name", return_value="")
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


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
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


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
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
