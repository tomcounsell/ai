"""Tests for tools/improvement_eval/runner.py (#3216, task 6).

The runner composes every gate in the plan's order and is the single writer
of ``ImprovementEvaluation``. These tests drive the real arms (two private
``redis-server`` processes reached only through ``arm_worker`` subprocesses)
against a small seeded corpus, and pin the three disjoint exit handlers, the
six named ``infra_failure`` conditions, the Gate 1 ordering, the blinding
record, the stopping rule, and the documented Race 1b repair.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py): rows land in
a claimed test DB under test-scoped ``project_key`` values.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from unittest import mock

import pytest

from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_experiment import ImprovementExperiment
from models.verifying_artifact_store import verifying_artifact_store
from tools.improvement_eval import runner
from tools.improvement_eval.errors import InfraFailure

PK = "test3216runner"

APPROVING_JUDGE_DICT = {
    "judge_id": "fake-judge",
    "verdict": "APPROVED",
    "blockers": 0,
    "confidence": 0.9,
}


def _approving_judge(candidate_output, *, blinded_arm_id, trial_id):
    return {"status": "ok", "judge": dict(APPROVING_JUDGE_DICT)}


def _skipping_judge(candidate_output, *, blinded_arm_id, trial_id):
    return {"status": "skipped", "reason": "Judge provider call failed: ConnectionError"}


def _seed_memory(project_key, content):
    from models.memory import Memory

    record = Memory(
        agent_id="test-3216",
        project_key=project_key,
        content=content,
        importance=5.0,
        source="agent",
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
def corpus():
    """Two memories, so a ranking has a second place to put the gold id."""
    _seed_memory(PK, "runner lighthouse beacon on the headland")
    _seed_memory(PK, "runner grocery errands for the week")
    from tools.improvement_eval.corpus import export_corpus

    return export_corpus(PK)


QUERIES = [
    {"trial_id": "trial-one", "query_text": "zxqvkw qvxj runner-absent"},
    {"trial_id": "trial-two", "query_text": "zxqvkw qvxj runner-absent again"},
]


def _protocol(corpus, *, incumbent, candidate, batch_size=2, **extra):
    baseline = runner.capture_baseline(PK, QUERIES, incumbent=incumbent, export=corpus)
    full = runner.capture_baseline(PK, QUERIES, incumbent={"limit": 10}, export=corpus)
    queries = []
    for query in QUERIES:
        ranked = full["ranked_ids"][query["trial_id"]]
        assert len(ranked) == 2, "the full ranking must place both seeded records"
        gold = ranked[-1]  # the second-ranked id: an arm limited to one misses it
        queries.append({**query, "gold_id": gold})
    protocol = {
        "batch_size": batch_size,
        "endpoints": ["recall_at_2", "mrr"],
        "holdout_partition": "epoch-test",
        "queries": queries,
        "baseline": baseline,
        "incumbent": incumbent,
        "candidate": candidate,
    }
    protocol.update(extra)
    return protocol


def _freeze(protocol, **experiment_fields) -> ImprovementExperiment:
    ref = runner.freeze_protocol(protocol)
    fields = {
        "project_key": PK,
        "created_at": datetime.now(UTC),
        "hypothesis": "a wider candidate finds the gold memory",
        "mechanism": "a larger limit admits the second-ranked record",
        "falsifier": "recall_at_2 does not rise",
        "candidate_surfaces": json.dumps(["tools/improvement_eval/"]),
        "manifest": json.dumps({"protocol_ref": ref, "base_revision": "abc123"}),
    }
    fields.update(experiment_fields)
    experiment = ImprovementExperiment(**fields)
    assert experiment.save() is not False
    experiment = _reload(experiment.id)
    experiment.contract_digest = runner.compute_contract_digest(experiment)
    experiment.state = "frozen"
    experiment.frozen_at = datetime.now(UTC)
    assert experiment.save() is not False
    return _reload(experiment.id)


def _reload(experiment_id) -> ImprovementExperiment:
    row = ImprovementExperiment.query.filter(project_key=PK, id=experiment_id).first()
    assert row is not None
    return row


def _accepting_protocol(corpus):
    # Incumbent sees one record and misses the gold; the candidate sees both,
    # so recall_at_2 rises from 0 to 1 and mrr from 0 to 0.5 on every trial.
    return _protocol(corpus, incumbent={"limit": 1}, candidate={"limit": 10})


def _rejecting_protocol(corpus):
    # The candidate narrows to one record and loses the gold id every trial.
    return _protocol(corpus, incumbent={"limit": 10}, candidate={"limit": 1})


def _evaluate(experiment, **kwargs):
    kwargs.setdefault("judges", [_approving_judge])
    return runner.evaluate(str(experiment.id), PK, **kwargs)


# ---------------------------------------------------------------------------
# The four exits
# ---------------------------------------------------------------------------


class TestVerdicts:
    def test_accept_path_end_to_end(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus))
        evaluation = _evaluate(experiment)

        assert evaluation.state == "complete"
        assert evaluation.verdict == "accept"
        assert runner.has_verdict(evaluation)
        assert evaluation.trials == 2
        assert evaluation.charter_digest == charter.digest
        assert evaluation.contract_digest == experiment.contract_digest
        assert evaluation.correction == "holm; fixed-batch(n=2, endpoints=2)"
        assert evaluation.blinded is True
        assert evaluation.arm_assignment_digest.startswith("sha256:")
        assert evaluation.holdout_partition == "epoch-test"
        effect = json.loads(evaluation.effect)
        assert effect["recall_at_2"] == 1.0
        interval = json.loads(evaluation.confidence_interval)
        assert interval["recall_at_2"]["lower"] > 0.0
        records = json.loads(evaluation.judge_records)
        assert len(records) == 4  # two trials, two arms, one judge
        assert {r["blinded_arm_id"] for r in records} == {"arm-a", "arm-b"}
        assert all(r["charter_digest"] == charter.digest for r in records)
        assert all(verifying_artifact_store.exists(r["raw_response_ref"]) for r in records)
        assert _reload(experiment.id).state == "complete"

    def test_reject_path_end_to_end(self, charter, corpus):
        experiment = _freeze(_rejecting_protocol(corpus))
        evaluation = _evaluate(experiment)
        assert evaluation.verdict == "reject"
        assert evaluation.trials == 2
        assert json.loads(evaluation.effect)["recall_at_2"] == -1.0

    def test_short_batch_is_inconclusive_and_names_the_shortfall(self, charter, corpus):
        protocol = _accepting_protocol(corpus)
        protocol["batch_size"] = 3
        experiment = _freeze(protocol)
        evaluation = _evaluate(experiment)
        assert evaluation.verdict == "inconclusive"
        assert evaluation.effect is None
        assert "short by 1" in evaluation.notes
        assert "2 of 3" in evaluation.notes

    def test_infra_failure_and_reject_have_disjoint_causes(self, charter, corpus):
        rejected = _evaluate(_freeze(_rejecting_protocol(corpus)))
        wedged = _freeze(_rejecting_protocol(corpus), state="running")
        wedged.state = "running"
        wedged.save()
        broken = _evaluate(wedged)

        assert rejected.verdict == "reject"
        assert broken.verdict == "infra_failure"
        assert rejected.verdict != broken.verdict
        # reject comes only from a completed measurement
        assert rejected.trials == 2 and rejected.effect is not None
        # infra_failure never measured anything
        assert broken.trials == 0 and broken.effect is None
        assert "Gate 0" in broken.notes


# ---------------------------------------------------------------------------
# Artifact integrity: invalidated, no verdict
# ---------------------------------------------------------------------------


def _corrupt_live_artifact(reference: str) -> None:
    _digest, relative_path = reference[len("$CF:") :].split(":", 1)
    live_path = os.path.join(verifying_artifact_store.base_path, relative_path)
    with open(live_path, "ab") as handle:
        handle.write(b"\n# tampered after freezing\n")


class TestArtifactIntegrity:
    def test_corrupted_archive_invalidates_without_verdict(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus))
        _corrupt_live_artifact(experiment.manifest)  # a hydrated row reads back its $CF: ref

        evaluation = _evaluate(experiment)

        assert evaluation.state == "invalidated"
        assert not runner.has_verdict(evaluation)
        assert evaluation.verdict not in {"accept", "reject"}
        assert evaluation.verdict == "inconclusive"  # the schema default, never written
        assert "invalidated" in evaluation.notes
        assert evaluation.trials == 0

    def test_corrupted_protocol_invalidates_too(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus))
        ref = json.loads(runner.read_content(experiment, "manifest"))["protocol_ref"]
        _corrupt_live_artifact(ref)
        evaluation = _evaluate(experiment)
        assert evaluation.state == "invalidated"
        assert not runner.has_verdict(evaluation)

    def test_has_verdict_is_true_only_for_complete(self):
        assert runner.has_verdict(mock.Mock(state="complete"))
        assert not runner.has_verdict(mock.Mock(state="invalidated"))
        assert not runner.has_verdict(mock.Mock(state="pending"))


# ---------------------------------------------------------------------------
# The six infra_failure conditions
# ---------------------------------------------------------------------------


class TestInfraFailureConditions:
    def test_contract_digest_mismatch(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus))
        experiment.hypothesis = "edited after freezing"
        experiment.save()
        evaluation = _evaluate(_reload(experiment.id))
        assert evaluation.verdict == "infra_failure"
        assert "contract digest" in evaluation.notes
        assert _reload(experiment.id).state == "frozen"  # Gate 0 lost: untouched

    def test_arm_would_not_spawn(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus))

        @contextlib.contextmanager
        def _no_redis(**kwargs):
            raise InfraFailure("redis-server did not start: probe")
            yield

        with mock.patch("tools.improvement_eval.arena.arm_redis_server", _no_redis):
            evaluation = _evaluate(experiment)
        assert evaluation.verdict == "infra_failure"
        assert "redis-server did not start" in evaluation.notes
        assert _reload(experiment.id).state == "aborted"

    def test_unequal_corpus_digests(self, charter, corpus):
        from tools.improvement_eval.arena import run_arm_job as real_run_arm_job

        experiment = _freeze(_accepting_protocol(corpus))

        def _drifting(arm, project_key, job, **kwargs):
            response = real_run_arm_job(arm, project_key, job, **kwargs)
            if job["mode"] == "digest":
                response = {**response, "digest": "sha256:not-the-export"}
            return response

        with mock.patch("tools.improvement_eval.arena.run_arm_job", _drifting):
            evaluation = _evaluate(experiment)
        assert evaluation.verdict == "infra_failure"
        assert "did not read the frozen corpus" in evaluation.notes

    def test_parity_miss_never_invokes_the_candidate_arm(self, charter, corpus):
        protocol = _accepting_protocol(corpus)
        for trial_id in protocol["baseline"]["ranked_ids"]:
            protocol["baseline"]["ranked_ids"][trial_id] = ["no-such-memory"]
        experiment = _freeze(protocol)
        candidate_arm = mock.Mock(side_effect=AssertionError("candidate arm was invoked"))

        evaluation = _evaluate(experiment, _candidate_arm=candidate_arm)

        assert evaluation.verdict == "infra_failure"
        assert "baseline parity" in evaluation.notes
        candidate_arm.assert_not_called()

    def test_judge_provider_unreachable(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus))
        evaluation = _evaluate(experiment, judges=[_skipping_judge])
        assert evaluation.verdict == "infra_failure"
        assert "judge skipped" in evaluation.notes
        assert "ConnectionError" in evaluation.notes

    def test_uncalibrated_judge(self, charter, corpus):
        """The default roster calibrates first; an empty reference set fails the run."""
        experiment = _freeze(_accepting_protocol(corpus))
        transport = mock.Mock(side_effect=AssertionError("provider must never be called"))
        evaluation = runner.evaluate(str(experiment.id), PK, judge_complete=transport)
        assert evaluation.verdict == "infra_failure"
        assert "below the floor" in evaluation.notes
        transport.assert_not_called()

    def test_missing_charter_is_infra_failure(self, corpus):
        experiment = _freeze(_accepting_protocol(corpus))
        evaluation = _evaluate(experiment)
        assert evaluation.verdict == "infra_failure"
        assert "no ImprovementCharter" in evaluation.notes

    def test_empty_corpus_fails_through_parity_not_a_crash(self, charter):
        from tools.improvement_eval.corpus import export_corpus

        empty = export_corpus(PK)
        protocol = {
            "batch_size": 1,
            "endpoints": ["mrr"],
            "queries": [{"trial_id": "t", "query_text": "anything", "gold_id": "x"}],
            "baseline": {"corpus_digest": "sha256:recorded-elsewhere", "ranked_ids": {"t": []}},
            "incumbent": {},
            "candidate": {},
        }
        assert empty.record_count == 0
        evaluation = _evaluate(_freeze(protocol))
        assert evaluation.verdict == "infra_failure"
        assert "baseline parity: corpus digest" in evaluation.notes

    def test_unexpected_exception_is_infra_failure_naming_the_type(self, charter, corpus):
        experiment = _freeze(_accepting_protocol(corpus))
        with mock.patch(
            "tools.improvement_eval.corpus.export_corpus",
            side_effect=RuntimeError("exporter exploded"),
        ):
            evaluation = _evaluate(experiment)
        assert evaluation.verdict == "infra_failure"
        assert "unexpected RuntimeError" in evaluation.notes


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
        evaluation = _evaluate(experiment)

        assert evaluation.blinded is False
        assert "blinding leak" in evaluation.notes
        assert "trial-one" in evaluation.notes
        # recorded, not suppressed: the measurement still completed
        assert evaluation.verdict == "accept"

    def test_clean_run_records_blinded_true_not_null(self, charter, corpus):
        evaluation = _evaluate(_freeze(_accepting_protocol(corpus)))
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
