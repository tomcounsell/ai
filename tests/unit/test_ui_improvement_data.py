"""``ui/data/improvement.py::get_control_status`` (Task 11)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from models.improvement_case import ImprovementCase
from tools.improvement_control.intents import admit, mark_reconciliation_required
from tools.improvement_control.journal import transition
from ui.data.improvement import get_control_status


def fresh_pk() -> str:
    return f"test-3215-ui-data-{uuid.uuid4().hex[:8]}"


def new_case(pk: str) -> ImprovementCase:
    return ImprovementCase.create(
        project_key=pk, state="investigating", title="t", created_at=datetime.now(UTC)
    )


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
