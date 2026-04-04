"""Tests for custom emoji indexing in tools/emoji_embedding.py.

Tests the EmojiResult dataclass, custom emoji index building (mocked Telethon),
cache round-trip, unified search across standard and custom embeddings,
and graceful degradation when custom emoji are unavailable.
"""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


class TestEmojiResult:
    """Test EmojiResult dataclass behavior."""

    def test_str_returns_standard_emoji(self):
        """str(EmojiResult) returns the standard emoji string."""
        from tools.emoji_embedding import EmojiResult

        result = EmojiResult(emoji="\U0001f525")
        assert str(result) == "\U0001f525"

    def test_str_returns_placeholder_for_custom(self):
        """str(EmojiResult) returns placeholder when is_custom and no emoji."""
        from tools.emoji_embedding import CUSTOM_EMOJI_PLACEHOLDER, EmojiResult

        result = EmojiResult(document_id=12345, is_custom=True)
        assert str(result) == CUSTOM_EMOJI_PLACEHOLDER

    def test_str_returns_emoji_for_custom_with_fallback(self):
        """str(EmojiResult) returns emoji when custom has a fallback emoji set."""
        from tools.emoji_embedding import EmojiResult

        result = EmojiResult(emoji="\U0001f525", document_id=12345, is_custom=True)
        assert str(result) == "\U0001f525"

    def test_str_returns_default_when_empty(self):
        """str(EmojiResult) returns default thinking emoji when both fields empty."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult

        result = EmojiResult()
        assert str(result) == DEFAULT_EMOJI

    def test_display_property(self):
        """display property should match __str__."""
        from tools.emoji_embedding import EmojiResult

        result = EmojiResult(emoji="\U0001f44d")
        assert result.display == str(result)

    def test_is_custom_flag(self):
        """is_custom flag should reflect custom vs standard."""
        from tools.emoji_embedding import EmojiResult

        standard = EmojiResult(emoji="\U0001f44d")
        assert not standard.is_custom

        custom = EmojiResult(document_id=999, is_custom=True)
        assert custom.is_custom

    def test_backward_compat_string_comparison(self):
        """EmojiResult should work in string contexts via __str__."""
        from tools.emoji_embedding import EmojiResult

        result = EmojiResult(emoji="\U0001f525")
        # Common pattern: comparing result to a string
        assert str(result) == "\U0001f525"
        # Format string
        assert f"Emoji: {result}" == "Emoji: \U0001f525"


class TestCustomEmojiCache:
    """Test custom emoji cache loading and saving."""

    def test_load_custom_embeddings_from_cache(self):
        """Should load custom embeddings from cache file."""
        from tools.emoji_embedding import CUSTOM_CACHE_PATH, clear_cache

        clear_cache()

        fake_custom = {
            "custom:12345": [0.1, 0.2, 0.3],
            "custom:67890": [0.4, 0.5, 0.6],
        }

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir=CUSTOM_CACHE_PATH.parent
        ) as f:
            json.dump(fake_custom, f)
            tmp_cache = Path(f.name)

        try:
            with patch("tools.emoji_embedding.CUSTOM_CACHE_PATH", tmp_cache):
                clear_cache()
                from tools.emoji_embedding import _load_custom_embeddings

                loaded = _load_custom_embeddings()
                assert len(loaded) == 2
                assert "custom:12345" in loaded
                assert loaded["custom:12345"] == [0.1, 0.2, 0.3]
        finally:
            tmp_cache.unlink(missing_ok=True)
            clear_cache()

    def test_missing_cache_returns_empty(self):
        """Should return empty dict when no custom cache file exists."""
        from tools.emoji_embedding import clear_cache

        clear_cache()

        with patch(
            "tools.emoji_embedding.CUSTOM_CACHE_PATH",
            Path("/nonexistent/custom_emoji_embeddings.json"),
        ):
            from tools.emoji_embedding import _load_custom_embeddings

            loaded = _load_custom_embeddings()
            assert loaded == {}

        clear_cache()

    def test_disabled_returns_empty(self):
        """Should return empty dict when custom emoji is disabled."""
        from tools.emoji_embedding import clear_cache

        clear_cache()

        with patch("tools.emoji_embedding._custom_emoji_disabled", True):
            from tools.emoji_embedding import _load_custom_embeddings

            loaded = _load_custom_embeddings()
            assert loaded == {}

        clear_cache()


class TestUnifiedSearch:
    """Test unified search across standard and custom embeddings."""

    def test_standard_emoji_wins_by_default(self):
        """Standard emoji should win when custom score is not high enough."""
        from tools.emoji_embedding import EmojiResult, clear_cache, find_best_emoji

        clear_cache()

        fake_standard = {
            "\U0001f525": [1.0, 0.0, 0.0],  # fire
        }
        fake_custom = {
            "custom:999": [0.98, 0.0, 0.0],  # slightly less similar
        }

        with (
            patch("tools.emoji_embedding._embedding_cache", fake_standard),
            patch("tools.emoji_embedding._custom_embedding_cache", fake_custom),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.knowledge_search._compute_embedding",
                return_value=[0.99, 0.0, 0.0],
            ),
        ):
            result = find_best_emoji("hot")
            assert isinstance(result, EmojiResult)
            assert not result.is_custom
            assert result.emoji == "\U0001f525"

        clear_cache()

    def test_custom_emoji_wins_with_sufficient_delta(self):
        """Custom emoji should win when score exceeds standard by delta."""
        from tools.emoji_embedding import EmojiResult, clear_cache, find_best_emoji

        clear_cache()

        fake_standard = {
            "\U0001f525": [0.5, 0.5, 0.0],  # fire - moderate match
        }
        fake_custom = {
            "custom:42": [1.0, 0.0, 0.0],  # much better match
        }

        with (
            patch("tools.emoji_embedding._embedding_cache", fake_standard),
            patch("tools.emoji_embedding._custom_embedding_cache", fake_custom),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.knowledge_search._compute_embedding",
                return_value=[1.0, 0.0, 0.0],
            ),
        ):
            result = find_best_emoji("hot")
            assert isinstance(result, EmojiResult)
            assert result.is_custom
            assert result.document_id == 42
            # Should still carry the standard emoji as fallback
            assert result.emoji == "\U0001f525"

        clear_cache()

    def test_empty_custom_cache_returns_standard(self):
        """When custom cache is empty, should return standard emoji only."""
        from tools.emoji_embedding import EmojiResult, clear_cache, find_best_emoji

        clear_cache()

        fake_standard = {
            "\U0001f44d": [1.0, 0.0],  # thumbs up
        }

        with (
            patch("tools.emoji_embedding._embedding_cache", fake_standard),
            patch("tools.emoji_embedding._custom_embedding_cache", {}),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.knowledge_search._compute_embedding",
                return_value=[0.9, 0.1],
            ),
        ):
            result = find_best_emoji("good")
            assert isinstance(result, EmojiResult)
            assert not result.is_custom
            assert result.emoji == "\U0001f44d"

        clear_cache()

    def test_find_best_emoji_returns_emoji_result(self):
        """find_best_emoji should always return EmojiResult, not str."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, clear_cache, find_best_emoji

        clear_cache()

        # Empty input
        result = find_best_emoji("")
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI

        # None input
        result = find_best_emoji(None)
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI

        clear_cache()

    def test_find_best_emoji_for_message_returns_emoji_result(self):
        """find_best_emoji_for_message should return EmojiResult."""
        from tools.emoji_embedding import (
            DEFAULT_EMOJI,
            EmojiResult,
            clear_cache,
            find_best_emoji_for_message,
        )

        clear_cache()

        result = find_best_emoji_for_message("")
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI

        result = find_best_emoji_for_message(None)
        assert isinstance(result, EmojiResult)
        assert str(result) == DEFAULT_EMOJI

        clear_cache()


