"""Properties of the store (plan 01, P1 to P3): a stateful machine against
the real database with a pure Python model beside it, in the manner of
spike 01. Skipped when Postgres is unreachable."""

import asyncio
import uuid

import psycopg
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from kernel.events import append, read, read_for
from schemas.events import CURRENT_VERSION, EVENT_TYPES
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres

KEYS = ["a", "b", "objective_id"]
TEXT = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=6)
PAYLOADS = st.dictionaries(
    st.sampled_from(KEYS), st.one_of(TEXT, st.integers()), max_size=3
)
NEVER = ("never_appended", "never_appended")


class EventStore(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.conn = self.loop.run_until_complete(
            psycopg.AsyncConnection.connect(dsn("kernel_rw"), autocommit=True)
        )
        tag = uuid.uuid4().hex[:8]
        self.spaces = [f"space-{tag}-a", f"space-{tag}-b"]
        # space -> list of (id, type, payload), in append order
        self.model = {s: [] for s in self.spaces}
        self.pairs: set[tuple[str, str]] = {NEVER}

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    @rule(
        space=st.sampled_from([0, 1]),
        type=st.sampled_from(sorted(EVENT_TYPES)),
        payload=PAYLOADS,
    )
    def append(self, space, type, payload):
        space = self.spaces[space]
        event_id = self.run(
            append(self.conn, space_id=space, type=type, payload=payload)
        )
        self.model[space].append((event_id, type, payload))
        for k, v in payload.items():
            if isinstance(v, str):
                self.pairs.add((k, v))

    @rule(space=st.sampled_from([0, 1]), limit=st.integers(1, 50), data=st.data())
    def page(self, space, limit, data):
        """P2: concatenated pages equal the full read; a page never holds an
        id at or below its `after`; no page exceeds `limit`."""
        space = self.spaces[space]
        full = self.run(read(self.conn, space_id=space, limit=10_000))
        after, pages = 0, []
        while True:
            page = self.run(read(self.conn, space_id=space, after=after, limit=limit))
            assert len(page) <= limit
            assert all(e.id > after for e in page)
            pages.extend(page)
            if len(page) < limit:
                break
            after = page[-1].id
        assert pages == full
        seen = [0] + [e.id for e in full]
        after = data.draw(st.sampled_from(seen))
        page = self.run(read(self.conn, space_id=space, after=after, limit=limit))
        assert page == [e for e in full if e.id > after][:limit]

    @rule(space=st.sampled_from([0, 1]), data=st.data())
    def read_for(self, space, data):
        """P3: read_for equals a Python filter over the full read."""
        space = self.spaces[space]
        key, value = data.draw(st.sampled_from(sorted(self.pairs)))
        got = self.run(read_for(self.conn, space_id=space, key=key, value=value))
        full = self.run(read(self.conn, space_id=space, limit=10_000))
        assert got == [e for e in full if e.payload.get(key) == value]

    @invariant()
    def read_is_the_model(self):
        """P1: the store is a log."""
        all_ids = []
        for space in self.spaces:
            events = self.run(read(self.conn, space_id=space, after=0, limit=10_000))
            assert [(e.id, e.type, e.payload) for e in events] == self.model[space]
            ids = [e.id for e in events]
            assert ids == sorted(ids) and len(set(ids)) == len(ids)
            assert all(e.space_id == space for e in events)
            assert all(e.schema_version == CURRENT_VERSION[e.type] for e in events)
            all_ids.extend(ids)
        assert len(set(all_ids)) == len(all_ids)

    def teardown(self):
        self.run(self.conn.close())
        self.loop.close()


TestEventStore = EventStore.TestCase
TestEventStore.settings = settings(
    max_examples=40, stateful_step_count=30, deadline=None
)
