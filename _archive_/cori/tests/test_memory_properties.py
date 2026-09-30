"""The seven memory properties of plan 09.

The pattern is spike 05: a strategy over operations, a statement that must
hold, a named test. Store-backed examples write through `write` and `ingest`
against local Postgres and Redis db 1, in a transaction that is rolled back,
so the only thing an example leaves behind is Redis rows it deletes itself.

Decided in build: the three space ids are fixed for the module and carry a
per-run prefix, rather than a per-example one. Every example deletes its own
rows, so two examples cannot collide; the run prefix is what keeps two runs
of the suite apart. Patching `load_all` once per module also keeps the
patch off the per-example path.
"""

import asyncio
import uuid

import psycopg
import pytest
from hypothesis import given, settings, strategies as st

popoto = pytest.importorskip("popoto")
from popoto.redis_db import POPOTO_REDIS_DB  # noqa: E402

from kernel import events, memory  # noqa: E402
from kernel.memory import Episode  # noqa: E402
from schemas.memory import BeliefProposal, EpisodeWrite, SliceQuery  # noqa: E402
from schemas.space import Space  # noqa: E402
from tests.conftest import dsn, requires_postgres  # noqa: E402


def _redis_up() -> bool:
    try:
        return POPOTO_REDIS_DB.ping()
    except Exception:
        return False


pytestmark = [
    requires_postgres,
    pytest.mark.skipif(not _redis_up(), reason="local Redis is not reachable"),
]

RUN = uuid.uuid4().hex[:8]
SPACES = tuple(f"prop-{RUN}-{letter}" for letter in ("a", "b", "c"))

WORDS = ("deploy", "invoice", "friday", "window", "quiet", "report")
KINDS = ("turn", "report", "summary", "decision")
REGARDS = (None, "c1", "o1")
CLASSES = ("PROJECT", "OPERATOR")

STORE = settings(max_examples=100, deadline=None)
PURE = settings(max_examples=2000)


@pytest.fixture(autouse=True, scope="module")
def _spaces_are_real():
    """Seams §3.8 gives `write` and `propose` no `spaces` argument, so the
    module name `kernel.memory.load_all` is the injection point."""
    manifests = {
        space: Space(
            id=space, kind="client", roots=[f"/{space}"], max_effect_class="propose"
        )
        for space in SPACES
    }
    original = memory.load_all
    memory.load_all = lambda: manifests
    yield
    memory.load_all = original


texts = st.lists(st.sampled_from(WORDS), min_size=1, max_size=4).map(" ".join)
episode_ops = st.lists(
    st.fixed_dictionaries(
        {
            "space": st.sampled_from(SPACES),
            "kind": st.sampled_from(KINDS),
            "text": texts,
            "regards": st.sampled_from(REGARDS),
            "data_class": st.sampled_from(CLASSES),
        }
    ),
    min_size=1,
    max_size=6,
)
queries = st.fixed_dictionaries(
    {
        "space": st.sampled_from(SPACES),
        "query": st.one_of(st.just(""), st.sampled_from(WORDS)),
        "k": st.integers(min_value=1, max_value=20),
        "kinds": st.one_of(
            st.none(), st.lists(st.sampled_from(KINDS), min_size=1, unique=True)
        ),
        "regards": st.sampled_from(REGARDS),
        "max_data_class": st.sampled_from(CLASSES),
    }
)


def run(coro):
    return asyncio.run(coro)


def clean() -> None:
    for space in SPACES:
        for row in list(Episode.query.filter(space=space)):
            row.delete()


async def connect():
    return await psycopg.AsyncConnection.connect(dsn("kernel_rw"))


async def seed(conn) -> dict[str, int]:
    """One citable event per space, so a summary or a decision has evidence."""
    return {
        space: await events.append(
            conn,
            space_id=space,
            type="message.received",
            payload={"text": "the person wrote"},
        )
        for space in SPACES
    }


