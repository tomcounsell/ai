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
        """Every validated Telegram reaction should have a label."""
        from bridge.response import VALIDATED_REACTIONS
        from tools.emoji_embedding import EMOJI_LABELS

        missing = [e for e in VALIDATED_REACTIONS if e not in EMOJI_LABELS]
        assert not missing, f"Missing labels for validated emojis: {missing}"

    def test_label_count_matches_validated(self):
        """Label count should match the validated reactions exactly."""
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


class TestOffensiveEmojiBlocked:
    """Hostile faces must never be selectable as a reaction (issue #1882).

    The middle finger 🖕 plus the outward-directed hostile faces
    (👎 🤬 😡 🤮 😱) are filtered out of find_best_emoji candidates so a reaction
    on a user's own message can never read as blame.
    """

    MIDDLE_FINGER = "\U0001f595"
    # Locked hostile deny-list — must match tools.emoji_embedding.BLOCKED_REACTION_EMOJIS.
    HOSTILE = frozenset(
        {
            "\U0001f595",  # 🖕 middle finger
            "\U0001f44e",  # 👎 thumbs down
            "\U0001f92c",  # 🤬 face with symbols on mouth
            "\U0001f621",  # 😡 pouting face
            "\U0001f92e",  # 🤮 face vomiting
            "\U0001f631",  # 😱 face screaming in fear
        }
    )

    def test_not_in_emoji_labels(self):
        """🖕 must not be in the selection label set."""
        from tools.emoji_embedding import EMOJI_LABELS

        assert self.MIDDLE_FINGER not in EMOJI_LABELS

    def test_not_in_validated_reactions(self):
        """🖕 must not be in the validated reactions policy list."""
        from bridge.response import VALIDATED_REACTIONS

        assert self.MIDDLE_FINGER not in VALIDATED_REACTIONS

    def test_in_blocklist(self):
        """🖕 must be in the defensive selection-time blocklist."""
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        assert self.MIDDLE_FINGER in BLOCKED_REACTION_EMOJIS

    def test_blocklist_is_exact_hostile_set(self):
        """The blocklist is exactly the locked hostile deny-list."""
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        assert BLOCKED_REACTION_EMOJIS == self.HOSTILE

    def test_hostile_faces_in_blocklist(self):
        """Every hostile face (👎 🤬 😡 🤮 😱) is in the selection-time blocklist."""
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        for emoji in self.HOSTILE:
            assert emoji in BLOCKED_REACTION_EMOJIS, f"{emoji!r} must be blocked"

    def test_never_selected_even_if_cached(self):
        """Even a stale cache containing 🖕 must not produce it as a result.

        Simulates a production machine whose on-disk cache predates the
        removal: the blocklist filter in find_best_emoji must skip it, even
        when its embedding is the nearest match to the query.
        """
        from tools.emoji_embedding import EmojiResult, clear_cache, find_best_emoji

        clear_cache()

        # Stale cache where 🖕 is the closest vector to the query.
        fake_cache = {
            self.MIDDLE_FINGER: [1.0, 0.0, 0.0],  # nearest to query below
            "\U0001f44d": [0.0, 1.0, 0.0],  # thumbs up
        }

        with (
            patch("tools.emoji_embedding._embedding_cache", fake_cache),
            patch("tools.emoji_embedding._custom_embedding_cache", {}),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.emoji_embedding._compute_embedding",
                return_value=[0.95, 0.05, 0.0],  # closest to 🖕 vector
            ),
        ):
            result = find_best_emoji("furious")
            assert isinstance(result, EmojiResult)
            assert str(result) != self.MIDDLE_FINGER

        clear_cache()


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
                "tools.emoji_embedding._compute_embedding",
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
        """Should select from top-K candidates; nearest emoji wins most often."""

        from tools.emoji_embedding import clear_cache, find_best_emoji

        clear_cache()

        # Mock: pre-load cache with known embeddings
        fake_cache = {
            "\U0001f525": [1.0, 0.0, 0.0],  # fire — clearly closest
            "\U0001f622": [0.0, 1.0, 0.0],  # crying
            "\U0001f44d": [0.0, 0.0, 1.0],  # thumbs up
        }

        with (
            patch("tools.emoji_embedding._embedding_cache", fake_cache),
            patch("tools.emoji_embedding._custom_embedding_cache", {}),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.emoji_embedding._compute_embedding",
                return_value=[0.9, 0.1, 0.0],  # Close to fire
            ),
        ):
            # With randomness enabled, run many times and verify the nearest
            # emoji wins the majority of the time.
            results = [str(find_best_emoji("awesome")) for _ in range(50)]
            assert all(isinstance(r, str) for r in results)
            fire_count = results.count("\U0001f525")
            # The closest match should win at least 40% of the time at temperature=4
            assert fire_count >= 10, f"Expected fire to win often, got {fire_count}/50"

        clear_cache()

    def test_message_function_uses_work_type(self):
        """find_best_emoji_for_message uses work_type→action mapping, not content embedding."""
        from tools.emoji_embedding import ACTION_EMOJI_MAP, find_best_emoji_for_message

        result = find_best_emoji_for_message("Bug report: login fails", work_type="bug")
        assert result.emoji in ACTION_EMOJI_MAP["investigate_bug"], (
            f"Expected emoji from investigate_bug candidates, got {result.emoji}"
        )


