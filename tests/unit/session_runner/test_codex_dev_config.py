"""Unit tests for the Codex dev-lane session config (plan #2001 Task 3).

``codex_mcp_config_for`` is the flagged-eng-only gate for the session-local
MCP surface: unflagged sessions get None (byte-identical argv), flagged eng
sessions get a one-server config carrying ``AGENT_SESSION_ID``.
``preflight_codex_dev_lane`` is the executor's fail-fast gate.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from config.enums import SessionType


def _session(session_type=SessionType.ENG, dev_harness="codex", sid="agent-id-1"):
    return SimpleNamespace(session_type=session_type, dev_harness=dev_harness, id=sid)


def test_unflagged_session_gets_no_mcp_config():
    from agent.codex_dev_config import codex_mcp_config_for

    assert codex_mcp_config_for(_session(dev_harness=None)) is None


def test_teammate_session_gets_no_mcp_config():
    from agent.codex_dev_config import codex_mcp_config_for

    assert codex_mcp_config_for(_session(session_type=SessionType.TEAMMATE)) is None


def test_missing_id_gets_no_mcp_config():
    from agent.codex_dev_config import codex_mcp_config_for

    assert codex_mcp_config_for(_session(sid=None)) is None


def test_flagged_eng_session_gets_session_scoped_config():
    from agent.codex_dev_config import codex_mcp_config_for

    cfg = codex_mcp_config_for(_session())
    assert set(cfg["mcpServers"]) == {"codex_dev"}
    server = cfg["mcpServers"]["codex_dev"]
    assert server["args"] == ["-m", "mcp_servers.codex_dev_server"]
    assert server["env"] == {"AGENT_SESSION_ID": "agent-id-1"}


def test_config_prefers_project_venv_python(tmp_path):
    from agent.codex_dev_config import codex_mcp_config_for

    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()

    cfg = codex_mcp_config_for(_session(), project_root=str(tmp_path))
    assert cfg["mcpServers"]["codex_dev"]["command"] == str(venv_python)


def test_config_falls_back_to_current_interpreter(tmp_path):
    from agent.codex_dev_config import codex_mcp_config_for

    cfg = codex_mcp_config_for(_session(), project_root=str(tmp_path))
    assert cfg["mcpServers"]["codex_dev"]["command"] == sys.executable


def test_preflight_non_flagged_is_noop():
    from agent.codex_dev_config import preflight_codex_dev_lane

    assert preflight_codex_dev_lane(_session(dev_harness=None), "/tmp") is None


def test_preflight_success_returns_none(monkeypatch):
    import agent.session_runner.harness.codex as codex_mod
    from agent.codex_dev_config import preflight_codex_dev_lane

    monkeypatch.setattr(
        codex_mod, "preflight_codex", lambda sandbox, worktree, api_key=None: ("0.154.0", None)
    )
    assert preflight_codex_dev_lane(_session(), "/tmp") is None


def test_preflight_failure_returns_actionable_error(monkeypatch):
    import agent.session_runner.harness.codex as codex_mod
    from agent.codex_dev_config import preflight_codex_dev_lane

    monkeypatch.setattr(
        codex_mod,
        "preflight_codex",
        lambda sandbox, worktree, api_key=None: (None, "Codex CLI not found."),
    )
    error = preflight_codex_dev_lane(_session(), "/tmp")
    assert error is not None and "Codex CLI not found" in error
