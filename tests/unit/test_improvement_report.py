"""``tools/improvement_report.py``: ``is_seeded``, the seeded-case scan, and the
qualified-result report with its three mandatory derived sections (lane 5,
#3217, task 6). Rows land in the claimed test db (autouse ``redis_test_db``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_controller_state import ImprovementControllerState
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_evidence import ImprovementEvidence
from models.improvement_experiment import ImprovementExperiment
from models.improvement_investigation import ImprovementInvestigation
from models.improvement_model_revision import ImprovementModelRevision
from models.verifying_artifact_store import VerifyingArtifactStore
from tools import improvement as cli
from tools import improvement_experiment as ex
from tools import improvement_report as report
from tools.improvement_ranking import RankedCase, write_snapshot

PK = "valor"


@pytest.fixture(name="charter")
def charter_fixture(tmp_path):
    pinned = ImprovementCharter.pinned(PK)
    if pinned is not None:
        return pinned
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None
    return row


@pytest.fixture(name="store")
def store_fixture(tmp_path):
    return VerifyingArtifactStore(base_path=str(tmp_path / "artifacts"))


def evidence(**overrides) -> ImprovementEvidence:
    fields = dict(
        project_key=PK,
        created_at=datetime.now(UTC),
        kind="inspiration",
        classification="unknown",
        text="charter §3 names inference first",
    )
    fields.update(overrides)
    return ImprovementEvidence.create(**fields)


def new_case(charter, evidence_rows=(), **overrides) -> ImprovementCase:
    fields = dict(
        project_key=PK,
        state="investigating",
        title="report case",
        summary="summary text",
        created_at=datetime.now(UTC),
        priority_area="inference",
        ranking_rationale="charter §3 names inference first",
        charter_digest=charter.digest,
        dedup_identity="seed:charter-s3:inference",
        evidence_ids=json.dumps([e.id for e in evidence_rows]),
    )
    fields.update(overrides)
    return ImprovementCase.create(**fields)


class TestIsSeeded:
    @pytest.mark.parametrize(
        ("source_ref", "detail", "expected"),
        [
            ("seed:charter-s3:inference", None, True),
            (
                "memory:abc",
                json.dumps({"seed": "charter-s3:inference", "priority_area": "inference"}),
                True,
            ),
            ("memory:abc", json.dumps({"priority_area": "inference"}), False),
            ("memory:abc", "not json", False),
            ("memory:abc", None, False),
            (None, None, False),
            ("seeded-but-not-prefixed", json.dumps([1, 2]), False),
        ],
    )
    def test_single_definition(self, source_ref, detail, expected):
        row = SimpleNamespace(source_ref=source_ref, detail=detail)
        assert report.is_seeded(row) is expected

    def test_marker_text(self):
        assert report.seed_marker(SimpleNamespace(source_ref="seed:x", detail=None)) == "seed:x"
        assert (
            report.seed_marker(
                SimpleNamespace(
                    source_ref="memory:1", detail=json.dumps({"seed": "charter-s3:inference"})
                )
            )
            == "charter-s3:inference"
        )
        assert report.seed_marker(SimpleNamespace(source_ref="memory:1", detail=None)) is None


class TestSeededCases:
    def test_names_only_the_case_with_a_marked_row(self, charter):
        marked = evidence(
            source_ref="memory:1", detail=json.dumps({"seed": "charter-s3:inference"})
        )
        plain = evidence(source_ref="memory:2", detail=json.dumps({"url": "https://example.test"}))
        seeded = new_case(charter, [marked])
        observed = new_case(charter, [plain], dedup_identity="observed cluster")
        found = report.seeded_cases(PK)
        assert found == [(seeded.id, "charter-s3:inference")]
        assert observed.id not in {cid for cid, _ in found}


class TestBuildReport:
    def test_seeded_inputs_line(self, charter, store):
        marked = evidence(source_ref="seed:charter-s3:inference")
        plain = evidence(source_ref="memory:2", detail=json.dumps({"url": "https://example.test"}))
        seeded = new_case(charter, [marked])
        observed = new_case(charter, [plain], dedup_identity="observed cluster")
        text = report.build_report(observed.id, PK, store=store)
        section = report.section(text, "What this does not establish")
        line = next(row for row in section.splitlines() if "seeded" in row.lower())
        assert seeded.id in line
        assert "seed:charter-s3:inference" in line
        assert observed.id not in line

    def test_no_seeded_cases_says_so(self, charter, store):
        plain = evidence(source_ref="memory:2")
        case = new_case(charter, [plain], dedup_identity="observed cluster")
        section = report.section(
            report.build_report(case.id, PK, store=store), "What this does not establish"
        )
        assert "no case" in section.lower() and "seed" in section.lower()

    def test_sections_are_present_in_order_for_a_bare_case(self, charter, store):
        case = new_case(charter, [])
        text = report.build_report(case.id, PK, store=store)
        measured = text.index("## What was measured")
        limits = text.index("## What this does not establish")
        change = text.index("## What would change the answer")
        assert measured < limits < change
        assert case.id in text and charter.digest in text
        assert "No evidence rows" in text
        assert report.section(text, "What this does not establish").strip()

    def test_missing_case_raises_lookup_error(self, store):
        with pytest.raises(LookupError, match="CASE_NOT_FOUND"):
            report.build_report("no-such", PK, store=store)

    def _frozen_with_verdict(self, charter, store, verdict, **eval_extra):
        from tests.unit.test_improvement_experiment import (
            OpenMeter,
            baseline_stub,
            builder_dropping,
            fake_export,
        )

        row = evidence(source_ref="memory:9")
        case = new_case(charter, [row], dedup_identity="report cluster")
        outcome = ex.propose_experiment(
            PK,
            case.id,
            hypothesis="a wider limit finds the gold memory",
            mechanism="limit admits the second-ranked record",
            falsifier="recall_at_5 does not rise by 0.02",
            candidate={"limit": 20},
            envelope="retrieval_parameters",
        )
        assert outcome.accepted, outcome
        frozen = ex.freeze_experiment(
            PK,
            outcome.experiment_id,
            builder=builder_dropping(0),
            exporter=lambda project_key: fake_export(),
            baseline=baseline_stub,
            meter=OpenMeter(),
            store=store,
            seed=11,
        )
        assert frozen.accepted, frozen
        experiment = ImprovementExperiment.query.get(project_key=PK, id=outcome.experiment_id)
        fields = dict(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="complete",
            verdict=verdict,
            experiment_id=experiment.id,
            contract_digest=experiment.contract_digest,
            charter_digest=charter.digest,
            holdout_partition="known-item-11",
            blinded=True,
            trials=30,
            effect=json.dumps({"mrr": 0.05, "recall_at_5": 0.1}),
            confidence_interval=json.dumps(
                {
                    "mrr": {
                        "lower": 0.03,
                        "upper": 0.07,
                        "n": 30,
                        "raw_p_value": 0.001,
                        "adjusted_p_value": 0.002,
                    },
                    "recall_at_5": {
                        "lower": 0.05,
                        "upper": 0.15,
                        "n": 30,
                        "raw_p_value": 0.001,
                        "adjusted_p_value": 0.002,
                    },
                }
            ),
            correction="holm(2)",
            notes=(
                'calibration: {"kappa": 0.8, "size": 12}\n'
                "all endpoints cleared: ['mrr', 'recall_at_5']"
            ),
        )
        fields.update(eval_extra)
        evaluation = ImprovementEvaluation.create(**fields)
        applied = ex.apply_verdict(PK, evaluation.id)
        assert applied.accepted, applied
        return case, experiment, evaluation

    def test_accept_verdict_still_has_a_non_empty_limits_section(self, charter, store):
        case, experiment, evaluation = self._frozen_with_verdict(charter, store, "accept")
        ImprovementEvidence.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            kind="spend_receipt",
            classification="unknown",
            source_ref="unit2-known_item_generation-abc",
            detail=json.dumps(
                {
                    "unit": 2,
                    "purpose": "known_item_generation",
                    "metering": "estimated",
                    "usd": 0.06,
                    "case_id": case.id,
                }
            ),
        )
        text = report.build_report(case.id, PK, store=store)
        limits = report.section(text, "What this does not establish")
        assert limits.strip()
        assert "loop operational" in limits
        assert "30" in limits and "0.02" in limits
        assert "agent behavior" in limits
        assert "estimated" in limits and "known_item_generation" in limits
        assert "identity" in limits.lower()
        measured = report.section(text, "What was measured")
        assert "recall_at_5" in measured and "mrr" in measured
        assert "known-item-11" in measured
        assert "30" in measured
        assert "sha256:" in measured
        change = report.section(text, "What would change the answer")
        assert "recall_at_5 does not rise by 0.02" in change
        assert "larger sample" in change.lower()
        assert "accept" in text and evaluation.id in text and experiment.contract_digest in text
        assert "blinded: True" in text or "blinded=True" in text
        assert "kappa" in text

    def test_records_are_rendered_in_the_plan_order(self, charter, store):
        case, experiment, evaluation = self._frozen_with_verdict(charter, store, "reject")
        ImprovementInvestigation.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            kind="web_research",
            state="resolved",
            case_id=case.id,
            query="what does the retrieval limit default to",
            claims=json.dumps(
                {
                    "claims": [
                        {
                            "claim": "the default limit is 10",
                            "url": "https://example.test/doc",
                            "retrieved_at": "2026-09-14T10:00:00+00:00",
                        }
                    ],
                    "notes": [],
                }
            ),
            provisional_assumption="the corpus is representative",
            assumption_detail=json.dumps(
                {"overturning_observation": "a second corpus ranks differently"}
            ),
            interpretation="the limit is 10 by default",
        )
        ImprovementModelRevision.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="current",
            summary="retrieval misses second-ranked memories",
            rationale="observed on the seeded corpus",
            prediction="recall_at_5 rises with limit 20",
            evidence_ids=json.dumps(json.loads(case.evidence_ids)),
        )
        ranked = [
            RankedCase(
                case_id=case.id, position=1, factors={"quality": 1}, reason="r", blocked_by=None
            )
        ]
        ref = write_snapshot(ranked, previous_ref=None, charter_digest=charter.digest, store=store)
        ImprovementControllerState.get_or_create(PK).record(last_snapshot_ref=ref)
        text = report.build_report(case.id, PK, store=store)
        order = [
            text.index("## Case"),
            text.index("## Ranking positions"),
            text.index("## Investigations"),
            text.index("## Model revisions"),
            text.index("## Experiment"),
            text.index("## Evaluation"),
            text.index("## What was measured"),
            text.index("## What this does not establish"),
            text.index("## What would change the answer"),
        ]
        assert order == sorted(order)
        assert "position 1" in text
        assert "https://example.test/doc" in text and "2026-09-14" in text
        assert "the corpus is representative" in text
        assert "recall_at_5 rises with limit 20" in text
        assert '{"limit": 20}' in text and '{"limit": 10}' in text
        assert "a second corpus ranks differently" in report.section(
            text, "What would change the answer"
        )
        assert "rejected" in text

    def test_cli_prints_the_report(self, charter, store, capsys, monkeypatch):
        case = new_case(charter, [], dedup_identity="cli cluster")
        monkeypatch.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", store.base_path)
        code = cli.main(["--json", "report", "--case", case.id])
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 0
        assert payload["case_id"] == case.id
        assert "## What this does not establish" in payload["report"]
        code = cli.main(["report", "--case", "no-such"])
        out = capsys.readouterr().out
        assert code == 1 and "CASE_NOT_FOUND" in out
