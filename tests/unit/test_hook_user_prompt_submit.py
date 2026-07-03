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

    def test_no_session_type_kwarg_defaults_to_eng(self):
        """When session_type kwarg is absent (env var was None), session_type is 'eng'."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = None
            session = AgentSession.create_local(
                session_id="local-abc-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "eng"

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

    def test_session_type_eng_stored(self):
        """When session_type='eng' is passed (from SESSION_TYPE env var), session stores it."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = "eng"
            session = AgentSession.create_local(
                session_id="local-eng-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "eng"

    def test_session_type_eng_explicit_stored(self):
        """When SESSION_TYPE=eng is explicitly set, session stores 'eng' explicitly."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = "eng"
            session = AgentSession.create_local(
                session_id="local-eng-def2",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "eng"


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

    def test_main_skips_create_local_when_no_env_vars(self, monkeypatch):
        """No env vars set -> skip create_local entirely (issue #1001)."""
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
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

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

        mock_create.assert_not_called()

    def test_main_creates_session_when_session_type_set(self, monkeypatch):
        """When SESSION_TYPE is set (worker-spawned), main() creates AgentSession."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Worker session",
            "session_id": "test-session-st",
            "cwd": "/tmp",
        }
        fake_sidecar = {}
        fake_agent_session = MagicMock()
        fake_agent_session.agent_session_id = "mock-agent-id-st"

        monkeypatch.setenv("SESSION_TYPE", "dev")
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

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

    def test_main_blocks_parent_linked_create_by_default(self, monkeypatch):
        """VALOR_PARENT_SESSION_ID set -> NO AgentSession created (#1633 stopgap)."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Child session",
            "session_id": "test-session-ps",
            "cwd": "/tmp",
        }
        fake_sidecar = {}

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_ALLOW_CHILD_SESSIONS", raising=False)
        monkeypatch.setenv("VALOR_PARENT_SESSION_ID", "agt_parent123")

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch("models.agent_session.AgentSession.create_local") as mock_create,
        ):
            hook.main()

        mock_create.assert_not_called()

    def test_main_creates_parent_linked_session_with_bypass(self, monkeypatch):
        """VALOR_PARENT_SESSION_ID + VALOR_ALLOW_CHILD_SESSIONS=1 -> create with parent."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Child session",
            "session_id": "test-session-ps-bypass",
            "cwd": "/tmp",
        }
        fake_sidecar = {}
        fake_agent_session = MagicMock()
        fake_agent_session.agent_session_id = "mock-agent-id-ps"

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.setenv("VALOR_ALLOW_CHILD_SESSIONS", "1")
        monkeypatch.setenv("VALOR_PARENT_SESSION_ID", "agt_parent123")

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
        assert call_kwargs.get("parent_agent_session_id") == "agt_parent123"

    def test_main_blocks_create_when_both_env_vars_set_without_bypass(self, monkeypatch):
        """SESSION_TYPE + VALOR_PARENT_SESSION_ID without bypass -> NO create (#1633).

        Guards against an implementation that silently creates a parentless
        record instead of skipping: the parent env var forces a full skip.
        """
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Both env vars, blocked",
            "session_id": "test-session-both-blocked",
            "cwd": "/tmp",
        }
        fake_sidecar = {}

        monkeypatch.setenv("SESSION_TYPE", "teammate")
        monkeypatch.delenv("VALOR_ALLOW_CHILD_SESSIONS", raising=False)
        monkeypatch.setenv("VALOR_PARENT_SESSION_ID", "agt_parent456")

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch("models.agent_session.AgentSession.create_local") as mock_create,
        ):
            hook.main()

        mock_create.assert_not_called()

    def test_main_creates_session_when_both_env_vars_set(self, monkeypatch):
        """Both env vars set with bypass -> create AgentSession."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Both env vars",
            "session_id": "test-session-both",
            "cwd": "/tmp",
        }
        fake_sidecar = {}
        fake_agent_session = MagicMock()
        fake_agent_session.agent_session_id = "mock-agent-id-both"

        monkeypatch.setenv("SESSION_TYPE", "teammate")
        monkeypatch.setenv("VALOR_ALLOW_CHILD_SESSIONS", "1")
        monkeypatch.setenv("VALOR_PARENT_SESSION_ID", "agt_parent456")

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
        assert call_kwargs.get("session_type") == "teammate"
        assert call_kwargs.get("parent_agent_session_id") == "agt_parent456"


class TestPhantomTwinPrevention:
    """Phantom PM twin prevention (issue #1157).

    When the worker sets AGENT_SESSION_ID and/or VALOR_SESSION_ID to an
    existing live AgentSession, the hook must attach to that record and
    return WITHOUT calling AgentSession.create_local(). This guarantees
    that worker-spawned subprocesses produce exactly ONE AgentSession row.
    """

    def test_attaches_to_worker_session_when_agent_session_id_set(self, monkeypatch):
        """AGENT_SESSION_ID resolves to a live session -> create_local NOT called."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Worker-spawned first prompt",
            "session_id": "claude-uuid-worker-1",
            "cwd": "/tmp",
        }
        fake_sidecar = {}

        # Simulate a live worker-created AgentSession
        fake_worker_session = MagicMock()
        fake_worker_session.agent_session_id = "agt_real_worker"
        fake_worker_session.status = "running"

        monkeypatch.setenv("AGENT_SESSION_ID", "agt_real_worker")
        monkeypatch.setenv("SESSION_TYPE", "pm")  # worker also sets this
        monkeypatch.setenv("VALOR_PARENT_SESSION_ID", "agt_parent_789")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar") as mock_save_sidecar,
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch(
                "models.agent_session.AgentSession.get_by_id", return_value=fake_worker_session
            ) as mock_get_by_id,
            patch("models.agent_session.AgentSession.create_local") as mock_create,
        ):
            hook.main()

        mock_get_by_id.assert_called_with("agt_real_worker")
        mock_create.assert_not_called()
        # Sidecar was written with the worker session's agent_session_id
        mock_save_sidecar.assert_called()
        written_sidecar = mock_save_sidecar.call_args.args[1]
        assert written_sidecar.get("agent_session_id") == "agt_real_worker"

    def test_attaches_via_valor_session_id_fallback(self, monkeypatch):
        """AGENT_SESSION_ID missing, VALOR_SESSION_ID resolves via filter lookup."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Fallback path",
            "session_id": "claude-uuid-worker-2",
            "cwd": "/tmp",
        }
        fake_sidecar = {}

        fake_worker_session = MagicMock()
        fake_worker_session.agent_session_id = "agt_fallback_worker"
        fake_worker_session.status = "running"

        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        monkeypatch.setenv("VALOR_SESSION_ID", "0_1777000000000")
        monkeypatch.setenv("SESSION_TYPE", "pm")
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar") as mock_save_sidecar,
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("models.agent_session.AgentSession.create_local") as mock_create,
        ):
            mock_query.filter.return_value = [fake_worker_session]
            hook.main()

        mock_create.assert_not_called()
        mock_save_sidecar.assert_called()
        written_sidecar = mock_save_sidecar.call_args.args[1]
        assert written_sidecar.get("agent_session_id") == "agt_fallback_worker"

    def test_falls_through_when_worker_session_terminal(self, monkeypatch):
        """AGENT_SESSION_ID -> TERMINAL session -> fall through, create_local called (#1113)."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Terminal worker session",
            "session_id": "claude-uuid-terminal",
            "cwd": "/tmp",
        }
        fake_sidecar = {}

        fake_terminal_session = MagicMock()
        fake_terminal_session.agent_session_id = "agt_killed"
        fake_terminal_session.status = "killed"  # terminal

        fake_new_session = MagicMock()
        fake_new_session.agent_session_id = "mock-new-id"

        monkeypatch.setenv("AGENT_SESSION_ID", "agt_killed")
        monkeypatch.setenv("SESSION_TYPE", "pm")  # gate allows create_local to fire
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch(
                "models.agent_session.AgentSession.get_by_id", return_value=fake_terminal_session
            ),
            patch(
                "models.agent_session.AgentSession.create_local", return_value=fake_new_session
            ) as mock_create,
        ):
            hook.main()

        # Terminal session -> fall through to existing gate -> create_local IS called
        mock_create.assert_called_once()

    def test_falls_through_when_get_by_id_raises(self, monkeypatch):
        """If get_by_id raises, hook does NOT propagate and falls through to existing gate."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "get_by_id raises",
            "session_id": "claude-uuid-raise",
            "cwd": "/tmp",
        }
        fake_sidecar = {}

        fake_new_session = MagicMock()
        fake_new_session.agent_session_id = "mock-new-id-raise"

        monkeypatch.setenv("AGENT_SESSION_ID", "agt_boom")
        monkeypatch.setenv("SESSION_TYPE", "pm")
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch(
                "models.agent_session.AgentSession.get_by_id",
                side_effect=RuntimeError("redis unavailable"),
            ),
            patch(
                "models.agent_session.AgentSession.create_local", return_value=fake_new_session
            ) as mock_create,
        ):
            # Must not raise
            hook.main()

        # Fell through to create_local (gate allows since SESSION_TYPE is set)
        mock_create.assert_called_once()

    def test_subsequent_prompt_miss_on_worker_session_is_harmless(self, monkeypatch):
        """Subsequent-prompt branch: sidecar has a real worker agent_session_id (not local-*).

        The filter(session_id=f'local-{session_id}') at line 65 will miss, the
        re-activation branch silently no-ops — that is correct behavior because
        the worker owns the real session's status. Assert: no exception, no
        create_local call, no sidecar re-write.
        """
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "Subsequent prompt in worker session",
            "session_id": "claude-uuid-subseq",
            "cwd": "/tmp",
        }
        # Sidecar already has a worker agent_session_id (not a local-* one)
        fake_sidecar = {"agent_session_id": "agt_real_worker_subseq"}

        monkeypatch.setenv("AGENT_SESSION_ID", "agt_real_worker_subseq")
        monkeypatch.setenv("SESSION_TYPE", "pm")

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value=fake_sidecar),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar") as mock_save_sidecar,
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
            patch("models.agent_session.AgentSession.query") as mock_query,
            patch("models.agent_session.AgentSession.create_local") as mock_create,
        ):
            # filter returns [] -> re-activation branch silently no-ops
            mock_query.filter.return_value = []
            # Must not raise
            hook.main()

        mock_create.assert_not_called()
        # Sidecar was not re-written (the re-activation branch didn't find anything)
        mock_save_sidecar.assert_not_called()


