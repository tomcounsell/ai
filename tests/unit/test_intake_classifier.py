"""Tests for intake message intent classification (#320).

Tests ``classify_message_intent_async``, which decides whether a message is
an interjection into an active session or a new work request. Durability
plan #2494 Task 13 moved the classifier onto the LOCAL granite model via
PydanticAI (``agent.llm.run_typed_local``); the ``acknowledgment`` class
retired with ``AgentSession.expectations``.

Unit tests monkeypatch ``run_typed_local`` (no model call). A small live
class runs against the real granite daemon when Ollama is reachable.
"""

import pytest

from agent.llm import LLMCallError
from tools.classifier import (
    INTENT_CLASSIFICATION_PROMPT,
    INTENT_CONFIDENCE_THRESHOLD,
    VALID_INTENTS,
    IntentDecision,
    classify_message_intent_async,
)


def _fake_decision(monkeypatch, decision: IntentDecision):
    async def fake_run_typed_local(prompt, output_type, **kwargs):
        return decision

    monkeypatch.setattr("agent.llm.run_typed_local", fake_run_typed_local)


def _forbid_model_call(monkeypatch):
    async def exploding(prompt, output_type, **kwargs):
        raise AssertionError("granite must not be called on this path")

    monkeypatch.setattr("agent.llm.run_typed_local", exploding)


class TestFastPathNoModelCall:
    """Messages classified without calling granite."""

    @pytest.mark.parametrize("empty", ["", "   ", None])
    async def test_empty_message_returns_new_work(self, empty, monkeypatch):
        _forbid_model_call(monkeypatch)
        result = await classify_message_intent_async(empty)
        assert result["intent"] == "new_work"
        assert result["confidence"] == 1.0

    async def test_no_session_context_returns_new_work(self, monkeypatch):
        """Without session context, there's nothing to interject into."""
        _forbid_model_call(monkeypatch)
        result = await classify_message_intent_async(
            "Actually make it blue",
            session_context="",
        )
        assert result["intent"] == "new_work"
        assert result["confidence"] == 1.0

    async def test_response_structure_always_valid(self, monkeypatch):
        _forbid_model_call(monkeypatch)
        result = await classify_message_intent_async("")
        assert result["intent"] in VALID_INTENTS
        assert "confidence" in result
        assert "reason" in result


class TestMockedClassification:
    """Verdict handling with a mocked granite call."""

    async def test_interjection_classification(self, monkeypatch):
        _fake_decision(
            monkeypatch,
            IntentDecision(intent="interjection", confidence=0.95, reason="Course correction"),
        )
        result = await classify_message_intent_async(
            "Actually make it blue instead",
            session_context="Working on UI redesign",
        )
        assert result["intent"] == "interjection"
        assert result["confidence"] == 0.95

    async def test_new_work_classification(self, monkeypatch):
        _fake_decision(
            monkeypatch,
            IntentDecision(intent="new_work", confidence=0.9, reason="Unrelated request"),
        )
        result = await classify_message_intent_async(
            "Fix the login bug",
            session_context="Working on UI redesign",
        )
        assert result["intent"] == "new_work"

    async def test_low_confidence_defaults_to_new_work(self, monkeypatch):
        low = INTENT_CONFIDENCE_THRESHOLD - 0.1
        _fake_decision(
            monkeypatch,
            IntentDecision(intent="interjection", confidence=low, reason="Maybe related"),
        )
        result = await classify_message_intent_async(
            "hmm",
            session_context="Working on UI redesign",
        )
        assert result["intent"] == "new_work"
        assert "Below confidence threshold" in result["reason"]

    async def test_confidence_exactly_at_threshold_passes(self, monkeypatch):
        _fake_decision(
            monkeypatch,
            IntentDecision(
                intent="interjection",
                confidence=INTENT_CONFIDENCE_THRESHOLD,
                reason="Clear follow-up",
            ),
        )
        result = await classify_message_intent_async(
            "also add error handling",
            session_context="Working on UI redesign",
        )
        assert result["intent"] == "interjection"

    async def test_new_work_not_affected_by_threshold(self, monkeypatch):
        _fake_decision(
            monkeypatch,
            IntentDecision(intent="new_work", confidence=0.2, reason="Uncertain"),
        )
        result = await classify_message_intent_async(
            "do a thing",
            session_context="Working on UI redesign",
        )
        assert result["intent"] == "new_work"

    async def test_invalid_confidence_fails_open_to_new_work(self, monkeypatch):
        _fake_decision(
            monkeypatch,
            IntentDecision(intent="interjection", confidence=3.5, reason="broken"),
        )
        result = await classify_message_intent_async(
            "x",
            session_context="Working on UI redesign",
        )
        assert result["intent"] == "new_work"
        assert result["confidence"] == 0.0

    async def test_model_failure_fails_open_to_new_work(self, monkeypatch):
        """Ollama unreachable → conservative default, never an exception."""

        async def unreachable(prompt, output_type, **kwargs):
            raise LLMCallError("ollama unreachable")

        monkeypatch.setattr("agent.llm.run_typed_local", unreachable)

        result = await classify_message_intent_async(
            "Actually make it blue",
            session_context="Working on UI redesign",
        )
        assert result["intent"] == "new_work"
        assert result["confidence"] == 0.0
        assert "Classification failed" in result["reason"]


class TestPromptContent:
    def test_prompt_includes_both_intents(self):
        for intent in VALID_INTENTS:
            assert intent in INTENT_CLASSIFICATION_PROMPT

    def test_prompt_includes_context_placeholders(self):
        assert "{message}" in INTENT_CLASSIFICATION_PROMPT
        assert "{session_context}" in INTENT_CLASSIFICATION_PROMPT
        assert "{session_status}" in INTENT_CLASSIFICATION_PROMPT

    def test_acknowledgment_class_is_retired(self):
        assert "acknowledgment" not in VALID_INTENTS
        assert "acknowledgment" not in INTENT_CLASSIFICATION_PROMPT


def _granite_reachable() -> bool:
    try:
        import httpx

        from config.settings import settings

        resp = httpx.get(f"{settings.models.ollama_host}/api/tags", timeout=2.0)
        return resp.status_code == 200 and "granite" in resp.text.lower()
    except Exception:
        return False


@pytest.mark.skipif(not _granite_reachable(), reason="granite/Ollama not reachable")
class TestRealGraniteClassification:
    """Live accuracy smoke against the local granite daemon."""

    async def test_clear_interjection_on_live_granite(self):
        result = await classify_message_intent_async(
            "Actually, make the button blue instead of green",
            session_context="Redesigning the settings page UI; agent asked for color preference",
            session_status="running",
        )
        assert result["intent"] in VALID_INTENTS
        assert 0.0 <= result["confidence"] <= 1.0
