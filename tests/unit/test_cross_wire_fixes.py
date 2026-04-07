"""Tests for cross-wire bug fixes (issue #232).

Tests three fixes:
1. Classifier Q&A awareness — informational answers classified as COMPLETION
2. Session isolation — fresh sessions don't set continue_conversation=True
3. Non-SDLC auto-continue guard — planning language auto-continues, answers deliver

Run with: pytest tests/test_cross_wire_fixes.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# === Fix 1: Classifier Q&A awareness ===


class TestClassifierQACompletion:
    """Verify that Q&A/informational answers are classified as COMPLETION."""

    def test_qa_answer_heuristic_not_status(self):
        """Informational answers should not be classified as STATUS_UPDATE by heuristics."""
        from bridge.summarizer import OutputType, _classify_with_heuristics

        # Typical Q&A answer about a system feature
        qa_answer = (
            "The summarizer works by first classifying the agent output using an LLM "
            "classifier. It determines whether the output is a status update, completion, "
            "question, blocker, or error. Based on this classification, it decides whether "
            "to auto-continue (for status updates) or deliver to the user."
        )
        result = _classify_with_heuristics(qa_answer)
        # Heuristics may return None (meaning "pass to LLM") or a type.
        # The key assertion: it should NOT be classified as STATUS_UPDATE
        if result.output_type is not None:
            assert result.output_type != OutputType.STATUS_UPDATE, (
                f"Q&A answer was classified as STATUS_UPDATE by heuristics: {result}"
            )

    def test_architecture_explanation_heuristic_not_status(self):
        """Architecture explanations should not be status updates."""
        from bridge.summarizer import OutputType, _classify_with_heuristics

        explanation = (
            "Here's how the routing system handles messages: When a Telegram message "
            "arrives, the bridge extracts metadata (chat_id, sender, thread info) and "
            "creates an AgentSession in Redis. The job queue picks up the session and "
            "spawns a Claude Code subprocess with the appropriate system prompt."
        )
        result = _classify_with_heuristics(explanation)
        if result.output_type is not None:
            assert result.output_type != OutputType.STATUS_UPDATE

    @pytest.mark.asyncio
    async def test_qa_answer_classified_as_completion(self):
        """Full classifier should classify Q&A answers as COMPLETION."""
        from bridge.summarizer import OutputType, classify_output

        qa_answer = (
            "The summarizer feature works by classifying agent output into five categories: "
            "status_update, completion, question, blocker, and error. When an output is "
            "classified as a status update, the bridge automatically re-enqueues the job "
            "to continue the session. Completions are delivered to the user via Telegram. "
            "The classifier uses an LLM with few-shot examples to make these decisions."
        )
        result = await classify_output(qa_answer)
        assert result.output_type == OutputType.COMPLETION, (
            f"Q&A answer classified as {result.output_type.value} "
            f"(expected COMPLETION): {result.reason}"
        )


# === Fix 2: Session isolation ===


try:
    import agent.sdk_client  # noqa: F401

    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False


@pytest.mark.skipif(not _SDK_AVAILABLE, reason="claude_agent_sdk not importable")
class TestSessionIsolation:
    """Verify that _has_prior_session correctly gates continue_conversation."""

    def test_has_prior_session_returns_false_for_unknown(self):
        """Unknown session_id should return False (don't continue)."""
        from agent.sdk_client import _has_prior_session

        # Random ID that doesn't exist in Redis
        result = _has_prior_session("nonexistent_session_12345")
        assert result is False

    def test_has_prior_session_handles_none_gracefully(self):
        """None session_id should not crash."""
        from agent.sdk_client import _has_prior_session

        # The function expects a string, but should handle edge cases
        result = _has_prior_session("")
        assert result is False

    def test_create_options_fresh_session_no_continue(self):
        """Fresh sessions should not set continue_conversation=True."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        # Use a session_id that has no prior AgentSession in Redis
        options = agent._create_options(session_id="fresh_session_no_prior_232")
        assert options.continue_conversation is False, (
            "Fresh session should not continue conversation"
        )
        assert options.resume is None, "Fresh session should not resume"

    def test_create_options_no_session_id(self):
        """No session_id should not set continue_conversation."""
        from agent.sdk_client import ValorAgent

        agent = ValorAgent()
        options = agent._create_options(session_id=None)
        assert options.continue_conversation is False


# === Fix 3: Non-SDLC auto-continue guard ===
# Removed: TestNonSDLCAutoContinueGuard — tested _is_planning_language
# which was part of the old classifier→coach→routing chain.
# The nudge loop now handles all routing decisions. See issue #309.
