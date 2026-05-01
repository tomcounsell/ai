"""Unit tests for bridge/redundancy_filter.py.

Tests the should_suppress() function, all five termination conditions,
threshold edge cases, empty/None inputs, and the error-fallback contract.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from bridge.redundancy_filter import (
    REDUNDANCY_THRESHOLD,
    REDUNDANCY_WINDOW_SECONDS,
    SuppressionVerdict,
    should_suppress,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _draft(text: str, artifacts: dict | None = None, age_secs: float = 0.0) -> dict:
    """Build a recent_sent_drafts entry."""
    return {
        "ts": time.time() - age_secs,
        "text": text,
        "artifacts": artifacts or {},
    }


def _repeat_text(n: int = 100) -> str:
    """Generate a long string so bigrams are plentiful and Jaccard is reliable."""
    return " ".join(["checking status waiting children completing reviewing"] * n)


# ── SuppressionVerdict dataclass ─────────────────────────────────────────────


class TestSuppressionVerdict:
    def test_required_fields(self):
        v = SuppressionVerdict(action="send", reason="no_baseline")
        assert v.action == "send"
        assert v.reason == "no_baseline"
        assert v.jaccard is None
        assert v.matched_index is None

    def test_suppress_with_jaccard(self):
        v = SuppressionVerdict(action="suppress", reason="dup", jaccard=0.75, matched_index=0)
        assert v.action == "suppress"
        assert v.jaccard == 0.75
        assert v.matched_index == 0


# ── Termination condition 1: empty / whitespace-only draft ───────────────────


class TestEmptyDraft:
    def test_empty_string_sends(self):
        v = should_suppress("", {}, [_draft(_repeat_text())], None, None)
        assert v.action == "send"
        assert v.reason == "empty_draft"

    def test_whitespace_only_sends(self):
        v = should_suppress("   \n\t  ", {}, [_draft(_repeat_text())], None, None)
        assert v.action == "send"
        assert v.reason == "empty_draft"


# ── Termination condition 2: no baseline ─────────────────────────────────────


class TestNoBaseline:
    def test_none_recent_drafts_sends(self):
        v = should_suppress(_repeat_text(), {}, None, None, None)
        assert v.action == "send"
        assert v.reason == "no_baseline"

    def test_empty_list_sends(self):
        v = should_suppress(_repeat_text(), {}, [], None, None)
        assert v.action == "send"
        assert v.reason == "no_baseline"


# ── Termination condition 3: non-empty expectations ──────────────────────────


class TestHasExpectations:
    def test_non_empty_expectations_sends(self):
        prior = _draft(_repeat_text())
        v = should_suppress(_repeat_text(), {}, [prior], ["please confirm?"], None)
        assert v.action == "send"
        assert v.reason == "has_expectations"

    def test_empty_list_expectations_does_not_short_circuit(self):
        """Empty list is falsy — should NOT trigger has_expectations."""
        prior = _draft(_repeat_text())
        v = should_suppress(_repeat_text(), {}, [prior], [], None)
        # Depends on similarity; just assert it's NOT has_expectations
        assert v.reason != "has_expectations"

    def test_none_expectations_does_not_short_circuit(self):
        prior = _draft(_repeat_text())
        v = should_suppress(_repeat_text(), {}, [prior], None, None)
        assert v.reason != "has_expectations"


# ── Termination condition 4: terminal session status ─────────────────────────


class TestTerminalStatus:
    @pytest.mark.parametrize("status", ["completed", "failed", "blocked"])
    def test_terminal_status_sends(self, status):
        prior = _draft(_repeat_text())
        v = should_suppress(_repeat_text(), {}, [prior], None, status)
        assert v.action == "send"
        assert v.reason == "terminal_status"

    def test_non_terminal_status_does_not_short_circuit(self):
        prior = _draft(_repeat_text())
        v = should_suppress(_repeat_text(), {}, [prior], None, "active")
        assert v.reason != "terminal_status"

    def test_none_status_does_not_short_circuit(self):
        prior = _draft(_repeat_text())
        v = should_suppress(_repeat_text(), {}, [prior], None, None)
        assert v.reason != "terminal_status"


# ── Termination condition 5: new artifact ────────────────────────────────────


class TestNewArtifact:
    def test_new_pr_url_sends(self):
        """A new PR URL in the draft prevents suppression."""
        text = _repeat_text()
        prior = _draft(text, artifacts={"urls": ["https://example.com/a"]})
        new_artifacts = {"urls": ["https://example.com/a", "https://github.com/foo/bar/pull/99"]}
        v = should_suppress(text, new_artifacts, [prior], None, None)
        assert v.action == "send"
        assert v.reason == "new_artifact"

    def test_new_commit_hash_sends(self):
        text = _repeat_text()
        prior = _draft(text, artifacts={})
        new_artifacts = {"commits": ["abc123def456"]}
        v = should_suppress(text, new_artifacts, [prior], None, None)
        assert v.action == "send"
        assert v.reason == "new_artifact"

    def test_same_artifacts_does_not_short_circuit(self):
        """Identical artifact sets do NOT trigger new_artifact termination."""
        text = _repeat_text()
        artifacts = {"urls": ["https://github.com/foo/bar/pull/10"]}
        prior = _draft(text, artifacts=artifacts)
        v = should_suppress(text, artifacts, [prior], None, None)
        # Should suppress (if text is similar enough) or send for other reasons
        assert v.reason != "new_artifact"

    def test_empty_new_artifacts_does_not_trigger_new_artifact(self):
        text = _repeat_text()
        prior = _draft(text, artifacts={})
        v = should_suppress(text, {}, [prior], None, None)
        assert v.reason != "new_artifact"

    def test_none_new_artifacts_treated_as_empty(self):
        text = _repeat_text()
        prior = _draft(text, artifacts={})
        v = should_suppress(text, None, [prior], None, None)
        assert v.reason != "new_artifact"


# ── Bigram Jaccard suppression ────────────────────────────────────────────────


class TestBigramJaccard:
    def test_identical_text_suppresses(self):
        text = _repeat_text()
        prior = _draft(text)
        v = should_suppress(text, {}, [prior], None, "active")
        assert v.action == "suppress"
        assert v.jaccard is not None
        assert v.jaccard >= REDUNDANCY_THRESHOLD
        assert v.matched_index == 0

    def test_slightly_different_text_may_suppress(self):
        """Near-verbatim repeat (adds one word) should still suppress."""
        base = _repeat_text(50)
        text = base + " additionally"
        prior = _draft(base)
        v = should_suppress(text, {}, [prior], None, None)
        # With a long shared base the Jaccard should be well above threshold.
        assert v.action == "suppress"

    def test_very_different_text_sends(self):
        prior_text = "checking status waiting children completing reviewing " * 50
        new_text = "shipping production deploy feature branch merged success " * 50
        prior = _draft(prior_text)
        v = should_suppress(new_text, {}, [prior], None, None)
        assert v.action == "send"
        assert v.reason in ("below_threshold", "new_artifact")

    def test_threshold_boundary_below_sends(self):
        """Test below the default threshold (0.63 < 0.65) → send.

        Jaccard = |I| / (|I| + |A| + |B|) where I=intersection, A=new-only, B=prior-only.
        For J=0.63: new has 63 items, prior has 63+37=100 items, overlap=63 → 63/(63+0+37)=0.63.
        """
        from unittest.mock import patch as _patch

        text = _repeat_text()
        prior = _draft(text)

        # new_bigrams: items 0..62 (63 items)
        # prior_bigrams: items 0..62 (shared) + items 100..136 (37 prior-only)
        new_bigrams = frozenset([(f"w{i}",) for i in range(63)])
        prior_bigrams = frozenset(
            [(f"w{i}",) for i in range(63)] + [(f"x{i}",) for i in range(37)]
        )
        # J = 63 / (63 + 0 + 37) = 63/100 = 0.63 < 0.65 → send

        with _patch("agent.memory_extraction._extract_bigrams", side_effect=[new_bigrams, prior_bigrams]):
            v = should_suppress(text, {}, [prior], None, None)

        assert v.action == "send"
        assert v.reason == "below_threshold"

    def test_threshold_boundary_above_suppresses(self):
        """Test above the default threshold (0.66 > 0.65) → suppress.

        Jaccard = |I| / (|I| + |A| + |B|).
        For J=0.66: new has 66 items, prior has 66+34=100 items, overlap=66 → 66/(66+0+34)=0.66.
        """
        from unittest.mock import patch as _patch

        text = _repeat_text()
        prior = _draft(text)

        # new_bigrams: items 0..65 (66 items)
        # prior_bigrams: items 0..65 (shared) + items 100..133 (34 prior-only)
        new_bigrams = frozenset([(f"w{i}",) for i in range(66)])
        prior_bigrams = frozenset(
            [(f"w{i}",) for i in range(66)] + [(f"x{i}",) for i in range(34)]
        )
        # J = 66 / (66 + 0 + 34) = 66/100 = 0.66 > 0.65 → suppress

        with _patch("agent.memory_extraction._extract_bigrams", side_effect=[new_bigrams, prior_bigrams]):
            v = should_suppress(text, {}, [prior], None, None)

        assert v.action == "suppress"

    def test_stale_prior_skipped(self):
        """Entries older than REDUNDANCY_WINDOW_SECONDS are excluded from comparison."""
        text = _repeat_text()
        # Create a prior that is older than the window.
        stale_prior = _draft(text, age_secs=REDUNDANCY_WINDOW_SECONDS + 1)
        v = should_suppress(text, {}, [stale_prior], None, None)
        # No in-window prior → no comparison → send.
        assert v.action == "send"
        assert v.reason == "below_threshold"

    def test_multiple_priors_picks_best_match(self):
        """When multiple priors exist, suppression fires if ANY exceeds threshold."""
        text = _repeat_text()
        distant = _draft("completely different content unrelated words " * 50)
        identical = _draft(text)
        v = should_suppress(text, {}, [distant, identical], None, None)
        assert v.action == "suppress"
        assert v.matched_index == 1


# ── Error fallback contract ───────────────────────────────────────────────────


class TestErrorFallback:
    def test_exception_inside_filter_returns_send(self):
        """Any unhandled exception inside should_suppress returns send."""
        with patch(
            "agent.memory_extraction._extract_bigrams",
            side_effect=RuntimeError("bigram extractor crashed"),
        ):
            text = _repeat_text()
            prior = _draft(text)
            v = should_suppress(text, {}, [prior], None, None)

        assert v.action == "send"
        assert v.reason == "filter_error"

    def test_filter_never_raises(self):
        """should_suppress must not propagate any exception."""
        # Pass garbage inputs.
        v = should_suppress(None, "not-a-dict", "not-a-list", 42, object())  # type: ignore[arg-type]
        # May return any send verdict — the important thing is no exception.
        assert v.action == "send"
