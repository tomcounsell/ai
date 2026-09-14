"""Unit tests for the blinded judge envelope (lane 4, task 3a).

Covers ``tools.improvement_eval.blinding`` (seeded arm assignment,
blinded ids, identity-leak scan) and ``tools.improvement_eval.envelope``
(judge envelope wrap). The inner judge dict must stay consumable by
``agent.sdlc_review_consensus.compute_consensus`` unchanged.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from agent.sdlc_review_consensus import compute_consensus
from tools.improvement_eval.blinding import (
    BLINDED_ARM_IDS,
    CANDIDATE_ARM,
    INCUMBENT_ARM,
    ArmAssignment,
    assign_arms,
    identity_tokens,
    scan_for_identity,
)
from tools.improvement_eval.envelope import (
    EVALUATOR_VERSION,
    coerce_judge_dict,
    serialize_envelope,
    wrap_judge_envelope,
)


def _experiment(**overrides):
    base = {
        "id": "exp-001",
        "project_key": "valor",
        "candidate_surfaces": json.dumps(["agent/memory_retrieval.py", "tools/memory_search"]),
        "manifest": json.dumps({"branch": "candidate/add-rerank", "files": ["a.py"]}),
        "hypothesis": "reranking improves recall",
        "mechanism": None,
        "falsifier": None,
        "contract_digest": "sha256:abc",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _judge(**overrides):
    base = {
        "judge_id": "serves-charter",
        "verdict": "APPROVED",
        "blockers": 0,
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


def _envelope_kwargs(**overrides):
    base = {
        "judge": _judge(),
        "experiment_id": "exp-001",
        "contract_digest": "sha256:abc",
        "charter_digest": "sha256:charter",
        "trial_id": "trial-1",
        "blinded_arm_id": "arm-a",
        "raw_response_ref": "artifact:judge-raw:001",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# arm assignment
# ---------------------------------------------------------------------------


def test_assign_arms_is_deterministic_per_experiment():
    first = assign_arms("exp-001")
    second = assign_arms("exp-001")
    assert first == second
    assert isinstance(first, ArmAssignment)


def test_assign_arms_covers_both_arms_and_both_blinded_ids():
    assignment = assign_arms("exp-001")
    assert sorted(assignment.run_order) == [CANDIDATE_ARM, INCUMBENT_ARM]
    assert sorted(assignment.blinded_ids.values()) == sorted(BLINDED_ARM_IDS)
    assert sorted(assignment.blinded_ids.keys()) == [CANDIDATE_ARM, INCUMBENT_ARM]


def test_assign_arms_varies_across_experiments():
    orders = {assign_arms(f"exp-{i:03d}").run_order for i in range(20)}
    assert len(orders) == 2
    mappings = {tuple(sorted(assign_arms(f"exp-{i:03d}").blinded_ids.items())) for i in range(20)}
    assert len(mappings) == 2


def test_arm_assignment_digest_shape_and_stability():
    assignment = assign_arms("exp-001")
    assert assignment.digest.startswith("sha256:")
    assert len(assignment.digest) == len("sha256:") + 64
    int(assignment.digest.split(":")[1], 16)
    assert assign_arms("exp-001").digest == assignment.digest
    assert assign_arms("exp-002").digest != assignment.digest


# ---------------------------------------------------------------------------
# identity tokens derive from the experiment record
# ---------------------------------------------------------------------------


def test_identity_tokens_come_from_the_record():
    tokens = identity_tokens(_experiment())
    assert "agent/memory_retrieval.py" in tokens
    assert "tools/memory_search" in tokens
    assert "candidate/add-rerank" in tokens
    assert "reranking improves recall" in tokens


def test_identity_tokens_accept_list_surfaces_and_object_experiments():
    exp = {"candidate_surfaces": ["a.py"], "manifest": None}
    assert "a.py" in identity_tokens(exp)


def test_identity_tokens_empty_for_bare_record():
    assert identity_tokens(SimpleNamespace()) == []
    assert identity_tokens({}) == []


def test_identity_tokens_include_operator_supplied_list():
    exp = _experiment(identity_tokens=["codename-nightingale"])
    assert "codename-nightingale" in identity_tokens(exp)


def test_identity_tokens_never_include_envelope_plumbing():
    tokens = identity_tokens(_experiment())
    assert "exp-001" not in tokens
    assert "sha256:abc" not in tokens


# ---------------------------------------------------------------------------
# scan_for_identity
# ---------------------------------------------------------------------------


def test_clean_envelope_scans_clean():
    envelope = serialize_envelope(wrap_judge_envelope(**_envelope_kwargs()))
    scan = scan_for_identity(envelope, _experiment())
    assert scan.leaked is False
    assert scan.hits == ()


def test_identity_leak_is_found_and_named():
    kwargs = _envelope_kwargs(
        judge=_judge(reasoning_summary="touches agent/memory_retrieval.py, looks good")
    )
    envelope = serialize_envelope(wrap_judge_envelope(**kwargs))
    scan = scan_for_identity(envelope, _experiment())
    assert scan.leaked is True
    assert "agent/memory_retrieval.py" in scan.hits


def test_scan_on_empty_envelope_returns_no_leak():
    assert scan_for_identity("", _experiment()).leaked is False


def test_scan_with_whitespace_only_token_raises():
    with pytest.raises(ValueError):
        scan_for_identity("some envelope", {"candidate_surfaces": ["   "]})


def test_scan_skips_empty_tokens():
    scan = scan_for_identity("anything", {"candidate_surfaces": ["", "a.py"]})
    assert scan.leaked is False


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------


def test_envelope_carries_all_wrapper_fields():
    envelope = wrap_judge_envelope(**_envelope_kwargs())
    assert envelope["experiment_id"] == "exp-001"
    assert envelope["contract_digest"] == "sha256:abc"
    assert envelope["charter_digest"] == "sha256:charter"
    assert envelope["evaluator_version"] == EVALUATOR_VERSION
    assert envelope["trial_id"] == "trial-1"
    assert envelope["blinded_arm_id"] == "arm-a"
    assert envelope["raw_response_ref"] == "artifact:judge-raw:001"
    assert envelope["judge"]["judge_id"] == "serves-charter"


def test_inner_dict_flows_through_consensus_unchanged():
    envelope = wrap_judge_envelope(**_envelope_kwargs())
    first = compute_consensus([envelope["judge"]])
    second = compute_consensus([_judge()])
    assert _without_timestamps(first) == _without_timestamps(second)


def _without_timestamps(consensus):
    cleaned = {k: v for k, v in consensus.items() if k != "decided_at"}
    nested = cleaned.get("consensus")
    if isinstance(nested, dict):
        cleaned["consensus"] = {k: v for k, v in nested.items() if k != "decided_at"}
    return cleaned


def test_envelope_coerces_untrusted_judge_data():
    envelope = wrap_judge_envelope(
        **_envelope_kwargs(
            judge=_judge(verdict="  approved w/ nits ", blockers="2", confidence=9.5)
        )
    )
    assert envelope["judge"]["verdict"] == "APPROVED"
    assert envelope["judge"]["blockers"] == 2
    assert envelope["judge"]["confidence"] == 1.0


def test_envelope_preserves_extra_judge_keys():
    envelope = wrap_judge_envelope(
        **_envelope_kwargs(judge=_judge(reasoning_summary="ok", meta={"m": 1}))
    )
    assert envelope["judge"]["reasoning_summary"] == "ok"
    assert envelope["judge"]["meta"] == {"m": 1}


def test_envelope_rejects_true_arm_identity_in_blinded_slot():
    with pytest.raises(ValueError):
        wrap_judge_envelope(**_envelope_kwargs(blinded_arm_id="candidate"))
    with pytest.raises(ValueError):
        wrap_judge_envelope(**_envelope_kwargs(blinded_arm_id=""))


def test_envelope_rejects_missing_judge_id_and_empty_charter_digest():
    with pytest.raises(ValueError):
        wrap_judge_envelope(**_envelope_kwargs(judge=_judge(judge_id=" ")))
    with pytest.raises(ValueError):
        wrap_judge_envelope(**_envelope_kwargs(charter_digest=""))


def test_envelope_module_never_touches_the_charter_model():
    import pathlib
    import subprocess

    from tools.improvement_eval import envelope

    assert "improvement_charter" not in pathlib.Path(envelope.__file__).read_text()
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import tools.improvement_eval.envelope, sys; "
            "sys.exit(0 if 'models.improvement_charter' in sys.modules else 1)",
        ],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 1


def test_serialize_envelope_is_canonical():
    first = serialize_envelope(wrap_judge_envelope(**_envelope_kwargs()))
    second = serialize_envelope(wrap_judge_envelope(**_envelope_kwargs()))
    assert first == second
    assert json.loads(first)["judge"]["blockers"] == 0


def test_coerce_judge_dict_rejects_non_mapping():
    with pytest.raises(ValueError):
        coerce_judge_dict(["not", "a", "dict"])
