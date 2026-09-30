"""Plan 05 task 1: the report, trace, and record schemas as seams §1.6,
§1.7, §1.17, with the two validators the plan adds."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from schemas.records import (
    EffectLedgerRecord,
    GatewayLogRecord,
    Invocation,
    ToolLogRecord,
)
from schemas.report import (
    ArtifactRef,
    AssumptionDelta,
    CriterionResult,
    EvidenceRef,
    Report,
    ScribeReport,
    Verdict,
)
from schemas.trace import Question, Terminal, TraceEvent


def evidence():
    return EvidenceRef(kind="test_output", ref="/work/out.txt", sha256=None)


def report(**over):
    fields = dict(
        artifact_refs=[], evidence=[evidence()], assumption_deltas=[], summary="done"
    )
    fields.update(over)
    return Report(**fields)


def start_row(**over):
    fields = dict(
        brief_id="b1",
        generation=1,
        space_id="s1",
        seq=1,
        event="tool.start",
        tool="bash",
        input={"command": "true"},
        input_sha256="0" * 64,
    )
    fields.update(over)
    return ToolLogRecord(**fields)


def test_extra_fields_are_refused():
    with pytest.raises(ValidationError):
        Report(summary="x", artifact_refs=[], evidence=[], assumption_deltas=[], y=1)
    with pytest.raises(ValidationError):
        ToolLogRecord(**start_row().model_dump(), extra="no")
    with pytest.raises(ValidationError):
        Question(question_id="q", text="t", tool_seq=1, extra="no")


def test_records_are_frozen():
    row = start_row()
    with pytest.raises(ValidationError):
        row.seq = 2
    ref = ArtifactRef(kind="code", path="/work/x.py", sha256="a" * 64)
    with pytest.raises(ValidationError):
        ref.sha256 = "b" * 64
    inv = Invocation(start=row, end=None, closed_by=None)
    with pytest.raises(ValidationError):
        inv.closed_by = "end"


def test_report_summary_over_2000_characters_is_refused():
    assert report(summary="x" * 2000).summary == "x" * 2000
    with pytest.raises(ValidationError):
        report(summary="x" * 2001)


def test_assumption_delta_without_evidence_is_refused():
    with pytest.raises(ValidationError):
        AssumptionDelta(statement="tests exist", status="challenged", evidence=[])
    AssumptionDelta(statement="tests exist", status="challenged", evidence=[evidence()])


def test_terminal_report_present_iff_outcome_is_report():
    with pytest.raises(ValidationError):
        Terminal(outcome="report", report=None, error=None)
    with pytest.raises(ValidationError):
        Terminal(outcome="aborted", report=report(), error=None)
    with pytest.raises(ValidationError):
        Terminal(outcome="failed", report=report(), error="boom")
    assert Terminal(outcome="report", report=report(), error=None).report is not None
    assert Terminal(outcome="aborted", report=None, error=None).report is None
    assert Terminal(outcome="failed", report=None, error="boom").error == "boom"


def test_terminal_carries_any_of_the_three_report_types():
    verdict = Verdict(
        outcome="pass",
        predicted_failure=0.1,
        criteria=[CriterionResult(criterion="c", met=True, reason="r")],
        scope_findings=[],
        summary="s",
    )
    scribe = ScribeReport(written=[], proposed=[], could_not=[])
    assert isinstance(
        Terminal(outcome="report", report=verdict, error=None).report, Verdict
    )
    assert isinstance(
        Terminal(outcome="report", report=scribe, error=None).report, ScribeReport
    )


def test_verdict_outcome_follows_its_criteria():
    with pytest.raises(ValidationError):
        Verdict(
            outcome="pass",
            predicted_failure=0.1,
            criteria=[CriterionResult(criterion="c", met=False, reason="r")],
            scope_findings=[],
            summary="s",
        )


def test_trace_event_shape():
    q = Question(question_id="q1", text="which name?", tool_seq=3)
    ev = TraceEvent(
        brief_id="b1",
        generation=1,
        at=datetime.now(UTC),
        kind="question",
        question=q,
        terminal=None,
    )
    assert ev.question.tool_seq == 3
    with pytest.raises(ValidationError):
        Question(question_id="q1", text="x" * 2001, tool_seq=1)


def test_a_5_2_row_round_trips_through_tool_log_record():
    """Every §5.2 column, as a database row would present it."""
    columns = dict(
        id=17,
        brief_id="b1",
        generation=2,
        space_id="s1",
        seq=4,
        event="tool.end",
        tool="write",
        input=None,
        input_sha256=None,
        exit_status=0,
        stdout_sha256="1" * 64,
        stderr_sha256="2" * 64,
        artifact="/work/x.py",
        artifact_sha256="3" * 64,
        duration_ms=12,
        question_id=None,
        text=None,
        outcome=None,
        report=None,
        schema_version=1,
        at=datetime(2026, 9, 22, tzinfo=UTC),
    )
    row = ToolLogRecord(**columns)
    assert row.model_dump() == columns
    assert ToolLogRecord.model_validate(row.model_dump(mode="json")) == row
    terminal = ToolLogRecord(
        brief_id="b1",
        generation=2,
        space_id="s1",
        event="terminal",
        outcome="report",
        report=report().model_dump(mode="json"),
    )
    assert terminal.seq is None and terminal.tool is None and terminal.id is None
    with pytest.raises(ValidationError):
        start_row(event="tool.begin")
    with pytest.raises(ValidationError):
        start_row(tool="push_branch")


def test_the_other_two_records_mirror_their_columns():
    g = GatewayLogRecord(
        brief_id=None, generation=None, space_id=None, call=None, event="refused"
    )
    assert g.reason is None and g.schema_version == 1
    with pytest.raises(ValidationError):
        GatewayLogRecord(brief_id="b", generation=1, space_id="s", call=1, event="x")
    e = EffectLedgerRecord(
        effect_id="e1",
        space_id="s1",
        objective_id=None,
        brief_id=None,
        generation=None,
        action_type="connector_read",
        effect_class="read",
        idempotency_key="k",
        target="gmail:me",
        payload_sha256="0" * 64,
        payload={},
        event="intent",
    )
    assert e.outcome_kind is None
    with pytest.raises(ValidationError):
        e.model_copy(update={"effect_class": "class1"}).model_validate(
            {**e.model_dump(), "effect_class": "class1"}
        )


def test_invocation_pairs_a_start_with_what_closed_it():
    s = start_row()
    e = start_row(event="tool.end", input=None, input_sha256=None, exit_status=0)
    assert Invocation(start=s, end=e, closed_by="end").end.seq == s.seq
    assert Invocation(start=s, end=None, closed_by="terminal").end is None
    with pytest.raises(ValidationError):
        Invocation(start=s, end=None, closed_by="adjacency")
