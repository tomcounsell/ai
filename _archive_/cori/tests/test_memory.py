"""Episodic memory against local Redis and local Postgres. Seams §3.8.

popoto refuses a ranked query that omits the partition key. `query.all()`
still crosses spaces, so "every retrieval names exactly one space" is a
kernel rule enforced in kernel code, with the partition as the backstop.

`REDIS_URL` is bound to db 1 at the top of `tests/conftest.py`, before any
import, because popoto connects at import time and the first module in the
session to import it binds the whole session.
"""

from datetime import UTC, datetime, timedelta

import pytest

popoto = pytest.importorskip("popoto")
from popoto.models.query import QueryException  # noqa: E402
from popoto.redis_db import POPOTO_REDIS_DB  # noqa: E402

from kernel import events, memory  # noqa: E402
from kernel.memory import Belief, Episode  # noqa: E402
from kernel.spaces import SpaceRefused  # noqa: E402
from schemas.events import Event  # noqa: E402
from schemas.ids import new_id  # noqa: E402
from schemas.memory import (  # noqa: E402
    BeliefProposal,
    EpisodeWrite,
    SliceQuery,
)
from schemas.space import UNASSIGNED_SPACE_ID  # noqa: E402

from tests.conftest import requires_postgres  # noqa: E402


def _redis_up() -> bool:
    try:
        return POPOTO_REDIS_DB.ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _redis_up(), reason="local Redis is not reachable")


def test_the_suite_is_bound_to_the_test_database():
    """The binding `tests/conftest.py` makes, asserted where it is used: db 0
    is the developer's own and nothing here may write to it."""
    assert POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] != 0


def make_episode(space: str, **kwargs) -> Episode:
    """An Episode row with the fields `write` would have filled."""
    fields = {
        "space": space,
        "kind": "turn",
        "data_class": "PROJECT",
        "regards": None,
        "episode_id": new_id(),
        "text": "",
        "text_sha256": "0" * 64,
        "provenance": [],
        "event_id": 1,
        "written_at": datetime.now(UTC),
    }
    fields.update(kwargs)
    return Episode.create(**fields)


@pytest.fixture
def two_spaces(space):
    now = datetime.now(UTC)
    a = make_episode(space, text="alpha remembers", written_at=now)
    b = make_episode(
        space + "-b", text="beta remembers", written_at=now - timedelta(hours=1)
    )
    yield space, space + "-b"
    a.delete()
    b.delete()


def test_round_trip_within_one_space(two_spaces):
    alpha, beta = two_spaces
    assert [e.text for e in Episode.query.filter(space=alpha)] == ["alpha remembers"]
    assert [e.text for e in Episode.query.filter(space=beta)] == ["beta remembers"]

    cutoff = datetime.now(UTC) - timedelta(minutes=1)
    assert [
        e.text for e in Episode.query.filter(space=alpha, written_at__gte=cutoff)
    ] == ["alpha remembers"]
    assert list(Episode.query.filter(space=beta, written_at__gte=cutoff)) == []


def test_ranked_query_without_space_is_refused(two_spaces):
    with pytest.raises(QueryException):
        list(Episode.query.filter(written_at__gte=datetime(1970, 1, 1, tzinfo=UTC)))


def test_belief_model_round_trips_on_314(space):
    """The `Belief` model is defined and unread at M0 (seams §6). This is the
    only thing that writes one: the fields popoto has not been exercised on
    here are `UniqueKeyField`, `ListField`, and `DatetimeField`."""
    confirmed = datetime.now(UTC).replace(microsecond=0)
    belief = Belief.create(
        scope=space,
        id=new_id(),
        statement="the person prefers a short weekly note",
        kind="preference",
        domain="correspondence",
        source_class="direct",
        status="ACTIVE",
        test="a note over ten lines is wrong",
        supersedes=None,
        supporting_events=[3, 4],
        last_confirmed_at=confirmed,
    )
    try:
        (read_back,) = list(Belief.query.filter(scope=space))
        assert read_back.id == belief.id
        assert read_back.statement == "the person prefers a short weekly note"
        assert list(read_back.supporting_events) == [3, 4]
        assert read_back.supersedes is None
        assert read_back.last_confirmed_at == confirmed
        assert list(Belief.query.filter(scope=space + "-other")) == []
    finally:
        belief.delete()


