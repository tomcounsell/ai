"""Regression test for issue #1113 (Root Cause 1).

The UserPromptSubmit hook previously called transition_status with
reject_from_terminal=False, which bypassed the terminal guard and re-activated
killed sessions every time a new prompt arrived. Once a PM session was killed
via `valor-session kill --id ...`, the next prompt would resurrect it — the
worker picked it back up as "running", producing a zombie.

This test covers the fix: the hook MUST check the current status BEFORE
transitioning, and refuse to re-activate sessions that are in terminal states
(killed, completed, failed, abandoned, cancelled).
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HOOK_PATH = (
    Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "user_prompt_submit.py"
)


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("user_prompt_submit", str(_HOOK_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTerminalSessionNotReactivated:
    """Killed/completed/failed sessions must NOT be re-activated by the hook."""

    @pytest.mark.parametrize(
        "terminal_status",
        ["killed", "completed", "failed", "abandoned", "cancelled"],
    )
    def test_terminal_session_is_not_reactivated(self, terminal_status, monkeypatch):
        """UserPromptSubmit hook on a terminal AgentSession must be a no-op.

        Reproduces the zombie revival: a killed PM session should stay killed
        even when a new prompt arrives via the same session_id.
        """
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Keep going!",
            "session_id": "test-session-zombie",
            "cwd": "/tmp",
        }
        # Sidecar has an agent_session_id -> subsequent-prompt branch fires
        fake_sidecar = {"agent_session_id": "agt_killed_123"}

        # Existing terminal session in Redis
        fake_session = MagicMock()
        fake_session.status = terminal_status
        fake_session.session_id = "local-test-session-zombie"
        fake_session.agent_session_id = "agt_killed_123"

        # query.filter(session_id=...) returns the terminal session
        fake_query = MagicMock()
        fake_query.filter = MagicMock(return_value=iter([fake_session]))

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.session_lifecycle.transition_status") as mock_transition,
        ):
            hook.main()

        # transition_status must NOT be called on a terminal session
        assert not mock_transition.called, (
            f"Hook called transition_status on a {terminal_status!r} session "
            f"(zombie revival path bypassed terminal guard)"
        )
        # Status must remain terminal
        assert fake_session.status == terminal_status

    def test_running_session_is_still_reactivated(self, monkeypatch):
        """Non-terminal sessions SHOULD still be re-activated (control case)."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Continue work",
            "session_id": "test-session-alive",
            "cwd": "/tmp",
        }
        fake_sidecar = {"agent_session_id": "agt_alive_456"}

        fake_session = MagicMock()
        fake_session.status = "dormant"  # non-terminal
        fake_session.session_id = "local-test-session-alive"
        fake_session.agent_session_id = "agt_alive_456"

        fake_query = MagicMock()
        fake_query.filter = MagicMock(return_value=iter([fake_session]))

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.session_lifecycle.transition_status") as mock_transition,
        ):
            hook.main()

        # transition_status SHOULD be called for non-terminal sessions
        assert mock_transition.called, "Non-terminal session must still be re-activated"
