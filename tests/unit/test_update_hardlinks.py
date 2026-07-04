"""Tests for the hardlinks step that propagates standalone scripts to ~/.local/bin.

Specifically validates that ``scripts/sdlc-tool`` lands at ``~/.local/bin/sdlc-tool``
as a real hardlink (same inode), not a copy. Tests use ``tmp_path`` and patch
``Path.home`` so they never touch the real ``~/.local/bin/``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.update import hardlinks


@pytest.fixture
def fake_project(tmp_path):
    """Build a minimal project layout containing scripts/sdlc-tool."""
    project = tmp_path / "ai-project"
    (project / "scripts").mkdir(parents=True)
    (project / ".claude" / "skills").mkdir(parents=True)
    (project / ".claude" / "commands").mkdir(parents=True)
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "hooks" / "sdlc").mkdir(parents=True)

    src = project / "scripts" / "sdlc-tool"
    src.write_text("#!/usr/bin/env bash\necho hello\n")
    src.chmod(0o755)
    return project


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() so ~/.local/bin and ~/.claude/ point at tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    return home


def test_sync_user_scripts_creates_hardlink(fake_project, fake_home):
    result = hardlinks.sync_user_scripts(fake_project)

    assert result.errors == 0, [a.error for a in result.actions if a.error]
    assert result.created == 1

    src = fake_project / "scripts" / "sdlc-tool"
    dst = fake_home / ".local" / "bin" / "sdlc-tool"
    assert dst.exists()
    # Same inode = real hardlink (not a copy)
    assert os.stat(src).st_ino == os.stat(dst).st_ino


def test_sync_user_scripts_idempotent(fake_project, fake_home):
    """Running twice with no change should be a no-op."""
    first = hardlinks.sync_user_scripts(fake_project)
    second = hardlinks.sync_user_scripts(fake_project)

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1
    assert second.errors == 0


def test_sync_user_scripts_replaces_stale_copy(fake_project, fake_home):
    """A non-hardlinked file at the destination should be replaced with a hardlink."""
    dst_dir = fake_home / ".local" / "bin"
    dst_dir.mkdir(parents=True)
    stale = dst_dir / "sdlc-tool"
    stale.write_text("# old version\n")
    stale_inode = os.stat(stale).st_ino

    result = hardlinks.sync_user_scripts(fake_project)
    assert result.errors == 0

    src = fake_project / "scripts" / "sdlc-tool"
    new_inode = os.stat(stale).st_ino
    assert new_inode != stale_inode  # got replaced
    assert new_inode == os.stat(src).st_ino  # now a hardlink to the source


def test_sync_user_scripts_missing_source_records_error(fake_project, fake_home):
    """Deleting the source should surface as an error rather than crashing."""
    (fake_project / "scripts" / "sdlc-tool").unlink()
    result = hardlinks.sync_user_scripts(fake_project)
    assert result.errors == 1
    assert any("Source missing" in (a.error or "") for a in result.actions)


def test_sync_claude_dirs_includes_user_scripts(fake_project, fake_home):
    """The top-level sync function must call sync_user_scripts."""
    # sync_claude_dirs reaches into _SDLC_HOOK_DEFS which expects real hook
    # files. We don't ship those in the fake project — but missing src dirs
    # are tolerated (sync_user_hooks early-returns), and the failure modes
    # for missing skills/commands dirs are also tolerated. The piece we care
    # about is that scripts/sdlc-tool gets hardlinked.
    hardlinks.sync_claude_dirs(fake_project)

    dst = fake_home / ".local" / "bin" / "sdlc-tool"
    assert dst.exists()
    src = fake_project / "scripts" / "sdlc-tool"
    assert os.stat(src).st_ino == os.stat(dst).st_ino


def test_user_bin_scripts_table_contains_sdlc_tool():
    """Regression guard: ensure the registry isn't empty."""
    paths = [src for src, _ in hardlinks.USER_BIN_SCRIPTS]
    assert "scripts/sdlc-tool" in paths


