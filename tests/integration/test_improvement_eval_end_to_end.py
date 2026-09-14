"""End-to-end evaluation through the default judge roster (#3216, task 6).

The unit runner tests inject a fake judge. This test wires the whole
default path instead: twenty retained architectural corrections so
calibration clears its floor and freezes a reference set, the real
``serves-charter`` judge quoting the pinned charter, the envelope wrapper,
and two live arms in private Redis processes. Only the provider transport is
injected, so no network call is made and the verdict is deterministic.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest import mock

import pytest

from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_experiment import ImprovementExperiment
from models.verifying_artifact_store import verifying_artifact_store
from tools.improvement_eval import runner
from tools.improvement_eval.calibration import MIN_REFERENCE_SET_SIZE
from tools.improvement_eval.judges.serves_charter import SERVES_CHARTER_JUDGE_ID

PK = "test3216e2e"

QUERIES = [
    {"trial_id": "e2e-one", "query_text": "zxqvkw qvxj e2e-absent"},
    {"trial_id": "e2e-two", "query_text": "zxqvkw qvxj e2e-absent again"},
]


def _seed_memory(content):
    from models.memory import Memory

    record = Memory(
        agent_id="test-3216", project_key=PK, content=content, importance=5.0, source="agent"
    )
    assert record.save() is not False
    return record


@pytest.fixture
def charter(tmp_path):
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None
    return row


@pytest.fixture
def architectural_evidence():
    from models.improvement_evidence import ImprovementEvidence

    for i in range(MIN_REFERENCE_SET_SIZE):
        row = ImprovementEvidence.record_once(
            PK,
            "correction",
            classification="architectural",
            source_ref=f"e2e-seed-{i}",
            text=f"start over, the whole flow misses the user journey ({i})",
        )
        assert row is not None


def _transport(*, provider, model, prompt):
    """A deterministic provider: refuses what calibration expects, approves arms."""
    serves = "calibration" not in prompt and "start over" not in prompt
    return json.dumps(
        {
            "serves_charter": serves,
            "blockers": 0 if serves else 1,
            "confidence": 0.9,
            "reasoning_summary": "deterministic transport for the end-to-end test",
        }
    )


def test_default_roster_end_to_end(charter, architectural_evidence):
    _seed_memory("e2e lighthouse beacon on the headland")
    _seed_memory("e2e grocery errands for the week")
    from tools.improvement_eval.corpus import export_corpus

    export = export_corpus(PK)
    baseline = runner.capture_baseline(PK, QUERIES, incumbent={"limit": 1}, export=export)
    full = runner.capture_baseline(PK, QUERIES, incumbent={"limit": 10}, export=export)
    queries = [{**q, "gold_id": full["ranked_ids"][q["trial_id"]][-1]} for q in QUERIES]
    protocol_ref = runner.freeze_protocol(
        {
            "batch_size": 2,
            "endpoints": ["recall_at_2", "mrr"],
            "holdout_partition": "epoch-e2e",
            "queries": queries,
            "baseline": baseline,
            "incumbent": {"limit": 1},
            "candidate": {"limit": 10},
        }
    )
    experiment = ImprovementExperiment(
        project_key=PK,
        created_at=datetime.now(UTC),
        hypothesis="a wider candidate finds the gold memory",
        mechanism="a larger limit admits the second-ranked record",
        falsifier="recall_at_2 does not rise",
        candidate_surfaces=json.dumps(["tools/improvement_eval/"]),
        manifest=json.dumps({"protocol_ref": protocol_ref, "base_revision": "e2e"}),
    )
    assert experiment.save() is not False
    experiment = ImprovementExperiment.query.filter(project_key=PK, id=experiment.id).first()
    experiment.contract_digest = runner.compute_contract_digest(experiment)
    experiment.state = "frozen"
    experiment.frozen_at = datetime.now(UTC)
    assert experiment.save() is not False

    with mock.patch("tools.improvement_eligibility.is_open_source", return_value=False):
        evaluation = runner.evaluate(str(experiment.id), PK, judge_complete=_transport)

    assert evaluation.state == "complete"
    assert evaluation.verdict == "accept"
    assert runner.has_verdict(evaluation)
    assert evaluation.blinded is True
    assert evaluation.charter_digest == charter.digest
    records = json.loads(evaluation.judge_records)
    assert len(records) == 4
    for record in records:
        assert record["judge"]["judge_id"] == SERVES_CHARTER_JUDGE_ID
        assert record["judge"]["charter_digest"] == charter.digest
        assert record["judge"]["meta"]["provider"] == "claude-subscription"
        assert record["blinded_arm_id"] in {"arm-a", "arm-b"}
        assert verifying_artifact_store.exists(record["raw_response_ref"])
    assert _artifact_refs("ImprovementCalibration"), "calibration froze no reference set"


def _artifact_refs(model_class_name: str) -> list[str]:
    import os

    root = os.path.join(verifying_artifact_store.base_path, model_class_name)
    if not os.path.isdir(root):
        return []
    return [name for name in os.listdir(root) if name.startswith("calibration-reference-")]
