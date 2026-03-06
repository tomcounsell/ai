"""Tests for work request classification (SDLC routing).

Tests the classify_work_request() function that determines whether a message
should be routed through SDLC (orchestrator in ai/) or directly to the
target project (Q&A).
"""

import pytest

from bridge.routing import classify_work_request


class TestFastPathPassthrough:
    """Messages that should bypass LLM classification entirely."""

    @pytest.mark.parametrize(
        "message",
        [
            "/sdlc issue 123",
            "/do-plan my-feature",
            "/do-build docs/plans/my-plan.md",
            "/do-test",
            "/do-patch",
            "/do-pr-review 42",
            "/do-docs",
            "/prime",
            "/setup",
            "/update",
        ],
    )
    def test_slash_commands_passthrough(self, message):
        assert classify_work_request(message) == "passthrough"

    @pytest.mark.parametrize(
        "message",
        [
            "continue",
            "merge",
            "👍",
            "yes",
            "no",
            "ok",
            "lgtm",
            "LGTM",
            "Continue",
        ],
    )
    def test_continuation_commands_passthrough(self, message):
        assert classify_work_request(message) == "passthrough"

    @pytest.mark.parametrize(
        "message",
        ["", "  ", None],
    )
    def test_empty_messages_passthrough(self, message):
        assert classify_work_request(message) == "passthrough"


class TestFastPathSdlc:
    """Messages that should be classified as SDLC via fast path."""

    @pytest.mark.parametrize(
        "message",
        [
            "issue 123",
            "issue #123",
            "#42",
            "#1",
        ],
    )
    def test_issue_references_sdlc(self, message):
        assert classify_work_request(message) == "sdlc"


class TestLlmClassification:
    """Messages that require LLM classification.

    These tests call the actual Ollama/Haiku backend.
    They verify the classification prompt works correctly.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "Fix the login bug",
            "Add a dark mode toggle to settings",
            "Implement user authentication",
            "Refactor the database queries",
            "The checkout flow is broken, users can't complete purchases",
            "Update the API to support pagination",
            "Create a new endpoint for user profiles",
        ],
    )
    def test_work_requests_classified_as_sdlc(self, message):
        result = classify_work_request(message)
        assert result == "sdlc", f"Expected 'sdlc' for: {message}, got: {result}"

    @pytest.mark.parametrize(
        "message",
        [
            "How does the auth system work?",
            "What is the database schema for users?",
            "Can you explain the deployment process?",
            "What version of Python are we using?",
            "Where is the config file for the API?",
        ],
    )
    def test_questions_classified_as_question(self, message):
        result = classify_work_request(message)
        assert result == "question", f"Expected 'question' for: {message}, got: {result}"


class TestClassifierSdlcType:
    """Tests for tools/classifier.py accepting 'sdlc' as a valid classification type.

    These are unit tests that mock the Anthropic API to verify the classifier
    accepts and validates 'sdlc' responses (fixes issue #276, Bug 1).
    """

    def test_sdlc_type_accepted_by_validator(self):
        """The classifier validation logic accepts 'sdlc' as a valid type."""
        from unittest.mock import MagicMock, patch

        # Mock the Anthropic API to return an sdlc classification
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"type": "sdlc", "confidence": 0.95, "reason": "SDLC pipeline reference"}'
            )
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with (
            patch("tools.classifier.anthropic.Anthropic", return_value=mock_client),
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
        ):
            from tools.classifier import classify_request

            result = classify_request("SDLC issue 274")

        assert result["type"] == "sdlc"
        assert result["confidence"] == 0.95

    def test_sdlc_prompt_includes_sdlc_category(self):
        """The classification prompt includes the 'sdlc' category."""
        from tools.classifier import CLASSIFICATION_PROMPT

        assert "sdlc" in CLASSIFICATION_PROMPT.lower()
        assert '"sdlc"' in CLASSIFICATION_PROMPT

    def test_all_four_types_in_prompt(self):
        """The prompt lists all four classification types."""
        from tools.classifier import CLASSIFICATION_PROMPT

        for type_name in ["bug", "feature", "chore", "sdlc"]:
            assert type_name in CLASSIFICATION_PROMPT


class TestProcessNarrationStripping:
    """Tests for the process narration stripping in summarizer."""

    def test_strips_check_narration(self):
        from bridge.summarizer import _strip_process_narration

        text = "Let me check the code.\nThe function returns 42."
        result = _strip_process_narration(text)
        assert "Let me check" not in result
        assert "The function returns 42." in result

    def test_strips_look_narration(self):
        from bridge.summarizer import _strip_process_narration

        text = "Looking at the file:\nThe config is correct."
        result = _strip_process_narration(text)
        assert "Looking at" not in result
        assert "The config is correct." in result

    def test_preserves_meaningful_ill_statements(self):
        from bridge.summarizer import _strip_process_narration

        text = "I'll document the API changes in docs/api.md"
        result = _strip_process_narration(text)
        assert "I'll document" in result

    def test_preserves_meaningful_content(self):
        from bridge.summarizer import _strip_process_narration

        text = "The fix is deployed and working."
        result = _strip_process_narration(text)
        assert text == result

    def test_does_not_return_empty(self):
        from bridge.summarizer import _strip_process_narration

        text = "Let me check this.\nLet me look at that."
        result = _strip_process_narration(text)
        # Should return original if everything stripped
        assert len(result) > 0

    def test_strips_multiple_narration_lines(self):
        from bridge.summarizer import _strip_process_narration

        text = (
            "Let me check the code.\n"
            "Good.\n"
            "Now let me investigate further.\n"
            "Found the bug in line 10.\n"
            "The issue is a missing null check."
        )
        result = _strip_process_narration(text)
        assert "Let me check" not in result
        assert "Good." not in result
        assert "Now let me investigate" not in result
        assert "Found the bug" in result
        assert "missing null check" in result
