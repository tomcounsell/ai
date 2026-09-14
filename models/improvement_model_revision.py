"""ImprovementModelRevision — one revision of the system's model of itself.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}``; recency
  ``SortedField(created_at, partition_by="project_key")``.
- One IndexedField, low-cardinality: ``state`` (``current`` / ``superseded``).
  ``revision`` is an ``IntField`` and is deliberately not indexed — a
  monotonic counter is an unbounded index, exactly what the schema gate stops.
- **This is the record that makes recursion checkable.** Claim level 3 is
  "a changed research process produces greater validated gains per comparable
  budget on fresh opportunities". Demonstrating that requires knowing which
  research process was in force when each gain was measured, so every revision
  names what changed, on what evidence, and what it predicts. A revision with
  no falsifiable prediction is a note, and the loop treats it as one.
- **TTL decision: immortal, no ``Meta.ttl``.** These rows are the spine of the
  recursion claim: an evaluation is only interpretable against the model
  revision it ran under. Expiring them would erase the comparison the whole
  system exists to make.

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

#: Whether this revision is the one in force. Low-cardinality on purpose.
MODEL_REVISION_STATES: tuple[str, ...] = ("current", "superseded")


class ImprovementModelRevision(Model):
    """One revision of how the system understands its own behavior.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        state: ``current`` | ``superseded``. Low-cardinality index.
        revision: Monotonic counter. Not indexed (unbounded).
        summary: What changed in the system's model of itself.
        rationale: The evidence that forced the change.
        prediction: What this revision predicts, stated so it can be wrong.
        evidence_ids: JSON list of ``ImprovementEvidence`` ids behind it.
        supersedes_id: The revision this one replaces.
        research_process_digest: Digest of the research process in force, so a
            later comparison can tell whether the process itself changed.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    state = IndexedField(default="current")
    revision = IntField(default=1)
    summary = Field(null=True)
    rationale = Field(null=True)
    prediction = Field(null=True)
    evidence_ids = Field(null=True)
    supersedes_id = Field(null=True)
    research_process_digest = Field(null=True)