def test_sync_skills_prunes_intra_dir_orphan(fake_project, fake_home):
    """A file deleted from a surviving source skill dir must be pruned from ~/.claude.

    Regression for the skills-renovation rollout: pass 1 deleted
    do-pr-review/sub-skills/README.md (content folded into SKILL.md), but the
    dir-level stale cleanup only removes whole skill dirs whose source is gone.
    The stale hardlink lingered on fleet machines and could be loaded alongside
    the renovated SKILL.md, contradicting current instructions.
    """
    src_skill = fake_project / ".claude" / "skills-global" / "do-review"
    (src_skill / "sub-skills").mkdir(parents=True)
    (src_skill / "SKILL.md").write_text("# review skill\n")
    (src_skill / "sub-skills" / "keep.md").write_text("keep\n")
    old = src_skill / "sub-skills" / "old.md"
    old.write_text("old guidance\n")

    hardlinks.sync_claude_dirs(fake_project)
    dst_skill = fake_home / ".claude" / "skills" / "do-review"
    assert (dst_skill / "sub-skills" / "old.md").exists()

    # Source file deleted (dir survives) — next sync must prune the dst copy.
    old.unlink()
    result = hardlinks.sync_claude_dirs(fake_project)

    assert not (dst_skill / "sub-skills" / "old.md").exists(), (
        "orphan file lingered after source deletion"
    )
    assert (dst_skill / "sub-skills" / "keep.md").exists()
    assert (dst_skill / "SKILL.md").exists()
    assert result.removed >= 1


def test_sync_skills_prune_removes_emptied_subdir(fake_project, fake_home):
    """When every file in a subdir is deleted at source, the empty dst subdir goes too."""
    src_skill = fake_project / ".claude" / "skills-global" / "do-review"
    (src_skill / "refs").mkdir(parents=True)
    (src_skill / "SKILL.md").write_text("# review skill\n")
    gone = src_skill / "refs" / "only.md"
    gone.write_text("only\n")

    hardlinks.sync_claude_dirs(fake_project)
    gone.unlink()
    (src_skill / "refs").rmdir()
    hardlinks.sync_claude_dirs(fake_project)

    dst_refs = fake_home / ".claude" / "skills" / "do-review" / "refs"
    assert not dst_refs.exists(), "emptied subdir lingered in destination"
    assert (fake_home / ".claude" / "skills" / "do-review" / "SKILL.md").exists()


def test_sync_skills_prune_leaves_foreign_skill_dirs_alone(fake_project, fake_home):
    """A user-level skill dir not backed by this project must never be touched."""
    foreign = fake_home / ".claude" / "skills" / "foreign-skill"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("foreign\n")
    (foreign / "notes.md").write_text("private notes\n")

    hardlinks.sync_claude_dirs(fake_project)

    assert (foreign / "SKILL.md").exists()
    assert (foreign / "notes.md").exists()


def test_sync_commands_recurses_into_namespace_subdirs(fake_project, fake_home):
    """Namespaced commands (e.g. granite/prime-pm-role.md) must hardlink globally.

    Regression for the granite PTY production hang: PR #1694 moved persona
    delivery to /granite:prime-pm-role slash commands living in
    .claude/commands/granite/. The granite container runs claude in OTHER
    repos' worktrees, so the command is only resolvable if it syncs to
    ~/.claude/commands/granite/. A top-level-only glob left it unsynced, and
    every granite session hung on "Unknown command: /granite:prime-pm-role".
    """
    src_ns = fake_project / ".claude" / "commands" / "granite"
    src_ns.mkdir(parents=True)
    src_cmd = src_ns / "prime-pm-role.md"
    src_cmd.write_text("---\nname: prime-pm-role\n---\nPrime the PM persona.\n")

    hardlinks._sync_commands(
        fake_project / ".claude" / "commands",
        fake_home / ".claude" / "commands",
        hardlinks.HardlinkSyncResult(),
    )

    dst_cmd = fake_home / ".claude" / "commands" / "granite" / "prime-pm-role.md"
    assert dst_cmd.exists(), "namespaced command was not synced into ~/.claude/commands/granite/"
    assert os.stat(src_cmd).st_ino == os.stat(dst_cmd).st_ino, "synced as copy, not hardlink"
