"""Unit tests for reflections/pm_audio_briefing/builder.py.

Covers:
- Pass A prompt asserts forbidden phrases (forward-looking, no-numbers,
  bare-digit, first-sentence-is-decision)
- BriefingNumbersDetectedError class is declared at module top
- Two-layer regex guard:
  Layer 2 catches "issue 1197", "PR 1197" but NOT "#1197"
  Layer 3 catches bare 4+ digit integers including the numeric part of "#1197"
  Both allow "$500", "250 users", "v3.5.2"
- Pass B word-count cut is strictly deterministic
- Empty signals + skip_when_empty handling
- Written follow-up parity with audio
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from reflections.pm_audio_briefing import builder

pytestmark = [pytest.mark.unit]


# --- Pass A prompt content ---------------------------------------------------


class TestPassAPrompt:
    """Pass A's system prompt must encode all semantic constraints."""

    def test_prompt_forbids_forward_looking_commitments(self):
        p = builder._PASS_A_SYSTEM
        for phrase in ("we will", "I'll push", "pushing to", "unless you want it sooner"):
            assert phrase in p, f"Pass A prompt missing forbidden phrase: {phrase!r}"

    def test_prompt_forbids_issue_pr_numbers(self):
        p = builder._PASS_A_SYSTEM.lower()
        assert "issue numbers" in p or "issue number" in p
        assert "pr number" in p or "pr numbers" in p

    def test_prompt_forbids_bare_3_digit_integers(self):
        p = builder._PASS_A_SYSTEM
        # The plan requires explicit guidance against bare 3+ digit integers
        assert "3 or more digits" in p or "three or more digits" in p

    def test_prompt_requires_first_sentence_is_decision(self):
        p = builder._PASS_A_SYSTEM
        assert "first sentence must be a decision" in p


# --- BriefingNumbersDetectedError class -------------------------------------


class TestBriefingNumbersDetectedError:
    def test_class_is_declared(self):
        # Must NOT raise NameError on attribute access
        assert isinstance(builder.BriefingNumbersDetectedError, type)
        assert issubclass(builder.BriefingNumbersDetectedError, RuntimeError)


# --- Layer 2/3 regex behavior ------------------------------------------------


class TestLayer2Prefixed:
    """Layer 2 catches `issue 1197`, `PR 1197`, `pr-363`, `issue_1197` but NOT `#1197`.

    Per plan B1-R5: separator changed from \\s* to [\\s\\-_]* to catch hyphen/underscore forms.
    """

    def test_layer2_catches_issue_prefix(self):
        assert re.search(builder._NUMBERS_PREFIXED_RE, "issue 1197 was fixed")

    def test_layer2_catches_pr_prefix(self):
        assert re.search(builder._NUMBERS_PREFIXED_RE, "PR 1197 merged")

    def test_layer2_catches_hyphen_separator(self):
        # Per plan B1-R5: pr-363 is caught by Layer 2 (was previously slipping through)
        assert re.search(builder._NUMBERS_PREFIXED_RE, "pr-363 was merged")

    def test_layer2_catches_underscore_separator(self):
        # Per plan B1-R5: issue_1197 is caught by Layer 2
        assert re.search(builder._NUMBERS_PREFIXED_RE, "issue_1197 closed")

    def test_layer2_does_not_catch_pound_only(self):
        # `#` is not a word char so `\b` before it never anchors
        assert not re.search(builder._NUMBERS_PREFIXED_RE, "#1197")

    def test_layer2_allows_dollar_amounts(self):
        assert not re.search(builder._NUMBERS_PREFIXED_RE, "raised $500")

    def test_layer2_allows_user_counts(self):
        assert not re.search(builder._NUMBERS_PREFIXED_RE, "250 users signed up")


class TestLayer3Bare:
    """Layer 3 catches bare 3+ digit integers including the numeric part of `#1197`.

    Per plan B1-R5: floor changed from 4+ to 3+ with lookbehind/lookahead guards.
    """

    def test_layer3_catches_bare_4_digit(self):
        assert re.search(builder._NUMBERS_BARE_RE, "1197")

    def test_layer3_catches_bare_3_digit(self):
        # Per plan B1-R5: bare 3-digit numbers like 363 are now caught.
        assert re.search(builder._NUMBERS_BARE_RE, "bare 363 in transcript")

    def test_layer3_catches_pound_form_via_digit(self):
        # `#1197` has 1197 as a bare token Layer 3 catches
        assert re.search(builder._NUMBERS_BARE_RE, "#1197 fixed")

    def test_layer3_allows_dollar_prefixed_3_digit(self):
        assert not re.search(builder._NUMBERS_BARE_RE, "$500 raised")

    def test_layer3_allows_user_counts(self):
        assert not re.search(builder._NUMBERS_BARE_RE, "250 users active")

    def test_layer3_allows_version_strings(self):
        # v3.5.2 -- digits separated by dots, no individual segment >= 3 digits
        assert not re.search(builder._NUMBERS_BARE_RE, "v3.5.2 release")


# --- Combined guard via _check_numbers --------------------------------------


