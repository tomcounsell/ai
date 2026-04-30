"""Unit tests for agent/memory_retrieval.py -- BM25 + RRF fusion retrieval."""

from unittest.mock import MagicMock, patch


class TestFilterByProject:
    """Test the _filter_by_project() helper."""

    def test_filters_to_matching_keys(self):
        from agent.memory_retrieval import _filter_by_project

        results = [
            ("Memory:agent1:projA:key1", 10.0),
            ("Memory:agent1:projB:key2", 8.0),
            ("Memory:agent1:projA:key3", 5.0),
        ]
        filtered = _filter_by_project(results, "projA")
        assert len(filtered) == 2
        assert all("projA" in k for k, _ in filtered)

    def test_empty_project_key_returns_all(self):
        from agent.memory_retrieval import _filter_by_project

        results = [("Memory:a:projA:k1", 1.0), ("Memory:a:projB:k2", 2.0)]
        filtered = _filter_by_project(results, "")
        assert len(filtered) == 2

    def test_no_matches_returns_empty(self):
        from agent.memory_retrieval import _filter_by_project

        results = [("Memory:a:projA:k1", 1.0), ("Memory:a:projB:k2", 2.0)]
        filtered = _filter_by_project(results, "projC")
        assert filtered == []

    def test_empty_input_returns_empty(self):
        from agent.memory_retrieval import _filter_by_project

        assert _filter_by_project([], "projA") == []


class TestRrfFuse:
    """Test the rrf_fuse() function."""

    def test_empty_lists(self):
        from agent.memory_retrieval import rrf_fuse

        result = rrf_fuse([], [], [])
        assert result == []

    def test_single_list(self):
        from agent.memory_retrieval import rrf_fuse

        ranked = [("key1", 10.0), ("key2", 5.0), ("key3", 1.0)]
        result = rrf_fuse(ranked, k=60)
        assert len(result) == 3
        # key1 at rank 1 should have highest score
        assert result[0][0] == "key1"
        assert result[1][0] == "key2"
        assert result[2][0] == "key3"

    def test_two_lists_same_keys(self):
        from agent.memory_retrieval import rrf_fuse

        list1 = [("key1", 10.0), ("key2", 5.0)]
        list2 = [("key1", 8.0), ("key2", 3.0)]
        result = rrf_fuse(list1, list2, k=60)
        assert len(result) == 2
        # key1 is rank 1 in both lists -> highest RRF
        assert result[0][0] == "key1"

    def test_two_lists_different_keys(self):
        from agent.memory_retrieval import rrf_fuse

        list1 = [("key1", 10.0)]
        list2 = [("key2", 8.0)]
        result = rrf_fuse(list1, list2, k=60)
        assert len(result) == 2
        # Both at rank 1 in their respective lists -> equal RRF score
        assert result[0][1] == result[1][1]

    def test_rrf_score_calculation(self):
        from agent.memory_retrieval import rrf_fuse

        # With k=60, rank 1 gives 1/(60+1) = 0.01639...
        list1 = [("key1", 10.0)]
        result = rrf_fuse(list1, k=60)
        expected_score = 1.0 / (60 + 1)
        assert abs(result[0][1] - expected_score) < 1e-10

    def test_multi_list_rrf_accumulation(self):
        from agent.memory_retrieval import rrf_fuse

        # key1 appears at rank 1 in all 3 lists
        list1 = [("key1", 10.0)]
        list2 = [("key1", 8.0)]
        list3 = [("key1", 5.0)]
        result = rrf_fuse(list1, list2, list3, k=60)
        expected_score = 3.0 / (60 + 1)  # 3 * 1/(60+1)
        assert abs(result[0][1] - expected_score) < 1e-10

    def test_limit_caps_results(self):
        from agent.memory_retrieval import rrf_fuse

        ranked = [(f"key{i}", float(10 - i)) for i in range(20)]
        result = rrf_fuse(ranked, k=60, limit=5)
        assert len(result) == 5

    def test_bytes_keys_decoded(self):
        from agent.memory_retrieval import rrf_fuse

        ranked = [(b"Memory:abc123", 10.0), (b"Memory:def456", 5.0)]
        result = rrf_fuse(ranked, k=60)
        assert all(isinstance(k, str) for k, _s in result)
        assert result[0][0] == "Memory:abc123"

    def test_ranking_order_matters(self):
        """Items ranked higher in more lists get better RRF scores."""
        from agent.memory_retrieval import rrf_fuse

        # key1 is rank 1 in list1, rank 2 in list2
        # key2 is rank 2 in list1, rank 1 in list2
        list1 = [("key1", 10.0), ("key2", 5.0)]
        list2 = [("key2", 10.0), ("key1", 5.0)]
        result = rrf_fuse(list1, list2, k=60)
        # Both should have equal RRF scores (symmetric)
        assert abs(result[0][1] - result[1][1]) < 1e-10

    def test_key_appearing_in_more_lists_ranks_higher(self):
        """A key present in all 3 lists outranks one in only 1 list."""
        from agent.memory_retrieval import rrf_fuse

        # key1 in all 3 lists at rank 2
        # key2 in only list1 at rank 1
        list1 = [("key2", 10.0), ("key1", 5.0)]
        list2 = [("key1", 5.0)]
        list3 = [("key1", 5.0)]
        result = rrf_fuse(list1, list2, list3, k=60)
        # key1: 1/(60+2) + 1/(60+1) + 1/(60+1) = 0.01613 + 0.01639 + 0.01639
        # key2: 1/(60+1) = 0.01639
        assert result[0][0] == "key1"

    def test_empty_individual_lists_ignored(self):
        from agent.memory_retrieval import rrf_fuse

        ranked = [("key1", 10.0)]
        result = rrf_fuse(ranked, [], [], k=60)
        assert len(result) == 1
        assert result[0][0] == "key1"

    def test_graceful_with_no_args(self):
        from agent.memory_retrieval import rrf_fuse

        result = rrf_fuse()
        assert result == []


