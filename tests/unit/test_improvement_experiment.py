"""``tools/improvement_experiment.py``: the retrieval-parameter envelope, the
seven-step freeze, the evaluation wrapper, the verdict, and the CLI wrappers
(lane 5, #3217, task 6).

Every freeze here injects the builder, the exporter, and the baseline so no
test calls the known-item LLM path or spawns an arm, except the one
end-to-end test at the bottom, which drives lane 4's real arms the way
``test_improvement_eval_runner.py`` does. Rows land in the claimed test db
(autouse ``redis_test_db``, tests/conftest.py).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_experiment import ImprovementExperiment
from models.improvement_investigation import ImprovementInvestigation
from models.verifying_artifact_store import VerifyingArtifactStore
from tools import improvement as cli
from tools import improvement_experiment as ex
from tools.improvement_control import keys
from tools.improvement_control.journal import journal_tail, read_head, set_state
from tools.improvement_control.lease import default_lease
from tools.improvement_control.projection import apply
from tools.improvement_eval import runner
from tools.memory_eval.query_set import KnownItem

PK = "valor"  # the CLI is hardcoded to PROJECT_KEY = "valor"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    return VerifyingArtifactStore(base_path=str(tmp_path / "protocols"))


def new_case(charter, *, state="investigating", **overrides) -> ImprovementCase:
    fields = dict(
        project_key=PK,
        state=state,
        title="retrieval limit case",
        summary="the incumbent limit may miss the gold memory",
        created_at=datetime.now(UTC),
        priority_area="memory",
        ranking_rationale="charter §3 names memory",
        charter_digest=charter.digest,
        dedup_identity=f"memory retrieval limit {uuid.uuid4().hex[:8]}",
        evidence_ids=json.dumps([]),
    )
    fields.update(overrides)
    return ImprovementCase.create(**fields)


def move_state(case_id: str, state: str) -> None:
    """The only sanctioned state move: set_state under the lease, then apply."""
    lease = default_lease()
    key = keys.lease_key(PK, case_id)
    generation = lease.acquire(key, ttl=90)
    assert generation is not None
    try:
        result = set_state(PK, case_id, generation=generation, state=state, by="test")
        assert result.accepted, result
    finally:
        lease.release(key, generation)
    apply(PK, case_id)


def fake_export(n_records: int = 30, *, bad_lines: int = 0):
    """A ``CorpusExport``-shaped object whose JSONL body carries ``n_records``
    memories; ``bad_lines`` unparseable lines are sprinkled in."""
    lines = [json.dumps({"popoto_export": 1, "model": "Memory", "matched_count": n_records})]
    for i in range(n_records):
        lines.append(
            json.dumps(
                {
                    "key": f"Memory:m{i:03d}",
                    "values": {
                        "memory_id": f"m{i:03d}",
                        "content": f"memory number {i} about lighthouse {i}",
                        "importance": float(1 + i % 5),
                    },
                    "state": {},
                    "model_state": {},
                }
            )
        )
    for _ in range(bad_lines):
        lines.append("{not json")
    text = "\n".join(lines) + "\n"
    return SimpleNamespace(
        project_key=PK,
        digest="sha256:" + "ab" * 32,
        jsonl_text=text,
        record_count=n_records,
        git_sha="deadbeef",
    )


def builder_dropping(n_drop: int):
    """A ``build_known_item_set`` stand-in that skips ``n_drop`` sampled records
    the way the real builder skips degenerate generations."""

    def build(records, *, n_queries, seed):
        sampled = records[:n_queries]
        kept = sampled[: max(0, len(sampled) - n_drop)]
        return [
            KnownItem(query=f"query for {record.memory_id}", gold_memory_id=record.memory_id)
            for record in kept
        ]

    return build


def baseline_stub(project_key, queries, *, incumbent, export):
    assert incumbent == {"limit": 10}
    return {
        "corpus_digest": export.digest,
        "ranked_ids": {q["trial_id"]: [q["gold_id"], "other"] for q in queries},
    }


class OpenMeter:
    """A meter that admits everything and records what it was asked."""

    def __init__(self, refuse: bool = False):
        self.refuse = refuse
        self.reservations: list[dict] = []
        self.settled: list[tuple] = []
        self.released: list[str] = []
        self.Refusal = ex.paid_inference_meter.Refusal

    def reserve(self, project_key, usd, *, purpose, case_id=None):
        if self.refuse:
            return self.Refusal("day_exhausted")
        rid = uuid.uuid4().hex
        self.reservations.append({"id": rid, "usd": usd, "purpose": purpose, "case_id": case_id})
        return SimpleNamespace(reservation_id=rid, cents=round(usd * 100))

    def settle(self, project_key, reservation_id, usd, *, metering):
        self.settled.append((reservation_id, usd, metering))

    def release(self, project_key, reservation_id):
        self.released.append(reservation_id)


def propose(charter, case=None, **overrides):
    case = case or new_case(charter)
    fields = dict(
        hypothesis="a wider limit finds the gold memory",
        mechanism="limit admits the second-ranked record",
        falsifier="recall_at_5 does not rise",
        candidate={"limit": 20},
        envelope="retrieval_parameters",
    )
    fields.update(overrides)
    outcome = ex.propose_experiment(PK, case.id, **fields)
    assert outcome.accepted, outcome
    return case, ImprovementExperiment.query.get(project_key=PK, id=outcome.experiment_id)


def freeze(experiment, store, **kw):
    kw.setdefault("builder", builder_dropping(0))
    kw.setdefault("exporter", lambda project_key: fake_export())
    kw.setdefault("baseline", baseline_stub)
    kw.setdefault("meter", OpenMeter())
    kw.setdefault("store", store)
    return ex.freeze_experiment(PK, experiment.id, **kw)


def reload_experiment(experiment_id) -> ImprovementExperiment:
    row = ImprovementExperiment.query.get(project_key=PK, id=experiment_id)
    assert row is not None
    return row


def reload_case(case_id) -> ImprovementCase:
    row = ImprovementCase.query.get(project_key=PK, id=case_id)
    assert row is not None
    return row


def manifest_of(experiment) -> dict:
    return json.loads(runner.read_content(reload_experiment(experiment.id), "manifest"))


def protocol_of(experiment, store) -> dict:
    return runner.load_protocol(reload_experiment(experiment.id), store=store)


def events(case_id) -> list[str]:
    return [e["event"] for e in journal_tail(PK, case_id, 50)]


def protocol_keys(store) -> set[str]:
    root = Path(store.base_path)
    if not root.exists():
        return set()
    return {p.name for p in root.rglob("protocol-*")}


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


class TestValidateCandidate:
    def test_incumbent_has_no_none_keys(self):
        assert ex.INCUMBENT == {"limit": 10}
        assert all(v is not None for v in ex.INCUMBENT.values())
        assert set(ex.INCUMBENT) <= set(ex.ENVELOPES["retrieval_parameters"])

    @pytest.mark.parametrize(
        ("candidate", "reason"),
        [
            ({}, "EMPTY_CANDIDATE"),
            (None, "EMPTY_CANDIDATE"),
            ({"limit": 10}, "IDENTICAL_TO_INCUMBENT"),
            ({"retrieval_mode": "current"}, "KEY_OUTSIDE_ENVELOPE"),
            ({"limit": 20, "retrieval_mode": "current"}, "KEY_OUTSIDE_ENVELOPE"),
            ({"limit": 20, "clock_skew_s": 5}, "KEY_OUTSIDE_ENVELOPE"),
            ({"limit": 51}, "VALUE_OUTSIDE_RANGE"),
            ({"limit": 0}, "VALUE_OUTSIDE_RANGE"),
            ({"rrf_k": 201}, "VALUE_OUTSIDE_RANGE"),
            ({"min_rrf_score": 1.5}, "VALUE_OUTSIDE_RANGE"),
            ({"limit": "20"}, "VALUE_OUTSIDE_RANGE"),
            ({"limit": True}, "VALUE_OUTSIDE_RANGE"),
            ({"limit": 20, "rrf_k": None}, "NONE_VALUE"),
            ({"rrf_k": None}, "NONE_VALUE"),
        ],
    )
    def test_refusals_carry_distinct_codes(self, candidate, reason):
        outcome = ex.validate_candidate(candidate)
        assert not outcome.accepted
        assert outcome.reason == reason

    def test_empty_and_incumbent_are_distinct_codes(self):
        assert ex.validate_candidate({}).reason != ex.validate_candidate(ex.INCUMBENT).reason

    def test_refuses_retrieval_mode(self):
        outcome = ex.validate_candidate({"retrieval_mode": "hybrid"})
        assert outcome.reason == "KEY_OUTSIDE_ENVELOPE"
        assert "retrieval_mode" in outcome.message
        assert "retrieval_mode" not in ex.ENVELOPES["retrieval_parameters"]

    @pytest.mark.parametrize(
        "candidate",
        [
            {"limit": 20},
            {"rrf_k": 30},
            {"min_rrf_score": 0.1},
            {"limit": 1, "rrf_k": 200, "min_rrf_score": 1.0},
        ],
    )
    def test_accepts_values_inside_the_envelope(self, candidate):
        outcome = ex.validate_candidate(candidate)
        assert outcome.accepted, outcome
        assert outcome.extra["surfaces"] == sorted(candidate)

    def test_unknown_envelope_is_refused(self):
        assert (
            ex.validate_candidate({"limit": 20}, envelope="agent_run").reason == "UNKNOWN_ENVELOPE"
        )


# ---------------------------------------------------------------------------
# Known-item records
# ---------------------------------------------------------------------------


class TestKnownItemRecords:
    def test_parses_body_lines_and_skips_the_manifest(self):
        records = ex.known_item_records(fake_export(3))
        assert [r.memory_id for r in records] == ["m000", "m001", "m002"]
        assert records[0].content.startswith("memory number 0")
        assert records[1].importance == 2.0

    def test_bad_lines_are_skipped_and_counted(self):
        records, skipped = ex.parse_known_item_records(fake_export(2, bad_lines=2).jsonl_text)
        assert len(records) == 2
        assert skipped == 2
        assert len(ex.known_item_records(fake_export(2, bad_lines=2))) == 2


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------


class TestPropose:
    def test_creates_a_proposed_experiment_and_journals_hypothesis_proposed(self, charter):
        case, experiment = propose(charter, candidate={"limit": 20, "rrf_k": 30})
        assert experiment.state == "proposed"
        assert experiment.case_id == case.id
        assert json.loads(experiment.candidate_surfaces) == ["limit", "rrf_k"]
        notes = json.loads(experiment.notes)
        assert notes["candidate"] == {"limit": 20, "rrf_k": 30}
        assert notes["envelope"] == "retrieval_parameters"
        assert events(case.id) == ["hypothesis_proposed"]
        assert reload_case(case.id).state == "investigating"

    def test_prior_answers_names_2082(self, charter):
        _, experiment = propose(charter)
        prior = json.loads(experiment.notes)["prior_answers"]
        assert prior == [ex.PRIOR_ANSWER_2082]
        assert prior[0]["ref"] == "#2082"
        assert prior[0]["doc"] == "docs/features/hybrid-retrieval-eval.md"

    def test_invalid_candidate_writes_nothing(self, charter):
        case = new_case(charter)
        outcome = ex.propose_experiment(
            PK,
            case.id,
            hypothesis="h",
            mechanism="m",
            falsifier="f",
            candidate={"retrieval_mode": "hybrid"},
            envelope="retrieval_parameters",
        )
        assert outcome.reason == "KEY_OUTSIDE_ENVELOPE"
        assert events(case.id) == []
        assert not [
            e for e in ImprovementExperiment.query.filter(project_key=PK) if e.case_id == case.id
        ]

    def test_missing_case_and_missing_text_are_refused(self, charter):
        case = new_case(charter)
        assert (
            ex.propose_experiment(
                PK,
                "no-such-case",
                hypothesis="h",
                mechanism="m",
                falsifier="f",
                candidate={"limit": 20},
                envelope="retrieval_parameters",
            ).reason
            == "CASE_NOT_FOUND"
        )
        assert (
            ex.propose_experiment(
                PK,
                case.id,
                hypothesis="",
                mechanism="m",
                falsifier="f",
                candidate={"limit": 20},
                envelope="retrieval_parameters",
            ).reason
            == "INCOMPLETE_HYPOTHESIS"
        )

    def test_busy_case_is_refused_without_a_write(self, charter):
        case = new_case(charter)
        lease = default_lease()
        key = keys.lease_key(PK, case.id)
        generation = lease.acquire(key, ttl=30)
        try:
            outcome = ex.propose_experiment(
                PK,
                case.id,
                hypothesis="h",
                mechanism="m",
                falsifier="f",
                candidate={"limit": 20},
                envelope="retrieval_parameters",
            )
        finally:
            lease.release(key, generation)
        assert outcome.reason == "CASE_BUSY"
        assert events(case.id) == []


# ---------------------------------------------------------------------------
# Freeze
# ---------------------------------------------------------------------------


class TestFreeze:
    def test_manifest_carries_base_revision_and_candidate_ref(self, charter, store):
        case, experiment = propose(charter, candidate={"limit": 20})
        outcome = freeze(experiment, store)
        assert outcome.accepted, outcome
        manifest = manifest_of(experiment)
        assert set(manifest) == {
            "protocol_ref",
            "base_revision",
            "candidate_ref",
            "candidate",
            "incumbent",
            "envelope",
            "corpus_digest",
        }
        assert manifest["base_revision"]
        assert manifest["candidate_ref"] == manifest["base_revision"]
        assert manifest["incumbent"] == {"limit": 10}
        assert manifest["candidate"] == {"limit": 20}
        assert manifest["envelope"] == "retrieval_parameters"
        assert manifest["corpus_digest"] == fake_export().digest
        assert manifest["protocol_ref"].startswith("$CF:")

    def test_frozen_record_and_journal(self, charter, store):
        case, experiment = propose(charter)
        outcome = freeze(experiment, store, seed=7)
        assert outcome.accepted, outcome
        row = reload_experiment(experiment.id)
        assert row.state == "frozen"
        assert row.frozen_at is not None
        assert row.contract_digest == runner.compute_contract_digest(row)
        assert int(row.charter_version) == charter.version
        assert events(case.id) == ["hypothesis_proposed", "experiment_frozen", "state_changed"]
        tail = journal_tail(PK, case.id, 5)
        frozen = next(e for e in tail if e["event"] == "experiment_frozen")
        assert frozen["payload_digest"] == row.contract_digest
        assert reload_case(case.id).state == "experimenting"
        apply(PK, case.id)
        assert reload_case(case.id).state == "experimenting"

    def test_protocol_shape(self, charter, store):
        _, experiment = propose(charter, candidate={"limit": 20, "min_rrf_score": 0.05})
        assert freeze(experiment, store, n_queries=25, seed=3).accepted
        protocol = protocol_of(experiment, store)
        assert protocol["endpoints"] == ["recall_at_5", "mrr"]
        assert protocol["thresholds"] == {
            "mrr": {"margin": 0.02, "alpha": 0.05},
            "recall_at_5": {"margin": 0.02, "alpha": 0.05},
        }
        assert protocol["holdout_partition"] == "known-item-3"
        assert protocol["infra_failure_cap"] == 0
        assert protocol["incumbent"] == {"limit": 10}
        assert protocol["candidate"] == {"limit": 20, "min_rrf_score": 0.05}
        assert protocol["batch_size"] == len(protocol["queries"]) == 25
        assert protocol["queries"][0] == {
            "trial_id": "q000",
            "query_text": "query for m000",
            "gold_id": "m000",
        }
        assert protocol["baseline"]["corpus_digest"] == fake_export().digest
        assert set(protocol["baseline"]["ranked_ids"]) == {
            q["trial_id"] for q in protocol["queries"]
        }

    def test_batch_size_equals_queries_produced(self, charter, store):
        _, experiment = propose(charter)
        assert freeze(experiment, store, n_queries=30, builder=builder_dropping(2)).accepted
        protocol = protocol_of(experiment, store)
        assert len(protocol["queries"]) == 28
        assert protocol["batch_size"] == len(protocol["queries"])

    def test_shortfall_refuses(self, charter, store):
        _, experiment = propose(charter)
        outcome = freeze(experiment, store, n_queries=30, builder=builder_dropping(12))
        assert not outcome.accepted
        assert outcome.reason == "KNOWN_ITEM_SHORTFALL"
        assert "18" in outcome.message and "30" in outcome.message
        assert reload_experiment(experiment.id).state == "proposed"
        assert protocol_keys(store) == set()

    def test_shortfall_below_min_queries_constant(self, charter, store):
        assert ex.MIN_QUERIES == 20
        _, experiment = propose(charter)
        assert freeze(experiment, store, n_queries=20, builder=builder_dropping(0)).accepted
        _, other = propose(charter)
        assert (
            freeze(other, store, n_queries=20, builder=builder_dropping(1)).reason
            == "KNOWN_ITEM_SHORTFALL"
        )

    def test_builder_failure_leaves_proposed_and_writes_no_protocol(self, charter, store):
        case, experiment = propose(charter)
        before = reload_experiment(experiment.id).manifest
        meter = OpenMeter()

        def boom(records, *, n_queries, seed):
            raise RuntimeError("provider down")

        outcome = freeze(experiment, store, builder=boom, meter=meter)
        assert not outcome.accepted
        assert outcome.reason == "BUILDER_FAILED"
        assert "provider down" in outcome.message
        row = reload_experiment(experiment.id)
        assert row.state == "proposed"
        assert row.manifest == before
        assert row.contract_digest is None
        assert protocol_keys(store) == set()
        assert meter.released == [meter.reservations[0]["id"]]
        assert events(case.id) == ["hypothesis_proposed"]

    def test_baseline_failure_leaves_proposed_and_writes_no_protocol(self, charter, store):
        _, experiment = propose(charter)

        def boom(project_key, queries, *, incumbent, export):
            raise RuntimeError("arm would not spawn")

        outcome = freeze(experiment, store, baseline=boom)
        assert outcome.reason == "BASELINE_FAILED"
        assert reload_experiment(experiment.id).state == "proposed"
        assert protocol_keys(store) == set()

    def test_unit2_refusal_leaves_proposed(self, charter, store):
        _, experiment = propose(charter)
        outcome = freeze(experiment, store, meter=OpenMeter(refuse=True))
        assert outcome.reason == "UNIT2_UNAVAILABLE"
        assert reload_experiment(experiment.id).state == "proposed"
        assert protocol_keys(store) == set()

    def test_generation_is_reserved_and_settled_as_estimated(self, charter, store):
        case, experiment = propose(charter)
        meter = OpenMeter()
        assert freeze(
            experiment, store, n_queries=30, builder=builder_dropping(2), meter=meter
        ).accepted
        assert meter.reservations[0]["purpose"] == "known_item_generation"
        assert meter.reservations[0]["case_id"] == case.id
        assert meter.reservations[0]["usd"] == pytest.approx(30 * ex.KNOWN_ITEM_PRICE_ESTIMATE_USD)
        (rid, usd, metering) = meter.settled[0]
        assert rid == meter.reservations[0]["id"]
        assert usd == pytest.approx(28 * ex.KNOWN_ITEM_PRICE_ESTIMATE_USD)
        assert metering == "estimated"

    def test_novelty_check_refuses_a_rejected_identity(self, charter, store):
        rejected = new_case(charter, dedup_identity="memory limit widening")
        move_state(rejected.id, "rejected")
        case = new_case(charter, dedup_identity="memory limit widening")
        _, experiment = propose(charter, case)
        outcome = freeze(experiment, store)
        assert outcome.reason == "REJECTED_IDENTITY"
        assert rejected.id in outcome.message
        assert reload_experiment(experiment.id).state == "proposed"

    def test_only_a_proposed_experiment_freezes(self, charter, store):
        _, experiment = propose(charter)
        assert freeze(experiment, store).accepted
        assert freeze(experiment, store).reason == "EXPERIMENT_NOT_PROPOSED"
        assert ex.freeze_experiment(PK, "no-such", store=store).reason == "EXPERIMENT_NOT_FOUND"

    def test_model_revision_in_force_is_recorded(self, charter, store):
        from models.improvement_model_revision import ImprovementModelRevision

        revision = ImprovementModelRevision.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="current",
            summary="s",
            prediction="p",
        )
        _, experiment = propose(charter)
        assert freeze(experiment, store).accepted
        assert reload_experiment(experiment.id).model_revision_id == revision.id


# ---------------------------------------------------------------------------
# Apply verdict
# ---------------------------------------------------------------------------


def evaluation_for(
    experiment, *, verdict, state="complete", notes="all endpoints cleared", **extra
):
    fields = dict(
        project_key=PK,
        created_at=datetime.now(UTC),
        state=state,
        experiment_id=experiment.id,
        contract_digest=experiment.contract_digest,
        trials=20,
        notes=notes,
    )
    if verdict is not None:
        fields["verdict"] = verdict
    fields.update(extra)
    return ImprovementEvaluation.create(**fields)


def frozen_pair(charter, store, *, case_state="evaluating"):
    case, experiment = propose(charter)
    assert freeze(experiment, store).accepted
    move_state(case.id, case_state)
    return case, reload_experiment(experiment.id)


class TestApplyVerdict:
    @pytest.mark.parametrize(
        ("verdict", "state", "expected"),
        [
            ("reject", "complete", "rejected"),
            ("accept", "complete", "evaluating"),
            ("inconclusive", "complete", "investigating"),
            ("infra_failure", "complete", "evaluating"),
            (None, "invalidated", "evaluating"),
        ],
    )
    def test_each_verdict_moves_the_case_and_survives_projection(
        self, charter, store, verdict, state, expected
    ):
        case, experiment = frozen_pair(charter, store)
        notes = "calibration: {}\nendpoints below margin across the whole interval: ['mrr']"
        if verdict in ("infra_failure", None):
            notes = "infra_failure: judge skipped on trial 'q000': provider unreachable"
        evaluation = evaluation_for(experiment, verdict=verdict, state=state, notes=notes)
        outcome = ex.apply_verdict(PK, evaluation.id)
        assert outcome.accepted, outcome
        assert reload_case(case.id).state == expected
        apply(PK, case.id)
        row = reload_case(case.id)
        assert row.state == expected
        assert json.loads(row.evaluation_ids) == [evaluation.id]
        assert "verdict_applied" in events(case.id)
        tail = journal_tail(PK, case.id, 10)
        applied = next(e for e in tail if e["event"] == "verdict_applied")
        assert evaluation.id in applied["payload_digest"]
        if verdict == "reject":
            assert (
                row.rejected_reason == "endpoints below margin across the whole interval: ['mrr']"
            )
        else:
            assert not row.rejected_reason

    def test_apply_verdict_state_survives_projection(self, charter, store):
        case, experiment = frozen_pair(charter, store)
        evaluation = evaluation_for(experiment, verdict="reject", notes="mrr below margin")
        assert ex.apply_verdict(PK, evaluation.id).accepted
        apply(PK, case.id)
        assert reload_case(case.id).state == "rejected"
        assert read_head(PK, case.id).state == "rejected"

    @pytest.mark.parametrize(
        ("verdict", "state"), [("infra_failure", "complete"), (None, "invalidated")]
    )
    def test_infra_failure_and_invalidated_abort_and_open_a_probe(
        self, charter, store, verdict, state
    ):
        case, experiment = frozen_pair(charter, store)
        evaluation = evaluation_for(
            experiment,
            verdict=verdict,
            state=state,
            notes="infra_failure: arm corpus digest differs",
        )
        outcome = ex.apply_verdict(PK, evaluation.id)
        assert outcome.accepted, outcome
        assert reload_experiment(experiment.id).state == "aborted"
        probes = [
            i
            for i in ImprovementInvestigation.query.filter(project_key=PK, kind="probe")
            if i.case_id == case.id
        ]
        assert len(probes) == 1
        assert "arm corpus digest differs" in probes[0].query
        assert outcome.extra["investigation_id"] == probes[0].id
        assert reload_case(case.id).state == "evaluating"

    def test_already_applied_is_a_no_op(self, charter, store):
        case, experiment = frozen_pair(charter, store)
        evaluation = evaluation_for(experiment, verdict="reject", notes="mrr below margin")
        assert ex.apply_verdict(PK, evaluation.id).accepted
        n_events = len(events(case.id))
        again = ex.apply_verdict(PK, evaluation.id)
        assert not again.accepted
        assert again.reason == "ALREADY_APPLIED"
        assert len(events(case.id)) == n_events
        assert json.loads(reload_case(case.id).evaluation_ids) == [evaluation.id]

    def test_busy_case_writes_nothing(self, charter, store):
        case, experiment = frozen_pair(charter, store)
        evaluation = evaluation_for(experiment, verdict="reject")
        lease = default_lease()
        key = keys.lease_key(PK, case.id)
        generation = lease.acquire(key, ttl=30)
        try:
            outcome = ex.apply_verdict(PK, evaluation.id)
        finally:
            lease.release(key, generation)
        assert outcome.reason == "CASE_BUSY"
        assert reload_case(case.id).state == "evaluating"
        assert reload_case(case.id).evaluation_ids in (None, "[]")
        assert "verdict_applied" not in events(case.id)

    def test_missing_rows_are_refused(self, charter, store):
        assert ex.apply_verdict(PK, "no-such").reason == "EVALUATION_NOT_FOUND"
        orphan = ImprovementEvaluation.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="complete",
            verdict="reject",
            experiment_id="gone",
        )
        assert ex.apply_verdict(PK, orphan.id).reason == "EXPERIMENT_NOT_FOUND"

    def test_no_state_written_by_orm(self):
        import re

        src = Path(ex.__file__).read_text(encoding="utf-8")
        assert not re.search(r"\.state *= *['\"]rejected", src)
        assert "default_lease().acquire(" in src
        assert "projection" in src


# ---------------------------------------------------------------------------
# Evaluate wrapper
# ---------------------------------------------------------------------------


class TestEvaluateExperiment:
    def test_slot_not_held_refuses_before_spending(self, charter, store, monkeypatch):
        case, experiment = frozen_pair(charter, store, case_state="experimenting")
        monkeypatch.setenv("AGENT_SESSION_ID", "session-x")
        meter = OpenMeter()
        outcome = ex.evaluate_experiment(PK, experiment.id, meter=meter, judges=[], store=store)
        assert outcome.reason == "SLOT_NOT_HELD"
        assert meter.reservations == []
        assert reload_experiment(experiment.id).state == "frozen"
        assert reload_case(case.id).state == "experimenting"

    def test_slot_held_by_a_running_intent_admits(self, charter, store, monkeypatch):
        from tools.improvement_control.intents import admit, record_materialized, record_running
        from tools.improvement_control.journal import transition

        case, experiment = frozen_pair(charter, store, case_state="experimenting")
        head = read_head(PK, case.id)
        lease = default_lease()
        key = keys.lease_key(PK, case.id)
        generation = lease.acquire(key, ttl=30)
        try:
            r = transition(
                PK,
                case.id,
                expected_revision=head.revision,
                generation=generation,
                event="action_proposed",
                payload_digest="d",
                action_id="a1",
            )
            assert r.accepted, r
            r = admit(
                PK,
                case.id,
                "a1",
                expected_revision=r.revision,
                generation=generation,
                action_type="experiment",
                max_concurrent=5,
            )
            assert r.accepted, r
            r = record_materialized(
                PK,
                case.id,
                "a1",
                expected_revision=r.revision,
                generation=generation,
                agent_session_id="session-x",
            )
            assert r.accepted, r
            r = record_running(
                PK, case.id, "a1", expected_revision=r.revision, generation=generation
            )
            assert r.accepted, r
        finally:
            lease.release(key, generation)
        apply(PK, case.id)
        monkeypatch.setenv("AGENT_SESSION_ID", "session-x")
        meter = OpenMeter(refuse=True)
        outcome = ex.evaluate_experiment(PK, experiment.id, meter=meter, judges=[], store=store)
        # past the slot gate; the meter is the next refusal
        assert outcome.reason == "UNIT2_UNAVAILABLE"

    def test_unit2_unavailable_refuses_before_the_runner(self, charter, store, monkeypatch):
        case, experiment = frozen_pair(charter, store, case_state="experimenting")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        called = []
        monkeypatch.setattr(runner, "evaluate", lambda *a, **k: called.append(a))
        outcome = ex.evaluate_experiment(
            PK, experiment.id, meter=OpenMeter(refuse=True), judges=[], store=store
        )
        assert outcome.reason == "UNIT2_UNAVAILABLE"
        assert called == []
        assert reload_experiment(experiment.id).state == "frozen"

    def test_reservation_is_sized_from_the_query_count(self, charter, store, monkeypatch):
        case, experiment = frozen_pair(charter, store, case_state="experimenting")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        meter = OpenMeter()

        def fake_evaluate(
            experiment_id, project_key, *, judges=None, store=None, meter=None, arm_run_id=None
        ):
            assert store is not None
            row = reload_experiment(experiment_id)
            row.state = "complete"
            row.save()
            return evaluation_for(row, verdict="inconclusive", trials=30, notes="fixed batch short")

        monkeypatch.setattr(runner, "evaluate", fake_evaluate)
        outcome = ex.evaluate_experiment(PK, experiment.id, meter=meter, judges=[], store=store)
        assert outcome.accepted, outcome
        assert meter.reservations[0]["purpose"] == "evaluation_judges"
        assert meter.reservations[0]["usd"] == pytest.approx(30 * 2 * ex.JUDGE_PRICE_ESTIMATE_USD)
        assert meter.settled[0][2] == "estimated"
        assert outcome.extra["verdict"] == "inconclusive"
        assert reload_case(case.id).state == "investigating"
        assert json.loads(reload_case(case.id).evaluation_ids) == [outcome.extra["evaluation_id"]]


class TestAgentFreezeEvaluate:
    """Review fixes: freeze owns the agent_task envelope (C1), evaluate
    forwards the meter and spend id (C2), and the judge reserve sizes from
    tasks (C3)."""

    AGENT_CANDIDATE = {
        "model": "spark-test-3311",
        "persona": "persona-scout-3311",
        "bounds": {"timeout_s": 30, "max_turns": 2, "spend_cap": 1.0},
    }
    AGENT_INCUMBENT = {
        "model": "prior-model-3311",
        "persona": "persona-prior-3311",
        "bounds": {"timeout_s": 30, "max_turns": 2, "spend_cap": 1.0},
    }
    AGENT_ENDPOINTS = ["rubric-quality"]

    def _tasks(self, n=2):
        return [
            {"id": f"t{i + 1}", "prompt": f"summarize the readiness snapshot {i + 1}"}
            for i in range(n)
        ]

    def _agent_baseline_fake(self):
        def _fake(project_key, tasks, *, incumbent=None, export=None, meter=None, arm_run_id=None):
            _fake.seen.append({"metered": meter is not None, "arm_run_id": arm_run_id})
            return {
                "corpus_digest": export.digest,
                "outcomes": {
                    task["id"]: {"passed": True, "output": "prior work", "model": "m"}
                    for task in tasks
                },
            }

        _fake.seen = []
        return _fake

    def _frozen_agent(self, charter, store, n_tasks=2):
        tasks = self._tasks(n_tasks)
        case, experiment = propose(
            charter,
            envelope="agent_task",
            candidate=dict(self.AGENT_CANDIDATE),
            tasks=tasks,
            incumbent=dict(self.AGENT_INCUMBENT),
            endpoints=list(self.AGENT_ENDPOINTS),
        )
        baseline_fake = self._agent_baseline_fake()
        outcome = freeze(experiment, store, meter=OpenMeter(), agent_baseline=baseline_fake)
        assert outcome.accepted, outcome
        move_state(case.id, "experimenting")
        return case, reload_experiment(experiment.id), baseline_fake

    def test_freeze_agent_tasks_builds_an_agent_protocol(self, charter, store):
        tasks = self._tasks(2)
        case, experiment = propose(
            charter,
            envelope="agent_task",
            candidate=dict(self.AGENT_CANDIDATE),
            tasks=tasks,
            incumbent=dict(self.AGENT_INCUMBENT),
            endpoints=list(self.AGENT_ENDPOINTS),
        )
        meter = OpenMeter()
        baseline_fake = self._agent_baseline_fake()
        outcome = freeze(experiment, store, meter=meter, agent_baseline=baseline_fake)
        assert outcome.accepted, outcome
        assert outcome.extra["n_tasks"] == 2
        protocol = protocol_of(experiment, store)
        assert protocol["mode"] == "agent"
        assert protocol["tasks"] == tasks
        assert protocol["endpoints"] == self.AGENT_ENDPOINTS
        assert protocol["incumbent"] == self.AGENT_INCUMBENT
        assert protocol["baseline"]["tolerance"] == {"kind": "pass_fail"}
        assert "queries" not in protocol
        assert manifest_of(experiment)["envelope"] == "agent_task"
        assert [r["purpose"] for r in meter.reservations] == []
        assert baseline_fake.seen == [{"metered": True, "arm_run_id": f"{experiment.id}:baseline"}]

    def test_propose_agent_tasks_missing_refused(self, charter):
        case = new_case(charter)
        outcome = ex.propose_experiment(
            PK,
            case.id,
            hypothesis="the skill reaches the right call",
            mechanism="the checklist names the freeze call",
            falsifier="rubric scores do not separate the arms",
            candidate=dict(self.AGENT_CANDIDATE),
            envelope="agent_task",
        )
        assert outcome.accepted is False
        assert outcome.reason == "TASKS_MISSING"

    def test_freeze_revalidates_tasks_from_notes(self, charter, store):
        case, experiment = propose(
            charter,
            envelope="agent_task",
            candidate=dict(self.AGENT_CANDIDATE),
            tasks=self._tasks(2),
            incumbent=dict(self.AGENT_INCUMBENT),
            endpoints=list(self.AGENT_ENDPOINTS),
        )
        row = reload_experiment(experiment.id)
        notes = json.loads(row.notes)
        del notes["tasks"]
        row.notes = json.dumps(notes, sort_keys=True)
        assert row.save() is not False
        outcome = freeze(row, store, agent_baseline=self._agent_baseline_fake())
        assert outcome.accepted is False
        assert outcome.reason == "TASKS_MISSING"
        assert reload_experiment(experiment.id).state == "proposed"

    def _agent_proposal_fields(self, **overrides):
        fields = dict(
            hypothesis="the skill reaches the right call",
            mechanism="the checklist names the freeze call",
            falsifier="rubric scores do not separate the arms",
            candidate=dict(self.AGENT_CANDIDATE),
            envelope="agent_task",
            tasks=self._tasks(2),
            incumbent=dict(self.AGENT_INCUMBENT),
            endpoints=list(self.AGENT_ENDPOINTS),
        )
        fields.update(overrides)
        return fields

    @pytest.mark.parametrize(
        ("drop", "reason"),
        [
            ("tasks", "TASKS_MISSING"),
            ("incumbent", "INCUMBENT_INVALID"),
            ("endpoints", "ENDPOINTS_MISSING"),
        ],
    )
    def test_propose_agent_inputs_missing_refused(self, charter, drop, reason):
        case = new_case(charter)
        fields = self._agent_proposal_fields()
        del fields[drop]
        outcome = ex.propose_experiment(PK, case.id, **fields)
        assert outcome.accepted is False
        assert outcome.reason == reason

    def test_propose_agent_candidate_without_spend_cap_refused(self, charter):
        case = new_case(charter)
        candidate = dict(self.AGENT_CANDIDATE)
        candidate["bounds"] = {"timeout_s": 30, "max_turns": 2}
        fields = self._agent_proposal_fields(candidate=candidate)
        outcome = ex.propose_experiment(PK, case.id, **fields)
        assert outcome.accepted is False
        assert outcome.reason == "VALUE_OUTSIDE_RANGE"

    def test_propose_agent_incumbent_without_model_refused(self, charter):
        case = new_case(charter)
        outcome = ex.propose_experiment(
            PK, case.id, **self._agent_proposal_fields(incumbent={"persona": "p"})
        )
        assert outcome.accepted is False
        assert outcome.reason == "INCUMBENT_INVALID"

    @pytest.mark.parametrize(
        ("key", "reason"),
        [
            ("tasks", "TASKS_MISSING"),
            ("incumbent", "INCUMBENT_INVALID"),
            ("endpoints", "ENDPOINTS_MISSING"),
        ],
    )
    def test_freeze_revalidates_agent_inputs_from_notes(self, charter, store, key, reason):
        case, experiment = propose(
            charter,
            envelope="agent_task",
            candidate=dict(self.AGENT_CANDIDATE),
            tasks=self._tasks(2),
            incumbent=dict(self.AGENT_INCUMBENT),
            endpoints=list(self.AGENT_ENDPOINTS),
        )
        row = reload_experiment(experiment.id)
        notes = json.loads(row.notes)
        del notes[key]
        row.notes = json.dumps(notes, sort_keys=True)
        assert row.save() is not False
        outcome = freeze(row, store, agent_baseline=self._agent_baseline_fake())
        assert outcome.accepted is False
        assert outcome.reason == reason
        assert reload_experiment(experiment.id).state == "proposed"

    def test_evaluate_forwards_meter_and_spend_id_to_the_runner(self, charter, store, monkeypatch):
        case, experiment, _ = self._frozen_agent(charter, store)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        meter = OpenMeter()
        seen = {}

        def fake_evaluate(
            experiment_id, project_key, *, judges=None, store=None, meter=None, arm_run_id=None
        ):
            seen["meter"] = meter
            seen["arm_run_id"] = arm_run_id
            row = reload_experiment(experiment_id)
            row.state = "complete"
            row.save()
            return evaluation_for(row, verdict="inconclusive", trials=2, notes="agent batch")

        monkeypatch.setattr(runner, "evaluate", fake_evaluate)
        outcome = ex.evaluate_experiment(
            PK, experiment.id, meter=meter, judges=[], store=store, arm_run_id="lane5b-3311"
        )
        assert outcome.accepted, outcome
        assert seen["meter"] is meter
        assert seen["arm_run_id"] == "lane5b-3311"

    def test_judge_reserve_is_sized_from_the_task_count(self, charter, store, monkeypatch):
        case, experiment, _ = self._frozen_agent(charter, store, n_tasks=3)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        meter = OpenMeter()

        def fake_evaluate(
            experiment_id, project_key, *, judges=None, store=None, meter=None, arm_run_id=None
        ):
            # C2 pins the forwarding; here only the reserve size matters.
            row = reload_experiment(experiment_id)
            row.state = "complete"
            row.save()
            return evaluation_for(row, verdict="inconclusive", trials=3, notes="agent batch")

        monkeypatch.setattr(runner, "evaluate", fake_evaluate)
        outcome = ex.evaluate_experiment(
            PK, experiment.id, meter=meter, judges=[], store=store, arm_run_id="lane5b-3311"
        )
        assert outcome.accepted, outcome
        assert meter.reservations[0]["purpose"] == "evaluation_judges"
        assert meter.reservations[0]["usd"] == pytest.approx(3 * 2 * ex.JUDGE_PRICE_ESTIMATE_USD)


# ---------------------------------------------------------------------------
# Show, repair, and the CLI
# ---------------------------------------------------------------------------


def run_cli(argv: list[str], capsys) -> tuple[int, dict]:
    code = cli.main(["--json", *argv])
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else {})


class TestShowRepairAndCli:
    def test_show_on_an_aborted_experiment_prints_the_infra_failure_notes(
        self, charter, store, capsys
    ):
        case, experiment = frozen_pair(charter, store)
        evaluation = evaluation_for(
            experiment,
            verdict="infra_failure",
            notes="infra_failure: judge skipped on trial 'q000': ConnectionError",
        )
        assert ex.apply_verdict(PK, evaluation.id).accepted
        code, payload = run_cli(["experiment", "show", "--id", experiment.id], capsys)
        assert code == 0
        assert payload["state"] == "aborted"
        assert payload["verdict"] == "infra_failure"
        assert "ConnectionError" in payload["notes"]
        assert payload["prior_answers"][0]["ref"] == "#2082"
        code = cli.main(["experiment", "show", "--id", experiment.id])
        human = capsys.readouterr().out
        assert "aborted" in human and "ConnectionError" in human

    def test_show_missing_experiment_exits_one(self, capsys):
        code, payload = run_cli(["experiment", "show", "--id", "no-such"], capsys)
        assert code == 1
        assert payload["reason"] == "EXPERIMENT_NOT_FOUND"

    def test_repair_returns_a_running_experiment_to_frozen(self, charter, store, capsys):
        _, experiment = frozen_pair(charter, store)
        experiment.state = "running"
        experiment.save()
        code, payload = run_cli(["experiment", "repair", "--id", experiment.id], capsys)
        assert code == 0
        assert payload["state"] == "frozen"
        assert reload_experiment(experiment.id).state == "frozen"
        code, payload = run_cli(["experiment", "repair", "--id", "no-such"], capsys)
        assert code == 1 and payload["reason"] == "EXPERIMENT_NOT_FOUND"

    def test_freeze_cli_creates_from_flags_when_no_proposed_experiment_exists(
        self, charter, store, capsys, monkeypatch
    ):
        case = new_case(charter)
        monkeypatch.setattr(ex, "build_known_item_set", builder_dropping(0))
        monkeypatch.setattr(ex, "export_corpus", lambda project_key: fake_export())
        monkeypatch.setattr(ex, "capture_baseline", baseline_stub)
        monkeypatch.setattr(ex, "paid_inference_meter", OpenMeter())
        code, payload = run_cli(
            [
                "experiment",
                "freeze",
                "--case",
                case.id,
                "--hypothesis",
                "h",
                "--mechanism",
                "m",
                "--falsifier",
                "f",
                "--candidate",
                json.dumps({"limit": 20}),
                "--n-queries",
                "22",
                "--seed",
                "5",
            ],
            capsys,
        )
        assert code == 0, payload
        row = reload_experiment(payload["experiment_id"])
        assert row.state == "frozen"
        assert payload["contract_digest"] == row.contract_digest
        assert events(case.id) == ["hypothesis_proposed", "experiment_frozen", "state_changed"]

    def test_freeze_cli_reads_the_latest_proposed_experiment(
        self, charter, store, capsys, monkeypatch
    ):
        case, experiment = propose(charter)
        monkeypatch.setattr(ex, "build_known_item_set", builder_dropping(0))
        monkeypatch.setattr(ex, "export_corpus", lambda project_key: fake_export())
        monkeypatch.setattr(ex, "capture_baseline", baseline_stub)
        monkeypatch.setattr(ex, "paid_inference_meter", OpenMeter())
        code, payload = run_cli(["experiment", "freeze", "--case", case.id], capsys)
        assert code == 0, payload
        assert payload["experiment_id"] == experiment.id
        assert reload_experiment(experiment.id).state == "frozen"

    def test_freeze_cli_refuses_with_the_reason_code(self, charter, store, capsys, monkeypatch):
        case, experiment = propose(charter)
        monkeypatch.setattr(ex, "build_known_item_set", builder_dropping(12))
        monkeypatch.setattr(ex, "export_corpus", lambda project_key: fake_export())
        monkeypatch.setattr(ex, "capture_baseline", baseline_stub)
        monkeypatch.setattr(ex, "paid_inference_meter", OpenMeter())
        code, payload = run_cli(["experiment", "freeze", "--case", case.id], capsys)
        assert code == 1
        assert payload["reason"] == "KNOWN_ITEM_SHORTFALL"
        code, payload = run_cli(["experiment", "freeze", "--case", "no-such-case"], capsys)
        assert code == 1 and payload["reason"] == "NO_PROPOSED_EXPERIMENT"

    def test_evaluate_cli_refuses_slot_not_held(self, charter, store, capsys, monkeypatch):
        _, experiment = frozen_pair(charter, store, case_state="experimenting")
        monkeypatch.setenv("AGENT_SESSION_ID", "session-x")
        code, payload = run_cli(["experiment", "evaluate", "--id", experiment.id], capsys)
        assert code == 1
        assert payload["reason"] == "SLOT_NOT_HELD"


# ---------------------------------------------------------------------------
# End to end against lane 4's real arms (no LLM)
# ---------------------------------------------------------------------------


def _seed_memory(project_key, content):
    from models.memory import Memory

    record = Memory(
        agent_id="test-3217",
        project_key=project_key,
        content=content,
        importance=5.0,
        source="agent",
    )
    assert record.save() is not False
    return record


class TestEndToEnd:
    def test_freeze_evaluate_and_apply_verdict_on_real_arms(self, charter, monkeypatch):
        """Freeze on the real export with an injected builder, capture the
        baseline on a real incumbent arm, run lane 4's ``evaluate`` with its
        approving test judge, and apply the verdict. The candidate narrows to
        one record and loses the gold id on every trial, so the verdict is
        ``reject`` and the case leaves the open set."""
        from tests.unit.improvement_eval_runner_support import _approving_judge
        from tools.improvement_eval.corpus import export_corpus

        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        monkeypatch.setattr(ex, "MIN_QUERIES", 2)
        _seed_memory(PK, "experiment lighthouse beacon on the headland")
        _seed_memory(PK, "experiment grocery errands for the week")
        holder: dict = {}

        def exporter(project_key):
            holder["export"] = export_corpus(project_key)
            return holder["export"]

        def builder(records, *, n_queries, seed):
            probe = [{"trial_id": "p", "query_text": "zxqvkw qvxj experiment-absent"}]
            full = runner.capture_baseline(
                PK, probe, incumbent={"limit": 10}, export=holder["export"]
            )
            ranked = full["ranked_ids"]["p"]
            assert len(ranked) == 2
            gold = ranked[-1]
            return [
                KnownItem(query="zxqvkw qvxj experiment-absent", gold_memory_id=gold),
                KnownItem(query="zxqvkw qvxj experiment-absent again", gold_memory_id=gold),
            ]

        case, experiment = propose(charter, candidate={"limit": 1})
        outcome = ex.freeze_experiment(
            PK,
            experiment.id,
            n_queries=2,
            seed=1,
            builder=builder,
            exporter=exporter,
            meter=OpenMeter(),
        )
        assert outcome.accepted, outcome
        assert reload_case(case.id).state == "experimenting"

        result = ex.evaluate_experiment(
            PK, experiment.id, meter=OpenMeter(), judges=[_approving_judge]
        )
        assert result.accepted, result
        evaluation = ImprovementEvaluation.query.get(
            project_key=PK, id=result.extra["evaluation_id"]
        )
        assert evaluation.verdict == "reject", evaluation.notes
        assert result.extra["verdict"] == "reject"
        row = reload_case(case.id)
        assert row.state == "rejected"
        assert json.loads(row.evaluation_ids) == [evaluation.id]
        assert row.rejected_reason
        apply(PK, case.id)
        assert reload_case(case.id).state == "rejected"
        assert events(case.id)[-2:] == ["verdict_applied", "state_changed"]
        assert reload_experiment(experiment.id).state == "complete"
        assert ex.apply_verdict(PK, evaluation.id).reason == "ALREADY_APPLIED"
        shown = ex.show_experiment(PK, experiment.id)
        assert shown["verdict"] == "reject"
