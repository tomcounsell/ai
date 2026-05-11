"""Unit tests for bridge/context.py helper functions.

Covers the implicit-context heuristic (`references_prior_context`), its
companion `matched_context_patterns`, and the `_build_completed_resume_text`
layered preamble. See `docs/plans/reply_thread_context_hydration.md` —
implementation notes IN-3 and IN-12.
"""

from __future__ import annotations

import pytest

from bridge.context import (
    DEICTIC_CONTEXT_PATTERNS,
    REPLY_THREAD_CONTEXT_HEADER,
    STATUS_QUESTION_PATTERNS,
    matched_context_patterns,
    references_prior_context,
)


class TestReplyThreadContextHeader:
    """The canonical header constant must match the string used by format_reply_chain."""

    def test_header_is_stable_string(self):
        assert REPLY_THREAD_CONTEXT_HEADER == "REPLY THREAD CONTEXT"

    def test_format_reply_chain_uses_the_constant(self):
        from bridge.context import format_reply_chain

        chain = [{"sender": "Tom", "content": "hello", "message_id": 1, "date": None}]
        formatted = format_reply_chain(chain)
        # Idempotency guard in agent_session_queue relies on this substring.
        assert REPLY_THREAD_CONTEXT_HEADER in formatted
        assert formatted.count(REPLY_THREAD_CONTEXT_HEADER) == 1


class TestReferencesPriorContextNegativeGuards:
    """IN-12: locked behavior for None / empty / whitespace-only / non-string."""

    def test_none_returns_false(self):
        assert references_prior_context(None) is False

    def test_empty_string_returns_false(self):
        assert references_prior_context("") is False

    def test_whitespace_only_returns_false(self):
        assert references_prior_context("   ") is False
        assert references_prior_context("\t\n  ") is False

    def test_non_string_returns_false(self):
        assert references_prior_context(123) is False
        assert references_prior_context(["did we fix it?"]) is False
        assert references_prior_context({"text": "the bug"}) is False

    def test_does_not_raise_on_weird_input(self):
        # Must not raise for any input shape
        for weird in (0, 0.0, b"bytes", object()):
            references_prior_context(weird)  # must not raise


class TestReferencesPriorContextDeictic:
    """Deictic/back-reference phrases should trigger the directive."""

    @pytest.mark.parametrize(
        "text",
        [
            "did we get that fixed?",
            "did we ship the fix?",
            "the bug is still broken",
            "that issue is blocking release",
            "still failing in CI",
            "still broken on main",
            "we fixed the repo yesterday",
            "we shipped the change",
            "we merged the PR",
            "we resolved it",
            "last time we talked about this",
            "as I mentioned earlier",
            "as I said before",
            "what about that ticket",
            "what about the PR",
            "what about the pull request",
        ],
    )
    def test_matches_deictic_phrase(self, text):
        assert references_prior_context(text) is True, f"expected match for {text!r}"


class TestReferencesPriorContextStatusQuestions:
    """Status-question patterns are re-used unchanged — coverage sanity check."""

    @pytest.mark.parametrize(
        "text",
        [
            "what are you working on?",
            "what's the status?",
            "any updates?",
            "how's it going",
            "catch me up",
        ],
    )
    def test_status_questions_still_trigger(self, text):
        assert references_prior_context(text) is True


class TestReferencesPriorContextNegatives:
    """High-precision intent: self-contained statements must NOT trigger."""

    @pytest.mark.parametrize(
        "text",
        [
            "hello world",
            "please add logging to the auth module",
            "here is the revised plan",
            "thanks!",
            "run the tests please",
            "create a new issue about caching",
        ],
    )
    def test_self_contained_does_not_trigger(self, text):
        assert references_prior_context(text) is False


class TestMatchedContextPatterns:
    """Companion helper must expose the specific patterns that hit, for audit logs."""

    def test_returns_list(self):
        assert isinstance(matched_context_patterns("did we fix it?"), list)

    def test_empty_for_negative_input(self):
        assert matched_context_patterns(None) == []
        assert matched_context_patterns("") == []
        assert matched_context_patterns("   ") == []
        assert matched_context_patterns("hello world") == []

    def test_returns_pattern_strings_on_match(self):
        patterns = matched_context_patterns("did we fix the bug?")
        assert len(patterns) >= 1
        assert all(isinstance(p, str) for p in patterns)

    def test_multiple_matches_return_multiple_entries(self):
        # "did we" + "the bug" should both match
        patterns = matched_context_patterns("did we ship the bug fix?")
        assert len(patterns) >= 2


class TestHeuristicListSize:
    """IN-3: resist expansion -- keep pattern lists small."""

    def test_deictic_list_is_small(self):
        # Cap at ~10 per IN-3
        assert len(DEICTIC_CONTEXT_PATTERNS) <= 10

    def test_status_list_is_small(self):
        assert len(STATUS_QUESTION_PATTERNS) <= 12