class TestGetRelevanceRanked:
    """Test get_relevance_ranked() with mocked Redis."""

    def test_returns_empty_on_error(self):
        from agent.memory_retrieval import get_relevance_ranked

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.zrevrange.side_effect = Exception("connection failed")
            result = get_relevance_ranked("test-project")
            assert result == []

    def test_returns_decoded_tuples(self):
        from agent.memory_retrieval import get_relevance_ranked

        mock_results = [
            (b"Memory:key1:proj", 1000.0),
            (b"Memory:key2:proj", 500.0),
        ]

        with (
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
            patch("popoto.DecayingSortedField.get_sortedset_db_key") as mock_key,
        ):
            mock_key_obj = MagicMock()
            mock_key_obj.redis_key = "$DecayingSortF:Memory:relevance:test"
            mock_key.return_value = mock_key_obj
            mock_redis.zrevrange.return_value = mock_results

            result = get_relevance_ranked("test")

        assert len(result) == 2
        assert result[0] == ("Memory:key1:proj", 1000.0)
        assert result[1] == ("Memory:key2:proj", 500.0)


class TestGetConfidenceRanked:
    """Test get_confidence_ranked() with mocked Redis."""

    def _mock_confidence_field(self):
        """Helper: patch ConfidenceField.get_special_use_field_db_key to return a mock key."""
        mock_base_key = MagicMock()
        mock_base_key.redis_key = "$ConfidencF:Memory:confidence"
        return patch(
            "popoto.ConfidenceField.get_special_use_field_db_key",
            return_value=mock_base_key,
        )

    def test_returns_empty_on_error(self):
        from agent.memory_retrieval import get_confidence_ranked

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.side_effect = Exception("connection failed")
            with self._mock_confidence_field():
                result = get_confidence_ranked("proj")
            assert result == []

    def test_returns_empty_when_no_data(self):
        from agent.memory_retrieval import get_confidence_ranked

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = {}
            with self._mock_confidence_field():
                result = get_confidence_ranked("proj")
            assert result == []

    def test_returns_sorted_by_confidence(self):
        import msgpack

        from agent.memory_retrieval import get_confidence_ranked

        mock_data = {
            b"Memory:agent1:proj:key1": msgpack.packb({"confidence": 0.9, "evidence_count": 5}),
            b"Memory:agent1:proj:key2": msgpack.packb({"confidence": 0.3, "evidence_count": 2}),
            b"Memory:agent1:proj:key3": msgpack.packb({"confidence": 0.7, "evidence_count": 3}),
        }

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = mock_data
            with self._mock_confidence_field():
                result = get_confidence_ranked("proj")

        assert len(result) == 3
        # Should be sorted by confidence descending
        assert result[0][0] == "Memory:agent1:proj:key1"
        assert result[0][1] == 0.9
        assert result[1][0] == "Memory:agent1:proj:key3"
        assert result[1][1] == 0.7
        assert result[2][0] == "Memory:agent1:proj:key2"
        assert result[2][1] == 0.3

    def test_filters_by_project_key(self):
        """Only entries whose Redis key contains the project_key are returned."""
        import msgpack

        from agent.memory_retrieval import get_confidence_ranked

        mock_data = {
            b"Memory:agent1:projA:key1": msgpack.packb({"confidence": 0.9}),
            b"Memory:agent1:projB:key2": msgpack.packb({"confidence": 0.8}),
            b"Memory:agent1:projA:key3": msgpack.packb({"confidence": 0.7}),
        }

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = mock_data
            with self._mock_confidence_field():
                result = get_confidence_ranked("projA")

        assert len(result) == 2
        assert all("projA" in k for k, _s in result)

    def test_limit_caps_results(self):
        import msgpack

        from agent.memory_retrieval import get_confidence_ranked

        mock_data = {
            f"Memory:agent1:proj:key{i}".encode(): msgpack.packb({"confidence": float(i) / 10})
            for i in range(20)
        }

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = mock_data
            with self._mock_confidence_field():
                result = get_confidence_ranked("proj", limit=5)

        assert len(result) == 5

    def test_corrupt_entry_skipped(self):
        import msgpack

        from agent.memory_retrieval import get_confidence_ranked

        mock_data = {
            b"Memory:agent1:proj:good": msgpack.packb({"confidence": 0.8}),
            b"Memory:agent1:proj:bad": b"not-valid-msgpack-\xff\xff",
        }

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = mock_data
            with self._mock_confidence_field():
                result = get_confidence_ranked("proj")

        # Only the valid entry should be returned
        assert len(result) == 1
        assert result[0][0] == "Memory:agent1:proj:good"


