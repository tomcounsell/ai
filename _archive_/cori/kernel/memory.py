"""Episodic memory and the operator record's proposal path. Seams §3.8;
architecture §6; tech stack §3.1.

Two stores and one direction. The event is the record with standing (tech
stack §12), so every write appends `episode.written` first and writes the
Redis row second; the row is an index over the log and carries the event's
id and timestamp, so it can be checked against the log and rebuilt from it.
Nothing here has authority: memory is evidence (architecture, decision 5).

Every model carries `space` as a key field and every ranked or decaying
field partitions by it, so a retrieval names exactly one space (tech stack
§3.1). Redis has no row-level security; what keeps spaces apart is the space
in every key, a space on every filter, `allowed_keys` on every ranked
search, and the provenance check that refuses evidence from another space.

`REDIS_URL` is set before popoto is imported because popoto connects at
import time and reads only that variable. The default is db 0, the process
default; `tests/conftest.py` binds db 1 before any import.
"""

import os

os.environ.setdefault(
    "REDIS_URL", os.environ.get("CORI_REDIS_URL", "redis://localhost:6379/0")
)

import asyncio  # noqa: E402
import hashlib  # noqa: E402
from datetime import datetime  # noqa: E402
from typing import Sequence, get_args  # noqa: E402

import psycopg  # noqa: E402
from popoto import (  # noqa: E402
    BM25Field,
    DatetimeField,
    IntField,
    KeyField,
    ListField,
    Model,
    SortedField,
    StringField,
    UniqueKeyField,
)

from kernel import events  # noqa: E402
from schemas.belief import PERSON_SOURCE_CLASSES, SourceClass  # noqa: E402
from schemas.events import Event, EventType  # noqa: E402

# Bound as module names, looked up at call time, never captured: a test
# patches `kernel.memory.load_all` with a mapping that covers its own space
# ids. Seams §3.8 gives `write` and `propose` no `spaces` argument, so the
# module name is the injection point (the tree's pattern for
# `check_may_open`).
from kernel.spaces import check_may_open, load_all  # noqa: E402,F401
from schemas.ids import EpisodeId, SpaceId, new_id  # noqa: E402
from schemas.memory import (  # noqa: E402
    BeliefProposal,
    EpisodeKind,
    EpisodeWrite,
    MemoryHit,
    SliceQuery,
)
from schemas.space import UNASSIGNED_SPACE_ID, DataClass  # noqa: E402

# Room for a Scribe summary or a report. popoto's StringField defaults to
# 1024 characters, which a turn passes without trying.
TEXT_MAX = 100_000


class MemoryRefused(Exception):
    """Evidence a write or a proposal may not cite: an event id that names no
    row, or one that belongs to another space. The reason is the message.

    Decided in build: an exception of memory's own rather than `SpaceRefused`,
    because the space refusals here are `kernel.spaces`'s and raise that, and
    a caller that catches one should not be catching the other.
    """


class Episode(Model):
    """One remembered thing inside one space.

    One model holds all four kinds (seams §6): `EpisodeKind` already names
    `summary`, and one model means one retrieval path and one partition
    rule. `kind`, `data_class`, and `regards` are key fields so a
    structure-first retrieval is a set intersection rather than a scan, and
    so the Redis key itself names the partition and the class of every row.
    """

    space = KeyField()  # tech stack §3.1: the partition
    kind = KeyField()  # turn | report | summary | decision
    data_class = KeyField()  # PROJECT | OPERATOR
    regards = KeyField(null=True)  # conversation, objective, or brief id
    episode_id = UniqueKeyField()  # UUIDv7, minted by write()
    text = StringField(max_length=TEXT_MAX)
    text_sha256 = StringField(max_length=64)
    text_bm25 = BM25Field(source="text")  # scoped per query by allowed_keys
    provenance = ListField()  # event ids
    # The `episode.written` row this episode was written with. Property 3 and
    # task 3's test resolve it to the log by primary key, which a payload
    # lookup cannot do as cheaply.
    event_id = IntField()
    written_at = SortedField(type=datetime, partition_by="space")  # the event's time


