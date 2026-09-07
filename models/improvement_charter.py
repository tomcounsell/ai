"""ImprovementCharter — the immutable authority record the controller runs under.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``):

- KeyField set = ``{id, project_key}`` — ``id`` (``AutoKeyField``) for identity,
  ``project_key`` (``KeyField``) for the partition every improvement record
  shares. A single recency ``SortedField(created_at, partition_by="project_key")``
  serves the "newest charter for this project" read without an unbounded index.
- One IndexedField, low-cardinality: ``state`` (``active`` / ``superseded``).
  The schema gate's rule is a cardinality rule — never index a pid, uuid,
  version counter, or timestamp — and a two-valued lifecycle field honors it.
  ``version`` is an ``IntField`` and is deliberately **not** indexed: it grows
  without bound, so an index on it is exactly the anti-pattern the gate exists
  to stop. Version lookups go through the recency sort plus a Python filter.
- **Charter versions are immutable.** Amending the charter appends a new row
  and flips the previous row's ``state`` to ``superseded``; a row is never
  edited in place. The controller cannot write this model at all — amending
  its own objectives, authority, or budgets is outside every authority it
  holds. Only a human, through ``valor-improve`` or a migration, writes a
  charter.
- **TTL decision: immortal, no ``Meta.ttl``.** The charter is the record of
  what the system was authorized to do at the time it acted. An evaluation or
  release that cites a charter version must still be able to resolve it years
  later, so an expiring charter would strand the lineage that makes a release
  auditable.

Runtime defaults (the bounds a charter starts from) live in
``config.settings.ImprovementSettings``; this record holds the versioned,
human-approved scope on top of them. See
``docs/features/improvement-controller.md``.
"""

from __future__ import annotations

from datetime import datetime

from popoto import (
    AutoKeyField,
    DatetimeField,
    Field,
    IndexedField,
    IntField,
    KeyField,
    Model,
    SortedField,
)

#: The two values ``state`` may hold. Kept as a module constant so the index
#: cardinality is checkable by a test rather than by reading prose.
CHARTER_STATES: tuple[str, ...] = ("active", "superseded")


class ImprovementCharter(Model):
    """One immutable version of the improvement controller's charter.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key; every improvement record carries it.
        created_at: Recency sort, partitioned by ``project_key``.
        state: ``active`` | ``superseded``. Low-cardinality index.
        version: Monotonic version counter. Not indexed (unbounded).
        scope: JSON describing the authorized research surfaces.
        authority: JSON describing what the controller may decide alone.
        budgets: JSON snapshot of the budget units this version authorizes.
        approved_by: Who approved this version.
        approved_at: When it was approved.
        notes: Free text explaining why this version exists.
    """

    id = AutoKeyField()
    project_key = KeyField()
    created_at = SortedField(type=datetime, partition_by="project_key")
    state = IndexedField(default="active")
    version = IntField(default=1)
    scope = Field(null=True)
    authority = Field(null=True)
    budgets = Field(null=True)
    approved_by = Field(null=True)
    approved_at = DatetimeField(null=True)
    notes = Field(null=True)
