"""Tests for sync_user_hooks() and _merge_sdlc_hook_settings() in hardlinks.py.

Verifies that user-level SDLC hooks are deployed correctly to ~/.claude/hooks/sdlc/
and that the settings merge preserves existing hooks, deduplicates on repeated runs,
and never clobbers non-SDLC entries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.update.hardlinks import (
    HardlinkSyncResult,
    _merge_sdlc_hook_settings,
    sync_user_hooks,
)


@pytest.fixture
def mock_home(tmp_path: Path, monkeypatch):
    """Create a fake home directory and patch Path.home()."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def project_with_hooks(tmp_path: Path):
    """Create a fake project with user_level hook scripts."""
    project = tmp_path / "project"
    hooks_dir = project / ".claude" / "hooks" / "user_level"
    hooks_dir.mkdir(parents=True)

    # Create three hook scripts
    for name in [
        "validate_commit_message.py",
        "sdlc_reminder.py",
        "validate_sdlc_on_stop.py",
    ]:
        script = hooks_dir / name
        script.write_text(
            f"#!/usr/bin/env python3\n# {name}\nimport sys\nsys.exit(0)\n"
        )

    return project


# ===========================================================================
# sync_user_hooks: file deployment
# ===========================================================================


class TestSyncUserHooksDeployment:
    """sync_user_hooks should copy scripts to ~/.claude/hooks/sdlc/."""

    def test_creates_hooks_directory(self, mock_home, project_with_hooks):
        result = HardlinkSyncResult()
        sync_user_hooks(project_with_hooks, result)

        sdlc_dir = mock_home / ".claude" / "hooks" / "sdlc"
        assert sdlc_dir.is_dir()

    def test_copies_all_three_scripts(self, mock_home, project_with_hooks):
        result = HardlinkSyncResult()
        sync_user_hooks(project_with_hooks, result)

        sdlc_dir = mock_home / ".claude" / "hooks" / "sdlc"
        assert (sdlc_dir / "validate_commit_message.py").exists()
        assert (sdlc_dir / "sdlc_reminder.py").exists()
        assert (sdlc_dir / "validate_sdlc_on_stop.py").exists()

    def test_scripts_are_executable(self, mock_home, project_with_hooks):
        import os

        result = HardlinkSyncResult()
        sync_user_hooks(project_with_hooks, result)

        sdlc_dir = mock_home / ".claude" / "hooks" / "sdlc"
        for name in [
            "validate_commit_message.py",
            "sdlc_reminder.py",
            "validate_sdlc_on_stop.py",
        ]:
            assert os.access(sdlc_dir / name, os.X_OK)

    def test_reports_created_actions(self, mock_home, project_with_hooks):
        result = HardlinkSyncResult()
        sync_user_hooks(project_with_hooks, result)

        # 3 scripts copied = 3 created actions
        assert result.created >= 3

    def test_no_errors_on_clean_deploy(self, mock_home, project_with_hooks):
        result = HardlinkSyncResult()
        sync_user_hooks(project_with_hooks, result)
        assert result.errors == 0

    def test_no_hooks_when_source_missing(self, mock_home, tmp_path):
        """If .claude/hooks/user_level/ doesn't exist, sync should be a no-op."""
        project = tmp_path / "empty_project"
        project.mkdir()

        result = HardlinkSyncResult()
        sync_user_hooks(project, result)
        assert result.created == 0
        assert result.errors == 0


# ===========================================================================
# _merge_sdlc_hook_settings: settings.json merge
# ===========================================================================