class Belief(Model):
    """The operator record's row. Defined, unwritten, and unread at M0
    (seams §6): the belief table is a materialized view of the log and the
    view is built at M1. popoto's `ValidityField` for supersession and
    `ConfidenceField` for observed confidence arrive with that write path,
    since a field with no writer is a hook."""

    scope = KeyField()  # a space id or "global"
    id = UniqueKeyField()  # seams §1.13 names it id
    statement = StringField(max_length=TEXT_MAX)
    kind = StringField()
    domain = StringField()
    source_class = StringField()
    status = StringField()
    test = StringField(max_length=TEXT_MAX)
    supersedes = StringField(null=True)
    supporting_events = ListField()
    last_confirmed_at = DatetimeField(null=True)


# ---------------------------------------------------------------------------
# The write path (seams §3.8)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _load_evidence(
    conn: psycopg.AsyncConnection, space: SpaceId, ids: Sequence[int]
) -> dict[int, dict]:
    """`id`, `space_id`, `type`, and the approval kind and session of every
    cited event, refusing a missing one or one from another space.

    Memory's own SQL rather than a new `events` function: it reads three
    columns and never the payload of anything but an approval, and tech stack
    §3 (LOCKED) puts hand-written SQL in the kernel. One statement, whatever
    the list holds, so a refusal costs one round trip.

    The refusal is whole: one foreign or missing id refuses the write, so a
    summary can never be half evidence from another space.
    """
    if not ids:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, space_id, type, "
            "payload->'approval'->>'kind', payload->'approval'->>'session_id' "
            "FROM events WHERE id = ANY(%s)",
            (list(dict.fromkeys(ids)),),
        )
        rows = await cur.fetchall()
    found = {
        int(row[0]): {
            "space_id": row[1],
            "type": row[2],
            "approval_kind": row[3],
            "approval_session_id": row[4],
        }
        for row in rows
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise MemoryRefused(
            f"provenance cites {missing} which no event row carries; "
            "memory says nothing the log did not record"
        )
    foreign = sorted(
        {i for i in ids if found[i]["space_id"] != space},
        key=lambda i: i,
    )
    if foreign:
        raise MemoryRefused(
            f"provenance cites {foreign} from another space; evidence never "
            f"crosses into {space!r}"
        )
    return found


async def write(conn: psycopg.AsyncConnection, write: EpisodeWrite) -> EpisodeId:
    """Append `episode.written`, then write the Redis row. Seams §3.8.

    Event first, row second: the event is the record with standing (tech
    stack §12) and the row is the index over it. The payload carries the
    text, so a row can be checked against, and rebuilt from, the log that has
    standing, which is what makes poisoning a diff you can find
    (architecture §6). `written_at` is the appended event's `occurred_at`,
    read back by primary key in the same transaction, rather than a second
    clock reading.

    A failure writing the row raises inside the caller's transaction, so the
    event is never committed without it. The gap the other way, a caller that
    rolls back after this returns, leaves a row with no event; property 3
    finds it and the M1 retention sweep removes it.
    """
    check_may_open(write.space, load_all())
    await _load_evidence(conn, write.space, write.provenance)

    episode_id = new_id()
    text_sha256 = _sha256(write.text)
    event_id = await events.append(
        conn,
        space_id=write.space,
        type="episode.written",
        payload={
            "episode_id": episode_id,
            "space": write.space,
            "kind": write.kind,
            "provenance": list(write.provenance),
            "data_class": write.data_class,
            "regards": write.regards,
            "text": write.text,
            "text_sha256": text_sha256,
        },
    )
    cur = await conn.execute(
        "SELECT occurred_at FROM events WHERE id = %s", (event_id,)
    )
    row = await cur.fetchone()
    occurred_at: datetime = row[0]

    await asyncio.to_thread(
        Episode.create,
        space=write.space,
        kind=write.kind,
        data_class=write.data_class,
        regards=write.regards,
        episode_id=episode_id,
        text=write.text,
        text_sha256=text_sha256,
        provenance=list(write.provenance),
        event_id=event_id,
        written_at=occurred_at,
    )
    return episode_id


# ---------------------------------------------------------------------------
# The read path (seams §3.8)

# PROJECT admits PROJECT; OPERATOR admits both (seams §1.14). A caller that
# omits the cap gets the narrower slice, which is SliceQuery's default.
VISIBLE_AT: dict[DataClass, tuple[DataClass, ...]] = {
    "PROJECT": ("PROJECT",),
    "OPERATOR": ("PROJECT", "OPERATOR"),
}

ALL_KINDS: tuple[EpisodeKind, ...] = get_args(EpisodeKind)


def _hit(row: Episode, score: float) -> MemoryHit:
    return MemoryHit(
        space=row.space,
        episode_id=row.episode_id,
        kind=row.kind,
        text=row.text,
        provenance=[int(event_id) for event_id in row.provenance],
        written_at=row.written_at,
        score=score,
        regards=row.regards,
        data_class=row.data_class,
        text_sha256=row.text_sha256,
    )


def _candidates(
    space: SpaceId,
    kinds: Sequence[EpisodeKind],
    data_classes: Sequence[DataClass],
    regards: str | None,
) -> dict[str, Episode]:
    """The structure-first step: rows keyed by their redis key.

    `space`, `kind`, `data_class`, and `regards` are key fields, so each
    combination is a set intersection rather than a scan, and every filter
    names the space. popoto's filter takes one value per field, so a query
    over several kinds or both classes is the union of those intersections.
    """
    rows: dict[str, Episode] = {}
    for kind in kinds:
        for data_class in data_classes:
            terms = {"space": space, "kind": kind, "data_class": data_class}
            if regards is not None:
                terms["regards"] = regards
            for row in Episode.query.filter(**terms):
                rows[row.pk] = row
    return rows


async def retrieve(query: SliceQuery) -> list[MemoryHit]:
    """At most `k` hits from exactly one space. Seams §3.8.

    Structure first, then lexical rank. The structure step is what keeps the
    slice inside the space and at or below the class; the rank step is
    popoto's BM25 scoped by `allowed_keys`, so a hit from another space is
    impossible by construction and the limit counts in-space hits.

    Deterministic for identical state: BM25 ties break on the redis key
    inside popoto's script, and a blank query orders by `episode_id`, which
    is a UUIDv7 and so total. A retrieval that reordered between two renders
    of the same state would break the cache and the replay guarantee of tech
    stack §8.

    The door (seams §2.4) has already replaced `max_data_class` with the
    Brief's and checked the space against it, so nothing here re-checks the
    caller.
    """
    kinds = tuple(query.kinds) if query.kinds else ALL_KINDS
    candidates = await asyncio.to_thread(
        _candidates,
        query.space,
        kinds,
        VISIBLE_AT[query.max_data_class],
        query.regards,
    )
    if not candidates:
        return []

    if not query.query.strip():
        newest = sorted(
            candidates.values(), key=lambda row: row.episode_id, reverse=True
        )
        return [_hit(row, 0.0) for row in newest[: query.k]]

    ranked = await asyncio.to_thread(
        BM25Field.search,
        Episode,
        "text_bm25",
        query.query,
        limit=query.k,
        allowed_keys=set(candidates),
    )
    return [_hit(candidates[key], score) for key, score in ranked if key in candidates]


async def latest_summary(space: SpaceId, regards: str) -> MemoryHit | None:
    """The newest summary for one conversation or objective, or None.

    Three key fields, one intersection, no scan and no rank. No data class
    cap: the one caller is the supervisor's render (seams §3.3), which holds
    OPERATOR, and the door does not expose this call, so a worker reaches a
    summary only through `retrieve`, where the cap applies.

    `regards` is required because a summary that belongs to no thread and no
    objective is not something anything rehydrates from.
    """
    candidates = await asyncio.to_thread(
        _candidates, space, ("summary",), VISIBLE_AT["OPERATOR"], regards
    )
    if not candidates:
        return None
    newest = max(candidates.values(), key=lambda row: row.episode_id)
    return _hit(newest, 0.0)


# ---------------------------------------------------------------------------
# ingest (seams §3.8)

# What the kernel copies into memory without a language step, and how.
# Architecture §6: episodic memory ingests raw turns with no extraction;
# architecture §3.5 leaves language to the Scribe, and a copy of a turn needs
# none. Conversation turns are OPERATOR because the person's conversation is
# where OPERATOR slices are in play; a report comes from a worker already
# capped at PROJECT.
INGESTED: dict[str, dict[str, object]] = {
    "message.received": {
        "kind": "turn",
        "text": ("text",),
        "regards": "conversation_id",
        "data_class": "OPERATOR",
    },
    "message.sent": {
        "kind": "turn",
        "text": ("text",),
        "regards": "conversation_id",
        "data_class": "OPERATOR",
    },
    "report.landed": {
        "kind": "report",
        "text": ("report", "summary"),
        "regards": "objective_id",
        "data_class": "PROJECT",
    },
}


async def ingest(conn: psycopg.AsyncConnection, event: Event) -> EpisodeId | None:
    """Remember an appended event, or return None because nothing here does.

    The episode cites the event it was made from and goes through `write`, so
    an ingested episode gets its own `episode.written` row in the log like
    any other and is rebuildable from it.

    The unassigned space remembers nothing: it reads headers and does nothing
    else (architecture §9). That is a None rather than a refusal because the
    supervisor calls this inside the same transaction as the utterance's own
    event, and a refusal would unwind the person's message.
    """
    if event.space_id == UNASSIGNED_SPACE_ID:
        return None
    mapping = INGESTED.get(event.type)
    if mapping is None:
        return None

    text: object = event.payload
    for step in mapping["text"]:
        if not isinstance(text, dict):
            return None
        text = text.get(step)
    if not isinstance(text, str):
        return None

    return await write(
        conn,
        EpisodeWrite(
            space=event.space_id,
            kind=mapping["kind"],
            text=text,
            provenance=[event.id],
            data_class=mapping["data_class"],
            regards=event.payload.get(mapping["regards"]),
        ),
    )


# ---------------------------------------------------------------------------
# The proposal path (seams §3.8)

# The event types a person authors at M0: the three seams §4 events that carry
# a surface session_id or are a kernel-typed utterance of the person's.
PERSON_AUTHORED: frozenset[str] = frozenset(
    {"message.received", "correction.recorded", "approval.minted"}
)

# An approval the kernel minted for itself is not a person's approval
# (seams §1.12), and it shares its event type with one that is.
KERNEL_APPROVAL_KINDS: frozenset[str] = frozenset({"self_approved", "expired"})
KERNEL_SESSION_ID = "kernel"


def derive_ceiling(
    event_types: Sequence[EventType], proposed: SourceClass
) -> SourceClass:
    """The highest source class the evidence can carry. Pure.

    Architecture §6 as written: the proposer chooses among the three person
    classes when every piece of evidence is the person's, and an inference is
    `inferred` whatever the proposer wrote. M0 records the ceiling and reads
    it never.

    An empty list satisfies "every type is person-authored" vacuously and so
    returns `proposed`. `propose` never reaches that branch: a
    `BeliefProposal` carries at least one supporting event (seams §1.14,
    min_length 1).
    """
    if proposed not in PERSON_SOURCE_CLASSES:
        return "inferred"
    if any(event_type not in PERSON_AUTHORED for event_type in event_types):
        return "inferred"
    return proposed


async def propose(conn: psycopg.AsyncConnection, proposal: BeliefProposal) -> str:
    """Record a proposed belief in the log and return its id. Seams §3.8.

    No Redis row: the belief table is a materialized view of the log
    (architecture §6) and the view is built at M1, so at M0 this writes the
    event and nothing reads it.

    A global-scope proposal is refused by the space check, because `"global"`
    has no manifest; who sets a belief's scope is a person's decision
    (architecture §6, PATCHED), not a proposer's.

    The self-approval check lives here rather than in `derive_ceiling`,
    because the seam gives that function types only and a kernel
    self-approval shares its type with the person's approval. A proposal
    citing one lands `inferred`: no person was involved, and M1 reads this
    log.
    """
    check_may_open(proposal.space, load_all())
    evidence = await _load_evidence(conn, proposal.space, proposal.supporting_events)

    kernel_minted = any(
        row["approval_kind"] in KERNEL_APPROVAL_KINDS
        or row["approval_session_id"] == KERNEL_SESSION_ID
        for row in evidence.values()
    )
    types = [evidence[event_id]["type"] for event_id in proposal.supporting_events]
    derived_ceiling = (
        "inferred"
        if kernel_minted
        else derive_ceiling(types, proposal.proposed_source_class)
    )

    proposal_id = new_id()
    await events.append(
        conn,
        space_id=proposal.space,
        type="belief.proposed",
        payload={
            "proposal": proposal.model_dump(mode="json"),
            "proposal_id": proposal_id,
            "derived_ceiling": derived_ceiling,
        },
    )
    return proposal_id
