"""Regression tests for generalizing the A1 rebuild guard to every IndexedField.

Issue #2207 (redis-phantom-agentsession-flood): PR #2102's A1 guard scoped the
identity-less-skip shim to only the ``status`` field's ``on_save``. But
``task_type``, ``claude_session_uuid``, and ``claude_pid`` are also
``IndexedField``s that popoto's ``rebuild_indexes()`` re-SADDs on every pass
for identity-less hashes -- the same phantom re-inflation leak, just on a
different index. ``AgentSession.repair_indexes()`` now enumerates every
IndexedField at runtime (``isinstance(f, IndexedField)``) and installs the
guard on all of them.

These tests use the autouse ``redis_test_db`` fixture (tests/conftest.py) which
patches ``POPOTO_REDIS_DB`` to a per-worker test DB and flushes it -- no
production Redis is ever touched. All seeded records use a test-scoped
``project_key``.
"""

from __future__ import annotations

import threading

import msgpack


def _redis():
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _seed_identityless_hash(project_key: str, tag: str) -> str:
    """Seed a 7-part ``AgentSession:*`` hash with NO session_id."""
    r = _redis()
    key = f"AgentSession:None:{tag}:None:{project_key}:None:None"
    assert len(key.split(":")) == 7, "seed key must match db_key_length"
    r.hset(key, mapping={"id": msgpack.packb(None), "placeholder": msgpack.packb("")})
    return key


def test_all_indexed_fields_enumerated_at_runtime():
    """repair_indexes derives the guarded field set from IndexedField instances,
    not a hardcoded name list -- assert it covers every current IndexedField."""
    from popoto import IndexedField

    from models.agent_session import AgentSession

    indexed_field_names = {
        name for name, f in AgentSession._meta.fields.items() if isinstance(f, IndexedField)
    }
    assert indexed_field_names == {"status", "task_type", "claude_session_uuid", "claude_pid"}


def test_task_type_index_does_not_reinflate_from_identityless_hashes():
    from models.agent_session import AgentSession

    pk = "test-2207-tasktype"
    r = _redis()
    index_key = "$IndexF:AgentSession:task_type:trm"

    s = AgentSession(session_id="healthy-tt", project_key=pk, task_type="trm")
    s.save()
    assert r.sismember(index_key, s._redis_key)

    for j in range(3):
        _seed_identityless_hash(pk, f"ghosttt{j:028d}")

    AgentSession.repair_indexes()
    # Only the one healthy record should remain indexed under task_type=trm;
    # the identity-less hashes (which decode task_type=None, not "trm") must
    # not pollute a task_type index either.
    assert r.scard(index_key) == 1
    assert AgentSession._last_quarantined_identityless >= 3

    # Second pass stays flat -- no re-inflation.
    AgentSession.repair_indexes()
    assert r.scard(index_key) == 1


def test_claude_pid_index_does_not_reinflate_from_identityless_hashes():
    from models.agent_session import AgentSession

    pk = "test-2207-pid"
    r = _redis()

    s = AgentSession(session_id="healthy-pid", project_key=pk, claude_pid=12345)
    s.save()
    index_key = "$IndexF:AgentSession:claude_pid:12345"
    assert r.sismember(index_key, s._redis_key)

    for j in range(2):
        _seed_identityless_hash(pk, f"ghostpid{j:027d}")

    AgentSession.repair_indexes()
    assert r.scard(index_key) == 1

    AgentSession.repair_indexes()
    assert r.scard(index_key) == 1


def test_quarantine_count_sums_across_all_indexed_fields():
    """_last_quarantined_identityless accumulates shim invocations across every
    IndexedField's shim, not just status's -- so it should be >= a per-field
    count times the number of IndexedFields touched during one rebuild pass
    (popoto's on_save loop runs every field for every hash)."""
    from models.agent_session import AgentSession

    pk = "test-2207-sum"
    n_ghosts = 5
    for j in range(n_ghosts):
        _seed_identityless_hash(pk, f"ghostsum{j:026d}")

    AgentSession.repair_indexes()
    # 4 IndexedFields x n_ghosts identity-less hashes, at minimum (popoto may
    # additionally write back artifact hashes during scan_iter).
    assert AgentSession._last_quarantined_identityless >= n_ghosts * 4


def test_shims_restored_after_repair_no_leak():
    """After repair_indexes() returns, every IndexedField's on_save must be
    back to the class-level classmethod -- no shim left installed."""
    from popoto import IndexedField

    from models.agent_session import AgentSession

    AgentSession.repair_indexes()

    for _, f in AgentSession._meta.fields.items():
        if isinstance(f, IndexedField):
            assert "on_save" not in f.__dict__, f"shim leaked on field {f}"


def test_reentrant_call_from_another_thread_is_a_noop():
    """A concurrent repair_indexes() call while one is already in-flight must
    not race the shim installs -- it should back off, log, and return (0, 0)."""
    from models.agent_session import AgentSession

    pk = "test-2207-reentrant"
    for j in range(3):
        _seed_identityless_hash(pk, f"ghostre{j:027d}")

    # Hold the lock manually to simulate an in-flight repair, then verify a
    # second call observes it as busy and no-ops rather than blocking.
    lock = AgentSession.__dict__.get("_repair_lock")
    if lock is None:
        lock = threading.Lock()
        AgentSession._repair_lock = lock

    acquired = lock.acquire(blocking=False)
    assert acquired
    try:
        result = AgentSession.repair_indexes()
    finally:
        lock.release()

    assert result == (0, 0)