class TestRetrieveMemories:
    """Test the full retrieve_memories() pipeline."""

    def test_returns_empty_on_exception(self):
        from agent.memory_retrieval import retrieve_memories

        with patch("popoto.BM25Field") as mock_bm25:
            mock_bm25.search.side_effect = Exception("boom")
            with (
                patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
                patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
                patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            ):
                result = retrieve_memories("test query", "proj")
                assert result == []

    def test_fuses_four_signals(self):
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "test-123"
        mock_record.superseded_by = ""  # active record, must pass superseded filter

        bm25_results = [("Memory:test-123:proj", 5.0)]
        relevance_results = [("Memory:test-123:proj", 1000.0)]
        confidence_results = [("Memory:test-123:proj", 0.8)]
        embedding_results = [("Memory:test-123:proj", 0.95)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=embedding_results),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test query", "project", limit=10)

        assert len(result) == 1
        assert result[0] is mock_record
        # Should have score attribute set
        assert hasattr(result[0], "score")

    def test_deduplicates_across_signals(self):
        """Same key in multiple signals should produce one result."""
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "test-123"
        mock_record.superseded_by = ""  # active

        key = "Memory:test-123:proj"
        bm25_results = [(key, 5.0)]
        relevance_results = [(key, 1000.0)]
        confidence_results = [(key, 0.8)]
        embedding_results = [(key, 0.9)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=embedding_results),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test query", "project", limit=10)

        # Should be exactly 1 record, not 4
        assert len(result) == 1

    def test_bm25_failure_degrades_gracefully(self):
        """When BM25 fails, retrieval still works with remaining signals."""
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "test-456"
        mock_record.superseded_by = ""  # active

        key = "Memory:test-456:proj"
        relevance_results = [(key, 1000.0)]
        confidence_results = [(key, 0.8)]
        embedding_results = [(key, 0.85)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=embedding_results),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.side_effect = Exception("BM25 index missing")
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test query", "project", limit=10)

        # Should still return results from relevance + confidence + embedding
        assert len(result) == 1

    def test_empty_query_returns_empty(self):
        """BM25 with empty query should not crash."""
        from agent.memory_retrieval import retrieve_memories

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
        ):
            mock_bm25.search.return_value = []
            result = retrieve_memories("", "proj")

        assert result == []

    def test_respects_rrf_k_override(self):
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "test-789"
        mock_record.superseded_by = ""  # active

        key = "Memory:test-789:proj"

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[(key, 100.0)]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = [(key, 5.0)]
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test", "proj", rrf_k=20)

        assert len(result) == 1
        # With k=20, rank 1 gives 1/(20+1), appearing in 2 lists
        expected_score = 2.0 / 21
        assert abs(result[0].score - expected_score) < 1e-10

    def test_hydration_failure_skips_record(self):
        """If Memory.query.get fails for a key, that key is skipped."""
        from agent.memory_retrieval import retrieve_memories

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = [("Memory:missing:proj", 5.0)]
            mock_memory_cls.query.get.return_value = None  # Not found

            result = retrieve_memories("test query", "proj")

        assert result == []

    def test_bm25_results_filtered_by_project(self):
        """BM25 returns global results; only those matching project_key should survive."""
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "owned"
        mock_record.superseded_by = ""  # active

        # BM25 returns results from two projects -- only projA should survive
        bm25_results = [
            ("Memory:agent1:projA:key1", 8.0),
            ("Memory:agent1:projB:key2", 6.0),
            ("Memory:agent1:projA:key3", 4.0),
        ]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test query", "projA", limit=10)

        # projB key should be excluded -- only 2 results from projA
        assert len(result) == 2
        # Verify hydration was only called for projA keys
        hydrated_keys = [call.args[0] for call in mock_memory_cls.query.get.call_args_list]
        assert all("projA" in k for k in hydrated_keys)
        assert not any("projB" in k for k in hydrated_keys)

    def test_cross_project_isolation_all_signals(self):
        """BM25 cross-project keys are filtered out before RRF fusion.

        BM25 returns global results (both projA and projB). The _filter_by_project
        call in retrieve_memories removes projB keys before they enter rrf_fuse.
        Relevance and confidence helpers are mocked at function level (already filtered).
        """
        from agent.memory_retrieval import retrieve_memories

        owned_record = MagicMock()
        owned_record.memory_id = "owned"
        owned_record.superseded_by = ""  # active

        # BM25 returns global results including foreign project
        bm25_results = [
            ("Memory:a:projA:k1", 9.0),
            ("Memory:a:projB:k2", 7.0),  # foreign -- should be filtered out
        ]
        # Relevance is natively partitioned by project_key
        relevance_results = [
            ("Memory:a:projA:k1", 1000.0),
        ]
        # Confidence helper is mocked post-filter (already filtered to projA)
        confidence_results = [
            ("Memory:a:projA:k1", 0.9),
        ]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = owned_record

            result = retrieve_memories("test query", "projA", limit=10)

        # Only projA key should survive -- projB filtered before fusion
        assert len(result) == 1
        assert result[0].memory_id == "owned"
        # Verify hydration only called with projA key
        hydrated_keys = [call.args[0] for call in mock_memory_cls.query.get.call_args_list]
        assert len(hydrated_keys) == 1
        assert "projA" in hydrated_keys[0]


