"""Unit tests for the serves-charter judge (lane 4, task 3b).

Covers ``tools.improvement_eval.judges.serves_charter``: the reserved judge
id, the status-discriminated envelope, typed coercion of every response
field, the never-fabricate rule for unparseable responses, verbatim charter
carriage with digest citation, and charter section 7 provider routing in
both directions.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from agent.sdlc_review_consensus import compute_consensus

CHARTER_TEXT = """---
owner: Tom Counsell
version: 2
effective: 2026-09-01
---

# Improvement charter (test fixture)

Section 7: open-source work carries no provider restriction.
Section 12: every verdict cites the digest it was measured under.
"""
CHARTER_DIGEST = "sha256:test-digest-001"
CANDIDATE = "candidate output: add reranking to retrieval"


def _run(raw_response: str, **overrides):
    from tools.improvement_eval.judges import serves_charter as sc

    params = {
        "candidate_output": CANDIDATE,
        "charter_text": CHARTER_TEXT,
        "charter_digest": CHARTER_DIGEST,
        "project_key": "test-3216-charter",
        "blinded_arm_id": "arm-a",
        "_complete": lambda *, provider, model, prompt: raw_response,
    }
    params.update(overrides)
    with patch("tools.improvement_eligibility.is_open_source", return_value=True):
        return sc.run_serves_charter_judge(**params)


def _aligned_response(**overrides):
    payload = {
        "serves_charter": True,
        "blockers": 0,
        "confidence": 0.8,
        "reasoning_summary": "The candidate stays inside the authorized surface.",
    }
    payload.update(overrides)
    return json.dumps(payload)


class TestJudgeId:
    def test_judge_id_value(self):
        from tools.improvement_eval.judges.serves_charter import SERVES_CHARTER_JUDGE_ID

        assert SERVES_CHARTER_JUDGE_ID == "serves-charter"

    def test_judge_id_disjoint_from_existing_roster(self):
        from tools.cross_vendor_judge import CROSS_VENDOR_JUDGE_ID
        from tools.improvement_eval.judges.serves_charter import SERVES_CHARTER_JUDGE_ID

        assert SERVES_CHARTER_JUDGE_ID not in {"code-quality", "risk", CROSS_VENDOR_JUDGE_ID}

    def test_module_docstring_notes_charter_digest_rule(self):
        from tools.improvement_eval.judges import serves_charter as sc

        assert "12" in sc.__doc__
        assert "digest" in sc.__doc__


class TestEnvelopeAndCoercion:
    def test_aligned_response_yields_ok_envelope(self):
        envelope = _run(_aligned_response())
        assert envelope["status"] == "ok"
        judge = envelope["judge"]
        assert judge["judge_id"] == "serves-charter"
        assert judge["verdict"] == "APPROVED"
        assert judge["blockers"] == 0
        assert judge["confidence"] == 0.8
        assert judge["reasoning_summary"]
        assert judge["charter_digest"] == CHARTER_DIGEST

    def test_negative_response_maps_to_changes_requested(self):
        envelope = _run(_aligned_response(serves_charter=False, blockers=2))
        assert envelope["status"] == "ok"
        assert envelope["judge"]["verdict"] == "CHANGES REQUESTED"
        assert envelope["judge"]["blockers"] == 2

    def test_every_field_coerced_with_typed_fallback(self):
        envelope = _run(_aligned_response(confidence="high", reasoning_summary=42))
        assert envelope["status"] == "ok"
        judge = envelope["judge"]
        assert isinstance(judge["confidence"], float)
        assert 0.0 <= judge["confidence"] <= 1.0
        assert isinstance(judge["reasoning_summary"], str)
        assert judge["reasoning_summary"]

    def test_confidence_clamped_to_unit_interval(self):
        envelope = _run(_aligned_response(confidence=9.5))
        assert envelope["judge"]["confidence"] == 1.0

    def test_ok_judge_dict_is_consumable_by_compute_consensus(self):
        envelope = _run(_aligned_response())
        result = compute_consensus([envelope["judge"]], rule="any-blocker-wins")
        assert result["verdict"] == "APPROVED"

    def test_prose_response_is_skipped_never_fabricated(self):
        envelope = _run("The candidate looks fine to me, broadly aligned overall.")
        assert envelope["status"] == "skipped"
        assert envelope["reason"]
        assert "judge" not in envelope

    def test_truncated_json_is_skipped(self):
        envelope = _run('{"serves_charter": true, "blockers": 0, "confiden')
        assert envelope["status"] == "skipped"
        assert "judge" not in envelope

    def test_object_missing_every_key_is_skipped(self):
        envelope = _run(json.dumps({"note": "no judge fields here"}))
        assert envelope["status"] == "skipped"
        assert "judge" not in envelope

    def test_bool_blockers_refused_as_skipped(self):
        envelope = _run(_aligned_response(blockers=True))
        assert envelope["status"] == "skipped"
        assert "judge" not in envelope

    def test_transport_failure_is_skipped(self):
        from tools.improvement_eval.judges import serves_charter as sc

        def _boom(*, provider, model, prompt):
            raise RuntimeError("provider unreachable")

        with patch("tools.improvement_eligibility.is_open_source", return_value=True):
            envelope = sc.run_serves_charter_judge(
                candidate_output=CANDIDATE,
                charter_text=CHARTER_TEXT,
                charter_digest=CHARTER_DIGEST,
                project_key="test-3216-charter",
                _complete=_boom,
            )
        assert envelope["status"] == "skipped"
        assert "judge" not in envelope

    def test_empty_charter_text_is_skipped(self):
        envelope = _run(_aligned_response(), charter_text="  ")
        assert envelope["status"] == "skipped"


class TestCharterCarriage:
    def test_prompt_carries_charter_text_verbatim(self):
        from tools.improvement_eval.judges import serves_charter as sc

        prompt = sc.build_prompt(CHARTER_TEXT, CANDIDATE, "arm-b")
        assert CHARTER_TEXT in prompt
        assert CANDIDATE in prompt
        assert "arm-b" in prompt

    def test_prompt_never_names_the_candidate_identity(self):
        from tools.improvement_eval.judges import serves_charter as sc

        prompt = sc.build_prompt(CHARTER_TEXT, CANDIDATE, "arm-b")
        assert "candidate/add-rerank" not in prompt


class TestProviderRouting:
    def test_open_source_project_routes_off_claude(self):
        from tools.improvement_eval.judges import serves_charter as sc

        with patch("tools.improvement_eligibility.is_open_source", return_value=True) as gate:
            route = sc.select_provider("test-3216-open")
        gate.assert_called_once_with("test-3216-open")
        assert route["provider"] != "claude-subscription"

    def test_client_project_judge_stays_on_subscription_providers(self):
        from tools.improvement_eval.judges import serves_charter as sc

        with patch("tools.improvement_eligibility.is_open_source", return_value=False) as gate:
            route = sc.select_provider("test-3216-client")
        gate.assert_called_once_with("test-3216-client")
        assert route["provider"] == "claude-subscription"

    def test_envelope_records_the_selected_provider(self):
        envelope = _run(_aligned_response())
        assert envelope["judge"]["meta"]["provider"] != "claude-subscription"


class TestSubscriptionTransportConfinement:
    def test_headless_judge_runs_with_no_tools_from_an_empty_cwd(self):
        """The subscription judge cannot open the repo or the artifact store and de-blind itself."""
        import os
        import subprocess

        from tools.improvement_eval.judges import serves_charter as sc

        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = list(argv)
            seen["cwd"] = kwargs.get("cwd")
            seen["cwd_entries"] = os.listdir(kwargs["cwd"])
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps({"result": _aligned_response()}), stderr=""
            )

        with patch.object(sc.subprocess, "run", fake_run):
            result = sc._complete_via_subscription(
                provider="claude-subscription", model="claude", prompt="PROMPT"
            )

        assert json.loads(result)["serves_charter"] is True
        argv = seen["argv"]
        assert argv[:2] == ["claude", "-p"]
        assert "--tools=" in argv  # every built-in tool disabled, as one argv element
        assert "--strict-mcp-config" in argv  # and no MCP server loaded
        assert argv[-1] == "PROMPT"  # the prompt stays last, after the variadic flags
        assert seen["cwd_entries"] == []  # nothing to read where the judge runs
        assert os.path.realpath(seen["cwd"]) != os.path.realpath(os.getcwd())
        assert not os.path.exists(seen["cwd"])  # the empty cwd is removed afterwards
