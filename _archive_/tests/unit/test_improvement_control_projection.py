"""Projection apply/replay: the head is authoritative (Task 5)."""

from __future__ import annotations

from datetime import UTC, datetime

from models.improvement_case import ImprovementCase
from tools.improvement_control import keys
from tools.improvement_control.journal import read_head, set_state, transition
from tools.improvement_control.projection import apply, replay
from utils.redis_client import text_redis

PK = "test-3215-projection"


def new_case_row() -> ImprovementCase:
    return ImprovementCase.create(
        project_key=PK, state="investigating", title="t", created_at=datetime.now(UTC)
    )


def propose(case_id: str, *, expected_revision: int = 0, action_id: str = "a1") -> int:
    r = transition(
        PK,
        case_id,
        expected_revision=expected_revision,
        generation=1,
        event="action_proposed",
        payload_digest="d1",
        action_id=action_id,
    )
    assert r.accepted, r
    return r.revision


class TestApply:
    def test_apply_writes_state_and_revision_from_the_head(self):
        case = new_case_row()
        propose(case.id)
        apply(PK, case.id)
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.revision == 1
        # The first accepted transition seeds the head's `state` from the row.
        assert reloaded.state == "investigating"

    def test_apply_on_a_case_with_no_head_is_a_no_op(self):
        case = new_case_row()
        apply(PK, case.id)  # no journal activity yet; must not raise
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.revision == 0

    def test_apply_on_a_head_with_empty_state_leaves_the_row_state_alone(self):
        """An empty head `state` never clobbers a real projection value;
        `revision` is still written, and `replay` reports the state the
        row actually holds afterwards."""
        case = new_case_row()
        propose(case.id)
        text_redis().hset(keys.head_key(PK, case.id), "state", "")
        assert read_head(PK, case.id).state == ""

        apply(PK, case.id)
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.state == "investigating"
        assert reloaded.revision == 1

        result = replay(PK, case.id)
        assert result.projection_state_after == "investigating"


class TestHeadStateContract:
    def test_next_accepted_write_reseeds_an_empty_head_state(self):
        """A head whose stored `state` is empty is re-seeded from the row on
        its next accepted transition rather than pinned to "" forever."""
        case = new_case_row()
        rev = propose(case.id)
        text_redis().hset(keys.head_key(PK, case.id), "state", "")
        propose(case.id, expected_revision=rev, action_id="a2")
        assert read_head(PK, case.id).state == "investigating"

    def test_state_changed_is_the_writer_of_head_state_and_apply_projects_it(self):
        case = new_case_row()
        rev = propose(case.id)
        r = set_state(PK, case.id, generation=1, state="experimenting", by="test")
        assert r.accepted, r
        assert r.revision == rev + 1
        assert read_head(PK, case.id).state == "experimenting"

        apply(PK, case.id)
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.state == "experimenting"
        assert reloaded.revision == rev + 1

        # A seed never overwrites what `state_changed` wrote.
        propose(case.id, expected_revision=rev + 1, action_id="a2")
        assert read_head(PK, case.id).state == "experimenting"

    def test_state_changed_refuses_a_name_outside_case_states(self):
        case = new_case_row()
        propose(case.id)
        r = set_state(PK, case.id, generation=1, state="not-a-state", by="test")
        assert r.accepted is False
        assert r.reason == "INVALID_ARGUMENT"
        assert read_head(PK, case.id).state == "investigating"


class TestReplay:
    def test_replay_corrects_a_direct_save_with_a_wrong_state(self):
        """A direct ORM save of `state` is overwritten by the next replay:
        the head is the authority for lifecycle state."""
        case = new_case_row()
        transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        apply(PK, case.id)
        # A direct ORM save bypassing the journal (forbidden in production,
        # simulated here to prove replay corrects it).
        case.state = "rejected"
        case.save()
        result = replay(PK, case.id)
        assert result.fold_reached_head is True
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.state == "investigating"
        assert reloaded.revision == 1

    def test_replay_reports_but_does_not_raise_when_the_tail_is_trimmed(self):
        case = new_case_row()
        rev = 0
        for i in range(6):
            r = transition(
                PK,
                case.id,
                expected_revision=rev,
                generation=1,
                event="action_proposed",
                payload_digest=f"d{i}",
                action_id=f"a{i}",
                journal_max_entries=3,
            )
            assert r.accepted
            rev = r.revision
        apply(PK, case.id)
        result = replay(PK, case.id)
        assert result.fold_reached_head is False
        assert result.first_folded_revision is not None
        # The head is still written even though the fold couldn't reach it.
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.revision == rev
