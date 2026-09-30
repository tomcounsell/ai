"""Tests for the intent classifier (four-way PM routing).

Tests IntentResult threshold behavior and classify_intent's caching,
fail-safe, and typed-output (#1925) behavior for teammate, collaboration,
other, and work intents. The actual Haiku API call is mocked at the
``agent.llm.run_typed`` boundary for unit tests -- no real network call and
no dependence on PydanticAI's internal Anthropic tool-calling wire format.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import agent.llm  # noqa: F401 -- ensures "agent.llm" resolves as a patch target below
from agent.intent_classifier import (
    TEAMMATE_CONFIDENCE_THRESHOLD,
    IntentClassification,
    IntentResult,
    classify_intent,
)

# === IntentResult Tests ===


class TestIntentResult:
    def test_teammate_high_confidence(self):
        r = IntentResult(intent="teammate", confidence=0.95, reasoning="asking for info")
        assert r.is_teammate is True
        assert r.is_work is False
        assert r.is_collaboration is False
        assert r.is_other is False
        assert r.is_direct_action is False

    def test_teammate_low_confidence(self):
        r = IntentResult(intent="teammate", confidence=0.85, reasoning="ambiguous")
        assert r.is_teammate is False
        assert r.is_work is False
        assert r.is_collaboration is False
        assert r.is_other is False

    def test_work_intent(self):
        r = IntentResult(intent="work", confidence=0.98, reasoning="action request")
        assert r.is_teammate is False
        assert r.is_collaboration is False
        assert r.is_other is False
        assert r.is_work is True
        assert r.is_direct_action is False

    def test_collaboration_high_confidence(self):
        r = IntentResult(intent="collaboration", confidence=0.95, reasoning="direct task")
        assert r.is_collaboration is True
        assert r.is_teammate is False
        assert r.is_work is False
        assert r.is_other is False
        assert r.is_direct_action is True

    def test_collaboration_low_confidence(self):
        """Collaboration is not gated by confidence threshold (unlike teammate)."""
        r = IntentResult(intent="collaboration", confidence=0.50, reasoning="low conf")
        assert r.is_collaboration is True
        assert r.is_direct_action is True
        assert r.is_work is False

    def test_other_intent(self):
        r = IntentResult(intent="other", confidence=0.92, reasoning="ambiguous discussion")
        assert r.is_other is True
        assert r.is_direct_action is True
        assert r.is_teammate is False
        assert r.is_collaboration is False
        assert r.is_work is False

    def test_other_low_confidence(self):
        """Other is not gated by confidence threshold (unlike teammate)."""
        r = IntentResult(intent="other", confidence=0.50, reasoning="low conf")
        assert r.is_other is True
        assert r.is_direct_action is True
        assert r.is_work is False

    def test_threshold_boundary(self):
        # Exactly at threshold: is teammate (needs to meet or exceed)
        r = IntentResult(
            intent="teammate", confidence=TEAMMATE_CONFIDENCE_THRESHOLD, reasoning="boundary"
        )
        assert r.is_teammate is True

        below = IntentResult(
            intent="teammate", confidence=TEAMMATE_CONFIDENCE_THRESHOLD - 0.01, reasoning="below"
        )
        assert below.is_teammate is False

    def test_frozen_dataclass(self):
        r = IntentResult(intent="teammate", confidence=0.95, reasoning="test")
        with pytest.raises(AttributeError):
            r.intent = "work"


# === classify_intent() Tests (mocked at the agent.llm.run_typed boundary) ===
#
# #1925: the classifier now gets structured output directly from
# agent.llm.run_typed (an IntentClassification instance) instead of parsing
# a raw single-line text response, so these tests mock run_typed rather than
# the Anthropic SDK. run_typed is imported locally inside classify_intent
# (matching this module's existing local-import style), so the patch target
# is its definition site, "agent.llm.run_typed" -- patching
# "agent.intent_classifier.run_typed" would miss the fresh per-call import.


class TestClassifyIntent:
    @pytest.fixture(autouse=True)
    def isolated_cache(self, monkeypatch, tmp_path):
        """Replace the module-level cache singleton with a tmp_path-rooted instance.

        Runs before every test to guarantee a cold cache. Required because the
        cache layer would otherwise short-circuit the mocked run_typed call and
        cause `mock_run_typed.assert_called_once()` to fail on the second test
        that shares the same key derivation path.
        """
        from agent import intent_classifier
        from utils.json_cache import JsonCache

        monkeypatch.setattr(
            intent_classifier,
            "_cache",
            JsonCache(tmp_path / "intent_cache.json", max_entries=10),
        )

    def test_teammate_classification(self):
        mock_run_typed = AsyncMock(
            return_value=IntentClassification(
                intent="teammate", confidence=0.97, reasoning="User is asking for information"
            )
        )
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
            patch("agent.llm.run_typed", mock_run_typed),
        ):
            result = asyncio.run(classify_intent("How does the bridge work?"))
        assert isinstance(result, IntentResult)
        assert result.intent == "teammate"
        assert result.confidence == 0.97
        assert result.is_teammate is True
        mock_run_typed.assert_called_once()

    def test_work_classification(self):
        mock_run_typed = AsyncMock(
            return_value=IntentClassification(
                intent="work", confidence=0.99, reasoning="User wants to fix something"
            )
        )
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
            patch("agent.llm.run_typed", mock_run_typed),
        ):
            result = asyncio.run(classify_intent("Fix the bridge"))
        assert result.intent == "work"
        assert result.confidence == 0.99
        assert result.is_work is True

    def test_collaboration_classification(self):
        mock_run_typed = AsyncMock(
            return_value=IntentClassification(
                intent="collaboration", confidence=0.96, reasoning="User wants to draft an issue"
            )
        )
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
            patch("agent.llm.run_typed", mock_run_typed),
        ):
            result = asyncio.run(classify_intent("Draft an issue for the flaky test"))
        assert result.intent == "collaboration"
        assert result.confidence == 0.96
        assert result.is_collaboration is True
        assert result.is_direct_action is True
        assert result.is_work is False

    def test_other_classification(self):
        mock_run_typed = AsyncMock(
            return_value=IntentClassification(
                intent="other", confidence=0.93, reasoning="User is brainstorming"
            )
        )
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
            patch("agent.llm.run_typed", mock_run_typed),
        ):
            result = asyncio.run(classify_intent("What should we do about the architecture?"))
        assert result.intent == "other"
        assert result.confidence == 0.93
        assert result.is_other is True
        assert result.is_direct_action is True
        assert result.is_work is False

    def test_no_api_key_defaults_to_work(self):
        with patch("utils.api_keys.get_anthropic_api_key", return_value=""):
            result = asyncio.run(classify_intent("What time is it?"))
        assert result.intent == "work"
        assert result.is_work is True

    def test_api_error_defaults_to_work(self):
        """LLMCallError (or any provider failure) fails safe to work -- unchanged
        fail-safe posture, now surfaced through agent.llm.LLMCallError instead
        of a raw anthropic SDK exception (#1925)."""
        from agent.llm import LLMCallError

        mock_run_typed = AsyncMock(side_effect=LLMCallError("simulated provider error"))
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
            patch("agent.llm.run_typed", mock_run_typed),
        ):
            result = asyncio.run(classify_intent("What's the status?"))
        assert result.intent == "work"
        assert result.is_work is True

    def test_context_passed_to_prompt(self):
        mock_run_typed = AsyncMock(
            return_value=IntentClassification(
                intent="teammate", confidence=0.95, reasoning="follow-up question"
            )
        )
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
            patch("agent.llm.run_typed", mock_run_typed),
        ):
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
        assert result.intent == "teammate"
        # Verify context was included in the prompt passed to run_typed
        call_args = mock_run_typed.call_args
        prompt = call_args[0][0]
        assert "Recent conversation:" in prompt
        assert "How does the bridge work?" in prompt

    def test_output_type_is_intent_classification(self):
        """run_typed is called with the typed IntentClassification output model."""
        mock_run_typed = AsyncMock(
            return_value=IntentClassification(intent="work", confidence=0.9, reasoning="x")
        )
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
            patch("agent.llm.run_typed", mock_run_typed),
        ):
            asyncio.run(classify_intent("Fix the thing"))
        call_args = mock_run_typed.call_args
        assert call_args[0][1] is IntentClassification

    def test_cache_hit_skips_second_run_typed_call(self):
        """Identical input on a warm cache must not re-invoke run_typed."""
        mock_run_typed = AsyncMock(
            return_value=IntentClassification(
                intent="teammate", confidence=0.97, reasoning="cached"
            )
        )
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="test-key"),
            patch("agent.llm.run_typed", mock_run_typed),
        ):
            first = asyncio.run(classify_intent("How does the bridge work?"))
            second = asyncio.run(classify_intent("How does the bridge work?"))
        assert first.intent == second.intent == "teammate"
        mock_run_typed.assert_called_once()


class TestIntentClassification:
    """IntentClassification (#1925) mirrors IntentResult's fields exactly so
    model_dump() round-trips through IntentResult(**cached_dict) with no
    translation layer -- see the dataclasses.asdict -> model_dump() note in
    agent/intent_classifier.py."""

    def test_model_dump_matches_intent_result_fields(self):
        classification = IntentClassification(
            intent="work", confidence=0.87, reasoning="fix requested"
        )
        dumped = classification.model_dump()
        assert set(dumped.keys()) == {"intent", "confidence", "reasoning"}
        # Round-trips cleanly into the dataclass the public API returns.
        result = IntentResult(**dumped)
        assert result.intent == "work"
        assert result.confidence == 0.87
        assert result.is_work is True

    def test_rejects_out_of_vocabulary_intent(self):
        with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError
            IntentClassification(intent="maybe", confidence=0.5, reasoning="unsure")
