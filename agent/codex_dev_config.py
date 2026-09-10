"""Session-local MCP config for the Codex dev lane (plan #2001, Phase 3).

``codex_mcp_config_for`` builds the per-session MCP config that exposes
the ``codex_dev`` tool to the Claude PM of ONE flagged eng session. It is
passed as ``TurnRequest.mcp_config`` (merged into the harness argv as a
single ``--mcp-config=`` + ``--strict-mcp-config`` pair) and never lands
in global ``.mcp.json`` / ``~/.claude.json`` — unflagged PM/teammate
sessions never see the tool.

Returns None for every non-flagged session (wrong type, unflagged, or
missing id), so unflagged turns stay byte-identical.
"""

from __future__ import annotations

import sys
from typing import Any


def codex_mcp_config_for(session: Any, project_root: str | None = None) -> dict | None:
    """Build the session-local MCP config for a flagged eng session.

    Returns ``{"mcpServers": {"codex_dev": {...}}}`` when
    ``session.session_type`` is eng and ``session.dev_harness == "codex"``,
    else None. The server entry carries ``AGENT_SESSION_ID`` in its env so
    the stateless server resolves its session per call.
    """
    try:
        from config.enums import SessionType
    except Exception:  # noqa: BLE001
        return None
    try:
        session_type = getattr(session, "session_type", None)
        dev_harness = getattr(session, "dev_harness", None)
        agent_session_id = getattr(session, "id", None)
    except Exception:  # noqa: BLE001
        return None
    if session_type != SessionType.ENG or dev_harness != "codex":
        return None
    if not agent_session_id:
        return None
    python = sys.executable or "python3"
    if project_root:
        candidate = f"{project_root}/.venv/bin/python"
        import os

        if os.path.exists(candidate):
            python = candidate
    return {
        "mcpServers": {
            "codex_dev": {
                "command": python,
                "args": ["-m", "mcp_servers.codex_dev_server"],
                "env": {"AGENT_SESSION_ID": str(agent_session_id)},
            }
        }
    }


def preflight_codex_dev_lane(session: Any, working_dir: str) -> str | None:
    """Fail-fast preflight for a flagged eng session (plan #2001, Phase 3).

    Validates Codex binary, minimum version, sandbox policy, exact
    worktree, and auth BEFORE the top-level Claude runner starts, so a
    missing provision fails with an actionable error instead of a
    mid-turn MCP failure. Returns None when the lane is clear, else the
    actionable error text. Non-flagged sessions always return None.
    """
    if getattr(session, "dev_harness", None) != "codex":
        return None
    try:
        from agent.session_runner.harness.codex import preflight_codex
        from config.settings import settings as _settings

        codex_cfg = getattr(_settings, "codex", None)
        sandbox = getattr(codex_cfg, "sandbox", "workspace-write") or "workspace-write"
    except Exception:  # noqa: BLE001
        return (
            "Codex dev lane flagged but lane imports failed — refusing to "
            "start. Check the worker checkout includes the Phase 3 build."
        )
    import os

    _, error = preflight_codex(
        sandbox=sandbox,
        worktree=str(working_dir),
        api_key=os.environ.get("CODEX_API_KEY"),
    )
    return error
