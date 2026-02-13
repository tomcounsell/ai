"""Tests for reply delivery reliability and steering race conditions.

These tests verify the fixes for the bug where auto-continue steering messages
pushed after the Claude agent finished would silently drop responses, because
the steering queue wasn't drained and no warning was logged.

Covers:
- Steering race condition: messages pushed after agent completion are drained
- Reaction emoji constants: correct emojis in validated/invalid lists
- filter_tool_logs: preserves real content, strips tool prefixes
- RedisJob auto_continue_count: persistence across re-enqueued jobs
- BossMessenger has_communicated() tracking

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

from unittest.mock import AsyncMock

import pytest

from agent.messenger import BossMessenger
from agent.steering import pop_all_steering_messages, push_steering_message


class TestSteeringRaceCondition:
    """Test that auto-continue steering messages pushed after agent completion
    are properly drained and don't silently drop responses.

    The bug scenario:
    1. Agent finishes processing and returns a response
    2. Auto-continue classifies as STATUS_UPDATE and pushes "continue" to steering
    3. Nobody consumes the steering message because the agent loop already exited
    4. The "continue" sits in Redis forever, response silently dropped

    The fix: _execute_job calls pop_all_steering_messages() after the agent
    completes, logging any leftovers as warnings.
    """

    def test_steering_messages_drained_after_agent_done(self):
        """Pushing 'continue' after agent is done should be drained, not lost."""
        session_id = "test_race_condition_drain"
        push_steering_message(session_id, "continue", "System (auto-continue)")

        leftover = pop_all_steering_messages(session_id)
        assert len(leftover) == 1
        assert leftover[0]["text"] == "continue"
        assert leftover[0]["sender"] == "System (auto-continue)"

        # Queue should now be empty after drain
        assert pop_all_steering_messages(session_id) == []

    def test_multiple_unconsumed_steering_messages_all_drained(self):
        """Multiple unconsumed steering messages should all be captured."""
        session_id = "test_race_multi_drain"
        push_steering_message(session_id, "continue", "System (auto-continue)")
        push_steering_message(session_id, "continue", "System (auto-continue)")
        push_steering_message(session_id, "focus on tests", "Tom")

        leftover = pop_all_steering_messages(session_id)
        assert len(leftover) == 3
        texts = [m["text"] for m in leftover]
        assert texts.count("continue") == 2
        assert "focus on tests" in texts

    def test_drain_on_empty_queue_returns_empty(self):
        """Draining an empty queue should return an empty list, not error."""
        leftover = pop_all_steering_messages("test_race_empty_drain")
        assert leftover == []

    def test_drain_is_atomic_no_partial_reads(self):
        """After draining, a second drain should get nothing."""
        session_id = "test_race_atomic_drain"
        push_steering_message(session_id, "msg1", "Alice")
        push_steering_message(session_id, "msg2", "Bob")

        first_drain = pop_all_steering_messages(session_id)
        assert len(first_drain) == 2

        second_drain = pop_all_steering_messages(session_id)
        assert len(second_drain) == 0

    def test_steering_messages_isolated_between_sessions(self):
        """Messages in one session should not appear in another's drain."""
        session_a = "test_race_session_a"
        session_b = "test_race_session_b"

        push_steering_message(session_a, "continue", "System")
        push_steering_message(session_b, "different", "Human")

        leftover_a = pop_all_steering_messages(session_a)
        leftover_b = pop_all_steering_messages(session_b)

        assert len(leftover_a) == 1
        assert leftover_a[0]["text"] == "continue"
        assert len(leftover_b) == 1
        assert leftover_b[0]["text"] == "different"


class TestMessengerCommunicationTracking:
    """Test that BossMessenger correctly tracks whether text was sent."""

    @pytest.mark.asyncio
    async def test_has_communicated_false_initially(self):
        """Fresh messenger should report no communication."""
        messenger = BossMessenger(
            _send_callback=AsyncMock(), chat_id="test", session_id="test"
        )
        assert messenger.has_communicated() is False

    @pytest.mark.asyncio
    async def test_has_communicated_true_after_send(self):
        """After sending a message, has_communicated should be True."""
        send_cb = AsyncMock()
        messenger = BossMessenger(
            _send_callback=send_cb, chat_id="test", session_id="test"
        )
        await messenger.send("Hello")
        assert messenger.has_communicated() is True

    @pytest.mark.asyncio
    async def test_has_communicated_false_after_failed_send(self):
        """If send fails (callback raises), has_communicated should be False."""
        send_cb = AsyncMock(side_effect=Exception("connection lost"))
        messenger = BossMessenger(
            _send_callback=send_cb, chat_id="test", session_id="test"
        )
        result = await messenger.send("Hello")
        assert result is False
        assert messenger.has_communicated() is False


