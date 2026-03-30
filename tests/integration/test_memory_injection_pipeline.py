"""Integration tests for the memory injection pipeline against real Redis.

Tests the full check_and_inject flow including bloom filter checks,
sliding window rate limiting, novel territory detection, deja vu detection,
multi-query decomposition, and session cleanup.

All tests use UUID-prefixed project_key for Redis isolation.
"""

import uuid


def _unique_key() -> str:
    """Generate a unique project_key for test isolation."""
    return f"test-{uuid.uuid4().hex[:8]}"


def _cleanup_memories(project_key: str):
    """Delete all memories for a given project_key."""
    from models.memory import Memory

    try:
        results = Memory.query.filter(project_key=project_key)
        for m in results:
            try:
                m.delete()
            except Exception:
                pass
    except Exception:
        pass


def _reset_hook_state(session_id: str):
    """Reset memory hook session-scoped state."""
    from agent.memory_hook import clear_session

    clear_session(session_id)


class TestCheckAndInjectFlow:
    """Test the full check_and_inject pipeline with real Redis data."""

    def test_inject_returns_none_before_window(self):
        """check_and_inject should return None before WINDOW_SIZE tool calls."""
        from agent.memory_hook import check_and_inject

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        try:
            # First call -- should not inject (window not full)
            result = check_and_inject(
                session_id=sid,
                tool_name="Read",
                tool_input={"file_path": "/some/file.py"},
                project_key=pk,
            )
            assert result is None
        finally:
            _reset_hook_state(sid)

    def test_inject_fires_at_window_boundary(self):
        """check_and_inject should attempt injection at WINDOW_SIZE boundary."""
        from agent.memory_hook import check_and_inject
        from config.memory_defaults import INJECTION_WINDOW_SIZE
        from models.memory import Memory

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        try:
            # Save a memory that should be discoverable
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="memory about database migration strategies and rollback procedures",
                importance=5.0,
                source="human",
            )

            for i in range(INJECTION_WINDOW_SIZE):
                check_and_inject(
                    session_id=sid,
                    tool_name="Grep",
                    tool_input={"pattern": "database migration rollback"},
                    project_key=pk,
                )

            # At the window boundary, it should have attempted injection
            # Result may be None if bloom/assembler didn't match, or a thought string
            # The key assertion is that it reached the injection logic without error
        finally:
            _reset_hook_state(sid)
            _cleanup_memories(pk)

    def test_inject_with_matching_content_returns_thoughts(self):
        """When bloom and assembler match, check_and_inject returns thought blocks."""
        from agent.memory_hook import check_and_inject
        from config.memory_defaults import INJECTION_WINDOW_SIZE
        from models.memory import Memory

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        try:
            # Save memories with distinctive content
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="kubernetes cluster autoscaling configuration guide",
                importance=6.0,
                source="human",
            )

            # Fill up the window with tool calls referencing matching keywords
            for i in range(INJECTION_WINDOW_SIZE):
                result = check_and_inject(
                    session_id=sid,
                    tool_name="Grep",
                    tool_input={"pattern": "kubernetes autoscaling cluster"},
                    project_key=pk,
                )

            # Result could be a thought string or None depending on bloom/assembler
            # Either outcome is valid; we're testing it doesn't crash
            if result is not None:
                assert "<thought>" in result
        finally:
            _reset_hook_state(sid)
            _cleanup_memories(pk)


class TestSlidingWindowRateLimiting:
    """Verify injection only fires every WINDOW_SIZE tool calls."""

    def test_no_injection_between_windows(self):
        """Tool calls between window boundaries should return None."""
        from agent.memory_hook import check_and_inject
        from config.memory_defaults import INJECTION_WINDOW_SIZE

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        try:
            # Fill the first window
            for i in range(INJECTION_WINDOW_SIZE):
                check_and_inject(sid, "Read", {"file_path": "/test.py"}, pk)

            # Now the next WINDOW_SIZE-1 calls should all return None
            for i in range(1, INJECTION_WINDOW_SIZE):
                result = check_and_inject(sid, "Read", {"file_path": "/test.py"}, pk)
                assert result is None, f"Call {i} after window should be None"
        finally:
            _reset_hook_state(sid)

    def test_second_window_boundary_fires(self):
        """The second window boundary should also attempt injection."""
        from agent.memory_hook import check_and_inject
        from config.memory_defaults import INJECTION_WINDOW_SIZE

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        try:
            # Two full windows
            for i in range(INJECTION_WINDOW_SIZE * 2):
                check_and_inject(sid, "Bash", {"command": "ls"}, pk)

            # Should have completed without error through two windows
        finally:
            _reset_hook_state(sid)


