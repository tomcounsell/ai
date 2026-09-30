"""The Objective node and its Contract. Seams §1.4; architecture §2.

`Objective` is the projection `kernel/tree.py` folds from the store, never a
row itself. `Contract` is the part of a node the person approves; any change
is a new revision.

`schemas/report.py` imports `ArtifactKind` and `AssumptionStatus` from here,
and `Objective.evidence` names `EvidenceRef` from there, so the two modules
would import each other at the top. The annotation stays a string, this
module imports `schemas.report` at its bottom once `ArtifactKind` exists, and
whichever module finishes second rebuilds `Objective` (plan 02, critique 8).
"""

from typing import Literal

from pydantic import Field, field_validator

from schemas.budget import Budget, Ceilings
from schemas.ids import BriefId, ConversationId, ObjectiveId, SpaceId
from schemas.space import Strict

AssumptionStatus = Literal["open", "supported", "challenged", "refuted"]


class Assumption(Strict, frozen=True):
    statement: str
    falsification_test: str
    status: AssumptionStatus = "open"


ObjectiveState = Literal[
    "FRAMED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "RUNNING",
    "VERIFYING",
    "SUCCEEDED",
    "FAILED",
    "PAUSED",
    "CANCELLED",
]

StateReason = Literal[
    # AWAITING_APPROVAL
    "commit",
    "awaiting_decision",
    "challenged_assumption",
    "budget_increase",
    "effect_class_elevation",
    # CANCELLED
    "deadline",
    "card_expired",
    "stopped_by_person",
    "subtree_revoked",
    "space_ended",
    # FAILED
    "verification_failed",
    "budget_exhausted",
    "worker_failed",
    # PAUSED
    "away",
    "paused_by_person",
    # SUCCEEDED
    "verified",
    "verification_sampled_out",
    # the rest
    "framed",
    "approved",
    "running",
    "verifying",
]

ArtifactKind = Literal["code", "document", "message", "decision_brief"]


class Contract(Strict, frozen=True):
    """The part of a node the person approves. Any change is a new revision."""

    premise: str
    non_goals: list[str]
    success_criteria: list[str] = Field(min_length=1)
    assumptions: list[Assumption]
    budget: Budget  # the supervisor's estimate in money; the node's root once approved
    basis: str = Field(max_length=500)  # one sentence: seats, turns, verification cost
    ceilings: Ceilings
    task_class: str  # ledger key; M0 uses "code.change" and "message.draft"
    artifact_kind: ArtifactKind  # what the Verifier judges, architecture §5
    root: str  # one of Space.roots; chosen by the supervisor at framing
    inputs: list[str] = []  # inbound item ids and paths the work is about

    @field_validator("artifact_kind")
    @classmethod
    def no_decision_brief_at_m0(cls, v: str) -> str:
        if v == "decision_brief":
            raise ValueError("decision_brief is refused at framing at M0 (seams §1.4)")
        return v

    def assumption(self, statement: str) -> Assumption | None:
        for a in self.assumptions:
            if a.statement == statement:
                return a
        return None


class ReportRef(Strict, frozen=True):
    brief_id: BriefId
    event_id: int  # the report.landed event
    summary: str


class Objective(Strict, frozen=True):
    """The projection kernel/tree.py folds from the store. Never a row itself."""

    id: ObjectiveId
    parent_id: ObjectiveId | None
    depth: int
    space: SpaceId
    conversation_id: ConversationId
    contract: Contract
    contract_revision: int
    approved_revision: int | None
    state: ObjectiveState
    state_reason: StateReason
    generation: int  # 1 + count of brief.stopped events for this node
    budget_consumed: Budget
    budget_allocated: Budget  # sum of live allocations to children
    owner_brief: BriefId | None
    reports: list[ReportRef]
    evidence: list["EvidenceRef"]
    children: list[ObjectiveId]


# The cycle's other half. When this module is imported first, the import
# below runs schemas/report.py to completion and its tail rebuilds
# `Objective`. When schemas/report.py is imported first, it is mid-import
# here, has no `EvidenceRef` yet, and rebuilds `Objective` itself once it
# does; the guard keeps this bottom from failing on the partial module.
import schemas.report as _report  # noqa: E402

if hasattr(_report, "EvidenceRef"):
    Objective.model_rebuild(_types_namespace={"EvidenceRef": _report.EvidenceRef})
