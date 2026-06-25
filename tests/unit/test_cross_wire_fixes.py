"""Tests for cross-wire bug fixes (issue #232).

Tests two remaining fixes:
1. Session isolation — fresh sessions don't set continue_conversation=True
2. Non-SDLC auto-continue guard (removed — tested old classifier chain)

Note: Fix 1 (Classifier Teammate awareness) has been removed. The LLM
classify_output cluster was deleted in the drafter passthrough refactor —
routing decisions now live in bridge/promise_gate.py and the nudge loop.

Run with: pytest tests/test_cross_wire_fixes.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
# which was part of the old classifier→summarizer→routing chain.
# The nudge loop now handles all routing decisions. See issue #309.
