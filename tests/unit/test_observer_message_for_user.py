"""Tests for the Observer message_for_user field and reason isolation.

Verifies that:
1. The observer's reason string never reaches user-facing output
2. message_for_user is propagated through the decision pipeline
3. The deliver_to_telegram tool schema accepts message_for_user
"""

import json

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


class TestDeliveryMessageWithGateWarnings:
    """Test that gate warnings are appended AFTER message_for_user selection.

    This mirrors the delivery logic in agent/job_queue.py (send_to_chat)
    lines 1662-1689. The fix ensures gate warnings are never lost when
    the Observer provides message_for_user instead of raw msg.
    """

    @staticmethod
    def _build_delivery_msg(
        decision: dict,
        raw_msg: str,
        unsatisfied_gates: list[str] | None = None,
    ) -> str:
        """Replicate the delivery message construction from job_queue.py.

        This mirrors the exact logic:
        1. Select message_for_user or fall back to raw msg
        2. Guard against empty/whitespace
        3. Append gate warnings if any gates are unsatisfied
        """
        # Line 1662: message_for_user takes priority over raw msg
        delivery_msg = decision.get("message_for_user", raw_msg)

        # Lines 1664-1668: empty/whitespace fallback
        if not delivery_msg or not delivery_msg.strip():
            delivery_msg = (
                "The task completed but produced no output. "
                "Please re-trigger if you expected results."
            )

        # Lines 1670-1689: gate warnings appended AFTER selection
        if unsatisfied_gates:
            gate_warning = "\n\n\u26a0\ufe0f **Incomplete pipeline gates:**\n" + "\n".join(
                unsatisfied_gates
            )
            delivery_msg = delivery_msg + gate_warning

        return delivery_msg

    def test_message_for_user_with_unsatisfied_gates(self):
        """When message_for_user is set AND gates are unsatisfied,
        the final delivery must include BOTH the curated message AND gate warnings.
        The raw msg must NOT appear in the output.
        """
        raw_msg = "Internal worker output with reasoning details"
        decision = {
            "message_for_user": "Build stage completed successfully.",
            "reason": "pipeline stage done",
        }
        unsatisfied_gates = [
            "  - test: No test results found",
            "  - docs: Missing documentation",
        ]

        result = self._build_delivery_msg(decision, raw_msg, unsatisfied_gates)

        # message_for_user text is present
        assert "Build stage completed successfully." in result
        # Gate warnings are present
        assert "\u26a0\ufe0f **Incomplete pipeline gates:**" in result
        assert "  - test: No test results found" in result
        assert "  - docs: Missing documentation" in result
        # Raw msg does NOT leak into delivery
        assert "Internal worker output with reasoning details" not in result

    def test_fallback_to_raw_msg_with_unsatisfied_gates(self):
        """When message_for_user is NOT set, raw msg is used,
        and gate warnings are still appended.
        """
        raw_msg = "Worker produced this output directly."
        decision = {"reason": "delivering result"}
        unsatisfied_gates = ["  - review: PR not approved"]

        result = self._build_delivery_msg(decision, raw_msg, unsatisfied_gates)

        assert "Worker produced this output directly." in result
        assert "\u26a0\ufe0f **Incomplete pipeline gates:**" in result
        assert "  - review: PR not approved" in result

    def test_message_for_user_with_all_gates_satisfied(self):
        """When message_for_user is set and all gates pass,
        no gate warning appears in the delivery.
        """
        raw_msg = "Internal worker output"
        decision = {
            "message_for_user": "Everything is complete!",
            "reason": "all done",
        }

        # No unsatisfied gates
        result = self._build_delivery_msg(decision, raw_msg, unsatisfied_gates=None)

        assert result == "Everything is complete!"
        assert "\u26a0\ufe0f" not in result
        assert "Internal worker output" not in result
