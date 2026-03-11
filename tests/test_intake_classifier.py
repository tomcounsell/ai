"""Tests for intake message intent classification (#320).

Tests the classify_message_intent() function that determines whether a message
is an interjection into an active session, a new work request, or an
acknowledgment of completed work.

Unit tests use mocks for the Anthropic API.
Integration tests call the real Haiku API for accuracy validation.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tools.classifier import (
    VALID_INTENTS,
    classify_message_intent,
)

# =============================================================================
# UNIT TESTS: Fast path (no API call)
# =============================================================================


class TestFastPathNoApiCall:
    """Messages that should be classified without calling the API."""

    def test_empty_message_returns_new_work(self):
        result = classify_message_intent("")
        assert result["intent"] == "new_work"
        assert result["confidence"] == 1.0

    def test_whitespace_message_returns_new_work(self):
        result = classify_message_intent("   ")
        assert result["intent"] == "new_work"
        assert result["confidence"] == 1.0

    def test_none_message_returns_new_work(self):
        result = classify_message_intent(None)
        assert result["intent"] == "new_work"
        assert result["confidence"] == 1.0

    def test_no_session_context_returns_new_work(self):
        """Without session context, there's nothing to interject into."""
        result = classify_message_intent(
            "Actually make it blue",
            session_context="",
            session_expectations="",
        )
        assert result["intent"] == "new_work"
        assert result["confidence"] == 1.0

    def test_response_structure_always_valid(self):
        """All fast-path responses have the required keys."""
        result = classify_message_intent("")
        assert "intent" in result
        assert "confidence" in result
        assert "reason" in result
        assert result["intent"] in VALID_INTENTS


# =============================================================================
# UNIT TESTS: Mocked API responses
# =============================================================================


class TestMockedClassification:
    """Tests with mocked Anthropic API to verify classification logic."""

    def _mock_haiku_response(self, response_dict: dict):
        """Create a mocked Anthropic response."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=json.dumps(response_dict))]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_interjection_classification(self):
        """High-confidence interjection is returned correctly."""
        mock_client = self._mock_haiku_response(
            {"intent": "interjection", "confidence": 0.95, "reason": "Course correction"}
        )
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "Actually make it blue instead",
                session_context="Working on UI redesign",
            )
        assert result["intent"] == "interjection"
        assert result["confidence"] == 0.95

    def test_new_work_classification(self):
        """New work request is returned correctly."""
        mock_client = self._mock_haiku_response(
            {"intent": "new_work", "confidence": 0.90, "reason": "New feature request"}
        )
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "Add dark mode to the settings page",
                session_context="Fixing login bug",
            )
        assert result["intent"] == "new_work"
        assert result["confidence"] == 0.90

    def test_acknowledgment_classification(self):
        """Acknowledgment is returned correctly."""
        mock_client = self._mock_haiku_response(
            {"intent": "acknowledgment", "confidence": 0.92, "reason": "User approves work"}
        )
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "Looks good, ship it",
                session_context="PR review pending",
                session_expectations="Awaiting approval",
                session_status="dormant",
            )
        assert result["intent"] == "acknowledgment"
        assert result["confidence"] == 0.92

    def test_low_confidence_defaults_to_new_work(self):
        """Below 0.80 confidence threshold, intent defaults to new_work."""
        mock_client = self._mock_haiku_response(
            {"intent": "interjection", "confidence": 0.60, "reason": "Unclear follow-up"}
        )
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "Something about the project",
                session_context="Working on feature",
            )
        assert result["intent"] == "new_work"
        assert "Below confidence threshold" in result["reason"]

    def test_api_failure_defaults_to_new_work(self):
        """API errors gracefully degrade to new_work."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "Follow up on the bug",
                session_context="Debugging issue",
            )
        assert result["intent"] == "new_work"
        assert result["confidence"] == 0.0
        assert "Classification failed" in result["reason"]

    def test_invalid_intent_type_raises_and_defaults(self):
        """Invalid intent type from API defaults to new_work."""
        mock_client = self._mock_haiku_response(
            {"intent": "question", "confidence": 0.90, "reason": "Asking something"}
        )
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "How does this work?",
                session_context="Working on feature",
            )
        # Should catch the ValueError and return new_work
        assert result["intent"] == "new_work"
        assert result["confidence"] == 0.0

    def test_markdown_code_block_response_handled(self):
        """Handles responses wrapped in markdown code blocks."""
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='```json\n{"intent": "interjection", "confidence": 0.88, '
                '"reason": "Follow-up"}\n```'
            )
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "Here is more context",
                session_context="Investigating bug",
            )
        assert result["intent"] == "interjection"
        assert result["confidence"] == 0.88

    def test_confidence_exactly_at_threshold(self):
        """Confidence exactly at 0.80 should be accepted (not defaulted)."""
        mock_client = self._mock_haiku_response(
            {"intent": "interjection", "confidence": 0.80, "reason": "Borderline"}
        )
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "One more thing",
                session_context="Active work session",
            )
        assert result["intent"] == "interjection"
        assert result["confidence"] == 0.80

    def test_new_work_not_affected_by_threshold(self):
        """new_work intent is never overridden by threshold check."""
        mock_client = self._mock_haiku_response(
            {"intent": "new_work", "confidence": 0.50, "reason": "Low confidence work"}
        )
        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("tools.classifier.get_anthropic_api_key", return_value="test-key"),
        ):
            result = classify_message_intent(
                "Do something else",
                session_context="Current task",
            )
        assert result["intent"] == "new_work"
        assert result["confidence"] == 0.50


