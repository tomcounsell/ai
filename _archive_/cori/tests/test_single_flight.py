"""single_flight serializes per key and needs an open transaction.
Seams §0, §3.1; plan 01 P4."""

import asyncio
import uuid

import psycopg
import pytest
from hypothesis import given, settings, strategies as st

from kernel.events import NoTransaction, append, read_for, single_flight
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres

KEYS = ["k1", "k2", "k3"]
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


def fresh_space() -> str:
    return f"space-{uuid.uuid4().hex[:8]}"


async def counter_step(conn, space, key, sleep_ms):
    """Read the count for `key`, wait, append it as the next `n`."""
    seen = await read_for(conn, space_id=space, key="key", value=key)
    await asyncio.sleep(sleep_ms / 1000)
    await append(
        conn,
        space_id=space,
        type="message.sent",
        payload={"key": key, "n": len(seen)},
    )


async def run_schedule(space, keys, sleeps, *, locked):
    conns = [await psycopg.AsyncConnection.connect(dsn("kernel_rw")) for _ in keys]

    async def one(conn, key, sleep_ms):
        async with conn.transaction():
            if locked:
                async with single_flight(conn, f"test:{space}:{key}"):
                    await counter_step(conn, space, key, sleep_ms)
            else:
                await counter_step(conn, space, key, sleep_ms)

    try:
        await asyncio.gather(*(one(c, k, s) for c, k, s in zip(conns, keys, sleeps)))
        async with conns[0].transaction():
            return {
                key: [
                    e.payload["n"]
                    for e in await read_for(
                        conns[0], space_id=space, key="key", value=key
                    )
                ]
                for key in set(keys)
            }
    finally:
        for c in conns:
            await c.close()


@settings(max_examples=20, deadline=None)
@given(
    st.integers(2, 16).flatmap(
        lambda n: st.tuples(
            st.lists(st.sampled_from(KEYS), min_size=n, max_size=n),
            st.lists(st.integers(0, 20), min_size=n, max_size=n),
        )
    )
)
def test_single_flight_serializes_per_key(schedule):
    """P4: for every key the appended counters are exactly range(count)."""
    keys, sleeps = schedule
    counters = asyncio.run(run_schedule(fresh_space(), keys, sleeps, locked=True))
    for key, ns in counters.items():
        assert ns == list(range(keys.count(key))), (key, ns)


def test_without_single_flight_counters_collide():
    """The control: sixteen connections on one key with a 50 ms gap between
    read and append produce at least one duplicate counter."""
    keys, sleeps = ["k1"] * 16, [50] * 16
    counters = asyncio.run(run_schedule(fresh_space(), keys, sleeps, locked=False))
    ns = counters["k1"]
    assert len(ns) == 16 and len(set(ns)) < 16, ns


async def test_single_flight_refuses_autocommit():
    async with await psycopg.AsyncConnection.connect(
        dsn("kernel_rw"), autocommit=True
    ) as conn:
        with pytest.raises(NoTransaction):
            async with single_flight(conn, f"test:{uuid.uuid4().hex}"):
                pass


async def acquire_on_new_connection(key):
    async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as conn:
        async with conn.transaction():
            await conn.execute(LOCK_SQL, (key,))


async def test_single_flight_accepts_autocommit_inside_transaction_block():
    key = f"test:{uuid.uuid4().hex}"
    async with await psycopg.AsyncConnection.connect(
        dsn("kernel_rw"), autocommit=True
    ) as conn:
        async with conn.transaction():
            async with single_flight(conn, key):
                second = asyncio.create_task(acquire_on_new_connection(key))
                await asyncio.sleep(0.3)
                assert not second.done(), "the lock was not held"
        await asyncio.wait_for(second, 2.0)


async def test_lock_releases_when_connection_closes():
    key = f"test:{uuid.uuid4().hex}"
    first = await psycopg.AsyncConnection.connect(dsn("kernel_rw"))
    await first.execute(LOCK_SQL, (key,))
    second = asyncio.create_task(acquire_on_new_connection(key))
    await asyncio.sleep(0.2)
    assert not second.done()
    await first.close()
    await asyncio.wait_for(second, 1.0)
