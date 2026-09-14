"""ImprovementExperiment — one candidate change, built and frozen for measurement.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}``; recency
  ``SortedField(created_at, partition_by="project_key")``.
- One IndexedField, low-cardinality: ``state`` (five values, see
  :data:`EXPERIMENT_STATES`). ``case_id`` and ``contract_digest`` are unbounded
  and stay plain fields.
- **The contract is frozen before the arms run.** ``contract_digest`` is the
  ``"sha256:<hex>"`` normalized digest of the preregistered contract — the
  hypothesis, the mechanism, the falsifier, the endpoints, the stopping rule.
  Freezing it before measurement is what stops the endpoint from being chosen
  after the result is visible. An experiment whose contract digest changes
  after ``state="frozen"`` is a new experiment.
- ``manifest`` is a ``ContentField`` on the verifying artifact store, so the
  candidate manifest is re-hashed on every load. A corrupted artifact
  invalidates the evaluation rather than quietly scoring it.
- **TTL decision: immortal, no ``Meta.ttl``.** ``ImprovementEvaluation`` is
  immortal and references this row; expiring the experiment would strand every
  evaluation's lineage and leave a verdict nobody can trace back to what was
  actually run. Rejected experiments are also the system's memory of what has
  already been tried.

See ``docs/features/improvement-evaluation.md``.
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

#: Where the experiment stands. Low-cardinality on purpose.
EXPERIMENT_STATES: tuple[str, ...] = (
    "proposed",  # hypothesis written, contract not yet frozen
    "frozen",  # contract digest fixed; arms may now run
    "running",
    "complete",
    "aborted",  # infra failure or charter violation, never a result
)


class ImprovementExperiment(Model):
    """One candidate change under a frozen, preregistered contract.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        state: One of :data:`EXPERIMENT_STATES`. Low-cardinality index.
        case_id: The ``ImprovementCase`` this serves. Not indexed (unbounded).
        hypothesis: What the change is expected to do.
        mechanism: Why it would do that.
        falsifier: What result would show the hypothesis is wrong.
        contract_digest: ``"sha256:<hex>"`` of the frozen contract.
        candidate_surfaces: JSON list of the surfaces the candidate may modify.
        manifest: The candidate manifest, on the verifying artifact store.
        frozen_at: When the contract was frozen.
        model_revision_id: The system model revision in force.
        charter_version: The charter version in force.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    state = IndexedField(default="proposed")
    case_id = Field(null=True)
    hypothesis = Field(null=True)
    mechanism = Field(null=True)
    falsifier = Field(null=True)
    contract_digest = Field(null=True)
    candidate_surfaces = Field(null=True)
    manifest = ContentField(store=verifying_artifact_store)
    frozen_at = DatetimeField(null=True)
    model_revision_id = Field(null=True)
    charter_version = Field(null=True)
