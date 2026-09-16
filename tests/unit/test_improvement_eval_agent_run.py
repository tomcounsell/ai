"""Tests for the agent_run arm job mode (#3311, Task 1), the runner's
agent-trial branch (#3311, Task 2), and agent-trial spend plumbing
(#3311, Task 3).

Task 1 covers mode dispatch (one bounded trial per task plus the candidate
manifest), the teardown digest parity check (a session write to the
frozen corpus invalidates the arm), and the pre-judge identity scan
row (a manifest model string in the envelope makes scan_for_identity
fire through the existing manifest IDENTITY_FIELD).

Task 2 covers the runner branch: agent trials dispatch the agent_run arm
per trial and score outcomes through the JudgeFn roster, the serialized
agent-run envelope passes the pre-judge scan with the candidate manifest
in the identity dict (a hit records, never fails), Gate 1 compares
incumbent outcomes against the recorded baseline under a frozen
per-task tolerance, and each mode's validator rejects the other's keys.

Task 3 covers spend: each agent trial reserves its task budget against
unit 2 pre-trial (with the ``arm:<arm_run_id>:`` resource prefix) and
settles post-trial; a refused reservation is a harness error counting
toward ``infra_failure_cap``, never a scored zero.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from tools.improvement_eval.errors import InfraFailure

PK_AGENT_RUN = "test3311agentrun"
MODEL_TOKEN = "spark-test-model-3311"


@pytest.fixture(autouse=True)
def _disarmed_guard():
    """``handle_job`` arms the writer guard in-process; release it per test."""
    from tools.improvement_eval import writer_guard

    writer_guard.disarm()
    yield
    writer_guard.disarm()


@pytest.fixture(autouse=True)
def _open_source_project(monkeypatch):
    """These tests exercise the trial path, not the eligibility gate.

    The gate itself is covered in ``test_improvement_eligibility.py``; here
    every fake project reads as open-source so trials reach the arm.
    """
    monkeypatch.setattr("tools.improvement_eligibility.is_open_source", lambda project_key: True)


def _seed_memory(project_key, content):
    from models.memory import Memory

    record = Memory(
        agent_id="test-3311",
        project_key=project_key,
        content=content,
        importance=5.0,
        source="agent",
    )
    assert record.save() is not False
    return record


def _export(project_key):
    from tools.improvement_eval.corpus import export_corpus

    return export_corpus(project_key)


def _job(export, **overrides):
    job = {
        "mode": "agent_run",
        "jsonl": export.jsonl_text,
        "project_key": PK_AGENT_RUN,
        "tasks": [{"id": "t1", "prompt": "summarize the lighthouse log"}],
        "manifest": {"model": MODEL_TOKEN, "skill": "s1", "persona": "p1"},
        "bounds": {"timeout_s": 30, "max_turns": 2, "spend_cap": 1.0},
    }
    job.update(overrides)
    return job


class TestAgentRunDispatch:
    def test_agent_run_is_a_known_mode(self):
        from tools.improvement_eval import arm_worker

        assert "agent_run" in arm_worker.JOB_MODES

    def test_runs_one_trial_per_task_and_returns_manifest(self):
        from tools.improvement_eval import arm_worker

        _seed_memory(PK_AGENT_RUN, "agent run dispatch probe")
        export = _export(PK_AGENT_RUN)
        job = _job(
            export,
            tasks=[
                {"id": "t1", "prompt": "first"},
                {"id": "t2", "prompt": "second"},
            ],
        )
        calls = []

        def _fake_trial(task, manifest, bounds, project_key):
            calls.append((task["id"], manifest["model"], bounds["timeout_s"]))
            return {"task_id": task["id"], "output": f"out-{task['id']}"}

        with mock.patch.object(arm_worker, "run_agent_trial", _fake_trial):
            response = arm_worker.handle_job(job)
        assert [t["task_id"] for t in response["trials"]] == ["t1", "t2"]
        assert response["candidate_manifest"] == job["manifest"]
        assert response["digest"] == export.digest
        assert response["manifest"] == export.manifest_canon
        assert [c[0] for c in calls] == ["t1", "t2"]

    def test_empty_tasks_refused_before_restore(self):
        from tools.improvement_eval import arm_worker

        _seed_memory(PK_AGENT_RUN, "agent run empty tasks probe")
        export = _export(PK_AGENT_RUN)
        with pytest.raises(InfraFailure, match="tasks"):
            arm_worker.handle_job(_job(export, tasks=[]))

    def test_manifest_missing_model_refused(self):
        from tools.improvement_eval import arm_worker

        _seed_memory(PK_AGENT_RUN, "agent run manifest probe")
        export = _export(PK_AGENT_RUN)
        with pytest.raises(InfraFailure, match="model"):
            arm_worker.handle_job(_job(export, manifest={"skill": "s1"}))


class TestAgentRunTeardown:
    def test_session_write_to_corpus_invalidates_the_arm(self):
        """A trial write the wrapper never saw still fails at the digest re-check."""
        from tools.improvement_eval import arm_worker, writer_guard

        _seed_memory(PK_AGENT_RUN, "agent run escaped write probe")
        export = _export(PK_AGENT_RUN)

        def _writing_trial(task, manifest, bounds, project_key):
            _seed_memory(project_key, "escaped session write landed")
            return {"task_id": task["id"], "output": "wrote anyway"}

        job = _job(export)
        with (
            mock.patch.object(writer_guard, "arm", lambda: None),
            mock.patch.object(arm_worker, "run_agent_trial", _writing_trial),
        ):
            with pytest.raises(InfraFailure, match="corpus digest changed"):
                arm_worker.handle_job(job)
        assert not writer_guard.is_armed()


class TestAgentRunIdentityScan:
    def test_manifest_model_in_envelope_fires_the_scan(self):
        """The candidate manifest rides the existing manifest IDENTITY_FIELD."""
        from tools.improvement_eval.blinding import scan_for_identity
        from tools.improvement_eval.envelope import serialize_envelope, wrap_judge_envelope

        experiment = {"manifest": json.dumps({"model": MODEL_TOKEN})}
        envelope = serialize_envelope(
            wrap_judge_envelope(
                judge={
                    "judge_id": "rubric-t1",
                    "verdict": "APPROVED",
                    "blockers": 0,
                    "confidence": 0.8,
                    "reasoning_summary": f"session used {MODEL_TOKEN} well",
                },
                experiment_id="exp-3311",
                contract_digest="sha256:abc",
                charter_digest="sha256:charter",
                trial_id="t1",
                blinded_arm_id="arm-a",
                raw_response_ref="artifact:judge-raw:3311",
            )
        )
        scan = scan_for_identity(envelope, experiment)
        assert scan.leaked is True
        assert MODEL_TOKEN in scan.hits


# ---------------------------------------------------------------------------
# Task 2: the runner's agent-trial branch
# ---------------------------------------------------------------------------

PK_RUNNER = "test3311agentrunner"
INCUMBENT_MODEL = "prior-model-3311"

AGENT_TASKS = [
    {"id": "t1", "prompt": "summarize the lighthouse log"},
    {"id": "t2", "prompt": "summarize the grocery list"},
]

INCUMBENT_PARAMS = {
    "model": INCUMBENT_MODEL,
    "skill": "skill-prior-3311",
    "persona": "persona-prior-3311",
    "prompt_hash": "sha256:prompt-prior-3311",
    "bounds": {"timeout_s": 30, "max_turns": 2, "spend_cap": 1.0},
}

CANDIDATE_PARAMS = {
    "model": MODEL_TOKEN,
    "skill": "skill-acquire-3311",
    "persona": "persona-scout-3311",
    "prompt_hash": "sha256:prompt-candidate-3311",
    "bounds": {"timeout_s": 30, "max_turns": 2, "spend_cap": 1.0},
}


def _trial_outcome(task_id, output, passed=True, model=MODEL_TOKEN):
    return {
        "task_id": task_id,
        "output": output,
        "passed": passed,
        "model": model,
        "scratch": "/tmp/arm-scratch-3311",
    }


def _agent_protocol(
    corpus_digest,
    *,
    incumbent_outcomes,
    tasks=None,
    incumbent=None,
    candidate=None,
    endpoints=("rubric-quality",),
    tolerance=None,
    batch_size=2,
):
    baseline = {"corpus_digest": corpus_digest, "outcomes": incumbent_outcomes}
    if tolerance is not None:
        baseline["tolerance"] = tolerance
    return {
        "mode": "agent",
        "batch_size": batch_size,
        "endpoints": list(endpoints),
        "holdout_partition": "epoch-test",
        "tasks": [dict(t) for t in (tasks if tasks is not None else AGENT_TASKS)],
        "baseline": baseline,
        "incumbent": dict(incumbent or INCUMBENT_PARAMS),
        "candidate": dict(candidate or CANDIDATE_PARAMS),
    }


def _freeze_agent(protocol, manifest_extra=None, **fields):
    from datetime import UTC, datetime

    from models.improvement_experiment import ImprovementExperiment
    from tools.improvement_eval import runner

    ref = runner.freeze_protocol(protocol)
    manifest = {"protocol_ref": ref, "base_revision": "abc123", **(manifest_extra or {})}
    row_fields = {
        "project_key": PK_RUNNER,
        "created_at": datetime.now(UTC),
        "hypothesis": "the new session writes tighter summaries",
        "mechanism": "the candidate skill revises each draft once",
        "falsifier": "rubric scores do not rise",
        "candidate_surfaces": json.dumps(["tools/agent-skills/"]),
        "manifest": json.dumps(manifest),
    }
    row_fields.update(fields)
    experiment = ImprovementExperiment(**row_fields)
    assert experiment.save() is not False
    experiment = ImprovementExperiment.query.filter(project_key=PK_RUNNER, id=experiment.id).first()
    assert experiment is not None
    experiment.contract_digest = runner.compute_contract_digest(experiment)
    experiment.state = "frozen"
    experiment.frozen_at = datetime.now(UTC)
    assert experiment.save() is not False
    return ImprovementExperiment.query.filter(project_key=PK_RUNNER, id=experiment.id).first()


def _reload_agent_evaluation(evaluation):
    from models.improvement_evaluation import ImprovementEvaluation

    row = ImprovementEvaluation.query.filter(project_key=PK_RUNNER, id=evaluation.id).first()
    assert row is not None
    return row


@pytest.fixture
def agent_charter(tmp_path):
    from models.improvement_charter import _CHARTER_PATH, ImprovementCharter

    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK_RUNNER)
    assert row is not None
    return row


@pytest.fixture
def agent_corpus():
    _seed_memory(PK_RUNNER, "runner lighthouse beacon on the headland")
    _seed_memory(PK_RUNNER, "runner grocery errands for the week")
    from tools.improvement_eval.corpus import export_corpus

    return export_corpus(PK_RUNNER)


def _rubric_judge(endpoint="rubric-quality", score_fn=None):
    """A rubric judge: scores the agent outcome, keyed by endpoint name."""

    def _judge(candidate_output, *, blinded_arm_id, trial_id):
        payload = json.loads(candidate_output)
        _judge.seen.append(payload)
        output = payload.get("outcome", {}).get("output", "")
        score = score_fn(output) if score_fn is not None else 1.0
        return {
            "status": "ok",
            "judge": {
                "judge_id": endpoint,
                "verdict": "APPROVED",
                "blockers": 0,
                "confidence": 0.9,
                "score": score,
            },
        }

    _judge.seen = []
    return _judge


def _incumbent_fake(outputs="prior work summary"):
    """Dispatch fake for the incumbent arm: asserts the prior model, never the candidate's."""

    def _fake(arm, export, project_key, task, arm_params):
        assert arm_params.get("model") == INCUMBENT_MODEL, (
            f"incumbent dispatch carried model {arm_params.get('model')!r}"
        )
        if isinstance(outputs, dict):
            text, passed = outputs[task["id"]]
        else:
            text, passed = outputs, True
        return _trial_outcome(task["id"], text, passed, INCUMBENT_MODEL)

    return _fake