class TestPrefetchWiring:
    """Tests for the UserPromptSubmit -> prefetch wiring (issue #1180)."""

    def test_main_invokes_prefetch_after_ingest(self, monkeypatch):
        """main() calls memory_bridge.prefetch with session_id, prompt, cwd."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "investigate the auth flow that broke after PR 800 deployment",
            "session_id": "claude-uuid-pf-1",
            "cwd": "/tmp",
        }

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest") as mock_ingest,
            patch("hook_utils.memory_bridge.prefetch", return_value=None) as mock_prefetch,
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value={}),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
        ):
            hook.main()

        # Both ingest and prefetch fire on a fresh prompt
        mock_ingest.assert_called_once()
        mock_prefetch.assert_called_once()
        call_args, call_kwargs = mock_prefetch.call_args
        assert call_args[0] == "claude-uuid-pf-1"
        assert call_args[1] == "investigate the auth flow that broke after PR 800 deployment"
        assert call_kwargs.get("cwd") == "/tmp"

    def test_main_emits_hookspecific_output_when_prefetch_returns_string(self, monkeypatch, capsys):
        """When prefetch returns thoughts, main() prints hookSpecificOutput JSON."""
        import json as _json

        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "investigate the auth flow that broke after PR 800 deployment",
            "session_id": "claude-uuid-pf-2",
            "cwd": "/tmp",
        }

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch(
                "hook_utils.memory_bridge.prefetch",
                return_value="<thought>auth notes</thought>",
            ),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value={}),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
        ):
            hook.main()

        captured = capsys.readouterr()
        # Locate a JSON line in stdout that has the hookSpecificOutput shape
        parsed = None
        for line in captured.out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "hookSpecificOutput" in parsed:
                break
            parsed = None

        assert parsed is not None, f"No hookSpecificOutput JSON in stdout: {captured.out!r}"
        hso = parsed["hookSpecificOutput"]
        assert hso.get("hookEventName") == "UserPromptSubmit"
        assert hso.get("additionalContext") == "<thought>auth notes</thought>"

    def test_main_no_output_when_prefetch_returns_none(self, monkeypatch, capsys):
        """When prefetch returns None, main() does not print any JSON payload."""
        import json as _json

        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "investigate the auth flow that broke after PR 800 deployment",
            "session_id": "claude-uuid-pf-3",
            "cwd": "/tmp",
        }

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.prefetch", return_value=None),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value={}),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
        ):
            hook.main()

        captured = capsys.readouterr()
        for line in captured.out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            assert "hookSpecificOutput" not in parsed, (
                f"Unexpected hookSpecificOutput on stdout: {captured.out!r}"
            )

    def test_main_swallows_prefetch_exception(self, monkeypatch):
        """A raised exception in prefetch is swallowed -- main() still completes."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "investigate the auth flow that broke after PR 800 deployment",
            "session_id": "claude-uuid-pf-4",
            "cwd": "/tmp",
        }

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch(
                "hook_utils.memory_bridge.prefetch",
                side_effect=RuntimeError("boom"),
            ),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value={}),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
        ):
            # Must not raise
            hook.main()

    def test_main_skips_prefetch_when_session_id_missing(self, monkeypatch):
        """Without session_id, prefetch is not invoked (no sidecar to write)."""
        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "investigate the auth flow that broke after PR 800 deployment",
            # no session_id
            "cwd": "/tmp",
        }

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.prefetch") as mock_prefetch,
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value={}),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
        ):
            hook.main()

        mock_prefetch.assert_not_called()


