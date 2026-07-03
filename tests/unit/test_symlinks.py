"""Tests for scripts/update/hardlinks.py — retired commands and project-only skill scoping."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import scripts.update.hardlinks as hardlinks
from scripts.update.hardlinks import (
    PROJECT_ONLY_SKILLS,
    RENAMED_REMOVALS,
    HardlinkSyncResult,
    _cleanup_renamed,
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
}


def test_project_only_skills_exist():
    """PROJECT_ONLY_SKILLS must contain the expected set."""
    assert PROJECT_ONLY_SKILLS == EXPECTED_PROJECT_ONLY


def test_project_only_skills_is_set():
    """PROJECT_ONLY_SKILLS should be a set for O(1) lookup."""
    assert isinstance(PROJECT_ONLY_SKILLS, set | frozenset)


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

    # Set up project skills-global dir (minimal) — sync reads from here, not skills/
    skills_dir = project / ".claude" / "skills-global" / "do-test"
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

    # Also set up home skills dir (real directory, post-migration state)
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


# ---------------------------------------------------------------------------
# Symlink migration: old ~/.claude/skills dir-symlink → real hardlinked dir
# ---------------------------------------------------------------------------


@pytest.fixture
def symlink_migration_project(tmp_path: Path, monkeypatch):
    """Project layout where ~/.claude/skills is still the old directory symlink."""
    project = tmp_path / "project"
    home = tmp_path / "home"

    # skills-global/ has the global skill
    skills_global = project / ".claude" / "skills-global" / "do-test"
    skills_global.mkdir(parents=True)
    (skills_global / "SKILL.md").write_text("# do-test")

    # skills/ has only project-only skills (new layout)
    (project / ".claude" / "skills" / "telegram").mkdir(parents=True)
    (project / ".claude" / "skills" / "telegram" / "SKILL.md").write_text("# telegram")

    (project / ".claude" / "commands").mkdir(parents=True)
    (project / ".claude" / "agents").mkdir(parents=True)

    # Old layout: ~/.claude/skills is a directory symlink to .claude/skills/
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "skills").symlink_to(project / ".claude" / "skills")
    (home / ".claude" / "commands").mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: home)
    return project, home


def test_sync_migrates_skills_dir_symlink(symlink_migration_project):
    """sync_claude_dirs removes the old dir-symlink and replaces with hardlinked real dir."""
    project, home = symlink_migration_project
    user_skills = home / ".claude" / "skills"

    assert user_skills.is_symlink(), "precondition: starts as symlink"

    result = sync_claude_dirs(project)

    # Symlink is gone, replaced by a real directory
    assert not user_skills.is_symlink()
    assert user_skills.is_dir()

    # Global skill was hardlinked in
    assert (user_skills / "do-test" / "SKILL.md").exists()

    # Project-only skill was NOT synced (telegram is in PROJECT_ONLY_SKILLS)
    assert not (user_skills / "telegram").exists()

    # Migration removal counted
    assert result.removed >= 1


# ---------------------------------------------------------------------------
# Bucket C RENAMED_REMOVALS entries (issue #1783)
# ---------------------------------------------------------------------------

BUCKET_C_MOVED_SKILLS = ["setup", "prime", "sdlc", "do-deploy"]


@pytest.mark.parametrize("skill", BUCKET_C_MOVED_SKILLS)
def test_bucket_c_skills_in_removals(skill: str):
    """Each moved Bucket C skill must appear in RENAMED_REMOVALS as a skills entry."""
    assert (
        "skills",
        skill,
    ) in RENAMED_REMOVALS, f"Moved Bucket C skill {skill!r} missing from RENAMED_REMOVALS"


# ---------------------------------------------------------------------------
# _cleanup_renamed inode guard (issue #1783, concern #2)
# ---------------------------------------------------------------------------


def test_cleanup_renamed_removes_genuine_orphan(tmp_path: Path, monkeypatch):
    """A stale user-level skill dir not hardlinked to any project source is removed."""
    monkeypatch.setattr(hardlinks, "RENAMED_REMOVALS", [("skills", "sdlc")])

    project = tmp_path / "project"
    # Project no longer provides sdlc under skills-global (it was moved out)
    (project / ".claude" / "skills-global").mkdir(parents=True)

    user_claude = tmp_path / "home" / ".claude"
    orphan = user_claude / "skills" / "sdlc"
    orphan.mkdir(parents=True)
    (orphan / "SKILL.md").write_text("# stale orphan, not hardlinked to anything live")

    result = HardlinkSyncResult()
    _cleanup_renamed(user_claude, project, result)

    assert not orphan.exists(), "genuine orphan should be removed"
    assert result.removed >= 1


def test_cleanup_renamed_preserves_project_backed(tmp_path: Path, monkeypatch):
    """A user-level skill still hardlinked to a live project source is preserved.

    Simulates a foreign repo that legitimately provides its own same-named
    skill under skills-global/ — the blanket RENAMED_REMOVALS sweep must not
    delete it.
    """
    monkeypatch.setattr(hardlinks, "RENAMED_REMOVALS", [("skills", "sdlc")])

    project = tmp_path / "project"
    src_skill = project / ".claude" / "skills-global" / "sdlc"
    src_skill.mkdir(parents=True)
    src_file = src_skill / "SKILL.md"
    src_file.write_text("# live project-backed sdlc skill")

    user_claude = tmp_path / "home" / ".claude"
    dst_skill = user_claude / "skills" / "sdlc"
    dst_skill.mkdir(parents=True)
    # Hardlink (shared inode) — proves it is project-backed
    os.link(src_file, dst_skill / "SKILL.md")

    result = HardlinkSyncResult()
    _cleanup_renamed(user_claude, project, result)

    assert dst_skill.exists(), "project-backed skill must be preserved"
    assert (dst_skill / "SKILL.md").exists()
