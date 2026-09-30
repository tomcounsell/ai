"""Reports, verdicts, and what the Scribe wrote. Seams §1.6, verbatim;
architecture §3.1, §5. Written by the tree build because it was absent; the
worker plan keeps it.

The ~500 token summary cap of architecture §3.1 is enforced as 2,000
characters, because a character cap is checkable without a tokenizer.
"""

from typing import Literal

from pydantic import Field, model_validator

from schemas.ids import EpisodeId
from schemas.objective import ArtifactKind, AssumptionStatus, Objective
from schemas.space import Strict


class ArtifactRef(Strict, frozen=True):
    kind: ArtifactKind
    path: str  # inside the mount, e.g. /work/src/x.py or /work/reply.md
    sha256: str  # taken on the host side of the mount by the tool bridge, never by the model


EvidenceKind = Literal[
    "test_output", "diff", "measurement", "excerpt", "tool_log_seq", "effect"
]


class EvidenceRef(Strict, frozen=True):
    kind: EvidenceKind
    ref: str  # a path, a tool log seq, an effect id, or a URL
    sha256: str | None


class AssumptionDelta(Strict, frozen=True):
    statement: str  # equals Contract.assumptions[i].statement
    status: AssumptionStatus
    # a challenge without evidence is refused, architecture §2
    evidence: list[EvidenceRef] = Field(min_length=1)


class Report(Strict, frozen=True):
    """output_type of the Executor and Planner loop."""

    artifact_refs: list[ArtifactRef]
    evidence: list[EvidenceRef]
    assumption_deltas: list[AssumptionDelta]
    summary: str = Field(max_length=2000)


class CheckResult(Strict, frozen=True):
    """A deterministic check the kernel ran before the Verifier read any prose."""

    # tests | build | citations_resolve | sections_present | length |
    # recipient_allowed | no_operator_content
    name: str
    passed: bool
    output_sha256: str
    detail: str = Field(max_length=2000)


class CitationResolution(Strict, frozen=True):
    citation: str
    resolved: bool
    excerpt: str | None  # required when resolved is True
    excerpt_sha256: str | None


class CriterionResult(Strict, frozen=True):
    criterion: str  # equals Contract.success_criteria[i]
    met: bool | None  # None is abstain
    reason: str = Field(max_length=1000)


VerdictOutcome = Literal["pass", "fail", "abstain"]


def derive_outcome(criteria: list[CriterionResult]) -> VerdictOutcome:
    """Any met False is fail, else any None is abstain, else pass."""
    if any(c.met is False for c in criteria):
        return "fail"
    if any(c.met is None for c in criteria):
        return "abstain"
    return "pass"


class Verdict(Strict, frozen=True):
    """output_type of the Verifier loop."""

    outcome: VerdictOutcome
    predicted_failure: float = Field(ge=0, le=1)
    criteria: list[CriterionResult] = Field(min_length=1)
    scope_findings: list[str]
    summary: str = Field(max_length=2000)

    @model_validator(mode="after")
    def outcome_follows_criteria(self) -> "Verdict":
        derived = derive_outcome(self.criteria)
        if self.outcome != derived:
            raise ValueError(
                f"outcome {self.outcome!r} disagrees with the criteria, "
                f"which derive {derived!r} (seams §1.6)"
            )
        return self


class ScribeReport(Strict, frozen=True):
    """Architecture §3.5: what was written and where, what was proposed, what it could not do."""

    written: list[EpisodeId]
    proposed: list[str]  # belief proposal ids
    could_not: list[str]


# The other half of the objective.py cycle: when this module is imported
# first, objective.py's bottom import finds this module mid-import without
# `EvidenceRef` and leaves the rebuild to this line.
Objective.model_rebuild(_types_namespace={"EvidenceRef": EvidenceRef})
