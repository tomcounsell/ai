"""Objective, Contract, Brief, DelegateRequest, Report, Verdict, and the
import order of the objective/report cycle. Plan 02 task 3; seams §1.4 to
§1.7."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr, ValidationError

from schemas.brief import (
    Brief,
    BriefToken,
    ContextBlock,
    ContextSlice,
    DelegateRequest,
)
from schemas.budget import ZERO, Budget, Ceilings
from schemas.capability import Capability
from schemas.objective import Assumption, Contract, Objective, ReportRef
from schemas.report import (
    ArtifactRef,
    AssumptionDelta,
    CriterionResult,
    EvidenceRef,
    Report,
    ScribeReport,
    Verdict,
)
from schemas.sandbox import SandboxProfile, SnapshotRef
from schemas.trace import Question, Terminal, TraceEvent

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
SPACE = "psyoptimal"


def ceilings() -> Ceilings:
    return Ceilings(
        max_effect_class="propose",
        deadline=NOW + timedelta(hours=1),
        max_data_class="PROJECT",
    )


def contract(**over) -> Contract:
    fields = dict(
        premise="fix the failing test",
        non_goals=["refactor"],
        success_criteria=["pytest green"],
        assumptions=[Assumption(statement="tests exist", falsification_test="ls")],
        budget=Budget(usd_micros=1_000_000),
        basis="one Executor turn, two Verifier turns",
        ceilings=ceilings(),
        task_class="code.change",
        artifact_kind="code",
        root="/repo",
        inputs=[],
    )
    fields.update(over)
    return Contract(**fields)


def block(kind="node", text="hello", data_class="PROJECT") -> ContextBlock:
    return ContextBlock(kind=kind, data_class=data_class, text=text, sources=[])


def context_slice(blocks=None) -> ContextSlice:
    blocks = blocks if blocks is not None else [block()]
    return ContextSlice(space=SPACE, blocks=blocks, sha256=ContextSlice.digest(blocks))


def evidence() -> EvidenceRef:
    return EvidenceRef(kind="test_output", ref="/work/out.txt", sha256=None)


def objective() -> Objective:
    return Objective(
        id="o1",
        parent_id=None,
        depth=0,
        space=SPACE,
        conversation_id="c1",
        contract=contract(),
        contract_revision=1,
        approved_revision=None,
        state="FRAMED",
        state_reason="framed",
        generation=1,
        budget_consumed=ZERO,
        budget_allocated=ZERO,
        owner_brief=None,
        reports=[ReportRef(brief_id="b1", event_id=3, summary="done")],
        evidence=[evidence()],
        children=[],
    )


def profile() -> SandboxProfile:
    return SandboxProfile(
        name="scratch",
        space=SPACE,
        mount_source="/tmp/x",
        readonly=True,
        network="hostonly",
        key="b1",
        env={},
    )


def brief() -> Brief:
    return Brief(
        id="b1",
        objective_id="o1",
        space=SPACE,
        agent_class="Executor",
        harness="pydantic_ai",
        generation=1,
        context_slice=context_slice(),
        instruction=None,
        max_data_class="PROJECT",
        budget=Budget(usd_micros=10),
        ceilings=ceilings(),
        capabilities=frozenset(
            {Capability(name="read", effect_class="read", scope=SPACE)}
        ),
        sandbox_profile=profile(),
        model_ref="claude-opus-5",
        gateway_token=SecretStr("secret"),
        report_schema="Report",
        issued_at=NOW,
    )


def snapshot() -> SnapshotRef:
    return SnapshotRef(
        id="s1", handle_id="h1", path="/tmp/s.tar", sha256="00", files={}, taken_at=NOW
    )


def request(**over) -> DelegateRequest:
    fields = dict(
        objective_id="o1",
        agent_class="Executor",
        budget=Budget(usd_micros=10),
        capabilities=frozenset(),
        sandbox_profile="worktree",
        report_schema="Report",
        max_data_class="PROJECT",
        context_slice=context_slice(),
    )
    fields.update(over)
    return DelegateRequest(**fields)


def report() -> Report:
    return Report(
        artifact_refs=[ArtifactRef(kind="code", path="/work/x.py", sha256="ab")],
        evidence=[evidence()],
        assumption_deltas=[
            AssumptionDelta(
                statement="tests exist", status="challenged", evidence=[evidence()]
            )
        ],
        summary="changed x",
    )


def verdict() -> Verdict:
    return Verdict(
        outcome="pass",
        predicted_failure=0.1,
        criteria=[CriterionResult(criterion="pytest green", met=True, reason="ran")],
        scope_findings=[],
        summary="fine",
    )


def trace_event() -> TraceEvent:
    return TraceEvent(
        brief_id="b1",
        generation=1,
        at=NOW,
        kind="terminal",
        question=None,
        terminal=Terminal(outcome="report", report=report(), error=None),
    )


@pytest.mark.parametrize(
    "model",
    [
        contract(),
        objective(),
        context_slice(),
        BriefToken(brief_id="b1", generation=1),
        request(),
        report(),
        verdict(),
        ScribeReport(written=["e1"], proposed=[], could_not=["x"]),
        Question(question_id="q1", text="which?", tool_seq=4),
        trace_event(),
    ],
    ids=lambda m: type(m).__name__,
)
def test_round_trips_through_json(model):
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_brief_round_trips_without_its_token():
    b = brief()
    again = Brief.model_validate_json(b.model_dump_json())
    assert again.gateway_token.get_secret_value() == "**********"
    assert again.model_copy(update={"gateway_token": b.gateway_token}) == b
    assert "secret" not in b.model_dump_json()


def test_extra_field_refused():
    with pytest.raises(ValidationError):
        Contract(**contract().model_dump(), owner="me")
    with pytest.raises(ValidationError):
        BriefToken(brief_id="b1", generation=1, extra=1)


def test_success_criteria_needs_one_entry():
    with pytest.raises(ValidationError):
        contract(success_criteria=[])


def test_decision_brief_refused_at_framing():
    with pytest.raises(ValidationError):
        contract(artifact_kind="decision_brief")


def test_contract_assumption_lookup():
    c = contract()
    assert c.assumption("tests exist").falsification_test == "ls"
    assert c.assumption("nope") is None


def test_delegate_request_refuses_framer():
    with pytest.raises(ValidationError):
        request(agent_class="Framer")


def test_delegate_request_refuses_no_objective_for_executor():
    with pytest.raises(ValidationError):
        request(objective_id=None)


def test_delegate_request_refuses_verify_without_snapshot():
    with pytest.raises(ValidationError):
        request(
            agent_class="Verifier", sandbox_profile="verify", report_schema="Verdict"
        )
    ok = request(
        agent_class="Verifier",
        sandbox_profile="verify",
        report_schema="Verdict",
        snapshot=snapshot(),
    )
    assert ok.snapshot == snapshot()


def test_delegate_request_accepts_objective_less_scribe():
    r = request(
        objective_id=None,
        agent_class="Scribe",
        sandbox_profile="scratch",
        report_schema="ScribeReport",
        instruction="write the turn up",
    )
    assert r.objective_id is None and r.instruction == "write the turn up"


def test_digest_stable_under_block_field_order_and_sensitive_to_text():
    a = ContextBlock(kind="node", data_class="PROJECT", text="t", sources=["1"])
    b = ContextBlock(sources=["1"], text="t", data_class="PROJECT", kind="node")
    assert ContextSlice.digest([a]) == ContextSlice.digest([b])
    assert ContextSlice.digest([a, b]) == ContextSlice.digest([b, a])
    changed = a.model_copy(update={"text": "t2"})
    assert ContextSlice.digest([a]) != ContextSlice.digest([changed])
    assert ContextSlice.digest([a, b]) != ContextSlice.digest([a, changed])
    assert len(ContextSlice.digest([])) == 64


def test_assumption_delta_needs_evidence():
    with pytest.raises(ValidationError):
        AssumptionDelta(statement="x", status="challenged", evidence=[])


def test_verdict_outcome_follows_criteria():
    met = CriterionResult(criterion="a", met=True, reason="")
    unmet = CriterionResult(criterion="b", met=False, reason="")
    abstain = CriterionResult(criterion="c", met=None, reason="")
    base = dict(predicted_failure=0.5, scope_findings=[], summary="")
    assert Verdict(outcome="fail", criteria=[met, unmet, abstain], **base)
    assert Verdict(outcome="abstain", criteria=[met, abstain], **base)
    assert Verdict(outcome="pass", criteria=[met], **base)
    with pytest.raises(ValidationError):
        Verdict(outcome="pass", criteria=[met, abstain], **base)
    with pytest.raises(ValidationError):
        Verdict(outcome="fail", criteria=[met], **base)
    with pytest.raises(ValidationError):
        Verdict(outcome="pass", criteria=[], **base)


@pytest.mark.parametrize("first", ["schemas.objective", "schemas.report"])
def test_import_order(first):
    code = (
        f"import {first}\n"
        "import schemas.objective, schemas.report\n"
        "defs = schemas.objective.Objective.model_json_schema()['$defs']\n"
        "assert 'EvidenceRef' in defs, defs.keys()\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "ok"
