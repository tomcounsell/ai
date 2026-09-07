"""ImprovementEvidence — one durable observation the improvement loop reasons from.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}`` — ``id`` (``AutoKeyField``) for identity,
  ``project_key`` (``KeyField``) for the partition. A single recency
  ``SortedField(created_at, partition_by="project_key")`` serves every "what
  has the system observed lately" read.
- Two IndexedFields, both low-cardinality: ``kind`` (five values, see
  :data:`EVIDENCE_KINDS`) and ``classification`` (five values, see
  :data:`EVIDENCE_CLASSIFICATIONS`). The schema gate's rule is a cardinality
  rule — never index a pid, uuid, or timestamp. ``source_session_id`` and
  ``source_ref`` are unbounded and are therefore plain fields: dedup reads the
  bounded recency window and filters in Python (:meth:`already_recorded`),
  which is cheap because the window is bounded and the writer runs on a tick,
  not per message.
- **This is the row the loop counts.** The correction detector, the
  memory-inspiration adapter, and the expectation reconciler all write here.
  Rework is derived from rows with ``classification="architectural"``. That
  replaces the retired ``TaskTypeProfile`` aggregate, which was structurally
  always zero because its input field had no writer, so it measured nothing.
  This row's writers are real. A ``SessionEvent`` append is optional
  in-session provenance only: session events are embedded dicts inside a
  ``ListField`` and are not queryable, so nothing may gate on them.
- **Classification is uncertain evidence, never a verdict.** ``unknown`` is the
  honest default and the detector uses it freely. A classification only becomes
  a claim once corroborated, and the dashboard shows the raw counts beside any
  normalized rate so a shrinking count with shrinking detection cannot read as
  a win.
- **TTL decision: 30 days, following ``ReflectionRun``.** These are
  high-volume observation rows on the same footing as reflection run history,
  and the rollup horizon they feed is the same one ``tools/analytics.py --days``
  defaults to. Anything that must outlive the window is distilled onto the
  ``ImprovementCase`` (immortal) before the row expires; widening the window is
  a charter decision, not a builder decision.

See ``docs/features/improvement-controller.md``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from popoto import (
    AutoKeyField,
    DatetimeField,
    Field,
    IndexedField,
    KeyField,
    Model,
    SortedField,
)

logger = logging.getLogger(__name__)

#: What produced this evidence. Low-cardinality on purpose — this is an index.
EVIDENCE_KINDS: tuple[str, ...] = (
    "correction",  # a human corrected the agent mid-flight
    "inspiration",  # a Tom-sourced memory worth researching
    "shipped_work",  # expectation_reconciler's shipped-work evidence
    "owner_liveness",  # expectation_reconciler's owner-liveness evidence
    "other",
)

#: How a correction is read. ``unknown`` is the honest default: classification
#: is uncertain evidence until corroborated.
EVIDENCE_CLASSIFICATIONS: tuple[str, ...] = (
    "architectural",  # a rescue: the work missed the end-to-end journey
    "preference",  # an ordinary taste correction
    "scope",  # new or changed scope, not a defect
    "clarification",  # expected domain clarification
    "unknown",
)

#: How far back :meth:`ImprovementEvidence.already_recorded` looks when
#: deduplicating. Bounded on purpose: the writer runs on a 15-minute tick, so a
#: window this size covers many ticks of overlap without an unbounded scan.
#: Provisional/tunable.
DEDUP_WINDOW = 500

#: Lower bound for the recency range read in :meth:`ImprovementEvidence.recent`.
#: ``SortedField(type=datetime)`` compares against a ``datetime``; an ``int``
#: raises ``AttributeError: 'int' object has no attribute 'tzinfo'`` and turns
#: the bounded read into a silent full-partition scan. The epoch is the widest
#: honest bound: every live row is newer than it, and its presence is what makes
#: popoto push ``limit`` down into the sorted-set range read.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class ImprovementEvidence(Model):
    """One observation the improvement loop can reason from.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key.
        created_at: Recency sort, partitioned by ``project_key``.
        kind: One of :data:`EVIDENCE_KINDS`. Low-cardinality index.
        classification: One of :data:`EVIDENCE_CLASSIFICATIONS`. Low-cardinality
            index. Meaningful for ``kind="correction"``; ``unknown`` elsewhere.
        source_session_id: The ``AgentSession.session_id`` this came from, when
            it came from a session. Used for dedup. Not indexed (unbounded).
        source_ref: A pointer back to the origin — a memory id, a URL, an issue
            number. Not indexed (unbounded).
        text: The observed text itself (the matched correction, the memory body).
        detail: JSON with whatever the producing adapter wants to keep.
        observed_at: When the underlying event happened, which is often well
            before ``created_at`` (the tick that noticed it).
        confidence: The producer's own confidence in ``classification``, 0..1.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    kind = IndexedField(default="correction")
    classification = IndexedField(default="unknown")
    source_session_id = Field(null=True)
    source_ref = Field(null=True)
    text = Field(null=True)
    detail = Field(null=True)
    observed_at = DatetimeField(null=True)
    confidence = Field(null=True)

    class Meta:
        # 30 days, matching ReflectionRun and the tools/analytics.py --days
        # rollup horizon. Declared at the Model level so popoto applies Redis
        # EXPIRE on every save rather than a raw r.expire() call, which would
        # violate the no-raw-Redis-on-Popoto-keys invariant.
        ttl = 86400 * 30

    @classmethod
    def recent(cls, project_key: str, limit: int = 100) -> list[ImprovementEvidence]:
        """Return the newest rows for ``project_key``, newest first.

        Reads the bounded recency partition rather than the whole keyspace, so
        the cost is a function of ``limit`` and not of how long the system has
        been running. The ``created_at__gt`` bound is what makes that true —
        popoto only pushes ``limit`` into the sorted-set range read when a
        ``SortedField`` predicate is present. It is deliberately not wrapped in
        a ``try``: a swallowed failure here degrades silently into the
        unbounded scan this method exists to avoid, which is exactly how the
        defect that motivated this comment survived review. An empty partition
        returns ``[]`` rather than raising, so there is nothing to fall back to.
        """
        rows = list(cls.query.filter(project_key=project_key, created_at__gt=_EPOCH, limit=limit))
        rows.sort(
            key=lambda r: getattr(r, "created_at", None) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return rows[:limit]

    @classmethod
    def already_recorded(
        cls,
        project_key: str,
        kind: str,
        source_ref: str | None = None,
        source_session_id: str | None = None,
    ) -> bool:
        """True when an equivalent row is already in the recent window.

        Dedup is by ``(kind, source_ref)`` when a ``source_ref`` is given —
        that is the memory id or URL that uniquely names the origin — and by
        ``(kind, source_session_id)`` otherwise. Neither field is indexed (both
        are unbounded), so the match happens in Python over
        :data:`DEDUP_WINDOW` recent rows.
        """
        if source_ref is None and source_session_id is None:
            return False
        for row in cls.recent(project_key, limit=DEDUP_WINDOW):
            if getattr(row, "kind", None) != kind:
                continue
            if source_ref is not None and getattr(row, "source_ref", None) == source_ref:
                return True
            if (
                source_ref is None
                and source_session_id is not None
                and getattr(row, "source_session_id", None) == source_session_id
            ):
                return True
        return False

    @classmethod
    def record_once(
        cls,
        project_key: str,
        kind: str,
        *,
        classification: str = "unknown",
        source_ref: str | None = None,
        source_session_id: str | None = None,
        text: str | None = None,
        detail: str | None = None,
        observed_at: datetime | None = None,
        confidence: float | None = None,
    ) -> ImprovementEvidence | None:
        """Write one evidence row unless an equivalent one already exists.

        Returns the new row, or ``None`` when the observation was a duplicate.
        Observer adapters run on a repeating tick and re-read overlapping
        windows by design, so idempotency lives here rather than in each
        adapter.
        """
        if kind not in EVIDENCE_KINDS:
            logger.warning(
                "ImprovementEvidence.record_once: unknown kind %r, recording as 'other'", kind
            )
            kind = "other"
        if classification not in EVIDENCE_CLASSIFICATIONS:
            classification = "unknown"
        if cls.already_recorded(
            project_key, kind, source_ref=source_ref, source_session_id=source_session_id
        ):
            return None
        return cls.create(
            project_key=project_key,
            created_at=datetime.now(UTC),
            kind=kind,
            classification=classification,
            source_ref=source_ref,
            source_session_id=source_session_id,
            text=text,
            detail=detail,
            observed_at=observed_at,
            confidence=confidence,
        )
