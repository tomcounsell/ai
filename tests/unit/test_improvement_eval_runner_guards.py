"""Runner guard tests for tools/improvement_eval/runner.py (#3216, task 6).

The second half of the runner suite: blinding on a queried row, the Race 1b
wedge repair, the contract digest, and the single-writer invariant. It lives
apart from ``test_improvement_eval_runner.py`` because nearly every test here
drives two private ``redis-server`` arms and the whole set takes over ten
minutes on one worker; under ``--dist loadfile`` one file lands on one xdist
worker, and a lone worker that long trips ``scripts/pytest-clean.sh``'s
idle-controller wedge guard. Two files keep each below the guard's window.
``TestArmParamAllowlist`` and ``TestArmJobPassThroughs`` are the exceptions:
the first calls ``_retrieve_job`` in-process against a mocked export, the
second runs ``handle_job`` in-process with ``retrieve_memories`` replaced by
a spy, and neither spawns an arm.

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
    _seed_memory,
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


class TestArmParamAllowlist:
    """Only ``ARM_PARAM_KEYS`` reach the arm worker's job spec (in-process, no arms)."""

    def test_limit_is_forwarded(self):
        export = mock.Mock(jsonl_text="x")
        job = runner._retrieve_job(export, PK, {"query_text": "q"}, {"limit": 2})
        assert job["limit"] == 2
        assert job["mode"] == "retrieve"

    def test_clock_skew_in_a_protocol_arm_dict_is_refused(self):
        # The clock-gap lever is run_arm_job's keyword, never a contract input:
        # a frozen protocol naming it must fail the run, not skew one arm.
        export = mock.Mock(jsonl_text="x")
        with pytest.raises(InfraFailure, match="clock_skew_s"):
            runner._retrieve_job(
                export, PK, {"query_text": "q"}, {"limit": 2, "clock_skew_s": 2592000}
            )

    def test_mode_override_is_refused(self):
        export = mock.Mock(jsonl_text="x")
        with pytest.raises(InfraFailure, match="mode"):
            runner._retrieve_job(export, PK, {"query_text": "q"}, {"mode": "restore"})

    def test_rrf_keys_are_forwarded_and_retrieval_mode_is_refused(self):
        """Lane 5 (#3217) widens the allowlist to the retrieval-parameter
        envelope, and no further: ``retrieval_mode`` is an environment
        setting the arena pins, never a contract input. Lane 5b (#3311)
        adds the agent-manifest slice to the union; each mode's validator
        still admits only its own keys (see TestCrossModeKeys in
        test_improvement_eval_agent_run.py)."""
        export = mock.Mock(jsonl_text="x")
        job = runner._retrieve_job(
            export, PK, {"query_text": "q"}, {"limit": 2, "rrf_k": 30, "min_rrf_score": 0.1}
        )
        assert job["rrf_k"] == 30
        assert job["min_rrf_score"] == 0.1
        assert runner.RETRIEVAL_PARAM_KEYS == frozenset({"limit", "rrf_k", "min_rrf_score"})
        assert runner.ARM_PARAM_KEYS == runner.RETRIEVAL_PARAM_KEYS | runner.AGENT_PARAM_KEYS
        with pytest.raises(InfraFailure, match="retrieval_mode"):
            runner._validate_arm_params("candidate", {"limit": 2, "retrieval_mode": "hybrid"})
        with pytest.raises(InfraFailure, match="retrieval_mode"):
            runner._retrieve_job(export, PK, {"query_text": "q"}, {"retrieval_mode": "current"})


class TestArmJobPassThroughs:
    """``handle_job`` forwards ``rrf_k``/``min_rrf_score`` only when the job
    carries them (lane 5, #3217). Runs ``handle_job`` in-process against the
    test db with ``retrieve_memories`` replaced by a spy; no arm is spawned."""

    @staticmethod
    def _run(job_extra: dict) -> dict:
        from tools.improvement_eval import arm_worker, writer_guard
        from tools.improvement_eval.corpus import export_corpus

        _seed_memory(PK, "pass-through probe memory")
        export = export_corpus(PK)
        seen: dict = {}

        def spy(query_text, project_key, **kwargs):
            seen.update(kwargs)
            return []

        job = {
            "mode": "retrieve",
            "jsonl": export.jsonl_text,
            "project_key": PK,
            "query_text": "anything",
            **job_extra,
        }
        try:
            with mock.patch("agent.memory_retrieval.retrieve_memories", spy):
                arm_worker.handle_job(job)
        finally:
            writer_guard.disarm()
        return seen

    def test_absent_keys_leave_retrieve_memories_at_its_defaults(self):
        seen = self._run({"limit": 3})
        assert seen == {"limit": 3}

    def test_present_keys_are_forwarded(self):
        seen = self._run({"limit": 3, "rrf_k": 45, "min_rrf_score": 0.25})
        assert seen == {"limit": 3, "rrf_k": 45, "min_rrf_score": 0.25}

    def test_non_numeric_pass_through_is_a_harness_error(self):
        with pytest.raises(InfraFailure, match="rrf_k"):
            self._run({"rrf_k": "many"})

    def test_retrieve_ranked_ids_forwards_only_non_none_values(self):
        from tools.improvement_eval.retrieval import retrieve_ranked_ids

        seen: list[dict] = []

        def spy(query_text, project_key, **kwargs):
            seen.append(kwargs)
            return [mock.Mock(memory_id="m1")]

        with mock.patch("agent.memory_retrieval.retrieve_memories", spy):
            assert retrieve_ranked_ids("q", PK, limit=4) == ["m1"]
            assert retrieve_ranked_ids("q", PK, limit=4, rrf_k=None, min_rrf_score=None) == ["m1"]
            assert retrieve_ranked_ids("q", PK, limit=4, rrf_k=9) == ["m1"]
            assert retrieve_ranked_ids("q", PK, min_rrf_score=0.5) == ["m1"]
        assert seen == [
            {"limit": 4},
            {"limit": 4},
            {"limit": 4, "rrf_k": 9},
            {"limit": 10, "min_rrf_score": 0.5},
        ]

    def test_handle_job_source_never_names_retrieval_mode(self):
        import inspect

        from tools.improvement_eval import arm_worker

        src = inspect.getsource(arm_worker.handle_job)
        assert "rrf_k" in src and "min_rrf_score" in src
        assert "retrieval_mode" not in src


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
