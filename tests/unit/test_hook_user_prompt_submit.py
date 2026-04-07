"""Tests for user_prompt_submit hook session_type env var registration.

Verifies that the hook reads SESSION_TYPE env var and passes it through
to AgentSession.create_local(), so local-* records reflect the correct persona.

The hook script lives in .claude/hooks/ and is not importable as a module from
tests. These tests therefore validate the end-to-end contract through two lenses:
  1. Direct env-var read test — verify os.environ.get("SESSION_TYPE") behaves
     correctly in isolation (trivial sanity test for the pattern used in the hook).
  2. AgentSession.create_local() kwarg pass-through — confirm that passing the
     env value as session_type kwarg stores the correct value in the session record.
  3. Full call-chain test — load the actual hook module, call main(), and assert
     that create_local receives the session_type kwarg derived from the env var.

Together they close the critical path:
  env var → hook reads it → create_local gets it → Redis stores it.
"""

import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestSessionTypeEnvVarPattern:
    """Validate the env-var read pattern used in user_prompt_submit.py."""

    def test_env_var_absent_returns_none(self, monkeypatch):
        """When SESSION_TYPE is not set, os.environ.get returns None."""
        monkeypatch.delenv("SESSION_TYPE", raising=False)
        assert os.environ.get("SESSION_TYPE") is None

    def test_env_var_teammate_returns_teammate(self, monkeypatch):
        """When SESSION_TYPE=teammate, os.environ.get returns 'teammate'."""
        monkeypatch.setenv("SESSION_TYPE", "teammate")
        assert os.environ.get("SESSION_TYPE") == "teammate"

    def test_env_var_pm_returns_pm(self, monkeypatch):
        """When SESSION_TYPE=pm, os.environ.get returns 'pm'."""
        monkeypatch.setenv("SESSION_TYPE", "pm")
        assert os.environ.get("SESSION_TYPE") == "pm"

    def test_conditional_kwarg_omitted_when_none(self):
        """The conditional kwargs pattern omits session_type when value is None."""
        session_type_override = None
        kwargs = {"session_type": session_type_override} if session_type_override else {}
        assert "session_type" not in kwargs

    def test_conditional_kwarg_included_when_set(self):
        """The conditional kwargs pattern includes session_type when value is set."""
        session_type_override = "teammate"
        kwargs = {"session_type": session_type_override} if session_type_override else {}
        assert kwargs.get("session_type") == "teammate"


class TestCreateLocalSessionTypeKwarg:
    """AgentSession.create_local() should store whatever session_type is passed."""

    def test_no_session_type_kwarg_defaults_to_dev(self):
        """When session_type kwarg is absent (env var was None), session_type is 'dev'."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = None
            session = AgentSession.create_local(
                session_id="local-abc-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "dev"

    def test_session_type_teammate_stored(self):
        """When session_type='teammate' is passed (from SESSION_TYPE env var), session stores it."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = "teammate"
            session = AgentSession.create_local(
                session_id="local-tm-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "teammate"

    def test_session_type_pm_stored(self):
        """When session_type='pm' is passed (from SESSION_TYPE env var), session stores it."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = "pm"
            session = AgentSession.create_local(
                session_id="local-pm-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "pm"

    def test_session_type_dev_explicit_stored(self):
        """When SESSION_TYPE=dev is explicitly set, session stores 'dev' explicitly."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = "dev"
            session = AgentSession.create_local(
                session_id="local-dev-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "dev"


# ---------------------------------------------------------------------------
# Helper: load hook module from .claude/hooks/user_prompt_submit.py
# ---------------------------------------------------------------------------

_HOOK_PATH = (
    Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "user_prompt_submit.py"
)


def _load_hook_module():
    """Load user_prompt_submit as a module (importlib, not normal import path)."""
    spec = importlib.util.spec_from_file_location("user_prompt_submit", str(_HOOK_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMainCallChain:
    """Full call-chain test: env var → main() → create_local receives session_type."""

    def test_main_passes_session_type_teammate_to_create_local(self, monkeypatch):
        """When SESSION_TYPE=teammate, main() calls create_local with session_type='teammate'."""
        hook = _load_hook_module()

        # Minimal hook input: first prompt in a new session (no existing agent_session_id)
        fake_hook_input = {
            "prompt": "Hello, teammate!",
            "session_id": "test-session-abc",
            "cwd": "/tmp",
        }

        # Sidecar has no agent_session_id yet (first prompt scenario)
        fake_sidecar = {}

        # AgentSession stub returned by create_local
        fake_agent_session = MagicMock()
        fake_agent_session.agent_session_id = "mock-agent-id-123"

        monkeypatch.setenv("SESSION_TYPE", "teammate")

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch(
                "models.agent_session.AgentSession.create_local", return_value=fake_agent_session
            ) as mock_create,
        ):
            hook.main()

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs.get("session_type") == "teammate", (
            f"Expected create_local to be called with session_type='teammate', "
            f"but got kwargs: {call_kwargs}"
        )

    def test_main_omits_session_type_when_env_var_unset(self, monkeypatch):
        """When SESSION_TYPE is not set, main() calls create_local without session_type kwarg."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Hello, default!",
            "session_id": "test-session-xyz",
            "cwd": "/tmp",
        }
        fake_sidecar = {}
        fake_agent_session = MagicMock()
        fake_agent_session.agent_session_id = "mock-agent-id-456"

        monkeypatch.delenv("SESSION_TYPE", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch(
                "models.agent_session.AgentSession.create_local", return_value=fake_agent_session
            ) as mock_create,
        ):
            hook.main()

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert "session_type" not in call_kwargs, (
            f"Expected create_local to be called WITHOUT session_type kwarg, "
            f"but got kwargs: {call_kwargs}"
        )
