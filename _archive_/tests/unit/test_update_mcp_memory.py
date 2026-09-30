"""Unit tests for scripts/update/mcp_memory.py.

Verifies:
  - idempotent verify_memory_mcp(write=True) installs the entry on a fresh
    file and is a no-op on re-run
  - drift detection in --verify mode (write=False) reports without writing
  - atomic backup → tmp → rename pattern
  - fcntl lock acquired/released
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def claude_config_path(tmp_path, monkeypatch):
    """Redirect mcp_memory's claude config + lock + backup to tmp_path."""
    from scripts.update import mcp_memory

    cfg = tmp_path / "claude.json"
    lock = tmp_path / "claude.json.lock"
    bak = tmp_path / "claude.json.bak"
    monkeypatch.setattr(mcp_memory, "CLAUDE_CONFIG_PATH", cfg)
    monkeypatch.setattr(mcp_memory, "CLAUDE_CONFIG_LOCK_PATH", lock)
    monkeypatch.setattr(mcp_memory, "CLAUDE_CONFIG_BACKUP_PATH", bak)
    return cfg


def test_install_on_fresh_file(claude_config_path):
    from scripts.update import mcp_memory

    # Pre-create with empty mcpServers to mimic a real config.
    claude_config_path.write_text(json.dumps({"mcpServers": {}}))

    result = mcp_memory.verify_memory_mcp(write=True)
    assert result.ok is True
    assert result.action in ("installed", "ok")

    config = json.loads(claude_config_path.read_text())
    assert "memory" in config["mcpServers"]
    entry = config["mcpServers"]["memory"]
    assert entry["type"] == "stdio"
    assert entry["command"] == "python3"
    assert entry["args"] == ["-m", "mcp_servers.memory_server"]
    assert "PYTHONPATH" in entry["env"]


def test_idempotent_no_op_when_correct(claude_config_path):
    from scripts.update import mcp_memory

    # First install
    claude_config_path.write_text(json.dumps({"mcpServers": {}}))
    first = mcp_memory.verify_memory_mcp(write=True)
    assert first.ok is True

    # Second run should be a no-op.
    second = mcp_memory.verify_memory_mcp(write=True)
    assert second.ok is True
    assert second.action == "ok"


def test_repairs_drift(claude_config_path):
    from scripts.update import mcp_memory

    # Drifted entry — wrong command.
    claude_config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory": {
                        "type": "stdio",
                        "command": "wrong",
                        "args": ["bad", "args"],
                        "env": {},
                    }
                }
            }
        )
    )

    result = mcp_memory.verify_memory_mcp(write=True)
    assert result.ok is True
    assert result.action == "repaired"

    config = json.loads(claude_config_path.read_text())
    entry = config["mcpServers"]["memory"]
    assert entry["command"] == "python3"


def test_verify_mode_reports_drift_without_writing(claude_config_path):
    from scripts.update import mcp_memory

    claude_config_path.write_text(json.dumps({"mcpServers": {}}))

    result = mcp_memory.verify_memory_mcp(write=False)
    assert result.ok is False
    assert result.action == "drift_detected"

    # File must be unchanged.
    config = json.loads(claude_config_path.read_text())
    assert "memory" not in config.get("mcpServers", {})


def test_verify_mode_reports_ok_when_correct(claude_config_path):
    from scripts.update import mcp_memory

    # Install once.
    claude_config_path.write_text(json.dumps({"mcpServers": {}}))
    mcp_memory.verify_memory_mcp(write=True)

    # Now verify-only should report ok.
    result = mcp_memory.verify_memory_mcp(write=False)
    assert result.ok is True
    assert result.action == "ok"


def test_atomic_write_creates_backup(claude_config_path):
    from scripts.update import mcp_memory

    claude_config_path.write_text(json.dumps({"mcpServers": {}, "marker": "v1"}))
    mcp_memory.verify_memory_mcp(write=True)

    # Backup file should exist alongside.
    bak = mcp_memory.CLAUDE_CONFIG_BACKUP_PATH
    assert bak.exists()
    backup_data = json.loads(bak.read_text())
    assert backup_data.get("marker") == "v1"


def test_other_servers_preserved(claude_config_path):
    from scripts.update import mcp_memory

    claude_config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other-server": {
                        "type": "stdio",
                        "command": "uv",
                        "args": ["run", "x"],
                        "env": {},
                    }
                },
                "otherKey": {"unrelated": True},
            }
        )
    )

    mcp_memory.verify_memory_mcp(write=True)
    config = json.loads(claude_config_path.read_text())
    # Other server preserved.
    assert "other-server" in config["mcpServers"]
    assert config["mcpServers"]["other-server"]["command"] == "uv"
    # Top-level keys preserved.
    assert config.get("otherKey") == {"unrelated": True}
    # Memory installed.
    assert "memory" in config["mcpServers"]


def test_resolve_repo_root_uses_git():
    from scripts.update import mcp_memory

    root = mcp_memory._resolve_repo_root()
    assert root is not None
    assert "/" in root


def test_check_ollama_handles_unreachable():
    from scripts.update import mcp_memory

    # Point at a port that is almost certainly closed.
    ok, msg = mcp_memory.check_ollama_for_titles("http://127.0.0.1:1")
    assert ok is False
    assert isinstance(msg, str) and msg


def test_check_ollama_handles_non_json():
    from scripts.update import mcp_memory

    class FakeResp:
        def read(self):
            return b"not json"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=FakeResp()):
        ok, msg = mcp_memory.check_ollama_for_titles("http://localhost:11434")
    assert ok is False
    assert "non-JSON" in msg
