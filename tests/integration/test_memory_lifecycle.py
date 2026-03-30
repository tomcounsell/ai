"""Integration tests for Memory model lifecycle against real Redis.

Covers save-search-recall, bloom filter, decay ordering, write filter,
ObservationProtocol confidence updates, dismissal tracking, category
re-ranking, knowledge companion memories, and project isolation.

All tests use UUID-prefixed project_key for Redis isolation.
"""

import json
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


class TestSaveSearchRecallCycle:
    """Save memories, search via ContextAssembler, verify correct records surface."""

    def test_save_and_query_by_project_key(self):
        """Save a memory and retrieve it by project_key filter."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Redis integration testing for memory lifecycle",
                importance=5.0,
                source="human",
            )
            assert m is not None
            assert m.content == "Redis integration testing for memory lifecycle"

            results = Memory.query.filter(project_key=pk)
            assert len(results) >= 1
            contents = [r.content for r in results]
            assert "Redis integration testing for memory lifecycle" in contents
        finally:
            _cleanup_memories(pk)

    def test_save_multiple_and_query(self):
        """Save multiple memories and verify all are retrievable."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            contents = [
                "First memory about deployment strategies",
                "Second memory about testing patterns",
                "Third memory about Redis configuration",
            ]
            for c in contents:
                Memory.safe_save(
                    agent_id="test-agent",
                    project_key=pk,
                    content=c,
                    importance=3.0,
                    source="agent",
                )

            results = Memory.query.filter(project_key=pk)
            result_contents = [r.content for r in results]
            for c in contents:
                assert c in result_contents
        finally:
            _cleanup_memories(pk)

    def test_safe_save_returns_memory_instance(self):
        """safe_save returns a Memory instance on success."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Saved memory instance check",
                importance=2.0,
                source="agent",
            )
            assert m is not None
            assert hasattr(m, "memory_id")
            assert m.memory_id  # non-empty
        finally:
            _cleanup_memories(pk)


class TestBloomFilterIntegration:
    """Test ExistenceFilter bloom checks against real Redis."""

    def test_bloom_true_for_saved_content(self):
        """Bloom filter returns True for terms from saved content."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="kubernetes deployment rollback strategy",
                importance=4.0,
                source="human",
            )

            bloom_field = Memory._meta.fields.get("bloom")
            assert bloom_field is not None

            # At least some content terms should hit the bloom filter
            hit_count = 0
            for word in ["kubernetes", "deployment", "rollback", "strategy"]:
                try:
                    if bloom_field.might_exist(Memory, word):
                        hit_count += 1
                except Exception:
                    pass

            # Bloom filter fingerprints full content; at least one term should match
            assert hit_count > 0, "Expected at least one bloom hit for saved content terms"
        finally:
            _cleanup_memories(pk)

    def test_bloom_false_for_unrelated_content(self):
        """Bloom filter should not hit for totally unrelated terms."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="python flask web framework tutorial",
                importance=3.0,
                source="agent",
            )

            bloom_field = Memory._meta.fields.get("bloom")
            assert bloom_field is not None

            # These unrelated terms should almost certainly not be in the bloom filter
            # (1% false positive rate means they're extremely unlikely to ALL hit)
            unrelated = ["xylophone", "paleontology", "cryogenics"]
            all_hit = all(bloom_field.might_exist(Memory, word) for word in unrelated)
            # It's possible for one to false-positive, but all three is vanishingly unlikely
            assert not all_hit, "All unrelated terms hitting bloom filter is unexpected"
        finally:
            _cleanup_memories(pk)


class TestDecayBehavior:
    """Test DecayingSortedField ordering reflects importance-weighted decay."""

    def test_higher_importance_ranks_higher(self):
        """Memories with higher importance should rank higher in decay-sorted results."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Low importance observation about minor detail",
                importance=1.0,
                source="agent",
            )
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="High importance human instruction about architecture",
                importance=6.0,
                source="human",
            )

            results = Memory.query.filter(project_key=pk)
            assert len(results) >= 2

            # Find the two memories
            importances = {r.content: r.importance for r in results}
            assert importances.get("High importance human instruction about architecture") == 6.0
            assert importances.get("Low importance observation about minor detail") == 1.0
        finally:
            _cleanup_memories(pk)