def _candidate_fake(output="new work summary", passed=True):
    def _fake(arm, export, project_key, task, arm_params):
        assert arm_params.get("model") == MODEL_TOKEN
        return _trial_outcome(task["id"], output, passed, MODEL_TOKEN)

    return _fake


def _score_by_output(output):
    return 1.0 if "new work" in output else 0.0


class TestAgentTrialBranch:
    def test_dispatches_agent_arm_per_trial_and_scores_through_judges(
        self, agent_charter, agent_corpus
    ):
        """The branch dispatches per trial and rubric scores become metrics."""
        from tools.improvement_eval import runner

        baseline = {
            t["id"]: _trial_outcome(t["id"], "prior work summary", True, INCUMBENT_MODEL)
            for t in AGENT_TASKS
        }
        experiment = _freeze_agent(
            _agent_protocol(agent_corpus.digest, incumbent_outcomes=baseline)
        )
        judge = _rubric_judge(score_fn=_score_by_output)
        with mock.patch.object(runner, "_run_agent_arm", _incumbent_fake()):
            evaluation = _reload_agent_evaluation(
                runner.evaluate(
                    str(experiment.id),
                    PK_RUNNER,
                    judges=[judge],
                    _candidate_agent_arm=_candidate_fake(),
                )
            )
        assert evaluation.verdict == "accept"
        assert evaluation.trials == 2
        # both arms' trials went through the roster: arm metadata is scrubbed
        # before any judge sees a trial, so the candidate manifest cannot leak
        # by construction
        assert len(judge.seen) == 4
        for payload in judge.seen:
            assert "outcome" in payload
            assert "ranked_ids" not in payload
            assert "model" not in payload["outcome"]
            assert "scratch" not in payload["outcome"]
            assert MODEL_TOKEN not in json.dumps(payload)
        assert evaluation.blinded is True
        assert "t1=agree" in (evaluation.notes or "")
        assert "t2=agree" in (evaluation.notes or "")

    def test_manifest_model_in_output_records_leak_without_failing(
        self, agent_charter, agent_corpus
    ):
        """A hit records blinded=False plus the note; the trial still scores."""
        from tools.improvement_eval import runner

        baseline = {
            t["id"]: _trial_outcome(t["id"], "prior work summary", True, INCUMBENT_MODEL)
            for t in AGENT_TASKS
        }
        experiment = _freeze_agent(
            _agent_protocol(agent_corpus.digest, incumbent_outcomes=baseline)
        )
        judge = _rubric_judge(score_fn=_score_by_output)
        with mock.patch.object(runner, "_run_agent_arm", _incumbent_fake()):
            evaluation = _reload_agent_evaluation(
                runner.evaluate(
                    str(experiment.id),
                    PK_RUNNER,
                    judges=[judge],
                    _candidate_agent_arm=_candidate_fake(f"new work summary via {MODEL_TOKEN}"),
                )
            )
        assert evaluation.blinded is False
        assert MODEL_TOKEN in (evaluation.notes or "")
        assert evaluation.verdict == "accept"


