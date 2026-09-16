"""Tests for the agent_run arm job mode (#3311, Task 1).

Covers mode dispatch (one bounded trial per task plus the candidate
manifest), the teardown digest parity check (a session write to the
frozen corpus invalidates the arm), and the pre-judge identity scan
row (a manifest model string in the envelope makes scan_for_identity
fire through the existing manifest IDENTITY_FIELD).
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
