"""``tools/improvement_ranking.py`` (#3217, task 5): the five ordinal factor
rules, the lexicographic order with ``blocked``, the immutable snapshot and
its diff, ``latest_snapshot``, and the canonical process-spec bytes lane 6
copies verbatim.

``rank`` is pure and takes plain objects, so the factor tests build light
stand-ins for the ORM rows; the snapshot tests write through a real
``VerifyingArtifactStore`` rooted in ``tmp_path`` (the same corruption shape
``tests/unit/test_improvement_eval_runner.py`` uses), and ``latest_snapshot``
reads the controller-state row in the claimed test DB.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from models.verifying_artifact_store import ArtifactIntegrityError, VerifyingArtifactStore
from tools import improvement_ranking as ranking
from tools.improvement_ranking import (
    STARTING_PRIORITIES,
    RankedCase,
    load_snapshot,
    process_spec_json,
    rank,
    write_snapshot,
)

PK = "test-3217-ranking"
T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
CHARTER = SimpleNamespace(digest="sha256:" + "f" * 64, version=2)


def case(case_id, *, area="other", evidence_ids=(), blocked_by=None, minutes=0, state="observed"):
    return SimpleNamespace(
        id=case_id,
        state=state,
        priority_area=area,
        evidence_ids=json.dumps(list(evidence_ids)),
        blocked_by=blocked_by,
        created_at=T0 + timedelta(minutes=minutes),
        evaluation_ids=None,
    )


def evidence(eid, *, kind="correction", classification="unknown"):
    return SimpleNamespace(id=eid, kind=kind, classification=classification)


def investigation(case_id, *, state="resolved", kind="web_research"):
    return SimpleNamespace(id=f"inv-{case_id}-{state}", case_id=case_id, state=state, kind=kind)


def experiment(case_id, *, state="proposed", surfaces=None):
    return SimpleNamespace(
        id=f"exp-{case_id}-{state}",
        case_id=case_id,
        state=state,
        candidate_surfaces=json.dumps(surfaces) if surfaces is not None else None,
    )


def evaluation(experiment_id, *, verdict="inconclusive"):
    return SimpleNamespace(id=f"eval-{experiment_id}", experiment_id=experiment_id, verdict=verdict)


def revision(evidence_ids):
    return SimpleNamespace(id="rev-1", evidence_ids=json.dumps(list(evidence_ids)))


def factors_of(ranked, case_id):
    return next(r.factors for r in ranked if r.case_id == case_id)


def _rank(cases, **kw):
    kw.setdefault("evidence", [])
    kw.setdefault("investigations", [])
    kw.setdefault("experiments", [])
    kw.setdefault("charter", CHARTER)
    return rank(cases, **kw)


# ---------------------------------------------------------------------------
# One test per factor rule
# ---------------------------------------------------------------------------


class TestOpportunityCost:
    def test_starting_priority_leader_is_high_and_named_means_are_medium(self):
        assert STARTING_PRIORITIES == (
            "inference",
            "token_efficiency",
            "skills",
            "personas",
            "cloud_execution",
        )
        ranked = _rank([case("a", area="inference"), case("b", area="memory"), case("c")])
        assert factors_of(ranked, "a")["opportunity_cost"] == 3
        assert factors_of(ranked, "b")["opportunity_cost"] == 2
        assert factors_of(ranked, "c")["opportunity_cost"] == 1

    def test_second_case_in_the_same_starting_area_is_medium(self):
        ranked = _rank([case("a", area="inference"), case("b", area="inference", minutes=1)])
        assert factors_of(ranked, "a")["opportunity_cost"] == 3
        assert factors_of(ranked, "b")["opportunity_cost"] == 2


class TestQuality:
    def test_architectural_or_three_rows_is_high_two_medium_one_low(self):
        rows = [
            evidence("e1", classification="architectural"),
            evidence("e2"),
            evidence("e3"),
            evidence("e4"),
            evidence("e5"),
            evidence("e6"),
            evidence("e7"),
        ]
        ranked = _rank(
            [
                case("arch", evidence_ids=["e1"]),
                case("three", evidence_ids=["e2", "e3", "e4"], minutes=1),
                case("two", evidence_ids=["e5", "e6"], minutes=2),
                case("one", evidence_ids=["e7"], minutes=3),
            ],
            evidence=rows,
        )
        assert factors_of(ranked, "arch")["quality"] == 3
        assert factors_of(ranked, "three")["quality"] == 3
        assert factors_of(ranked, "two")["quality"] == 2
        assert factors_of(ranked, "one")["quality"] == 1


class TestResourceCost:
    def test_investigation_low_frozen_medium_missing_arm_or_credential_high(self):
        ranked = _rank(
            [
                case("inv"),
                case("frozen", minutes=1),
                case("agent", minutes=2),
                case("vault", minutes=3, blocked_by="vault:meta_model_api"),
            ],
            experiments=[
                experiment("frozen", state="frozen"),
                experiment("agent", state="proposed", surfaces=["agent_run"]),
            ],
        )
        assert factors_of(ranked, "inv")["resource_cost"] == 1
        assert factors_of(ranked, "frozen")["resource_cost"] == 2
        assert factors_of(ranked, "agent")["resource_cost"] == 3
        assert factors_of(ranked, "vault")["resource_cost"] == 3


class TestUncertainty:
    def test_unresolved_high_resolved_medium_cited_low_inconclusive_resets(self):
        ranked = _rank(
            [
                case("fresh", evidence_ids=["e1"]),
                case("resolved", evidence_ids=["e2"], minutes=1),
                case("cited", evidence_ids=["e3"], minutes=2),
                case("incon", evidence_ids=["e4"], minutes=3),
            ],
            evidence=[evidence(f"e{i}") for i in range(1, 5)],
            investigations=[
                investigation("resolved"),
                investigation("cited"),
                investigation("incon"),
            ],
            experiments=[experiment("incon", state="complete")],
            evaluations=[evaluation("exp-incon-complete", verdict="inconclusive")],
            revisions=[revision(["e3"])],
        )
        assert factors_of(ranked, "fresh")["uncertainty"] == 3
        assert factors_of(ranked, "resolved")["uncertainty"] == 2
        assert factors_of(ranked, "cited")["uncertainty"] == 1
        assert factors_of(ranked, "incon")["uncertainty"] == 3


class TestUnlockedCapacity:
    @pytest.mark.parametrize(
        ("area", "expected"),
        [
            ("inference", 3),
            ("cloud_execution", 3),
            ("research_process", 3),
            ("skills", 2),
            ("evaluators", 2),
            ("memory", 2),
            ("orchestration", 2),
            ("personas", 1),
            ("infrastructure", 1),
            ("other", 1),
        ],
    )
    def test_rule_table(self, area, expected):
        ranked = _rank([case("a", area=area)])
        assert factors_of(ranked, "a")["unlocked_capacity"] == expected


# ---------------------------------------------------------------------------
# The order
# ---------------------------------------------------------------------------


class TestOrder:
    def test_lexicographic_key_with_blocked_last(self):
        ranked = _rank(
            [
                case("blocked-inference", area="inference", blocked_by="vault:meta_model_api"),
                case("other", minutes=1),
                case("memory", area="memory", minutes=2),
                case("skills", area="skills", minutes=3),
            ]
        )
        assert [r.case_id for r in ranked] == ["skills", "memory", "other", "blocked-inference"]
        assert [r.position for r in ranked] == [1, 2, 3, 4]
        blocked = ranked[-1]
        assert blocked.blocked_by == "vault:meta_model_api"
        assert blocked.factors["opportunity_cost"] == 3  # keeps its factors, loses only the order

    def test_ties_break_on_created_at(self):
        ranked = _rank([case("later", minutes=5), case("earlier", minutes=0)])
        assert [r.case_id for r in ranked] == ["earlier", "later"]

    def test_reason_names_the_charter_passage_and_each_factor(self):
        ranked = _rank([case("a", area="inference")])
        reason = ranked[0].reason
        assert "charter §3" in reason
        for name in (
            "opportunity_cost",
            "quality",
            "resource_cost",
            "uncertainty",
            "unlocked_capacity",
        ):
            assert name in reason

    def test_rank_of_nothing_is_empty(self):
        assert _rank([]) == []

    def test_ranked_case_shape(self):
        ranked = _rank([case("a")])
        assert isinstance(ranked[0], RankedCase)
        assert set(ranked[0].factors) == {
            "opportunity_cost",
            "quality",
            "resource_cost",
            "uncertainty",
            "unlocked_capacity",
        }
        assert all(v in (1, 2, 3) for v in ranked[0].factors.values())


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return VerifyingArtifactStore(base_path=str(tmp_path / "content"))


class TestSnapshot:
    def test_write_then_load_round_trips_the_canonical_document(self, store):
        ranked = _rank([case("a", area="inference"), case("b", minutes=1)])
        ref = write_snapshot(
            ranked,
            previous_ref=None,
            charter_digest=CHARTER.digest,
            store=store,
            intake_pool=["inv-1"],
            at=T0,
        )
        assert ref.startswith("$CF:")
        doc = load_snapshot(ref, store=store)
        assert doc["schema"] == 1
        assert doc["charter_digest"] == CHARTER.digest
        assert doc["at"] == T0.isoformat()
        assert [o["case_id"] for o in doc["order"]] == ["a", "b"]
        assert doc["order"][0]["position"] == 1
        assert set(doc["order"][0]) == {"case_id", "position", "factors", "reason", "blocked_by"}
        assert doc["intake_pool"] == ["inv-1"]
        assert doc["previous_ref"] is None
        assert doc["diff"] == {"entered": ["a", "b"], "left": [], "moved": []}
        assert ranking.snapshot_digest(ref).startswith("sha256:")

    def test_key_and_model_class_name_follow_freeze_protocol(self, store):
        ref = write_snapshot(
            _rank([case("a")]), previous_ref=None, charter_digest="d", store=store, at=T0
        )
        _digest, relative_path = ref[len("$CF:") :].split(":", 1)
        assert relative_path.startswith("ImprovementRankingSnapshot/ranking-")

    def test_diff_names_entered_left_and_moved(self, store):
        first = write_snapshot(
            _rank([case("a", area="inference"), case("b", minutes=1), case("c", minutes=2)]),
            previous_ref=None,
            charter_digest="d",
            store=store,
            at=T0,
        )
        rejected = case("c", minutes=2, state="rejected")
        rejected.evaluation_ids = json.dumps(["eval-9"])
        second_ranked = _rank([case("b", minutes=1), case("d", area="inference", minutes=3)])
        second = write_snapshot(
            second_ranked,
            previous_ref=first,
            charter_digest="d",
            store=store,
            at=T0 + timedelta(minutes=15),
            cases=[case("a"), case("b", minutes=1), rejected],
        )
        doc = load_snapshot(second, store=store)
        assert doc["previous_ref"] == first
        assert doc["diff"]["entered"] == ["d"]
        assert doc["diff"]["left"] == [
            {"case_id": "a", "reason": "left open set"},
            {"case_id": "c", "reason": "rejected: evaluation eval-9"},
        ]
        # b sat at position 2 in both snapshots: same position is not a move.
        assert doc["diff"]["moved"] == []

    def test_moved_records_from_to_and_why(self, store):
        first = write_snapshot(
            _rank([case("a", area="skills"), case("b", area="skills", minutes=1)]),
            previous_ref=None,
            charter_digest="d",
            store=store,
            at=T0,
        )
        # b gains three evidence rows: quality 1 -> 3, it becomes the skills
        # leader (opportunity_cost 2 -> 3) and overtakes a.
        rows = [evidence("e1"), evidence("e2"), evidence("e3")]
        second = write_snapshot(
            _rank(
                [
                    case("a", area="skills"),
                    case("b", area="skills", minutes=1, evidence_ids=["e1", "e2", "e3"]),
                ],
                evidence=rows,
            ),
            previous_ref=first,
            charter_digest="d",
            store=store,
            at=T0 + timedelta(minutes=15),
        )
        doc = load_snapshot(second, store=store)
        moved = {m["case_id"]: m for m in doc["diff"]["moved"]}
        assert moved["b"]["from"] == 2 and moved["b"]["to"] == 1
        assert "quality 1->3" in moved["b"]["why"]
        assert moved["a"]["from"] == 1 and moved["a"]["to"] == 2

    def test_empty_order_still_writes_and_names_every_previous_case_under_left(self, store):
        first = write_snapshot(
            _rank([case("a"), case("b", minutes=1)]),
            previous_ref=None,
            charter_digest="d",
            store=store,
            at=T0,
        )
        second = write_snapshot(
            rank([], evidence=[], investigations=[], experiments=[], charter=CHARTER),
            previous_ref=first,
            charter_digest="d",
            store=store,
            at=T0 + timedelta(minutes=15),
        )
        doc = load_snapshot(second, store=store)
        assert doc["order"] == []
        assert doc["diff"]["left"] == [
            {"case_id": "a", "reason": "no open cases"},
            {"case_id": "b", "reason": "no open cases"},
        ]

    def test_load_snapshot_integrity_error_propagates(self, store):
        """Corrupt the archive copy the way lane 4's mutation test does: save
        the key again with different bytes (which archives the original),
        tamper the archive, remove the live file."""
        ref = write_snapshot(
            _rank([case("a")]), previous_ref=None, charter_digest="d", store=store, at=T0
        )
        content_hash, relative_path = ref[len("$CF:") :].split(":", 1)
        model_class_name, filename = relative_path.split("/", 1)
        key = filename[: -len(store.extension)]
        live_path = os.path.join(store.base_path, relative_path)
        store.save(b"{}", key=key, model_class_name=model_class_name)
        archive_path = os.path.join(
            store.base_path, ".versions", content_hash[:2], f"{content_hash}{store.extension}"
        )
        assert os.path.exists(archive_path)
        with open(archive_path, "ab") as handle:
            handle.write(b"\n# tampered archive copy\n")
        os.remove(live_path)
        with pytest.raises(ArtifactIntegrityError):
            load_snapshot(ref, store=store)

    def test_latest_snapshot_reads_the_controller_state_row(self, store):
        from models.improvement_controller_state import ImprovementControllerState

        assert ranking.latest_snapshot(PK, store=store) is None
        ref = write_snapshot(
            _rank([case("a")]), previous_ref=None, charter_digest="d", store=store, at=T0
        )
        ImprovementControllerState.get_or_create(PK).record(last_snapshot_ref=ref)
        doc = ranking.latest_snapshot(PK, store=store)
        assert doc is not None
        assert [o["case_id"] for o in doc["order"]] == ["a"]


# ---------------------------------------------------------------------------
# Process spec bytes (lane 6 copies this fixture verbatim)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _FixtureSpec:
    selection_rule: str
    investigation_budget_split: dict
    revision_cadence_seconds: int
    planner_prompt_digest: str
    skill_digest: str
    extra: dict


FIXTURE_SPEC = _FixtureSpec(
    selection_rule="ordinal-lexicographic-v1",
    investigation_budget_split={},
    revision_cadence_seconds=900,
    planner_prompt_digest="sha256:" + "a" * 64,
    skill_digest="sha256:" + "b" * 64,
    extra={"ranking_module_digest": "sha256:" + "c" * 64},
)

FIXTURE_SPEC_BYTES = (
    b'{"extra":{"ranking_module_digest":"sha256:'
    + b"c" * 64
    + b'"},"investigation_budget_split":{},"planner_prompt_digest":"sha256:'
    + b"a" * 64
    + b'","revision_cadence_seconds":900,"selection_rule":"ordinal-lexicographic-v1",'
    b'"skill_digest":"sha256:' + b"b" * 64 + b'"}'
)


def test_process_spec_canonical_bytes():
    assert process_spec_json(FIXTURE_SPEC) == FIXTURE_SPEC_BYTES
    assert process_spec_json(dataclasses.asdict(FIXTURE_SPEC)) == FIXTURE_SPEC_BYTES
    assert json.loads(FIXTURE_SPEC_BYTES) == dataclasses.asdict(FIXTURE_SPEC)