# ---------------------------------------------------------------------------
# write (task 3)


async def _seed_event(conn, space: str, type: str = "message.received") -> int:
    """One event to cite. The provenance check reads only id, space, and type."""
    return await events.append(
        conn, space_id=space, type=type, payload={"text": "the person wrote"}
    )


@requires_postgres
async def test_write_appends_event_then_row(kernel, space, spaces_for):
    spaces_for(space)
    evidence = await _seed_event(kernel, space)
    episode_id = await memory.write(
        kernel,
        EpisodeWrite(
            space=space,
            kind="summary",
            text="the thread so far",
            provenance=[evidence],
            data_class="PROJECT",
            regards="c1",
        ),
    )
    try:
        (row,) = list(Episode.query.filter(space=space, episode_id=episode_id))
        cur = await kernel.execute(
            "SELECT id, occurred_at, payload FROM events "
            "WHERE type = 'episode.written' AND payload->>'episode_id' = %s",
            (episode_id,),
        )
        event_id, occurred_at, payload = await cur.fetchone()

        assert payload["text_sha256"] == row.text_sha256
        assert row.event_id == event_id
        assert row.written_at == occurred_at
        assert payload["text"] == "the thread so far"
        assert payload["provenance"] == [evidence]
        assert row.data_class == "PROJECT"
        assert row.regards == "c1"
    finally:
        for row in list(Episode.query.filter(space=space, episode_id=episode_id)):
            row.delete()
        await kernel.rollback()


@requires_postgres
async def test_write_refuses_foreign_provenance(kernel, space, spaces_for):
    """Evidence from another space refuses the write whole: no row, and after
    the caller's rollback no event either."""
    other = space + "-other"
    spaces_for(space, other)
    foreign = await _seed_event(kernel, other)

    with pytest.raises(memory.MemoryRefused):
        await memory.write(
            kernel,
            EpisodeWrite(
                space=space,
                kind="summary",
                text="borrowed",
                provenance=[foreign],
                data_class="PROJECT",
            ),
        )
    with pytest.raises(memory.MemoryRefused):
        await memory.write(
            kernel,
            EpisodeWrite(
                space=space,
                kind="summary",
                text="invented",
                provenance=[foreign + 10_000_000],
                data_class="PROJECT",
            ),
        )

    assert list(Episode.query.filter(space=space)) == []
    await kernel.rollback()
    cur = await kernel.execute(
        "SELECT count(*) FROM events WHERE space_id = %s", (space,)
    )
    assert (await cur.fetchone())[0] == 0


@requires_postgres
async def test_write_refuses_unassigned_and_unknown_space(kernel, space, spaces_for):
    """The unpatched fixture id is the unknown space: `spaces_for` is given
    another id, so this one has no manifest."""
    spaces_for(space + "-known")
    for target in (space, UNASSIGNED_SPACE_ID):
        with pytest.raises(SpaceRefused):
            await memory.write(
                kernel,
                EpisodeWrite(
                    space=target,
                    kind="turn",
                    text="hello",
                    provenance=[],
                    data_class="PROJECT",
                ),
            )
    await kernel.rollback()


# ---------------------------------------------------------------------------
# retrieve (task 4)


@pytest.fixture
async def written(kernel, space, spaces_for):
    """A write helper bound to one space, cleaning up every row it made."""
    spaces_for(space, space + "-other")
    made: list[str] = []

    async def _write(text: str, **kwargs) -> str:
        fields = {
            "space": space,
            "kind": "turn",
            "text": text,
            "provenance": [],
            "data_class": "PROJECT",
        }
        fields.update(kwargs)
        episode_id = await memory.write(kernel, EpisodeWrite(**fields))
        made.append((fields["space"], episode_id))
        return episode_id

    yield _write
    for made_space, episode_id in made:
        for row in list(Episode.query.filter(space=made_space, episode_id=episode_id)):
            row.delete()
    await kernel.rollback()


