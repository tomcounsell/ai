"""Runner guard tests for tools/improvement_eval/runner.py (#3216, task 6).

The second half of the runner suite: blinding on a queried row, the Race 1b
wedge repair, the contract digest, and the single-writer invariant. It lives
apart from ``test_improvement_eval_runner.py`` because every runner test
drives two private ``redis-server`` arms and the whole set takes over ten
minutes on one worker; under ``--dist loadfile`` one file lands on one xdist
worker, and a lone worker that long trips ``scripts/pytest-clean.sh``'s
idle-controller wedge guard. Two files keep each below the guard's window.

Fixtures and helpers come from ``improvement_eval_runner_support``.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from models.improvement_evaluation import ImprovementEvaluation
from tests.unit.improvement_eval_runner_support import (  # noqa: F401 -- fixtures
    APPROVING_JUDGE_DICT,
    PK,
    _accepting_protocol,
    _approving_judge,
    _evaluate,
    _freeze,
    _reload,
    _reload_evaluation,
    charter_fixture,
    corpus_fixture,
)
from tools.improvement_eval import runner
from tools.improvement_eval.errors import InfraFailure

# ---------------------------------------------------------------------------
# Blinding
# ---------------------------------------------------------------------------


class TestBlinding:
    def test_identity_leak_sets_blinded_false(self, charter, corpus):
        # A judge legitimately sees the trial id; an operator who lists it as
        # candidate identity has built a leak, and the run must say so.
        experiment = _freeze(
            _accepting_protocol(corpus),
            candidate_surfaces=json.dumps(["trial-one"]),
        )
        evaluation = _reload_evaluation(_evaluate(experiment))

        assert evaluation.blinded is False  # hydrated through Field(type=bool)
        assert "blinding leak" in evaluation.notes
        assert "trial-one" in evaluation.notes
        # recorded, not suppressed: the measurement still completed
        assert evaluation.verdict == "accept"

    def test_manifest_branch_leak_on_a_queried_record_sets_blinded_false(self, charter, corpus):
        """The manifest is a ContentField: the scan reads its content, never its $CF: reference."""
        experiment = _freeze(
            _accepting_protocol(corpus), manifest_extra={"branch": "candidate/add-rerank"}
        )
        assert experiment.manifest.startswith("$CF:")  # what the runner's real input looks like

        def _leaking_judge(candidate_output, *, blinded_arm_id, trial_id):
            judge = {**APPROVING_JUDGE_DICT, "reasoning_summary": "candidate/add-rerank ranks well"}
            return {"status": "ok", "judge": judge}

        evaluation = _reload_evaluation(_evaluate(experiment, judges=[_leaking_judge]))

        assert evaluation.blinded is False
        assert "candidate/add-rerank" in evaluation.notes
        assert evaluation.verdict == "accept"

    def test_clean_run_records_blinded_true_not_null(self, charter, corpus):
        evaluation = _reload_evaluation(_evaluate(_freeze(_accepting_protocol(corpus))))
        assert evaluation.blinded is True

    def test_judges_never_see_the_true_arm_identity(self, charter, corpus):
        seen = []

        def _spy(candidate_output, *, blinded_arm_id, trial_id):
            seen.append((candidate_output, blinded_arm_id))
            return _approving_judge(
                candidate_output, blinded_arm_id=blinded_arm_id, trial_id=trial_id
            )

        _evaluate(_freeze(_accepting_protocol(corpus)), judges=[_spy])
        assert seen
        for output, arm_id in seen:
            assert arm_id in {"arm-a", "arm-b"}
            assert "candidate" not in output
            assert "incumbent" not in output


# ---------------------------------------------------------------------------
# Race 1 / 1b: the running state and its repair
# ---------------------------------------------------------------------------


class TestWedgedExperiment:
    def test_crashed_run_leaves_a_documented_repair(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus))
        experiment.state = "running"
        experiment.save()

        broken = _evaluate(experiment)
        assert broken.verdict == "infra_failure"
        assert "'running'" in broken.notes
        assert broken.verdict not in {"accept", "reject"}
        assert _reload(experiment.id).state == "running"  # the loser touches nothing

        repaired = runner.repair_wedged_experiment(project_key=PK, experiment_id=str(experiment.id))
        assert repaired.state == "frozen"
        assert _reload(experiment.id).state == "frozen"

        retried = _evaluate(_reload(experiment.id))
        assert "Gate 0" not in (retried.notes or "")
        assert retried.verdict == "accept"

    def test_repair_of_a_missing_experiment_raises(self):
        with pytest.raises(LookupError):
            runner.repair_wedged_experiment(project_key=PK, experiment_id="no-such-id")

    def test_docstring_shows_the_same_repair_call(self):
        assert "repair_wedged_experiment(project_key=project_key, experiment_id=experiment_id)" in (
            runner.__doc__ or ""
        )


# ---------------------------------------------------------------------------
# Contract digest and endpoints
# ---------------------------------------------------------------------------


class TestContract:
    def test_digest_is_crlf_normalized_and_field_sensitive(self):
        base = mock.Mock(
            hypothesis="h",
            mechanism="m\nline",
            falsifier="f",
            candidate_surfaces="[]",
            manifest="{}",
        )
        crlf = mock.Mock(
            hypothesis="h",
            mechanism="m\r\nline",
            falsifier="f",
            candidate_surfaces="[]",
            manifest="{}",
        )
        changed = mock.Mock(
            hypothesis="h2",
            mechanism="m\nline",
            falsifier="f",
            candidate_surfaces="[]",
            manifest="{}",
        )
        assert runner.compute_contract_digest(base) == runner.compute_contract_digest(crlf)
        assert runner.compute_contract_digest(base) != runner.compute_contract_digest(changed)
        assert runner.compute_contract_digest(base).startswith("sha256:")

    def test_unknown_endpoint_is_a_harness_error(self):
        with pytest.raises(InfraFailure):
            runner.score_endpoint("ndcg_at_5", ["a"], "a")
        with pytest.raises(InfraFailure):
            runner.score_endpoint("recall_at_zero", ["a"], "a")
        assert runner.score_endpoint("recall_at_1", ["a", "b"], "b") == 0.0
        assert runner.score_endpoint("mrr", ["a", "b"], "b") == 0.5

    def test_manifest_without_protocol_ref_is_infra_failure(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus), manifest=json.dumps({"base": "x"}))
        evaluation = _evaluate(experiment)
        assert evaluation.verdict == "infra_failure"
        assert "protocol_ref" in evaluation.notes


class TestSingleWriter:
    def test_one_evaluation_row_per_run(self, charter, corpus):
        before = len(list(ImprovementEvaluation.query.filter(project_key=PK)))
        _evaluate(_freeze(_accepting_protocol(corpus)))
        after = len(list(ImprovementEvaluation.query.filter(project_key=PK)))
        assert after == before + 1
