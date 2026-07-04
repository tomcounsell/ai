"""Reactions placed on a user's message must never be hostile.

These tests lock the policy introduced for issue #1882:

- ``REACTION_ERROR`` is *pinned* to 🤔 (U+1F914) — a fixed, deterministic,
  non-hostile emoji resolved without any semantic draw. It stays 🤔 even when the
  embeddings API is unavailable (``OPENROUTER_API_KEY`` unset), because the pinned
  path never calls ``find_best_emoji``.
- ``BLOCKED_REACTION_EMOJIS`` is the single source of truth for "never aim
  hostility at a user." Hostile faces are filtered out of ``find_best_emoji``
  candidates, so no semantically-resolved reaction (success/complete) can ever draw
  one — even when a hostile face would be the top-scoring candidate. The hostile
  faces stay in ``VALIDATED_REACTIONS`` (they are valid Telegram reactions); they
  are simply unselectable by the resolver.
"""

from unittest.mock import patch

import pytest

# Locked hostile deny-list (issue #1882 Decision 2).
HOSTILE = {
    "\U0001f595",  # 🖕 middle finger
    "\U0001f44e",  # 👎 thumbs down
    "\U0001f92c",  # 🤬 face with symbols on mouth
    "\U0001f621",  # 😡 pouting face
    "\U0001f92e",  # 🤮 face vomiting
    "\U0001f631",  # 😱 face screaming in fear
}

# The issue mandates these four faces be blocked outright, plus the pre-existing 🖕.
HARD_REQUIRED = {"\U0001f44e", "\U0001f92c", "\U0001f621", "\U0001f92e"}  # 👎 🤬 😡 🤮
MIDDLE_FINGER = "\U0001f595"  # 🖕

PINNED_ERROR = "\U0001f914"  # 🤔


def _clear_terminal_cache():
    """Force fresh lazy resolution of the terminal reaction constants."""
    from agent import constants

    constants._TERMINAL_EMOJI_CACHE.clear()


class TestErrorReactionPinned:
    """REACTION_ERROR is a fixed, non-hostile 🤔 — never a semantic lottery."""

    def test_error_reaction_is_thinking_face(self):
        _clear_terminal_cache()
        from agent.constants import REACTION_ERROR

        assert REACTION_ERROR.emoji == PINNED_ERROR

    def test_error_reaction_stable_across_repeated_access(self):
        _clear_terminal_cache()
        import agent.constants as constants

        first = constants.REACTION_ERROR
        second = constants.REACTION_ERROR

        assert first.emoji == PINNED_ERROR
        assert second.emoji == PINNED_ERROR
        # Cached identity: same EmojiResult object on repeat access.
        assert first is second

    def test_error_reaction_stable_without_api_key(self):
        """The pinned path must not depend on the embeddings API.

        With OPENROUTER_API_KEY unset the semantic resolver would degrade, but the
        pinned error reaction must still yield 🤔 (never the old 😢 fallback).
        """
        _clear_terminal_cache()
        # OPENROUTER_API_KEY absent AND find_best_emoji rigged to explode: if the
        # pinned path ever reached the semantic resolver the AssertionError would
        # surface instead of the test silently passing.
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                side_effect=AssertionError("pinned path must not call find_best_emoji"),
            ),
        ):
            from agent.constants import REACTION_ERROR

            assert REACTION_ERROR.emoji == PINNED_ERROR

    def test_error_reaction_not_hostile(self):
        _clear_terminal_cache()
        from agent.constants import REACTION_ERROR
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        assert REACTION_ERROR.emoji not in BLOCKED_REACTION_EMOJIS

    def test_error_reaction_is_a_validated_reaction(self):
        _clear_terminal_cache()
        from agent.constants import REACTION_ERROR
        from bridge.response import VALIDATED_REACTIONS
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        assert REACTION_ERROR.emoji in VALIDATED_REACTIONS
        assert REACTION_ERROR.emoji not in BLOCKED_REACTION_EMOJIS


class TestHostileDenyList:
    """BLOCKED_REACTION_EMOJIS is the locked hostile set."""

    def test_exact_membership(self):
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        assert BLOCKED_REACTION_EMOJIS == frozenset(HOSTILE)

    def test_hard_required_faces_and_middle_finger_blocked(self):
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        for emoji in HARD_REQUIRED:
            assert emoji in BLOCKED_REACTION_EMOJIS, f"{emoji!r} must be blocked"
        assert MIDDLE_FINGER in BLOCKED_REACTION_EMOJIS

    def test_empathetic_sadness_stays_selectable(self):
        """Self-directed sadness/worry is not hostility and stays selectable."""
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        for emoji in ("\U0001f622", "\U0001f62d", "\U0001f628"):  # 😢 😭 😨
            assert emoji not in BLOCKED_REACTION_EMOJIS


class TestFindBestEmojiNeverHostile:
    """find_best_emoji filters hostile faces out of candidate scoring."""

    def test_hostile_top_candidate_is_skipped(self):
        """Even when a hostile face is the nearest vector, it is never returned.

        Injects a stale cache where 😡 is the closest embedding to the query and
        asserts the resolver skips it (the block filter runs before scoring wins).
        """
        from tools.emoji_embedding import EmojiResult, clear_cache, find_best_emoji

        clear_cache()
        hostile = "\U0001f621"  # 😡 — would be the top candidate
        fake_cache = {
            hostile: [1.0, 0.0, 0.0],  # nearest to the query below
            "\U0001f44d": [0.0, 1.0, 0.0],  # 👍 thumbs up (safe fallback)
        }
        with (
            patch("tools.emoji_embedding._embedding_cache", fake_cache),
            patch("tools.emoji_embedding._custom_embedding_cache", {}),
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
            patch(
                "tools.emoji_embedding._compute_embedding",
                return_value=[0.95, 0.05, 0.0],  # closest to the 😡 vector
            ),
        ):
            result = find_best_emoji("furious angry")
            assert isinstance(result, EmojiResult)
            assert result.emoji != hostile
            assert result.emoji not in HOSTILE

        clear_cache()


class TestNoTerminalConstantIsHostile:
    """No terminal reaction constant can resolve to a hostile emoji."""

    @pytest.mark.parametrize("name", ["REACTION_ERROR", "REACTION_SUCCESS", "REACTION_COMPLETE"])
    def test_terminal_constant_not_hostile(self, name):
        _clear_terminal_cache()
        import agent.constants as constants
        from tools.emoji_embedding import BLOCKED_REACTION_EMOJIS

        result = getattr(constants, name)
        assert result.emoji not in BLOCKED_REACTION_EMOJIS
