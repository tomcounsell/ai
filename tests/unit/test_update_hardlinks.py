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
    result = hardlinks.sync_claude_dirs(fake_project)

    dst = fake_home / ".local" / "bin" / "sdlc-tool"
    assert dst.exists()
    src = fake_project / "scripts" / "sdlc-tool"
    assert os.stat(src).st_ino == os.stat(dst).st_ino


def test_user_bin_scripts_table_contains_sdlc_tool():
    """Regression guard: ensure the registry isn't empty."""
    paths = [src for src, _ in hardlinks.USER_BIN_SCRIPTS]
    assert "scripts/sdlc-tool" in paths
