"""Tests for tools/session_progress.py — CLI tool for updating AgentSession progress.

Verifies that:
- _find_session() finds sessions by session_id
- _find_session() finds sessions by task_list_id
- _find_session() handles Redis connection errors gracefully (returns None)
- main() updates stage on a found session
- main() sets links (issue_url, plan_url, pr_url) on a found session
- main() exits 0 when session not found (fire-and-forget behavior)

All tests mock at the Popoto query level — no live Redis connection required.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock the claude_agent_sdk before agent package tries to import it
if "claude_agent_sdk" not in sys.modules:
    _mock_sdk = MagicMock()
    sys.modules["claude_agent_sdk"] = _mock_sdk

from tools.session_progress import _find_session, main

MOCK_TARGET = "models.agent_session.AgentSession"


class TestFindSessionBySessionId:
    """Tests for _find_session() lookup by session_id field."""

    @patch(MOCK_TARGET)
    def test_finds_session_by_session_id(self, mock_agent_session_cls):
        """_find_session returns the first matching session when found by session_id."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-abc-123"
        mock_agent_session_cls.query.filter.return_value = [mock_session]

        result = _find_session("sess-abc-123")

        mock_agent_session_cls.query.filter.assert_called_once_with(
            session_id="sess-abc-123"
        )
        assert result is mock_session

    @patch(MOCK_TARGET)
    def test_returns_first_when_multiple_matches(self, mock_agent_session_cls):
        """_find_session returns the first session if multiple match session_id."""
        first = MagicMock()
        first.session_id = "sess-dup"
        second = MagicMock()
        second.session_id = "sess-dup"
        mock_agent_session_cls.query.filter.return_value = [first, second]

        result = _find_session("sess-dup")
        assert result is first


class TestFindSessionByTaskListId:
    """Tests for _find_session() fallback lookup by task_list_id."""

    @patch(MOCK_TARGET)
    def test_finds_session_by_task_list_id(self, mock_agent_session_cls):
        """_find_session falls back to task_list_id when session_id yields no results."""
        mock_agent_session_cls.query.filter.return_value = []  # No session_id match

        mock_session = MagicMock()
        mock_session.task_list_id = "wire-session-progress"
        mock_agent_session_cls.query.all.return_value = [mock_session]

        result = _find_session("wire-session-progress")

        assert result is mock_session
        mock_agent_session_cls.query.all.assert_called_once()

    @patch(MOCK_TARGET)
    def test_returns_none_when_no_match(self, mock_agent_session_cls):
        """_find_session returns None when neither session_id nor task_list_id match."""
        mock_agent_session_cls.query.filter.return_value = []

        other_session = MagicMock()
        other_session.task_list_id = "other-slug"
        mock_agent_session_cls.query.all.return_value = [other_session]

        result = _find_session("nonexistent-id")
        assert result is None

    @patch(MOCK_TARGET)
    def test_returns_none_when_no_sessions_exist(self, mock_agent_session_cls):
        """_find_session returns None when no sessions exist at all."""
        mock_agent_session_cls.query.filter.return_value = []
        mock_agent_session_cls.query.all.return_value = []

        result = _find_session("anything")
        assert result is None


class TestFindSessionRedisError:
    """Tests for _find_session() graceful handling of Redis connection errors."""

    @patch(MOCK_TARGET)
    def test_returns_none_on_redis_error(self, mock_agent_session_cls, capsys):
        """_find_session returns None and prints warning when Redis is unavailable."""
        mock_agent_session_cls.query.filter.side_effect = ConnectionError(
            "Connection refused"
        )

        result = _find_session("sess-abc")

        assert result is None
        captured = capsys.readouterr()
        assert "Redis connection error" in captured.err

    @patch(MOCK_TARGET)
    def test_returns_none_on_generic_exception(self, mock_agent_session_cls, capsys):
        """_find_session returns None on any exception, not just ConnectionError."""
        mock_agent_session_cls.query.filter.side_effect = RuntimeError(
            "Unexpected failure"
        )

        result = _find_session("sess-abc")

        assert result is None
        captured = capsys.readouterr()
        assert "Redis connection error" in captured.err


