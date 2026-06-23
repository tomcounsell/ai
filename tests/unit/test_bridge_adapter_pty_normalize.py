"""Unit tests for agent.granite_container.bridge_adapter._normalize_pty_buffer (#1768).

The diff-gate in `_on_pty_read` stamps `last_pty_activity_at` only when the
NORMALIZED buffer differs from the prior read. These tests pin the empirical
contract the plan's Risk 2 requires: spinner-glyph/verb, elapsed-seconds, and
cursor/blink-only deltas normalize to the SAME string (no activity stamp on a
wedged-but-animating screen), while a genuinely-new transcript line normalizes
to a DIFFERENT string (does stamp).
"""

from __future__ import annotations

from agent.granite_container.bridge_adapter import _normalize_pty_buffer

# A stable block of transcript content surrounding the animating status line.
_CONTENT = (
    "> Implementing the stall recovery ladder\n"
    "● Reading reflections/stall_advisory.py\n"
    "  Found _maybe_recover gate ladder\n"
)


class TestNormalizePtyBuffer:
    def test_spinner_frame_delta_normalizes_equal(self):
        # Same surrounding content; only the spinner glyph + verb frame differs.
        a = _CONTENT + "✻ Sprouting… (3s)\n"
        b = _CONTENT + "✶ Whirlpooling… (4s)\n"
        assert _normalize_pty_buffer(a) == _normalize_pty_buffer(b)

    def test_elapsed_seconds_delta_normalizes_equal(self):
        # Same content; only the elapsed-seconds counter differs.
        a = _CONTENT + "● Working… esc to interrupt · 12s\n"
        b = _CONTENT + "● Working… esc to interrupt · 47s\n"
        assert _normalize_pty_buffer(a) == _normalize_pty_buffer(b)

    def test_new_text_content_normalizes_different(self):
        # A genuinely new transcript line must survive normalization.
        a = _CONTENT + "✻ Sprouting… (3s)\n"
        b = _CONTENT + "● Editing config/settings.py\n" + "✻ Sprouting… (3s)\n"
        assert _normalize_pty_buffer(a) != _normalize_pty_buffer(b)

    def test_non_string_input_fails_soft(self):
        # Fail-soft: a non-string (None) must not raise. The implementation's
        # try/except returns the original input on any normalization error.
        assert _normalize_pty_buffer(None) is None
