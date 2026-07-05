"""Unit tests for scripts.update.reflection_arm (plan-migration backstop arming).

Issue #1900, Tier 0. Covers the update-time step that flips
merged-branch-cleanup's ``enabled`` to True in both the vault reflections.yaml
and the in-repo copy -- durably, since config/reflections.yaml is gitignored
and gets clobbered from the vault on every /update.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import yaml

from scripts.update.reflection_arm import arm_merged_branch_cleanup

pytestmark = pytest.mark.sdlc

REFLECTIONS_DISABLED = {
    "reflections": [
        {"name": "merged-branch-cleanup", "enabled": False, "callable": "x.y"},
        {"name": "other-reflection", "enabled": True, "callable": "a.b"},
    ]
}

REFLECTIONS_ENABLED = {
    "reflections": [
        {"name": "merged-branch-cleanup", "enabled": True, "callable": "x.y"},
    ]
}

PROJECTS_OWNED = {"projects": {"valor": {"machine": "Tom's MacBook Pro"}}}
PROJECTS_NOT_OWNED = {"projects": {"valor": {"machine": "Some Other Machine"}}}


def _setup(tmp_path, reflections=None, projects=None, repo_reflections=None):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    vault_path = vault_dir / "reflections.yaml"
    vault_path.write_text(
        yaml.safe_dump(reflections if reflections is not None else REFLECTIONS_DISABLED)
    )

    project_dir = tmp_path / "repo"
    (project_dir / "config").mkdir(parents=True)
    (project_dir / "scripts").mkdir(parents=True)
    (project_dir / "scripts" / "install_reflection_worker.sh").write_text("#!/bin/bash\ntrue\n")
    (project_dir / "config" / "projects.json").write_text(
        json.dumps(projects if projects is not None else PROJECTS_OWNED)
    )
    if repo_reflections is not None:
        (project_dir / "config" / "reflections.yaml").write_text(yaml.safe_dump(repo_reflections))

    return vault_path, project_dir


def _states(path):
    data = yaml.safe_load(path.read_text())
    return {r["name"]: r.get("enabled", True) for r in data["reflections"]}


@patch("scripts.update.reflection_arm.subprocess.run")
@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
def test_owner_arms_disabled_reflection(mock_machine, mock_run, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_reflections=REFLECTIONS_DISABLED)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = arm_merged_branch_cleanup(project_dir)

    assert result.success is True
    assert result.action == "armed"
    assert _states(vault_path)["merged-branch-cleanup"] is True
    assert _states(project_dir / "config" / "reflections.yaml")["merged-branch-cleanup"] is True
    mock_run.assert_called_once()


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
def test_already_enabled_is_noop(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(
        tmp_path, reflections=REFLECTIONS_ENABLED, repo_reflections=REFLECTIONS_ENABLED
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = arm_merged_branch_cleanup(project_dir)

    assert result.success is True
    assert result.action == "noop"


@patch("tools.machine_identity.computer_name", return_value="Some Other Machine")
def test_non_owner_skips_without_mutating(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(
        tmp_path, projects=PROJECTS_OWNED, repo_reflections=REFLECTIONS_DISABLED
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = arm_merged_branch_cleanup(project_dir)

    assert result.action == "skipped"
    assert _states(vault_path)["merged-branch-cleanup"] is False
    assert _states(project_dir / "config" / "reflections.yaml")["merged-branch-cleanup"] is False


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
def test_missing_vault_file_skips(mock_machine, tmp_path, monkeypatch):
    _, project_dir = _setup(tmp_path, repo_reflections=REFLECTIONS_DISABLED)
    monkeypatch.setenv("REFLECTIONS_YAML", str(tmp_path / "does-not-exist.yaml"))

    result = arm_merged_branch_cleanup(project_dir)

    assert result.action == "skipped"
    assert "not found" in result.detail


@patch("tools.machine_identity.computer_name", return_value="")
def test_unresolvable_machine_name_fails_closed(mock_machine, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_reflections=REFLECTIONS_DISABLED)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = arm_merged_branch_cleanup(project_dir)

    assert result.action == "skipped"
    assert _states(vault_path)["merged-branch-cleanup"] is False
