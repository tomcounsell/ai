"""Unit tests for the Codex dev-lane Redis lease (plan #2001 Task 3).

These tests run against the real Redis test DB claimed by
``scripts/pytest-clean.sh`` (``REDIS_URL`` is exported process-wide by
``pytest_configure``), because the lease's contract — ``SET NX EX``,
TTL crash-release, Lua compare-and-delete — is only meaningful against
a real server. Session ids are UUID-scoped per test and every key is
released or deleted in a ``finally`` so no state leaks between tests.
"""

from __future__ import annotations

import time
import uuid

import pytest

from agent.codex_dev_lease import (
    DEV_LEASE_TTL_S,
    DevLaneBusy,
    DevLease,
    acquire_dev_lease,
    key_for,
)


def _sid() -> str:
    return f"test-lease-{uuid.uuid4().hex}"


@pytest.fixture()
def redis_client():
    from utils.redis_client import text_redis

    client = text_redis()
    keys: list[str] = []
    yield client, keys
    for key in keys:
        try:
            client.delete(key)
        except Exception:  # noqa: BLE001 -- best-effort cleanup
            pass


def test_key_for_namespaces_by_session():
    assert key_for("abc") == "codex:devlane:abc"
    assert key_for("abc") != key_for("def")


def test_acquire_sets_nx_ex_with_ttl(redis_client):
    client, keys = redis_client
    sid = _sid()
    keys.append(key_for(sid))
    lease = acquire_dev_lease(sid, timeout_s=0)
    try:
        assert isinstance(lease, DevLease)
        ttl = client.ttl(key_for(sid))
        assert 0 < ttl <= DEV_LEASE_TTL_S
    finally:
        lease.release()


def test_second_acquirer_gets_busy_on_try_lock(redis_client):
    client, keys = redis_client
    sid = _sid()
    keys.append(key_for(sid))
    lease = acquire_dev_lease(sid, timeout_s=0)
    try:
        with pytest.raises(DevLaneBusy):
            acquire_dev_lease(sid, timeout_s=0)
    finally:
        lease.release()


def test_release_frees_lane_for_next_acquirer(redis_client):
    client, keys = redis_client
    sid = _sid()
    keys.append(key_for(sid))
    with acquire_dev_lease(sid, timeout_s=0):
        with pytest.raises(DevLaneBusy):
            acquire_dev_lease(sid, timeout_s=0)
    # After context exit the lane is free again.
    with acquire_dev_lease(sid, timeout_s=0):
        pass


def test_release_is_compare_and_delete(redis_client):
    """A stale holder never deletes another holder's lease.

    Simulates TTL expiry + another holder's reacquire by overwriting the key, then
    releasing the stale lease: the current key must survive.
    """
    client, keys = redis_client
    sid = _sid()
    key = key_for(sid)
    keys.append(key)
    lease = acquire_dev_lease(sid, timeout_s=0)
    try:
        client.set(key, "someone-elses-token", ex=DEV_LEASE_TTL_S)
        lease.release()
        assert client.get(key) == "someone-elses-token"
    finally:
        client.delete(key)


def test_release_is_idempotent(redis_client):
    client, keys = redis_client
    sid = _sid()
    keys.append(key_for(sid))
    lease = acquire_dev_lease(sid, timeout_s=0)
    lease.release()
    lease.release()  # must not raise
    assert client.get(key_for(sid)) is None


def test_blocking_acquire_waits_for_release(redis_client):
    client, keys = redis_client
    sid = _sid()
    keys.append(key_for(sid))
    first = acquire_dev_lease(sid, timeout_s=0)
    try:
        started = time.monotonic()
        with pytest.raises(DevLaneBusy):
            acquire_dev_lease(sid, timeout_s=0.5)
        assert time.monotonic() - started >= 0.4
    finally:
        first.release()
    with acquire_dev_lease(sid, timeout_s=5):
        pass


async def test_async_context_manager_releases(redis_client):
    client, keys = redis_client
    sid = _sid()
    keys.append(key_for(sid))
    async with acquire_dev_lease(sid, timeout_s=0):
        assert client.get(key_for(sid)) is not None
    assert client.get(key_for(sid)) is None
