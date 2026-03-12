"""Tests for stop_reason plumbing from SDK to Observer routing.

Covers:
1. Observer._handle_read_session() includes stop_reason in output
2. Observer.run() deterministic routing for budget_exceeded -> DELIVER
3. Observer.run() deterministic routing for rate_limited -> STEER with backoff
4. Observer.run() passes through end_turn normally (no short-circuit)
5. Stop reason registry get/consume semantics
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestObserverStopReasonRouting:
    """Observer deterministic routing based on stop_reason."""

    @pytest.fixture()
    def mock_session(self):
        session = MagicMock()
        session.session_id = "test-session"
        session.correlation_id = "test-corr"
        session.is_sdlc_job.return_value = False
        session.has_remaining_stages.return_value = False
        session.has_failed_stage.return_value = False
        session.classification_type = "conversation"
        session.get_stage_progress.return_value = {}
        session.get_links.return_value = {}
        session.get_history_list.return_value = []
        session.queued_steering_messages = []
        session.context_summary = None
        session.expectations = None
        session.status = "active"
        return session

    def test_read_session_includes_stop_reason(self, mock_session):
        from bridge.observer import Observer

        observer = Observer(
            session=mock_session,
            worker_output="some output",
            auto_continue_count=0,
            send_cb=AsyncMock(),
            enqueue_fn=AsyncMock(),
            stop_reason="budget_exceeded",
        )
        result = observer._handle_read_session()
        assert result["stop_reason"] == "budget_exceeded"

    def test_read_session_stop_reason_none_by_default(self, mock_session):
        from bridge.observer import Observer

        observer = Observer(
            session=mock_session,
            worker_output="some output",
            auto_continue_count=0,
            send_cb=AsyncMock(),
            enqueue_fn=AsyncMock(),
        )
        result = observer._handle_read_session()
        assert result["stop_reason"] is None

    @pytest.mark.asyncio()
    async def test_budget_exceeded_delivers_with_warning(self, mock_session):
        """budget_exceeded stop_reason should deliver immediately."""
        from bridge.observer import Observer

        observer = Observer(
            session=mock_session,
            worker_output="partial work done",
            auto_continue_count=0,
            send_cb=AsyncMock(),
            enqueue_fn=AsyncMock(),
            stop_reason="budget_exceeded",
        )
        with (
            patch("bridge.observer.parse_outcome_from_text", return_value=None),
            patch("bridge.observer.detect_stages", return_value=[]),
            patch("bridge.observer.apply_transitions", return_value=0),
        ):
            decision = await observer.run()

        assert decision["action"] == "deliver"
        assert "budget" in decision["reason"].lower()
        assert decision["stop_reason"] == "budget_exceeded"

    @pytest.mark.asyncio()
    async def test_rate_limited_steers_with_backoff(self, mock_session):
        """rate_limited stop_reason should steer with backoff instruction."""
        from bridge.observer import Observer

        observer = Observer(
            session=mock_session,
            worker_output="partial work done",
            auto_continue_count=0,
            send_cb=AsyncMock(),
            enqueue_fn=AsyncMock(),
            stop_reason="rate_limited",
        )
        with (
            patch("bridge.observer.parse_outcome_from_text", return_value=None),
            patch("bridge.observer.detect_stages", return_value=[]),
            patch("bridge.observer.apply_transitions", return_value=0),
        ):
            decision = await observer.run()

        assert decision["action"] == "steer"
        assert "rate" in decision["coaching_message"].lower()
        assert decision["stop_reason"] == "rate_limited"

    @pytest.mark.asyncio()
    async def test_end_turn_falls_through_to_normal(self, mock_session):
        """end_turn stop_reason should not short-circuit -- uses normal LLM flow."""
        from bridge.observer import Observer

        observer = Observer(
            session=mock_session,
            worker_output="some output",
            auto_continue_count=0,
            send_cb=AsyncMock(),
            enqueue_fn=AsyncMock(),
            stop_reason="end_turn",
        )
        # end_turn should fall through to LLM observer, so we mock the API
        with (
            patch("bridge.observer.parse_outcome_from_text", return_value=None),
            patch("bridge.observer.detect_stages", return_value=[]),
            patch("bridge.observer.apply_transitions", return_value=0),
            patch("bridge.observer.get_anthropic_api_key", return_value="fake-key"),
            patch("bridge.observer.anthropic") as mock_anthropic,
        ):
            # Mock the API to make a deliver decision
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client

            tool_use_read = MagicMock()
            tool_use_read.type = "tool_use"
            tool_use_read.name = "read_session"
            tool_use_read.input = {}
            tool_use_read.id = "tu1"

            tool_use_deliver = MagicMock()
            tool_use_deliver.type = "tool_use"
            tool_use_deliver.name = "deliver_to_telegram"
            tool_use_deliver.input = {"reason": "end_turn normal"}
            tool_use_deliver.id = "tu2"

            resp1 = MagicMock()
            resp1.content = [tool_use_read]
            resp2 = MagicMock()
            resp2.content = [tool_use_deliver]

            mock_client.messages.create.side_effect = [resp1, resp2]

            decision = await observer.run()

        # Should have gone through LLM path and delivered
        assert decision["action"] == "deliver"


class TestStopReasonRegistry:
    """Test the session stop_reason registry in sdk_client."""

    def test_get_stop_reason_returns_and_clears(self):
        from agent.sdk_client import _session_stop_reasons, get_stop_reason

        _session_stop_reasons["sess-1"] = "budget_exceeded"
        result = get_stop_reason("sess-1")
        assert result == "budget_exceeded"
        # Should be cleared after get
        assert get_stop_reason("sess-1") is None

    def test_get_stop_reason_missing_returns_none(self):
        from agent.sdk_client import get_stop_reason

        assert get_stop_reason("nonexistent") is None
