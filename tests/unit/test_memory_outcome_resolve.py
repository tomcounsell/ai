"""Unit tests for reflections/memory/memory_outcome_resolve.py.

Covers the orphaned-sidecar sweep (issue #2203): crashed/killed sessions
leave a `data/sessions/{id}/memory_buffer.json` sidecar that the Stop hook
never reaches, so its `injected[]` entries would otherwise receive no
outcome signal. This sweep resolves them to the neutral "deferred" outcome
and cleans up.

Uses real Memory records (project-scoped under "test-outcome-resolve", per
the repo's Manual Testing Hygiene convention) and a real
`hook_utils.memory_bridge` sidecar directory redirected to `tmp_path` via
monkeypatching its `_PROJECT_ROOT` module global -- the same singleton
module the reflection imports, so the redirect is visible to both.
"""

from __future__ import annotations

import json
import os
import sys
import time as _time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_memory_bridge():
    """Import the real hook_utils.memory_bridge module (hook-local sys.path)."""
    hooks_dir = str(_REPO_ROOT / ".claude" / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    from hook_utils import memory_bridge

    return memory_bridge


@pytest.fixture
def sidecar_root(tmp_path, monkeypatch):
    """Redirect hook_utils.memory_bridge's sidecar root to tmp_path.

    Patches the module-global `_PROJECT_ROOT` on the real (singleton, cached
    in sys.modules) memory_bridge module, so both this fixture and the
    reflection under test resolve sidecars under `tmp_path/data/sessions`.
    """
    mb = _get_memory_bridge()
    monkeypatch.setattr(mb, "_PROJECT_ROOT", tmp_path)
    root = tmp_path / "data" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_stale_sidecar(
    sidecar_root: Path, session_id: str, injected: list, *, age_seconds: float
):
    """Write a memory_buffer.json sidecar with mtime pushed `age_seconds` into the past."""
    session_dir = sidecar_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = session_dir / "memory_buffer.json"
    sidecar_path.write_text(json.dumps({"count": 0, "buffer": [], "injected": injected}))
    old_time = _time.time() - age_seconds
    os.utime(sidecar_path, (old_time, old_time))
    return sidecar_path


def _seed_memory(project_key: str = "test-outcome-resolve") -> str:
    """Save a real Memory record and return its memory_id."""
    from models.memory import Memory

    m = Memory.safe_save(
        agent_id="test-outcome-resolve-agent",
        project_key=project_key,
        content="deployment strategy uses blue green rollback",
        importance=1.0,
    )
    assert m is not None, "Memory.safe_save must succeed for the test fixture"
    return m.memory_id


def _cleanup_memory(memory_id: str) -> None:
    from models.memory import Memory

    for m in Memory.query.filter(memory_id=memory_id):
        m.delete()


@pytest.fixture
def large_ttl(monkeypatch):
    """Ensure INJECTION_RESOLVE_TTL used by the reflection is a small, known value
    so 'stale' fixtures (aged 2x the TTL) reliably qualify without depending on
    the real production default."""
    import config.memory_defaults as defaults

    monkeypatch.setattr(defaults, "INJECTION_RESOLVE_TTL", 60)
    return 60


class TestOrphanedSidecarSweep:
    @pytest.mark.asyncio
    async def test_stale_sidecar_resolves_injections_to_deferred(self, sidecar_root, large_ttl):
        """Simulated session death: a stale sidecar's injections resolve to
        'deferred' and the sidecar is cleaned up.

        Asserts observable state (NOT merely 'no crash') on two axes:
          - the record's `last_outcome` metadata is set to 'deferred'
            (`_persist_outcome_metadata` ran), and
          - a pending RecallProposal keyed by the record's REDIS KEY is
            resolved by `on_context_used` -- proving the reflection fed the
            outcome map keyed by redis_key (not memory_id), the exact mis-key
            the plan warns against (an unmatched instance would silently
            default to 'deferred' and mask the bug).
        """
        from popoto import ObservationProtocol, RecallProposal

        from models.memory import Memory
        from reflections.memory.memory_outcome_resolve import run

        memory_id = _seed_memory()
        try:
            mem = Memory.query.filter(memory_id=memory_id)[0]
            redis_key = mem.db_key.redis_key

            # Surface the memory so a pending RecallProposal exists keyed by
            # redis_key; the sweep's on_context_used("deferred") must resolve it.
            ObservationProtocol.on_surfaced([mem])
            pending_before = {k for k, _ in RecallProposal.get_pending(Memory)}
            assert redis_key in pending_before, "fixture must create a pending proposal"

            sidecar_path = _write_stale_sidecar(
                sidecar_root,
                "crashed-session-1",
                [{"memory_id": memory_id, "content": "deployment strategy uses blue green"}],
                age_seconds=large_ttl * 3,
            )

            result = await run()

            assert result["status"] == "ok"
            assert result["resolved"] == 1
            assert not sidecar_path.exists(), "stale sidecar must be cleaned up"

            refreshed = Memory.query.filter(memory_id=memory_id)[0]
            assert refreshed.metadata.get("last_outcome") == "deferred"

            pending_after = {k for k, _ in RecallProposal.get_pending(Memory)}
            assert redis_key not in pending_after, (
                "proposal must be resolved by on_context_used keyed by redis_key"
            )
        finally:
            _cleanup_memory(memory_id)

    @pytest.mark.asyncio
    async def test_fresh_sidecar_is_not_swept(self, sidecar_root, large_ttl):
        """A live session's sidecar (mtime under TTL) must not be resolved --
        it is refreshed by every recall injection, so early resolution would
        risk a premature/duplicate outcome."""
        from reflections.memory.memory_outcome_resolve import run

        memory_id = _seed_memory()
        try:
            sidecar_path = _write_stale_sidecar(
                sidecar_root,
                "live-session-1",
                [{"memory_id": memory_id, "content": "deployment strategy uses blue green"}],
                age_seconds=1,
            )

            await run()

            assert sidecar_path.exists(), "fresh sidecar must not be swept"

            from models.memory import Memory

            refreshed = Memory.query.filter(memory_id=memory_id)[0]
            assert refreshed.metadata.get("last_outcome") != "deferred"
        finally:
            _cleanup_memory(memory_id)

    @pytest.mark.asyncio
    async def test_empty_sidecar_dir_is_noop(self, sidecar_root, large_ttl):
        """No sidecars at all -> no-op, zero count, no error."""
        from reflections.memory.memory_outcome_resolve import run

        result = await run()

        assert result["status"] == "ok"
        assert "0 sidecars swept" in result["summary"]

    @pytest.mark.asyncio
    async def test_empty_injected_list_is_noop_cleanup(self, sidecar_root, large_ttl):
        """A stale sidecar with injected: [] has nothing to resolve, but is
        still cleaned up (no dangling empty sidecar files)."""
        from reflections.memory.memory_outcome_resolve import run

        sidecar_path = _write_stale_sidecar(
            sidecar_root, "empty-injected-session", [], age_seconds=large_ttl * 3
        )

        result = await run()

        assert result["status"] == "ok"
        assert not sidecar_path.exists()
        assert "0 memories resolved" in result["summary"]

    @pytest.mark.asyncio
    async def test_malformed_sidecar_quarantined_and_counted(self, sidecar_root, large_ttl):
        """A crashed-mid-write / malformed sidecar (invalid JSON) must not abort
        the sweep. It is QUARANTINED to a `.corrupt` sibling (never re-scanned,
        never retried unbounded) and the outcome_resolve_malformed_count counter
        is incremented (ADVISORY 3)."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        from config.memory_defaults import DEFAULT_PROJECT_KEY
        from reflections.memory.memory_outcome_resolve import run

        session_dir = sidecar_root / "malformed-session"
        session_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = session_dir / "memory_buffer.json"
        # Partial / crashed-mid-write content: not valid JSON.
        sidecar_path.write_text('{"count": 0, "injected": [')
        old = _time.time() - large_ttl * 3
        os.utime(sidecar_path, (old, old))

        counter_key = f"{DEFAULT_PROJECT_KEY}:memory-gate:outcome_resolve_malformed_count"
        before = int(_R.get(counter_key) or 0)

        result = await run()

        assert result["status"] == "ok"
        assert result["malformed"] == 1
        # Quarantined, not silently deleted: original gone, `.corrupt` present.
        assert not sidecar_path.exists()
        assert (session_dir / "memory_buffer.json.corrupt").exists()
        after = int(_R.get(counter_key) or 0)
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_malformed_sidecar_not_retried_on_next_sweep(self, sidecar_root, large_ttl):
        """The quarantined `.corrupt` file must be inert -- a second sweep finds
        nothing to do (bounds unbounded retry of a poison sidecar)."""
        from reflections.memory.memory_outcome_resolve import run

        session_dir = sidecar_root / "malformed-session-2"
        session_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = session_dir / "memory_buffer.json"
        sidecar_path.write_text("{not valid json at all")
        old = _time.time() - large_ttl * 3
        os.utime(sidecar_path, (old, old))

        first = await run()
        second = await run()

        assert first["malformed"] == 1
        assert second["malformed"] == 0
        assert "0 sidecars swept" in second["summary"]

    @pytest.mark.asyncio
    async def test_compare_and_delete_leaves_resumed_sidecar(
        self, sidecar_root, large_ttl, monkeypatch
    ):
        """CONCERN 2: if the sidecar's mtime is bumped between read and cleanup
        (a resumed session rewrote it via recall()), the sweep must LEAVE the
        sidecar in place -- blindly unlinking would destroy the resumed
        session's fresh injections."""
        from reflections.memory import memory_outcome_resolve

        memory_id = _seed_memory()
        try:
            sidecar_path = _write_stale_sidecar(
                sidecar_root,
                "resumed-session",
                [{"memory_id": memory_id, "content": "deployment strategy uses blue green"}],
                age_seconds=large_ttl * 3,
            )

            real_resolve = memory_outcome_resolve._resolve_injections

            def _resolve_and_bump(injected):
                # Simulate a resumed session rewriting the sidecar (mtime moves
                # forward) in the window between the sweep's read and its cleanup.
                bumped = _time.time()
                os.utime(sidecar_path, (bumped, bumped))
                return real_resolve(injected)

            monkeypatch.setattr(memory_outcome_resolve, "_resolve_injections", _resolve_and_bump)

            result = await memory_outcome_resolve.run()

            assert result["status"] == "ok"
            assert sidecar_path.exists(), "resumed (mtime-bumped) sidecar must be left intact"
            assert result["swept"] == 0
        finally:
            _cleanup_memory(memory_id)

    @pytest.mark.asyncio
    async def test_sweep_is_idempotent(self, sidecar_root, large_ttl):
        """Running the sweep twice in a row is safe: the second run finds
        nothing (the sidecar was already cleaned up by the first run)."""
        from reflections.memory.memory_outcome_resolve import run

        memory_id = _seed_memory()
        try:
            _write_stale_sidecar(
                sidecar_root,
                "crashed-session-2",
                [{"memory_id": memory_id, "content": "deployment strategy uses blue green"}],
                age_seconds=large_ttl * 3,
            )

            first = await run()
            second = await run()

            assert first["status"] == "ok"
            assert second["status"] == "ok"
            assert "0 sidecars swept" in second["summary"]
        finally:
            _cleanup_memory(memory_id)


class TestOutcomeResolveCounter:
    @pytest.mark.asyncio
    async def test_resolve_increments_gate_counter(self, sidecar_root, large_ttl):
        """Each resolved memory increments outcome_resolve_count for its
        project_key, mirroring the prune_count/dedup_merge_count pattern."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        from reflections.memory.memory_outcome_resolve import run

        project_key = "test-outcome-resolve-counter"
        memory_id = _seed_memory(project_key=project_key)
        counter_key = f"{project_key}:memory-gate:outcome_resolve_count"
        try:
            before = int(_R.get(counter_key) or 0)
            _write_stale_sidecar(
                sidecar_root,
                "crashed-session-counter",
                [{"memory_id": memory_id, "content": "deployment strategy uses blue green"}],
                age_seconds=large_ttl * 3,
            )

            await run()

            after = int(_R.get(counter_key) or 0)
            assert after == before + 1
        finally:
            _cleanup_memory(memory_id)


class TestImportable:
    def test_wired_through_memory_management(self):
        """The sweep is importable via reflections.memory_management, matching
        the other memory reflections' wiring convention."""
        from reflections.memory_management import run_memory_outcome_resolve

        assert callable(run_memory_outcome_resolve)

    @pytest.mark.asyncio
    async def test_callable_as_zero_arg_run(self, sidecar_root, large_ttl):
        """run() must be callable with zero arguments (the reflection scheduler
        calls no-params callables zero-arg)."""
        from reflections.memory.memory_outcome_resolve import run

        result = await run()
        assert isinstance(result, dict)
        assert "status" in result
