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
        assert (
            "[Prior session context: This continues a previously completed session.]"
            in result
        )
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