async def write_ops(conn, ops, evidence) -> list[tuple[str, str]]:
    written = []
    for op in ops:
        provenance = (
            [evidence[op["space"]]] if op["kind"] in ("summary", "decision") else []
        )
        episode_id = await memory.write(conn, EpisodeWrite(provenance=provenance, **op))
        written.append((op["space"], episode_id))
    return written


# ---------------------------------------------------------------------------
# 1. Hits never cross spaces


@STORE
@given(ops=episode_ops, query=queries)
def test_hits_never_cross_spaces(ops, query):
    async def body():
        conn = await connect()
        try:
            written = await write_ops(conn, ops, await seed(conn))
            mine = {
                episode_id for space, episode_id in written if space == query["space"]
            }
            hits = await memory.retrieve(SliceQuery(**query))
            for hit in hits:
                assert hit.space == query["space"]
                assert hit.episode_id in mine
        finally:
            clean()
            await conn.rollback()
            await conn.close()

    run(body())


# ---------------------------------------------------------------------------
# 2. Hits never exceed the caller's data class


@STORE
@given(ops=episode_ops, space=st.sampled_from(SPACES))
def test_hits_never_exceed_data_class(ops, space):
    async def body():
        conn = await connect()
        try:
            await write_ops(conn, ops, await seed(conn))
            for word in ("",) + WORDS:
                capped = await memory.retrieve(
                    SliceQuery(space=space, query=word, k=20)
                )
                assert all(hit.data_class == "PROJECT" for hit in capped)

            # Fewer than 20 writes, so k never binds: the cap narrows the
            # candidate set and does nothing else.
            capped = await memory.retrieve(SliceQuery(space=space, query="", k=20))
            uncapped = await memory.retrieve(
                SliceQuery(space=space, query="", k=20, max_data_class="OPERATOR")
            )
            assert {hit.episode_id for hit in capped} <= {
                hit.episode_id for hit in uncapped
            }
        finally:
            clean()
            await conn.rollback()
            await conn.close()

    run(body())


# ---------------------------------------------------------------------------
# 3. Memory says nothing the log did not record


@STORE
@given(ops=episode_ops, query=queries)
def test_every_hit_has_its_event(ops, query):
    async def body():
        conn = await connect()
        try:
            await write_ops(conn, ops, await seed(conn))
            hits = await memory.retrieve(SliceQuery(**query))
            for hit in hits:
                (row,) = list(
                    Episode.query.filter(space=hit.space, episode_id=hit.episode_id)
                )
                cur = await conn.execute(
                    "SELECT type, occurred_at, payload FROM events WHERE id = %s",
                    (row.event_id,),
                )
                type, occurred_at, payload = await cur.fetchone()
                assert type == "episode.written"
                assert payload["episode_id"] == hit.episode_id
                assert payload["text_sha256"] == hit.text_sha256
                assert occurred_at == hit.written_at
        finally:
            clean()
            await conn.rollback()
            await conn.close()

    run(body())


# ---------------------------------------------------------------------------
# 4. Retrieval is a function of store state


@STORE
@given(ops=episode_ops, query=queries)
def test_retrieve_is_deterministic(ops, query):
    async def body():
        conn = await connect()
        try:
            await write_ops(conn, ops, await seed(conn))
            first = await memory.retrieve(SliceQuery(**query))
            second = await memory.retrieve(SliceQuery(**query))
            assert [(h.episode_id, h.score) for h in first] == [
                (h.episode_id, h.score) for h in second
            ]
        finally:
            clean()
            await conn.rollback()
            await conn.close()

    run(body())


# ---------------------------------------------------------------------------
# 5. Foreign or missing evidence is refused whole


