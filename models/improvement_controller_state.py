"""ImprovementControllerState: the planner tick's own non-case state.

Schema (schema-gate ruling for ``docs/plans/recursive-self-improvement.md``,
lane 5, #3217):

- KeyField set = ``{project_key}``: exactly one row per project. The row is a
  cursor, not a record of anything that happened, so it has no ``id``, no
  recency sort, and no history; the ranking snapshots it points at are the
  history, and they live content-addressed in the verifying artifact store.
- **No IndexedField.** Every column is a plain ``Field(null=True)``: a
  ``$CF:`` reference, an ISO timestamp, and a charter digest are each
  unbounded, and the only read is "this project's row", which the key
  answers.
- **Written by ordinary ORM ``save()``, never through the control journal.**
  Lane 3's ``transition`` fences every write against a case head and a case
  lease, and there is no case to fence a tick cursor against. Critique round 3
  of the lane 5 plan removed the invented "case-independent controller head"
  for exactly that reason; this row is what replaced it.
- **TTL decision: immortal, no ``Meta.ttl``.** The cursor is what lets the
  next tick find the previous snapshot and diff against it. An expiring cursor
  would make the first tick after a quiet month write a snapshot whose
  ``diff`` names every case as ``entered``, which is false.

See ``docs/features/improvement-controller.md``.
"""

from __future__ import annotations

from popoto import Field, KeyField, Model


class ImprovementControllerState(Model):
    """One project's planner-tick cursor.

    Fields:
        project_key: The partition, and the whole key.
        last_snapshot_ref: ``$CF:`` reference of the newest ranking snapshot.
        evidence_watermark: ISO timestamp of the newest evidence row the
            last tick read; a scan bound only, never a dedup source.
        last_tick_at: ISO timestamp of the last completed tick.
        charter_digest: The pinned charter digest the last tick ran under.
            A pinned digest that differs from it is how the tick notices an
            amendment landed.
        digest_watermark: ISO timestamp of the newest row the assumption
            digest rendered and delivered (``reflections/
            improvement_assumption_digest.py``). Written only after a
            successful send, so a failed send re-sends next time.
    """

    project_key = KeyField()
    last_snapshot_ref = Field(null=True)
    evidence_watermark = Field(null=True)
    last_tick_at = Field(null=True)
    charter_digest = Field(null=True)
    digest_watermark = Field(null=True)

    @classmethod
    def get(cls, project_key: str) -> ImprovementControllerState | None:
        rows = list(cls.query.filter(project_key=project_key))
        return rows[0] if rows else None

    @classmethod
    def get_or_create(cls, project_key: str) -> ImprovementControllerState:
        existing = cls.get(project_key)
        if existing is not None:
            return existing
        return cls.create(project_key=project_key)

    def record(self, **fields) -> ImprovementControllerState:
        """Set the given cursor fields and save. Unknown names are refused."""
        allowed = {
            "last_snapshot_ref",
            "evidence_watermark",
            "last_tick_at",
            "charter_digest",
            "digest_watermark",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"ImprovementControllerState has no field(s) {sorted(unknown)}")
        for name, value in fields.items():
            setattr(self, name, value)
        self.save()
        return self
