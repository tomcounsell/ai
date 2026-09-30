"""What a worker sends back over the trace. Seams §1.7, verbatim; tech stack
§5; spike 08. Written by the tree build because it was absent; the worker
plan keeps it.

Tool invocations do not travel as trace events. They are tool log rows
(seams §5.2) that the adapter records through the kernel API before and after
each call. A `question` pauses the worker until `answer()`; a `terminal` is
the last event of a run.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from schemas.ids import BriefId, QuestionId
from schemas.report import Report, ScribeReport, Verdict
from schemas.space import Strict


class Question(Strict, frozen=True):
    question_id: QuestionId
    text: str = Field(max_length=2000)
    tool_seq: int  # the tool.start seq of the ask that raised it


class Terminal(Strict, frozen=True):
    outcome: Literal["report", "aborted", "failed"]
    # present iff outcome == "report"; validated against Brief.report_schema
    report: Report | Verdict | ScribeReport | None
    error: str | None

    @model_validator(mode="after")
    def report_present_iff_reported(self) -> "Terminal":
        """Plan 05: `report` is present iff `outcome == "report"`. Which of
        the three types it is gets checked against `Brief.report_schema` by
        the adapter's output type and by `tree.land_report`, which hold the
        Brief; this model does not."""
        if self.outcome == "report" and self.report is None:
            raise ValueError("a terminal with outcome 'report' carries a report")
        if self.outcome != "report" and self.report is not None:
            raise ValueError(
                f"a terminal with outcome {self.outcome!r} carries no report"
            )
        return self


class TraceEvent(Strict, frozen=True):
    brief_id: BriefId
    generation: int
    at: datetime
    kind: Literal["question", "terminal"]
    question: Question | None
    terminal: Terminal | None
