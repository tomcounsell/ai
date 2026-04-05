"""Tests for reflections hooks_audit step."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_load_local_projects():
    """Mock load_local_projects so tests don't require projects.json on disk."""
    with patch("scripts.reflections.load_local_projects", return_value=[]):
        yield


@pytest.fixture
def runner():
    """Create a ReflectionsRunner instance with mocked dependencies."""
    with patch("scripts.reflections.load_local_projects", return_value=[]):
        from scripts.reflections import ReflectionRunner

        r = ReflectionRunner()
        return r


class TestHooksAuditMissingFiles:
    """Test that step_hooks_audit handles missing files gracefully."""

    @pytest.mark.asyncio
    async def test_handles_missing_hooks_log(self, runner, tmp_path):
        """Should not crash when hooks.log does not exist."""
        with patch("scripts.reflections.PROJECT_ROOT", tmp_path):
            # No logs/hooks.log exists
            await runner.step_hooks_audit()

        # Step should complete and store progress
        progress = runner.state.step_progress.get("hooks_audit")
        assert progress is not None
        assert "error" not in progress

    @pytest.mark.asyncio
    async def test_handles_missing_settings_json(self, runner, tmp_path):
        """Should not crash when .claude/settings.json does not exist."""
        # Create logs dir with empty hooks.log so log scanning works
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "hooks.log").write_text("")

        with patch("scripts.reflections.PROJECT_ROOT", tmp_path):
            await runner.step_hooks_audit()

        progress = runner.state.step_progress.get("hooks_audit")
        assert progress is not None
        assert "error" not in progress


class TestHooksAuditSettingsValidation:
    """Test settings.json validation logic."""

    @pytest.mark.asyncio
    async def test_detects_missing_script_files(self, runner, tmp_path):
        """Should report findings when hook scripts don't exist on disk."""
        # Create logs dir
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "hooks.log").write_text("")

        # Create settings.json with a hook pointing to a non-existent script
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python .claude/hooks/nonexistent_script.py",
                            }
                        ],
                    }
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        with patch("scripts.reflections.PROJECT_ROOT", tmp_path):
            await runner.step_hooks_audit()

        findings = runner.state.findings.get("ai:hooks_audit", [])
        assert any("nonexistent_script.py" in f for f in findings)

    @pytest.mark.asyncio
    async def test_detects_stop_hooks_without_or_true(self, runner, tmp_path):
        """Stop hooks must have || true to avoid blocking Claude."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "hooks.log").write_text("")

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        # Create the script so it passes the "exists" check
        hooks_dir = claude_dir / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "stop.py").write_text("# placeholder")

        settings = {
            "hooks": {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python .claude/hooks/stop.py",
                            }
                        ],
                    }
                ]
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        with patch("scripts.reflections.PROJECT_ROOT", tmp_path):
            await runner.step_hooks_audit()

        findings = runner.state.findings.get("ai:hooks_audit", [])
        assert any("|| true" in f for f in findings)


class TestHooksAuditLogErrors:
    """Test hooks.log error extraction."""

    @pytest.mark.asyncio
    async def test_counts_recent_errors(self, runner, tmp_path):
        """Should count errors from the last 24 hours."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        now = datetime.now()
        recent_ts = now.strftime("%Y-%m-%d %H:%M:%S")
        old_ts = (now - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")

        log_content = (
            f"{recent_ts},000 - hooks - ERROR - Recent hook failure\n"
            f"{old_ts},000 - hooks - ERROR - Old hook failure\n"
        )
        (logs_dir / "hooks.log").write_text(log_content)

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"hooks": {}}))

        with patch("scripts.reflections.PROJECT_ROOT", tmp_path):
            await runner.step_hooks_audit()

        progress = runner.state.step_progress.get("hooks_audit", {})
        assert progress.get("errors_24h") == 1
