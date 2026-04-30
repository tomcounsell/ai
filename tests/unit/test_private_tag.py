"""Unit tests for agent.private_tag.strip_private.

Covers:
- Single tag, multiple tags, no tags (no-op).
- Unmatched opener (literal pass-through).
- Whitespace cleanup ONLY when at least one tag was actually stripped
  (the C2 fix from sdlc-1179 critique cycle 1).
- Empty / None input (defensive guard).
- Idempotency.
- Performance bound on a 10KB adversarial input (Risk 2).
- DEBUG log emission on full-strip-to-empty (sdlc-1179 N3).
"""

from __future__ import annotations

import logging
import time

from agent.private_tag import strip_private


class TestSingleTag:
    def test_strips_single_tag(self) -> None:
        out = strip_private("the key is <private>sk-abc123</private>, why?")
        # The regex strip leaves two spaces between "is" and ","; the
        # multi-space collapse runs ONLY because at least one tag was
        # stripped, leaving a single space.
        assert out == "the key is , why?"

    def test_inline_tag_no_surrounding_space(self) -> None:
        out = strip_private("prefix<private>secret</private>suffix")
        assert out == "prefixsuffix"

    def test_tag_at_start(self) -> None:
        out = strip_private("<private>x</private>tail")
        assert out == "tail"

    def test_tag_at_end(self) -> None:
        out = strip_private("head<private>x</private>")
        assert out == "head"


class TestMultipleTags:
    def test_two_tags_in_one_message(self) -> None:
        out = strip_private("a <private>x</private> b <private>y</private> c")
        assert out == "a b c"

    def test_three_tags(self) -> None:
        out = strip_private("<private>1</private><private>2</private><private>3</private>")
        assert out == ""

    def test_non_greedy_does_not_collapse_separate_tags(self) -> None:
        # Verify <private>A</private> ... <private>B</private> doesn't
        # match across the gap (non-greedy regex behavior).
        out = strip_private("<private>A</private> KEEP <private>B</private>")
        assert "KEEP" in out
        assert "A" not in out
        assert "B" not in out


class TestNoTagInput:
    """C2 fix: no-tag input must be returned bit-identically (no whitespace collapse)."""

    def test_no_tag_input_is_unchanged_simple(self) -> None:
        assert strip_private("hello world") == "hello world"

    def test_no_tag_input_is_unchanged_double_space(self) -> None:
        # Pre-existing multi-space sequences must survive.
        text = "hello    world"
        assert strip_private(text) == text

    def test_no_tag_input_is_unchanged_with_tabs(self) -> None:
        text = "col1\t\tcol2\t\tcol3"
        assert strip_private(text) == text

    def test_no_tag_input_is_unchanged_with_newlines(self) -> None:
        text = "line1\n\nline2\n\n\nline3"
        assert strip_private(text) == text

    def test_no_tag_input_is_unchanged_mixed_whitespace(self) -> None:
        text = "a  b\t\tc\n\nd    e"
        assert strip_private(text) == text


class TestUnmatchedOpener:
    """An opening <private> with no closing </private> is left as literal text."""

    def test_unmatched_opener_alone(self) -> None:
        text = "<private>open without close"
        assert strip_private(text) == text

    def test_unmatched_opener_then_unrelated_close(self) -> None:
        text = "<private>open </other>"
        assert strip_private(text) == text


class TestEmptyAndNoneInput:
    def test_empty_string(self) -> None:
        assert strip_private("") == ""

    def test_none_input(self) -> None:
        # type: ignore[arg-type]
        assert strip_private(None) == ""  # type: ignore[arg-type]

    def test_whitespace_only(self) -> None:
        # Whitespace-only is not a tag; pass through unchanged.
        assert strip_private("   ") == "   "

    def test_non_string_input(self) -> None:
        # Defensive: a non-string non-None falls into the `not text` branch
        # for falsy values (0, []), which return "". For a truthy non-string
        # like `123`, isinstance check returns the empty string.
        assert strip_private(123) == ""  # type: ignore[arg-type]


class TestEmptyAfterStripping:
    """Wrapping the entire content yields empty output."""

    def test_full_content_wrapped(self) -> None:
        assert strip_private("<private>everything</private>") == ""

    def test_full_content_wrapped_with_padding(self) -> None:
        # Strip leaves "  " between the (now-gone) tag and trailing whitespace,
        # collapsed to single space. .strip() is the caller's job.
        out = strip_private("  <private>everything</private>  ")
        # Whitespace-only output (multi-space collapsed once tags were stripped).
        assert out.strip() == ""


class TestIdempotency:
    def test_idempotent_simple(self) -> None:
        x = strip_private("a <private>b</private> c")
        assert strip_private(x) == x

    def test_idempotent_no_tag(self) -> None:
        x = "no tags here at all"
        assert strip_private(strip_private(x)) == x


class TestNewlinePreservation:
    def test_newlines_around_tag_preserved(self) -> None:
        out = strip_private("line1\n<private>secret</private>\nline2")
        assert "line1" in out and "line2" in out
        assert "secret" not in out
        # newlines must not be touched.
        assert "\n" in out

    def test_double_newlines_preserved(self) -> None:
        text = "para1\n\npara2"
        # No tag -> bit-identical
        assert strip_private(text) == text


class TestMultilineTagBody:
    def test_multiline_body_stripped(self) -> None:
        # re.DOTALL allows the .*? to span newlines.
        text = "before\n<private>line1\nline2\nline3</private>\nafter"
        out = strip_private(text)
        assert "line1" not in out
        assert "line2" not in out
        assert "line3" not in out
        assert "before" in out
        assert "after" in out


class TestPerformance:
    """Risk 2 mitigation: regex must complete in <50ms on 10KB adversarial input."""

    def test_large_input_with_many_tags(self) -> None:
        # 100 tags with ~100 chars of body each -> ~10KB
        body = "x" * 100
        chunk = f"prefix <private>{body}</private> suffix "
        text = chunk * 100  # ~25KB
        start = time.perf_counter()
        out = strip_private(text)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert "x" * 100 not in out
        # Generous bound for slow CI; the regex itself runs in well under 5ms.
        assert elapsed_ms < 50, f"strip_private took {elapsed_ms:.2f}ms (>50ms bound)"


class TestStripToEmptyLogsDebug:
    """N3: emit a DEBUG log when content was reduced to empty / whitespace-only.

    Operationally, this gives users a grep target for diagnosing dropped
    messages: ``grep private_tag.strip_to_empty logs/...``.
    """

    def test_strip_to_empty_logs_debug(self, caplog) -> None:
        with caplog.at_level(logging.DEBUG, logger="agent.private_tag"):
            out = strip_private("<private>everything</private>")
        assert out == ""
        assert any("private_tag.strip_to_empty" in r.message for r in caplog.records), (
            f"Expected private_tag.strip_to_empty in DEBUG logs; got: "
            f"{[r.message for r in caplog.records]}"
        )

    def test_partial_strip_does_not_log_strip_to_empty(self, caplog) -> None:
        with caplog.at_level(logging.DEBUG, logger="agent.private_tag"):
            out = strip_private("hello <private>secret</private> world")
        assert "secret" not in out
        # Only logs when the post-strip text is whitespace-only.
        assert not any("private_tag.strip_to_empty" in r.message for r in caplog.records)

    def test_no_tag_input_does_not_log(self, caplog) -> None:
        with caplog.at_level(logging.DEBUG, logger="agent.private_tag"):
            strip_private("nothing private here")
        assert not any("private_tag" in r.message for r in caplog.records)
