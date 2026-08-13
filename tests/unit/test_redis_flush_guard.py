"""Regression coverage for the Redis flush ownership guard.

On 2026-06-03 a flushdb()/flushall() against db=0 wiped the production dataset
(memories, Telegram history, chats, knowledge docs). AOF was off and the RDB
snapshot was overwritten post-wipe, so the data was unrecoverable.

The guard is installed at conftest import time (tests/conftest.py
``_install_redis_flush_ownership_guard``). It patches the sync and async Redis
classes so that:
  - flushdb() is allowed only against a db this process has CLAIMED (#2628)
  - db=0 is denied with its own message; no test process can ever claim it
  - flushall() always raises (it wipes every db regardless of selection)

These tests assert the guard is active for any test process. The ownership half
lives in tests/unit/test_conftest_isolation_guards.py, where a real competing
flock holder can be spawned.
"""

import pytest
import redis
import redis.asyncio as aioredis

from tests.db_claim import claim_test_db


def test_flushdb_on_db0_is_blocked():
    client = redis.Redis(db=0)
    with pytest.raises(RuntimeError, match="db=0"):
        client.flushdb()


def test_flushdb_via_from_url_db0_is_blocked():
    client = redis.Redis.from_url("redis://localhost:6379/0")
    with pytest.raises(RuntimeError, match="db=0"):
        client.flushdb()


def test_flushall_is_blocked_on_db0():
    client = redis.Redis(db=0)
    with pytest.raises(RuntimeError, match="flushall"):
        client.flushall()


def test_flushall_is_blocked_even_on_test_db():
    # flushall ignores the selected db and wipes everything, so it must be
    # blocked regardless of which db the client points at.
    client = redis.Redis(db=claim_test_db())
    with pytest.raises(RuntimeError, match="flushall"):
        client.flushall()


def test_flushdb_on_own_test_db_is_allowed():
    # The redis_test_db fixture relies on being able to flush its own db; the
    # guard must not block that. The db number comes from the claim API and
    # nowhere else -- deriving it from the xdist worker id (the pre-#2606 rule)
    # is what made this test flush a stranger's database (#2628). The old
    # helper re-derived gw{N}+1, which stops being this process's claim as
    # soon as any other pytest process on the machine holds a lower slot, so
    # the flush landed on a database someone else owned (#2655).
    client = redis.Redis(db=claim_test_db())
    assert client.flushdb() is True


@pytest.mark.asyncio
async def test_async_flushall_is_blocked():
    client = aioredis.Redis(db=0)
    with pytest.raises(RuntimeError, match="flushall"):
        await client.flushall()


@pytest.mark.asyncio
async def test_async_flushdb_on_db0_is_blocked():
    client = aioredis.Redis(db=0)
    with pytest.raises(RuntimeError, match="db=0"):
        await client.flushdb()
