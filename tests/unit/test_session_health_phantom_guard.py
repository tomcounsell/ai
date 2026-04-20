"""Regression tests for the phantom-record guard in agent/session_health.py (issue #1069).

A "phantom" session is an AgentSession instance returned by ``query.all()`` where
attribute access falls through to the class-level Popoto ``Field`` descriptor
instead of a hydrated string value. Phantoms are produced when an index set
(e.g. ``$IndexF:AgentSession:status:pending`` or ``$Class:AgentSession``)
contains a member that points to a Redis hash with no real field data — either
because the hash was partially written, deleted out from under the index, or
otherwise corrupted.

The bug (issue #1069): ``cleanup_corrupted_agent_sessions`` iterated
``query.all()`` without guarding against phantoms. The length check against
``str(getattr(session, "id", ""))`` then misread a descriptor repr (~60 chars)
as an "id" value, flagged it as "corrupt" (32 != 60), and called
``session.delete()`` — which collateral-damaged REAL records whose indexed-field
values happened to match the phantom.

These tests seed the exact production failure mode (an orphan hash that
returns a phantom) and verify:

1. Phantoms are filtered before mutation.
2. Real records with matching indexed-field values are NOT destroyed.
3. ``repair_indexes()`` runs when phantoms are observed, even if no real
   corrupt records were deleted — clearing orphan ``$IndexF`` members at the
   source.
4. The helper has correct behavior on empty input and on pure-phantom input.
5. Existing behavior (no-op when nothing corrupt and no phantoms) is preserved.
"""

from __future__ import annotations

import logging

import pytest

# ---------------------------------------------------------------------------
# Phantom-seeding helpers
# ---------------------------------------------------------------------------


