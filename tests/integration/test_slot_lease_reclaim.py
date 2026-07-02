"""End-to-end regression tests for the slot-lease reap pass (issue #1820, Fix #2).

Exercises the hoisted top-of-tick reap pass (``agent.session_health._reap_slot_leases``,
called from ``_agent_session_health_check``) against a real ``SlotLeaseRegistry`` and
real ``AgentSession`` rows in Redis — no mocked worker loop, no mocked health check.

Covers:
- Acceptance criterion #1 (plan): a leaked permit is automatically reclaimed without a
  process restart, on a DRAINED queue with NO live worker registered for the orphaned
  session's worker_key — the reap pass must not depend on ``worker_alive`` or on any
  pending session existing.
- Blocker 2 regression guard: a still-``running``, progressing owner's lease is NEVER
  reclaimed on a wall-clock basis — only a terminal owner is reclaimed.
- Operator CONCERN regression guard: ``SLOT_LEASE_REAP_DISABLED=1`` preserves detection
  (the leaked-slot WARNING still fires) while suppressing the reclaim action itself.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

import agent.session_state as _session_state
from agent.session_health import _agent_session_health_check
from agent.slot_lease import SlotLeaseRegistry
from models.agent_session import AgentSession

PROJECT_KEY = "test-slot-lease-reclaim"


def _create_session(session_id: str, status: str, **overrides) -> AgentSession:
    """Create a minimal AgentSession for slot-lease reap testing."""
    defaults = {
        "project_key": PROJECT_KEY,
        "status": status,
        "priority": "normal",
        "created_at": time.time(),
        "session_id": session_id,
        "working_dir": "/tmp/slot-lease-test",
        "message_text": "slot lease reclaim test",
        "sender_name": "SlotLeaseTester",
        "chat_id": f"chat-{session_id}",
        "telegram_message_id": 1,
        "session_type": "teammate",
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


def _cleanup() -> None:
    stale = [s for s in AgentSession.query.all() if s.project_key == PROJECT_KEY]
    for s in stale:
        try:
            s.delete()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _isolate_registry_and_sessions():
    """Swap in a fresh registry for each test and restore/clean up afterward."""
    prior_registry = _session_state._slot_registry
    yield
    _session_state._slot_registry = prior_registry
    _cleanup()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reap_reclaims_orphaned_terminal_owner_and_unblocks_parked_worker(
    redis_test_db, monkeypatch
):
    """Acceptance criterion #1: a leaked permit is reclaimed without a process
    restart, and a worker parked at ``acquire()`` proceeds — on a drained queue
    with NO live worker registered for the orphaned session's worker_key.
    """
    monkeypatch.delenv("SLOT_LEASE_REAP_DISABLED", raising=False)

    registry = SlotLeaseRegistry(max_concurrent=1)
    _session_state._slot_registry = registry

    # Orphan the slot: acquire the only permit, bind it to a session, then let
    # that session's row go terminal WITHOUT releasing the lease. No worker
    # future is registered anywhere — the reap must not depend on worker_alive.
    await registry.acquire()
    session = _create_session("orphan-1", status="killed")
    registry.bind(session.agent_session_id)
    assert registry.permits_free() == 0

    # A second caller parks at acquire() — simulates the drained-queue worker
    # loop waiting for a slot that will never be released by its rightful
    # (wedged) owner.
    parked_acquired = asyncio.Event()

    async def _parked_acquirer():
        await registry.acquire()
        parked_acquired.set()

    parked_task = asyncio.create_task(_parked_acquirer())
    await asyncio.sleep(0.05)
    assert not parked_acquired.is_set(), "Precondition: second acquire() must park"

    try:
        # --- Run the health check on a fully drained queue, no live worker ---
        await _agent_session_health_check()

        await asyncio.wait_for(parked_acquired.wait(), timeout=2.0)
        assert parked_acquired.is_set(), (
            "Expected the reap pass to reclaim the orphaned lease and unblock "
            "the parked acquire() without any worker restart."
        )
        assert registry.leases() == []
    finally:
        if not parked_task.done():
            parked_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(parked_task), timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reap_does_not_reclaim_still_running_progressing_owner(redis_test_db, monkeypatch):
    """Blocker 2 regression guard: no wall-clock reclaim arm. A still-``running``
    owner's lease must NEVER be reclaimed by the reap pass, however old
    ``acquired_at`` is — only a terminal owner is reclaimed.
    """
    monkeypatch.delenv("SLOT_LEASE_REAP_DISABLED", raising=False)

    registry = SlotLeaseRegistry(max_concurrent=1)
    _session_state._slot_registry = registry

    await registry.acquire()
    # created_at recent enough that the running-session scan's own recovery
    # threshold (AGENT_SESSION_HEALTH_MIN_RUNNING=300s) does not independently
    # attempt to recover this session in the same tick — isolates the
    # assertion to the reap pass specifically.
    session = _create_session(
        "still-running-1", status="running", created_at=time.time(), started_at=time.time()
    )
    registry.bind(session.agent_session_id)
    # Force acquired_at far in the past — proves there is no wall-clock arm:
    # even a "very old" lease must not be reclaimed while its owner is running.
    stale_lease = registry.leases()[0]
    stale_lease.acquired_at = time.time() - 100_000

    assert registry.permits_free() == 0

    await _agent_session_health_check()

    assert registry.permits_free() == 0, (
        "A still-running, progressing owner's lease must never be reclaimed on "
        "a wall-clock basis (Blocker 2) — over-admission regression."
    )
    assert len(registry.leases()) == 1
    assert registry.leases()[0].owner_session_id == session.agent_session_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reap_disabled_still_logs_but_does_not_reclaim(redis_test_db, monkeypatch, caplog):
    """Operator CONCERN regression guard: SLOT_LEASE_REAP_DISABLED=1 preserves
    detection (the leaked-slot WARNING still fires every tick) while
    suppressing the reclaim action and the slot_reclaims counter increment —
    the kill-switch degrades to detect-only, never to no-visibility.
    """
    monkeypatch.setenv("SLOT_LEASE_REAP_DISABLED", "1")

    registry = SlotLeaseRegistry(max_concurrent=1)
    _session_state._slot_registry = registry

    await registry.acquire()
    session = _create_session("orphan-disabled-1", status="failed")
    registry.bind(session.agent_session_id)
    assert registry.permits_free() == 0

    with caplog.at_level(logging.WARNING, logger="agent.session_health"):
        await _agent_session_health_check()

    # Detection preserved: the leaked-slot fingerprint WARNING still fires.
    assert any("SLOT-LEASE FINGERPRINT" in record.getMessage() for record in caplog.records), (
        "Expected the leaked-slot fingerprint WARNING even with the kill-switch enabled"
    )

    # Reclaim suppressed: permit stays held, lease stays bound.
    assert registry.permits_free() == 0, (
        "SLOT_LEASE_REAP_DISABLED=1 must suppress the reclaim action — the permit must remain held."
    )
    assert len(registry.leases()) == 1
