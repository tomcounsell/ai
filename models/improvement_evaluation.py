"""ImprovementEvaluation — one paired, blinded measurement of a candidate.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}``; recency
  ``SortedField(created_at, partition_by="project_key")``.
- Two IndexedFields, both low-cardinality: ``state`` (three values) and
  ``verdict`` (four values, see :data:`EVALUATION_VERDICTS`).
  ``experiment_id`` and ``contract_digest`` are unbounded and stay plain
  fields.
- **Infra failure is not a candidate failure.** ``infra_failure`` is its own
  verdict, separate from ``reject``. A harness that crashed says nothing about
  the candidate, and collapsing the two would let flaky infrastructure read as
  evidence against a change.
- **Blinding is recorded, not assumed.** ``blinded`` and ``arm_assignment_digest``
  say whether judges saw candidate identity and how arms were assigned. An
  evaluation that cannot state this is not a paired comparison, and the
  dashboard shows it as one that cannot be.
- ``judge_records`` is a ``ContentField`` on the verifying artifact store: the
  raw judge envelopes are re-hashed on every load, so a corrupted evidence file
  invalidates the verdict instead of silently changing it.
- **TTL decision: immortal, no ``Meta.ttl``, like ``Job``.** The verdict and its
  lineage are the product of the entire loop. Level-2 and level-3 claims are
  comparisons across evaluations over time; expiring them would make the claims
  unfalsifiable.

See ``docs/features/improvement-evaluation.md``.
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
from popoto.fields.content_field import ContentField

from models.verifying_artifact_store import verifying_artifact_store

#: Where the evaluation stands. Low-cardinality on purpose.
EVALUATION_STATES: tuple[str, ...] = ("pending", "complete", "invalidated")

#: What the evaluation concluded. ``infra_failure`` is deliberately distinct
#: from ``reject``: a broken harness is not evidence about the candidate.
EVALUATION_VERDICTS: tuple[str, ...] = ("accept", "reject", "inconclusive", "infra_failure")


class ImprovementEvaluation(Model):
    """One frozen-contract comparison of a candidate against its incumbent.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        state: One of :data:`EVALUATION_STATES`. Low-cardinality index.
        verdict: One of :data:`EVALUATION_VERDICTS`. Low-cardinality index.
        experiment_id: The ``ImprovementExperiment`` measured. Not indexed.
        contract_digest: The frozen contract this ran under.
        charter_digest: The ``sha256:<hex>`` of the charter the evaluation ran
            under. Per charter §12, actions complete under the digest they
            carry, so the verdict can be re-read against the exact authority
            it was measured under.
        evaluator_version: Which evaluator produced the numbers.
        holdout_partition: Which holdout split was used, and its rotation epoch.
        blinded: Whether judges saw candidate identity.
        arm_assignment_digest: Digest of the randomized run ordering.
        trials: Number of paired trials completed.
        effect: JSON point estimate per endpoint.
        confidence_interval: JSON bootstrap 95% CI per endpoint.
        correction: The repeated-selection correction applied, named.
        judge_records: Raw judge envelopes, on the verifying artifact store.
        notes: What the evaluator wants a reader to know before believing it.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    state = IndexedField(default="pending")
    verdict = IndexedField(default="inconclusive")
    experiment_id = Field(null=True)
    contract_digest = Field(null=True)
    charter_digest = Field(null=True)
    evaluator_version = Field(null=True)
    holdout_partition = Field(null=True)
    # `type=bool` is load-bearing: without it a queried row hydrates the value
    # as the string "False", which is truthy, and a failed blinding would read
    # as a success to every consumer.
    blinded = Field(type=bool, null=True)
    arm_assignment_digest = Field(null=True)
    trials = IntField(default=0)
    effect = Field(null=True)
    confidence_interval = Field(null=True)
    correction = Field(null=True)
    judge_records = ContentField(store=verifying_artifact_store)
    notes = Field(null=True)