class TestAgentGate1:
    @pytest.mark.parametrize(
        ("inc", "rec", "expected"),
        [(True, True, True), (False, False, True), (True, False, False), (False, True, False)],
    )
    def test_pass_fail_agreement(self, inc, rec, expected):
        from tools.improvement_eval import runner

        assert (
            runner._agent_baseline_agree({"passed": inc}, {"passed": rec}, {"kind": "pass_fail"})
            is expected
        )

    def test_pass_fail_is_the_default_tolerance(self):
        from tools.improvement_eval import runner

        assert runner._agent_baseline_agree({"passed": True}, {"passed": True}, {}) is True
        assert runner._agent_baseline_agree({"passed": True}, {"passed": True}, None) is True
        assert runner._agent_baseline_agree({}, {"passed": True}, None) is False
        assert runner._agent_baseline_agree({}, {}, None) is True

    @pytest.mark.parametrize(
        ("inc_score", "rec_score", "margin", "expected"),
        [(0.8, 0.85, 0.1, True), (0.8, 0.95, 0.1, False), (0.5, 0.5, 0.0, True)],
    )
    def test_score_margin_tolerance(self, inc_score, rec_score, margin, expected):
        from tools.improvement_eval import runner

        tolerance = {"kind": "score_margin", "margin": margin}
        assert (
            runner._agent_baseline_agree({"score": inc_score}, {"score": rec_score}, tolerance)
            is expected
        )

    def test_unknown_tolerance_kind_is_a_harness_error(self):
        from tools.improvement_eval import runner

        with pytest.raises(InfraFailure, match="t9"):
            runner._agent_baseline_agree(
                {"passed": True}, {"passed": True}, {"kind": "exact"}, trial_id="t9"
            )

    def test_mismatch_names_trials_and_never_calls_candidate(self, agent_charter, agent_corpus):
        from tools.improvement_eval import runner

        baseline = {
            t["id"]: _trial_outcome(t["id"], "prior work summary", True, INCUMBENT_MODEL)
            for t in AGENT_TASKS
        }
        experiment = _freeze_agent(
            _agent_protocol(agent_corpus.digest, incumbent_outcomes=baseline)
        )

        called = []

        def _never_candidate(arm, export, project_key, task, arm_params):
            called.append(task["id"])
            raise AssertionError("the candidate must not run when Gate 1 fails")

        with mock.patch.object(
            runner,
            "_run_agent_arm",
            _incumbent_fake(
                {"t1": ("prior work summary", True), "t2": ("changed work summary", False)}
            ),
        ):
            evaluation = _reload_agent_evaluation(
                runner.evaluate(
                    str(experiment.id),
                    PK_RUNNER,
                    judges=[_rubric_judge()],
                    _candidate_agent_arm=_never_candidate,
                )
            )
        assert evaluation.verdict == "infra_failure"
        assert "t2" in (evaluation.notes or "")
        assert called == []


