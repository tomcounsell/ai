"""Agent teams are disabled for every headless ``claude -p`` spawn.

Decision record: docs/features/agent-teams-headless-policy.md. The fleet-wide
user settings enable CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS for interactive
sessions, and the user-settings ``env`` block overwrites the subprocess
environment — so the disable MUST ride a CLI ``--settings`` source (the only
higher-precedence layer). Two seams are covered:

1. Role sessions (PM/dev/teammate): the per-session settings file written by
   ``generate_hook_settings`` carries the env override.
2. Everything else through the harness (message drafter, probes,
   drafter-review): ``get_response_via_harness`` injects an inline
   ``--settings`` JSON when no settings file is supplied.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.session_runner.hook_edge import HEADLESS_ENV_OVERRIDES, generate_hook_settings

# ---------------------------------------------------------------------------
# Helpers (mirrors fixtures in test_session_model_routing.py)
# ---------------------------------------------------------------------------


async def _async_lines(payload: str):
    for line in payload.splitlines(keepends=True):
        yield line.encode("utf-8")


def _stub_subprocess(mock_exec, result_text: str = "ok", session_id: str = "sess_abc"):
    stdout_data = (
        json.dumps({"type": "result", "result": result_text, "session_id": session_id}) + "\n"
    )
    mock_proc = AsyncMock()
    mock_proc.stdout = _async_lines(stdout_data)
    mock_proc.stderr = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    mock_exec.return_value = mock_proc
    return mock_proc


# ---------------------------------------------------------------------------
# The override constant itself
# ---------------------------------------------------------------------------


def test_override_disables_agent_teams_flag():
    """ "0" is outside Claude Code's truthy set {"1","true","yes","on"}."""
    assert HEADLESS_ENV_OVERRIDES["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "0"


# ---------------------------------------------------------------------------
# Seam 1: role-session settings file
# ---------------------------------------------------------------------------


def test_generated_hook_settings_disable_agent_teams(tmp_path):
    settings_path, _edge = generate_hook_settings(tmp_path, tmp_path / "e.ndjson")
    settings = json.loads(open(settings_path).read())
    assert settings["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "0"
    # The env block is additive — the hooks the runner depends on survive.
    assert "Stop" in settings["hooks"]


# ---------------------------------------------------------------------------
# Seam 2: harness inline --settings fallback
# ---------------------------------------------------------------------------


class TestHarnessInlineSettingsFallback:
    @pytest.mark.asyncio
    async def test_no_settings_path_injects_inline_override(self):
        from agent.session_runner.harness.claude import get_response_via_harness

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            _stub_subprocess(mock_exec)
            await get_response_via_harness(message="hello", working_dir="/tmp/test")

            argv = mock_exec.call_args.args
            assert "--settings" in argv, f"--settings missing from argv: {argv}"
            idx = argv.index("--settings")
            inline = json.loads(argv[idx + 1])
            assert inline["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "0"
            # --settings must precede the positional message.
            assert idx < argv.index("hello")

    @pytest.mark.asyncio
    async def test_settings_path_used_verbatim_without_inline_duplicate(self, tmp_path):
        from agent.session_runner.harness.claude import get_response_via_harness

        settings_path, _edge = generate_hook_settings(tmp_path, tmp_path / "e.ndjson")
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            _stub_subprocess(mock_exec)
            await get_response_via_harness(
                message="hello", working_dir="/tmp/test", settings_path=settings_path
            )

            argv = mock_exec.call_args.args
            settings_flags = [i for i, a in enumerate(argv) if a == "--settings"]
            assert len(settings_flags) == 1, f"expected exactly one --settings: {argv}"
            assert argv[settings_flags[0] + 1] == settings_path
