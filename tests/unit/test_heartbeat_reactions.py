"""Liveness tick counter constants and slot precedence (#2716).

Covers the three invariants the plan names as highest-value:

1. Every glyph the counter can emit is a legal Telegram reaction — the
   structural defense against #1313's defect class, where ⏳ shipped despite
   already being in this repo's own INVALID_REACTIONS.
2. The fallback arc registers NO entry in ``_reaction_constants()``. Stated as
   non-registration rather than glyph-disjointness on purpose: the arc leads
   with 👀, which IS ``REACTION_RECEIVED``, so a disjointness assertion would
   fail against the arc the design specifies. ``_assert_distinct()`` inspects
   only the registry dict and runs at import, so a glyph merely reused by an
   unregistered sequence cannot stop the bridge from starting — but registering
   the arc would.
3. Advancing past the ceiling raises instead of clamping.
"""

import pytest

from agent.reaction_priority import (
    DEFAULT_PRIORITY,
    PRIORITY_HEARTBEAT,
    PRIORITY_INGESTION,
    PRIORITY_PICKUP,
    PRIORITY_SUPPRESS,
    PRIORITY_TERMINAL,
    priority_for_glyph,
)
from bridge.response import (
    HEARTBEAT_FALLBACK_ARC,
    HEARTBEAT_MAX_TICKS,
    HEARTBEAT_TICK_INTERVAL_SECONDS,
    INVALID_REACTIONS,
    PREMIUM_DIGIT_REACTIONS,
    REACTION_RECEIVED,
    VALIDATED_REACTIONS,
    _reaction_constants,
    heartbeat_reaction,
)


class TestEmittableGlyphsAreLegal:
    @pytest.mark.parametrize("glyph", HEARTBEAT_FALLBACK_ARC)
    def test_arc_glyph_is_a_validated_reaction(self, glyph):
        assert glyph in VALIDATED_REACTIONS
        assert glyph not in INVALID_REACTIONS

    @pytest.mark.parametrize("tick", range(HEARTBEAT_MAX_TICKS + 1))
    def test_every_tick_emits_a_validated_glyph(self, tick):
        result = heartbeat_reaction(tick)
        assert result.emoji in VALIDATED_REACTIONS

    def test_arc_avoids_the_error_face(self):
        """REACTION_ERROR is pinned to 🤔; a healthy session must never wear it."""
        from agent.constants import REACTION_ERROR

        assert str(REACTION_ERROR) not in HEARTBEAT_FALLBACK_ARC


class TestArcNonRegistration:
    def test_arc_registers_no_constant(self):
        registry = _reaction_constants()
        assert "HEARTBEAT_FALLBACK_ARC" not in registry
        assert not any(isinstance(v, tuple) for v in registry.values())

    def test_arc_leads_with_received_and_that_is_safe(self):
        """The reuse is deliberate: slot 0 IS the acknowledgement."""
        assert HEARTBEAT_FALLBACK_ARC[0] == REACTION_RECEIVED
        # Import-time invariant still holds precisely because the arc is not
        # in the registry. This would raise ImportError if it were.
        from bridge.response import _assert_distinct

        _assert_distinct()


class TestTickGlyphs:
    def test_slot_zero_is_the_acknowledgement(self):
        assert heartbeat_reaction(0).emoji == HEARTBEAT_FALLBACK_ARC[0]

    def test_later_ticks_alternate(self):
        glyphs = [heartbeat_reaction(t).emoji for t in range(1, 6)]
        assert glyphs == [
            HEARTBEAT_FALLBACK_ARC[1],
            HEARTBEAT_FALLBACK_ARC[2],
            HEARTBEAT_FALLBACK_ARC[1],
            HEARTBEAT_FALLBACK_ARC[2],
            HEARTBEAT_FALLBACK_ARC[1],
        ]

    def test_unpinned_digits_degrade_to_standard(self):
        """No pinned document_id → a plain standard-emoji result, never a crash."""
        result = heartbeat_reaction(1)
        if 1 not in PREMIUM_DIGIT_REACTIONS:
            assert result.document_id is None
            assert result.is_custom is False

    def test_pinned_digits_are_in_range(self):
        for tick, doc_id in PREMIUM_DIGIT_REACTIONS.items():
            assert 1 <= tick <= HEARTBEAT_MAX_TICKS
            assert isinstance(doc_id, int)

    def test_pinned_digit_rides_the_custom_schema(self, monkeypatch):
        monkeypatch.setitem(PREMIUM_DIGIT_REACTIONS, 3, 12345)
        result = heartbeat_reaction(3)
        assert result.document_id == 12345
        assert result.is_custom is True
        # The standard fallback glyph is still carried, so set_reaction can
        # degrade when the pack is gone or the account loses Premium.
        assert result.emoji in VALIDATED_REACTIONS


class TestCeilingRaisesRatherThanClamps:
    def test_max_tick_is_allowed(self):
        assert heartbeat_reaction(HEARTBEAT_MAX_TICKS).emoji in VALIDATED_REACTIONS

    def test_past_ceiling_raises(self):
        with pytest.raises(ValueError, match="exceeds HEARTBEAT_MAX_TICKS"):
            heartbeat_reaction(HEARTBEAT_MAX_TICKS + 1)

    def test_far_past_ceiling_raises_not_clamps(self):
        with pytest.raises(ValueError):
            heartbeat_reaction(HEARTBEAT_MAX_TICKS + 100)

    def test_negative_tick_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            heartbeat_reaction(-1)

    def test_ceiling_window_is_one_hundred_minutes_by_default(self):
        """9 digits over 90 minutes, plus the slot-0 👀, is the 100-minute promise."""
        assert HEARTBEAT_MAX_TICKS == 9
        assert (HEARTBEAT_MAX_TICKS + 1) * HEARTBEAT_TICK_INTERVAL_SECONDS == 6000


class TestSlotPrecedence:
    def test_tick_ranks_lowest(self):
        assert PRIORITY_HEARTBEAT > PRIORITY_SUPPRESS > PRIORITY_PICKUP
        assert PRIORITY_PICKUP > PRIORITY_INGESTION > PRIORITY_TERMINAL

    @pytest.mark.parametrize(
        ("glyph", "expected"),
        [
            ("⚠", PRIORITY_INGESTION),
            ("🤯", PRIORITY_INGESTION),
            ("✍", PRIORITY_PICKUP),
            ("👀", PRIORITY_SUPPRESS),
        ],
    )
    def test_glyph_derivation(self, glyph, expected):
        assert priority_for_glyph(glyph) == expected

    def test_unmapped_and_clear_default_to_terminal(self):
        """Fail-safe direction: an unrecognized reaction wins, only the tick yields."""
        assert priority_for_glyph(None) == DEFAULT_PRIORITY == PRIORITY_TERMINAL
        assert priority_for_glyph("🦄") == PRIORITY_TERMINAL

    def test_eyes_are_ambiguous_which_is_why_the_tick_is_explicit(self):
        """👀 derives to rank 4, NOT the tick's rank 5 — hence the explicit pass."""
        assert priority_for_glyph(HEARTBEAT_FALLBACK_ARC[0]) != PRIORITY_HEARTBEAT
