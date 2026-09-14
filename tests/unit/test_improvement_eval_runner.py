"""Tests for tools/improvement_eval/runner.py (#3216, task 6).

The runner composes every gate in the plan's order and is the single writer
of ``ImprovementEvaluation``. These tests drive the real arms (two private
``redis-server`` processes reached only through ``arm_worker`` subprocesses)
against a small seeded corpus, and pin the three disjoint exit handlers, the
six ``infra_failure`` categories (a candidate-side worker failure included),
the Gate 1 ordering, the blinding record read from a queried row, the
stopping rule, and the documented Race 1b repair.

This module holds the four exits, artifact integrity, and the ``infra_failure``
conditions; blinding, the wedge repair, the contract digest, and the
single-writer invariant live in ``test_improvement_eval_runner_guards.py``.
Fixtures and helpers come from ``improvement_eval_runner_support``, which
records why the suite is split across two files.
"""

from __future__ import annotations

import contextlib
import json
import os
from unittest import mock

from models.verifying_artifact_store import verifying_artifact_store
from tests.unit.improvement_eval_runner_support import (  # noqa: F401 -- fixtures
    PK,
    _accepting_protocol,
    _approving_judge,
    _evaluate,
    _freeze,
    _protocol,
    _rejecting_protocol,
    _reload,
    _reload_evaluation,
    _skipping_judge,
    charter_fixture,
    corpus_fixture,
)
from tools.improvement_eval import runner
from tools.improvement_eval.errors import InfraFailure

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


def _corrupt_archive_copy(reference: str) -> None:
    """Tamper ``.versions/{prefix}/{hash}{ext}`` and leave the live path absent.

    ``FilesystemStore.save`` archives a live file only when it is overwritten,
    so the artifact is saved a second time under its own key with different
    bytes (which archives the original), the archive copy is tampered, and
    the live file is removed. ``load`` then has only the archive branch left.
    """
    content_hash, relative_path = reference[len("$CF:") :].split(":", 1)
    model_class_name, filename = relative_path.split("/", 1)
    store = verifying_artifact_store
    key = filename[: -len(store.extension)]
    live_path = os.path.join(store.base_path, relative_path)
    store.save(b"{}", key=key, model_class_name=model_class_name)
    archive_path = os.path.join(
        store.base_path, ".versions", content_hash[:2], f"{content_hash}{store.extension}"
    )
    assert os.path.exists(archive_path), "the overwrite archived nothing"
    with open(archive_path, "ab") as handle:
        handle.write(b"\n# tampered archive copy\n")
    os.remove(live_path)


class TestArtifactIntegrity:
    def test_corrupted_archive_invalidates_without_verdict(self, charter, corpus):
        """The archive copy specifically: live path absent, only the tampered version remains."""
        experiment = _freeze(_accepting_protocol(corpus))
        _corrupt_archive_copy(experiment.manifest)  # a hydrated row reads back its $CF: ref

        evaluation = _evaluate(experiment)

        assert evaluation.state == "invalidated"
        assert not runner.has_verdict(evaluation)
        assert evaluation.verdict not in {"accept", "reject"}
        assert evaluation.verdict == "inconclusive"  # the schema default, never written
        assert "invalidated: Archived artifact" in evaluation.notes
        assert evaluation.trials == 0

    def test_corrupted_live_artifact_invalidates_without_verdict(self, charter, corpus):
        """The live path, with no archive to fall back to, is corruption rather than absence."""
        experiment = _freeze(_accepting_protocol(corpus))
        _corrupt_live_artifact(experiment.manifest)

        evaluation = _evaluate(experiment)

        assert evaluation.state == "invalidated"
        assert not runner.has_verdict(evaluation)
        assert evaluation.verdict not in {"accept", "reject"}
        assert "invalidated: Live artifact" in evaluation.notes
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

    def test_candidate_arm_worker_failure_is_infra_failure_not_reject(self, charter, corpus):
        """Both arms run the same worker, so a candidate-side InfraFailure is harness breakage."""
        experiment = _freeze(_rejecting_protocol(corpus))
        candidate_arm = mock.Mock(side_effect=InfraFailure("arm worker timed out after 600s"))

        evaluation = _evaluate(experiment, _candidate_arm=candidate_arm)

        assert candidate_arm.called
        assert evaluation.verdict == "infra_failure"
        assert "harness error on candidate arm" in evaluation.notes
        assert "exceed the cap of 0" in evaluation.notes
        assert evaluation.effect is None  # nothing was scored against the candidate

    def test_candidate_failures_within_the_cap_are_excluded_not_scored(self, charter, corpus):
        """A tolerated worker failure drops the trial; it never becomes a zero for the candidate."""
        protocol = _protocol(corpus, incumbent={"limit": 10}, candidate={"limit": 1})
        protocol["infra_failure_cap"] = 2
        candidate_arm = mock.Mock(side_effect=InfraFailure("arm worker exited with returncode 1"))

        evaluation = _evaluate(_freeze(protocol), _candidate_arm=candidate_arm)

        assert evaluation.verdict == "inconclusive"
        assert evaluation.trials == 0
        assert evaluation.effect is None
        assert evaluation.notes.count("harness error on candidate arm") == 2
        # no judge scan ran, so there is no blinding fact to state
        assert _reload_evaluation(evaluation).blinded is None

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
