"""Unit tests for AgentSession.get_by_id (issue #765).

Verifies the canonical raw-string lookup helper:
- Positive lookup returns the session.
- Missing id returns None.
- Empty / None / whitespace input returns None without raising.
- Backend exceptions are caught, logged as warnings, and None returned.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from models.agent_session import AgentSession


class TestGetByIdEmptyInputs:
    """Empty/None/whitespace inputs short-circuit to None without touching the backend."""

    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_empty_inputs_return_none(self, value):
        with patch("models.agent_session.AgentSession.query") as mock_query:
            assert AgentSession.get_by_id(value) is None
            mock_query.filter.assert_not_called()

    def test_non_string_input_returns_none(self):
        with patch("models.agent_session.AgentSession.query") as mock_query:
            assert AgentSession.get_by_id(12345) is None  # type: ignore[arg-type]
            mock_query.filter.assert_not_called()


class TestGetByIdLookups:
    """Positive and negative lookups against a mocked Popoto query."""

    def test_positive_lookup_returns_session(self):
        sentinel = MagicMock(spec=AgentSession)
        sentinel.id = "abc-123"
        with patch("models.agent_session.AgentSession.query") as mock_query:
            mock_query.filter.return_value = [sentinel]
            result = AgentSession.get_by_id("abc-123")
        assert result is sentinel
        mock_query.filter.assert_called_once_with(id="abc-123")

    def test_missing_id_returns_none(self):
        with patch("models.agent_session.AgentSession.query") as mock_query:
            mock_query.filter.return_value = []
            assert AgentSession.get_by_id("not-a-real-id") is None

    def test_multiple_matches_logs_warning_and_returns_first(self, caplog):
        first = MagicMock(spec=AgentSession)
        second = MagicMock(spec=AgentSession)
        with caplog.at_level(logging.WARNING, logger="models.agent_session"):
            with patch("models.agent_session.AgentSession.query") as mock_query:
                mock_query.filter.return_value = [first, second]
                result = AgentSession.get_by_id("dup-id")
        assert result is first
        assert any("found 2 sessions" in r.message for r in caplog.records)

    def test_backend_exception_is_logged_and_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING, logger="models.agent_session"):
            with patch("models.agent_session.AgentSession.query") as mock_query:
                mock_query.filter.side_effect = RuntimeError("redis down")
                result = AgentSession.get_by_id("any-id")
        assert result is None
        assert any(
            "get_by_id lookup failed" in r.message and "any-id" in r.message for r in caplog.records
        )