# =============================================================================
# UNIT TESTS: Prompt content validation
# =============================================================================


class TestPromptContent:
    """Verify the classification prompt includes all required elements."""

    def test_prompt_includes_all_intents(self):
        from tools.classifier import INTENT_CLASSIFICATION_PROMPT

        for intent in VALID_INTENTS:
            assert intent in INTENT_CLASSIFICATION_PROMPT

    def test_prompt_includes_json_format(self):
        from tools.classifier import INTENT_CLASSIFICATION_PROMPT

        assert '"intent"' in INTENT_CLASSIFICATION_PROMPT
        assert '"confidence"' in INTENT_CLASSIFICATION_PROMPT
        assert '"reason"' in INTENT_CLASSIFICATION_PROMPT

    def test_prompt_includes_context_placeholders(self):
        from tools.classifier import INTENT_CLASSIFICATION_PROMPT

        assert "{message}" in INTENT_CLASSIFICATION_PROMPT
        assert "{session_context}" in INTENT_CLASSIFICATION_PROMPT
        assert "{session_expectations}" in INTENT_CLASSIFICATION_PROMPT
        assert "{session_status}" in INTENT_CLASSIFICATION_PROMPT


# =============================================================================
# UNIT TESTS: AgentSession steering integration
# =============================================================================


class TestSessionSteeringIntegration:
    """Verify AgentSession push/pop steering messages work correctly."""

    def test_push_and_pop_steering_message(self):
        from models.agent_session import AgentSession

        session = AgentSession()
        session.push_steering_message("test message 1")
        session.push_steering_message("test message 2")

        messages = session.pop_steering_messages()
        assert messages == ["test message 1", "test message 2"]

    def test_pop_empty_returns_empty_list(self):
        from models.agent_session import AgentSession

        session = AgentSession()
        messages = session.pop_steering_messages()
        assert messages == []

    def test_pop_clears_queue(self):
        from models.agent_session import AgentSession

        session = AgentSession()
        session.push_steering_message("hello")
        session.pop_steering_messages()

        # Second pop should be empty
        messages = session.pop_steering_messages()
        assert messages == []


# =============================================================================
# INTEGRATION TESTS: Real Haiku API calls
# =============================================================================


class TestRealHaikuClassification:
    """Integration tests using real Haiku API calls.

    These tests validate that the Haiku model correctly classifies
    representative messages for each intent type.
    """

    @pytest.mark.parametrize(
        "message,session_context,expected_intent",
        [
            # Interjections - follow-ups to active work
            (
                "Actually, make the button blue instead of red",
                "Working on UI color scheme for the dashboard",
                "interjection",
            ),
            (
                "Here is the additional context you asked for",
                "Investigating production bug, asked for error logs",
                "interjection",
            ),
            (
                "Also add error handling for the edge case",
                "Implementing the payment processing module",
                "interjection",
            ),
            # New work - unrelated to active session
            (
                "Fix the broken login button on the homepage",
                "Working on database migration scripts",
                "new_work",
            ),
            (
                "What time is my next meeting?",
                "Debugging Redis connection issues",
                "new_work",
            ),
            (
                "Set up a new CI/CD pipeline for the frontend repo",
                "Reviewing pull request for backend API",
                "new_work",
            ),
        ],
    )
    def test_intent_classification_accuracy(self, message, session_context, expected_intent):
        """Verify Haiku classifies representative messages correctly."""
        result = classify_message_intent(
            message=message,
            session_context=session_context,
        )
        assert result["intent"] == expected_intent, (
            f"Expected {expected_intent} for message {message!r}, "
            f"got {result['intent']} (reason: {result['reason']})"
        )
        assert result["confidence"] >= 0.80

    @pytest.mark.parametrize(
        "message,session_context,session_expectations",
        [
            # Acknowledgments - signal work is done
            (
                "Looks good, ship it",
                "PR ready for review",
                "Waiting for human approval to merge",
            ),
            (
                "LGTM, thanks",
                "Completed the requested feature",
                "Waiting for sign-off",
            ),
            (
                "Perfect, exactly what I wanted",
                "Delivered the analysis report",
                "Awaiting confirmation",
            ),
        ],
    )
    def test_acknowledgment_with_dormant_context(
        self, message, session_context, session_expectations
    ):
        """Verify Haiku classifies acknowledgments when session is dormant."""
        result = classify_message_intent(
            message=message,
            session_context=session_context,
            session_expectations=session_expectations,
            session_status="dormant",
        )
        assert result["intent"] == "acknowledgment", (
            f"Expected acknowledgment for message {message!r}, "
            f"got {result['intent']} (reason: {result['reason']})"
        )
        assert result["confidence"] >= 0.80

    def test_graceful_failure_with_invalid_key(self):
        """API failure with bad key returns new_work gracefully."""
        with patch("tools.classifier.get_anthropic_api_key", return_value="invalid-key"):
            result = classify_message_intent(
                "Follow up on the bug",
                session_context="Working on fix",
            )
        assert result["intent"] == "new_work"
        assert result["confidence"] == 0.0
