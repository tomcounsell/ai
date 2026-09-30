"""Episodic memory as it crosses a boundary. Seams §1.14; architecture §6.

Four models: what a caller writes, what a retrieval returns, what a retrieval
asks for, and what the Scribe proposes to the operator record. Memory is
evidence with no authority (architecture, decision 5), so nothing here says
what to do; every model names exactly one space, which is what keeps a
retrieval inside it (architecture, decision 6).

`provenance` is a list of event ids. A summary or a decision compresses
something, so it cites what it compressed (architecture §6: summaries are
additions with provenance links); a raw turn or report may cite nothing,
because `ingest` writes it from the event itself.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from schemas.belief import BeliefKind, SourceClass
from schemas.ids import EpisodeId, SpaceId
from schemas.space import DataClass, Strict

EpisodeKind = Literal["turn", "report", "summary", "decision"]

# The kinds that compress something and so must say what (seams §1.14).
KINDS_NEEDING_PROVENANCE: frozenset[EpisodeKind] = frozenset({"summary", "decision"})


class EpisodeWrite(Strict, frozen=True):
    space: SpaceId
    kind: EpisodeKind
    text: str
    provenance: list[int]  # event ids; min_length 1 for summary and decision
    data_class: DataClass
    regards: str | None = None  # conversation, objective, or brief id

    @model_validator(mode="after")
    def compressed_kinds_cite_their_evidence(self) -> Self:
        if self.kind in KINDS_NEEDING_PROVENANCE and not self.provenance:
            raise ValueError(
                f"an episode of kind {self.kind!r} compresses events and must "
                "cite them: provenance is empty"
            )
        return self


class MemoryHit(Strict, frozen=True):
    space: SpaceId
    episode_id: EpisodeId
    kind: EpisodeKind
    text: str
    provenance: list[int]
    written_at: datetime
    score: float
    regards: str | None
    data_class: DataClass
    text_sha256: str


class SliceQuery(Strict, frozen=True):
    space: SpaceId
    query: str
    k: int = Field(ge=1, le=20)
    kinds: list[EpisodeKind] | None = None
    regards: str | None = None
    max_data_class: DataClass = "PROJECT"


class BeliefProposal(Strict, frozen=True):
    space: SpaceId
    statement: str
    kind: BeliefKind
    domain: str
    supporting_events: list[int] = Field(min_length=1)
    test: str
    # The kernel derives the ceiling from the evidence authors; M0 records it
    # and reads it never (seams §1.14).
    proposed_source_class: SourceClass
