"""Tests for tools/emoji_embedding.py -- emoji embedding index.

Tests label completeness, fallback behavior, cache round-trip, and
embedding-based emoji selection.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


class TestEmojiLabels:
    """Test emoji label completeness and consistency."""

    def test_all_validated_reactions_have_labels(self):
        """Every one of the 73 validated Telegram reactions should have a label."""
        from bridge.response import VALIDATED_REACTIONS
        from tools.emoji_embedding import EMOJI_LABELS

        missing = [e for e in VALIDATED_REACTIONS if e not in EMOJI_LABELS]
        assert not missing, f"Missing labels for validated emojis: {missing}"

    def test_label_count_matches_validated(self):
        """Label count should match the 73 validated reactions exactly."""
        from bridge.response import VALIDATED_REACTIONS
        from tools.emoji_embedding import EMOJI_LABELS

        assert len(EMOJI_LABELS) == len(VALIDATED_REACTIONS)

    def test_no_extra_labels(self):
        """Labels should not contain emojis outside the validated set."""
        from bridge.response import VALIDATED_REACTIONS
        from tools.emoji_embedding import EMOJI_LABELS

        extras = [e for e in EMOJI_LABELS if e not in VALIDATED_REACTIONS]
        assert not extras, f"Extra emojis not in VALIDATED_REACTIONS: {extras}"

    def test_labels_are_nonempty_strings(self):
        """Every label should be a non-empty string."""
        from tools.emoji_embedding import EMOJI_LABELS

        for emoji, label in EMOJI_LABELS.items():
            assert isinstance(label, str), f"Label for {emoji} is not a string"
            assert label.strip(), f"Label for {emoji} is empty"


class TestFallbackBehavior:
    """Test fallback to default emoji on bad input."""

    def test_empty_string_returns_default(self):
        """find_best_emoji('') should return EmojiResult with default emoji."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, find_best_emoji

        result = find_best_emoji("")
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI

    def test_none_returns_default(self):
        """find_best_emoji(None) should return EmojiResult with default emoji."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, find_best_emoji

        result = find_best_emoji(None)
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI

    def test_whitespace_returns_default(self):
        """find_best_emoji('   ') should return EmojiResult with default emoji."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, find_best_emoji

        result = find_best_emoji("   ")
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI

    def test_message_empty_returns_default(self):
        """find_best_emoji_for_message('') should return EmojiResult with default."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, find_best_emoji_for_message

        result = find_best_emoji_for_message("")
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI

    def test_message_none_returns_default(self):
        """find_best_emoji_for_message(None) should return EmojiResult with default."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, find_best_emoji_for_message

        result = find_best_emoji_for_message(None)
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI


class TestEmbeddingApiFallback:
    """Test fallback when embedding API is unavailable."""

    def test_no_api_key_returns_default(self):
        """Should return EmojiResult with default when OPENROUTER_API_KEY is not set."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, clear_cache, find_best_emoji

        clear_cache()
        env = {}  # No OPENROUTER_API_KEY
        with patch.dict(os.environ, env, clear=True):
            result = find_best_emoji("excited")
            assert isinstance(result, EmojiResult)
            assert str(result) == DEFAULT_EMOJI

    def test_api_failure_returns_default(self):
        """Should return EmojiResult with default when embedding API call fails."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, clear_cache, find_best_emoji

        clear_cache()
        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.knowledge_search._compute_embedding",
                return_value=None,
            ),
        ):
            result = find_best_emoji("excited")
            assert isinstance(result, EmojiResult)
            assert str(result) == DEFAULT_EMOJI


class TestCacheRoundTrip:
    """Test cache save and load."""

    def test_cache_saves_and_loads(self):
        """Embeddings should be saveable to disk and loadable on next init."""
        from tools.emoji_embedding import CACHE_PATH, clear_cache

        clear_cache()

        # Create a fake cache
        fake_embeddings = {
            "\U0001f525": [0.1, 0.2, 0.3],  # fire
            "\U0001f44d": [0.4, 0.5, 0.6],  # thumbs up
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir=CACHE_PATH.parent
        ) as f:
            json.dump(fake_embeddings, f)
            tmp_cache = Path(f.name)

        try:
            # Patch CACHE_PATH to use temp file
            with patch("tools.emoji_embedding.CACHE_PATH", tmp_cache):
                clear_cache()
                from tools.emoji_embedding import _load_or_compute_embeddings

                loaded = _load_or_compute_embeddings()
                assert len(loaded) == 2
                assert "\U0001f525" in loaded
                assert loaded["\U0001f525"] == [0.1, 0.2, 0.3]
        finally:
            tmp_cache.unlink(missing_ok=True)
            clear_cache()

    def test_corrupted_cache_triggers_rebuild(self):
        """Corrupted cache file should trigger a rebuild attempt."""
        from tools.emoji_embedding import CACHE_PATH, clear_cache

        clear_cache()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir=CACHE_PATH.parent
        ) as f:
            f.write("not valid json {{{")
            tmp_cache = Path(f.name)

        try:
            with (
                patch("tools.emoji_embedding.CACHE_PATH", tmp_cache),
                patch.dict(os.environ, {}, clear=True),  # No API key = empty result
            ):
                clear_cache()
                from tools.emoji_embedding import _load_or_compute_embeddings

                result = _load_or_compute_embeddings()
                # Without API key, should return empty dict (rebuild failed)
                assert result == {}
        finally:
            tmp_cache.unlink(missing_ok=True)
            clear_cache()


class TestEmojiSelection:
    """Test emoji selection with mocked embeddings."""

    def test_selects_nearest_emoji(self):
        """Should select the emoji whose label embedding is nearest."""
        from tools.emoji_embedding import EmojiResult, clear_cache, find_best_emoji

        clear_cache()

        # Mock: pre-load cache with known embeddings
        fake_cache = {
            "\U0001f525": [1.0, 0.0, 0.0],  # fire
            "\U0001f622": [0.0, 1.0, 0.0],  # crying
            "\U0001f44d": [0.0, 0.0, 1.0],  # thumbs up
        }

        with (
            patch("tools.emoji_embedding._embedding_cache", fake_cache),
            patch("tools.emoji_embedding._custom_embedding_cache", {}),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.knowledge_search._compute_embedding",
                return_value=[0.9, 0.1, 0.0],  # Close to fire
            ),
        ):
            result = find_best_emoji("awesome")
            assert isinstance(result, EmojiResult)
            assert str(result) == "\U0001f525"

        clear_cache()

    def test_message_function_uses_snippet(self):
        """find_best_emoji_for_message should work with message text."""
        from tools.emoji_embedding import EmojiResult, clear_cache, find_best_emoji_for_message

        clear_cache()

        fake_cache = {
            "\U0001f44d": [1.0, 0.0],
        }

        with (
            patch("tools.emoji_embedding._embedding_cache", fake_cache),
            patch("tools.emoji_embedding._custom_embedding_cache", {}),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.knowledge_search._compute_embedding",
                return_value=[0.9, 0.1],
            ),
        ):
            result = find_best_emoji_for_message("This is a great message about programming")
            assert isinstance(result, EmojiResult)
            assert str(result) == "\U0001f44d"

        clear_cache()
