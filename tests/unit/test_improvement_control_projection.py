"""Projection apply/replay: the head is authoritative (Task 5)."""

from __future__ import annotations

from datetime import UTC, datetime

from models.improvement_case import ImprovementCase
from tools.improvement_control.journal import transition
from tools.improvement_control.projection import apply, replay

PK = "test-3215-projection"


def new_case_row() -> ImprovementCase:
    return ImprovementCase.create(
        project_key=PK, state="investigating", title="t", created_at=datetime.now(UTC)
    )


class TestApply:
    def test_apply_writes_state_and_revision_from_the_head(self):
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
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.revision == 1

    def test_apply_on_a_case_with_no_head_is_a_no_op(self):
        case = new_case_row()
        apply(PK, case.id)  # no journal activity yet; must not raise
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.revision == 0


class TestReplay:
    def test_replay_corrects_a_direct_save_with_a_wrong_state(self):
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
        case.revision = 999
        case.save()
        result = replay(PK, case.id)
        assert result.fold_reached_head is True
        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
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
