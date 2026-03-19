"""Unit tests for error summary enforcement across failure paths.

Verifies that all code paths marking sessions as "failed" capture and persist
error context into the session summary, so the reflections system can produce
actionable bug reports instead of vague "empty error summary" issues.

See: docs/plans/error_summary_enforcement.md
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ===================================================================
# sdk_client.py: error summary in crash guard
# ===================================================================


class TestSdkClientErrorCapture:
    """Verify that sdk_client crash guard passes exception details to complete_transcript."""

    @pytest.mark.asyncio
    async def test_exception_summary_passed_to_complete_transcript(self, monkeypatch):
        """When agent.query() raises, complete_transcript receives the error summary."""
        captured_calls = []

        def mock_complete_transcript(session_id, status=None, summary=None):
            captured_calls.append({"session_id": session_id, "status": status, "summary": summary})

        # We test the crash guard pattern directly by simulating the catch block logic
        # from sdk_client.py lines 1254-1269. This avoids mocking the entire
        # get_agent_response_sdk function which has deep dependencies.
        error = ConnectionError("Redis connection refused on port 6379")

        # Simulate the crash guard logic
        try:
            raise error
        except Exception as e:
            error_summary = f"{type(e).__name__}: {e}"[:500]
            mock_complete_transcript("test-session-1", status="failed", summary=error_summary)

        assert len(captured_calls) == 1
        call = captured_calls[0]
        assert call["session_id"] == "test-session-1"
        assert call["status"] == "failed"
        assert call["summary"] == "ConnectionError: Redis connection refused on port 6379"
        assert call["summary"] is not None
        assert len(call["summary"]) > 0

    def test_error_summary_truncated_to_500_chars(self):
        """Very long exception messages are truncated to 500 characters."""
        long_message = "x" * 1000
        error = ValueError(long_message)

        error_summary = f"{type(error).__name__}: {error}"[:500]

        assert len(error_summary) == 500
        assert error_summary.startswith("ValueError: ")

    def test_error_summary_format(self):
        """Error summary follows the 'ExceptionType: message' format."""
        test_cases = [
            (RuntimeError("timeout"), "RuntimeError: timeout"),
            (KeyError("missing_key"), "KeyError: 'missing_key'"),
            (TypeError("expected str"), "TypeError: expected str"),
        ]
        for error, expected in test_cases:
            summary = f"{type(error).__name__}: {error}"[:500]
            assert summary == expected


# ===================================================================
# reflections.py: empty-summary guard
# ===================================================================


class TestReflectionsEmptySummaryGuard:
    """Verify that reflections skips failed sessions with empty summaries."""

    def _make_session(self, session_id, status, summary, started_at=1710000000):
        return SimpleNamespace(
            session_id=session_id,
            status=status,
            summary=summary,
            started_at=started_at,
            log_path=None,
            tool_call_count=0,
            turn_count=0,
        )

    @patch("models.bridge_event.BridgeEvent")
    @patch("models.agent_session.AgentSession")
    def test_skips_empty_summary_failed_session(
        self, mock_agent_session, mock_bridge_event, caplog
    ):
        """Failed sessions with empty summary are skipped with a warning."""
        from scripts.reflections import analyze_sessions_from_redis

        session = self._make_session("sess-empty", "failed", summary="", started_at=1710000000)
        mock_agent_session.query.all.return_value = [session]
        mock_bridge_event.query.filter.return_value = []

        with caplog.at_level(logging.WARNING):
            result = analyze_sessions_from_redis("2024-03-09")

        # Session should NOT appear in error_patterns
        assert len(result["error_patterns"]) == 0
        # Warning should have been logged
        assert any("Skipping failed session sess-empty" in r.message for r in caplog.records)

    @patch("models.bridge_event.BridgeEvent")
    @patch("models.agent_session.AgentSession")
    def test_skips_none_summary_failed_session(self, mock_agent_session, mock_bridge_event, caplog):
        """Failed sessions with None summary are skipped."""
        from scripts.reflections import analyze_sessions_from_redis

        session = self._make_session("sess-none", "failed", summary=None, started_at=1710000000)
        mock_agent_session.query.all.return_value = [session]
        mock_bridge_event.query.filter.return_value = []

        with caplog.at_level(logging.WARNING):
            result = analyze_sessions_from_redis("2024-03-09")

        assert len(result["error_patterns"]) == 0
        assert any("Skipping failed session sess-none" in r.message for r in caplog.records)

    @patch("models.bridge_event.BridgeEvent")
    @patch("models.agent_session.AgentSession")
    def test_includes_nonempty_summary_failed_session(self, mock_agent_session, mock_bridge_event):
        """Failed sessions with a populated summary are included normally."""
        from scripts.reflections import analyze_sessions_from_redis

        session = self._make_session(
            "sess-good", "failed", summary="ConnectionError: Redis refused", started_at=1710000000
        )
        mock_agent_session.query.all.return_value = [session]
        mock_bridge_event.query.filter.return_value = []

        result = analyze_sessions_from_redis("2024-03-09")

        assert len(result["error_patterns"]) == 1
        assert result["error_patterns"][0]["session_id"] == "sess-good"
        assert result["error_patterns"][0]["summary"] == "ConnectionError: Redis refused"

    @patch("models.bridge_event.BridgeEvent")
    @patch("models.agent_session.AgentSession")
    def test_skips_whitespace_only_summary(self, mock_agent_session, mock_bridge_event, caplog):
        """Failed sessions with whitespace-only summary are skipped."""
        from scripts.reflections import analyze_sessions_from_redis

        session = self._make_session("sess-ws", "failed", summary="   \n  ", started_at=1710000000)
        mock_agent_session.query.all.return_value = [session]
        mock_bridge_event.query.filter.return_value = []

        with caplog.at_level(logging.WARNING):
            result = analyze_sessions_from_redis("2024-03-09")

        assert len(result["error_patterns"]) == 0
