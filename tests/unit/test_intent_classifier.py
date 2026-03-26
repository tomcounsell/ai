"""Tests for the intent classifier (Q&A vs work routing).

Tests the parsing logic, threshold behavior, and golden examples.
The actual Haiku API call is mocked for unit tests.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from agent.intent_classifier import (
    QA_CONFIDENCE_THRESHOLD,
    IntentResult,
    _parse_classifier_response,
    classify_intent,
)

# === IntentResult Tests ===


class TestIntentResult:
    def test_qa_high_confidence(self):
        r = IntentResult(intent="qa", confidence=0.95, reasoning="asking for info")
        assert r.is_qa is True
        assert r.is_work is False

    def test_qa_low_confidence_defaults_to_work(self):
        r = IntentResult(intent="qa", confidence=0.85, reasoning="ambiguous")
        assert r.is_qa is False
        assert r.is_work is True

    def test_work_intent(self):
        r = IntentResult(intent="work", confidence=0.98, reasoning="action request")
        assert r.is_qa is False
        assert r.is_work is True

    def test_threshold_boundary(self):
        # Exactly at threshold: not Q&A (needs to exceed)
        r = IntentResult(intent="qa", confidence=QA_CONFIDENCE_THRESHOLD, reasoning="boundary")
        assert r.is_qa is True

        below = IntentResult(
            intent="qa", confidence=QA_CONFIDENCE_THRESHOLD - 0.01, reasoning="below"
        )
        assert below.is_qa is False

    def test_frozen_dataclass(self):
        r = IntentResult(intent="qa", confidence=0.95, reasoning="test")
        with pytest.raises(AttributeError):
            r.intent = "work"


# === Parser Tests ===


class TestParseClassifierResponse:
    def test_valid_qa_response(self):
        r = _parse_classifier_response("qa 0.97 User is asking about architecture")
        assert r.intent == "qa"
        assert r.confidence == 0.97
        assert "architecture" in r.reasoning

    def test_valid_work_response(self):
        r = _parse_classifier_response("work 0.99 User wants to fix a bug")
        assert r.intent == "work"
        assert r.confidence == 0.99

    def test_case_insensitive_intent(self):
        r = _parse_classifier_response("QA 0.95 question about system")
        assert r.intent == "qa"

    def test_no_reasoning(self):
        r = _parse_classifier_response("work 0.88")
        assert r.intent == "work"
        assert r.confidence == 0.88
        assert r.reasoning == ""

    def test_unparseable_response(self):
        r = _parse_classifier_response("gibberish")
        assert r.intent == "work"
        assert r.confidence == 0.0

    def test_unknown_intent(self):
        r = _parse_classifier_response("maybe 0.50 unsure")
        assert r.intent == "work"
        assert r.confidence == 0.0

    def test_bad_confidence(self):
        r = _parse_classifier_response("qa abc some reasoning")
        assert r.intent == "work"
        assert r.confidence == 0.0

    def test_empty_string(self):
        r = _parse_classifier_response("")
        assert r.intent == "work"
        assert r.confidence == 0.0

    def test_whitespace_handling(self):
        r = _parse_classifier_response("  qa  0.96  asking about feature  ")
        assert r.intent == "qa"
        assert r.confidence == 0.96


# === classify_intent() Tests (mocked API) ===


def _make_mock_response(text: str):
    """Create a mock Anthropic API response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


class TestClassifyIntent:
    def test_qa_classification(self):
        with patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"):
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _make_mock_response(
                "qa 0.97 User is asking for information"
            )
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = asyncio.run(classify_intent("How does the bridge work?"))
                assert result.intent == "qa"
                assert result.confidence == 0.97
                assert result.is_qa is True

    def test_work_classification(self):
        with patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"):
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _make_mock_response(
                "work 0.99 User wants to fix something"
            )
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = asyncio.run(classify_intent("Fix the bridge"))
                assert result.intent == "work"
                assert result.confidence == 0.99
                assert result.is_work is True

    def test_no_api_key_defaults_to_work(self):
        with patch("utils.api_keys.get_anthropic_api_key", return_value=""):
            result = asyncio.run(classify_intent("What time is it?"))
            assert result.intent == "work"
            assert result.is_work is True

    def test_api_error_defaults_to_work(self):
        with patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"):
            with patch("anthropic.Anthropic", side_effect=RuntimeError("API down")):
                result = asyncio.run(classify_intent("What's the status?"))
                assert result.intent == "work"
                assert result.is_work is True

    def test_context_passed_to_api(self):
        with patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"):
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _make_mock_response(
                "qa 0.95 follow-up question"
            )
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = asyncio.run(
                    classify_intent(
                        "And what about the nudge loop?",
                        context={
                            "recent_messages": [
                                "How does the bridge work?",
                                "It uses Telethon",
                            ]
                        },
                    )
                )
                assert result.intent == "qa"
                # Verify context was included in the API call
                call_args = mock_client.messages.create.call_args
                user_msg = call_args[1]["messages"][0]["content"]
                assert "Recent conversation:" in user_msg
                assert "How does the bridge work?" in user_msg


# === Golden Examples (parser-level, no API needed) ===


GOLDEN_QA_EXAMPLES = [
    ("qa 0.98", "What's the status of feature X?"),
    ("qa 0.97", "How does the bridge work?"),
    ("qa 0.99", "Where is the observer prompt?"),
    ("qa 0.92", "What's broken in the bridge?"),
    ("qa 0.95", "Show me the recent PRs"),
    ("qa 0.93", "What tests are failing?"),
    ("qa 0.96", "Who worked on the memory system?"),
    ("qa 0.97", "When was the last deployment?"),
    ("qa 0.98", "Explain the nudge loop"),
    ("qa 0.95", "What's in the .env file?"),
    ("qa 0.96", "How many open issues do we have?"),
    ("qa 0.97", "What model does the classifier use?"),
    ("qa 0.94", "Can you check if the tests pass?"),
    ("qa 0.93", "What's the current branch?"),
    ("qa 0.96", "List the MCP servers"),
]

GOLDEN_WORK_EXAMPLES = [
    ("work 0.99", "Fix the bridge"),
    ("work 0.98", "Add a new endpoint for health checks"),
    ("work 0.97", "Create an issue for the memory leak"),
    ("work 0.99", "Deploy the latest changes"),
    ("work 0.96", "Update the README"),
    ("work 0.88", "The observer prompt has a bug"),
    ("work 0.95", "ok fix that"),
    ("work 0.99", "Merge PR 42"),
    ("work 0.98", "Make the tests pass"),
    ("work 0.97", "Refactor the job queue"),
    ("work 0.93", "Can you update the docs?"),
    ("work 0.99", "Complete issue 499"),
    ("work 0.99", "Run the SDLC pipeline on this"),
    ("work 0.96", "Write a test for the classifier"),
    ("work 0.94", "Clean up the dead code"),
]


class TestGoldenExamples:
    @pytest.mark.parametrize("response,description", GOLDEN_QA_EXAMPLES)
    def test_qa_examples_parse_correctly(self, response, description):
        result = _parse_classifier_response(response)
        assert result.intent == "qa", f"Expected qa for: {description}"
        assert result.confidence >= 0.90, f"Expected high confidence for: {description}"

    @pytest.mark.parametrize("response,description", GOLDEN_WORK_EXAMPLES)
    def test_work_examples_parse_correctly(self, response, description):
        result = _parse_classifier_response(response)
        assert result.intent == "work", f"Expected work for: {description}"