class TestBuildCustomEmojiIndex:
    """Test async custom emoji index building."""

    @pytest.mark.asyncio
    async def test_build_index_with_sticker_sets(self):
        """Should build embeddings from Telethon sticker set data."""
        from tools.emoji_embedding import CUSTOM_CACHE_PATH, build_custom_emoji_index, clear_cache

        clear_cache()

        # Mock sticker set data
        mock_stickerset_ref = SimpleNamespace(id=100)
        mock_attr = SimpleNamespace(alt="\U0001f389", stickerset=mock_stickerset_ref)
        mock_doc = SimpleNamespace(id=12345, attributes=[mock_attr])

        mock_set = SimpleNamespace(id=100, title="Party Emoji")
        mock_result = SimpleNamespace(sets=[mock_set], documents=[mock_doc])

        mock_client = AsyncMock()
        mock_client.return_value = mock_result

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir=CUSTOM_CACHE_PATH.parent
        ) as f:
            tmp_cache = Path(f.name)

        try:
            with (
                patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
                patch("tools.emoji_embedding.CUSTOM_CACHE_PATH", tmp_cache),
                patch(
                    "tools.knowledge_search._compute_embedding",
                    return_value=[0.1, 0.2, 0.3],
                ),
            ):
                embeddings = await build_custom_emoji_index(mock_client)

            assert "custom:12345" in embeddings
            assert embeddings["custom:12345"] == [0.1, 0.2, 0.3]

            # Verify cache file was written
            assert tmp_cache.exists()
            cached = json.loads(tmp_cache.read_text())
            assert "custom:12345" in cached
        finally:
            tmp_cache.unlink(missing_ok=True)
            clear_cache()

    @pytest.mark.asyncio
    async def test_build_index_api_failure_disables(self):
        """API failure should disable custom emoji and return empty."""
        from tools.emoji_embedding import build_custom_emoji_index, clear_cache

        clear_cache()

        mock_client = AsyncMock()
        mock_client.side_effect = Exception("Premium required")

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            embeddings = await build_custom_emoji_index(mock_client)

        assert embeddings == {}

        # Custom emoji should now be disabled
        from tools.emoji_embedding import _custom_emoji_disabled

        assert _custom_emoji_disabled is True

        clear_cache()

    @pytest.mark.asyncio
    async def test_build_index_no_api_key(self):
        """Missing API key should skip indexing."""
        from tools.emoji_embedding import build_custom_emoji_index, clear_cache

        clear_cache()

        mock_client = AsyncMock()

        with patch.dict(os.environ, {}, clear=True):
            embeddings = await build_custom_emoji_index(mock_client)

        assert embeddings == {}
        mock_client.assert_not_called()

        clear_cache()

    @pytest.mark.asyncio
    async def test_build_index_empty_sticker_sets(self):
        """Empty sticker sets should return empty dict without crash."""
        from tools.emoji_embedding import build_custom_emoji_index, clear_cache

        clear_cache()

        mock_result = SimpleNamespace(sets=[], documents=[])
        mock_client = AsyncMock()
        mock_client.return_value = mock_result

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            embeddings = await build_custom_emoji_index(mock_client)

        assert embeddings == {}

        clear_cache()

    @pytest.mark.asyncio
    async def test_rebuild_clears_existing(self):
        """rebuild_custom_emoji_index should clear disabled flag and cache."""
        from tools.emoji_embedding import (
            clear_cache,
            rebuild_custom_emoji_index,
        )

        clear_cache()

        mock_result = SimpleNamespace(sets=[], documents=[])
        mock_client = AsyncMock()
        mock_client.return_value = mock_result

        with (
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.emoji_embedding.CUSTOM_CACHE_PATH",
                Path("/tmp/test_custom_emoji_rebuild.json"),
            ),
        ):
            # Set disabled state
            import tools.emoji_embedding

            tools.emoji_embedding._custom_emoji_disabled = True

            await rebuild_custom_emoji_index(mock_client)

            # Disabled flag should be cleared
            assert tools.emoji_embedding._custom_emoji_disabled is False

        clear_cache()


