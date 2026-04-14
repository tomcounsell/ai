"""Unit tests for tools._sdlc_utils shared session lookup.

Tests cover:
- find_session_by_issue matching PM sessions by issue URL suffix
- Returns None when no match
- Handles invalid input (0, negative, None)
- Handles Redis errors gracefully
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestFindSessionByIssue:
    """Tests for the shared find_session_by_issue function."""

    def test_finds_matching_pm_session(self):
        from tools._sdlc_utils import find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = "https://github.com/tomcounsell/ai/issues/941"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(941)

        assert result == mock_session

    def test_returns_none_when_no_match(self):
        from tools._sdlc_utils import find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = "https://github.com/tomcounsell/ai/issues/999"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(941)

        assert result is None

    def test_returns_none_for_zero(self):
        from tools._sdlc_utils import find_session_by_issue

        result = find_session_by_issue(0)
        assert result is None

    def test_returns_none_for_negative(self):
        from tools._sdlc_utils import find_session_by_issue

        result = find_session_by_issue(-1)
        assert result is None

    def test_handles_redis_error_gracefully(self):
        from tools._sdlc_utils import find_session_by_issue

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = ConnectionError("Redis down")

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(941)

        assert result is None

    def test_handles_session_without_issue_url(self):
        from tools._sdlc_utils import find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = None

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(941)

        assert result is None
