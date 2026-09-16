"""Tests threading the unit-2 meter through the live agent path (#3311, Task 4).

``evaluate()`` and ``capture_agent_baseline()`` accept an optional meter
plus ``arm_run_id``; every agent trial then reserves its frozen per-task
budget pre-trial (``arm:<arm_run_id>:<trial>``) and settles post-trial. A
refused reservation surfaces as a harness error before anything spawns.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest import mock

import pytest

PK_METER = "test3311agentmeter"

TASKS = [
    {"id": "t1", "prompt": "decide on the first freeze snapshot"},
    {"id": "t2", "prompt": "decide on the second freeze snapshot"},
]

PARAMS = {
    "model": "claude-subscription",
    "bounds": {"timeout_s": 30, "spend_cap": 0.02},
}


class _FakeRefusal:
    def __init__(self, reason):
        self.reason = reason


class _FakeReservation:
    def __init__(self, reservation_id, cents):
        self.reservation_id = reservation_id
        self.cents = cents


class _FakeMeter:
    Refusal = _FakeRefusal

    def __init__(self):
        self.calls = []

    def reserve(self, project_key, amount_usd, *, purpose, case_id=None, **kwargs):
        self.calls.append(("reserve", project_key, amount_usd, purpose, case_id))
        return _FakeReservation(f"res-{len(self.calls)}", round(float(amount_usd) * 100))

    def settle(self, project_key, reservation_id, usd, *, metering):
        self.calls.append(("settle", project_key, reservation_id, usd, metering))

    def release(self, project_key, reservation_id):
        self.calls.append(("release", project_key, reservation_id))


@contextmanager
def _fake_server():
    yield object()


def _fake_run_arm_job(arm, project_key, job):
    if job.get("mode") == "digest":
        return {"digest": "sha256:meter-3311", "manifest": ["meter-manifest"]}
    task_id = job["tasks"][0]["id"]
    return {
        "trials": [
            {
                "task_id": task_id,
                "output": "checks read\nVERDICT: FREEZE",
                "passed": True,
                "model": "claude-subscription",
                "scratch": "s",
            }
        ],
        "candidate_manifest": job.get("manifest"),
        "digest": "sha256:meter-3311",
        "manifest": ["meter-manifest"],
    }


@pytest.fixture
def meter_charter(tmp_path):
    from models.improvement_charter import _CHARTER_PATH, ImprovementCharter

    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK_METER)
    assert row is not None
    return row


def _freeze_metered(protocol):
    from models.improvement_experiment import ImprovementExperiment
    from tools.improvement_eval import runner

    ref = runner.freeze_protocol(protocol)
    experiment = ImprovementExperiment(
        project_key=PK_METER,
        created_at=datetime.now(UTC),
        hypothesis="the skill guides the session to the right readiness call",
        mechanism="the candidate prompt carries the frozen preflight checklist",
        falsifier="rubric scores do not rise",
        candidate_surfaces=json.dumps(["skill"]),
        manifest=json.dumps({"protocol_ref": ref, "base_revision": "abc123"}),
    )
    assert experiment.save() is not False
    experiment = ImprovementExperiment.query.filter(project_key=PK_METER, id=experiment.id).first()
    experiment.contract_digest = runner.compute_contract_digest(experiment)
    experiment.state = "frozen"
    experiment.frozen_at = datetime.now(UTC)
    assert experiment.save() is not False
    return ImprovementExperiment.query.filter(project_key=PK_METER, id=experiment.id).first()


def _metered_protocol():
    outcomes = {t["id"]: {"passed": True, "output": "checks read\nVERDICT: FREEZE"} for t in TASKS}
    return {
        "mode": "agent",
        "batch_size": 2,
        "endpoints": ["readiness-verdict"],
        "holdout_partition": "epoch-test",
        "tasks": [dict(t) for t in TASKS],
        "baseline": {"corpus_digest": "sha256:meter-3311", "outcomes": outcomes},
        "incumbent": dict(PARAMS),
        "candidate": dict(PARAMS),
    }


def _rubric_ok(endpoint="readiness-verdict"):
    def _judge(candidate_output, *, blinded_arm_id, trial_id):
        return {
            "status": "ok",
            "judge": {
                "judge_id": endpoint,
                "verdict": "APPROVED",
                "blockers": 0,
                "confidence": 1.0,
                "score": 1.0,
            },
        }

    return _judge


class TestMeterThreading:
    def test_capture_agent_baseline_meters_each_task(self):
        from tools.improvement_eval import runner

        meter = _FakeMeter()
        export = mock.Mock(digest="sha256:meter-3311", jsonl_text="x")
        with (
            mock.patch("tools.improvement_eval.arena.arm_redis_server", _fake_server),
            mock.patch("tools.improvement_eval.arena.run_arm_job", _fake_run_arm_job),
        ):
            baseline = runner.capture_agent_baseline(
                PK_METER,
                [dict(t) for t in TASKS],
                incumbent=dict(PARAMS),
                export=export,
                meter=meter,
                arm_run_id="exp3311:baseline",
            )
        assert sorted(baseline["outcomes"]) == ["t1", "t2"]
        assert meter.calls[0] == (
            "reserve",
            PK_METER,
            0.02,
            "agent_trial",
            "arm:exp3311:baseline:t1",
        )
        assert meter.calls[1][0] == "settle"
        assert meter.calls[2] == (
            "reserve",
            PK_METER,
            0.02,
            "agent_trial",
            "arm:exp3311:baseline:t2",
        )
        assert meter.calls[3][0] == "settle"

    def test_evaluate_meters_gate1_and_paired_trials(self, meter_charter):
        from tools.improvement_eval import runner

        meter = _FakeMeter()
        experiment = _freeze_metered(_metered_protocol())
        with (
            mock.patch("tools.improvement_eval.arena.arm_redis_server", _fake_server),
            mock.patch("tools.improvement_eval.arena.run_arm_job", _fake_run_arm_job),
            mock.patch(
                "tools.improvement_eval.corpus.export_corpus",
                lambda project_key, store=None: mock.Mock(
                    digest="sha256:meter-3311",
                    jsonl_text="x",
                    manifest_canon=["meter-manifest"],
                ),
            ),
        ):
            from models.improvement_evaluation import ImprovementEvaluation

            evaluation = runner.evaluate(
                str(experiment.id),
                PK_METER,
                judges=[_rubric_ok()],
                meter=meter,
                arm_run_id="exp3311eval",
            )
            evaluation = ImprovementEvaluation.query.filter(
                project_key=PK_METER, id=evaluation.id
            ).first()
        assert evaluation.trials == 2
        # the fake arms return identical outcomes, so the honest verdict is
        # inconclusive (zero deltas); this test proves the metering, not the verdict
        assert evaluation.verdict == "inconclusive"
        reserves = [c for c in meter.calls if c[0] == "reserve"]
        settles = [c for c in meter.calls if c[0] == "settle"]
        assert len(reserves) == 6
        assert len(settles) == 6
        assert reserves[0][4] == "arm:exp3311eval:t1"
        assert all(case.startswith("arm:exp3311eval:") for _, _, _, _, case in reserves)

    def test_evaluate_without_meter_runs_unmetered(self, meter_charter):
        from tools.improvement_eval import runner

        experiment = _freeze_metered(_metered_protocol())
        with (
            mock.patch("tools.improvement_eval.arena.arm_redis_server", _fake_server),
            mock.patch("tools.improvement_eval.arena.run_arm_job", _fake_run_arm_job),
            mock.patch(
                "tools.improvement_eval.corpus.export_corpus",
                lambda project_key, store=None: mock.Mock(
                    digest="sha256:meter-3311",
                    jsonl_text="x",
                    manifest_canon=["meter-manifest"],
                ),
            ),
        ):
            from models.improvement_evaluation import ImprovementEvaluation

            evaluation = runner.evaluate(str(experiment.id), PK_METER, judges=[_rubric_ok()])
            evaluation = ImprovementEvaluation.query.filter(
                project_key=PK_METER, id=evaluation.id
            ).first()
        assert evaluation.trials == 2
        assert evaluation.verdict == "inconclusive"