class TestGracefulDegradation:
    """Test graceful degradation paths."""

    def test_standard_behavior_unchanged_without_custom(self):
        """Standard emoji path should work identically when custom index is empty."""
        from tools.emoji_embedding import EmojiResult, clear_cache, find_best_emoji

        clear_cache()

        fake_standard = {
            "\U0001f525": [1.0, 0.0, 0.0],
            "\U0001f622": [0.0, 1.0, 0.0],
        }

        with (
            patch("tools.emoji_embedding._embedding_cache", fake_standard),
            patch("tools.emoji_embedding._custom_embedding_cache", {}),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.knowledge_search._compute_embedding",
                return_value=[0.9, 0.1, 0.0],
            ),
        ):
            result = find_best_emoji("awesome")
            assert isinstance(result, EmojiResult)
            assert str(result) == "\U0001f525"
            assert not result.is_custom

        clear_cache()

    def test_no_api_key_returns_default_emoji_result(self):
        """Missing API key should return default EmojiResult."""
        from tools.emoji_embedding import DEFAULT_EMOJI, EmojiResult, clear_cache, find_best_emoji

        clear_cache()
        env = {}
        with patch.dict(os.environ, env, clear=True):
            result = find_best_emoji("excited")
            assert isinstance(result, EmojiResult)
            assert str(result) == DEFAULT_EMOJI

        clear_cache()
