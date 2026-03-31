"""Tests for scripts/steer_child.py -- parent-child DevSession steering."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.steer_child import main


@pytest.fixture
def mock_parent():
    """Create a mock parent ChatSession."""
    parent = MagicMock()
    parent.agent_session_id = "parent-001"
    parent.session_type = "chat"
    parent.is_chat = True
    parent.is_dev = False
    return parent


@pytest.fixture
def mock_child():
    """Create a mock child DevSession."""
    child = MagicMock()
    child.agent_session_id = "child-001"
    child.session_type = "dev"
    child.is_chat = False
    child.is_dev = True
    child.parent_chat_session_id = "parent-001"
    child.status = "running"
    child.slug = "my-feature"
    child.current_stage = "BUILD"
    return child


# Patch targets: imports happen inside functions, so patch the source modules
_AGENT_SESSION = "models.agent_session.AgentSession"
_PUSH_STEERING = "agent.steering.push_steering_message"


class TestSteerChild:
    """Tests for the steer command (--session-id + --message)."""

    @patch(_PUSH_STEERING)
    @patch(_AGENT_SESSION)
    def test_valid_steering(self, mock_agent_session_cls, mock_push, mock_child):
        """Successful steering pushes message and exits 0."""
        mock_agent_session_cls.query.get.return_value = mock_child

        result = main(
            [
                "--session-id",
                "child-001",
                "--message",
                "focus on tests",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 0
        mock_push.assert_called_once_with(
            session_id="child-001",
            text="focus on tests",
            sender="ChatSession",
            is_abort=False,
        )

    @patch(_PUSH_STEERING)
    @patch(_AGENT_SESSION)
    def test_abort_flag(self, mock_agent_session_cls, mock_push, mock_child):
        """--abort flag sets is_abort=True in steering message."""
        mock_agent_session_cls.query.get.return_value = mock_child

        result = main(
            [
                "--session-id",
                "child-001",
                "--message",
                "stop everything",
                "--parent-id",
                "parent-001",
                "--abort",
            ]
        )

        assert result == 0
        mock_push.assert_called_once_with(
            session_id="child-001",
            text="stop everything",
            sender="ChatSession",
            is_abort=True,
        )

    @patch(_AGENT_SESSION)
    def test_empty_message_rejected(self, mock_agent_session_cls, mock_child):
        """Empty message text is rejected with exit code 1."""
        mock_agent_session_cls.query.get.return_value = mock_child

        result = main(
            [
                "--session-id",
                "child-001",
                "--message",
                "",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1

    @patch(_AGENT_SESSION)
    def test_whitespace_only_message_rejected(self, mock_agent_session_cls, mock_child):
        """Whitespace-only message is stripped and rejected."""
        mock_agent_session_cls.query.get.return_value = mock_child

        result = main(
            [
                "--session-id",
                "child-001",
                "--message",
                "   ",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1

    @patch(_AGENT_SESSION)
    def test_nonexistent_session_rejected(self, mock_agent_session_cls):
        """Non-existent session ID results in exit code 1."""
        mock_agent_session_cls.query.get.return_value = None

        result = main(
            [
                "--session-id",
                "nonexistent",
                "--message",
                "hello",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1

    @patch(_AGENT_SESSION)
    def test_non_child_rejected(self, mock_agent_session_cls, mock_child):
        """Session that is not a child of the parent is rejected."""
        mock_child.parent_chat_session_id = "other-parent"
        mock_agent_session_cls.query.get.return_value = mock_child

        result = main(
            [
                "--session-id",
                "child-001",
                "--message",
                "hello",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1

    @patch(_AGENT_SESSION)
    def test_non_dev_session_rejected(self, mock_agent_session_cls):
        """Chat session (not DevSession) is rejected as steering target."""
        chat = MagicMock()
        chat.is_dev = False
        mock_agent_session_cls.query.get.return_value = chat

        result = main(
            [
                "--session-id",
                "chat-001",
                "--message",
                "hello",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1

    @patch(_AGENT_SESSION)
    def test_inactive_session_rejected(self, mock_agent_session_cls, mock_child):
        """Completed/non-running session is rejected."""
        mock_child.status = "completed"
        mock_agent_session_cls.query.get.return_value = mock_child

        result = main(
            [
                "--session-id",
                "child-001",
                "--message",
                "hello",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1

    def test_missing_session_id(self):
        """Missing --session-id is rejected."""
        result = main(
            [
                "--message",
                "hello",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1

    def test_missing_message(self):
        """Missing --message is rejected."""
        result = main(
            [
                "--session-id",
                "child-001",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1

    def test_missing_parent_id(self):
        """Missing --parent-id (and no env var) is rejected."""
        env_without_valor = {
            k: v for k, v in __import__("os").environ.items() if k != "VALOR_SESSION_ID"
        }
        with patch.dict("os.environ", env_without_valor, clear=True):
            result = main(
                [
                    "--session-id",
                    "child-001",
                    "--message",
                    "hello",
                ]
            )

            assert result == 1

    @patch(_PUSH_STEERING)
    @patch(_AGENT_SESSION)
    def test_parent_id_from_env(self, mock_agent_session_cls, mock_push, mock_child):
        """VALOR_SESSION_ID env var is used when --parent-id not given."""
        mock_agent_session_cls.query.get.return_value = mock_child

        with patch.dict("os.environ", {"VALOR_SESSION_ID": "parent-001"}):
            result = main(
                [
                    "--session-id",
                    "child-001",
                    "--message",
                    "use env var",
                ]
            )

        assert result == 0
        mock_push.assert_called_once()

    @patch(_AGENT_SESSION)
    def test_session_lookup_exception(self, mock_agent_session_cls):
        """Exception during session lookup is handled gracefully."""
        mock_agent_session_cls.query.get.side_effect = Exception("Redis down")

        result = main(
            [
                "--session-id",
                "child-001",
                "--message",
                "hello",
                "--parent-id",
                "parent-001",
            ]
        )

        assert result == 1


class TestListChildren:
    """Tests for the --list flag."""

    @patch(_AGENT_SESSION)
    def test_list_with_active_children(
        self,
        mock_agent_session_cls,
        mock_parent,
        mock_child,
        capsys,
    ):
        """--list shows active child sessions."""
        mock_agent_session_cls.query.get.return_value = mock_parent
        mock_parent.get_dev_sessions.return_value = [mock_child]

        result = main(["--list", "--parent-id", "parent-001"])

        assert result == 0
        captured = capsys.readouterr()
        assert "child-001" in captured.out
        assert "my-feature" in captured.out

    @patch(_AGENT_SESSION)
    def test_list_no_active_children(self, mock_agent_session_cls, mock_parent, capsys):
        """--list with no active children shows informative message."""
        mock_agent_session_cls.query.get.return_value = mock_parent
        mock_parent.get_dev_sessions.return_value = []

        result = main(["--list", "--parent-id", "parent-001"])

        assert result == 0
        captured = capsys.readouterr()
        assert "No active" in captured.out

    @patch(_AGENT_SESSION)
    def test_list_filters_non_running(
        self,
        mock_agent_session_cls,
        mock_parent,
        mock_child,
        capsys,
    ):
        """--list only shows running children, not completed ones."""
        completed_child = MagicMock()
        completed_child.status = "completed"
        completed_child.agent_session_id = "child-002"

        mock_agent_session_cls.query.get.return_value = mock_parent
        mock_parent.get_dev_sessions.return_value = [mock_child, completed_child]

        result = main(["--list", "--parent-id", "parent-001"])

        assert result == 0
        captured = capsys.readouterr()
        assert "child-001" in captured.out
        assert "child-002" not in captured.out

    @patch(_AGENT_SESSION)
    def test_list_nonexistent_parent(self, mock_agent_session_cls):
        """--list with non-existent parent returns error."""
        mock_agent_session_cls.query.get.return_value = None

        result = main(["--list", "--parent-id", "nonexistent"])

        assert result == 1

    @patch(_AGENT_SESSION)
    def test_list_parent_lookup_exception(self, mock_agent_session_cls):
        """--list handles exception during parent lookup."""
        mock_agent_session_cls.query.get.side_effect = Exception("Redis down")

        result = main(["--list", "--parent-id", "parent-001"])

        assert result == 1