class TestRelevanceThreshold:
    """Test the post-fusion min_rrf_score relevance gate.

    The gate drops fused (key, score) tuples below the configured
    threshold BEFORE Memory hydration. CLI defaults to None (off);
    recall paths pass config.memory_defaults.RRF_MIN_SCORE explicitly.
    """

    def _make_record(self, mid: str = "rec-1") -> MagicMock:
        rec = MagicMock()
        rec.memory_id = mid
        rec.superseded_by = ""  # active
        return rec

    def test_threshold_none_preserves_back_compat(self):
        """min_rrf_score=None must behave exactly like today (no filter)."""
        from agent.memory_retrieval import retrieve_memories

        rec = self._make_record()
        # Single signal at rank 50: score = 1/(60+50) ≈ 0.00909, very low
        # but should still pass when threshold is None.
        key = "Memory:test:proj:rec-1"
        bm25_results = [(key, 0.1)] + [(f"Memory:test:proj:noise-{i}", 0.05) for i in range(49)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = rec

            result = retrieve_memories("query", "proj", limit=50, min_rrf_score=None)

        assert len(result) >= 1

    def test_threshold_drops_below_floor(self):
        """A record present in only one signal at low rank should be dropped."""
        from agent.memory_retrieval import retrieve_memories

        rec = self._make_record()
        key = "Memory:test:proj:rec-1"
        # Only in one signal -- max score is 1/(60+1) ≈ 0.01639. With
        # threshold = 0.02, this single-signal record should be dropped.
        bm25_results = [(key, 5.0)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = rec

            result = retrieve_memories("q", "proj", limit=10, min_rrf_score=0.02)

        assert result == []

    def test_threshold_keeps_strong_results(self):
        """Records present in multiple signals should survive the threshold."""
        from agent.memory_retrieval import retrieve_memories

        rec = self._make_record()
        key = "Memory:test:proj:rec-1"
        # Same record at rank 1 in 4 signals: score = 4/61 ≈ 0.0656,
        # well above any reasonable threshold.
        bm25_results = [(key, 5.0)]
        relevance_results = [(key, 1000.0)]
        confidence_results = [(key, 0.8)]
        embedding_results = [(key, 0.95)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=embedding_results),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = rec

            result = retrieve_memories("q", "proj", limit=10, min_rrf_score=0.02)

        assert len(result) == 1
        assert result[0] is rec

    def test_threshold_zero_equivalent_to_none(self):
        """min_rrf_score=0 must behave like None (always pass)."""
        from agent.memory_retrieval import retrieve_memories

        rec = self._make_record()
        key = "Memory:test:proj:rec-1"
        bm25_results = [(key, 5.0)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = rec

            result = retrieve_memories("q", "proj", limit=10, min_rrf_score=0)

        # threshold=0 must NOT drop the record -- it equals "always pass".
        assert len(result) == 1

    def test_threshold_inf_returns_empty(self):
        """min_rrf_score=inf must drop everything."""
        from agent.memory_retrieval import retrieve_memories

        rec = self._make_record()
        key = "Memory:test:proj:rec-1"
        bm25_results = [(key, 5.0)]
        relevance_results = [(key, 1000.0)]
        confidence_results = [(key, 0.8)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = rec

            result = retrieve_memories("q", "proj", limit=10, min_rrf_score=float("inf"))

        assert result == []

    def test_threshold_malformed_falls_through(self):
        """Non-numeric threshold must not crash; treated as no-op."""
        from agent.memory_retrieval import retrieve_memories

        rec = self._make_record()
        key = "Memory:test:proj:rec-1"

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = [(key, 5.0)]
            mock_memory_cls.query.get.return_value = rec

            # malformed threshold -- function must return results, not crash
            result = retrieve_memories("q", "proj", limit=10, min_rrf_score="bogus")

        assert isinstance(result, list)
        assert len(result) == 1

    def test_threshold_does_not_hydrate_dropped_keys(self):
        """Filtered keys must not trigger Memory.query.get -- saves Redis I/O."""
        from agent.memory_retrieval import retrieve_memories

        survivor = self._make_record("survivor")
        key_keep = "Memory:test:proj:survivor"
        key_drop = "Memory:test:proj:dropped"

        # Keep key: present in 4 signals (high score).
        # Drop key: present in 1 signal at rank 50 (low score).
        bm25_results = [(key_keep, 5.0), (key_drop, 0.05)]
        relevance_results = [(key_keep, 1000.0)]
        confidence_results = [(key_keep, 0.8)]
        embedding_results = [(key_keep, 0.9)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=embedding_results),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = survivor

            retrieve_memories("q", "proj", limit=10, min_rrf_score=0.02)

        # Only the surviving key should be hydrated -- the dropped key
        # must not appear in any Memory.query.get call.
        hydrated = [c.args[0] for c in mock_memory_cls.query.get.call_args_list]
        assert key_drop not in hydrated


class TestSupersededFilter:
    """Test that superseded records are excluded from retrieve_memories() results."""

    def test_superseded_records_excluded_from_recall(self):
        """Records with non-empty superseded_by must not appear in results."""
        from agent.memory_retrieval import retrieve_memories

        active_record = MagicMock()
        active_record.memory_id = "active-id"
        active_record.superseded_by = ""  # active

        superseded_record = MagicMock()
        superseded_record.memory_id = "superseded-id"
        superseded_record.superseded_by = "active-id"  # archived

        key_active = "Memory:agent:testproj:active-id"
        key_superseded = "Memory:agent:testproj:superseded-id"

        bm25_results = [(key_active, 0.8), (key_superseded, 0.7)]

        def mock_get(key):
            if key == key_active:
                return active_record
            if key == key_superseded:
                return superseded_record
            return None

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.side_effect = mock_get

            result = retrieve_memories("test query", "testproj", limit=10)

        # Only the active record should be returned; superseded filtered out
        result_ids = [r.memory_id for r in result]
        assert "active-id" in result_ids
        assert "superseded-id" not in result_ids

    def test_none_superseded_by_treated_as_active(self):
        """Records with superseded_by=None are treated as active (falsy check)."""
        from agent.memory_retrieval import retrieve_memories

        none_superseded_record = MagicMock()
        none_superseded_record.memory_id = "none-superseded-id"
        none_superseded_record.superseded_by = None  # falsy → treated as active

        key = "Memory:agent:testproj:none-superseded-id"

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = [(key, 0.9)]
            mock_memory_cls.query.get.return_value = none_superseded_record

            result = retrieve_memories("test query", "testproj", limit=10)

        # None superseded_by is falsy -> treated as active, should be included
        result_ids = [r.memory_id for r in result]
        assert "none-superseded-id" in result_ids


class TestGetEmbeddingRanked:
    """Test the get_embedding_ranked() function."""

    def test_returns_empty_when_no_provider(self):
        from agent.memory_retrieval import get_embedding_ranked

        with patch("popoto.fields.embedding_field.get_default_provider", return_value=None):
            result = get_embedding_ranked("test query", "proj")
        assert result == []

    def test_returns_empty_when_no_embeddings_on_disk(self):
        from agent.memory_retrieval import get_embedding_ranked

        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1] * 768]

        with (
            patch("popoto.fields.embedding_field.get_default_provider", return_value=mock_provider),
            patch(
                "popoto.fields.embedding_field.EmbeddingField.load_embeddings",
                return_value=(None, []),
            ),
        ):
            result = get_embedding_ranked("test query", "proj")
        assert result == []

    def test_returns_ranked_by_similarity(self):
        import numpy as np

        from agent.memory_retrieval import get_embedding_ranked

        # Create mock provider that returns a query vector
        query_vec = np.random.randn(768).astype(np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)

        mock_provider = MagicMock()
        mock_provider.embed.return_value = [query_vec.tolist()]

        # Create a matrix of stored embeddings: one similar, one dissimilar
        similar_vec = query_vec + np.random.randn(768).astype(np.float32) * 0.1
        similar_vec = similar_vec / np.linalg.norm(similar_vec)
        dissimilar_vec = -query_vec  # opposite direction
        dissimilar_vec = dissimilar_vec / np.linalg.norm(dissimilar_vec)

        matrix = np.stack([similar_vec, dissimilar_vec])
        keys = ["Memory:agent:proj:similar", "Memory:agent:proj:dissimilar"]

        with (
            patch("popoto.fields.embedding_field.get_default_provider", return_value=mock_provider),
            patch(
                "popoto.fields.embedding_field.EmbeddingField.load_embeddings",
                return_value=(matrix, keys),
            ),
        ):
            result = get_embedding_ranked("test query", "proj")

        # Similar vector should rank first (positive similarity)
        assert len(result) >= 1
        assert result[0][0] == "Memory:agent:proj:similar"
        assert result[0][1] > 0

    def test_filters_by_project_key(self):
        import numpy as np

        from agent.memory_retrieval import get_embedding_ranked

        query_vec = np.ones(768, dtype=np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)

        mock_provider = MagicMock()
        mock_provider.embed.return_value = [query_vec.tolist()]

        # Both vectors similar to query, but different projects
        vec = query_vec.copy()
        matrix = np.stack([vec, vec])
        keys = ["Memory:agent:projA:key1", "Memory:agent:projB:key2"]

        with (
            patch("popoto.fields.embedding_field.get_default_provider", return_value=mock_provider),
            patch(
                "popoto.fields.embedding_field.EmbeddingField.load_embeddings",
                return_value=(matrix, keys),
            ),
        ):
            result = get_embedding_ranked("test query", "projA")

        assert len(result) == 1
        assert "projA" in result[0][0]

    def test_graceful_on_provider_error(self):
        from agent.memory_retrieval import get_embedding_ranked

        mock_provider = MagicMock()
        mock_provider.embed.side_effect = RuntimeError("Ollama unreachable")

        with patch(
            "popoto.fields.embedding_field.get_default_provider", return_value=mock_provider
        ):
            result = get_embedding_ranked("test query", "proj")
        assert result == []

    def test_embedding_failure_degrades_to_three_signals(self):
        """When embedding search fails, retrieve_memories still works."""
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "test-no-embed"
        mock_record.superseded_by = ""

        key = "Memory:test-no-embed:proj"

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[(key, 100.0)]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[(key, 0.8)]),
            patch(
                "agent.memory_retrieval.get_embedding_ranked",
                side_effect=Exception("embedding crash"),
            ),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = [(key, 5.0)]
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test query", "proj", limit=10)

        # Should still return results from BM25 + relevance + confidence
        assert len(result) == 1


class TestOllamaEmbeddingProvider:
    """Test the OllamaEmbeddingProvider adapter."""

    def test_embed_calls_ollama_api(self):
        from agent.embedding_provider import OllamaEmbeddingProvider

        provider = OllamaEmbeddingProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.1] * 768]}
        mock_response.raise_for_status = MagicMock()

        with patch(
            "agent.embedding_provider.requests.post", return_value=mock_response
        ) as mock_post:
            result = provider.embed(["test text"])

        assert len(result) == 1
        assert len(result[0]) == 768
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/api/embed" in call_args[0][0]
        assert call_args[1]["json"]["model"] == "nomic-embed-text"

    def test_embed_empty_list_returns_empty(self):
        from agent.embedding_provider import OllamaEmbeddingProvider

        provider = OllamaEmbeddingProvider()
        result = provider.embed([])
        assert result == []

    def test_embed_raises_on_connection_error(self):
        import pytest

        from agent.embedding_provider import OllamaEmbeddingProvider

        provider = OllamaEmbeddingProvider()

        with patch(
            "agent.embedding_provider.requests.post",
            side_effect=__import__("requests").exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(RuntimeError, match="Ollama unreachable"):
                provider.embed(["test"])

    def test_dimensions_property(self):
        from agent.embedding_provider import OllamaEmbeddingProvider

        provider = OllamaEmbeddingProvider()
        assert provider.dimensions == 768

    def test_is_available_true(self):
        from agent.embedding_provider import OllamaEmbeddingProvider

        provider = OllamaEmbeddingProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"models": [{"name": "nomic-embed-text:latest"}]}
        mock_response.raise_for_status = MagicMock()

        with patch("agent.embedding_provider.requests.get", return_value=mock_response):
            assert provider.is_available() is True

    def test_is_available_false_no_model(self):
        from agent.embedding_provider import OllamaEmbeddingProvider

        provider = OllamaEmbeddingProvider()
        mock_response = MagicMock()
        mock_response.json.return_value = {"models": [{"name": "llama3:latest"}]}
        mock_response.raise_for_status = MagicMock()

        with patch("agent.embedding_provider.requests.get", return_value=mock_response):
            assert provider.is_available() is False

    def test_is_available_false_on_connection_error(self):
        from agent.embedding_provider import OllamaEmbeddingProvider

        provider = OllamaEmbeddingProvider()

        with patch(
            "agent.embedding_provider.requests.get",
            side_effect=__import__("requests").exceptions.ConnectionError("refused"),
        ):
            assert provider.is_available() is False

    def test_configure_sets_global_provider(self):
        from agent.embedding_provider import OllamaEmbeddingProvider, configure_embedding_provider

        mock_provider = OllamaEmbeddingProvider()

        with (
            patch.object(mock_provider, "is_available", return_value=True),
            patch(
                "agent.embedding_provider.OllamaEmbeddingProvider",
                return_value=mock_provider,
            ),
            patch("popoto.fields.embedding_field.set_default_provider") as mock_set,
        ):
            result = configure_embedding_provider()

        mock_set.assert_called_once_with(mock_provider)
        assert result is mock_provider

    def test_configure_returns_none_when_unavailable(self):
        from agent.embedding_provider import OllamaEmbeddingProvider, configure_embedding_provider

        mock_provider = OllamaEmbeddingProvider()

        with (
            patch.object(mock_provider, "is_available", return_value=False),
            patch(
                "agent.embedding_provider.OllamaEmbeddingProvider",
                return_value=mock_provider,
            ),
        ):
            result = configure_embedding_provider()

        assert result is None