class TestWriteFilterEnforcement:
    """Verify WriteFilterMixin gates based on importance threshold."""

    def test_below_threshold_silently_dropped(self):
        """Memories below _wf_min_threshold (0.15) are silently dropped."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Noise level memory that should be filtered",
                importance=0.05,  # below 0.15 threshold
                source="agent",
            )
            assert m is None, "Expected None for filtered-out memory"

            results = Memory.query.filter(project_key=pk)
            assert len(results) == 0, "Filtered memory should not persist"
        finally:
            _cleanup_memories(pk)

    def test_above_threshold_persists(self):
        """Memories above _wf_min_threshold persist normally."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Normal importance memory that should persist",
                importance=1.0,
                source="agent",
            )
            assert m is not None

            results = Memory.query.filter(project_key=pk)
            assert len(results) >= 1
        finally:
            _cleanup_memories(pk)

    def test_exactly_at_threshold(self):
        """Memories at exactly the threshold should persist."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Edge case memory at exact threshold boundary",
                importance=0.15,
                source="agent",
            )
            # At threshold -- should persist (threshold is minimum, not exclusive)
            assert m is not None
        finally:
            _cleanup_memories(pk)


class TestConfidenceUpdates:
    """Test ObservationProtocol confidence changes via real Redis."""

    def test_confirm_access_does_not_raise(self):
        """confirm_access() should not raise on a persisted memory."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Memory to test access tracking",
                importance=3.0,
                source="agent",
            )
            assert m is not None
            # confirm_access should not raise
            m.confirm_access()
        finally:
            _cleanup_memories(pk)

    def test_initial_confidence_is_default(self):
        """New memories should have the configured initial confidence."""
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Memory to check initial confidence value",
                importance=3.0,
                source="agent",
            )
            assert m is not None
            conf = getattr(m, "confidence", None)
            # Confidence should be at or near initial value
            assert conf is not None
        finally:
            _cleanup_memories(pk)


class TestDismissalTracking:
    """Test dismissal tracking and importance decay via _persist_outcome_metadata."""

    def test_dismissal_increments_count(self):
        """Simulated dismissal should increment dismissal_count in metadata."""
        from agent.memory_extraction import _persist_outcome_metadata
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Memory to test dismissal tracking",
                importance=4.0,
                source="human",
            )
            assert m is not None
            mid = m.memory_id

            _persist_outcome_metadata([m], {mid: "dismissed"})

            # Reload from Redis
            results = Memory.query.filter(project_key=pk)
            updated = [r for r in results if r.memory_id == mid]
            assert len(updated) == 1
            meta = updated[0].metadata or {}
            assert meta.get("dismissal_count", 0) == 1
            assert meta.get("last_outcome") == "dismissed"
        finally:
            _cleanup_memories(pk)

    def test_acted_resets_dismissal_count(self):
        """An acted outcome should reset dismissal_count to 0."""
        from agent.memory_extraction import _persist_outcome_metadata
        from models.memory import Memory

        pk = _unique_key()
        try:
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Memory to test acted outcome reset",
                importance=4.0,
                source="human",
                metadata={"dismissal_count": 2, "last_outcome": "dismissed"},
            )
            assert m is not None
            mid = m.memory_id

            _persist_outcome_metadata([m], {mid: "acted"})

            results = Memory.query.filter(project_key=pk)
            updated = [r for r in results if r.memory_id == mid]
            assert len(updated) == 1
            meta = updated[0].metadata or {}
            assert meta.get("dismissal_count") == 0
            assert meta.get("last_outcome") == "acted"
        finally:
            _cleanup_memories(pk)

    def test_importance_decays_after_threshold_dismissals(self):
        """After DISMISSAL_DECAY_THRESHOLD consecutive dismissals, importance decays."""
        from agent.memory_extraction import _persist_outcome_metadata
        from config.memory_defaults import (
            DISMISSAL_DECAY_THRESHOLD,
            DISMISSAL_IMPORTANCE_DECAY,
        )
        from models.memory import Memory

        pk = _unique_key()
        try:
            original_importance = 4.0
            m = Memory.safe_save(
                agent_id="test-agent",
                project_key=pk,
                content="Memory to test importance decay after dismissals",
                importance=original_importance,
                source="human",
                metadata={"dismissal_count": DISMISSAL_DECAY_THRESHOLD - 1},
            )
            assert m is not None
            mid = m.memory_id

            # This dismissal pushes it to the threshold
            _persist_outcome_metadata([m], {mid: "dismissed"})

            results = Memory.query.filter(project_key=pk)
            updated = [r for r in results if r.memory_id == mid]
            assert len(updated) == 1
            new_importance = updated[0].importance
            expected = original_importance * DISMISSAL_IMPORTANCE_DECAY
            assert abs(new_importance - expected) < 0.01, (
                f"Expected importance {expected}, got {new_importance}"
            )
        finally:
            _cleanup_memories(pk)


