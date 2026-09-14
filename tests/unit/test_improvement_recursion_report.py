"""The claim report (lane 6, #3218).

Three ladder levels, each supported or not with its interval, correction,
falsifier, and why-not; each level degrades on its own; and no count of
experiments, patches, or releases anywhere. Level 2 is seeded with a
lane-4-shaped evaluation row (``runner.py::_write_evaluation``'s exact string
expressions) and an accepted release; level 3 with a comparison row written
by ``compare.run`` under the real ledger reader, whose unit 1 is unmetered.
Rows land in the claimed test DB (autouse ``redis_test_db``,
tests/conftest.py) under a test-scoped ``project_key``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_experiment import ImprovementExperiment
from models.improvement_release import ImprovementRelease
from tools.improvement_eval.runner import freeze_protocol
from tools.improvement_recursion import report as report_module
from tools.improvement_recursion.arms import BudgetCap, BudgetUse, ReplayArm, ReplayArmRunner
from tools.improvement_recursion.compare import freeze, run
from tools.improvement_recursion.report import claim_report, render

PK = "test-3218-report"
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
INTERVAL = {"lower": 0.02, "upper": 0.22, "n": 12, "raw_p_value": 0.01, "adjusted_p_value": 0.02}
FALSIFIER = "architectural correction rate over a later 7-day window exceeds the baseline band"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
CAP = BudgetCap(unit1_usd=10.0, unit3_usd=10.0, subscription_turns=100, wall_seconds=3600)
FORBIDDEN_TOKENS = ("experiment_count", "patch_count", "merged_patch", "len(experiments)")
COUNT_KEY = re.compile(r"count|total|number|num_", re.IGNORECASE)


@pytest.fixture
def charter(tmp_path):
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _experiment(*, case_id=None, state="complete") -> ImprovementExperiment:
    ref = freeze_protocol({"primary_endpoint": "primary", "endpoints": ["primary"]})
    row = ImprovementExperiment(
        project_key=PK,
        created_at=NOW,
        state=state,
        case_id=case_id,
        hypothesis="a wider candidate finds the gold memory",
        falsifier="recall_at_2 does not rise",
        contract_digest="sha256:" + "c" * 64,
        candidate_surfaces=json.dumps(["tools/x.py"]),
        manifest=json.dumps({"protocol_ref": ref, "base_revision": "abc123"}),
    )
    assert row.save() is not False
    return row


def _lane4_evaluation(charter_row, experiment, *, effect=0.12) -> ImprovementEvaluation:
    row = ImprovementEvaluation(
        project_key=PK,
        created_at=NOW,
        state="complete",
        verdict="accept",
        experiment_id=str(experiment.id),
        contract_digest=experiment.contract_digest,
        charter_digest=charter_row.digest,
        evaluator_version="frozen-holdout/1",
        # runner.py:499-508's exact expressions
        effect=json.dumps({"primary": effect}, sort_keys=True),
        confidence_interval=json.dumps({"primary": INTERVAL}, sort_keys=True),
        correction="holm; fixed-batch(n=2, endpoints=1)",
        notes="\n".join(["accept"]),
        judge_records="",
    )
    assert row.save() is not False
    return row


def _release(evaluation, *, state="accepted", supported=True, reason="held") -> ImprovementRelease:
    outcome = {
        "verdict": "held" if supported else "regressed",
        "reason": reason,
        "falsifier": FALSIFIER,
        "claim_level_2_supported": supported,
        "history": [],
    }
    return ImprovementRelease.create(
        project_key=PK,
        created_at=NOW,
        state=state,
        kind="core_workflow",
        evaluation_id=str(evaluation.id),
        surfaces=json.dumps(["tools/x.py"]),
        candidate_ref="session/candidate",
        outcome=json.dumps(outcome),
    )


def _settings():
    return SimpleNamespace(
        improvement=SimpleNamespace(
            weekly_infrastructure_usd=50.0, budget_week_start="monday", budget_day_boundary="UTC"
        )
    )


def _comparison(gains_b=(0.5, 0.6, 0.5, 0.4)) -> ImprovementEvaluation:
    """A comparison row as ``compare.run`` writes it under the unmetered unit-1 reader."""
    case_ids = [
        ImprovementCase.create(
            project_key=PK, state="observed", priority_area=area, created_at=datetime.now(UTC)
        ).id
        for area in ("memory", "skills", "memory", "inference")
    ]
    experiment = freeze(
        arm_a=DIGEST_A, arm_b=DIGEST_B, opportunity_ids=case_ids, budget_cap=CAP, project_key=PK
    )
    use = BudgetUse(unit3_usd=1.0, subscription_turns=10, wall_seconds=100)
    runner = ReplayArmRunner(
        {
            DIGEST_A: ReplayArm(gains=dict.fromkeys(case_ids, 0.1), budget_use=use),
            DIGEST_B: ReplayArm(gains=dict(zip(case_ids, gains_b, strict=True)), budget_use=use),
        },
        project_key=PK,
        settings=_settings(),
        now=NOW,
    )
    return run(experiment.id, runner=runner, rng_seed=1, project_key=PK)


def _walk(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield (*path, key), child
            yield from _walk(child, (*path, key))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from _walk(child, (*path, i))


# ---------------------------------------------------------------------------
# Shape and degradation
# ---------------------------------------------------------------------------


def test_empty_project_reports_three_unsupported_levels():
    report = claim_report(PK, now=NOW)
    assert report["project_key"] == PK
    assert set(report["levels"]) == {1, 2, 3}
    for level in (1, 2, 3):
        entry = report["levels"][level]
        assert set(entry) == {
            "name",
            "supported",
            "evidence",
            "confidence_interval",
            "correction",
            "falsifier",
            "why_not",
        }
        assert entry["supported"] is False
        assert entry["evidence"] == []
        assert entry["confidence_interval"] is None
        assert isinstance(entry["why_not"], str) and entry["why_not"].strip()
    assert report["levels"][1]["why_not"] == "no complete cycle recorded"
    assert report["levels"][2]["why_not"] == "no accepted release recorded"
    assert report["levels"][3]["why_not"] == "no comparison recorded"


def test_level_1_reads_a_complete_cycle(charter):
    case = ImprovementCase.create(
        project_key=PK, state="rejected", priority_area="memory", created_at=NOW
    )
    experiment = _experiment(case_id=case.id)
    evaluation = _lane4_evaluation(charter, experiment)
    level = claim_report(PK, now=NOW)["levels"][1]
    assert level["supported"] is True
    assert level["why_not"] is None
    assert any(experiment.id in item for item in level["evidence"])
    assert any(evaluation.id in item for item in level["evidence"])
    assert any(case.id in item and "rejected" in item for item in level["evidence"])
    assert level["falsifier"] == "recall_at_2 does not rise"


def test_level_1_needs_the_case_to_have_left_the_open_states(charter):
    case = ImprovementCase.create(
        project_key=PK, state="evaluating", priority_area="memory", created_at=NOW
    )
    _lane4_evaluation(charter, _experiment(case_id=case.id))
    level = claim_report(PK, now=NOW)["levels"][1]
    assert level["supported"] is False
    assert level["why_not"] == "no complete cycle recorded"


def test_level_2_reads_a_lane4_shaped_accepted_release(charter):
    evaluation = _lane4_evaluation(charter, _experiment())
    release = _release(evaluation)
    level = claim_report(PK, now=NOW)["levels"][2]
    assert level["supported"] is True
    assert level["confidence_interval"] == INTERVAL
    assert level["correction"] == "holm; fixed-batch(n=2, endpoints=1)"
    assert level["falsifier"] == FALSIFIER
    assert level["why_not"] is None
    assert any(release.id in item for item in level["evidence"])
    assert any(evaluation.id in item for item in level["evidence"])
    assert any("primary" in item and "0.12" in item for item in level["evidence"])


def test_level_2_accepted_but_unsupported_release_names_why(charter):
    evaluation = _lane4_evaluation(charter, _experiment())
    release = _release(evaluation, supported=False, reason="DETECTION_DECLINED")
    level = claim_report(PK, now=NOW)["levels"][2]
    assert level["supported"] is False
    assert release.id in level["why_not"] and "DETECTION_DECLINED" in level["why_not"]
    assert level["falsifier"] == FALSIFIER


def test_level_2_ignores_releases_that_are_not_accepted(charter):
    _release(_lane4_evaluation(charter, _experiment()), state="observing")
    level = claim_report(PK, now=NOW)["levels"][2]
    assert level["supported"] is False
    assert level["why_not"] == "no accepted release recorded"


def test_level_3_refused_comparison_names_the_budget_reason(charter):
    evaluation = _comparison()
    assert evaluation.verdict == "inconclusive"
    level = claim_report(PK, now=NOW)["levels"][3]
    assert level["supported"] is False
    assert "BUDGET_UNKNOWN:unit1" in level["why_not"]
    assert evaluation.id in level["why_not"]
    assert level["confidence_interval"] is not None
    assert level["confidence_interval"]["n"] == 4
    assert level["correction"] == "holm"
    assert level["falsifier"]


def test_level_3_supported_only_with_accept_and_comparable_budget(charter, monkeypatch):
    evaluation = _comparison()
    monkeypatch.setattr(
        report_module,
        "budget_of",
        lambda row: {"comparable": True, "reasons": [], "a": {}, "b": {}, "cap": {}},
    )
    evaluation.verdict = "accept"
    evaluation.save()
    level = claim_report(PK, now=NOW)["levels"][3]
    assert level["supported"] is True
    assert level["why_not"] is None
    assert any(evaluation.id in item for item in level["evidence"])
    assert any(DIGEST_B in item for item in level["evidence"])


def test_level_3_ignores_lane4_evaluations(charter):
    _release(_lane4_evaluation(charter, _experiment()))
    level = claim_report(PK, now=NOW)["levels"][3]
    assert level["why_not"] == "no comparison recorded"


def test_each_level_degrades_independently(charter, monkeypatch, caplog):
    case = ImprovementCase.create(
        project_key=PK, state="released", priority_area="memory", created_at=NOW
    )
    _lane4_evaluation(charter, _experiment(case_id=case.id))
    _comparison()

    def _boom(project_key):
        raise RuntimeError("release table unreadable")

    monkeypatch.setattr(report_module, "_accepted_releases", _boom)
    with caplog.at_level(logging.WARNING, logger="tools.improvement_recursion.report"):
        report = claim_report(PK, now=NOW)
    assert report["levels"][1]["supported"] is True
    assert report["levels"][2]["supported"] is False
    assert report["levels"][2]["why_not"].startswith("could not be determined: ")
    assert "release table unreadable" in report["levels"][2]["why_not"]
    assert "BUDGET_UNKNOWN:unit1" in report["levels"][3]["why_not"]
    assert any("level 2" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Render and the no-count anti-criterion
# ---------------------------------------------------------------------------


def test_render_prints_the_three_levels_plainly(charter):
    _release(_lane4_evaluation(charter, _experiment()))
    text = render(claim_report(PK, now=NOW))
    assert "Level 1" in text and "Level 2" in text and "Level 3" in text
    assert "supported: yes" in text
    assert "supported: no" in text
    assert "no comparison recorded" in text
    assert "0.02" in text and "0.22" in text
    assert FALSIFIER in text


def test_report_and_render_carry_no_count(charter):
    _release(_lane4_evaluation(charter, _experiment()))
    _comparison()
    report = claim_report(PK, now=NOW)
    text = render(report)
    for token in FORBIDDEN_TOKENS:
        assert token not in text
        assert token not in json.dumps(report, default=str)
    for path, value in _walk(report):
        key = path[-1]
        if isinstance(key, str):
            assert not COUNT_KEY.search(key), f"count-like key at {path}"
        if isinstance(value, int) and not isinstance(value, bool):
            assert key == "n" and "confidence_interval" in path, (
                f"integer at {path} is the interval's sample size or nothing"
            )
    for entry in report["levels"].values():
        assert all(isinstance(item, str) for item in entry["evidence"])
