"""Regression tests for generalizing the A1 rebuild guard to every IndexedField.

Issue #2207 (redis-phantom-agentsession-flood): PR #2102's A1 guard scoped the
identity-less-skip shim to only the ``status`` field's ``on_save``. But
``task_type`` and ``claude_session_uuid`` are also
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
from unittest.mock import patch

import msgpack
import pytest


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
    # Durability plan #2494 deleted the ``claude_pid`` IndexedField (the #1271
    # pid-valued, unbounded-cardinality anti-pattern).
    assert indexed_field_names == {"status", "task_type", "claude_session_uuid"}


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


# Durability plan #2494 deleted the ``claude_pid`` IndexedField, so the former
# ``test_claude_pid_index_does_not_reinflate_from_identityless_hashes`` is
# removed with it — there is no pid index left to re-inflate.


def test_quarantine_counts_each_identityless_row_once_across_all_indexed_fields():
    """_last_quarantined_identityless is a de-duplicated ROW count, not a
    per-field invocation count (#3199). Under popoto's row-scoped counting,
    n_ghosts identity-less rows produce a count of n_ghosts regardless of how
    many IndexedFields would have touched each row through popoto's on_save
    loop -- a row seen through both the divergence pre-check and the on_save
    shim, or through multiple fields, still counts once. This also asserts
    the #2207 generalization more directly than counter arithmetic ever
    could: all three $IndexF sets stay clean, not just one."""
    from models.agent_session import AgentSession

    pk = "test-2207-sum"
    n_ghosts = 5
    for j in range(n_ghosts):
        _seed_identityless_hash(pk, f"ghostsum{j:026d}")

    AgentSession.repair_indexes()
    assert AgentSession._last_quarantined_identityless == n_ghosts

    r = _redis()
    for field_key in (
        "$IndexF:AgentSession:status:pending",
        "$IndexF:AgentSession:task_type:None",
        "$IndexF:AgentSession:claude_session_uuid:None",
    ):
        assert r.scard(field_key) == 0, f"{field_key} was re-inflated from identity-less rows"


def test_quarantine_count_persisted_to_redis_key_for_doctor():
    """The Redis-persisted quarantine count and the doctor suffix are the
    counter's only durable, cross-process surface -- assert both directly
    rather than only by inspection."""
    from models.agent_session import _LAST_QUARANTINED_IDENTITYLESS_REDIS_KEY, AgentSession
    from tools.doctor import _recent_quarantine_suffix

    pk = "test-3199-doctor-suffix"
    n_ghosts = 3
    for j in range(n_ghosts):
        _seed_identityless_hash(pk, f"ghostdoc{j:025d}")

    AgentSession.repair_indexes()

    raw = _redis().get(_LAST_QUARANTINED_IDENTITYLESS_REDIS_KEY)
    assert raw is not None
    assert int(raw) == n_ghosts

    suffix = _recent_quarantine_suffix()
    assert suffix != ""
    assert str(n_ghosts) in suffix


def test_diverged_key_decode_failure_is_counted_not_raised(monkeypatch):
    """A decode that raises for a diverged key must not fail repair_indexes()
    -- a row that cannot even be decoded is certainly not a hydrated session,
    so it is counted as identity-less rather than propagating the exception.

    Same structural hazard as the ImportError test: popoto's own
    rebuild_indexes() imports and calls decode_popoto_model_hashmap for
    EVERY scanned row (not just diverged ones) before repair_indexes()'s own
    diverged-key loop ever runs, under the bare try/finally with no except.
    A decode stub that raises unconditionally therefore raises out of
    popoto's own call first, unrelated to the branch under test. Stub
    rebuild_indexes() itself (as the ImportError-degrade test does) so only
    the call-site import/call inside repair_indexes()'s diverged-key loop is
    exercised."""
    import popoto.models.encoding as encoding_module
    from popoto.models.base import RebuildIndexesResult

    from models.agent_session import AgentSession

    pk = "test-3199-decode-raises"
    n_ghosts = 3
    seeded_keys = [_seed_identityless_hash(pk, f"ghostdecode{j:024d}") for j in range(n_ghosts)]

    monkeypatch.setattr(
        AgentSession,
        "rebuild_indexes",
        classmethod(lambda cls: RebuildIndexesResult(0, seeded_keys)),
    )

    def _boom(*args, **kwargs):
        raise ValueError("simulated decode failure")

    monkeypatch.setattr(encoding_module, "decode_popoto_model_hashmap", _boom)

    result = AgentSession.repair_indexes()
    assert isinstance(result, tuple) and len(result) == 2
    assert AgentSession._last_quarantined_identityless == n_ghosts


def test_decode_import_failure_degrades_to_unfiltered_count_and_reports_loud(monkeypatch):
    """The obvious one-step recipe (just make decode_popoto_model_hashmap
    unimportable) cannot reach the ImportError degrade branch: popoto's own
    rebuild_indexes() imports the very same symbol from the very same module
    inside the call repair_indexes() makes, which sits under a bare
    try/finally with no except -- any technique that makes the symbol
    unimportable raises out of cls.rebuild_indexes() and escapes
    repair_indexes() entirely before the degrade branch is ever reached.

    Working recipe: stub rebuild_indexes() to return a canned
    RebuildIndexesResult so popoto's own internal import of the symbol never
    runs, and ONLY THEN remove the symbol -- with the real rebuild stubbed
    out, the only remaining import of it is the one inside repair_indexes(),
    which is the branch under test.

    Also asserts the Sentry latch: two degraded passes in the same process
    must log ERROR each time but capture to Sentry only once."""
    import popoto.models.encoding as encoding_module
    from popoto.models.base import RebuildIndexesResult

    from models.agent_session import AgentSession

    pk = "test-3199-import-degrade"
    seeded_keys = [_seed_identityless_hash(pk, f"ghostimp{j:024d}") for j in range(4)]

    # Reset the Sentry latch so this assertion does not depend on test order.
    monkeypatch.setattr(AgentSession, "_decode_degrade_reported", False)

    # Stub rebuild_indexes so popoto's own internal import of
    # decode_popoto_model_hashmap (inside base.py's rebuild_indexes()) never
    # runs -- this is the technique test_plain_int_rebuild_result_degrades_to_
    # shim_only also uses.
    monkeypatch.setattr(
        AgentSession,
        "rebuild_indexes",
        classmethod(lambda cls: RebuildIndexesResult(0, seeded_keys)),
    )

    # Only now remove the symbol. With the real rebuild stubbed out, this
    # cannot break anything but the call-site import inside repair_indexes(),
    # which is the branch under test.
    monkeypatch.delattr(encoding_module, "decode_popoto_model_hashmap", raising=False)

    with (
        patch("models.agent_session.logger") as mock_logger,
        patch("sentry_sdk.capture_message") as mock_sentry,
    ):
        result_1 = AgentSession.repair_indexes()
        result_2 = AgentSession.repair_indexes()

    for result in (result_1, result_2):
        assert isinstance(result, tuple) and len(result) == 2

    # Do NOT widen the production `except ImportError` to `except Exception`
    # to make a naive version of this test pass -- that would swallow real
    # decode faults on the hot startup path in exchange for a test shortcut.
    assert AgentSession._last_quarantined_identityless == len(seeded_keys)
    # logger.error is the unconditional per-pass signal of record: it fires
    # on EVERY degraded pass.
    assert mock_logger.error.call_count == 2
    # The Sentry capture is latched to once per process: a moved upstream
    # symbol is a permanent condition, and repair_indexes() runs on worker
    # startup, an hourly reflection, and session pickup, so an unlatched
    # capture would be an unthrottled fleet-wide error stream.
    mock_sentry.assert_called_once()


def test_plain_int_rebuild_result_degrades_to_shim_only(monkeypatch):
    """A future popoto that returns a bare int (not RebuildIndexesResult)
    must not crash the getattr(result, "diverged_keys", ()) guard -- it
    should degrade to counting only what the retained on_save shim caught,
    with no AttributeError."""
    from models.agent_session import AgentSession

    real_rebuild = AgentSession.rebuild_indexes.__func__

    def _plain_int_rebuild(cls):
        return int(real_rebuild(cls))

    monkeypatch.setattr(AgentSession, "rebuild_indexes", classmethod(_plain_int_rebuild))

    pk = "test-3199-plain-int"
    for j in range(2):
        _seed_identityless_hash(pk, f"ghostplain{j:025d}")

    result = AgentSession.repair_indexes()
    assert isinstance(result, tuple) and len(result) == 2
    # The seeded ghosts are diverged rows under real popoto 1.9.0 and never
    # reach on_save, so with diverged_keys unavailable (plain int result)
    # nothing catches them this pass -- documenting the degrade rather than
    # newly breaking anything, since the shim remains the fallback seam.
    assert AgentSession._last_quarantined_identityless == 0


def test_redis_persistence_failure_is_non_fatal(monkeypatch):
    """The Redis SET that persists the count for the doctor's cross-process
    read must never fail the repair itself, and the in-memory counter must
    still be populated even when persistence raises."""
    from popoto.redis_db import POPOTO_REDIS_DB

    from models.agent_session import AgentSession

    pk = "test-3199-persist-fail"
    n_ghosts = 2
    for j in range(n_ghosts):
        _seed_identityless_hash(pk, f"ghostpf{j:025d}")

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated Redis SET failure")

    monkeypatch.setattr(POPOTO_REDIS_DB, "set", _boom)

    result = AgentSession.repair_indexes()
    assert isinstance(result, tuple) and len(result) == 2
    assert AgentSession._last_quarantined_identityless == n_ghosts


def test_shims_restored_after_repair_no_leak():
    """After repair_indexes() returns, every IndexedField's on_save must be
    back to the class-level classmethod -- no shim left installed."""
    from popoto import IndexedField

    from models.agent_session import AgentSession

    AgentSession.repair_indexes()

    for _, f in AgentSession._meta.fields.items():
        if isinstance(f, IndexedField):
            assert "on_save" not in f.__dict__, f"shim leaked on field {f}"


def test_shims_restored_after_rebuild_indexes_raises(monkeypatch):
    """Critique CONCERN #1: the happy-path restore (test_shims_restored_after_repair_no_leak)
    only proves the finally-restore fires when rebuild_indexes() succeeds. If
    cls.rebuild_indexes() raises mid-rebuild, the install-inside-try + finally
    invariant must still restore EVERY IndexedField's original on_save -- no
    shim may leak past the exception."""
    from popoto import IndexedField

    from models.agent_session import AgentSession

    def _boom():
        raise RuntimeError("simulated rebuild_indexes failure")

    monkeypatch.setattr(AgentSession, "rebuild_indexes", classmethod(lambda cls: _boom()))

    with pytest.raises(RuntimeError, match="simulated rebuild_indexes failure"):
        AgentSession.repair_indexes()

    for _, f in AgentSession._meta.fields.items():
        if isinstance(f, IndexedField):
            assert "on_save" not in f.__dict__, f"shim leaked on field {f} after exception"

    # Lock must also be released so a subsequent call is not spuriously
    # treated as re-entrant.
    lock = AgentSession.__dict__.get("_repair_lock")
    assert lock is not None
    assert lock.acquire(blocking=False), "repair_indexes lock not released after exception"
    lock.release()


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