@STORE
@given(
    picks=st.lists(
        st.sampled_from(("mine", "foreign", "unused")), min_size=0, max_size=3
    ),
    kind=st.sampled_from(KINDS),
)
def test_foreign_provenance_refused_whole(picks, kind):
    mine_space, foreign_space = SPACES[0], SPACES[1]

    async def body():
        conn = await connect()
        try:
            evidence = await seed(conn)
            sources = {
                "mine": evidence[mine_space],
                "foreign": evidence[foreign_space],
                "unused": evidence[mine_space] + 10_000_000,
            }
            provenance = [sources[pick] for pick in picks]
            whole = all(pick == "mine" for pick in picks)

            if kind in ("summary", "decision") and not provenance:
                with pytest.raises(ValueError):
                    EpisodeWrite(
                        space=mine_space,
                        kind=kind,
                        text="the thread so far",
                        provenance=provenance,
                        data_class="PROJECT",
                    )
                return

            write = EpisodeWrite(
                space=mine_space,
                kind=kind,
                text="the thread so far",
                provenance=provenance,
                data_class="PROJECT",
            )
            if whole:
                episode_id = await memory.write(conn, write)
                assert list(
                    Episode.query.filter(space=mine_space, episode_id=episode_id)
                )
            else:
                with pytest.raises(memory.MemoryRefused):
                    await memory.write(conn, write)
                assert list(Episode.query.filter(space=mine_space)) == []

            if not provenance:
                with pytest.raises(ValueError):
                    BeliefProposal(
                        space=mine_space,
                        statement="the person prefers a short note",
                        kind="preference",
                        domain="correspondence",
                        supporting_events=provenance,
                        test="a note over ten lines is wrong",
                        proposed_source_class="direct",
                    )
                return

            proposal = BeliefProposal(
                space=mine_space,
                statement="the person prefers a short note",
                kind="preference",
                domain="correspondence",
                supporting_events=provenance,
                test="a note over ten lines is wrong",
                proposed_source_class="direct",
            )
            if whole:
                assert await memory.propose(conn, proposal)
            else:
                with pytest.raises(memory.MemoryRefused):
                    await memory.propose(conn, proposal)
        finally:
            clean()
            await conn.rollback()
            await conn.close()

    run(body())

    # A refusal leaves no event: the caller's rollback above returned the log
    # to where it was, and nothing memory wrote survives it.
    async def nothing_survives():
        conn = await connect()
        try:
            cur = await conn.execute(
                "SELECT count(*) FROM events WHERE space_id = ANY(%s)",
                (list(SPACES),),
            )
            assert (await cur.fetchone())[0] == 0
        finally:
            await conn.close()

    run(nothing_survives())


# ---------------------------------------------------------------------------
# 6. The ceiling is derived from the authors (pure)


@PURE
@given(
    types=st.lists(
        st.sampled_from(
            (
                "message.received",
                "correction.recorded",
                "approval.minted",
                "message.sent",
                "report.landed",
                "objective.started",
            )
        ),
        max_size=5,
    ),
    proposed=st.sampled_from(("direct", "correction", "decision", "inferred")),
)
def test_ceiling_is_inferred_unless_person_authored(types, proposed):
    person = all(t in memory.PERSON_AUTHORED for t in types)
    expected = proposed if person and proposed != "inferred" else "inferred"
    assert memory.derive_ceiling(types, proposed) == expected


# ---------------------------------------------------------------------------
# 7. The latest summary is the newest one, and only that


@STORE
@given(
    ops=episode_ops,
    space=st.sampled_from(SPACES),
    regards=st.sampled_from([r for r in REGARDS if r is not None]),
)
def test_latest_summary_is_newest_matching(ops, space, regards):
    async def body():
        conn = await connect()
        try:
            written = await write_ops(conn, ops, await seed(conn))
            matching = [
                episode_id
                for (op, (op_space, episode_id)) in zip(ops, written)
                if op_space == space
                and op["kind"] == "summary"
                and op["regards"] == regards
            ]
            hit = await memory.latest_summary(space, regards)
            if not matching:
                assert hit is None
            else:
                assert hit is not None
                assert hit.episode_id == max(matching)
        finally:
            clean()
            await conn.rollback()
            await conn.close()

    run(body())
