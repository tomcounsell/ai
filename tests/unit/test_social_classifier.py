"""Tests for 3-way social classifier in bridge/routing.py."""

from bridge.routing import (
    _pick_reaction_emoji,
    classify_needs_response,
)


class TestClassifyNeedsResponse:
    """Test the 3-way classify_needs_response (respond/react/ignore)."""

    def test_returns_string_type(self):
        """Return value is always a string, never a bool."""
        result = classify_needs_response("ok")
        assert isinstance(result, str)
        assert result in ("respond", "react", "ignore")

    # --- ignore path ---

    def test_short_messages_ignored(self):
        assert classify_needs_response("ok") == "ignore"
        assert classify_needs_response("hi") == "ignore"
        assert classify_needs_response("k") == "ignore"

    def test_acknowledgments_ignored(self):
        assert classify_needs_response("thanks") == "ignore"
        assert classify_needs_response("got it") == "ignore"
        assert classify_needs_response("understood") == "ignore"
        assert classify_needs_response("yes") == "ignore"
        assert classify_needs_response("nope") == "ignore"

    def test_emoji_acknowledgments_ignored(self):
        assert classify_needs_response("\U0001f44d") == "ignore"  # thumbs up
        assert classify_needs_response("\U0001f44c") == "ignore"  # OK hand

    # --- react path ---

    def test_banter_gets_react(self):
        assert classify_needs_response("nice") == "react"
        assert classify_needs_response("lol") == "react"
        assert classify_needs_response("haha") == "react"
        assert classify_needs_response("awesome") == "react"
        assert classify_needs_response("cool") == "react"
        assert classify_needs_response("legit") == "react"

    def test_humor_tokens_get_react(self):
        assert classify_needs_response("lmao") == "react"
        assert classify_needs_response("rofl") == "react"
        assert classify_needs_response("heh") == "react"

    def test_case_insensitive(self):
        assert classify_needs_response("LOL") == "react"
        assert classify_needs_response("Nice!") == "react"
        assert classify_needs_response("THANKS") == "ignore"

    # --- empty / whitespace ---

    def test_empty_string_ignored(self):
        assert classify_needs_response("") == "ignore"

    def test_whitespace_only_ignored(self):
        assert classify_needs_response("   ") == "ignore"

    # --- default (would need Ollama, but we test that it doesn't crash) ---

    def test_real_question_not_fast_pathed(self):
        """Questions that need Ollama should not be caught by fast path.

        Without Ollama available in tests, this defaults to 'respond'
        (the conservative fallback).
        """
        result = classify_needs_response("How do I reset the database?")
        # Either respond (Ollama fallback) or respond (Ollama success)
        assert result == "respond"


class TestPickReactionEmoji:
    def test_humor_tokens_get_laugh(self):
        assert _pick_reaction_emoji("lol") == "\U0001f601"  # grinning face
        assert _pick_reaction_emoji("haha") == "\U0001f601"

    def test_non_humor_tokens_get_fire(self):
        assert _pick_reaction_emoji("nice") == "\U0001f525"  # fire
        assert _pick_reaction_emoji("cool") == "\U0001f525"
