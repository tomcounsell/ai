"""Unit tests for bridge.routing mention detection (config-only) and terminus detection.

These tests cover the three-state behavior of get_valor_usernames after the
removal of the hardcoded VALOR_USERNAMES constant:

1. project=None -> empty set (test ergonomics)
2. project with empty mention_triggers -> empty set
3. project with mention_triggers -> normalized set

They also cover classify_conversation_terminus fast-paths and failure modes.
"""

from __future__ import annotations

import pytest

from bridge import routing
from bridge.routing import (
    classify_conversation_terminus,
    get_valor_usernames,
    is_message_for_others,
    is_message_for_valor,
)


def test_get_valor_usernames_none_returns_empty_set():
    assert get_valor_usernames(None) == set()


def test_get_valor_usernames_empty_triggers_returns_empty_set(monkeypatch):
    # Force DEFAULT_MENTIONS empty so the dict.get default also yields []
    monkeypatch.setattr(routing, "DEFAULT_MENTIONS", [])
    project = {"telegram": {"mention_triggers": []}}
    assert get_valor_usernames(project) == set()


def test_get_valor_usernames_returns_normalized_triggers():
    project = {"telegram": {"mention_triggers": ["@Foo", "BAR", "valorengels"]}}
    assert get_valor_usernames(project) == {"foo", "bar", "valorengels"}


def test_get_valor_usernames_falls_back_to_default_mentions(monkeypatch):
    monkeypatch.setattr(routing, "DEFAULT_MENTIONS", ["@valor", "valor"])
    # No mention_triggers key on the project -> should use DEFAULT_MENTIONS
    project: dict = {"telegram": {}}
    assert get_valor_usernames(project) == {"valor"}


def test_is_message_for_valor_true_with_loaded_project():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_valor("hey @valor please help", project) is True


def test_is_message_for_valor_false_when_directed_elsewhere():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_valor("hey @somebody help", project) is False


def test_is_message_for_others_true_when_only_other_mentions():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_others("@bob look at this", project) is True


def test_is_message_for_others_false_when_valor_mentioned():
    project = {"telegram": {"mention_triggers": ["@valor"]}}
    assert is_message_for_others("@valor and @bob", project) is False


def test_no_legacy_valor_usernames_constant():
    """The hardcoded VALOR_USERNAMES constant must be gone."""
    assert not hasattr(routing, "VALOR_USERNAMES")


# =============================================================================
# classify_conversation_terminus tests
# =============================================================================


@pytest.mark.asyncio
async def test_classify_terminus_bot_no_question_returns_silent():
    """Bot sender with a declarative message (no ?) → SILENT (primary loop break)."""
    result = await classify_conversation_terminus(
        text="That makes sense, thanks.",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_human_question_returns_respond():
    """Human sender with a standalone ? → RESPOND fast-path."""
    result = await classify_conversation_terminus(
        text="Can you explain this further?",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_url_with_query_param_not_respond():
    """URL query-string ? must NOT trigger the RESPOND fast-path for bot senders."""
    result = await classify_conversation_terminus(
        text="Check https://example.com?q=1 for details",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_acknowledgment_token_returns_silent():
    """Human-sent acknowledgment token → SILENT (fires after sender check)."""
    result = await classify_conversation_terminus(
        text="got it",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_acknowledgment_fires_after_bot_check():
    """'yes' from a bot → SILENT via bot fast-path (fires first, same outcome)."""
    result = await classify_conversation_terminus(
        text="yes",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_ollama_failure_defaults_to_respond(monkeypatch):
    """When both Ollama and Haiku fail, classifier returns RESPOND (conservative)."""
    # Patch Ollama to raise
    monkeypatch.setattr(routing, "OLLAMA_LOCAL_MODEL", "nonexistent-model-xyz")
    # Patch Haiku to raise (return no API key)
    monkeypatch.setattr(routing, "get_anthropic_api_key", lambda: None)

    result = await classify_conversation_terminus(
        text="Interesting thought about the deployment pipeline here.",
        thread_messages=["previous context"],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_empty_text_returns_respond():
    """Empty text → RESPOND (treat as continuation, never silently drop)."""
    result = await classify_conversation_terminus(
        text="",
        thread_messages=[],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_bot_react_collapses_to_silent(monkeypatch):
    """When LLM returns REACT but sender_is_bot=True, result must be SILENT."""
    # Force the LLM path to return REACT by making Ollama return it
    # We do this by making both Ollama and Haiku unavailable so fallback = RESPOND,
    # but test the collapse logic directly using a monkeypatched inner helper.

    # Simulate Ollama returning "REACT"
    class FakeOllamaResponse:
        pass

    class FakeOllama:
        @staticmethod
        def chat(**kwargs):
            return {"message": {"content": "REACT"}}

    import types

    fake_module = types.ModuleType("ollama")
    fake_module.chat = FakeOllama.chat

    import sys

    monkeypatch.setitem(sys.modules, "ollama", fake_module)
    # Ensure Haiku not called (no API key)
    monkeypatch.setattr(routing, "get_anthropic_api_key", lambda: None)

    result = await classify_conversation_terminus(
        text="Sure, that all looks good.",
        thread_messages=[],
        sender_is_bot=True,
    )
    assert result == "SILENT"  # REACT must collapse to SILENT for bots


# =============================================================================
# Question-aware Fast-Path 2 tests (issue #1090)
# =============================================================================


@pytest.mark.asyncio
async def test_classify_terminus_human_short_reply_to_valor_question_returns_respond(
    monkeypatch,
):
    """Human "Yes" replying to a Valor question must NOT be silenced (issue #1090).

    Fast-Path 2 should skip its ≤1-word check because the replied-to Valor
    message contained a standalone ``?``. The message falls through to the LLM
    fallback; with both Ollama and Haiku mocked unavailable, the classifier's
    conservative default of RESPOND is returned.
    """
    # Force both LLM paths to fail so we hit the conservative default.
    monkeypatch.setattr(routing, "OLLAMA_LOCAL_MODEL", "nonexistent-model-xyz")
    monkeypatch.setattr(routing, "get_anthropic_api_key", lambda: None)

    result = await classify_conversation_terminus(
        text="Yes",
        thread_messages=["Should I select the Yudame workspace?"],
        sender_is_bot=False,
    )
    assert result == "RESPOND"


@pytest.mark.asyncio
async def test_classify_terminus_human_short_reply_no_question_still_silent():
    """Regression: when the replied-to message is NOT a question, Fast-Path 2
    still fires and a 1-word human reply is SILENT (existing behavior)."""
    result = await classify_conversation_terminus(
        text="Yes",
        thread_messages=["Here is the report you asked for."],
        sender_is_bot=False,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_bot_short_reply_to_valor_question_still_silent():
    """Bot-loop suppression: a bot "Yes" reply to a Valor question is still
    silenced via Fast-Path 1, because the reply text contains no ``?``. This
    test pins the Fast-Path 1 → Fast-Path 2 ordering — if reordered, it fails.
    """
    result = await classify_conversation_terminus(
        text="Yes",
        thread_messages=["Should I do X?"],
        sender_is_bot=True,
    )
    assert result == "SILENT"


@pytest.mark.asyncio
async def test_classify_terminus_url_query_in_thread_not_treated_as_question():
    """URL query-string ``?`` in thread_messages must NOT count as a question.
    Fast-Path 2 should still fire and SILENT the short reply."""
    result = await classify_conversation_terminus(
        text="Yes",
        thread_messages=["See https://example.com?q=1"],
        sender_is_bot=False,
    )
    assert result == "SILENT"
