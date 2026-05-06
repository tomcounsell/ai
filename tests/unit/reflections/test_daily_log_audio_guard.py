"""Unit tests for the audio guard regex (#1263).

Verifies that the Layer 2 (prefixed) and Layer 3 (bare 3+ digit) regexes
imported from reflections.pm_audio_briefing.builder catch the leak patterns
the daily-log audio brief is most likely to produce: "PR 1263",
"issue #1263", "10 commits". The exempted-units list (ms, %, requests, etc.)
must continue to pass.
"""

from __future__ import annotations

import pytest

from reflections.pm_audio_briefing.builder import (
    _NUMBERS_BARE_RE,
    _NUMBERS_PREFIXED_RE,
    BriefingNumbersDetectedError,
    _check_numbers,
)

# --- Layer 2: prefixed forms -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "We shipped PR 1263 today",
        "issue 1263 was closed",
        "issue #1263 needs review",
        "issue_1263 is the parent",
        "pr-1263 is queued",
        "PR-1263 ready",
    ],
)
def test_layer2_catches_prefixed_numbers(text):
    """Prefixed forms must trigger BriefingNumbersDetectedError."""
    with pytest.raises(BriefingNumbersDetectedError):
        _check_numbers(text)


# --- Layer 3: bare 3+ digit integers -----------------------------------------


def test_layer3_catches_bare_integers_without_exempted_unit():
    """Bare 3+ digit integers without an exempted unit suffix must trigger."""
    with pytest.raises(BriefingNumbersDetectedError):
        _check_numbers("We made 1263 changes")


def test_layer3_exempted_unit_passes():
    """Bare 3+ digit integers followed by an exempted unit suffix must NOT trigger.

    "363 lines" is exempt because 'lines' is an exempted unit; the regex only
    flags numbers whose adjacent word is not in the exempt list.
    """
    _check_numbers("Reviewed 363 lines of patch")  # must not raise


@pytest.mark.parametrize(
    "text",
    [
        "5 commits landed",  # 1-digit number, not caught by 3+ regex
        "10 commits landed",  # 2-digit number, also not caught
        "10ms latency improvement",  # ms exempt
        "50% faster",  # % exempt
        "300 users signed up",  # 3-digit but exempt by 'users'
        "150 requests per second",  # exempt by 'requests'
        "100 lines of context",  # exempt by 'lines'
        "200 ms response time",  # exempt by 'ms' (with optional whitespace)
    ],
)
def test_exempted_or_short_numbers_pass(text):
    """Short numbers and exempted-unit forms must NOT trigger the guard."""
    # Should not raise
    _check_numbers(text)


# --- Combination edge case ---------------------------------------------------


def test_audio_guard_blocks_pr_with_decimal():
    """A bare PR number adjacent to a decimal point in the same sentence."""
    with pytest.raises(BriefingNumbersDetectedError):
        _check_numbers("Cost was $0.42 and PR 1263 is merged")


# --- Direct regex sanity ----------------------------------------------------


def test_regexes_importable_from_pm_audio_briefing():
    """The plan requires direct import of the named regexes from builder.py."""
    assert _NUMBERS_PREFIXED_RE is not None
    assert _NUMBERS_BARE_RE is not None
    # Sanity: prefixed catches "PR 1263"
    assert _NUMBERS_PREFIXED_RE.search("PR 1263 closed")
    # Sanity: bare catches "1263 changes" (no exempt word)
    assert _NUMBERS_BARE_RE.search("we made 1263 changes")
    # Sanity: bare does NOT catch "100 users"
    assert not _NUMBERS_BARE_RE.search("100 users active")