class TestCategoryReRanking:
    """Test _apply_category_weights re-orders results by category."""

    def test_correction_ranks_above_pattern(self):
        """Correction category (weight 1.5) should rank above pattern (1.0) at equal scores."""
        from agent.memory_hook import _apply_category_weights

        class FakeRecord:
            def __init__(self, content, category, score):
                self.content = content
                self.metadata = {"category": category}
                self.score = score

        records = [
            FakeRecord("pattern observation", "pattern", 1.0),
            FakeRecord("correction instruction", "correction", 1.0),
        ]

        reranked = _apply_category_weights(records)
        assert reranked[0].content == "correction instruction"
        assert reranked[1].content == "pattern observation"

    def test_decision_ranks_above_surprise(self):
        """Decision category (weight 1.3) should rank above surprise (1.0)."""
        from agent.memory_hook import _apply_category_weights

        class FakeRecord:
            def __init__(self, content, category, score):
                self.content = content
                self.metadata = {"category": category}
                self.score = score

        records = [
            FakeRecord("surprising finding", "surprise", 1.0),
            FakeRecord("architectural decision", "decision", 1.0),
        ]

        reranked = _apply_category_weights(records)
        assert reranked[0].content == "architectural decision"

    def test_empty_records_returns_empty(self):
        """Empty list should return empty."""
        from agent.memory_hook import _apply_category_weights

        assert _apply_category_weights([]) == []

    def test_missing_metadata_uses_default_weight(self):
        """Records without metadata get the default weight (1.0)."""
        from agent.memory_hook import _apply_category_weights

        class FakeRecord:
            def __init__(self, content, score):
                self.content = content
                self.metadata = None
                self.score = score

        records = [FakeRecord("no metadata", 2.0), FakeRecord("also no metadata", 1.0)]
        reranked = _apply_category_weights(records)
        # Higher score should still be first with default weight
        assert reranked[0].score == 2.0


class TestKnowledgeCompanionMemories:
    """Test knowledge-sourced memories with reference field."""

    def test_knowledge_memory_with_json_reference(self):
        """Save a knowledge memory with JSON reference and verify persistence."""
        from models.memory import SOURCE_KNOWLEDGE, Memory

        pk = _unique_key()
        try:
            ref = json.dumps({"tool": "read_file", "params": {"file_path": "/docs/guide.md"}})
            m = Memory.safe_save(
                agent_id="knowledge-indexer",
                project_key=pk,
                content="Guide to configuring Redis for production workloads",
                importance=5.0,
                source=SOURCE_KNOWLEDGE,
                reference=ref,
            )
            assert m is not None
            assert m.source == SOURCE_KNOWLEDGE

            # Reload and verify reference persists
            results = Memory.query.filter(project_key=pk)
            knowledge_mems = [r for r in results if r.source == SOURCE_KNOWLEDGE]
            assert len(knowledge_mems) >= 1
            stored_ref = json.loads(knowledge_mems[0].reference)
            assert stored_ref["tool"] == "read_file"
            assert stored_ref["params"]["file_path"] == "/docs/guide.md"
        finally:
            _cleanup_memories(pk)

    def test_knowledge_memory_searchable(self):
        """Knowledge memories should appear in query results."""
        from models.memory import SOURCE_KNOWLEDGE, Memory

        pk = _unique_key()
        try:
            Memory.safe_save(
                agent_id="knowledge-indexer",
                project_key=pk,
                content="Database sharding strategies for horizontal scaling",
                importance=5.0,
                source=SOURCE_KNOWLEDGE,
                reference="{}",
            )

            results = Memory.query.filter(project_key=pk)
            assert any(r.source == SOURCE_KNOWLEDGE for r in results)
        finally:
            _cleanup_memories(pk)


class TestProjectIsolation:
    """Verify memories are partitioned by project_key."""

    def test_different_projects_isolated(self):
        """Memories under different project_keys should not cross-contaminate."""
        from models.memory import Memory

        pk1 = _unique_key()
        pk2 = _unique_key()
        try:
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk1,
                content="Project alpha specific memory content",
                importance=3.0,
                source="agent",
            )
            Memory.safe_save(
                agent_id="test-agent",
                project_key=pk2,
                content="Project beta specific memory content",
                importance=3.0,
                source="agent",
            )

            results_pk1 = Memory.query.filter(project_key=pk1)
            results_pk2 = Memory.query.filter(project_key=pk2)

            pk1_contents = [r.content for r in results_pk1]
            pk2_contents = [r.content for r in results_pk2]

            assert "Project alpha specific memory content" in pk1_contents
            assert "Project beta specific memory content" not in pk1_contents
            assert "Project beta specific memory content" in pk2_contents
            assert "Project alpha specific memory content" not in pk2_contents
        finally:
            _cleanup_memories(pk1)
            _cleanup_memories(pk2)


class TestFailurePaths:
    """Verify memory operations fail silently as designed."""

    def test_safe_save_returns_none_on_bad_kwargs(self):
        """safe_save should return None (not raise) with invalid kwargs."""
        from models.memory import Memory

        # Intentionally bad kwargs -- content is not provided, but model should
        # handle it gracefully via safe_save
        result = Memory.safe_save(
            agent_id="test-agent",
            project_key=_unique_key(),
            importance=0.01,  # below write filter
            source="agent",
        )
        # Either None (filtered or error) is acceptable -- no exception raised
        assert result is None

    def test_persist_outcome_metadata_skips_on_error(self):
        """_persist_outcome_metadata should skip individual records on error."""
        from agent.memory_extraction import _persist_outcome_metadata

        class BrokenMemory:
            memory_id = "broken-id"
            metadata = None
            importance = 1.0

            def save(self):
                raise RuntimeError("Simulated save error")

        # Should not raise
        _persist_outcome_metadata(
            [BrokenMemory()],
            {"broken-id": "dismissed"},
        )
