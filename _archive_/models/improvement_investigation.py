"""ImprovementInvestigation — one bounded act of finding something out.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}``; recency
  ``SortedField(created_at, partition_by="project_key")``.
- Two IndexedFields, both low-cardinality: ``kind`` (eight values, see
  :data:`INVESTIGATION_KINDS`) and ``state`` (five values, see
  :data:`INVESTIGATION_STATES`). ``case_id`` is unbounded and stays a plain
  field; a case's investigations are found through the recency partition.
  Eight kinds is the schema gate's default maximum
  (``tests/unit/test_improvement_models.py::DEFAULT_VOCABULARY_MAXIMUM``), so
  a ninth kind costs a reasoned ``VOCABULARY_MAXIMUMS`` entry there.
- **``stage`` is a plain field, never an index** (lane 5, #3217, spike-3).
  The lifecycle an investigation moves through has ten steps, in order:
  ``draft``, ``deduplicated``, ``policy_checked``, ``running``, ``recorded``,
  ``interpreted``, ``applied``, ``cancelled``, ``superseded``, ``failed``. Ten
  is past the gate's eight-value cap, and the only query the loop runs is
  "open or closed", which ``state`` already answers. ``state`` stays the
  open/closed index; ``stage`` is read off the row. The lifecycle helper
  advances it in order and refuses a skip.
- **Five more plain fields** (lane 5, #3217), all ``null=True`` and none
  indexed because each is unbounded or free text: ``sources`` (JSON list of
  ``{url, retrieved_at, title}``, the raw source pointers a claim keeps),
  ``prior_answers`` (JSON list of the investigation and case ids the novelty
  check surfaced), ``expected_information_value`` (free text),
  ``decision_affected`` (free text), and ``assumption_detail`` (JSON
  ``{charter_passage, evidence_ids, confidence, consequence,
  overturning_observation}``, the structured form behind
  ``provisional_assumption``). ``resolved_at`` (a plain ``DatetimeField``)
  is the moment ``resolve()`` closed the row; the assumption digest's
  watermark is the newest ``resolved_at`` it rendered, read off the row.
- **The controller asks no human anything.** There is no question kind, no
  attention queue, no daily question ceiling, and no poll-registry binding on
  this record. Uncertainty is resolved from Tom-sourced memories and online
  research; what cannot be resolved is recorded as a provisional assumption
  with its evidence, on this row, and shown on the dashboard as an assumption
  rather than a fact.
- **A claim with no URL and no retrieval date is not a claim.** ``claims``
  holds JSON entries of ``{claim, url, retrieved_at}``; an entry missing either
  is a note, and the evaluator treats it as one.
- **TTL decision: 30 days, following ``ReflectionRun``.** External claims stale:
  a provider's price or a technique's state of the art has a shelf life, and a
  cached claim outliving its retrieval date is worse than no claim. The durable
  distillate is copied onto the ``ImprovementCase`` (immortal) before the row
  expires, and ``expires_at`` lets an investigation declare a shorter life than
  the TTL when its subject moves faster than a month.

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

#: How the system went about finding out. Low-cardinality on purpose.
INVESTIGATION_KINDS: tuple[str, ...] = (
    "web_research",  # WebSearch / WebFetch on current practice
    "memory_retrieval",  # mining Tom-sourced memories for inspiration
    "trace_analysis",  # reading the system's own transcripts and events
    "probe",  # running something to see what happens
    "resource_acquisition",  # checking what a provider actually offers today
    "charter_amendment",  # a deferred decision that needs Tom's authorization (lane 3, #3215)
    "inspiration_intake",  # charter §4: reading a Tom-sourced inspiration into a case (lane 5)
    "skill_acquisition",  # charter §5: learning a named skill the loop lacks (lane 5, #3217)
)

#: Where the investigation stands. Low-cardinality on purpose.
INVESTIGATION_STATES: tuple[str, ...] = (
    "open",
    "resolved",
    "abandoned",
    "expired",
    "awaiting_authorization",  # sent to Tom, silence is not approval (lane 3, #3215)
)


class ImprovementInvestigation(Model):
    """One bounded act of finding something out, with its sources.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        kind: One of :data:`INVESTIGATION_KINDS`. Low-cardinality index.
        state: One of :data:`INVESTIGATION_STATES`. Low-cardinality index.
        case_id: The ``ImprovementCase`` this serves. Not indexed (unbounded).
        uncertainty: What the system did not know, stated before looking.
        query: The search, fetch, or probe actually issued.
        claims: JSON list of ``{claim, url, retrieved_at}`` entries.
        provisional_assumption: What the system decided to proceed on when the
            uncertainty could not be resolved. Shown on the dashboard as an
            assumption, never as a finding.
        interpretation: What the system concluded, and what would change it.
        expires_at: When these claims should stop being trusted.
        cost_usd: External-LLM dollars this investigation settled against the
            daily reservation.
        charter_digest: The ``sha256:<hex>`` of the charter in force when this
            investigation was admitted. Not indexed (unbounded).
        stage: Where in the ten-step lifecycle this investigation stands (see
            the module docstring). Plain field; ``state`` is the index.
        sources: JSON list of ``{url, retrieved_at, title}`` source pointers.
        prior_answers: JSON list of investigation and case ids the novelty
            check surfaced before this one was opened.
        expected_information_value: What knowing the answer is worth, in
            the planner's own words.
        decision_affected: Which decision the answer changes.
        assumption_detail: JSON ``{charter_passage, evidence_ids, confidence,
            consequence, overturning_observation}`` behind
            ``provisional_assumption``.
        resolved_at: When ``resolve()`` closed the row. Plain, unindexed.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    kind = IndexedField(default="web_research")
    state = IndexedField(default="open")
    case_id = Field(null=True)
    uncertainty = Field(null=True)
    query = Field(null=True)
    claims = Field(null=True)
    provisional_assumption = Field(null=True)
    interpretation = Field(null=True)
    expires_at = DatetimeField(null=True)
    cost_usd = Field(null=True)
    charter_digest = Field(null=True)
    stage = Field(null=True)
    sources = Field(null=True)
    prior_answers = Field(null=True)
    expected_information_value = Field(null=True)
    decision_affected = Field(null=True)
    assumption_detail = Field(null=True)
    resolved_at = DatetimeField(null=True)

    class Meta:
        # 30 days, matching ReflectionRun. Retrieval-dated external claims
        # stale; the durable distillate lives on the immortal case.
        ttl = 86400 * 30
