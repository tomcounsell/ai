"""Pydantic mirrors of the three execution records. Seams §1.17, §5.

One class per table with the columns' names and types: `GatewayLogRecord`
(§5.1), `ToolLogRecord` (§5.2), `EffectLedgerRecord` (§5.3). `ToolLogRecord`
is what the adapter passes to `KernelAPI.record_tool`; `id` is `None` until
the insert assigns it and `at` is set by the writer. Beside them,
`Invocation`, the pairing of a `tool.start` with its `tool.end` that the
Verifier reads through `kernel/api.py::invocations` (§3.11).

Depends on nothing outside `schemas/`.
"""

from datetime import datetime
from typing import Any, Literal

from schemas.gateway import CacheState, GatewayEvent, Usage
from schemas.ids import BriefId, ObjectiveId, QuestionId, SpaceId
from schemas.space import EffectClass, Strict

ToolLogEvent = Literal["tool.start", "tool.end", "question", "answer", "terminal"]
ToolName = Literal[
    "read", "write", "edit", "bash", "ask", "write_episode", "propose_belief"
]
TerminalOutcome = Literal["report", "aborted", "failed"]


class GatewayLogRecord(Strict, frozen=True):
    """One `gateway_log` row (seams §5.1)."""

    id: int | None = None
    brief_id: str | None  # may be a turn id; null on an unknown_token refusal
    generation: int | None
    space_id: SpaceId | None
    call: int | None
    event: GatewayEvent
    model: str | None = None
    request_sha256: str | None = None
    estimated_input: int | None = None
    usage: Usage | None = None
    stop_reason: str | None = None
    reason: str | None = None
    cache_state: CacheState | None = None
    schema_version: int = 1
    at: datetime | None = None


class ToolLogRecord(Strict, frozen=True):
    """One `tool_log` row (seams §5.2). Hashes and never content: `write`'s
    input carries `content_sha256`, `edit`'s carries `old_sha256`,
    `new_sha256`, and both lengths."""

    id: int | None = None
    brief_id: BriefId
    generation: int
    space_id: SpaceId
    seq: int | None = None  # tool.start and tool.end share it; null on terminal
    event: ToolLogEvent
    tool: ToolName | None = None  # null on terminal
    input: dict[str, Any] | None = None
    input_sha256: str | None = None
    exit_status: int | None = None
    stdout_sha256: str | None = None
    stderr_sha256: str | None = None
    artifact: str | None = None
    artifact_sha256: str | None = None
    duration_ms: int | None = None
    question_id: QuestionId | None = None
    text: str | None = None
    outcome: TerminalOutcome | None = None
    report: dict[str, Any] | None = None
    schema_version: int = 1
    at: datetime | None = None


EffectEvent = Literal["intent", "outcome", "refused", "reconciled"]
OutcomeKind = Literal["done", "unknown", "recovered", "refused", "failed"]


class EffectLedgerRecord(Strict, frozen=True):
    """One `effect_ledger` row (seams §5.3)."""

    id: int | None = None
    effect_id: str
    space_id: SpaceId
    objective_id: ObjectiveId | None
    brief_id: BriefId | None
    generation: int | None
    action_type: str
    effect_class: EffectClass
    idempotency_key: str
    target: str
    payload_sha256: str
    payload: dict[str, Any]
    event: EffectEvent
    outcome_kind: OutcomeKind | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    approval_id: str | None = None
    schema_version: int = 1
    at: datetime | None = None


class Invocation(Strict, frozen=True):
    """A `tool.start` and what closed it: its `tool.end`, the run's
    `terminal`, or nothing yet (seams §1.17)."""

    start: ToolLogRecord
    end: ToolLogRecord | None
    closed_by: Literal["end", "terminal"] | None