class TestCaptureAgentBaseline:
    def test_records_incumbent_outcomes_per_task(self):
        """Baseline capture stores one outcome per task, no arms spawned."""
        from contextlib import contextmanager

        from tools.improvement_eval import runner

        @contextmanager
        def _fake_server():
            yield object()

        export = mock.Mock(digest="sha256:fake", jsonl_text="x")
        with (
            mock.patch("tools.improvement_eval.arena.arm_redis_server", _fake_server),
            mock.patch.object(
                runner,
                "_run_agent_arm",
                lambda arm, export, project_key, task, params: _trial_outcome(
                    task["id"], "prior work summary", True, INCUMBENT_MODEL
                ),
            ),
        ):
            baseline = runner.capture_agent_baseline(
                PK_RUNNER, AGENT_TASKS, incumbent=dict(INCUMBENT_PARAMS), export=export
            )
        assert baseline["corpus_digest"] == "sha256:fake"
        assert sorted(baseline["outcomes"]) == ["t1", "t2"]
        assert baseline["outcomes"]["t1"]["passed"] is True


class TestCrossModeKeys:
    @pytest.mark.parametrize("key", ["model", "skill", "persona", "prompt_hash", "bounds"])
    def test_retrieval_validator_rejects_agent_keys(self, key):
        from tools.improvement_eval import runner

        value = {} if key == "bounds" else "x"
        with pytest.raises(InfraFailure, match=key):
            runner._validate_arm_params("candidate", {key: value})

    @pytest.mark.parametrize("key", ["limit", "rrf_k", "min_rrf_score"])
    def test_agent_validator_rejects_retrieval_keys(self, key):
        from tools.improvement_eval import runner

        with pytest.raises(InfraFailure, match=key):
            runner._validate_arm_params("candidate", {key: 1}, mode="agent")

    def test_agent_params_accepted_and_allowlist_is_the_union(self):
        from tools.improvement_eval import runner

        runner._validate_arm_params("candidate", dict(CANDIDATE_PARAMS), mode="agent")
        assert runner.AGENT_PARAM_KEYS == frozenset(
            {"model", "skill", "persona", "prompt_hash", "bounds"}
        )
        assert runner.ARM_PARAM_KEYS == runner.RETRIEVAL_PARAM_KEYS | runner.AGENT_PARAM_KEYS

    @pytest.mark.parametrize(
        "params",
        [
            {"model": MODEL_TOKEN},
            {"model": MODEL_TOKEN, "bounds": {"timeout_s": 30}},
            {"model": MODEL_TOKEN, "bounds": {"timeout_s": 30, "spend_cap": -1.0}},
            {"model": MODEL_TOKEN, "bounds": {"timeout_s": 30, "spend_cap": True}},
        ],
        ids=["no-bounds", "no-spend-cap", "negative-spend-cap", "bool-spend-cap"],
    )
    def test_agent_manifest_requires_a_spend_budget(self, params):
        """A protocol arm without a non-negative spend_cap never reaches an
        arm: metered trials would otherwise bill with no reservation held."""
        from tools.improvement_eval import runner

        with pytest.raises(InfraFailure, match="spend_cap"):
            runner._validate_agent_manifest("candidate", params)

    def test_retrieve_job_refuses_agent_keys(self):
        from tools.improvement_eval import runner

        export = mock.Mock(jsonl_text="x")
        with pytest.raises(InfraFailure, match="model"):
            runner._retrieve_job(export, PK_RUNNER, {"query_text": "q"}, {"model": "m"})

    def test_agent_job_builds_manifest_and_bounds(self):
        from tools.improvement_eval import runner

        export = mock.Mock(jsonl_text="x")
        job = runner._agent_job(
            export, PK_RUNNER, {"id": "t1", "prompt": "p"}, dict(CANDIDATE_PARAMS)
        )
        assert job["mode"] == "agent_run"
        assert job["tasks"] == [{"id": "t1", "prompt": "p"}]
        assert job["manifest"] == {
            "model": MODEL_TOKEN,
            "skill": "skill-acquire-3311",
            "persona": "persona-scout-3311",
            "prompt_hash": "sha256:prompt-candidate-3311",
        }
        assert job["bounds"] == {"timeout_s": 30, "max_turns": 2, "spend_cap": 1.0}

    def test_agent_job_refuses_retrieval_keys_and_defaults_bounds(self):
        from tools.improvement_eval import runner

        export = mock.Mock(jsonl_text="x")
        with pytest.raises(InfraFailure, match="limit"):
            runner._agent_job(
                export, PK_RUNNER, {"id": "t1", "prompt": "p"}, {"model": "m", "limit": 3}
            )
        params = {k: v for k, v in CANDIDATE_PARAMS.items() if k != "bounds"}
        assert (
            runner._agent_job(export, PK_RUNNER, {"id": "t1", "prompt": "p"}, params)["bounds"]
            == {}
        )

    def test_agent_manifest_requires_a_model(self):
        from tools.improvement_eval import runner

        with pytest.raises(InfraFailure, match="model"):
            runner._validate_agent_manifest("candidate", {"skill": "s"})
        runner._validate_agent_manifest("candidate", {"model": "m", "bounds": {"spend_cap": 1.0}})


