"""Precedence ranks for the single Telegram reaction slot (#2716).

Telegram permits exactly one reaction per sender per message, so every writer
that reacts to a session's originating message competes for one slot. Seven
writers exist; this module is the single place their relative precedence is
named, so the drain point (``bridge/telegram_relay.process_outbox``) can
arbitrate without importing any of them.

**Lower number wins.** Rank 1 is terminal and final; rank 5 (the liveness tick
counter) is lowest and yields to everything.

The full writer inventory, the two paths that bypass this ranking, and the
enforcement rules live in ``docs/features/session-liveness-tick-counter.md``.

This module deliberately imports nothing: it is pulled in by the watchdog, the
relay, the output handler, the tool-budget path, and the completion runner, and
any dependency here would risk an import cycle across those four packages.
"""

from __future__ import annotations

# Rank 1 — a session's terminal reaction (success / complete / error / clear).
# Final: once a rank-1 reaction owns the slot, nothing may overwrite it.
PRIORITY_TERMINAL = 1

# Rank 2 — ingestion-time and budget-exhaustion signals (⚠ worker-down, 🤯
# tool budget). They describe a condition the human must act on.
PRIORITY_INGESTION = 2

# Rank 3 — worker pickup (✍) and read-the-room suppression.
PRIORITY_PICKUP = 3

# Rank 4 — child-session completion suppress (👀).
PRIORITY_SUPPRESS = 4

# Rank 5 — the liveness tick counter. Lowest; yields to every other writer.
PRIORITY_HEARTBEAT = 5

# Writers that predate the ``priority`` payload field pass nothing, so the
# payload builder derives a rank from the glyph. Only the low-ranked writers
# need an entry: anything unmapped defaults to PRIORITY_TERMINAL, which is the
# fail-safe direction — an unrecognized reaction wins its slot exactly as it
# does today, and only the tick ever yields.
#
# 👀 is genuinely ambiguous under this derivation (child-completion suppress at
# rank 4 AND the tick's slot-0 arc entry at rank 5), which is why the tick
# publisher passes ``priority`` explicitly instead of leaning on this map.
_GLYPH_PRIORITY: dict[str, int] = {
    "⚠": PRIORITY_INGESTION,
    "🤯": PRIORITY_INGESTION,
    "✍": PRIORITY_PICKUP,
    "👀": PRIORITY_SUPPRESS,
}

DEFAULT_PRIORITY = PRIORITY_TERMINAL


def priority_for_glyph(glyph: str | None) -> int:
    """Derive a precedence rank from a reaction glyph.

    Fallback for outbox writers that do not pass ``priority`` explicitly.
    New code with the parameter in hand must pass it rather than relying on
    this derivation.

    Args:
        glyph: The reaction emoji, or ``None`` for a clear-reaction payload.

    Returns:
        The mapped rank, or :data:`DEFAULT_PRIORITY` when unmapped.
    """
    if not glyph:
        return DEFAULT_PRIORITY
    return _GLYPH_PRIORITY.get(str(glyph), DEFAULT_PRIORITY)


def is_ranked_glyph(glyph: str | None) -> bool:
    """True when this glyph has a real rank rather than the fallback.

    ``priority_for_glyph`` deliberately answers ``DEFAULT_PRIORITY`` (terminal)
    for anything it does not recognize, so an unknown writer is never dropped.
    That fail-safe is wrong for slot OWNERSHIP: an arbitrary agent-issued glyph
    from ``tools/react_with_emoji.py`` would otherwise claim the slot at
    terminal rank and suppress every lower-ranked writer, including the counter,
    until the owner key expired. Callers recording ownership consult this
    instead, so an unranked reaction delivers without taking the slot.
    """
    if not glyph:
        return False
    return str(glyph) in _GLYPH_PRIORITY
