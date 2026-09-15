"""``ui/data/improvement.py``: the control status (lane 3, #3215) and the
ranking, hypotheses, and rejected-approaches getters (lane 5, #3217).

Every getter answers one of three distinguishable states: content, "nothing
yet", or ``unavailable`` with the error text. A corrupted snapshot is
``unavailable``, never a stale order.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_controller_state import ImprovementControllerState
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_experiment import ImprovementExperiment
from models.verifying_artifact_store import VerifyingArtifactStore
from tools.improvement_control.intents import admit, mark_reconciliation_required
from tools.improvement_control.journal import transition
from tools.improvement_ranking import RankedCase, write_snapshot
from ui.data.improvement import (
    get_control_status,
    get_goals,
    get_hypotheses,
    get_ranking,
    get_rejected_approaches,
)

T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def fresh_pk() -> str:
    return f"test-3215-ui-data-{uuid.uuid4().hex[:8]}"


def new_case(pk: str, **over) -> ImprovementCase:
    fields = dict(project_key=pk, state="investigating", title="t", created_at=datetime.now(UTC))
    fields.update(over)
    return ImprovementCase.create(**fields)


@pytest.fixture
def store(monkeypatch, tmp_path):
    """The getters build their store from the environment; point it at tmp."""
    root = str(tmp_path / "content")
    monkeypatch.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", root)
    return VerifyingArtifactStore(base_path=root)


def ranked(case_id: str, position: int, *, blocked_by=None) -> RankedCase:
    return RankedCase(
        case_id=case_id,
        position=position,
        factors={
            "opportunity_cost": 3,
            "quality": 1,
            "resource_cost": 1,
            "uncertainty": 3,
            "unlocked_capacity": 3,
        },
        reason="starting priority",
        blocked_by=blocked_by,
    )


def snapshot(pk: str, store, entries, *, previous_ref=None, at=T0, cases=(), intake_pool=()):
    ref = write_snapshot(
        entries,
        previous_ref=previous_ref,
        charter_digest="sha256:" + "f" * 64,
        store=store,
        at=at,
        cases=cases,
        intake_pool=intake_pool,
    )
    ImprovementControllerState.get_or_create(pk).record(last_snapshot_ref=ref)
    return ref


def corrupt(store, ref: str) -> None:
    """Lane 4's mutation: re-save the key with other bytes (archiving the
    original), tamper the archive, remove the live file."""
    content_hash, relative_path = ref[len("$CF:") :].split(":", 1)
    model_class_name, filename = relative_path.split("/", 1)
    key = filename[: -len(store.extension)]
    live_path = os.path.join(store.base_path, relative_path)
    store.save(b"{}", key=key, model_class_name=model_class_name)
    archive_path = os.path.join(
        store.base_path, ".versions", content_hash[:2], f"{content_hash}{store.extension}"
    )
    with open(archive_path, "ab") as handle:
        handle.write(b"\n# tampered archive copy\n")
    os.remove(live_path)


class TestEmptyNamespace:
    def test_empty_namespace_reports_empty_not_unavailable(self):
        status = get_control_status(fresh_pk())
        assert status["unavailable"] is False
        assert status["empty"] is True


class TestSeededNamespace:
    def test_seeded_intent_and_paused_head_are_reported(self):
        pk = fresh_pk()
        case = new_case(pk)
        r = transition(
            pk,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="a1",
        )
        admit(
            pk,
            case.id,
            "a1",
            expected_revision=r.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        status = get_control_status(pk)
        assert status["empty"] is False
        assert "admitted" in status["intents_by_state"]
        assert status["intents_by_state"]["admitted"][0]["case_id"] == case.id

    def test_reconciliation_required_intent_is_listed_separately(self):
        pk = fresh_pk()
        case = new_case(pk)
        r = transition(
            pk,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="a1",
        )
        r2 = admit(
            pk,
            case.id,
            "a1",
            expected_revision=r.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        mark_reconciliation_required(
            pk, case.id, "a1", expected_revision=r2.revision, generation=1, from_state="admitted"
        )
        status = get_control_status(pk)
        assert len(status["reconciliation_required"]) == 1
        assert status["reconciliation_required"][0]["action_id"] == "a1"


class TestUnavailable:
    def test_read_failure_reports_unavailable(self, monkeypatch):
        from models.improvement_case import ImprovementCase as Case

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated outage")

        monkeypatch.setattr(Case.query, "filter", _raise)
        status = get_control_status(fresh_pk())
        assert status["unavailable"] is True


# ---------------------------------------------------------------------------
# Ranking (lane 5)
# ---------------------------------------------------------------------------


class TestRanking:
    def test_no_snapshot_yet(self, store):
        result = get_ranking(fresh_pk())
        assert result["no_snapshot_yet"] is True
        assert result["unavailable"] is False
        assert result["order"] == []

    def test_order_carries_movement_and_intake_pool(self, store):
        pk = fresh_pk()
        a = new_case(pk, title="case a", priority_area="inference")
        b = new_case(pk, title="case b", priority_area="skills")
        c = new_case(pk, title="case c")
        first = snapshot(pk, store, [ranked(a.id, 1), ranked(c.id, 2)])
        snapshot(
            pk,
            store,
            [ranked(b.id, 1), ranked(a.id, 2, blocked_by="vault:meta_model_api")],
            previous_ref=first,
            at=T0 + timedelta(hours=1),
            cases=[a, b, c],
            intake_pool=["ev-intake-1"],
        )

        result = get_ranking(pk)

        assert result["unavailable"] is False
        assert result["no_snapshot_yet"] is False
        assert [o["case_id"] for o in result["order"]] == [b.id, a.id]
        by_id = {o["case_id"]: o for o in result["order"]}
        assert by_id[b.id]["title"] == "case b"
        assert by_id[b.id]["movement"] == "entered"
        assert by_id[a.id]["movement"].startswith("moved from 1")
        assert by_id[a.id]["blocked_by"] == "vault:meta_model_api"
        assert result["left"] == [{"case_id": c.id, "reason": "left open set"}]
        assert result["intake_pool"] == ["ev-intake-1"]
        assert result["at"].startswith("2026-09-15T13:00")

    def test_corrupted_snapshot_is_unavailable_with_the_integrity_message(self, store):
        pk = fresh_pk()
        a = new_case(pk, title="case a")
        ref = snapshot(pk, store, [ranked(a.id, 1)])
        corrupt(store, ref)

        result = get_ranking(pk)

        assert result["unavailable"] is True
        assert "does not match its digest" in result["error"]
        assert result["order"] == []
        assert result["no_snapshot_yet"] is False

    def test_goals_reads_positions_from_the_snapshot(self, store):
        pk = fresh_pk()
        a = new_case(pk, title="case a", state="observed")
        b = new_case(pk, title="case b", state="observed")
        snapshot(pk, store, [ranked(b.id, 1), ranked(a.id, 2)])

        goals = get_goals(pk)

        positions = {c["title"]: c["position"] for c in goals["cases"]}
        assert positions == {"case b": 1, "case a": 2}
        assert [c["title"] for c in goals["cases"]] == ["case b", "case a"]
        assert goals["ranking_available"] is True

    def test_goals_without_a_snapshot_has_no_positions(self, store):
        pk = fresh_pk()
        new_case(pk, title="case a", state="observed")

        goals = get_goals(pk)

        assert goals["cases"][0]["position"] is None
        assert goals["ranking_available"] is False


# ---------------------------------------------------------------------------
# Hypotheses (lane 5)
# ---------------------------------------------------------------------------


class TestHypotheses:
    def test_nothing_yet(self):
        result = get_hypotheses(fresh_pk())
        assert result["no_hypotheses_yet"] is True
        assert result["unavailable"] is False

    def test_in_flight_experiments_only(self):
        pk = fresh_pk()
        case = new_case(pk, title="case a")
        frozen = ImprovementExperiment.create(
            project_key=pk,
            created_at=T0,
            state="frozen",
            case_id=case.id,
            hypothesis="rrf_k 30 ranks known items higher",
            mechanism="a smaller k weights top ranks more",
            falsifier="known-item recall at 10 does not rise",
            contract_digest="sha256:" + "a" * 64,
            frozen_at=T0,
        )
        ImprovementExperiment.create(
            project_key=pk,
            created_at=T0 + timedelta(minutes=1),
            state="complete",
            case_id=case.id,
            hypothesis="done already",
        )
        ImprovementExperiment.create(
            project_key=pk,
            created_at=T0 + timedelta(minutes=2),
            state="proposed",
            case_id=case.id,
            hypothesis="not yet frozen",
        )

        result = get_hypotheses(pk)

        assert result["no_hypotheses_yet"] is False
        assert [e["hypothesis"] for e in result["experiments"]] == [
            "not yet frozen",
            "rrf_k 30 ranks known items higher",
        ]
        row = result["experiments"][1]
        assert row["id"] == frozen.id
        assert row["mechanism"] == "a smaller k weights top ranks more"
        assert row["falsifier"] == "known-item recall at 10 does not rise"
        assert row["contract_digest"] == "sha256:" + "a" * 64
        assert row["frozen_at_text"] == "2026-09-15 12:00 UTC"
        assert row["case_title"] == "case a"
        assert result["experiments"][0]["frozen_at_text"] is None

    def test_read_failure_is_unavailable(self, monkeypatch):
        def _raise(*args, **kwargs):
            raise RuntimeError("experiment store unreachable")

        monkeypatch.setattr(ImprovementExperiment.query, "filter", _raise)
        result = get_hypotheses(fresh_pk())
        assert result["unavailable"] is True
        assert "experiment store unreachable" in result["error"]


# ---------------------------------------------------------------------------
# Rejected approaches (lane 5)
# ---------------------------------------------------------------------------


class TestRejectedApproaches:
    def test_nothing_yet(self, store):
        result = get_rejected_approaches(fresh_pk())
        assert result["no_rejected_yet"] is True
        assert result["unavailable"] is False

    def test_rejected_case_with_its_evaluation_and_the_snapshot_it_left(self, store):
        pk = fresh_pk()
        case = new_case(pk, title="rrf_k sweep", priority_area="memory")
        experiment = ImprovementExperiment.create(
            project_key=pk, created_at=T0, state="complete", case_id=case.id, hypothesis="h"
        )
        evaluation = ImprovementEvaluation.create(
            project_key=pk,
            created_at=T0,
            state="complete",
            verdict="reject",
            experiment_id=experiment.id,
            effect=json.dumps({"known_item_recall_at_10": -0.04}),
            confidence_interval=json.dumps(
                {"known_item_recall_at_10": {"lower": -0.09, "upper": 0.01, "n": 40}}
            ),
            notes="paired holdout\nno gain at k=30",
        )
        first = snapshot(pk, store, [ranked(case.id, 1)])
        case.state = "rejected"
        case.rejected_reason = "evaluation rejected the candidate"
        case.evaluation_ids = json.dumps([evaluation.id])
        case.save()
        second = snapshot(
            pk, store, [], previous_ref=first, at=T0 + timedelta(hours=1), cases=[case]
        )

        result = get_rejected_approaches(pk)

        assert result["no_rejected_yet"] is False
        assert len(result["cases"]) == 1
        row = result["cases"][0]
        assert row["title"] == "rrf_k sweep"
        assert row["rejected_reason"] == "evaluation rejected the candidate"
        assert row["evaluation"]["id"] == evaluation.id
        assert row["evaluation"]["verdict"] == "reject"
        assert row["evaluation"]["effects"] == [
            {
                "endpoint": "known_item_recall_at_10",
                "effect": -0.04,
                "interval": {"lower": -0.09, "upper": 0.01, "n": 40},
            }
        ]
        assert row["evaluation"]["notes"] == ["paired holdout", "no gain at k=30"]
        assert row["left_in"]["ref"] == second
        assert row["left_in"]["reason"] == f"rejected: evaluation {evaluation.id}"
        assert row["left_in"]["at"].startswith("2026-09-15T13:00")

    def test_rejected_case_without_an_evaluation_says_so(self, store):
        pk = fresh_pk()
        new_case(pk, title="abandoned", state="rejected", rejected_reason="novelty refused it")

        result = get_rejected_approaches(pk)

        row = result["cases"][0]
        assert row["evaluation"] is None
        assert row["left_in"] is None

    def test_corrupted_chain_keeps_the_cases_and_names_the_error(self, store):
        pk = fresh_pk()
        case = new_case(pk, title="rejected one", state="rejected", rejected_reason="r")
        ref = snapshot(pk, store, [ranked(case.id, 1)])
        corrupt(store, ref)

        result = get_rejected_approaches(pk)

        assert result["unavailable"] is False
        assert result["cases"][0]["title"] == "rejected one"
        assert result["cases"][0]["left_in"] is None
        assert "does not match its digest" in result["snapshot_error"]

    def test_read_failure_is_unavailable(self, monkeypatch, store):
        def _raise(*args, **kwargs):
            raise RuntimeError("case store unreachable")

        monkeypatch.setattr(ImprovementCase.query, "filter", _raise)
        result = get_rejected_approaches(fresh_pk())
        assert result["unavailable"] is True
        assert "case store unreachable" in result["error"]


class TestNoActivityCounter:
    def test_no_getter_result_carries_an_activity_counter(self, store):
        pk = fresh_pk()
        for result in (
            get_ranking(pk),
            get_hypotheses(pk),
            get_rejected_approaches(pk),
            get_goals(pk),
        ):
            assert "experiment_count" not in result
            assert "merged_patch_count" not in result