def _seed_phantom_record(project_key: str = "test", status: str = "pending") -> str:
    """Seed a Redis hash that will materialize as a phantom on query.all().

    Creates a minimal hash with no real field data (just a placeholder key)
    and registers it in the class membership set so ``query.all()`` tries to
    hydrate it. The resulting AgentSession instance will have ``id`` /
    ``agent_session_id`` as a Popoto ``Field`` descriptor instead of a string.

    Returns the fake hash id used.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    fake_id = "ghostidx00000000000000000000000a"
    hash_key = f"AgentSession:None:{fake_id}:None:{project_key}:None"
    POPOTO_REDIS_DB.hset(hash_key, "placeholder", "")
    POPOTO_REDIS_DB.sadd("$Class:AgentSession", hash_key)
    # Also seed the status index to simulate an orphan filter result.
    POPOTO_REDIS_DB.sadd(f"$IndexF:AgentSession:status:{status}", hash_key)
    return fake_id


def _all_sessions_raw() -> list:
    """Return the raw query.all() result (without the phantom filter)."""
    from models.agent_session import AgentSession

    return list(AgentSession.query.all())


# ---------------------------------------------------------------------------
# Tests for _filter_hydrated_sessions (unit-level)
# ---------------------------------------------------------------------------


class TestFilterHydratedSessions:
    def test_empty_input_returns_empty_list(self):
        from agent.session_health import _filter_hydrated_sessions

        assert _filter_hydrated_sessions([]) == []

    def test_phantom_only_returns_empty_and_logs_info(self, caplog):
        from agent.session_health import _filter_hydrated_sessions

        _seed_phantom_record()

        # Pre-assertion: seeding produced at least one phantom.
        raw = _all_sessions_raw()
        has_phantom = any(not isinstance(getattr(s, "agent_session_id", None), str) for s in raw)
        assert has_phantom, "Seeding did not produce a phantom — test would pass vacuously"

        with caplog.at_level(logging.DEBUG, logger="agent.session_health"):
            result = _filter_hydrated_sessions(raw)

        assert result == []
        assert any(
            "phantom-filter" in rec.message.lower() and rec.levelno == logging.INFO
            for rec in caplog.records
        ), "Expected aggregated INFO log with phantom count"

    def test_mixed_hydrated_and_phantom_keeps_only_hydrated(self):
        from agent.session_health import _filter_hydrated_sessions
        from models.agent_session import AgentSession

        # Create one real session.
        real = AgentSession(session_id="real-session", project_key="test", status="pending")
        real.save()

        # Seed a phantom that shares the status index bucket.
        _seed_phantom_record(project_key="test", status="pending")

        result = _filter_hydrated_sessions(_all_sessions_raw())
        ids = {s.agent_session_id for s in result}
        assert real.agent_session_id in ids
        assert len(result) == 1, f"Expected only the real session, got {len(result)}"

    def test_pure_phantom_logs_at_debug_not_warning(self, caplog):
        """Pure phantoms (all fields are Field descriptors) stay at DEBUG level."""
        from agent.session_health import _filter_hydrated_sessions

        _seed_phantom_record()

        with caplog.at_level(logging.DEBUG, logger="agent.session_health"):
            _filter_hydrated_sessions(_all_sessions_raw())

        # The per-record "Dropped phantom record" log should be DEBUG.
        dropped_records = [r for r in caplog.records if "Dropped phantom record" in r.message]
        assert dropped_records, "Expected at least one 'Dropped phantom record' log"
        for rec in dropped_records:
            assert rec.levelno == logging.DEBUG, (
                f"Expected DEBUG for dropped phantom, got {rec.levelname}"
            )


# ---------------------------------------------------------------------------
# Tests for cleanup_corrupted_agent_sessions behavior
# ---------------------------------------------------------------------------


class TestCleanupCorruptedAgentSessions:
    def test_orphan_in_index_does_not_destroy_real_record(self, caplog):
        """Regression test for issue #1069.

        Seed an orphan member in ``$IndexF:AgentSession:status:pending`` that
        points to a phantom hash. Create a live record with matching indexed
        values. Run cleanup. Assert:

        - The live record survives.
        - No real records are destroyed.
        - The orphan is cleaned at the source (via repair_indexes).
        """
        from popoto.redis_db import POPOTO_REDIS_DB

        from agent.session_health import cleanup_corrupted_agent_sessions
        from models.agent_session import AgentSession

        live = AgentSession(session_id="live-session", project_key="test", status="pending")
        live.save()
        live_id = live.agent_session_id

        _seed_phantom_record(project_key="test", status="pending")

        # Pre-assertion: confirm seeding produced a phantom.
        raw = _all_sessions_raw()
        assert any(not isinstance(getattr(s, "agent_session_id", None), str) for s in raw)

        with caplog.at_level(logging.INFO, logger="agent.session_health"):
            cleaned = cleanup_corrupted_agent_sessions()

        # No real records should have been deleted.
        assert cleaned == 0, (
            f"cleanup destroyed {cleaned} records; expected 0 because the only "
            "'corrupt' signal was a phantom"
        )

        # Live record must still exist.
        after = AgentSession.get_by_id(live_id)
        assert after is not None, "Live record was destroyed by cleanup"
        assert after.session_id == "live-session"

        # Orphan should be cleared from the status index after repair_indexes.
        members = POPOTO_REDIS_DB.smembers("$IndexF:AgentSession:status:pending")
        member_strs = {m.decode() if isinstance(m, bytes) else m for m in members}
        # After repair_indexes, the phantom hash key is no longer in the set.
        assert not any("ghostidx" in m for m in member_strs), (
            f"Orphan member still present in index set: {member_strs}"
        )

    def test_zero_sessions_does_not_call_repair_indexes(self, caplog):
        """Preserves existing behavior: empty Redis -> cleanup is a cheap no-op."""
        from agent.session_health import cleanup_corrupted_agent_sessions

        with caplog.at_level(logging.INFO, logger="agent.session_health"):
            cleaned = cleanup_corrupted_agent_sessions()

        assert cleaned == 0
        # No repair_indexes log line should appear.
        repair_logs = [rec for rec in caplog.records if "repair_indexes" in rec.message]
        messages = [rec.message for rec in repair_logs]
        assert not repair_logs, f"repair_indexes should not run on empty pass; got: {messages}"

    def test_phantoms_only_triggers_repair_indexes(self, caplog, monkeypatch):
        """Load-bearing: phantoms present but zero real corrupt -> repair DOES run.

        The assertion is that ``repair_indexes()`` was INVOKED (not that it
        succeeded). The plan explicitly preserves the ``except Exception`` guard
        around the repair call — if rebuild chokes on a partially-populated
        phantom hash, we log and continue.
        """
        from agent.session_health import cleanup_corrupted_agent_sessions
        from models.agent_session import AgentSession

        # Spy on repair_indexes so we can assert invocation even if it raises.
        invoked = {"count": 0}
        original = AgentSession.repair_indexes

        def _spy():
            invoked["count"] += 1
            return original()

        monkeypatch.setattr(AgentSession, "repair_indexes", _spy)

        _seed_phantom_record()

        with caplog.at_level(logging.INFO, logger="agent.session_health"):
            cleaned = cleanup_corrupted_agent_sessions()

        assert cleaned == 0
        assert invoked["count"] == 1, (
            "repair_indexes must be called when phantoms are observed, even if zero "
            "real corrupt records"
        )

    def test_cleanup_never_raises_redis_import_error(self):
        """The raw-Redis fallback block was removed — no stray 'import redis' should fire."""
        # Simply invoking cleanup on a populated db must not need the redis module.
        from agent.session_health import cleanup_corrupted_agent_sessions
        from models.agent_session import AgentSession

        s = AgentSession(session_id="t1", project_key="t", status="pending")
        s.save()
        # No exception expected.
        cleanup_corrupted_agent_sessions()


# ---------------------------------------------------------------------------
# Module-level source-code invariants (policy checks)
# ---------------------------------------------------------------------------


def test_no_raw_redis_import_in_session_health():
    """Policy: session_health.py must not import the redis module at top level.

    The deleted raw-Redis fallback was the only top-level consumer; now that
    it is gone, a stray top-level import would signal a regression.
    """
    import pathlib

    src = pathlib.Path(__file__).resolve()
    # Walk up to the repo root (parent of 'tests').
    repo_root = src
    while repo_root.name and repo_root.name != "tests":
        repo_root = repo_root.parent
    repo_root = repo_root.parent
    health_src = (repo_root / "agent" / "session_health.py").read_text()
    offending_lines = [
        line
        for line in health_src.splitlines()
        if line.startswith("import redis") or line.startswith("from redis ")
    ]
    assert not offending_lines, f"Unexpected top-level redis import: {offending_lines}"


def test_no_raw_redis_scan_or_delete_in_session_health():
    """Policy: the deleted raw-Redis fallback must not have crept back."""
    import pathlib

    src = pathlib.Path(__file__).resolve()
    repo_root = src
    while repo_root.name and repo_root.name != "tests":
        repo_root = repo_root.parent
    repo_root = repo_root.parent
    health_src = (repo_root / "agent" / "session_health.py").read_text()
    assert "r.scan_iter" not in health_src, "r.scan_iter pattern returned to session_health.py"
    # The specific fallback shape: "for key in r.scan_iter(...)". Check just the primitives.
    assert "r.delete(key)" not in health_src, (
        "Raw Redis r.delete(key) fallback re-introduced to session_health.py"
    )


@pytest.fixture
def ensure_popoto_field_import():
    """Ensure the Popoto Field type is importable (sanity check for test env)."""
    from popoto.fields.shortcuts import AutoKeyField  # noqa: F401

    return True
