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
  ``digest`` is a plain ``Field`` for the same reason: the index guard rejects
  any indexed field whose name marks it unbounded, so :meth:`load_from_file`
  matches the digest in Python over the project's charter rows.
- **Charter versions are immutable, and loading appends.**
  :meth:`load_from_file` projects ``docs/improvement-charter.md`` into one row
  per unseen digest. It creates or it returns what is already there; it never
  calls ``save()`` on an existing row, never flips a prior row's ``state``, and
  never deletes. An amended charter therefore produces a second ``active`` row
  and leaves the first exactly as it was, which is what keeps an old release
  auditable against the charter it was admitted under. ``state`` keeps its
  two-value vocabulary for a human-driven supersede later; the loader simply
  never writes it. The controller cannot write this model at all — amending its
  own objectives, authority, or budgets is outside every authority it holds.
  Only a human, through the charter file itself or a migration, writes a
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

import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml
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
from popoto.fields.content_field import ContentField

from models.verifying_artifact_store import verifying_artifact_store

logger = logging.getLogger(__name__)

#: The two values ``state`` may hold. Kept as a module constant so the index
#: cardinality is checkable by a test rather than by reading prose.
CHARTER_STATES: tuple[str, ...] = ("active", "superseded")

#: The only human who owns the charter. A file naming anyone else is refused.
CHARTER_OWNER = "Tom Counsell"

#: The charter file, anchored to this module's own checkout rather than to the
#: process cwd. Callers start from different directories — a reflection tick, a
#: FastAPI process, a ``.worktrees/`` checkout — and a cwd-relative default
#: would seed a different file, or none, per caller while a missing file
#: returns None silently.
_CHARTER_PATH = Path(__file__).resolve().parents[1] / "docs" / "improvement-charter.md"


def _parse_frontmatter(text: str) -> dict | None:
    """Return the YAML frontmatter mapping, or None if there is not one.

    None covers all three unusable shapes: no opening fence, no closing fence,
    and a block that is not a mapping. Malformed YAML raises, and the caller
    treats that the same way.
    """
    if not text.startswith("---"):
        return None
    closing = text.find("\n---", 3)
    if closing == -1:
        return None
    parsed = yaml.safe_load(text[3:closing])
    return parsed if isinstance(parsed, dict) else None


def _recency(row) -> float:
    """A row's ``created_at`` as a comparable float.

    Rows read back from Redis may carry a naive datetime; treating those as UTC
    keeps the sort total instead of raising on a naive/aware comparison.
    """
    value = getattr(row, "created_at", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.timestamp()
    return 0.0


class ImprovementCharter(Model):
    """One immutable version of the improvement controller's charter.

    Fields:
        id: Auto-generated Popoto primary key.
        project_key: Partition key; every improvement record carries it.
        created_at: Recency sort, partitioned by ``project_key``.
        state: ``active`` | ``superseded``. Low-cardinality index.
        version: Monotonic version counter. Not indexed (unbounded).
        digest: ``sha256:<hex>`` of the charter file, CRLF-normalized. The
            row's real identity. Not indexed (see the module docstring).
        effective: The date this charter version took effect, as written in
            the file's frontmatter.
        text: The charter's full text, on the verifying artifact store.
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
    digest = Field(null=True)
    effective = Field(null=True)
    text = ContentField(store=verifying_artifact_store)
    scope = Field(null=True)
    authority = Field(null=True)
    budgets = Field(null=True)
    approved_by = Field(null=True)
    approved_at = DatetimeField(null=True)
    notes = Field(null=True)

    @classmethod
    def load_from_file(
        cls,
        path: Path = _CHARTER_PATH,
        project_key: str = "valor",
    ) -> ImprovementCharter | None:
        """Seed one immutable charter row from the charter file.

        Returns the row for this file's digest: the existing one when the
        digest has been seen, a newly created one when it has not, and None
        when the file cannot be read, carries no usable frontmatter, or names
        an owner other than ``Tom Counsell``. Nothing is written on any None
        path.

        Append-only. An amended charter adds a row; the earlier row keeps every
        field it had, ``state`` included. Reading the pinned charter is
        :meth:`pinned`, not a ``state`` filter.

        A missing or non-numeric ``version`` does not refuse the seed. The
        digest is the identity and a version number is a display detail; only
        ``owner`` refuses.

        The default ``path`` is anchored to this module's checkout, so a
        worktree seeds its own branch's charter. The controller tick runs from
        a main checkout and therefore pins main's charter.

        Tolerated race: the digest lookup and the ``create()`` are not atomic,
        so two simultaneous callers can both miss and both create. Duplicate
        rows for one digest are harmless — rows are immutable and identical for
        a given digest — and :meth:`pinned` resolves by newest ``created_at``
        regardless of how many share a digest. Making this a compare-and-set
        would need an atomic primitive in a control namespace that does not
        exist yet.
        """
        # Imported inside the method, not at module scope. `tools.sdlc_verdict`
        # reaches `agent.sdlc_router`, which imports `agent/__init__`, which
        # imports `models/__init__` — so a module-level import here closes a
        # cycle that breaks any process importing the SDLC tools first.
        from tools.sdlc_verdict import compute_plan_hash

        path = Path(path)
        digest = compute_plan_hash(path)
        if digest is None:
            logger.warning("improvement charter: unreadable at %s; not seeding", path)
            return None

        try:
            frontmatter = _parse_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            logger.warning("improvement charter: frontmatter unparseable at %s: %s", path, e)
            return None
        if frontmatter is None:
            logger.warning("improvement charter: no usable frontmatter at %s", path)
            return None

        if frontmatter.get("owner") != CHARTER_OWNER:
            logger.warning(
                "improvement charter: %s declares owner %r, not %r; refusing to seed",
                path,
                frontmatter.get("owner"),
                CHARTER_OWNER,
            )
            return None

        for row in cls.query.filter(project_key=project_key):
            if getattr(row, "digest", None) == digest:
                return row

        fields: dict = {
            "project_key": project_key,
            "created_at": datetime.now(UTC),
            "state": "active",
            "digest": digest,
            "text": path.read_text(encoding="utf-8"),
        }
        effective = frontmatter.get("effective")
        if effective is not None:
            fields["effective"] = str(effective)
        try:
            fields["version"] = int(frontmatter["version"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "improvement charter: %s has no usable version; seeding with the default", path
            )
        return cls.create(**fields)

    @classmethod
    def pinned(cls, project_key: str = "valor") -> ImprovementCharter | None:
        """The charter in force: the newest row in this project's partition.

        Newest by ``created_at``, which is the rule whether or not two rows
        share a digest. Returns None when nothing has been seeded.
        """
        rows = list(cls.query.filter(project_key=project_key))
        if not rows:
            return None
        return max(rows, key=_recency)
