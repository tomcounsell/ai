"""Unit tests for ``verify.ensure_claude_update_channel`` (fleet channel pin).

The 'stable' Claude Code channel lags 'latest' by many releases, starving the
fleet of silent-exit fixes and blocking newer model ids (see ai repo memory
project_claude_cli_silent_exit_findings). ``/update`` pins every machine to
'latest'; these tests lock in that it flips 'stable', is idempotent once
already on 'latest', preserves all other settings keys, and never crashes on a
missing or malformed settings file.

No real ~/.claude: everything runs against a tmp_path home.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.update import verify


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point verify.Path.home() at a tmp dir with a .claude/ subdir."""
    (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(verify.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _write_settings(home: Path, data: dict) -> Path:
    path = home / ".claude" / "settings.json"
    path.write_text(json.dumps(data))
    return path


def test_flips_stable_to_latest_preserving_other_keys(fake_home: Path):
    path = _write_settings(
        fake_home,
        {"model": "claude-fable-5", "autoUpdatesChannel": "stable", "hooks": {"Stop": [1]}},
    )
    result = verify.ensure_claude_update_channel()
    assert result["changed"] is True
    after = json.loads(path.read_text())
    assert after["autoUpdatesChannel"] == "latest"
    # Untouched keys survive the rewrite.
    assert after["model"] == "claude-fable-5"
    assert after["hooks"] == {"Stop": [1]}


def test_idempotent_when_already_latest(fake_home: Path):
    _write_settings(fake_home, {"autoUpdatesChannel": "latest"})
    result = verify.ensure_claude_update_channel()
    assert result["changed"] is False
    assert "Already on 'latest'" in result["reason"]


def test_sets_channel_when_key_absent(fake_home: Path):
    path = _write_settings(fake_home, {"model": "x"})
    result = verify.ensure_claude_update_channel()
    assert result["changed"] is True
    assert json.loads(path.read_text())["autoUpdatesChannel"] == "latest"


def test_missing_settings_file_is_graceful(fake_home: Path):
    # .claude/ exists but no settings.json inside it.
    result = verify.ensure_claude_update_channel()
    assert result["changed"] is False
    assert "No ~/.claude/settings.json" in result["reason"]


def test_malformed_json_never_crashes(fake_home: Path):
    (fake_home / ".claude" / "settings.json").write_text("{ not valid json ")
    result = verify.ensure_claude_update_channel()
    assert result["changed"] is False
    assert "Failed to read/parse" in result["reason"]
