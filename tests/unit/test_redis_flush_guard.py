"""Regression coverage for the production-Redis (db=0) flush guard.

On 2026-06-03 a flushdb()/flushall() against db=0 wiped the production dataset
(memories, Telegram history, chats, knowledge docs). AOF was off and the RDB
snapshot was overwritten post-wipe, so the data was unrecoverable.

The guard is installed at conftest import time (tests/conftest.py
``_install_redis_db0_flush_guard``). It patches the sync and async Redis
classes so that:
  - flushdb() against db=0 raises (production), but db>=1 is allowed
  - flushall() always raises (it wipes every db regardless of selection)

These tests assert the guard is active for any test process.
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
    # The redis_test_db fixture relies on being able to flush db>=1; the guard
    # must not block that. Flush THIS PROCESS'S claimed db -- the old helper
    # re-derived gw{N}+1, which is not this process's claim as soon as any other
    # pytest process on the machine holds a lower slot, so the flush landed on a
    # database someone else owned (#2655).
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