class TestAgentTaskEnvelope:
    def test_valid_agent_candidate_admitted(self):
        from tools.improvement_experiment import validate_candidate

        outcome = validate_candidate(
            {"model": "m", "skill": "s", "bounds": {"timeout_s": 30, "spend_cap": 1.0}},
            envelope="agent_task",
        )
        assert outcome.accepted
        assert outcome.extra["surfaces"] == ["bounds", "model", "skill"]

    def test_retrieval_key_outside_agent_envelope(self):
        from tools.improvement_experiment import validate_candidate

        outcome = validate_candidate({"limit": 10}, envelope="agent_task")
        assert outcome.reason == "KEY_OUTSIDE_ENVELOPE"

    def test_agent_key_outside_retrieval_envelope(self):
        from tools.improvement_experiment import validate_candidate

        outcome = validate_candidate({"model": "m"}, envelope="retrieval_parameters")
        assert outcome.reason == "KEY_OUTSIDE_ENVELOPE"

    def test_blank_model_refused(self):
        from tools.improvement_experiment import validate_candidate

        outcome = validate_candidate({"model": "  "}, envelope="agent_task")
        assert outcome.reason == "VALUE_OUTSIDE_RANGE"

    @pytest.mark.parametrize(
        "bounds",
        [{"timeout_s": 0}, {"max_turns": 0}, {"spend_cap": -1}, {"unknown_key": 1}, "30"],
    )
    def test_bad_bounds_refused(self, bounds):
        from tools.improvement_experiment import validate_candidate

        outcome = validate_candidate({"model": "m", "bounds": bounds}, envelope="agent_task")
        assert outcome.reason == "VALUE_OUTSIDE_RANGE"

    def test_none_value_refused(self):
        from tools.improvement_experiment import validate_candidate

        outcome = validate_candidate({"model": None}, envelope="agent_task")
        assert outcome.reason == "NONE_VALUE"

    def test_boundless_candidate_refused(self):
        """Round-3 Finding 3: a candidate with no bounds carries no
        spend_cap, so propose refuses it instead of freezing a protocol
        the runner rejects after the baseline budget is burned."""
        from tools.improvement_experiment import validate_candidate

        outcome = validate_candidate({"model": "m", "skill": "s"}, envelope="agent_task")
        assert outcome.accepted is False
        assert outcome.reason == "VALUE_OUTSIDE_RANGE"


