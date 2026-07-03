"""Unit tests for agent.slot_lease.SlotLeaseRegistry (issue #1820, Fix #2).

Covers the acquire/bind/release/reclaim contract in isolation — no worker
loop, no health check, no Redis. See tests/integration/test_slot_lease_reclaim.py
for the end-to-end reap-pass regression coverage.

Deliberately does NOT test a deadline-expired reclaim: the wall-clock reclaim
arm was removed (see agent/slot_lease.py module docstring, "no reclaim
deadline") — the reaper reclaims on terminal-owner status only.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.slot_lease import Lease, SlotLeaseRegistry


@pytest.mark.asyncio
async def test_acquire_bind_release_happy_path():
    """acquire() -> bind() -> release() drains and restores exactly one permit."""
    registry = SlotLeaseRegistry(max_concurrent=1)
    assert registry.permits_free() == 1

    await registry.acquire()
    assert registry.permits_free() == 0
    assert registry.leases() == []  # acquire() alone records no lease

    registry.bind("session-a")
    assert registry.permits_free() == 0
    leases = registry.leases()
    assert len(leases) == 1
    assert isinstance(leases[0], Lease)
    assert leases[0].owner_session_id == "session-a"

    registry.release("session-a")
    assert registry.permits_free() == 1
    assert registry.leases() == []


@pytest.mark.asyncio
async def test_acquire_release_unbound_leaves_no_lease():
    """acquire() then release_unbound() (pop returned None) restores the permit
    and never creates a lease — the reaper (which iterates only bound leases)
    can never observe or mis-reclaim it."""
    registry = SlotLeaseRegistry(max_concurrent=2)
    assert registry.permits_free() == 2

    await registry.acquire()
    assert registry.permits_free() == 1
    assert registry.leases() == []

    registry.release_unbound()
    assert registry.permits_free() == 2
    assert registry.leases() == []


@pytest.mark.asyncio
async def test_reclaim_terminal_owner_frees_permit():
    """reclaim() on a bound owner releases the permit and drops the lease."""
    registry = SlotLeaseRegistry(max_concurrent=1)
    await registry.acquire()
    registry.bind("session-terminal")
    assert registry.permits_free() == 0

    registry.reclaim("session-terminal")
    assert registry.permits_free() == 1
    assert registry.leases() == []


@pytest.mark.asyncio
async def test_double_reclaim_is_idempotent_no_over_release():
    """A second reclaim() for the same owner after the first must be a no-op —
    the permit count must NOT go up a second time (over-release regression guard,
    Risk 1 in the plan)."""
    registry = SlotLeaseRegistry(max_concurrent=1)
    await registry.acquire()
    registry.bind("session-double")

    registry.reclaim("session-double")
    assert registry.permits_free() == 1

    # Second reclaim of the same (now-unbound) owner must not over-release.
    registry.reclaim("session-double")
    assert registry.permits_free() == 1, (
        "Double reclaim over-released the permit — permits_free exceeded max_concurrent"
    )


@pytest.mark.asyncio
async def test_double_release_is_idempotent_no_over_release():
    """release() called twice for the same owner must not over-release."""
    registry = SlotLeaseRegistry(max_concurrent=1)
    await registry.acquire()
    registry.bind("session-double-release")

    registry.release("session-double-release")
    assert registry.permits_free() == 1

    registry.release("session-double-release")
    assert registry.permits_free() == 1


@pytest.mark.asyncio
async def test_release_and_reclaim_race_only_one_wins():
    """If both release() and reclaim() fire for the same owner (a race between
    the worker loop's finally and the reaper), only the first actually releases
    a permit — the second is a no-op regardless of which method fires first."""
    registry = SlotLeaseRegistry(max_concurrent=1)
    await registry.acquire()
    registry.bind("session-race")

    registry.release("session-race")
    registry.reclaim("session-race")  # reaper fires after the loop's own release
    assert registry.permits_free() == 1


@pytest.mark.asyncio
async def test_release_unknown_owner_is_noop():
    """release()/reclaim() for an owner that was never bound is a no-op."""
    registry = SlotLeaseRegistry(max_concurrent=3)
    registry.release("never-bound")
    registry.reclaim("never-bound")
    assert registry.permits_free() == 3
    assert registry.leases() == []


@pytest.mark.asyncio
async def test_permits_free_accounting_across_multiple_owners():
    """permits_free() tracks concurrent acquires/binds/releases across owners."""
    registry = SlotLeaseRegistry(max_concurrent=3)
    await registry.acquire()
    registry.bind("a")
    await registry.acquire()
    registry.bind("b")
    assert registry.permits_free() == 1
    assert {lease.owner_session_id for lease in registry.leases()} == {"a", "b"}

    registry.release("a")
    assert registry.permits_free() == 2
    assert {lease.owner_session_id for lease in registry.leases()} == {"b"}

    registry.reclaim("b")
    assert registry.permits_free() == 3
    assert registry.leases() == []


@pytest.mark.asyncio
async def test_acquire_blocks_when_exhausted_and_unblocks_on_release():
    """acquire() blocks the caller when the ceiling is full — preserves the
    counting-semaphore backpressure contract the raw asyncio.Semaphore gave."""
    registry = SlotLeaseRegistry(max_concurrent=1)
    await registry.acquire()
    registry.bind("holder")

    acquired = False

    async def _second_acquire():
        nonlocal acquired
        await registry.acquire()
        acquired = True

    task = asyncio.create_task(_second_acquire())
    await asyncio.sleep(0.05)
    assert not acquired, "Second acquire() should block while the ceiling is full"

    registry.release("holder")
    await asyncio.wait_for(task, timeout=1.0)
    assert acquired, "Second acquire() should unblock once a permit is released"

    # Clean up: the second acquire's permit is unbound (test doesn't call bind).
    registry.release_unbound()
    assert registry.permits_free() == 1
