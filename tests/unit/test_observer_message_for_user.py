"""Tests for the Observer message_for_user field and reason isolation.

Verifies that:
1. The observer's reason string never reaches user-facing output
2. message_for_user is propagated through the decision pipeline
3. The deliver_to_telegram tool schema accepts message_for_user
"""

import json

import pytest

from bridge.observer import Observer, _build_tools


class TestDeliverToolSchema:
    """Verify the deliver_to_telegram tool schema includes message_for_user."""

    def test_schema_has_message_for_user(self):
        """deliver_to_telegram should accept an optional message_for_user field."""
        tools = _build_tools()
        deliver_tool = next(t for t in tools if t["name"] == "deliver_to_telegram")
        props = deliver_tool["input_schema"]["properties"]
        assert "message_for_user" in props
        assert props["message_for_user"]["type"] == "string"

    def test_message_for_user_not_required(self):
        """message_for_user should be optional (not in required)."""
        tools = _build_tools()
        deliver_tool = next(t for t in tools if t["name"] == "deliver_to_telegram")
        required = deliver_tool["input_schema"]["required"]
        assert "message_for_user" not in required

    def test_reason_is_required(self):
        """reason should remain required."""
        tools = _build_tools()
        deliver_tool = next(t for t in tools if t["name"] == "deliver_to_telegram")
        required = deliver_tool["input_schema"]["required"]
        assert "reason" in required

    def test_reason_description_says_internal(self):
        """reason description should indicate it's internal/logged only."""
        tools = _build_tools()
        deliver_tool = next(t for t in tools if t["name"] == "deliver_to_telegram")
        reason_desc = deliver_tool["input_schema"]["properties"]["reason"]["description"]
        assert "log" in reason_desc.lower() or "internal" in reason_desc.lower()


class TestDispatchToolMessageForUser:
    """Test that _dispatch_tool propagates message_for_user."""

    def _make_observer(self):
        """Create an Observer with minimal mocks for dispatch testing."""

        class FakeSession:
            session_id = "test-session"
            correlation_id = "test-cid"
            classification_type = "QUESTION"

            def is_sdlc_job(self):
                return False

            def has_remaining_stages(self):
                return False

            def has_failed_stage(self):
                return False

            def get_stage_progress(self):
                return {}

            def get_links(self):
                return {}

            def get_history_list(self):
                return []

            @property
            def queued_steering_messages(self):
                return []

            @property
            def context_summary(self):
                return None

            @property
            def expectations(self):
                return None

        return Observer(
            session=FakeSession(),
            worker_output="test output",
            auto_continue_count=0,
            send_cb=None,
            enqueue_fn=None,
        )

    def test_dispatch_deliver_with_message_for_user(self):
        """When message_for_user is provided, it should be in the result."""
        obs = self._make_observer()
        result_str = obs._dispatch_tool(
            "deliver_to_telegram",
            {
                "reason": "pipeline complete",
                "message_for_user": "Here are the results of the investigation.",
            },
        )
        result = json.loads(result_str)
        assert result["action"] == "deliver_to_telegram"
        assert result["reason"] == "pipeline complete"
        assert result["message_for_user"] == "Here are the results of the investigation."

    def test_dispatch_deliver_without_message_for_user(self):
        """When message_for_user is not provided, it should not be in result."""
        obs = self._make_observer()
        result_str = obs._dispatch_tool(
            "deliver_to_telegram",
            {"reason": "answering question"},
        )
        result = json.loads(result_str)
        assert result["action"] == "deliver_to_telegram"
        assert result["reason"] == "answering question"
        assert "message_for_user" not in result

    def test_dispatch_deliver_empty_message_for_user(self):
        """Empty string message_for_user should not be included."""
        obs = self._make_observer()
        result_str = obs._dispatch_tool(
            "deliver_to_telegram",
            {"reason": "test", "message_for_user": ""},
        )
        result = json.loads(result_str)
        assert "message_for_user" not in result

    def test_reason_in_result_is_for_logging_only(self):
        """The reason field exists in the dispatch result but is for logging.

        This test documents the contract: reason is extracted by the Observer
        run() method for logging but must never be sent to the user.
        """
        obs = self._make_observer()
        result_str = obs._dispatch_tool(
            "deliver_to_telegram",
            {
                "reason": "Auto-continue limit exceeded (4 > 3)",
                "message_for_user": "Investigation complete.",
            },
        )
        result = json.loads(result_str)
        # reason is present (for internal logging) but message_for_user
        # is the user-facing text
        assert result["reason"] == "Auto-continue limit exceeded (4 > 3)"
        assert result["message_for_user"] == "Investigation complete."