# ---------------------------------------------------------------------------
# Task 3: agent-trial spend plumbing (unit 2 reserve/settle per task budget)
# ---------------------------------------------------------------------------


class _FakeReservation:
    def __init__(self, reservation_id: str, cents: int) -> None:
        self.reservation_id = reservation_id
        self.cents = cents


class _FakeRefusal:
    def __init__(self, reason: str) -> None:
        self.reason = reason


class _FakeMeter:
    """Mirror of the unit-2 meter surface ``_run_agent_arm`` touches."""

    Refusal = _FakeRefusal

    def __init__(self, *, refuse_with: str | None = None) -> None:
        self.calls: list[tuple] = []
        self._refuse_with = refuse_with

    def reserve(self, project_key, amount_usd, *, purpose, case_id=None, **kwargs):
        self.calls.append(("reserve", project_key, amount_usd, purpose, case_id))
        if self._refuse_with is not None:
            return _FakeRefusal(self._refuse_with)
        return _FakeReservation(f"res-{len(self.calls)}", round(float(amount_usd) * 100))

    def settle(self, project_key, reservation_id, usd, *, metering):
        self.calls.append(("settle", project_key, reservation_id, usd, metering))

    def release(self, project_key, reservation_id):
        self.calls.append(("release", project_key, reservation_id))


def _spend_trial(task_id="t1"):
    return {"id": task_id, "prompt": "summarize the lighthouse log"}


def _spend_params(spend_cap=1.0):
    params = {"model": MODEL_TOKEN}
    if spend_cap is not None:
        params["bounds"] = {"timeout_s": 30, "max_turns": 2, "spend_cap": spend_cap}
    return params


def _spend_export():
    return mock.Mock(jsonl_text="x", digest="sha256:spend-3311")


