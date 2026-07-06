"""Tests for verify_tui_marker_contract() (D1b, issue #1817).

D1b converts a routine `claude` CLI auto-update that rewords a scraped TUI
string into a loud, operator-visible signal instead of a silent fleet-wide
PTY-session hang. `verify_tui_marker_contract()` is a fingerprint check: it
re-runs each marker regex (IDLE_BAR, PROMPT_GLYPH, SPINNER_EVIDENCE_RE, plus
the trust-folder prompt pattern in startup_parser.py) against a golden
sample of known-good CLI output, without spawning a live PTY.
"""

from __future__ import annotations

from unittest.mock import patch

from agent.granite_container.pty_driver import (
    _CONTRACT_GOLDEN_SAMPLES,
    verify_tui_marker_contract,
)


class TestGoldenSamplesMatch:
    def test_all_markers_match_their_golden_sample(self):
        """With unmodified markers and golden samples, the contract holds."""
        ok, failed = verify_tui_marker_contract()

        assert ok is True
        assert failed == []

    def test_golden_samples_cover_all_five_markers(self):
        """Sanity: the fixture dict itself must name all five markers."""
        assert set(_CONTRACT_GOLDEN_SAMPLES) == {
            "IDLE_BAR",
            "AGENTS_HINT_BAR",
            "PROMPT_GLYPH",
            "SPINNER_EVIDENCE_RE",
            "TRUST_FOLDER_PROMPT",
        }


class TestMarkerMismatchDetected:
    def test_idle_bar_mismatch_reported(self):
        """A regex that stops matching its golden sample is reported by name."""
        with patch(
            "agent.granite_container.pty_driver._CONTRACT_GOLDEN_SAMPLES",
            {**_CONTRACT_GOLDEN_SAMPLES, "IDLE_BAR": "totally different bottom bar text"},
        ):
            ok, failed = verify_tui_marker_contract()

        assert ok is False
        assert "IDLE_BAR" in failed

    def test_prompt_glyph_mismatch_reported(self):
        with patch(
            "agent.granite_container.pty_driver._CONTRACT_GOLDEN_SAMPLES",
            {**_CONTRACT_GOLDEN_SAMPLES, "PROMPT_GLYPH": "no glyph here"},
        ):
            ok, failed = verify_tui_marker_contract()

        assert ok is False
        assert "PROMPT_GLYPH" in failed

    def test_spinner_evidence_mismatch_reported(self):
        with patch(
            "agent.granite_container.pty_driver._CONTRACT_GOLDEN_SAMPLES",
            {**_CONTRACT_GOLDEN_SAMPLES, "SPINNER_EVIDENCE_RE": "nothing spinner-shaped"},
        ):
            ok, failed = verify_tui_marker_contract()

        assert ok is False
        assert "SPINNER_EVIDENCE_RE" in failed

    def test_trust_folder_mismatch_reported(self):
        with patch(
            "agent.granite_container.pty_driver._CONTRACT_GOLDEN_SAMPLES",
            {**_CONTRACT_GOLDEN_SAMPLES, "TRUST_FOLDER_PROMPT": "nothing about trust here"},
        ):
            ok, failed = verify_tui_marker_contract()

        assert ok is False
        assert "TRUST_FOLDER_PROMPT" in failed

    def test_multiple_mismatches_all_reported(self):
        with patch(
            "agent.granite_container.pty_driver._CONTRACT_GOLDEN_SAMPLES",
            {
                "IDLE_BAR": "x",
                "AGENTS_HINT_BAR": "x",
                "PROMPT_GLYPH": "x",
                "SPINNER_EVIDENCE_RE": "x",
                "TRUST_FOLDER_PROMPT": "x",
            },
        ):
            ok, failed = verify_tui_marker_contract()

        assert ok is False
        assert set(failed) == {
            "IDLE_BAR",
            "AGENTS_HINT_BAR",
            "PROMPT_GLYPH",
            "SPINNER_EVIDENCE_RE",
            "TRUST_FOLDER_PROMPT",
        }


class TestNeverRaises:
    def test_startup_parser_import_failure_reported_not_raised(self):
        """A broken startup_parser import must be reported as a failed marker,
        not propagate and crash the worker-startup caller."""
        with patch(
            "agent.granite_container.startup_parser.parse_startup_frame",
            side_effect=RuntimeError("boom"),
        ):
            ok, failed = verify_tui_marker_contract()

        assert ok is False
        assert "TRUST_FOLDER_PROMPT" in failed
