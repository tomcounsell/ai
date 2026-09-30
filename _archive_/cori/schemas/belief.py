"""The operator record's one row. Seams §1.13; architecture §6.

Schema only at M0. The belief table is a materialized view of the log, and
the view is built at M1; `kernel/memory.py::propose` appends the proposal as
an event and writes no belief anywhere. The fields are here so the proposal
path and the M1 reader agree on the shape before either exists.

`source_class` is derived by the kernel from who authored the supporting
events, never taken from a proposer (architecture §6, PATCHED). `scope` is a
space id, or `"global"` for the reserved partition no agent may write.
"""

from datetime import datetime
from typing import Literal

from schemas.ids import SpaceId
from schemas.space import Strict

BeliefKind = Literal["goal", "preference"]
SourceClass = Literal["direct", "correction", "decision", "inferred"]
BeliefStatus = Literal["ACTIVE", "QUARANTINED", "RETIRED", "SUPERSEDED"]

# The three classes a person's own evidence can carry. Anything else derived
# from anything else is `inferred` (architecture §6, who assigns the source
# class, PATCHED).
PERSON_SOURCE_CLASSES: frozenset[SourceClass] = frozenset(
    {"direct", "correction", "decision"}
)


class Belief(Strict, frozen=True):
    id: str
    statement: str
    kind: BeliefKind
    scope: SpaceId | Literal["global"]
    domain: str
    source_class: SourceClass
    supporting_events: list[int]
    status: BeliefStatus
    supersedes: str | None
    last_confirmed_at: datetime | None
    test: str