class TestMergeSettings:
    """_merge_sdlc_hook_settings should merge SDLC hooks into settings.json."""

    def test_creates_settings_when_missing(self, mock_home):
        hooks_dir = mock_home / ".claude" / "hooks" / "sdlc"
        hooks_dir.mkdir(parents=True)

        result = HardlinkSyncResult()
        _merge_sdlc_hook_settings(hooks_dir, result)

        settings_path = mock_home / ".claude" / "settings.json"
        assert settings_path.exists()

        settings = json.loads(settings_path.read_text())
        assert "hooks" in settings
        assert "PreToolUse" in settings["hooks"]
        assert "PostToolUse" in settings["hooks"]
        assert "Stop" in settings["hooks"]

    def test_preserves_existing_hooks(self, mock_home):
        """Existing hooks (e.g., calendar) must not be clobbered."""
        settings_path = mock_home / ".claude" / "settings.json"
        existing = {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "bash /path/to/calendar_hook.sh",
                                "timeout": 15,
                            }
                        ],
                    }
                ],
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "bash /path/to/calendar_hook.sh",
                                "timeout": 15,
                            }
                        ],
                    }
                ],
            },
            "statusLine": {"type": "command", "command": "/path/to/statusline.sh"},
        }
        settings_path.write_text(json.dumps(existing))

        hooks_dir = mock_home / ".claude" / "hooks" / "sdlc"
        hooks_dir.mkdir(parents=True)

        result = HardlinkSyncResult()
        _merge_sdlc_hook_settings(hooks_dir, result)

        settings = json.loads(settings_path.read_text())

        # Calendar hook in UserPromptSubmit should be preserved
        assert any(
            "calendar_hook" in entry["hooks"][0]["command"]
            for entry in settings["hooks"]["UserPromptSubmit"]
        )

        # Calendar hook in Stop should be preserved alongside SDLC hook
        stop_commands = [
            entry["hooks"][0]["command"] for entry in settings["hooks"]["Stop"]
        ]
        assert any("calendar_hook" in cmd for cmd in stop_commands)
        assert any("validate_sdlc_on_stop" in cmd for cmd in stop_commands)

        # Non-hook config should be preserved
        assert settings["statusLine"]["command"] == "/path/to/statusline.sh"

    def test_deduplicates_on_repeated_runs(self, mock_home):
        """Running the merge twice should not create duplicate entries."""
        hooks_dir = mock_home / ".claude" / "hooks" / "sdlc"
        hooks_dir.mkdir(parents=True)

        result1 = HardlinkSyncResult()
        _merge_sdlc_hook_settings(hooks_dir, result1)

        result2 = HardlinkSyncResult()
        _merge_sdlc_hook_settings(hooks_dir, result2)

        settings = json.loads((mock_home / ".claude" / "settings.json").read_text())

        # Count SDLC entries in PreToolUse — should be exactly 1
        pre_tool_use = settings["hooks"]["PreToolUse"]
        sdlc_entries = [
            e
            for e in pre_tool_use
            if "validate_commit_message" in e["hooks"][0]["command"]
        ]
        assert len(sdlc_entries) == 1

        # Count SDLC entries in Stop — should be exactly 1
        stop = settings["hooks"]["Stop"]
        sdlc_stop_entries = [
            e for e in stop if "validate_sdlc_on_stop" in e["hooks"][0]["command"]
        ]
        assert len(sdlc_stop_entries) == 1

    def test_uses_correct_hook_paths(self, mock_home):
        """Hook commands should reference the correct ~/.claude/hooks/sdlc/ path."""
        hooks_dir = mock_home / ".claude" / "hooks" / "sdlc"
        hooks_dir.mkdir(parents=True)

        result = HardlinkSyncResult()
        _merge_sdlc_hook_settings(hooks_dir, result)

        settings = json.loads((mock_home / ".claude" / "settings.json").read_text())

        # All hook commands should point to the hooks_dir
        for event, entries in settings["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "sdlc" in cmd:
                        assert str(hooks_dir) in cmd

    def test_post_tool_use_has_write_and_edit_matchers(self, mock_home):
        """PostToolUse should have entries for both Write and Edit matchers."""
        hooks_dir = mock_home / ".claude" / "hooks" / "sdlc"
        hooks_dir.mkdir(parents=True)

        result = HardlinkSyncResult()
        _merge_sdlc_hook_settings(hooks_dir, result)

        settings = json.loads((mock_home / ".claude" / "settings.json").read_text())

        post_tool_use = settings["hooks"]["PostToolUse"]
        matchers = {entry["matcher"] for entry in post_tool_use}
        assert "Write" in matchers
        assert "Edit" in matchers

    def test_pre_tool_use_has_bash_matcher(self, mock_home):
        """PreToolUse should have a Bash matcher for commit validation."""
        hooks_dir = mock_home / ".claude" / "hooks" / "sdlc"
        hooks_dir.mkdir(parents=True)

        result = HardlinkSyncResult()
        _merge_sdlc_hook_settings(hooks_dir, result)

        settings = json.loads((mock_home / ".claude" / "settings.json").read_text())

        pre_tool_use = settings["hooks"]["PreToolUse"]
        bash_entries = [e for e in pre_tool_use if e["matcher"] == "Bash"]
        assert len(bash_entries) >= 1


# ===========================================================================
# Full sync_user_hooks integration: deploy + merge
# ===========================================================================


class TestSyncUserHooksIntegration:
    """Full sync_user_hooks: deploy scripts AND merge settings."""

    def test_full_sync_deploys_and_merges(self, mock_home, project_with_hooks):
        result = HardlinkSyncResult()
        sync_user_hooks(project_with_hooks, result)

        # Scripts deployed
        sdlc_dir = mock_home / ".claude" / "hooks" / "sdlc"
        assert (sdlc_dir / "validate_commit_message.py").exists()

        # Settings merged
        settings_path = mock_home / ".claude" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        assert "PreToolUse" in settings["hooks"]

    def test_full_sync_no_errors(self, mock_home, project_with_hooks):
        result = HardlinkSyncResult()
        sync_user_hooks(project_with_hooks, result)
        assert result.errors == 0
        assert result.success is True  # errors == 0 doesn't change success flag