@requires_postgres
async def test_query_ranks_within_space(written, space):
    await written("the deploy went out on friday")
    await written("lunch was quiet")
    await written("a deploy needs a deploy window", space=space + "-other")

    hits = await memory.retrieve(SliceQuery(space=space, query="deploy", k=5))
    assert [hit.text for hit in hits] == ["the deploy went out on friday"]
    assert hits[0].score > 0
    assert hits[0].space == space


@requires_postgres
async def test_blank_query_returns_most_recent_first(written, space):
    first = await written("first")
    second = await written("second")
    third = await written("third")

    hits = await memory.retrieve(SliceQuery(space=space, query="", k=2))
    assert [hit.episode_id for hit in hits] == [third, second]
    assert [hit.score for hit in hits] == [0.0, 0.0]

    all_three = await memory.retrieve(SliceQuery(space=space, query="   ", k=20))
    assert [hit.episode_id for hit in all_three] == [third, second, first]


@requires_postgres
async def test_kinds_and_regards_filter(written, space):
    await written("a turn about the report", regards="c1")
    await written("a report about the report", kind="report", regards="o1")

    reports = await memory.retrieve(
        SliceQuery(space=space, query="report", k=5, kinds=["report"])
    )
    assert [hit.kind for hit in reports] == ["report"]

    by_thread = await memory.retrieve(
        SliceQuery(space=space, query="report", k=5, regards="c1")
    )
    assert [hit.regards for hit in by_thread] == ["c1"]

    both = await memory.retrieve(SliceQuery(space=space, query="report", k=5))
    assert len(both) == 2


@requires_postgres
async def test_project_caller_never_sees_operator_episode(written, space):
    await written("the person said something private", data_class="OPERATOR")
    await written("the worker said something", data_class="PROJECT")

    capped = await memory.retrieve(SliceQuery(space=space, query="said", k=5))
    assert [hit.data_class for hit in capped] == ["PROJECT"]

    uncapped = await memory.retrieve(
        SliceQuery(space=space, query="said", k=5, max_data_class="OPERATOR")
    )
    assert {hit.data_class for hit in uncapped} == {"PROJECT", "OPERATOR"}

    blank = await memory.retrieve(SliceQuery(space=space, query="", k=20))
    assert [hit.data_class for hit in blank] == ["PROJECT"]


# ---------------------------------------------------------------------------
# latest_summary (task 5)


@requires_postgres
async def test_latest_summary_returns_newest_for_regards(kernel, written, space):
    evidence = await _seed_event(kernel, space)
    await written(
        "the thread so far", kind="summary", provenance=[evidence], regards="c1"
    )
    newest = await written(
        "the thread as of now", kind="summary", provenance=[evidence], regards="c1"
    )

    hit = await memory.latest_summary(space, "c1")
    assert hit.episode_id == newest
    assert hit.text == "the thread as of now"
    assert hit.score == 0.0
    assert hit.kind == "summary"


@requires_postgres
async def test_latest_summary_none_when_absent(written, space):
    await written("a turn, not a summary", regards="c1")
    assert await memory.latest_summary(space, "c1") is None


@requires_postgres
async def test_latest_summary_ignores_other_kinds_regards_and_spaces(
    kernel, written, space
):
    """A summary in another thread, another space, or of another kind is not
    this thread's summary. The OPERATOR one is returned: this call carries no
    data class cap, because the render that calls it holds OPERATOR."""
    evidence = await _seed_event(kernel, space)
    other_space = space + "-other"
    other_evidence = await _seed_event(kernel, other_space)
    await written("another thread", kind="summary", provenance=[evidence], regards="c2")
    await written(
        "another space",
        space=other_space,
        kind="summary",
        provenance=[other_evidence],
        regards="c1",
    )
    await written("a decision", kind="decision", provenance=[evidence], regards="c1")
    mine = await written(
        "this thread",
        kind="summary",
        provenance=[evidence],
        regards="c1",
        data_class="OPERATOR",
    )

    hit = await memory.latest_summary(space, "c1")
    assert hit.episode_id == mine
    assert hit.data_class == "OPERATOR"


# ---------------------------------------------------------------------------
# ingest (task 6)


