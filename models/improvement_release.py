"""ImprovementRelease — a qualified candidate put in front of a human.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}``; recency
  ``SortedField(created_at, partition_by="project_key")``.
- One IndexedField, low-cardinality: ``state`` (six values, see
  :data:`RELEASE_STATES`). ``evaluation_id`` is unbounded and stays plain.
  ``kind`` holds one of :data:`RELEASE_KINDS`, validated in code and
  deliberately not indexed: three values on an immortal table that already
  carries a ``state`` index is not worth a second index set.
- **Automated promotion is disabled and this record does not enable it.** A
  release is a proposal for human review. It becomes ``approved`` only when a
  human approves it, and only once evaluator secrets and production credentials
  are separated from candidate execution and a human-amended charter names the
  reversible surfaces. A worktree is not a security boundary. Both conditions
  are events outside ``docs/plans/recursive-self-improvement.md``;
  ``tools/improvement_release/promotion.py`` names them and refuses.
- **The rollback plan is a field, not a promise.** ``rollback_plan`` and
  ``observation`` are written at proposal time, before anything is exposed, so
  "we can undo this" is a written commitment rather than a reassurance offered
  after something goes wrong. ``rollback_drill`` records the plan being
  executed for real against the proposed release before approval; ``drill_log``
  is the full transcript on the verifying artifact store, re-hashed on every
  load like ``ImprovementExperiment.manifest``, so a corrupted transcript
  invalidates the drill instead of silently changing it.
- **TTL decision: immortal, no ``Meta.ttl``, like ``Job``.** A release is the
  record of what the system asked to change about itself and what a human
  decided. It is the audit trail; it never expires.

Transitions (``tools/improvement_release/lifecycle.py`` is the sole writer):

    | From                    | Event          | To            |
    |-------------------------|----------------|---------------|
    | (none)                  | ``propose``    | ``proposed``  |
    | ``proposed``            | ``drill``      | ``proposed``  |
    | ``proposed``            | ``approve``    | ``approved``  |
    | ``approved``            | ``open_pr``    | ``approved``  |
    | ``approved``            | ``expose``     | ``observing`` |
    | ``observing``           | ``close_window`` | ``accepted`` on ``held``, else stays |
    | ``observing``, ``accepted`` | ``rollback`` | ``rolled_back`` |
    | ``proposed``, ``approved`` | ``withdraw`` | ``withdrawn`` |

``drill`` and ``open_pr`` write a field without changing state. Every
transition appends ``{event, at, detail}`` to ``outcome["history"]``.

See ``docs/features/improvement-controller.md`` and
``docs/features/improvement-release.md``.
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
from popoto.fields.content_field import ContentField

from models.verifying_artifact_store import verifying_artifact_store

#: Where the release stands. Low-cardinality on purpose.
RELEASE_STATES: tuple[str, ...] = (
    "proposed",  # awaiting a drill and human review
    "approved",  # a human approved it after a passed drill
    "observing",  # exposed, inside its observation window
    "accepted",  # the observation window closed with the gain held
    "rolled_back",
    "withdrawn",
)

#: What kind of surface the release changes. Validated in code, never indexed.
RELEASE_KINDS: tuple[str, ...] = ("core_workflow", "evaluator", "infrastructure")


class ImprovementRelease(Model):
    """A qualified candidate proposed for human review.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        state: One of :data:`RELEASE_STATES`. Low-cardinality index.
        kind: One of :data:`RELEASE_KINDS`. Plain field.
        evaluation_id: The ``ImprovementEvaluation`` that qualified it.
        case_id: The originating ``ImprovementCase``.
        surfaces: JSON list of what would change.
        candidate_ref: The git ref of the candidate as proposed.
        base_revision: The git revision the candidate was built against.
        exposure: JSON describing who or what would see the change; carries
            ``pr_number`` once a PR is open and ``merge_sha`` once exposed.
        exposed_at: When the candidate reached the target branch.
        rollback_plan: How to undo it, written before exposure.
        rollback_drill: JSON summary of the drill that executed the rollback
            plan against this release before approval.
        drill_log: The full drill transcript, on the verifying artifact store.
        observation: JSON observation plan: the metrics to watch, the window,
            and the baseline window, written before exposure.
        observation_window_ends_at: When the post-release check comes due.
        promotion_gate: JSON answer of the promotion gate at approval, so the
            record shows what was true when a human approved it.
        approved_by: The human who approved it, when one has.
        approved_at: When they approved it.
        outcome: What actually happened during the observation window, plus
            the bounded ``history`` list of transition events.
        charter_digest: The ``sha256:<hex>`` of the charter this release was
            admitted under. Not indexed (unbounded).
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    state = IndexedField(default="proposed")
    kind = Field(null=True)
    evaluation_id = Field(null=True)
    case_id = Field(null=True)
    surfaces = Field(null=True)
    candidate_ref = Field(null=True)
    base_revision = Field(null=True)
    exposure = Field(null=True)
    exposed_at = DatetimeField(null=True)
    rollback_plan = Field(null=True)
    rollback_drill = Field(null=True)
    drill_log = ContentField(store=verifying_artifact_store)
    observation = Field(null=True)
    observation_window_ends_at = DatetimeField(null=True)
    promotion_gate = Field(null=True)
    approved_by = Field(null=True)
    approved_at = DatetimeField(null=True)
    outcome = Field(null=True)
    charter_digest = Field(null=True)
