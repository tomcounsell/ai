"""ImprovementRelease — a qualified candidate put in front of a human.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}``; recency
  ``SortedField(created_at, partition_by="project_key")``.
- One IndexedField, low-cardinality: ``state`` (five values, see
  :data:`RELEASE_STATES`). ``evaluation_id`` is unbounded and stays plain.
- **Automated promotion is disabled and this record does not enable it.** A
  release is a proposal for human review. It becomes ``approved`` only when a
  human approves it, and only once evaluator secrets and production credentials
  are separated from candidate execution and a human-amended charter names the
  reversible surfaces. A worktree is not a security boundary. Both conditions
  are events outside ``docs/plans/recursive-self-improvement.md``.
- **The rollback plan is a field, not a promise.** ``rollback_plan`` and
  ``observation_window_ends_at`` are written at proposal time, before anything
  is exposed, so "we can undo this" is a written commitment rather than a
  reassurance offered after something goes wrong.
- **TTL decision: immortal, no ``Meta.ttl``, like ``Job``.** A release is the
  record of what the system asked to change about itself and what a human
  decided. It is the audit trail; it never expires.

See ``docs/features/improvement-controller.md``.
"""

from __future__ import annotations

from datetime import datetime

from popoto import (
    AutoKeyField,
    DatetimeField,
    Field,
    IndexedField,
    KeyField,
    Model,
    SortedField,
)

#: Where the release stands. Low-cardinality on purpose.
RELEASE_STATES: tuple[str, ...] = (
    "proposed",  # awaiting human review; the only state this build can reach
    "approved",  # a human approved it
    "observing",  # exposed, inside its observation window
    "rolled_back",
    "withdrawn",
)


class ImprovementRelease(Model):
    """A qualified candidate proposed for human review.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        state: One of :data:`RELEASE_STATES`. Low-cardinality index.
        evaluation_id: The ``ImprovementEvaluation`` that qualified it.
        case_id: The originating ``ImprovementCase``.
        surfaces: JSON list of what would change.
        exposure: JSON describing who or what would see the change.
        rollback_plan: How to undo it, written before exposure.
        observation_window_ends_at: When the post-release check comes due.
        approved_by: The human who approved it, when one has.
        approved_at: When they approved it.
        outcome: What actually happened during the observation window.
        charter_digest: The ``sha256:<hex>`` of the charter this release was
            admitted under. Not indexed (unbounded).
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    state = IndexedField(default="proposed")
    evaluation_id = Field(null=True)
    case_id = Field(null=True)
    surfaces = Field(null=True)
    exposure = Field(null=True)
    rollback_plan = Field(null=True)
    observation_window_ends_at = DatetimeField(null=True)
    approved_by = Field(null=True)
    approved_at = DatetimeField(null=True)
    outcome = Field(null=True)
    charter_digest = Field(null=True)
