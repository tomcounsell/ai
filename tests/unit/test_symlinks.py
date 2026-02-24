"""Tests for scripts/update/hardlinks.py — retired commands and project-only skill scoping."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.update.hardlinks import (
    PROJECT_ONLY_SKILLS,
    RENAMED_REMOVALS,
    HardlinkSyncResult,
    _sync_skills,
    sync_claude_dirs,
)

# ---------------------------------------------------------------------------
# RENAMED_REMOVALS: retired commands should be listed
# ---------------------------------------------------------------------------

RETIRED_COMMANDS = [
    "do-build.md",
    "do-plan.md",
    "do-test.md",
    "do-docs.md",
    "do-pr-review.md",
    "update.md",
    "sdlc.md",
]


@pytest.mark.parametrize("cmd", RETIRED_COMMANDS)
def test_retired_commands_in_removals(cmd: str):
    """Each retired command must appear in RENAMED_REMOVALS as a commands entry."""
    assert (
        "commands",
        cmd,
    ) in RENAMED_REMOVALS, f"Retired command {cmd!r} missing from RENAMED_REMOVALS"


# ---------------------------------------------------------------------------
# PROJECT_ONLY_SKILLS: certain skills must NOT be synced to ~/.claude/skills/
# ---------------------------------------------------------------------------

EXPECTED_PROJECT_ONLY = {
    "telegram",
    "reading-sms-messages",
    "checking-system-logs",
    "google-workspace",
}


def test_project_only_skills_exist():
    """PROJECT_ONLY_SKILLS must contain the expected set."""
    assert PROJECT_ONLY_SKILLS == EXPECTED_PROJECT_ONLY


def test_project_only_skills_is_set():
    """PROJECT_ONLY_SKILLS should be a set for O(1) lookup."""
    assert isinstance(PROJECT_ONLY_SKILLS, (set, frozenset))


# ---------------------------------------------------------------------------
# _sync_skills must skip project-only skills
# ---------------------------------------------------------------------------


@pytest.fixture
def skill_dirs(tmp_path: Path):
    """Create a fake project with both shared and project-only skills."""
    src_skills = tmp_path / "project" / ".claude" / "skills"
    dst_skills = tmp_path / "home" / ".claude" / "skills"

    # Shared skill
    (src_skills / "do-test").mkdir(parents=True)
    (src_skills / "do-test" / "SKILL.md").write_text("# do-test skill")

    # Project-only skill
    (src_skills / "telegram").mkdir(parents=True)
    (src_skills / "telegram" / "SKILL.md").write_text("# telegram skill")

    # Another project-only skill
    (src_skills / "reading-sms-messages").mkdir(parents=True)
    (src_skills / "reading-sms-messages" / "SKILL.md").write_text("# sms skill")

    return src_skills, dst_skills


def test_sync_skills_skips_project_only(skill_dirs):
    """_sync_skills should not create hardlinks for project-only skills."""
    src_skills, dst_skills = skill_dirs
    result = HardlinkSyncResult()
    _sync_skills(src_skills, dst_skills, result)

    # Shared skill was synced
    assert (dst_skills / "do-test" / "SKILL.md").exists()

    # Project-only skills were NOT synced
    assert not (dst_skills / "telegram").exists()
    assert not (dst_skills / "reading-sms-messages").exists()


def test_sync_skills_counts_skipped_project_only(skill_dirs):
    """Project-only skills should not increment created count."""
    src_skills, dst_skills = skill_dirs
    result = HardlinkSyncResult()
    _sync_skills(src_skills, dst_skills, result)

    # Only 1 skill should have been created (do-test)
    assert result.created == 1


# ---------------------------------------------------------------------------
# Full sync_claude_dirs integration: retired commands get cleaned up
# ---------------------------------------------------------------------------


@pytest.fixture
def full_project(tmp_path: Path, monkeypatch):
    """Create a project + home dir mimicking real layout."""
    project = tmp_path / "project"
    home = tmp_path / "home"

    # Set up project skills dir (minimal)
    skills_dir = project / ".claude" / "skills" / "do-test"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# test")

    # Set up project commands dir (empty is fine for this test)
    (project / ".claude" / "commands").mkdir(parents=True)
    (project / ".claude" / "agents").mkdir(parents=True)

    # Set up home dir with a retired command file that should be cleaned up
    home_cmds = home / ".claude" / "commands"
    home_cmds.mkdir(parents=True)
    (home_cmds / "do-build.md").write_text("old command")
    (home_cmds / "sdlc.md").write_text("old command")

    # Also set up home skills dir
    (home / ".claude" / "skills").mkdir(parents=True)

    # Patch Path.home to return our tmp home
    monkeypatch.setattr(Path, "home", lambda: home)

    return project, home


def test_sync_removes_retired_commands(full_project):
    """sync_claude_dirs should remove retired command files from ~/.claude/commands/."""
    project, home = full_project
    result = sync_claude_dirs(project)

    # Retired commands should have been removed
    assert not (home / ".claude" / "commands" / "do-build.md").exists()
    assert not (home / ".claude" / "commands" / "sdlc.md").exists()
    assert result.removed >= 2