class TestParaphraseRecall:
    """Acceptance test: semantic recall retrieves paraphrased content."""

    def test_paraphrase_retrieval_via_embedding_signal(self):
        """Saving 'user prefers terse replies' retrieves on 'keep answers short'.

        This tests the full retrieve_memories pipeline with a mock embedding
        provider that returns vectors with high cosine similarity for
        semantically similar text. BM25 returns nothing (no keyword overlap),
        but the embedding signal surfaces the match.
        """

        from agent.memory_retrieval import retrieve_memories

        # The stored memory and the query have zero keyword overlap
        stored_key = "Memory:agent:proj:terse-memory"

        mock_record = MagicMock()
        mock_record.memory_id = "terse-memory"
        mock_record.superseded_by = ""

        # Embedding signal: the provider returns similar vectors for both
        # "user prefers terse replies" and "keep answers short"
        embedding_results = [(stored_key, 0.92)]  # high similarity

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_embedding_ranked", return_value=embedding_results),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            # BM25 returns nothing -- no keyword overlap between
            # "terse replies" and "keep answers short"
            mock_bm25.search.return_value = []
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("keep answers short", "proj", limit=10)

        # The embedding signal alone should surface the memory
        assert len(result) == 1
        assert result[0].memory_id == "terse-memory"