class TestMainUpdatesStage:
    """Tests for main() stage update behavior."""

    @patch("tools.session_progress._find_session")
    def test_main_updates_stage_completed(self, mock_find):
        """main() calls append_history with stage and completed icon."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-123"
        mock_find.return_value = mock_session

        sys.argv = [
            "session_progress",
            "--session-id",
            "sess-123",
            "--stage",
            "BUILD",
            "--status",
            "completed",
        ]
        main()

        mock_session.append_history.assert_called_once_with("stage", "BUILD \u2611")

    @patch("tools.session_progress._find_session")
    def test_main_updates_stage_in_progress(self, mock_find):
        """main() calls append_history with in_progress icon."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-123"
        mock_find.return_value = mock_session

        sys.argv = [
            "session_progress",
            "--session-id",
            "sess-123",
            "--stage",
            "TEST",
            "--status",
            "in_progress",
        ]
        main()

        mock_session.append_history.assert_called_once_with("stage", "TEST \u25b6")

    @patch("tools.session_progress._find_session")
    def test_main_updates_stage_failed(self, mock_find):
        """main() calls append_history with failed icon."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-123"
        mock_find.return_value = mock_session

        sys.argv = [
            "session_progress",
            "--session-id",
            "sess-123",
            "--stage",
            "BUILD",
            "--status",
            "failed",
        ]
        main()

        mock_session.append_history.assert_called_once_with("stage", "BUILD \u2717")


class TestMainSetsLinks:
    """Tests for main() link-setting behavior."""

    @patch("tools.session_progress._find_session")
    def test_main_sets_issue_url(self, mock_find):
        """main() calls set_link('issue', url) when --issue-url is provided."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-123"
        mock_find.return_value = mock_session

        sys.argv = [
            "session_progress",
            "--session-id",
            "sess-123",
            "--issue-url",
            "https://github.com/org/repo/issues/42",
        ]
        main()

        mock_session.set_link.assert_called_once_with(
            "issue", "https://github.com/org/repo/issues/42"
        )

    @patch("tools.session_progress._find_session")
    def test_main_sets_plan_url(self, mock_find):
        """main() calls set_link('plan', url) when --plan-url is provided."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-123"
        mock_find.return_value = mock_session

        sys.argv = [
            "session_progress",
            "--session-id",
            "sess-123",
            "--plan-url",
            "https://github.com/org/repo/blob/main/docs/plans/feature.md",
        ]
        main()

        mock_session.set_link.assert_called_once_with(
            "plan", "https://github.com/org/repo/blob/main/docs/plans/feature.md"
        )

    @patch("tools.session_progress._find_session")
    def test_main_sets_pr_url(self, mock_find):
        """main() calls set_link('pr', url) when --pr-url is provided."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-123"
        mock_find.return_value = mock_session

        sys.argv = [
            "session_progress",
            "--session-id",
            "sess-123",
            "--pr-url",
            "https://github.com/org/repo/pull/99",
        ]
        main()

        mock_session.set_link.assert_called_once_with(
            "pr", "https://github.com/org/repo/pull/99"
        )

    @patch("tools.session_progress._find_session")
    def test_main_sets_multiple_links(self, mock_find):
        """main() sets all three links when all are provided."""
        mock_session = MagicMock()
        mock_session.session_id = "sess-123"
        mock_find.return_value = mock_session

        sys.argv = [
            "session_progress",
            "--session-id",
            "sess-123",
            "--issue-url",
            "https://github.com/issues/1",
            "--plan-url",
            "https://github.com/blob/plan.md",
            "--pr-url",
            "https://github.com/pull/2",
        ]
        main()

        assert mock_session.set_link.call_count == 3
        mock_session.set_link.assert_any_call("issue", "https://github.com/issues/1")
        mock_session.set_link.assert_any_call("plan", "https://github.com/blob/plan.md")
        mock_session.set_link.assert_any_call("pr", "https://github.com/pull/2")


class TestMainNoSessionFound:
    """Tests for main() fire-and-forget behavior when no session is found."""

    @patch("tools.session_progress._find_session")
    def test_exits_zero_when_session_not_found(self, mock_find):
        """main() exits with code 0 when no session is found (fire-and-forget)."""
        mock_find.return_value = None

        sys.argv = [
            "session_progress",
            "--session-id",
            "nonexistent",
            "--stage",
            "BUILD",
        ]

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

    @patch("tools.session_progress._find_session")
    def test_prints_warning_when_session_not_found(self, mock_find, capsys):
        """main() prints a warning to stderr when no session is found."""
        mock_find.return_value = None

        sys.argv = [
            "session_progress",
            "--session-id",
            "nonexistent",
            "--stage",
            "BUILD",
        ]

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "No session found" in captured.err
        assert "nonexistent" in captured.err
