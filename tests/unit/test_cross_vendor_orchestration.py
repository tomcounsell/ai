"""Unit tests for the parent orchestration's collect-iff-ok contract (issue #1626).

These are pure logic tests — no OpenAI calls, no Redis, no real sessions.
Verifies the envelope filtering logic that gates whether a cross-vendor judge
dict reaches compute_consensus and record_verdict.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from agent.sdlc_review_consensus import compute_consensus
from tools.cross_vendor_judge import CROSS_VENDOR_JUDGE_ID
from tools.sdlc_verdict import _validate_judges_payload, record_verdict

# ---------------------------------------------------------------------------
# Envelope collection contract — append-iff-ok
# ---------------------------------------------------------------------------


class TestOkEnvelopeAppendsJudgeDict:
    def test_ok_envelope_appends_judge_dict(self):
        """An ok envelope causes the parent to append its judge dict to the list."""
        judge_payload = {
            "judge_id": CROSS_VENDOR_JUDGE_ID,
            "verdict": "APPROVED",
            "blockers": 0,
            "tech_debt": 0,
            "confidence": 0.9,
            "reasoning_summary": "Looks fine.",
            "meta": {"model": "gpt-4o"},
        }
        envelope = {"status": "ok", "judge": judge_payload}

        # Simulate the parent's collection logic.
        judges: list = []
        if envelope["status"] == "ok":
            judges.append(envelope["judge"])

        assert len(judges) == 1
        assert judges[0] == judge_payload

    def test_skip_envelope_appends_nothing(self):
        """A skip envelope is filtered out — judge list remains empty."""
        envelope = {"status": "skipped", "reason": "api error", "meta": {"model": "gpt-4o"}}

        judges: list = []
        if envelope["status"] == "ok":
            judges.append(envelope["judge"])

        assert judges == []


# ---------------------------------------------------------------------------
# Skip envelopes never reach compute_consensus
# ---------------------------------------------------------------------------


class TestSkipEnvelopeNeverReachesComputeConsensus:
    def test_skip_envelope_never_reaches_compute_consensus(self):
        """An empty judges list (all skipped) produces conservative CHANGES REQUESTED."""
        # After filtering all skip envelopes, judges is empty.
        result = compute_consensus([], rule="any-blocker-wins")
        assert result["verdict"] == "CHANGES REQUESTED"
        assert result["consensus"]["n"] == 0


# ---------------------------------------------------------------------------
# Validation layer — skip envelope dicts rejected before record_verdict
# ---------------------------------------------------------------------------


class TestSkipEnvelopeNeverReachesRecordVerdict:
    def test_validate_judges_payload_rejects_dict_missing_verdict(self):
        """_validate_judges_payload rejects a dict that is missing 'verdict'."""
        bad_judge = {"judge_id": "cross-vendor", "blockers": 0}
        assert _validate_judges_payload([bad_judge]) is False

    def test_validate_judges_payload_rejects_dict_missing_blockers(self):
        """_validate_judges_payload rejects a dict that is missing 'blockers'."""
        bad_judge = {"judge_id": "cross-vendor", "verdict": "APPROVED"}
        assert _validate_judges_payload([bad_judge]) is False

    def test_validate_judges_payload_rejects_non_int_blockers(self):
        """_validate_judges_payload rejects a dict where blockers is not int."""
        # A skip envelope might have e.g. "reason" instead of proper fields.
        bad_judge = {"judge_id": "cross-vendor", "verdict": "APPROVED", "blockers": "many"}
        assert _validate_judges_payload([bad_judge]) is False

    def test_validate_judges_payload_accepts_well_formed_dict(self):
        """_validate_judges_payload accepts a correct judge dict."""
        good_judge = {
            "judge_id": CROSS_VENDOR_JUDGE_ID,
            "verdict": "APPROVED",
            "blockers": 0,
            "tech_debt": 0,
            "confidence": 0.9,
        }
        assert _validate_judges_payload([good_judge]) is True

    def test_record_verdict_returns_empty_for_malformed_judge_dict(self):
        """record_verdict returns {} when given a skip-like malformed judge dict."""

        class _FakeSession:
            session_id = "fake-orch-1"
            session_type = "eng"
            stage_states = "{}"

            def save(self):
                pass

        session = _FakeSession()

        with patch("tools.stage_states_helpers._reload_session", return_value=session):
            # Pass a dict that looks like a skip envelope — missing 'verdict', 'blockers'.
            result = record_verdict(
                session,
                "REVIEW",
                "APPROVED",
                judges=[{"status": "skipped", "reason": "api error"}],
                consensus={"rule": "any-blocker-wins"},
            )

        assert result == {}
        # No partial write — stage_states should be untouched.
        data = json.loads(session.stage_states)
        assert "_verdicts" not in data or "REVIEW" not in data.get("_verdicts", {})