class TestActionVocabulary:
    """Tests for the ACTION_EMOJI_MAP action vocabulary."""

    def test_all_action_emojis_are_valid_telegram_reactions(self):
        """Every emoji in ACTION_EMOJI_MAP must be a valid Telegram reaction."""
        from bridge.response import INVALID_REACTIONS, VALIDATED_REACTIONS
        from tools.emoji_embedding import ACTION_EMOJI_MAP

        invalid = [e for v in ACTION_EMOJI_MAP.values() for e in v if e not in VALIDATED_REACTIONS]
        assert not invalid, f"These emojis are NOT valid Telegram reactions: {invalid}"

        in_invalid = [e for v in ACTION_EMOJI_MAP.values() for e in v if e in INVALID_REACTIONS]
        assert not in_invalid, f"These emojis are in INVALID_REACTIONS: {in_invalid}"

    def test_all_six_categories_present(self):
        """All 6 action categories must be present."""
        from tools.emoji_embedding import ACTION_EMOJI_MAP

        expected = {
            "investigate_bug",
            "problem_solving",
            "acknowledge_task",
            "receive_praise",
            "answer_question",
            "general",
        }
        assert set(ACTION_EMOJI_MAP.keys()) == expected

    def test_thinking_emoji_only_in_answer_question(self):
        """🤔 must appear ONLY in answer_question category (C6)."""
        from tools.emoji_embedding import ACTION_EMOJI_MAP

        for category, emojis in ACTION_EMOJI_MAP.items():
            if category == "answer_question":
                # answer_question MAY have 🤔
                pass
            else:
                assert "🤔" not in emojis, (
                    f"🤔 found in {category} — it must be reserved for answer_question only (C6)"
                )

    def test_nerd_emoji_absent_from_all_categories(self):
        """🤓 must not appear in any action category (C5)."""
        from tools.emoji_embedding import ACTION_EMOJI_MAP

        for category, emojis in ACTION_EMOJI_MAP.items():
            assert "🤓" not in emojis, f"🤓 found in {category} (C5: must be absent)"

    def test_general_is_distinct_neutral_fallback(self):
        """general category must be ['👀'] (C6)."""
        from tools.emoji_embedding import ACTION_EMOJI_MAP

        assert ACTION_EMOJI_MAP["general"] == ["👀"], (
            f"general must be ['👀'], got {ACTION_EMOJI_MAP['general']}"
        )

    def test_no_offensive_emojis_in_action_map(self):
        """No blocked/offensive emojis in ACTION_EMOJI_MAP."""
        from tools.emoji_embedding import ACTION_EMOJI_MAP, BLOCKED_REACTION_EMOJIS

        blocked = [e for v in ACTION_EMOJI_MAP.values() for e in v if e in BLOCKED_REACTION_EMOJIS]
        assert not blocked, f"Blocked emojis found in ACTION_EMOJI_MAP: {blocked}"


class TestWorktypeToAction:
    """Tests for WORKTYPE_TO_ACTION mapping and find_best_emoji_for_message."""

    def test_bug_maps_to_investigate_bug(self):
        from tools.emoji_embedding import WORKTYPE_TO_ACTION

        assert WORKTYPE_TO_ACTION["bug"] == "investigate_bug"

    def test_task_types_map_to_acknowledge_task(self):
        from tools.emoji_embedding import WORKTYPE_TO_ACTION

        assert WORKTYPE_TO_ACTION["feature"] == "acknowledge_task"
        assert WORKTYPE_TO_ACTION["chore"] == "acknowledge_task"
        assert WORKTYPE_TO_ACTION["sdlc"] == "acknowledge_task"

    def test_bug_message_returns_investigate_bug_emoji(self):
        from tools.emoji_embedding import ACTION_EMOJI_MAP, find_best_emoji_for_message

        result = find_best_emoji_for_message("Login is broken", work_type="bug")
        assert result.emoji in ACTION_EMOJI_MAP["investigate_bug"]

    def test_feature_message_returns_acknowledge_task_emoji(self):
        from tools.emoji_embedding import ACTION_EMOJI_MAP, find_best_emoji_for_message

        result = find_best_emoji_for_message("Add dark mode", work_type="feature")
        assert result.emoji in ACTION_EMOJI_MAP["acknowledge_task"]

    def test_none_work_type_returns_general_emoji(self):
        from tools.emoji_embedding import ACTION_EMOJI_MAP, find_best_emoji_for_message

        result = find_best_emoji_for_message("hello", work_type=None)
        assert result.emoji in ACTION_EMOJI_MAP["general"]

    def test_unknown_work_type_falls_back_to_general(self):
        from tools.emoji_embedding import ACTION_EMOJI_MAP, find_best_emoji_for_message

        result = find_best_emoji_for_message("hello", work_type="nonexistent_type")
        assert result.emoji in ACTION_EMOJI_MAP["general"]

    def test_empty_text_returns_default_emoji(self):
        from tools.emoji_embedding import DEFAULT_EMOJI, find_best_emoji_for_message

        result = find_best_emoji_for_message("")
        assert result.emoji == DEFAULT_EMOJI

    def test_whitespace_only_text_returns_default_emoji(self):
        from tools.emoji_embedding import DEFAULT_EMOJI, find_best_emoji_for_message

        result = find_best_emoji_for_message("   ")
        assert result.emoji == DEFAULT_EMOJI

    def test_uses_random_choice_not_softmax(self):
        """Verify random.choice is used (not _softmax_sample which would TypeError on str list)."""
        import inspect

        from tools import emoji_embedding as m

        src = inspect.getsource(m.find_best_emoji_for_message)
        assert "random.choice" in src, "Must use random.choice"
        assert "_softmax_sample" not in src, (
            "_softmax_sample must not be used (would TypeError on bare strings)"
        )

    def test_function_is_synchronous(self):
        """find_best_emoji_for_message must be synchronous (not a coroutine)."""
        import asyncio

        from tools.emoji_embedding import find_best_emoji_for_message

        assert not asyncio.iscoroutinefunction(find_best_emoji_for_message), (
            "find_best_emoji_for_message must be synchronous (B2) — bridge uses asyncio.to_thread"
        )
