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


# A hand-authored vault file: header docs, per-entry annotations, inline
# comments. The flip must be line-scoped -- a yaml.safe_dump round-trip would
# destroy all of this (PR #1903 review Tech Debt).
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

  # Daily plan-migration backstop (issue #1900).
  - name: merged-branch-cleanup
    enabled: false  # armed by /update once validated
    callable: x.y
    every: 86400s  # daily
"""


@patch("scripts.update.reflection_arm.subprocess.run")
@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
def test_flip_preserves_comments_and_formatting(mock_machine, mock_run, tmp_path, monkeypatch):
    vault_path, project_dir = _setup(tmp_path, repo_reflections=REFLECTIONS_DISABLED)
    vault_path.write_text(COMMENTED_VAULT)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = arm_merged_branch_cleanup(project_dir)
    assert result.action == "armed"

    text = vault_path.read_text()
    assert _states(vault_path)["merged-branch-cleanup"] is True
    # Every comment survives, including the flipped line's inline comment.
    assert "# Reflections registry -- source of truth (iCloud vault)." in text
    assert "# Daily plan-migration backstop (issue #1900)." in text
    assert "enabled: true  # armed by /update once validated" in text
    assert "every: 86400s  # daily" in text
    # The untouched sibling entry is byte-identical.
    assert "enabled: true  # keep on" in text
    assert "every: 300s  # 5 minutes" in text
    # No stray temp file left behind.
    assert not list(vault_path.parent.glob("*.tmp"))


@patch("scripts.update.reflection_arm.subprocess.run")
@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
def test_arm_is_one_shot_and_respects_human_disarm(mock_machine, mock_run, tmp_path, monkeypatch):
    """After the arm fires once, a human `enabled: false` must stick.

    The update loop runs on a cron; without the one-shot marker it would
    silently re-arm an unattended push-to-main automation the operator
    deliberately disabled (PR #1903 review Tech Debt).
    """
    vault_path, project_dir = _setup(tmp_path, repo_reflections=REFLECTIONS_DISABLED)
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    first = arm_merged_branch_cleanup(project_dir)
    assert first.action == "armed"
    assert (project_dir / "data" / "reflection-armed-merged-branch-cleanup").exists()

    # Human disarms in the vault (the operational kill switch).
    vault_path.write_text(yaml.safe_dump(REFLECTIONS_DISABLED))

    second = arm_merged_branch_cleanup(project_dir)
    assert second.action == "skipped"
    assert "human-owned" in second.detail
    assert _states(vault_path)["merged-branch-cleanup"] is False


@patch("tools.machine_identity.computer_name", return_value="Tom's MacBook Pro")
def test_noop_also_stamps_marker(mock_machine, tmp_path, monkeypatch):
    """An already-enabled entry still stamps the marker: from that point on
    the flag is human-owned either way."""
    vault_path, project_dir = _setup(
        tmp_path, reflections=REFLECTIONS_ENABLED, repo_reflections=REFLECTIONS_ENABLED
    )
    monkeypatch.setenv("REFLECTIONS_YAML", str(vault_path))

    result = arm_merged_branch_cleanup(project_dir)

    assert result.action == "noop"
    assert (project_dir / "data" / "reflection-armed-merged-branch-cleanup").exists()