class TestAgentTrialSpend:
    def test_reserve_called_pre_trial_with_arm_prefixed_resource(self):
        """Reserve precedes the spawn and carries the arm-prefixed resource."""
        from tools.improvement_eval import runner

        meter = _FakeMeter()
        order: list[str] = []
        orig_reserve = meter.reserve

        def _spy_run(arm, project_key, job):
            order.append("run")
            return {"trials": [_trial_outcome("t1", "prior work summary")]}

        def _spy_reserve(project_key, amount_usd, *, purpose, case_id=None, **kwargs):
            order.append("reserve")
            return orig_reserve(project_key, amount_usd, purpose=purpose, case_id=case_id, **kwargs)

        meter.reserve = _spy_reserve  # type: ignore[method-assign]
        orig_settle = meter.settle

        def _spy_settle(project_key, reservation_id, usd, *, metering):
            order.append("settle")
            return orig_settle(project_key, reservation_id, usd, metering=metering)

        meter.settle = _spy_settle  # type: ignore[method-assign]
        with mock.patch("tools.improvement_eval.arena.run_arm_job", _spy_run):
            outcome = runner._run_agent_arm(
                object(),
                _spend_export(),
                PK_RUNNER,
                _spend_trial(),
                _spend_params(),
                arm_run_id="exp3311:candidate",
                meter=meter,
            )
        assert outcome["task_id"] == "t1"
        assert order == ["reserve", "run", "settle"]
        assert meter.calls[0] == (
            "reserve",
            PK_RUNNER,
            1.0,
            "agent_trial",
            "arm:exp3311:candidate:t1",
        )
        assert meter.calls[1] == ("settle", PK_RUNNER, "res-1", 1.0, "estimated")

    def test_refusal_is_a_harness_error_before_any_spawn(self):
        """An over-budget trial raises InfraFailure; the worker never spawns."""
        from tools.improvement_eval import runner

        meter = _FakeMeter(refuse_with="day_exhausted")

        def _must_not_spawn(arm, project_key, job):
            raise AssertionError("refused trial must not spawn a session")

        with mock.patch("tools.improvement_eval.arena.run_arm_job", _must_not_spawn):
            with pytest.raises(InfraFailure, match="t1.*day_exhausted"):
                runner._run_agent_arm(
                    object(),
                    _spend_export(),
                    PK_RUNNER,
                    _spend_trial(),
                    _spend_params(),
                    arm_run_id="exp3311:candidate",
                    meter=meter,
                )
        assert [c[0] for c in meter.calls] == ["reserve"]

    def test_worker_error_leaves_the_reservation_for_reconcile(self):
        """A failed trial settles nothing and releases nothing (charter 8:
        uncertain metering is not zero cost; the reconcile pass receipts it)."""
        from tools.improvement_eval import runner

        meter = _FakeMeter()

        def _boom(arm, project_key, job):
            raise InfraFailure("arm worker timed out after 30s on job mode 'agent_run'")

        with mock.patch("tools.improvement_eval.arena.run_arm_job", _boom):
            with pytest.raises(InfraFailure, match="timed out"):
                runner._run_agent_arm(
                    object(),
                    _spend_export(),
                    PK_RUNNER,
                    _spend_trial(),
                    _spend_params(),
                    arm_run_id="exp3311:incumbent",
                    meter=meter,
                )
        assert [c[0] for c in meter.calls] == ["reserve"]

    def test_missing_spend_cap_refused_before_spawn(self):
        """A metered task with no frozen per-task budget is refused; the
        session would otherwise bill with no reservation held."""
        from tools.improvement_eval import runner

        meter = _FakeMeter()

        def _must_not_spawn(arm, project_key, job):
            raise AssertionError("budgetless trial must not spawn a session")

        with mock.patch("tools.improvement_eval.arena.run_arm_job", _must_not_spawn):
            with pytest.raises(InfraFailure, match="spend_cap"):
                runner._run_agent_arm(
                    object(),
                    _spend_export(),
                    PK_RUNNER,
                    _spend_trial(),
                    _spend_params(spend_cap=None),
                    arm_run_id="exp3311:candidate",
                    meter=meter,
                )
        assert meter.calls == []

    def test_over_budget_trials_count_as_harness_errors_never_zero_scores(self):
        """End to end at the trial loop: refused trials are excluded and
        counted, the candidate never runs, and no trial scores zero."""
        import types

        from tools.improvement_eval import runner

        meter = _FakeMeter(refuse_with="day_exhausted")
        spawned: list[str] = []
        errors: list[tuple[str, str]] = []

        def _must_not_spawn(arm, project_key, job):
            spawned.append("spawn")
            raise AssertionError("refused trial must not spawn a session")

        def _harness_error(arm_name, trial_id, exc):
            errors.append((arm_name, trial_id))

        def _never_candidate(arm, export, project_key, task, arm_params):
            raise AssertionError("the candidate must not run when Gate 1 trials fail")

        ctx = types.SimpleNamespace(notes=[])
        export = _spend_export()
        recorded = {t["id"]: {"passed": True} for t in AGENT_TASKS}
        with mock.patch("tools.improvement_eval.arena.run_arm_job", _must_not_spawn):
            paired = runner._run_agent_trials(
                ctx=ctx,
                export=export,
                project_key=PK_RUNNER,
                tasks=[dict(t) for t in AGENT_TASKS],
                recorded_outcomes=recorded,
                tolerance={"kind": "pass_fail"},
                baseline_digest=export.digest,
                incumbent_params=dict(_spend_params()),
                candidate_params=dict(_spend_params()),
                incumbent_arm=object(),
                candidate_arm_server=object(),
                candidate_agent_arm=_never_candidate,
                assignment=mock.Mock(run_order=["incumbent", "candidate"]),
                harness_error=_harness_error,
                arm_run_id="exp3311",
                meter=meter,
            )
        assert paired == []
        assert [trial for _, trial in errors] == ["t1", "t2"]
        assert spawned == []
        assert all("harness error" in note for note in ctx.notes[-2:])