class TestBuildCompletedResumeText:
    """The bridge helper must layer context_summary + reply_chain_context stably."""

    def _fake_session(self, summary):
        class FakeSession:
            context_summary = summary

        return FakeSession()

    def test_summary_only_matches_legacy_format(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        result = _build_completed_resume_text(self._fake_session("did work"), "hi")
        assert result == "[Prior session context: did work]\n\nhi"

    def test_empty_reply_chain_is_noop(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        base = _build_completed_resume_text(self._fake_session("did work"), "hi")
        with_empty = _build_completed_resume_text(
            self._fake_session("did work"), "hi", reply_chain_context=""
        )
        with_none = _build_completed_resume_text(
            self._fake_session("did work"), "hi", reply_chain_context=None
        )
        assert base == with_empty == with_none

    def test_none_summary_falls_back_to_generic_sentinel(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        result = _build_completed_resume_text(self._fake_session(None), "hi")
        assert "[Prior session context: This continues a previously completed session.]" in result
        assert result.endswith("hi")

    def test_reply_chain_appears_between_summary_and_follow_up(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        chain_block = "REPLY THREAD CONTEXT (oldest to newest):\nTom: hi\nValor: hello"
        result = _build_completed_resume_text(
            self._fake_session("prior work"), "follow up", reply_chain_context=chain_block
        )
        # Order: summary -> chain -> follow_up
        i_summary = result.index("[Prior session context:")
        i_chain = result.index(REPLY_THREAD_CONTEXT_HEADER)
        i_follow = result.index("follow up")
        assert i_summary < i_chain < i_follow

    def test_exactly_one_header_when_caller_passes_one(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        chain_block = "REPLY THREAD CONTEXT (oldest to newest):\nTom: hi"
        result = _build_completed_resume_text(
            self._fake_session("ctx"), "msg", reply_chain_context=chain_block
        )
        assert result.count(REPLY_THREAD_CONTEXT_HEADER) == 1


class TestFilterToolLogsParity:
    """`bridge.context.filter_tool_logs` must be the canonical version
    in `bridge.response`.

    History: a stale local `def filter_tool_logs` lived in
    `bridge/context.py` and silently diverged from the canonical
    `bridge/response.py` implementation (variation-selector handling,
    backtick-shell echoes, `<5` char length floor — all weaker in the
    stale copy). PR #1077 consolidated `bridge/response.py` but its
    audit only grepped for *imports* of the canonical path, missing
    the orphan `def`. Issue #1359 closes the duplicate and these tests
    are the permanent guard against the same audit miss recurring.
    """

    def test_filter_tool_logs_is_response_canonical(self):
        """Identity assertion: any future re-introduction of a local
        `def filter_tool_logs` in `bridge/context.py` will shadow the
        import and fail this test, breaking CI on the offending PR."""
        import bridge.context
        import bridge.response

        assert bridge.context.filter_tool_logs is bridge.response.filter_tool_logs

    def test_format_reply_chain_drops_variation_selector_and_backtick_echo(self):
        """Through-pipeline assertion: `format_reply_chain` must drop
        U+FE0F-prefixed tool traces (`🛠️ exec:`, `📖 read:`) and
        backtick-wrapped shell echoes from Valor messages.

        Asserts the canonical filter (which handles variation selectors
        and the `_SHELL_COMMAND_HINTS` echo filter) reaches the live
        impact path that feeds `_build_completed_resume_text` at
        `bridge/telegram_bridge.py:1740` and `:2259`.
        """
        from bridge.context import format_reply_chain

        # U+FE0F variation selector after wrench emoji, plus a book emoji,
        # plus a backtick-wrapped shell command echo.
        valor_message = (
            "Here is the analysis you asked for.\n"
            "\U0001f6e0️ exec: ls -la\n"
            "\U0001f4d6 read: bridge/context.py\n"
            "`cd bridge && ls -la`\n"
            "The duplicate definition is at line 104."
        )
        chain = [
            {"sender": "Tom", "content": "what's in bridge/?", "message_id": 1, "date": None},
            {"sender": "Valor", "content": valor_message, "message_id": 2, "date": None},
        ]

        formatted = format_reply_chain(chain)

        # All three filter targets must be absent from the output.
        assert "\U0001f6e0" not in formatted, "wrench emoji tool trace leaked"
        assert "\U0001f4d6" not in formatted, "book emoji tool trace leaked"
        assert "`cd bridge && ls -la`" not in formatted, "backtick shell echo leaked"
        # The meaningful prose must still be present.
        assert "Here is the analysis you asked for." in formatted
        assert "The duplicate definition is at line 104." in formatted

    def test_format_reply_chain_omits_messages_below_length_floor(self):
        """Through-pipeline assertion: when `filter_tool_logs` returns `""`
        because the post-filter remainder is below the `<5` char floor,
        `format_reply_chain` must omit the Valor message entirely
        (the existing `if not content: continue` at `bridge/context.py:486-487`
        handles this once the canonical floor returns `""`).

        This floor is currently UNCOVERED through `format_reply_chain` — the
        existing direct-function tests at
        `tests/integration/test_reply_delivery.py:191-229` and
        `tests/e2e/test_message_pipeline.py:239-246` only exercise
        `filter_tool_logs` in isolation.
        """
        from bridge.context import format_reply_chain

        # After filter_tool_logs runs, only "ok" remains (2 chars, below
        # the `<5` floor) so the canonical version returns "".
        valor_short = "\U0001f6e0️ exec: ls\nok"
        chain = [
            {"sender": "Tom", "content": "run ls", "message_id": 1, "date": None},
            {"sender": "Valor", "content": valor_short, "message_id": 2, "date": None},
            {"sender": "Tom", "content": "thanks", "message_id": 3, "date": None},
        ]

        formatted = format_reply_chain(chain)

        # The Valor message must be omitted entirely.
        assert "ok" not in formatted, "below-floor remainder should not reach the output"
        assert "Valor:" not in formatted, "Valor message should be omitted, not just emptied"
        # The surrounding messages must still be present.
        assert "Tom: run ls" in formatted
        assert "Tom: thanks" in formatted
