"""Regression tests for the AgentSession pending-index phantom leak (issue #2101).

Root cause: ``AgentSession.repair_indexes()`` delegates to popoto's
``rebuild_indexes()``, which ``scan_iter``s every ``AgentSession:*`` hash and runs
``field.on_save`` for EVERY field in a generic loop. Because
``status = IndexedField(default="pending")``, any identity-less / near-empty hash
(no ``session_id``) decodes as ``status="pending"`` and gets re-SADDed to
``$IndexF:AgentSession:status:pending`` on every rebuild — the phantom
re-inflation leak. ``query.filter(status="pending")`` then drops these via
``_filter_hydrated_sessions`` (no ``session_id``), so the ORM count stays 0 while
``scard`` climbs.

The A1 fix installs a transient guard on the ``status`` field's ``on_save`` for
the duration of the ``rebuild_indexes()`` call only: identity-less records are
skipped (counted as quarantined), healthy records delegate to popoto's original
``on_save``. The guard is NOT active during normal live ``.save()`` — that path
must still index a legitimate new session (the inverse-bug guard).

These tests use the autouse ``redis_test_db`` fixture (tests/conftest.py) which
patches ``POPOTO_REDIS_DB`` to a per-worker test DB and flushes it — no
production Redis is ever touched. All seeded records use a test-scoped
``project_key``.
"""

from __future__ import annotations

import msgpack

PENDING_INDEX_KEY = "$IndexF:AgentSession:status:pending"