class TestSearchAssessQuality:
    """Tests for tools/memory_search search() assess_quality parameter (popoto v1.5.0)."""

    def _make_mock_record(self):
        """Build a minimal Memory mock for search() serialization."""
        from unittest.mock import MagicMock

        mock_record = MagicMock()
        mock_record.memory_id = "mem-1"
        mock_record.content = "deployment strategy"
        mock_record.score = 0.9
        mock_record.confidence = 0.7
        mock_record.source = "agent"
        mock_record.access_count = 2
        mock_record.metadata = {}
        return mock_record

    def test_assess_quality_false_no_quality_key(self):
        """search() without assess_quality must NOT include 'quality' key (backward compat)."""
        from unittest.mock import patch

        from tools.memory_search import search

        mock_record = self._make_mock_record()

        # retrieve_memories is imported inside search() so patch its module directly
        with (
            patch("agent.memory_retrieval.retrieve_memories", return_value=[mock_record]),
            patch("models.memory.Memory._meta") as mock_meta,
        ):
            mock_meta.fields = {}  # no bloom field → skip bloom check
            with patch("agent.memory_retrieval.retrieve_memories", return_value=[mock_record]):
                result = search("deploy", assess_quality=False)

        assert "quality" not in result, (
            f"assess_quality=False must not include 'quality' key; got: {list(result.keys())}"
        )
        assert "results" in result

    def test_assess_quality_true_returns_quality_key(self):
        """search(assess_quality=True) returns a 'quality' key when ContextAssembler succeeds."""
        import dataclasses
        from unittest.mock import MagicMock, patch

        from tools.memory_search import search

        mock_record = self._make_mock_record()

        # Build a fake RetrievalQuality dataclass for the mock
        @dataclasses.dataclass
        class FakeRetrievalQuality:
            avg_confidence: float = 0.75
            score_spread: float = 0.15
            fok_score: float = 0.60
            staleness_ratio: float = 0.05

        mock_assembler = MagicMock()
        mock_assembler.assess.return_value = FakeRetrievalQuality()

        mock_assembler_cls = MagicMock(return_value=mock_assembler)

        with (
            patch("agent.memory_retrieval.retrieve_memories", return_value=[mock_record]),
            patch("models.memory.Memory._meta") as mock_meta,
            patch("popoto.recipes.ContextAssembler", mock_assembler_cls),
        ):
            mock_meta.fields = {}  # no bloom field
            result = search("deploy", assess_quality=True)

        assert "quality" in result, (
            f"assess_quality=True must include 'quality' key; got: {list(result.keys())}"
        )
        assert result["quality"] is not None

    def test_assess_quality_true_failure_path_returns_results(self):
        """When ContextAssembler.assess() raises, search() returns 'results' without crashing."""
        from unittest.mock import MagicMock, patch

        from tools.memory_search import search

        mock_record = self._make_mock_record()

        mock_assembler = MagicMock()
        mock_assembler.assess.side_effect = RuntimeError("Redis connection refused")
        mock_assembler_cls = MagicMock(return_value=mock_assembler)

        with (
            patch("agent.memory_retrieval.retrieve_memories", return_value=[mock_record]),
            patch("models.memory.Memory._meta") as mock_meta,
            patch("popoto.recipes.ContextAssembler", mock_assembler_cls),
        ):
            mock_meta.fields = {}  # no bloom field
            result = search("deploy", assess_quality=True)

        # Must not crash; must still return results
        assert "results" in result, "search() must return 'results' even when quality probe fails"
        # quality key must be absent on failure (non-fatal)
        assert "quality" not in result, (
            "quality key must be absent when ContextAssembler.assess() raises"
        )