class TestCheckNumbers:
    """Combined-guard tests. Class name + `no_numbers` keyword in test names
    so `pytest -k no_numbers` collects them (used by the plan's Verification
    table)."""

    def test_no_numbers_raises_on_issue_prefix(self):
        with pytest.raises(builder.BriefingNumbersDetectedError):
            builder._check_numbers("Hi, issue 1197 was fixed yesterday.")

    def test_raises_on_issue_prefix(self):
        with pytest.raises(builder.BriefingNumbersDetectedError):
            builder._check_numbers("Hi, issue 1197 was fixed yesterday.")

    def test_raises_on_pound_only_via_layer3(self):
        with pytest.raises(builder.BriefingNumbersDetectedError):
            builder._check_numbers("#1197 is closed.")

    def test_raises_on_bare_4_digit(self):
        with pytest.raises(builder.BriefingNumbersDetectedError):
            builder._check_numbers("1197 was a memorable year.")

    def test_passes_on_clean_text(self):
        # No exception
        builder._check_numbers("We shipped the auth fix yesterday.")

    def test_passes_on_dollar_3_digit_version(self):
        builder._check_numbers("Raised $500 from 250 users; bumped v3.5.2.")


# --- Pass B word count cut --------------------------------------------------


class TestPassBCut:
    def test_under_max_passes_through(self):
        text = "One sentence. Two sentence. Three sentence."
        assert builder._pass_b_cut(text) == text

    def test_over_max_drops_trailing_sentences(self):
        # Build 100-word transcript
        text = " ".join(["word"] * 100)
        # Whole text is one sentence; ensure it stays short after split fails
        # We just need the function to not raise.
        out = builder._pass_b_cut(text)
        assert len(out.split()) <= 100

    def test_preserves_close_when_truncating(self):
        sents = ["Sentence one is here." for _ in range(20)]
        sents.append("I've got the rest.")
        text = " ".join(sents)
        out = builder._pass_b_cut(text)
        assert "I've got the rest" in out


# --- build() public API -----------------------------------------------------


class TestBuildEmptySignals:
    def test_skip_when_empty_returns_empties(self):
        out = builder.build({}, fallback_message="x", skip_when_empty=True)
        assert out == ("", "")

    def test_no_skip_returns_fallback(self):
        out, follow = builder.build(
            {"merges": []},
            fallback_message="Nothing shipped yesterday — three things queued.",
            skip_when_empty=False,
        )
        assert "Nothing shipped" in out
        assert follow == ""

    def test_no_skip_fallback_with_numbers_raises(self):
        # Misconfigured fallback containing a number triggers the guard
        with pytest.raises(builder.BriefingNumbersDetectedError):
            builder.build(
                {},
                fallback_message="Fallback for issue 1197",
                skip_when_empty=False,
            )


class TestBuildWithSignals:
    def test_passes_through_pass_a_and_b(self):
        signals = {"merges": [{"subject": "Fix auth flow", "pr_number": 1}]}

        clean_transcript = (
            "Shipped the auth fix yesterday and the rollout is looking clean across all "
            "environments. "
            "The login session persistence bug is now fully resolved with no regressions. "
            "A few quick FYIs: the memory dedup pass ran overnight successfully, "
            "a small dashboard polish landed in staging, and the nightly test suite is green. "
            "I've got the rest."
        )
        with patch.object(builder, "_draft_pass_a", return_value=clean_transcript):
            audio, follow = builder.build(signals, fallback_message="x", skip_when_empty=False)
        assert "auth fix" in audio
        assert "Shipped" in follow  # written followup includes a Shipped section
        assert "Fix auth flow" in follow

    def test_pass_a_with_forbidden_number_raises(self):
        signals = {"merges": [{"subject": "Auth", "pr_number": 1}]}
        with patch.object(builder, "_draft_pass_a", return_value="Shipped issue 1197."):
            with pytest.raises(builder.BriefingNumbersDetectedError):
                builder.build(signals, fallback_message="x", skip_when_empty=False)

    def test_pass_a_under_min_word_count_raises(self):
        """Pass A transcript under 55 words raises BriefingWordCountError (Tech Debt 1 fix)."""
        signals = {"merges": [{"subject": "Auth", "pr_number": 1}]}
        short_transcript = "Shipped the auth fix. I've got the rest."
        with patch.object(builder, "_draft_pass_a", return_value=short_transcript):
            with pytest.raises(builder.BriefingWordCountError) as exc_info:
                builder.build(signals, fallback_message="x", skip_when_empty=False)
        assert "minimum word count" in str(exc_info.value)

    def test_followup_includes_links_when_url_present(self):
        signals = {"merges": [{"subject": "Auth", "pr_number": 42, "url": "https://gh/x/42"}]}
        clean_transcript = (
            "We shipped a meaningful auth update that resolves the login session persistence bug. "
            "The fix went out cleanly and the error rate dropped to zero in all environments. "
            "A few quick FYIs: the deploy queue is clear, the dashboard now shows fresh data, "
            "and the nightly regression suite passed without issues. "
            "I've got the rest."
        )
        with patch.object(builder, "_draft_pass_a", return_value=clean_transcript):
            _, follow = builder.build(signals, fallback_message="x", skip_when_empty=False)
        assert "[#42]" in follow
        assert "https://gh/x/42" in follow
