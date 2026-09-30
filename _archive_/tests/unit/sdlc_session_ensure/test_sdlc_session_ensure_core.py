"""Unit tests for tools.sdlc_session_ensure: core ensure_session, CLI, message text (#2879)."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

from tests.db_claim import subprocess_env
from tests.unit.session_lookup_mock import wire_session_lookup

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


class TestEnsureSession:
    """Tests for the ensure_session function."""

    def test_returns_existing_session_by_issue(self):
        from tools.sdlc_session_ensure import ensure_session

        mock_session = MagicMock()
        mock_session.session_id = "sdlc-local-941"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]  # post-save readback
        wire_session_lookup(mock_as)

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=mock_session),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=941)

        assert result["session_id"] == "sdlc-local-941"
        assert result["created"] is False
        # ensure_session mints and emits the run identity (#2003), mirrored
        # to the session record.
        assert result["run_id"]
        assert mock_session.active_run_id == result["run_id"]

    def test_creates_new_session(self):
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-942"

        mock_as = MagicMock()
        # First filter call: idempotent existing-by-id check (none). Second:
        # the post-save run_id readback (the just-created session).
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        wire_session_lookup(mock_as)
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(
                issue_number=942,
                issue_url="https://github.com/tomcounsell/ai/issues/942",
            )

        assert result["session_id"] == "sdlc-local-942"
        assert result["created"] is True
        assert result["run_id"]
        assert mock_new_session.active_run_id == result["run_id"]
        mock_as.create_local.assert_called_once()

    def test_creates_new_session_with_is_ledger_true_at_create_call(self):
        """Non-executable ledger flag (#2042): is_ledger=True must be present
        in the SAME kwargs dict passed to create_local(), not added by a
        follow-up write. This closes the race where a worker could observe
        the row before a separate is_ledger=True write landed."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-947"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        wire_session_lookup(mock_as)
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=947)

        assert result["created"] is True
        mock_as.create_local.assert_called_once()
        # is_ledger=True must be a kwarg of the create_local() call itself --
        # present on the very first persisted row, not a later save().
        _call_args, call_kwargs = mock_as.create_local.call_args
        assert call_kwargs.get("is_ledger") is True

    def test_idempotent_by_session_id(self):
        """If a session with sdlc-local-{N} already exists, return it."""
        from tools.sdlc_session_ensure import ensure_session

        mock_existing = MagicMock()
        mock_existing.session_id = "sdlc-local-943"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_existing]
        wire_session_lookup(mock_as)

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
        ):
            result = ensure_session(issue_number=943)

        assert result["session_id"] == "sdlc-local-943"
        assert result["created"] is False
        assert result["run_id"]

    def test_returns_empty_for_invalid_issue_number(self):
        from tools.sdlc_session_ensure import ensure_session

        assert ensure_session(issue_number=0) == {}
        assert ensure_session(issue_number=-1) == {}

    def test_handles_redis_error_gracefully(self):
        from tools.sdlc_session_ensure import ensure_session

        with patch(
            "tools._sdlc_utils.find_session_by_issue",
            side_effect=ConnectionError("Redis down"),
        ):
            result = ensure_session(issue_number=941)

        assert result == {}

    def test_transition_status_failure_still_returns_session(self):
        """Session is usable even if transition_status fails."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-944"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        wire_session_lookup(mock_as)
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "models.session_lifecycle.transition_status",
                side_effect=RuntimeError("transition failed"),
            ),
        ):
            result = ensure_session(issue_number=944)

        assert result["session_id"] == "sdlc-local-944"
        assert result["created"] is True
        assert result["run_id"]

    def test_project_key_resolution_error_returns_empty(self):
        """#1158: on ProjectKeyResolutionError, ensure_session returns {} and
        does NOT create an AgentSession with a coerced/wrong project_key.

        The plan's governing principle: if the project→repo pairing can't be
        resolved, refuse to create a session rather than silently misroute.
        """
        from tools.sdlc_session_ensure import ensure_session
        from tools.valor_session import ProjectKeyResolutionError

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        wire_session_lookup(mock_as)

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.valor_session.resolve_project_key",
                side_effect=ProjectKeyResolutionError(
                    cwd="/tmp/unknown", available_keys=["valor", "ai"]
                ),
            ),
        ):
            result = ensure_session(issue_number=945)

        # Empty dict → no session created.
        assert result == {}
        # AgentSession.create_local was NEVER called — no coercion to a wrong
        # project happened.
        mock_as.create_local.assert_not_called()

    def test_projects_config_unavailable_error_returns_empty(self):
        """#1158: on ProjectsConfigUnavailableError (e.g., projects.json load
        failure), ensure_session returns {} with no session created.
        """
        from tools.sdlc_session_ensure import ensure_session
        from tools.valor_session import ProjectsConfigUnavailableError

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        wire_session_lookup(mock_as)

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch(
                "tools.valor_session.resolve_project_key",
                side_effect=ProjectsConfigUnavailableError(
                    "could not load projects.json: permission denied"
                ),
            ),
        ):
            result = ensure_session(issue_number=946)

        assert result == {}
        mock_as.create_local.assert_not_called()


class TestCLI:
    """Tests for CLI invocation."""

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_session_ensure", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=subprocess_env(project_root=REPO_ROOT),
        )
        assert result.returncode == 0
        assert "--issue-number" in result.stdout
        assert "--issue-url" in result.stdout

    def test_missing_required_arg(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_session_ensure"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=subprocess_env(project_root=REPO_ROOT),
        )
        assert result.returncode != 0


class TestCreateLocalMessageText:
    """Fix A (#1741): create_local receives a non-empty, issue-anchored message_text.

    Without Fix A, ``message_text`` was not passed to ``create_local``, so the
    AgentSession was created with ``message_text=None``. The executor then built
    the PTY container's first turn as "MESSAGE: None", which primed the granite
    PM with a phantom task and triggered a silent [/complete] no-op.

    These tests assert that ``create_local`` is always called with:
    - ``message_text`` kwarg present and non-empty
    - the text references the issue number (issue-anchored)
    - when ``issue_url`` is supplied, it is also embedded in the text
    """

    def test_create_local_receives_message_text(self):
        """create_local is called with a non-empty message_text kwarg."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1741"

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = [[], [mock_new_session]]
        wire_session_lookup(mock_as)
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            result = ensure_session(issue_number=1741)

        assert result["session_id"] == "sdlc-local-1741"
        assert result["created"] is True
        mock_as.create_local.assert_called_once()
        _, kwargs = mock_as.create_local.call_args
        assert "message_text" in kwargs, "create_local was not called with message_text kwarg"
        assert kwargs["message_text"], "message_text must be non-empty"

    def test_message_text_is_issue_anchored(self):
        """message_text references the issue number so the PM has a real goal anchor."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1742"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        wire_session_lookup(mock_as)
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            ensure_session(issue_number=1742)

        _, kwargs = mock_as.create_local.call_args
        msg = kwargs["message_text"]
        # Must reference the issue number so the PM can find the work to do.
        assert "1742" in msg, f"message_text must reference issue number 1742; got: {msg!r}"

    def test_message_text_embeds_issue_url_when_provided(self):
        """When issue_url is supplied, it is embedded in message_text."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1743"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        wire_session_lookup(mock_as)
        mock_as.create_local.return_value = mock_new_session

        issue_url = "https://github.com/tomcounsell/ai/issues/1743"

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            ensure_session(issue_number=1743, issue_url=issue_url)

        _, kwargs = mock_as.create_local.call_args
        msg = kwargs["message_text"]
        assert issue_url in msg, (
            f"message_text must embed the issue_url when supplied; got: {msg!r}"
        )

    def test_message_text_present_without_issue_url(self):
        """message_text is non-empty even when no issue_url is supplied."""
        from tools.sdlc_session_ensure import ensure_session

        mock_new_session = MagicMock()
        mock_new_session.session_id = "sdlc-local-1744"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []
        wire_session_lookup(mock_as)
        mock_as.create_local.return_value = mock_new_session

        with (
            patch("tools._sdlc_utils.find_session_by_issue", return_value=None),
            patch("models.agent_session.AgentSession", mock_as),
            patch("models.session_lifecycle.transition_status"),
        ):
            ensure_session(issue_number=1744)

        _, kwargs = mock_as.create_local.call_args
        msg = kwargs.get("message_text", "")
        assert msg and msg.strip(), "message_text must be non-empty even without issue_url"
        assert "1744" in msg