async def _appended(kernel, space: str, type: str, payload: dict) -> Event:
    """Append an event and read it back as the record `ingest` takes."""
    event_id = await events.append(kernel, space_id=space, type=type, payload=payload)
    cur = await kernel.execute(
        "SELECT id, space_id, type, schema_version, occurred_at, payload "
        "FROM events WHERE id = %s",
        (event_id,),
    )
    row = await cur.fetchone()
    return Event(
        id=row[0],
        space_id=row[1],
        type=row[2],
        schema_version=row[3],
        occurred_at=row[4],
        payload=row[5],
    )


@requires_postgres
async def test_ingest_maps_three_event_types(kernel, space, spaces_for):
    spaces_for(space)
    cases = [
        (
            "message.received",
            {"text": "can you look at the invoice", "conversation_id": "c1"},
            ("turn", "can you look at the invoice", "c1", "OPERATOR"),
        ),
        (
            "message.sent",
            {"text": "looking now", "conversation_id": "c1"},
            ("turn", "looking now", "c1", "OPERATOR"),
        ),
        (
            "report.landed",
            {"report": {"summary": "the invoice is paid"}, "objective_id": "o1"},
            ("report", "the invoice is paid", "o1", "PROJECT"),
        ),
    ]
    try:
        for type, payload, expected in cases:
            event = await _appended(kernel, space, type, payload)
            episode_id = await memory.ingest(kernel, event)
            (row,) = list(Episode.query.filter(space=space, episode_id=episode_id))
            assert (row.kind, row.text, row.regards, row.data_class) == expected
            assert list(row.provenance) == [event.id]
    finally:
        for row in list(Episode.query.filter(space=space)):
            row.delete()
        await kernel.rollback()


@requires_postgres
async def test_ingest_ignores_other_types(kernel, space, spaces_for):
    """Nothing but the three mapped types is remembered, and a mapped type
    whose payload carries no text is not half-remembered."""
    spaces_for(space)
    unmapped = Event(
        id=1,
        space_id=space,
        type="correction.recorded",
        schema_version=1,
        occurred_at=datetime.now(UTC),
        payload={"text": "not this one"},
    )
    assert await memory.ingest(kernel, unmapped) is None

    textless = Event(
        id=2,
        space_id=space,
        type="report.landed",
        schema_version=1,
        occurred_at=datetime.now(UTC),
        payload={"objective_id": "o1"},
    )
    assert await memory.ingest(kernel, textless) is None
    assert list(Episode.query.filter(space=space)) == []


@requires_postgres
async def test_ingest_returns_none_in_unassigned(kernel):
    """The unassigned space reads headers and remembers nothing. A None, not
    a refusal: the caller is inside the transaction of the person's own
    message and a refusal would unwind it."""
    event = Event(
        id=3,
        space_id=UNASSIGNED_SPACE_ID,
        type="message.received",
        schema_version=1,
        occurred_at=datetime.now(UTC),
        payload={"text": "hello", "conversation_id": "c1"},
    )
    assert await memory.ingest(kernel, event) is None


# ---------------------------------------------------------------------------
# propose and derive_ceiling (task 7)


def _proposal(space: str, supporting_events: list[int], **kwargs) -> BeliefProposal:
    fields = {
        "space": space,
        "statement": "the person prefers a short weekly note",
        "kind": "preference",
        "domain": "correspondence",
        "supporting_events": supporting_events,
        "test": "a note over ten lines is wrong",
        "proposed_source_class": "direct",
    }
    fields.update(kwargs)
    return BeliefProposal(**fields)


async def _ceiling(kernel, proposal_id: str) -> tuple[str, dict]:
    cur = await kernel.execute(
        "SELECT payload FROM events WHERE type = 'belief.proposed' "
        "AND payload->>'proposal_id' = %s",
        (proposal_id,),
    )
    (payload,) = await cur.fetchone()
    return payload["derived_ceiling"], payload