def _redis():
    """Return the (test-DB-patched) popoto Redis client at call time."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _seed_identityless_hash(project_key: str, tag: str) -> str:
    """Seed a 7-part ``AgentSession:*`` hash with NO session_id.

    The key has exactly ``db_key_length`` (7) segments so popoto's rebuild
    ``scan_iter`` picks it up, and the hash carries only an explicit
    ``id=None`` so it decodes with ``status`` falling back to its default
    ("pending") and ``session_id`` absent — the identity-less shape.

    Returns the redis key used.
    """
    r = _redis()
    key = f"AgentSession:None:{tag}:None:{project_key}:None:None"
    assert len(key.split(":")) == 7, "seed key must match db_key_length"
    r.hset(key, mapping={"id": msgpack.packb(None), "placeholder": msgpack.packb("")})
    return key


def _make_healthy_pending(project_key: str, session_id: str):
    """Create and save a real, hydrated pending AgentSession via the ORM."""
    from models.agent_session import AgentSession

    s = AgentSession(session_id=session_id, project_key=project_key, status="pending")
    s.save()
    return s


def _pending_scard() -> int:
    return _redis().scard(PENDING_INDEX_KEY)


# ---------------------------------------------------------------------------
# a. Leak reproduction / no re-inflation
# ---------------------------------------------------------------------------


def test_repair_does_not_reinflate_from_identityless_hashes():
    from models.agent_session import AgentSession

    pk = "test-2101-a"
    n_healthy = 3
    m_identityless = 4
    for i in range(n_healthy):
        _make_healthy_pending(pk, f"healthy-{i}")
    for j in range(m_identityless):
        _seed_identityless_hash(pk, f"ghost{j:028d}")

    # First repair pass: the index tracks healthy records ONLY, NOT n+m — the
    # A1 guard refuses to re-add the identity-less hashes. This is THE fix: the
    # scard no longer re-inflates from identity-less hashes.
    AgentSession.repair_indexes()
    assert _pending_scard() == n_healthy
    # At least the m seeded identity-less hashes are quarantined. popoto's
    # rebuild also writes back its own identity-less artifact hash(es) during
    # the scan (Risk 4 — the raw hash keyspace is NOT cleaned by A1, only the
    # index is), so the count is a lower-bounded per-pass event count, not an
    # exact seed count.
    assert AgentSession._last_quarantined_identityless >= m_identityless

    # Second pass: the index STAYS at n_healthy — no re-inflation across
    # rebuilds. This is the convergence-stability property.
    AgentSession.repair_indexes()
    assert _pending_scard() == n_healthy
    assert AgentSession._last_quarantined_identityless >= m_identityless


# ---------------------------------------------------------------------------
# b. Convergence from a pre-bloated index
# ---------------------------------------------------------------------------


def test_bloated_index_converges_in_one_pass_and_stays_flat():
    from models.agent_session import AgentSession

    pk = "test-2101-b"
    n_healthy = 2
    for i in range(n_healthy):
        _make_healthy_pending(pk, f"real-{i}")

    r = _redis()
    # Pre-bloat the :pending set with phantom members: some backed by
    # identity-less hashes, some pointing at hashes that do not exist at all.
    for j in range(50):
        ghost_key = _seed_identityless_hash(pk, f"bloatE{j:027d}")
        r.sadd(PENDING_INDEX_KEY, ghost_key)
    for j in range(50):
        r.sadd(PENDING_INDEX_KEY, f"AgentSession:None:gone{j:027d}:None:{pk}:None:None")

    assert _pending_scard() > n_healthy  # genuinely bloated

    AgentSession.repair_indexes()
    assert _pending_scard() == n_healthy  # converged in one pass

    AgentSession.repair_indexes()
    assert _pending_scard() == n_healthy  # stays flat


# ---------------------------------------------------------------------------
# c. Gone-hash orphan is cleared by the whole-$IndexF-key rebuild (not A1)
# ---------------------------------------------------------------------------


def test_gone_hash_orphan_cleared_by_wholekey_rebuild():
    from models.agent_session import AgentSession

    pk = "test-2101-c"
    r = _redis()
    # A :pending member whose backing AgentSession:* hash does NOT exist.
    # A1's on_save skip is a no-op for it (rebuild's scan_iter never sees a
    # hash for it) — it is cleared purely by the whole-$IndexF-key
    # delete-and-rebuild, which is therefore load-bearing.
    orphan = f"AgentSession:None:orphan{0:026d}:None:{pk}:None:None"
    r.sadd(PENDING_INDEX_KEY, orphan)
    assert r.sismember(PENDING_INDEX_KEY, orphan)
    assert not r.exists(orphan)

    AgentSession.repair_indexes()

    assert not r.sismember(PENDING_INDEX_KEY, orphan)
    # The gone-hash orphan is NOT counted as an identity-less quarantine —
    # rebuild never decodes a hash for it.
    assert AgentSession._last_quarantined_identityless == 0


# ---------------------------------------------------------------------------
# d. INVERSE-BUG GUARD: normal live save still indexes
# ---------------------------------------------------------------------------


def test_live_save_still_indexes_to_pending():
    pk = "test-2101-d"
    s = _make_healthy_pending(pk, "live-new")
    # The A1 guard must NOT be active on the live-save path — a legitimate
    # brand-new pending session must appear in the status index immediately.
    assert _redis().sismember(PENDING_INDEX_KEY, s._redis_key)


# ---------------------------------------------------------------------------
# e. All-healthy: no quarantine, exact re-index
# ---------------------------------------------------------------------------


def test_all_healthy_rebuild_no_quarantine():
    from models.agent_session import AgentSession

    pk = "test-2101-e"
    n_healthy = 3
    for i in range(n_healthy):
        _make_healthy_pending(pk, f"h-{i}")

    AgentSession.repair_indexes()
    assert _pending_scard() == n_healthy
    assert AgentSession._last_quarantined_identityless == 0


# ---------------------------------------------------------------------------
# f. Empty keyspace: returns a 2-tuple, does not crash
# ---------------------------------------------------------------------------


def test_empty_keyspace_returns_tuple_no_crash():
    from models.agent_session import AgentSession

    result = AgentSession.repair_indexes()
    assert isinstance(result, tuple)
    assert len(result) == 2
    stale, rebuilt = result
    assert stale == 0
    assert rebuilt == 0
    assert AgentSession._last_quarantined_identityless == 0
