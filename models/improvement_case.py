"""ImprovementCase — one weakness the system has decided is worth pursuing.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}`` — ``id`` (``AutoKeyField``) for identity,
  ``project_key`` (``KeyField``) for the partition. A single recency
  ``SortedField(created_at, partition_by="project_key")`` serves the "open
  cases for this project" read without an unbounded index.
- Two IndexedFields, both low-cardinality: ``state`` (seven values, see
  :data:`CASE_STATES`) and ``priority`` (four values). ``revision`` is an
  ``IntField`` and is deliberately not indexed — it grows without bound.
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

#: Bounded priority vocabulary.
CASE_PRIORITIES: tuple[str, ...] = ("urgent", "high", "normal", "low")


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
        objective: Which charter objective this case serves.
        updated_at: Last projection write.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    state = IndexedField(default="observed")
    priority = IndexedField(default="normal")
    revision = IntField(default=0)
    title = Field(null=True)
    summary = Field(null=True)
    alternative_explanations = Field(null=True)
    evidence_ids = Field(null=True)
    job_id = Field(null=True)
    charter_version = IntField(default=0)
    objective = Field(null=True)
    updated_at = Field(null=True)