def test_derive_ceiling_is_pure_and_person_authored_only():
    """Architecture §6: the proposer picks among the three person classes
    when the evidence is the person's; an inference is inferred whatever the
    proposer wrote."""
    assert memory.derive_ceiling(["message.received"], "direct") == "direct"
    assert memory.derive_ceiling(["correction.recorded"], "correction") == "correction"
    assert memory.derive_ceiling(["approval.minted"], "decision") == "decision"
    assert memory.derive_ceiling(["message.received"], "inferred") == "inferred"
    assert memory.derive_ceiling(["report.landed"], "direct") == "inferred"
    assert (
        memory.derive_ceiling(["message.received", "report.landed"], "direct")
        == "inferred"
    )
    # Vacuously person-authored. `propose` never reaches this: a proposal
    # carries at least one supporting event.
    assert memory.derive_ceiling([], "direct") == "direct"
    assert memory.derive_ceiling([], "inferred") == "inferred"


@requires_postgres
async def test_propose_records_event_with_ceiling(kernel, space, spaces_for):
    spaces_for(space)
    person = await _seed_event(kernel, space, "message.received")
    worker = await _seed_event(kernel, space, "report.landed")

    person_only = await memory.propose(kernel, _proposal(space, [person]))
    ceiling, payload = await _ceiling(kernel, person_only)
    assert ceiling == "direct"
    assert payload["proposal"]["statement"] == "the person prefers a short weekly note"
    assert payload["proposal"]["supporting_events"] == [person]

    mixed = await memory.propose(kernel, _proposal(space, [person, worker]))
    assert (await _ceiling(kernel, mixed))[0] == "inferred"

    assert person_only != mixed
    await kernel.rollback()


@requires_postgres
async def test_propose_writes_no_redis_row(kernel, space, spaces_for):
    """The belief table is a materialized view of the log and the view is
    built at M1, so M0 writes the event and nothing else."""
    spaces_for(space)
    person = await _seed_event(kernel, space, "message.received")
    await memory.propose(kernel, _proposal(space, [person]))
    assert list(Belief.query.filter(scope=space)) == []
    await kernel.rollback()


@requires_postgres
async def test_propose_refuses_foreign_evidence(kernel, space, spaces_for):
    other = space + "-other"
    spaces_for(space, other)
    foreign = await _seed_event(kernel, other, "message.received")
    with pytest.raises(memory.MemoryRefused):
        await memory.propose(kernel, _proposal(space, [foreign]))
    with pytest.raises(memory.MemoryRefused):
        await memory.propose(kernel, _proposal(space, [foreign + 10_000_000]))
    await kernel.rollback()
    cur = await kernel.execute(
        "SELECT count(*) FROM events WHERE type = 'belief.proposed' "
        "AND space_id = %s",
        (space,),
    )
    assert (await cur.fetchone())[0] == 0


@requires_postgres
async def test_propose_refuses_global_scope(kernel, space, spaces_for):
    """Who sets a belief's scope is a person's decision, so a proposer cannot
    reach global: it has no manifest and the space check refuses it."""
    spaces_for(space)
    person = await _seed_event(kernel, space, "message.received")
    for scope in ("global", UNASSIGNED_SPACE_ID, space + "-unknown"):
        with pytest.raises(SpaceRefused):
            await memory.propose(kernel, _proposal(scope, [person]))
    await kernel.rollback()


@requires_postgres
async def test_self_approval_never_raises_the_ceiling(kernel, space, spaces_for):
    """A kernel self-approval shares its event type with the person's
    approval, so the type alone would let it pass as the person's."""
    spaces_for(space)
    for payload in (
        {"approval": {"kind": "self_approved", "session_id": "s1"}},
        {"approval": {"kind": "expired", "session_id": "s1"}},
        {"approval": {"kind": "granted", "session_id": "kernel"}},
    ):
        event_id = await events.append(
            kernel, space_id=space, type="approval.minted", payload=payload
        )
        proposal_id = await memory.propose(
            kernel, _proposal(space, [event_id], proposed_source_class="decision")
        )
        assert (await _ceiling(kernel, proposal_id))[0] == "inferred"

    person_approval = await events.append(
        kernel,
        space_id=space,
        type="approval.minted",
        payload={"approval": {"kind": "granted", "session_id": "s1"}},
    )
    proposal_id = await memory.propose(
        kernel, _proposal(space, [person_approval], proposed_source_class="decision")
    )
    assert (await _ceiling(kernel, proposal_id))[0] == "decision"
    await kernel.rollback()