class TestAgentTrialSessionContract:
    """Review fixes on the session-transport contract: a quiet session
    scores zero like the OpenRouter path (C4), a non-positive timeout is a
    frozen-contract defect (R1), and a blank model never reaches a
    transport (R3)."""

    def _trial_args(self):
        task = {"id": "t1", "prompt": "summarize the lighthouse log"}
        manifest = {"model": "plain-model-3311"}
        bounds = {"timeout_s": 30}
        return task, manifest, bounds

    def test_empty_subscription_output_scores_zero(self, tmp_path, monkeypatch):
        from tools.improvement_eval import arm_worker

        monkeypatch.setenv("POPOTO_CONTENT_PATH", str(tmp_path))
        completed = mock.Mock(returncode=0, stdout="  \n", stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            result = arm_worker.run_agent_trial(*self._trial_args(), PK_AGENT_RUN)
        assert result["task_id"] == "t1"
        assert result["output"] == ""
        assert result["passed"] is False

    @pytest.mark.parametrize("timeout", [0, 0.0, -5])
    def test_non_positive_timeout_is_a_contract_defect(self, tmp_path, monkeypatch, timeout):
        from tools.improvement_eval import arm_worker

        monkeypatch.setenv("POPOTO_CONTENT_PATH", str(tmp_path))
        task, manifest, _ = self._trial_args()
        with pytest.raises(InfraFailure, match="timeout_s.*positive"):
            arm_worker.run_agent_trial(
                task,
                manifest,
                {"timeout_s": timeout},
                PK_AGENT_RUN,
                _complete=lambda prompt, timeout_s: "VERDICT: done",
            )

    @pytest.mark.parametrize("model", ["", "   ", None])
    def test_blank_model_refused_before_transport(self, tmp_path, monkeypatch, model):
        from tools.improvement_eval import arm_worker

        monkeypatch.setenv("POPOTO_CONTENT_PATH", str(tmp_path))
        task, _, bounds = self._trial_args()
        calls = []
        with pytest.raises(InfraFailure, match="model"):
            arm_worker.run_agent_trial(
                task,
                {"model": model},
                bounds,
                PK_AGENT_RUN,
                _complete=lambda prompt, timeout_s: calls.append((prompt, timeout_s)),
            )
        assert calls == []


class TestAgentRerunWorkerError:
    @pytest.mark.parametrize("run_order", [["incumbent", "candidate"], ["candidate", "incumbent"]])
    def test_incumbent_rerun_failure_is_a_harness_error(self, run_order):
        """Review C5: a worker error on the incumbent re-run excludes the
        trial and counts toward the cap instead of aborting the run. Both
        arm orders: candidate-first must not pair the fresh candidate
        against the stale Gate-1 incumbent."""
        """Review C5: a worker error on the incumbent re-run excludes the
        trial and counts toward the cap instead of aborting the run."""
        import types

        from tools.improvement_eval import runner

        seen: list[str] = []

        def _flaky_incumbent(arm, export, project_key, task, arm_params):
            seen.append(task["id"])
            if seen.count(task["id"]) > 1:
                raise InfraFailure(f"worker lost on re-run {task['id']}")
            return _trial_outcome(task["id"], "prior work summary", True, INCUMBENT_MODEL)

        errors: list[tuple[str, str]] = []

        def _harness_error(arm_name, trial_id, exc):
            errors.append((arm_name, trial_id))

        ctx = types.SimpleNamespace(notes=[])
        export = _spend_export()
        recorded = {
            task["id"]: _trial_outcome(task["id"], "prior work summary", True, INCUMBENT_MODEL)
            for task in AGENT_TASKS
        }
        with mock.patch.object(runner, "_run_agent_arm", _flaky_incumbent):
            paired = runner._run_agent_trials(
                ctx=ctx,
                export=export,
                project_key=PK_RUNNER,
                tasks=[dict(task) for task in AGENT_TASKS],
                recorded_outcomes=recorded,
                tolerance={"kind": "pass_fail"},
                baseline_digest=export.digest,
                incumbent_params=dict(INCUMBENT_PARAMS),
                candidate_params=dict(CANDIDATE_PARAMS),
                incumbent_arm=object(),
                candidate_arm_server=object(),
                candidate_agent_arm=_candidate_fake(),
                assignment=mock.Mock(run_order=run_order),
                harness_error=_harness_error,
            )
        assert paired == []
        assert errors == [("incumbent", "t1"), ("incumbent", "t2")]


class TestAgentCapEnforcement:
    def test_rerun_harness_error_trips_a_zero_cap(self, agent_charter, agent_corpus):
        """The production cap logic aborts the run when a re-run harness
        error exceeds it: verdict infra_failure naming the cap."""
        from tools.improvement_eval import runner

        baseline = {
            t["id"]: _trial_outcome(t["id"], "prior work summary", True, INCUMBENT_MODEL)
            for t in AGENT_TASKS
        }
        experiment = _freeze_agent(
            _agent_protocol(agent_corpus.digest, incumbent_outcomes=baseline)
        )
        seen: list[str] = []

        def _flaky_incumbent(arm, export, project_key, task, arm_params):
            seen.append(task["id"])
            if seen.count(task["id"]) > 1:
                raise InfraFailure(f"worker lost on re-run {task['id']}")
            return _trial_outcome(task["id"], "prior work summary", True, INCUMBENT_MODEL)

        with mock.patch.object(runner, "_run_agent_arm", _flaky_incumbent):
            evaluation = _reload_agent_evaluation(
                runner.evaluate(
                    str(experiment.id),
                    PK_RUNNER,
                    judges=[_rubric_judge()],
                    _candidate_agent_arm=_candidate_fake(),
                )
            )
        assert evaluation.verdict == "infra_failure"
        assert "exceed the cap" in (evaluation.notes or "")