class TestReactionEmojiSelection:
    """Test that the correct reaction emojis are in validated/invalid lists."""

    def test_reaction_complete_in_validated_list(self):
        """REACTION_COMPLETE (🏆) must be in the validated reactions list."""
        from bridge.response import REACTION_COMPLETE, VALIDATED_REACTIONS

        assert REACTION_COMPLETE in VALIDATED_REACTIONS

    def test_reaction_error_in_validated_list(self):
        """REACTION_ERROR (😱) must be in the validated reactions list."""
        from bridge.response import REACTION_ERROR, VALIDATED_REACTIONS

        assert REACTION_ERROR in VALIDATED_REACTIONS

    def test_reaction_success_in_validated_list(self):
        """REACTION_SUCCESS (👍) must be in the validated reactions list."""
        from bridge.response import REACTION_SUCCESS, VALIDATED_REACTIONS

        assert REACTION_SUCCESS in VALIDATED_REACTIONS

    def test_reaction_received_in_validated_list(self):
        """REACTION_RECEIVED (👀) must be in the validated reactions list."""
        from bridge.response import REACTION_RECEIVED, VALIDATED_REACTIONS

        assert REACTION_RECEIVED in VALIDATED_REACTIONS

    def test_cross_mark_not_used(self):
        """❌ should NOT be used for reactions (Telegram rejects it)."""
        from bridge.response import INVALID_REACTIONS

        assert "❌" in INVALID_REACTIONS

    def test_reaction_constants_are_distinct(self):
        """All reaction constants should be different emojis."""
        from bridge.response import (
            REACTION_COMPLETE,
            REACTION_ERROR,
            REACTION_PROCESSING,
            REACTION_RECEIVED,
            REACTION_SUCCESS,
        )

        all_reactions = [
            REACTION_RECEIVED,
            REACTION_PROCESSING,
            REACTION_SUCCESS,
            REACTION_COMPLETE,
            REACTION_ERROR,
        ]
        assert len(set(all_reactions)) == len(all_reactions)

    def test_no_validated_reaction_in_invalid_list(self):
        """No emoji should be in both VALIDATED_REACTIONS and INVALID_REACTIONS."""
        from bridge.response import INVALID_REACTIONS, VALIDATED_REACTIONS

        overlap = set(VALIDATED_REACTIONS) & set(INVALID_REACTIONS)
        assert overlap == set(), f"Emojis in both valid and invalid lists: {overlap}"


class TestFilterToolLogsFallback:
    """Test that filter_tool_logs doesn't silently drop non-empty responses."""

    def test_filter_preserves_normal_text(self):
        """Plain text without tool prefixes should pass through unchanged."""
        from bridge.response import filter_tool_logs

        result = filter_tool_logs("Here is the result of the analysis.")
        assert result == "Here is the result of the analysis."

    def test_filter_strips_tool_log_lines(self):
        """Lines starting with tool-use prefixes should be removed."""
        from bridge.response import filter_tool_logs

        result = filter_tool_logs("🛠️ exec: ls -la\n🔎 web_search: python docs")
        assert result == ""

    def test_filter_keeps_mixed_content(self):
        """In mixed content, tool lines stripped but real text preserved."""
        from bridge.response import filter_tool_logs

        result = filter_tool_logs(
            "🛠️ exec: ls -la\nHere are the results.\n📖 read: file.py"
        )
        assert "Here are the results." in result

    def test_filter_handles_empty_string(self):
        """Empty input should return empty output."""
        from bridge.response import filter_tool_logs

        assert filter_tool_logs("") == ""

    def test_filter_preserves_multiline_real_content(self):
        """Multiple lines of real content should all be preserved."""
        from bridge.response import filter_tool_logs

        text = "First line of response.\nSecond line of response.\nThird line."
        result = filter_tool_logs(text)
        assert "First line" in result
        assert "Second line" in result
        assert "Third line" in result


class TestAutoContineCountPersistence:
    """Test that auto_continue_count persists on RedisJob."""

    @pytest.mark.asyncio
    async def test_redis_job_stores_auto_continue_count(self):
        """RedisJob should persist auto_continue_count when explicitly set."""
        from agent.job_queue import RedisJob

        job = await RedisJob.async_create(
            project_key="test_project_ac",
            status="pending",
            created_at=1000.0,
            session_id="test_session_ac",
            working_dir="/tmp/test",
            message_text="continue",
            sender_name="System",
            chat_id="chat_ac_123",
            message_id=456,
            auto_continue_count=2,
        )
        assert job.auto_continue_count == 2
        await job.async_delete()

    @pytest.mark.asyncio
    async def test_redis_job_default_auto_continue_count(self):
        """Default auto_continue_count should be 0 for fresh jobs."""
        from agent.job_queue import RedisJob

        job = await RedisJob.async_create(
            project_key="test_project_ac_default",
            status="pending",
            created_at=1000.0,
            session_id="test_session_ac_default",
            working_dir="/tmp/test",
            message_text="hello",
            sender_name="Tom",
            chat_id="chat_ac_default",
            message_id=457,
        )
        assert job.auto_continue_count == 0
        await job.async_delete()
