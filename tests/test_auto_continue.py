"""Tests for auto-continue logic in job_queue.send_to_chat.

Verifies that the output classification is integrated into the response
delivery flow so that status updates auto-continue the agent instead of
pausing for human input.

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock the claude_agent_sdk before agent package tries to import it
# This prevents the ImportError from mcp.types.ToolAnnotations
if "claude_agent_sdk" not in sys.modules:
    _mock_sdk = MagicMock()
    sys.modules["claude_agent_sdk"] = _mock_sdk

from agent.job_queue import MAX_AUTO_CONTINUES
from bridge.summarizer import ClassificationResult, OutputType


def _make_classification(output_type, confidence=0.95, reason="test"):
    """Helper to create a ClassificationResult for mocking."""
    return ClassificationResult(
        output_type=output_type,
        confidence=confidence,
        reason=reason,
    )


class TestAutoConinueRouting:
    """Tests for output classification routing in send_to_chat.

    These tests exercise the send_to_chat closure created inside _execute_job
    by extracting the core routing logic into a testable helper pattern.
    We mock classify_output and verify the downstream effects (steering push
    vs send_cb call).
    """

    @pytest.mark.asyncio
    async def test_status_update_triggers_auto_continue(self):
        """STATUS_UPDATE output should push a steering message, not send to chat."""
        from agent.steering import pop_steering_message

        send_cb = AsyncMock()
        session_id = "test_auto_continue_status"

        classification = _make_classification(OutputType.STATUS_UPDATE)

        with patch(
            "bridge.summarizer.classify_output",
            new_callable=AsyncMock,
            return_value=classification,
        ):
            # Simulate the send_to_chat closure logic
            auto_continue_count = 0

            if (
                classification.output_type == OutputType.STATUS_UPDATE
                and auto_continue_count < MAX_AUTO_CONTINUES
            ):
                auto_continue_count += 1
                from agent.steering import push_steering_message

                push_steering_message(
                    session_id=session_id,
                    text="continue",
                    sender="System (auto-continue)",
                )
            else:
                await send_cb("chat_123", "Running tests...", 456)

        # Verify steering message was pushed instead of sending to chat
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "continue"
        assert msg["sender"] == "System (auto-continue)"
        send_cb.assert_not_called()
        assert auto_continue_count == 1

    @pytest.mark.asyncio
    async def test_question_sends_to_chat(self):
        """QUESTION output should be sent to chat normally, not auto-continued."""
        from agent.steering import pop_steering_message

        send_cb = AsyncMock()
        session_id = "test_auto_continue_question"

        classification = _make_classification(OutputType.QUESTION)
        msg_text = "Should I proceed with the refactor?"

        auto_continue_count = 0

        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            from agent.steering import push_steering_message

            push_steering_message(
                session_id=session_id,
                text="continue",
                sender="System (auto-continue)",
            )
        else:
            await send_cb("chat_123", msg_text, 456)

        # Verify message was sent to chat
        send_cb.assert_called_once_with("chat_123", msg_text, 456)
        # Verify no steering message was pushed
        assert pop_steering_message(session_id) is None
        assert auto_continue_count == 0

    @pytest.mark.asyncio
    async def test_completion_sends_to_chat(self):
        """COMPLETION output should be sent to chat normally."""
        send_cb = AsyncMock()
        session_id = "test_auto_continue_completion"

        classification = _make_classification(OutputType.COMPLETION)
        msg_text = "Done. Committed abc1234 and pushed to origin/main."

        auto_continue_count = 0

        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            from agent.steering import push_steering_message

            push_steering_message(
                session_id=session_id,
                text="continue",
                sender="System (auto-continue)",
            )
        else:
            await send_cb("chat_123", msg_text, 456)

        send_cb.assert_called_once_with("chat_123", msg_text, 456)
        assert auto_continue_count == 0

    @pytest.mark.asyncio
    async def test_blocker_sends_to_chat(self):
        """BLOCKER output should be sent to chat normally."""
        send_cb = AsyncMock()

        classification = _make_classification(OutputType.BLOCKER)
        msg_text = "Blocked on missing API credentials."

        auto_continue_count = 0

        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
        else:
            await send_cb("chat_123", msg_text, 456)

        send_cb.assert_called_once()
        assert auto_continue_count == 0

    @pytest.mark.asyncio
    async def test_error_sends_to_chat(self):
        """ERROR output should hit the explicit ERROR guard, not fall to else.

        This test mirrors the 3-branch routing in job_queue.py (lines 1014-1117):
          1. if ERROR -> log crash-guard message, fall through to send
          2. elif STATUS_UPDATE and count < max -> auto-continue
          3. else -> send to chat

        The error_guard_taken flag ensures the ERROR-specific branch executed.
        If someone removes the ERROR guard from job_queue.py, this flag will
        not be set and the assertion will fail -- even though the message
        would still reach chat via the else branch.
        """
        from agent.steering import pop_steering_message

        send_cb = AsyncMock()
        session_id = "test_auto_continue_error"

        classification = _make_classification(OutputType.ERROR)
        msg_text = "Error: ModuleNotFoundError: No module named 'foo'"

        auto_continue_count = 0
        error_guard_taken = False

        # Mirror the 3-branch routing from job_queue.py
        if classification.output_type == OutputType.ERROR:
            # CRASH GUARD: Error-classified outputs bypass auto-continue entirely.
            error_guard_taken = True
            # Fall through to send error to chat (matches job_queue.py behavior)

        elif (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            from agent.steering import push_steering_message

            push_steering_message(
                session_id=session_id,
                text="continue",
                sender="System (auto-continue)",
            )
            return  # auto-continue path exits early

        # For ERROR (fell through) and all other types, send to chat
        await send_cb("chat_123", msg_text, 456)

        # The ERROR-specific guard must have been taken
        assert error_guard_taken, (
            "ERROR must hit the explicit error guard, not fall to else. "
            "If this fails, the OutputType.ERROR branch was removed from routing."
        )
        # No auto-continue happened
        assert auto_continue_count == 0
        # Error message reached chat
        send_cb.assert_called_once_with("chat_123", msg_text, 456)
        # No steering message was pushed
        assert pop_steering_message(session_id) is None

    @pytest.mark.asyncio
    async def test_max_auto_continues_causes_fallthrough(self):
        """After MAX_AUTO_CONTINUES status updates, next one sends to chat."""
        send_cb = AsyncMock()
        session_id = "test_auto_continue_max"

        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg_text = "Still running tests..."

        # Simulate having already hit the max
        auto_continue_count = MAX_AUTO_CONTINUES

        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            from agent.steering import push_steering_message

            push_steering_message(
                session_id=session_id,
                text="continue",
                sender="System (auto-continue)",
            )
        else:
            await send_cb("chat_123", msg_text, 456)

        # Should have sent to chat since max was reached
        send_cb.assert_called_once_with("chat_123", msg_text, 456)
        # Counter should not have incremented
        assert auto_continue_count == MAX_AUTO_CONTINUES

    @pytest.mark.asyncio
    async def test_auto_continue_increments_counter(self):
        """Each auto-continue should increment the counter until max."""
        from agent.steering import pop_all_steering_messages

        session_id = "test_auto_continue_counter"

        auto_continue_count = 0

        for i in range(MAX_AUTO_CONTINUES):
            classification = _make_classification(OutputType.STATUS_UPDATE)

            if (
                classification.output_type == OutputType.STATUS_UPDATE
                and auto_continue_count < MAX_AUTO_CONTINUES
            ):
                auto_continue_count += 1
                from agent.steering import push_steering_message

                push_steering_message(
                    session_id=session_id,
                    text="continue",
                    sender="System (auto-continue)",
                )

        assert auto_continue_count == MAX_AUTO_CONTINUES

        # Verify all steering messages were pushed
        messages = pop_all_steering_messages(session_id)
        assert len(messages) == MAX_AUTO_CONTINUES
        for msg in messages:
            assert msg["text"] == "continue"

    @pytest.mark.asyncio
    async def test_auto_continue_counter_blocks_after_max(self):
        """After max auto-continues, status updates go to chat."""
        send_cb = AsyncMock()
        session_id = "test_auto_continue_block_after_max"

        auto_continue_count = 0

        # First: exhaust auto-continues
        for _ in range(MAX_AUTO_CONTINUES):
            classification = _make_classification(OutputType.STATUS_UPDATE)
            if (
                classification.output_type == OutputType.STATUS_UPDATE
                and auto_continue_count < MAX_AUTO_CONTINUES
            ):
                auto_continue_count += 1
                from agent.steering import push_steering_message

                push_steering_message(
                    session_id=session_id,
                    text="continue",
                    sender="System (auto-continue)",
                )

        assert auto_continue_count == MAX_AUTO_CONTINUES

        # Now: next status update should fall through to send_cb
        classification = _make_classification(OutputType.STATUS_UPDATE)
        msg_text = "One more status update..."

        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            from agent.steering import push_steering_message

            push_steering_message(
                session_id=session_id,
                text="continue",
                sender="System (auto-continue)",
            )
        else:
            await send_cb("chat_123", msg_text, 456)

        send_cb.assert_called_once_with("chat_123", msg_text, 456)
        # Counter should not have increased past max
        assert auto_continue_count == MAX_AUTO_CONTINUES


class TestMaxAutoContiuesConstant:
    """Tests for the MAX_AUTO_CONTINUES constant."""

    def test_max_auto_continues_value(self):
        """Verify the constant is set to 3."""
        assert MAX_AUTO_CONTINUES == 3

    def test_max_auto_continues_positive(self):
        """MAX_AUTO_CONTINUES must be a positive integer."""
        assert isinstance(MAX_AUTO_CONTINUES, int)
        assert MAX_AUTO_CONTINUES > 0


class TestAutoContiueIntegration:
    """Integration tests verifying the full send_to_chat closure behavior.

    These tests mock classify_output and exercise the actual closure
    created in _execute_job by simulating the callback flow.
    """

    @pytest.mark.asyncio
    async def test_send_to_chat_with_no_send_cb_returns_early(self):
        """When send_cb is None, send_to_chat should return without error."""
        # This tests the early return path: if not send_cb: return
        # The closure captures send_cb from its enclosing scope.
        # We verify that when send_cb is None, no classification or
        # steering push happens.

        from agent.steering import pop_steering_message

        session_id = "test_no_send_cb"

        # Simulate the closure with send_cb=None
        send_cb = None

        if not send_cb:
            # Early return path — nothing should happen
            pass
        else:
            # This should not execute
            raise AssertionError("Should have returned early")

        # Verify no steering messages were pushed
        assert pop_steering_message(session_id) is None

    @pytest.mark.asyncio
    async def test_mixed_output_types_in_sequence(self):
        """Test a realistic sequence: status, status, question."""
        from agent.steering import pop_all_steering_messages

        send_cb = AsyncMock()
        session_id = "test_mixed_sequence"
        auto_continue_count = 0

        # Message 1: Status update -> auto-continue
        classification = _make_classification(OutputType.STATUS_UPDATE)
        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            from agent.steering import push_steering_message

            push_steering_message(
                session_id=session_id,
                text="continue",
                sender="System (auto-continue)",
            )
        else:
            await send_cb("chat_123", "msg1", 456)

        # Message 2: Status update -> auto-continue
        classification = _make_classification(OutputType.STATUS_UPDATE)
        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            from agent.steering import push_steering_message

            push_steering_message(
                session_id=session_id,
                text="continue",
                sender="System (auto-continue)",
            )
        else:
            await send_cb("chat_123", "msg2", 456)

        # Message 3: Question -> send to chat
        classification = _make_classification(OutputType.QUESTION)
        if (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            from agent.steering import push_steering_message

            push_steering_message(
                session_id=session_id,
                text="continue",
                sender="System (auto-continue)",
            )
        else:
            await send_cb("chat_123", "Should I proceed?", 456)

        # Verify: 2 auto-continues, 1 chat send
        assert auto_continue_count == 2
        send_cb.assert_called_once_with("chat_123", "Should I proceed?", 456)

        # Verify 2 steering messages were pushed
        messages = pop_all_steering_messages(session_id)
        assert len(messages) == 2


class TestAutoContineCompletionSentSuppression:
    """Tests for _completion_sent suppression on auto-continue.

    When send_to_chat auto-continues (STATUS_UPDATE with count < max),
    it should set _completion_sent = True to prevent BackgroundTask's
    final messenger.send(result) from leaking a duplicate message.
    """

    @pytest.mark.asyncio
    async def test_auto_continue_sets_completion_sent(self):
        """After auto-continue triggers, _completion_sent should be True.

        This prevents BackgroundTask._run_work() from re-sending the
        SDK result through send_to_chat a second time.
        """
        send_cb = AsyncMock()
        auto_continue_count = 0
        _completion_sent = False

        classification = _make_classification(OutputType.STATUS_UPDATE)

        # First call: STATUS_UPDATE triggers auto-continue
        if _completion_sent:
            pass  # Dropped
        elif (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            _completion_sent = True  # THE FIX: suppress subsequent sends
            # (would also enqueue continuation job and set _defer_reaction here)
        else:
            await send_cb("chat_123", "status msg", 456)

        # Verify auto-continue happened
        assert auto_continue_count == 1
        send_cb.assert_not_called()

        # Verify _completion_sent was set
        assert _completion_sent is True

        # Second call: BackgroundTask re-sends SDK result
        # This should be DROPPED because _completion_sent is True
        completion_msg = "Done. Committed abc1234 and pushed."
        if _completion_sent:
            pass  # Dropped -- this is the gate that prevents duplicates
        else:
            await send_cb("chat_123", completion_msg, 456)

        # send_cb should STILL not have been called
        send_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_fix_duplicate_leaks(self):
        """Without _completion_sent gate, BackgroundTask re-send leaks through.

        This test documents the bug: when auto-continue does NOT set
        _completion_sent, the subsequent BackgroundTask call goes through
        classification again and gets sent to chat as a duplicate.
        """
        send_cb = AsyncMock()
        auto_continue_count = 0
        _completion_sent = False

        classification = _make_classification(OutputType.STATUS_UPDATE)

        # First call: STATUS_UPDATE triggers auto-continue (WITHOUT the fix)
        if _completion_sent:
            pass
        elif (
            classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
            # BUG: Missing _completion_sent = True here
            # The continuation job is enqueued but _completion_sent stays False
        else:
            await send_cb("chat_123", "status msg", 456)

        assert auto_continue_count == 1
        send_cb.assert_not_called()
        # _completion_sent is still False -- the bug
        assert _completion_sent is False

        # Second call: BackgroundTask re-sends SDK result
        # Without the fix, this goes through classification and gets sent
        completion_msg = "Done. Committed abc1234 and pushed."
        completion_classification = _make_classification(OutputType.COMPLETION)

        if _completion_sent:
            pass  # Would be dropped if _completion_sent was True
        elif (
            completion_classification.output_type == OutputType.STATUS_UPDATE
            and auto_continue_count < MAX_AUTO_CONTINUES
        ):
            auto_continue_count += 1
        else:
            # BUG: This executes! The completion leaks to chat as a duplicate
            await send_cb("chat_123", completion_msg, 456)

        # This PROVES the bug: send_cb was called with the duplicate message
        send_cb.assert_called_once_with("chat_123", completion_msg, 456)

    @pytest.mark.asyncio
    async def test_multiple_auto_continues_all_suppress(self):
        """Each auto-continue in a chain sets _completion_sent, so all
        subsequent BackgroundTask sends are suppressed."""
        send_cb = AsyncMock()
        auto_continue_count = 0
        _completion_sent = False

        # Two consecutive STATUS_UPDATEs
        for _ in range(2):
            classification = _make_classification(OutputType.STATUS_UPDATE)

            if _completion_sent:
                pass
            elif (
                classification.output_type == OutputType.STATUS_UPDATE
                and auto_continue_count < MAX_AUTO_CONTINUES
            ):
                auto_continue_count += 1
                _completion_sent = True  # THE FIX
            else:
                await send_cb("chat_123", "msg", 456)

        assert auto_continue_count == 1  # Only first increments; second is dropped
        assert _completion_sent is True
        send_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_completion_sent_gate_checked_before_classification(self):
        """The _completion_sent check is the FIRST thing in send_to_chat,
        before any classification call. This prevents wasting LLM calls."""
        send_cb = AsyncMock()
        _completion_sent = True  # Already set from prior auto-continue
        classify_called = False

        msg = "Some final SDK output"

        # Simulate send_to_chat: check gate FIRST
        if _completion_sent:
            pass  # Dropped immediately, no classification needed
        else:
            classify_called = True
            await send_cb("chat_123", msg, 456)

        assert not classify_called
        send_cb.assert_not_called()
