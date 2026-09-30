"""The Brief and what a caller hands the tree to get one. Seams §1.5;
architecture §3.1, §3.5; tech stack §4.1, §5.

The block kinds each agent class receives (architecture §3.1, §3.5, §5):
Executor and Planner get voice, node, path, memory, corrections, all PROJECT.
Verifier gets voice, instruction, criteria, artifacts, checks, tool_log,
effect_ledger, all PROJECT, and never a Report's summary or evidence, a node,
path, or memory block. Scribe gets the supervisor's rendered turn as blocks,
which may be OPERATOR, plus instruction; the roll-up travels as rollup and
the trigger as trigger. The adapter offers a class only the tools its Brief's
capabilities cover.
"""

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import SecretStr, model_validator

from schemas.budget import Budget, Ceilings
from schemas.capability import Capabilities
from schemas.ids import BriefId, ObjectiveId, SpaceId
from schemas.sandbox import SandboxProfile, SandboxProfileName, SnapshotRef
from schemas.space import DataClass, Strict

AgentClass = Literal["Planner", "Executor", "Verifier", "Scribe"]
Harness = Literal["pydantic_ai", "valor"]
ReportSchemaName = Literal["Report", "Verdict", "ScribeReport"]

ContextBlockKind = Literal[
    "voice",
    "instruction",
    "node",
    "path",
    "memory",
    "corrections",
    "criteria",
    "artifacts",
    "checks",
    "tool_log",
    "effect_ledger",
    "thread",
    "inbox",
    "operator_digest",
    "rollup",
    "trigger",
]


class ContextBlock(Strict, frozen=True):
    kind: ContextBlockKind
    data_class: DataClass
    text: str
    # event ids, episode ids, artifact paths, tool log seqs this block was rendered from
    sources: list[str]


class ContextSlice(Strict, frozen=True):
    space: SpaceId
    # rendered into the prompt in this order, each under a heading naming its kind
    blocks: list[ContextBlock]
    sha256: str  # ContextSlice.digest(blocks)

    @staticmethod
    def digest(blocks: list[ContextBlock]) -> str:
        """sha256 over the blocks' canonical JSON, keys sorted, no whitespace,
        so field order at construction never changes the hash."""
        canonical = json.dumps(
            [b.model_dump() for b in blocks], sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


class Brief(Strict, frozen=True):
    id: BriefId
    # None only for a Scribe brief fired from a conversation turn with no open objective
    objective_id: ObjectiveId | None
    space: SpaceId
    agent_class: AgentClass
    harness: Harness
    generation: int
    context_slice: ContextSlice
    instruction: str | None  # Scribe explicit trigger; None otherwise
    max_data_class: DataClass  # the kernel refuses a Brief with a block above it
    budget: Budget
    ceilings: Ceilings
    capabilities: Capabilities
    sandbox_profile: SandboxProfile
    model_ref: str  # pinned id from infra/models.yaml
    gateway_token: SecretStr  # the only field never stored; its sha256 is
    report_schema: ReportSchemaName
    issued_at: datetime


class BriefToken(Strict, frozen=True):
    """What every kernel API call carries. Refused when generation is stale."""

    brief_id: BriefId
    generation: int


class DelegateRequest(Strict, frozen=True):
    """What a caller hands kernel/tree.py; the kernel fills the rest of the Brief."""

    # None only for the implicit Scribe of an objective-less turn
    objective_id: ObjectiveId | None
    agent_class: AgentClass
    budget: Budget
    capabilities: Capabilities
    sandbox_profile: SandboxProfileName
    report_schema: ReportSchemaName
    max_data_class: DataClass
    context_slice: ContextSlice
    instruction: str | None = None
    # required for verify: the snapshot to judge; ignored otherwise
    snapshot: SnapshotRef | None = None

    @model_validator(mode="after")
    def only_a_scribe_goes_without_an_objective(self) -> "DelegateRequest":
        if self.objective_id is None and self.agent_class != "Scribe":
            raise ValueError(
                f"objective_id is None only for a Scribe, not {self.agent_class!r}"
            )
        return self

    @model_validator(mode="after")
    def verify_needs_its_snapshot(self) -> "DelegateRequest":
        if self.sandbox_profile == "verify" and self.snapshot is None:
            raise ValueError("a verify request names the snapshot to judge")
        return self