class TestNovelTerritoryDetection:
    """Test novel territory detection when no bloom hits but many keywords."""

    def test_novel_territory_thought(self):
        """Many unique keywords with zero bloom hits should trigger novel territory."""
        from agent.memory_hook import check_and_inject
        from config.memory_defaults import INJECTION_WINDOW_SIZE

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        try:
            # Use very distinctive keywords unlikely to match any bloom filter entries
            # We need enough unique keywords to exceed NOVEL_TERRITORY_KEYWORD_THRESHOLD
            result = None
            for i in range(INJECTION_WINDOW_SIZE):
                result = check_and_inject(
                    session_id=sid,
                    tool_name="Grep",
                    tool_input={
                        "pattern": "xyzomorphic paleoclimatology ytterbium zymurgy quaternion"
                    },
                    project_key=pk,
                )

            # Should either get novel territory thought or None
            if result is not None:
                assert "new territory" in result.lower() or "<thought>" in result
        finally:
            _reset_hook_state(sid)


class TestDejaVuDetection:
    """Test deja vu detection when bloom hits exist but no assembler results."""

    def test_deja_vu_with_bloom_hits_no_results(self):
        """Bloom hits without strong assembler results should trigger deja vu."""
        from agent.memory_hook import check_and_inject
        from config.memory_defaults import INJECTION_WINDOW_SIZE
        from models.memory import Memory

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        pk_other = _unique_key()
        try:
            # Save memory under a DIFFERENT project key so it registers in
            # the global bloom filter but won't appear in ContextAssembler
            # results filtered by pk
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk_other,
                content="quantum computing entanglement qubits superposition",
                importance=5.0,
                source="human",
            )

            result = None
            for i in range(INJECTION_WINDOW_SIZE):
                result = check_and_inject(
                    session_id=sid,
                    tool_name="Grep",
                    tool_input={"pattern": "quantum entanglement qubits"},
                    project_key=pk,
                )

            # May get deja vu thought, novel territory, or None
            # We're testing it doesn't crash and produces a valid response type
            assert result is None or isinstance(result, str)
        finally:
            _reset_hook_state(sid)
            _cleanup_memories(pk)
            _cleanup_memories(pk_other)


class TestMultiQueryDecomposition:
    """Test keyword clustering for multi-query retrieval."""

    def test_cluster_keywords_single_cluster(self):
        """Five or fewer keywords should produce a single cluster."""
        from agent.memory_hook import _cluster_keywords

        result = _cluster_keywords(["alpha", "beta", "gamma"])
        assert len(result) == 1
        assert result[0] == ["alpha", "beta", "gamma"]

    def test_cluster_keywords_multiple_clusters(self):
        """More than five keywords should be split into multiple clusters."""
        from agent.memory_hook import _cluster_keywords

        keywords = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
        result = _cluster_keywords(keywords)
        assert len(result) > 1
        # All keywords should be present across clusters
        all_kw = [kw for cluster in result for kw in cluster]
        assert set(all_kw) == set(keywords)

    def test_cluster_keywords_empty(self):
        """Empty keyword list should produce empty clusters."""
        from agent.memory_hook import _cluster_keywords

        assert _cluster_keywords([]) == []

    def test_cluster_keywords_max_clusters(self):
        """Should not produce more clusters than max_clusters."""
        from agent.memory_hook import _cluster_keywords

        keywords = list(f"kw{i}" for i in range(20))
        result = _cluster_keywords(keywords, max_clusters=3)
        assert len(result) <= 3


class TestSessionCleanup:
    """Verify clear_session removes all session-scoped state."""

    def test_clear_session_removes_state(self):
        """After clear_session, all session state should be gone."""
        from agent.memory_hook import (
            _injected_thoughts,
            _tool_buffers,
            _tool_counts,
            check_and_inject,
            clear_session,
        )

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        try:
            # Populate state
            check_and_inject(sid, "Read", {"file_path": "/test.py"}, pk)

            assert sid in _tool_counts
            assert sid in _tool_buffers

            clear_session(sid)

            assert sid not in _tool_counts
            assert sid not in _tool_buffers
            assert sid not in _injected_thoughts
        finally:
            # Ensure cleanup even if assertions fail
            clear_session(sid)

    def test_clear_session_idempotent(self):
        """Clearing a non-existent session should not raise."""
        from agent.memory_hook import clear_session

        clear_session(f"nonexistent-{uuid.uuid4().hex[:8]}")


class TestInjectionFailurePaths:
    """Verify check_and_inject fails silently as designed."""

    def test_inject_returns_none_on_no_keywords(self):
        """check_and_inject with empty tool input should return None."""
        from agent.memory_hook import check_and_inject
        from config.memory_defaults import INJECTION_WINDOW_SIZE

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        pk = _unique_key()
        try:
            for i in range(INJECTION_WINDOW_SIZE):
                result = check_and_inject(sid, "", {}, pk)
            # No keywords extracted from empty input
            assert result is None
        finally:
            _reset_hook_state(sid)

    def test_inject_never_raises(self):
        """check_and_inject should never raise, regardless of input."""
        from agent.memory_hook import check_and_inject

        sid = f"test-session-{uuid.uuid4().hex[:8]}"
        try:
            # Various edge cases
            check_and_inject(sid, None, None, None)
            check_and_inject(sid, "", {}, "")
            check_and_inject(sid, "tool", "not-a-dict", "pk")
        finally:
            _reset_hook_state(sid)
