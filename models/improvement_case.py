"""ImprovementCase — one weakness the system has decided is worth pursuing.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}`` — ``id`` (``AutoKeyField``) for identity,
  ``project_key`` (``KeyField``) for the partition. A single recency
  ``SortedField(created_at, partition_by="project_key")`` serves the "open
  cases for this project" read without an unbounded index.
- Three IndexedFields, all low-cardinality: ``state`` (seven values, see
  :data:`CASE_STATES`), ``priority`` (four values), and ``priority_area``
  (eleven values, see :data:`PRIORITY_AREAS`). They are three orthogonal
  readings of one case (lifecycle, urgency, and charter §3 classification),
  and the goals partial reads all three. ``revision`` (an ``IntField``) and
  ``charter_digest`` are deliberately not indexed: both are unbounded.
- **The projection is not the authority.** The control journal's Redis head
  holds the authoritative state and revision; this row is the queryable
  projection of it, updated through ORM ``save()`` after the journal commits.
  Every decision reads the head. A replay tool rebuilds this row from the
  journal, so a divergence is repairable rather than fatal. The journal itself
  arrives with lane 3.
- **TTL decision: immortal, no ``Meta.ttl``, like ``Job``.** A case is the
  durable record of what the system believed was wrong and what it did about
  it. Rejected experiments are exactly the memory that stops the loop from
  retrying a dead idea, so an expiring case would make the system rediscover
  its own failures forever.

See ``docs/features/improvement-controller.md``.
"""

from __future__ import annotations

from datetime import datetime

from popoto import (
    AutoKeyField,
    Field,
    IndexedField,
    IntField,
    KeyField,
    Model,
    SortedField,
)

#: The lifecycle a case moves through. Low-cardinality on purpose.
CASE_STATES: tuple[str, ...] = (
    "observed",  # evidence gathered, not yet worked
    "investigating",  # gathering what the system needs to know
    "experimenting",  # a candidate is being built in isolation
    "evaluating",  # paired blinded evaluation is running
    "released",  # a release record exists for human review
    "rejected",  # tried and found not to help; kept as memory
    "paused",  # break-glass, needs a human hand
)

#: The states that end a case's life. ``paused`` is deliberately absent: it is
#: break-glass, a case waiting on a human hand, and still open work.
TERMINAL_CASE_STATES: tuple[str, ...] = ("released", "rejected")

#: The states a case is still open in. Derived from :data:`CASE_STATES` rather
#: than listed, so adding a state to the lifecycle cannot leave the two sets
#: disagreeing about what "open" means. Readers query the ``state`` index one
#: value at a time from this tuple instead of hydrating the partition.
OPEN_CASE_STATES: tuple[str, ...] = tuple(s for s in CASE_STATES if s not in TERMINAL_CASE_STATES)

#: Bounded priority vocabulary.
CASE_PRIORITIES: tuple[str, ...] = ("urgent", "high", "normal", "low")

#: Charter §3's classification of what a case is trying to improve. Five come
#: from the charter's priority list and five from its closing sentence naming
#: the eligible means; ``other`` is what keeps the set from reading as a fixed
#: allocation. Orthogonal to :data:`CASE_PRIORITIES`, which is urgency.
PRIORITY_AREAS: tuple[str, ...] = (
    "inference",
    "token_efficiency",
    "skills",
    "personas",
    "cloud_execution",
    "research_process",
    "evaluators",
    "memory",
    "orchestration",
    "infrastructure",
    "other",
)


class ImprovementCase(Model):
    """A weakness the system is pursuing, with its lineage.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        state: One of :data:`CASE_STATES`. Low-cardinality index.
        priority: One of :data:`CASE_PRIORITIES`. Low-cardinality index.
        revision: The journal revision this projection was built from. Not
            indexed (unbounded).
        title: Short human-readable name.
        summary: What the system thinks is wrong and why it matters.
        alternative_explanations: JSON list of readings the evidence also
            supports. Recorded so a single plausible story cannot pass as the
            only story.
        evidence_ids: JSON list of ``ImprovementEvidence`` ids.
        job_id: The ``Job`` carrying the intended outcome, when one exists.
        charter_version: The charter version in force when the case was opened.
        charter_digest: The ``sha256:<hex>`` of the charter the case was ranked
            under. The version is the human-readable name; this is the identity.
        priority_area: One of :data:`PRIORITY_AREAS`. Low-cardinality index.
        ranking_rationale: Why this case sits where it does in the order.
        updated_at: Last projection write.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    state = IndexedField(default="observed")
    priority = IndexedField(default="normal")
    priority_area = IndexedField(default="other")
    revision = IntField(default=0)
    title = Field(null=True)
    summary = Field(null=True)
    alternative_explanations = Field(null=True)
    evidence_ids = Field(null=True)
    job_id = Field(null=True)
    charter_version = IntField(default=0)
    charter_digest = Field(null=True)
    ranking_rationale = Field(null=True)
    updated_at = Field(null=True)
