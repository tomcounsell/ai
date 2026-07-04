"""Integration tests for Fix #5 (#1821) — out-of-domain slot recovery.

The central proof (Acceptance #1): recovery is DRIVEN from a process OTHER than
the worker loop it polices. Here the bridge check runs FROM THE TEST PROCESS (not
the worker event loop), pushes a Redis reclaim-request, and the worker-side drain
then frees the leaked permit without a restart.

Also covers the Fix #5 failure paths + the four race scenarios:
- stale beacon → loop_wedged logged, NO reclaim-request, NO kill (no critical key)
- BRIDGE_SLOT_RECLAIM_ENABLED=0 → detect/log only, no reclaim-request
- drain fires under SLOT_LEASE_REAP_DISABLED=1 (distinct path from autonomous reclaim)
- a still-running requested owner is NOT reclaimed (Risk 3 + #1868)
- get_by_id → None / exception requested owner is SKIPPED (concern #2 / #1868)
- a burst of > RECLAIM_REQUESTS_MAX distinct owners keeps the list bounded (Race 4)
- bounded wall-time on the multi-owner bridge push (concern #4)
- new-worker / old-bridge → worker emits bridge_contract_stale (concern #5)

Uses the autouse redis_test_db fixture for Redis isolation.
"""

from __future__ import annotations

import json
import socket
import time
from unittest.mock import patch

import pytest

import agent.session_health as sh
import agent.session_state as session_state
import monitoring.session_watchdog as sw
from agent.slot_lease import SlotLeaseRegistry
from models.agent_session import AgentSession

_HOST = socket.gethostname()
_BEACON_KEY = f"worker:loop_beacon:{_HOST}"
_LEASES_KEY = f"worker:slot:leases:{_HOST}"
_RECLAIM_KEY = f"worker:slot:reclaim_requests:{_HOST}"
_ACTIONS_KEY = f"worker:watchdog:actions:{_HOST}"