class TestPrivateTagHookIngestion:
    """sdlc-1179: a Claude Code prompt with <private>X</private> wrapping must
    not result in a Memory record containing X.

    The hook calls memory_bridge.ingest(prompt) on UserPromptSubmit; the
    helper applies strip_private(content) before any Memory.safe_save call.
    """

    def test_ingest_called_via_hook_strips_private_before_save(self, monkeypatch):
        """End-to-end via the live hook + live memory_bridge.ingest:

        Patch only Memory.safe_save and bloom; let the actual ingest() run so
        the strip_private path is exercised. Assert the saved content excludes
        the wrapped region.
        """
        from hook_utils.memory_bridge import ingest

        captured = {}

        def fake_safe_save(**kwargs):
            captured.update(kwargs)
            return MagicMock()  # Non-None = saved

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom
        mock_memory_cls.safe_save.side_effect = fake_safe_save

        prompt = (
            "Refactor the auth handler. The current key is "
            "<private>sk-very-secret-redacted</private>. Should we rotate?"
        )

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("models.memory.SOURCE_HUMAN", "human"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            result = ingest(prompt)

        assert result is True
        saved_content = captured.get("content", "")
        assert "sk-very-secret-redacted" not in saved_content
        assert "<private>" not in saved_content
        assert "</private>" not in saved_content
        # Real (non-private) content survives.
        assert "Refactor the auth handler" in saved_content
        assert "rotate" in saved_content.lower()


class TestMemoryDeadlineGuard:
    """The SIGALRM wall-clock deadline around ingest + prefetch (degraded-Redis guard).

    A degraded Redis (5s-per-op socket timeout, no enforced retry budget) can
    stack ingest + prefetch waits past the harness's 15s UserPromptSubmit
    budget, causing "hook timed out after 15s -- output discarded". The hook
    arms a SIGALRM alarm for MEMORY_HOOK_DEADLINE_SECONDS so it always returns
    well under that limit, emitting no partial context on deadline.
    """

    def test_deadline_constant_exists_and_under_harness_limit(self):
        """The deadline constant exists, is a positive int, and is < 15s harness budget."""
        hook = _load_hook_module()
        assert hasattr(hook, "MEMORY_HOOK_DEADLINE_SECONDS")
        budget = hook.MEMORY_HOOK_DEADLINE_SECONDS
        assert isinstance(budget, int)
        assert 0 < budget < 15

    def test_deadline_aborts_slow_memory_work_without_partial_output(self, monkeypatch, capsys):
        """When prefetch hangs past the deadline, main() returns promptly emitting nothing.

        Patches the deadline down to 1s and makes prefetch sleep well past it.
        The SIGALRM handler interrupts the sleep, so the test takes ~1s (not 8s),
        and no hookSpecificOutput JSON is printed (no partial context leaks).
        """
        import json as _json
        import time as _time

        hook = _load_hook_module()
        # Shrink the deadline so the test is fast -- do NOT actually sleep 8s.
        monkeypatch.setattr(hook, "MEMORY_HOOK_DEADLINE_SECONDS", 1)

        fake_hook_input = {
            "prompt": "investigate the redis recall path and bloom filter latency for memory",
            "session_id": "claude-uuid-deadline-1",
            "cwd": "/tmp",
        }

        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        def _slow_prefetch(*_args, **_kwargs):
            # Simulates a hung Redis socket. The alarm fires at 1s and raises
            # _MemoryDeadlineExceeded (a BaseException), interrupting this sleep.
            _time.sleep(30)
            return "<thought>should never be emitted</thought>"

        started = _time.monotonic()
        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.prefetch", side_effect=_slow_prefetch),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value={}),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
        ):
            hook.main()  # Must not raise and must return promptly.
        elapsed = _time.monotonic() - started

        # The alarm interrupted the 30s sleep near the 1s deadline.
        assert elapsed < 10, f"Deadline did not abort slow memory work (took {elapsed:.1f}s)"

        # No partial context leaked to stdout.
        captured = capsys.readouterr()
        for line in captured.out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            assert "hookSpecificOutput" not in parsed, (
                f"Partial context leaked on deadline: {captured.out!r}"
            )

    def test_deadline_alarm_cancelled_after_fast_memory_work(self, monkeypatch):
        """After a fast happy path, the alarm is cancelled (no leftover pending alarm)."""
        import signal as _signal

        hook = _load_hook_module()

        fake_hook_input = {
            "prompt": "investigate the auth flow that broke after PR 800 deployment",
            "session_id": "claude-uuid-deadline-2",
            "cwd": "/tmp",
        }
        monkeypatch.delenv("SESSION_TYPE", raising=False)
        monkeypatch.delenv("VALOR_PARENT_SESSION_ID", raising=False)

        with (
            patch.object(hook, "read_hook_input", return_value=fake_hook_input),
            patch("hook_utils.memory_bridge.ingest"),
            patch("hook_utils.memory_bridge.prefetch", return_value=None),
            patch("hook_utils.memory_bridge.load_agent_session_sidecar", return_value={}),
            patch("hook_utils.memory_bridge.save_agent_session_sidecar"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="ai"),
        ):
            hook.main()

        # signal.alarm(0) returns the number of seconds left on any pending
        # alarm; 0 means the hook correctly cancelled its alarm in finally.
        remaining = _signal.alarm(0)
        assert remaining == 0, f"Alarm was left pending after main() ({remaining}s remaining)"