def _redis():
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _create_session(status: str, project_key: str = "test", **overrides) -> AgentSession:
    defaults = {
        "project_key": project_key,
        "status": status,
        "priority": "normal",
        "created_at": time.time(),
        "session_id": f"session-{status}-{time.time()}-{id(overrides)}",
        "working_dir": "/tmp/test",
        "message_text": "test",
        "sender_name": "Test",
        "chat_id": f"chat-{time.time()}",
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession.create(**defaults)


def _publish_beacon(*, armed: bool = True, age_offset: float = 0.0) -> None:
    _redis().set(
        _BEACON_KEY,
        json.dumps({"wall_ts": time.time() - age_offset, "loop_beacon_age_s": 0.1, "armed": armed}),
        ex=sh.WORKER_LOOP_BEACON_TTL_SECONDS,
    )


def _reclaim_list() -> list[str]:
    return [v.decode() if isinstance(v, bytes) else v for v in _redis().lrange(_RECLAIM_KEY, 0, -1)]


def _actions() -> list[dict]:
    return [json.loads(v) for v in _redis().lrange(_ACTIONS_KEY, 0, -1)]


def _counter(key: str) -> int:
    val = _redis().get(key)
    return int(val) if val else 0


@pytest.fixture
def registry():
    """Install a fresh SlotLeaseRegistry as the module-global singleton."""
    original = session_state._slot_registry
    reg = SlotLeaseRegistry(max_concurrent=20)
    session_state._slot_registry = reg
    try:
        yield reg
    finally:
        session_state._slot_registry = original


async def _orphan_slot(reg: SlotLeaseRegistry, status: str = "completed") -> AgentSession:
    """Acquire a permit and bind a lease to a session, leaving it unreleased."""
    session = _create_session(status="running")
    await reg.acquire()
    reg.bind(session.id)
    # Flip terminal WITHOUT releasing the permit — the leak the reaper targets.
    session.status = status
    session.save(update_fields=["status", "updated_at"])
    return session


# ---------------------------------------------------------------------------
# Acceptance #1 — recovery driven from a non-worker process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acceptance_1_reclaim_driven_from_non_worker_process(registry):
    """Bridge (test process) pushes a reclaim-request; worker drain frees the slot."""
    session = await _orphan_slot(registry, status="completed")
    before = registry.permits_free()
    assert session.id in {lease.owner_session_id for lease in registry.leases()}

    _publish_beacon(armed=True)
    sh._publish_slot_leases(registry, registry.leases())

    # Runs FROM THE TEST PROCESS, not the worker loop (Acceptance #1).
    sw.check_worker_liveness_and_slots()

    assert session.id in _reclaim_list(), "bridge must push a reclaim-request"
    assert any(a["action"] == "reclaim_requested" for a in _actions())

    # Worker-side drain performs the actual reclaim (loop-affinity physics).
    sh._drain_reclaim_requests(registry)

    assert registry.permits_free() == before + 1, "permit must be freed by the drain"
    assert session.id not in {lease.owner_session_id for lease in registry.leases()}
    assert _counter(f"{session.project_key}:session-health:bridge_reclaims") == 1


# ---------------------------------------------------------------------------
# Stale beacon → loop_wedged, NO reclaim, NO kill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_beacon_logs_loop_wedged_no_kill(registry):
    await _orphan_slot(registry, status="completed")
    _publish_beacon(armed=True, age_offset=sw.BRIDGE_WORKER_BEACON_STALE_S + 30)
    sh._publish_slot_leases(registry, registry.leases())

    sw.check_worker_liveness_and_slots()

    assert _reclaim_list() == [], "no reclaim-request under a stale (wedged) beacon"
    assert _counter(f"{_HOST}:worker-watchdog:loop_wedged_detected") == 1
    assert any(a["action"] == "loop_wedged" and a["deferring_kill"] for a in _actions())
    # NO kill — the bridge must never write the critical worker-recovery key.
    assert _redis().get(f"worker:watchdog:critical:{_HOST}") is None


@pytest.mark.asyncio
async def test_missing_beacon_treated_as_wedged(registry):
    await _orphan_slot(registry, status="completed")
    _redis().delete(_BEACON_KEY)  # worker down / TTL expired
    sh._publish_slot_leases(registry, registry.leases())

    sw.check_worker_liveness_and_slots()

    assert _reclaim_list() == []
    assert _counter(f"{_HOST}:worker-watchdog:loop_wedged_detected") == 1


@pytest.mark.asyncio
async def test_unarmed_beacon_never_wedged_no_reclaim(registry):
    await _orphan_slot(registry, status="completed")
    _publish_beacon(armed=False)  # loop not yet ticked
    sh._publish_slot_leases(registry, registry.leases())

    sw.check_worker_liveness_and_slots()

    assert _reclaim_list() == []
    assert _counter(f"{_HOST}:worker-watchdog:loop_wedged_detected") == 0


# ---------------------------------------------------------------------------
# Kill-switch + gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclaim_disabled_detects_but_does_not_push(registry):
    await _orphan_slot(registry, status="completed")
    _publish_beacon(armed=True)
    sh._publish_slot_leases(registry, registry.leases())

    with patch.dict("os.environ", {"BRIDGE_SLOT_RECLAIM_ENABLED": "0"}):
        sw.check_worker_liveness_and_slots()

    assert _reclaim_list() == [], "no reclaim-request when the kill-switch is off"


@pytest.mark.asyncio
async def test_drain_fires_under_slot_lease_reap_disabled(registry):
    """The drain is a DISTINCT path from the autonomous reclaim, so it fires even
    when SLOT_LEASE_REAP_DISABLED=1 gates the autonomous Phase-2 reclaim off."""
    session = await _orphan_slot(registry, status="completed")
    before = registry.permits_free()
    # Pre-load the reclaim-request as if the bridge had pushed it.
    _redis().rpush(_RECLAIM_KEY, session.id)

    with patch.dict("os.environ", {"SLOT_LEASE_REAP_DISABLED": "1"}):
        sh._reap_slot_leases()  # full reap tick; autonomous reclaim gated OFF

    assert registry.permits_free() == before + 1, "drain must reclaim even when reap disabled"


# ---------------------------------------------------------------------------
# Risk 3 / #1868 — never strip a live or unknown owner's permit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_requested_owner_not_reclaimed(registry):
    """A requested owner still 'running' must NOT be reclaimed (Risk 3)."""
    session = await _orphan_slot(registry, status="running")  # stays live
    before = registry.permits_free()
    _redis().rpush(_RECLAIM_KEY, session.id)

    sh._drain_reclaim_requests(registry)

    assert registry.permits_free() == before, "live owner's permit must be preserved"
    assert session.id in {lease.owner_session_id for lease in registry.leases()}


@pytest.mark.asyncio
async def test_none_lookup_requested_owner_skipped(registry):
    """get_by_id → None (transient blip) is 'unknown → SKIP', NOT terminal (#1868)."""
    await reg_acquire_ghost(registry, "ghost-owner-none")
    before = registry.permits_free()
    _redis().rpush(_RECLAIM_KEY, "ghost-owner-none")

    # No AgentSession row for this owner → get_by_id returns None.
    sh._drain_reclaim_requests(registry)

    assert registry.permits_free() == before, "unknown owner must NOT be reclaimed"
    assert "ghost-owner-none" in {lease.owner_session_id for lease in registry.leases()}


@pytest.mark.asyncio
async def test_lookup_exception_requested_owner_skipped(registry):
    """A get_by_id EXCEPTION (Redis blip) is also 'unknown → SKIP' (#1868)."""
    session = await _orphan_slot(registry, status="completed")
    before = registry.permits_free()
    _redis().rpush(_RECLAIM_KEY, session.id)

    with patch.object(AgentSession, "get_by_id", side_effect=RuntimeError("redis blip")):
        sh._drain_reclaim_requests(registry)

    assert registry.permits_free() == before, "lookup exception must NOT reclaim"


async def reg_acquire_ghost(reg: SlotLeaseRegistry, owner: str) -> None:
    """Bind a lease for an owner id that has no AgentSession row."""
    await reg.acquire()
    reg.bind(owner)


# ---------------------------------------------------------------------------
# Race 4 — bounded reclaim-request list + bounded push wall-time (concern #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reclaim_request_list_bounded_under_burst(registry, monkeypatch):
    """A burst of > RECLAIM_REQUESTS_MAX distinct owners keeps the list bounded."""
    monkeypatch.setattr(sw, "RECLAIM_REQUESTS_MAX", 5)
    owners = []
    for _ in range(12):
        s = await _orphan_slot(registry, status="completed")
        owners.append(s.id)
    _publish_beacon(armed=True)
    sh._publish_slot_leases(registry, registry.leases())

    start = time.monotonic()
    sw.check_worker_liveness_and_slots()
    elapsed = time.monotonic() - start

    assert _redis().llen(_RECLAIM_KEY) <= 5, "LTRIM must bound the reclaim-request list"
    # Concern #4: batched pushes complete well under a serial N×socket_timeout bound.
    assert elapsed < 3.0, f"multi-owner push must be non-blocking (took {elapsed:.2f}s)"


# ---------------------------------------------------------------------------
# Healthy tick clears dedup markers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_tick_clears_reclaim_dedup(registry):
    """After a leak is requested, a subsequent healthy tick clears the dedup markers."""
    session = await _orphan_slot(registry, status="completed")
    _publish_beacon(armed=True)
    sh._publish_slot_leases(registry, registry.leases())
    sw.check_worker_liveness_and_slots()
    dedup_key = f"{sw.WORKER_SLOT_RECLAIM_DEDUP_KEY_PREFIX}{_HOST}:{session.id}"
    assert _redis().get(dedup_key) is not None

    # Resolve the leak: drain frees the permit, republish an empty lease snapshot.
    sh._drain_reclaim_requests(registry)
    sh._publish_slot_leases(registry, registry.leases())
    sw.check_worker_liveness_and_slots()

    assert _redis().get(dedup_key) is None, "healthy tick must clear the dedup marker"


# ---------------------------------------------------------------------------
# bridge_contract_stale — new-worker / old-bridge (concern #5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_contract_stale_emitted_when_no_bridge_pushes(registry):
    """Terminal-owner leak present but the reclaim-request channel is always empty
    (no bridge pushing) → worker emits bridge_contract_stale rather than dropping.

    #1873 item 2: the stale-check now reads the owner map built by
    ``_reap_slot_leases`` (drained == 0 path), not its own ``get_by_id`` loop, so
    this drives the full reap tick end-to-end.
    """
    await _orphan_slot(registry, status="completed")
    assert _redis().llen(_RECLAIM_KEY) == 0  # old-bridge never pushes
    _last_drain_key = f"{sh.WORKER_SLOT_LAST_RECLAIM_DRAIN_KEY_PREFIX}{_HOST}"
    _redis().delete(_last_drain_key)  # no prior drain ts → stale

    # Full reap tick: drained == 0 → owner map built → stale-check reads the map.
    with patch.dict("os.environ", {"SLOT_LEASE_REAP_DISABLED": "1"}):
        sh._reap_slot_leases()  # gate Phase-2 off so the terminal lease survives

    assert _counter(f"{_HOST}:worker-watchdog:bridge_contract_stale") == 1
    assert any(a["action"] == "bridge_contract_stale" for a in _actions())


# ---------------------------------------------------------------------------
# #1873 item 2 — reap tick drives the stale-check off the owner map; Phase-2
# reclaim reads FRESH (the #1868 None-divergence + the resume-during-drain guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_phase2_reclaims_terminal_owner_fresh_read(registry):
    """Phase-2 re-reads each owner FRESH and reclaims a genuinely terminal one."""
    session = await _orphan_slot(registry, status="completed")
    before = registry.permits_free()

    sh._reap_slot_leases()  # full tick, Phase-2 enabled

    assert registry.permits_free() == before + 1, "terminal owner's permit must be reclaimed"
    assert session.id not in {lease.owner_session_id for lease in registry.leases()}


@pytest.mark.asyncio
async def test_reap_not_found_owner_reclaimed_but_no_stale(registry):
    """A not-found (None) owner: Phase-2 reclaims (reaper-side #1868 policy), but the
    read-only stale-check treats None as unknown → NO stale emission."""
    await reg_acquire_ghost(registry, "ghost-owner-reap-none")
    before = registry.permits_free()
    _redis().delete(f"{_HOST}:worker-watchdog:bridge_contract_stale")

    sh._reap_slot_leases()  # get_by_id → None for the ghost owner

    assert registry.permits_free() == before + 1, "not-found owner reclaimed by the reaper"
    assert "ghost-owner-reap-none" not in {lease.owner_session_id for lease in registry.leases()}
    assert _counter(f"{_HOST}:worker-watchdog:bridge_contract_stale") == 0, (
        "None owner is unknown for the stale-check → no emission"
    )


@pytest.mark.asyncio
async def test_reap_lookup_error_owner_no_reclaim_no_stale(registry):
    """A lookup-error (_ABSENT) owner: Phase-2 skips reclaim (fresh read raised) AND
    the stale-check records _ABSENT (not terminal) → no reclaim, no stale, no crash."""
    session = await _orphan_slot(registry, status="completed")
    before = registry.permits_free()
    _redis().delete(f"{_HOST}:worker-watchdog:bridge_contract_stale")

    with patch.object(AgentSession, "get_by_id", side_effect=RuntimeError("redis blip")):
        sh._reap_slot_leases()  # must not raise

    assert registry.permits_free() == before, "lookup-error owner must NOT be reclaimed"
    assert session.id in {lease.owner_session_id for lease in registry.leases()}
    assert _counter(f"{_HOST}:worker-watchdog:bridge_contract_stale") == 0, (
        "_ABSENT owner is not terminal for the stale-check → no emission"
    )


@pytest.mark.asyncio
async def test_reap_resume_during_drain_not_reclaimed(registry):
    """Regression guard: an owner terminal at owner-map-snapshot time but re-reads
    NON-terminal at Phase-2 reclaim time (a resume-during-drain simulation) must NOT
    be reclaimed — proving Phase-2's FRESH re-read prevents the live-permit strip."""
    from types import SimpleNamespace

    session = await _orphan_slot(registry, status="completed")
    before = registry.permits_free()

    terminal_rec = SimpleNamespace(status="completed", project_key="test")
    live_rec = SimpleNamespace(status="running", project_key="test")
    calls = {"n": 0}

    def _fake_get_by_id(_owner_id):
        calls["n"] += 1
        # Call #1 = owner-map snapshot (terminal); call #2 = Phase-2 fresh read (live).
        return terminal_rec if calls["n"] == 1 else live_rec

    with patch.object(AgentSession, "get_by_id", side_effect=_fake_get_by_id):
        sh._reap_slot_leases()

    assert registry.permits_free() == before, "resumed (now-live) owner's permit must survive"
    assert session.id in {lease.owner_session_id for lease in registry.leases()}


@pytest.mark.asyncio
async def test_reap_drained_positive_records_beacon_no_stale(registry):
    """C2 guard: on the ``drained > 0`` path the owner map is never built, the drain
    beacon IS written, and no stale is emitted (owner_records defaults to {})."""
    session = await _orphan_slot(registry, status="completed")
    _redis().rpush(_RECLAIM_KEY, session.id)  # bridge pushed one request
    last_drain_key = f"{sh.WORKER_SLOT_LAST_RECLAIM_DRAIN_KEY_PREFIX}{_HOST}"
    _redis().delete(last_drain_key)
    _redis().delete(f"{_HOST}:worker-watchdog:bridge_contract_stale")

    sh._reap_slot_leases()  # drain pops 1 → drained == 1

    assert _redis().get(last_drain_key) is not None, "drained>0 must record the drain beacon"
    assert _counter(f"{_HOST}:worker-watchdog:bridge_contract_stale") == 0, (
        "drained>0 path must not emit stale"
    )


# ---------------------------------------------------------------------------
# #1873 item 1 — SCAN-based reclaim-dedup clear (non-blocking, fail-quiet)
# ---------------------------------------------------------------------------


def test_clear_reclaim_dedup_scan_deletes_matching_markers():
    """SCAN enumerate + batched delete removes every matching dedup marker."""
    r = _redis()
    keys = [f"{sw.WORKER_SLOT_RECLAIM_DEDUP_KEY_PREFIX}{_HOST}:owner-{i}" for i in range(7)]
    for k in keys:
        r.set(k, "1", ex=sw.WORKER_SLOT_KEY_TTL_SECONDS)

    sw._clear_reclaim_dedup(r, _HOST)

    for k in keys:
        assert r.get(k) is None, f"{k} must be cleared via the SCAN sweep"


def test_clear_reclaim_dedup_zero_match_no_raise():
    """Zero matching keys → no delete issued, no raise (empty-batch guard)."""
    r = _redis()
    # A host with no markers at all.
    sw._clear_reclaim_dedup(r, f"nohost-{time.time()}")


def test_clear_reclaim_dedup_is_fail_quiet():
    """A raising Redis client is swallowed — the clear never raises (fail-quiet)."""

    class _BoomRedis:
        def scan_iter(self, *_a, **_k):
            raise RuntimeError("simulated scan failure")

    # Must not raise.
    sw._clear_reclaim_dedup(_BoomRedis(), _HOST)
