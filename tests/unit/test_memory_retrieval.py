"""Unit tests for agent/memory_retrieval.py -- BM25 + RRF fusion retrieval."""

from unittest.mock import MagicMock, patch


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

    def test_returns_empty_on_error(self):
        from agent.memory_retrieval import get_confidence_ranked

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.side_effect = Exception("connection failed")
            result = get_confidence_ranked()
            assert result == []

    def test_returns_empty_when_no_data(self):
        from agent.memory_retrieval import get_confidence_ranked

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = {}
            result = get_confidence_ranked()
            assert result == []

    def test_returns_sorted_by_confidence(self):
        import msgpack

        from agent.memory_retrieval import get_confidence_ranked

        mock_data = {
            b"Memory:key1": msgpack.packb({"confidence": 0.9, "evidence_count": 5}),
            b"Memory:key2": msgpack.packb({"confidence": 0.3, "evidence_count": 2}),
            b"Memory:key3": msgpack.packb({"confidence": 0.7, "evidence_count": 3}),
        }

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = mock_data
            result = get_confidence_ranked()

        assert len(result) == 3
        # Should be sorted by confidence descending
        assert result[0][0] == "Memory:key1"
        assert result[0][1] == 0.9
        assert result[1][0] == "Memory:key3"
        assert result[1][1] == 0.7
        assert result[2][0] == "Memory:key2"
        assert result[2][1] == 0.3

    def test_limit_caps_results(self):
        import msgpack

        from agent.memory_retrieval import get_confidence_ranked

        mock_data = {
            f"Memory:key{i}".encode(): msgpack.packb({"confidence": float(i) / 10})
            for i in range(20)
        }

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = mock_data
            result = get_confidence_ranked(limit=5)

        assert len(result) == 5

    def test_corrupt_entry_skipped(self):
        import msgpack

        from agent.memory_retrieval import get_confidence_ranked

        mock_data = {
            b"Memory:good": msgpack.packb({"confidence": 0.8}),
            b"Memory:bad": b"not-valid-msgpack-\xff\xff",
        }

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.hgetall.return_value = mock_data
            result = get_confidence_ranked()

        # Only the valid entry should be returned
        assert len(result) == 1
        assert result[0][0] == "Memory:good"


class TestRetrieveMemories:
    """Test the full retrieve_memories() pipeline."""

    def test_returns_empty_on_exception(self):
        from agent.memory_retrieval import retrieve_memories

        with patch("popoto.BM25Field") as mock_bm25:
            mock_bm25.search.side_effect = Exception("boom")
            with (
                patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
                patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            ):
                result = retrieve_memories("test query", "project")
                assert result == []

    def test_fuses_three_signals(self):
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "test-123"

        bm25_results = [("Memory:test-123:proj", 5.0)]
        relevance_results = [("Memory:test-123:proj", 1000.0)]
        confidence_results = [("Memory:test-123:proj", 0.8)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
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

        key = "Memory:test-123:proj"
        bm25_results = [(key, 5.0)]
        relevance_results = [(key, 1000.0)]
        confidence_results = [(key, 0.8)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = bm25_results
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test query", "project", limit=10)

        # Should be exactly 1 record, not 3
        assert len(result) == 1

    def test_bm25_failure_degrades_gracefully(self):
        """When BM25 fails, retrieval still works with relevance + confidence."""
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "test-456"

        key = "Memory:test-456:proj"
        relevance_results = [(key, 1000.0)]
        confidence_results = [(key, 0.8)]

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=relevance_results),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=confidence_results),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.side_effect = Exception("BM25 index missing")
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test query", "project", limit=10)

        # Should still return results from relevance + confidence
        assert len(result) == 1

    def test_empty_query_returns_empty(self):
        """BM25 with empty query should not crash."""
        from agent.memory_retrieval import retrieve_memories

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
        ):
            mock_bm25.search.return_value = []
            result = retrieve_memories("", "project")

        assert result == []

    def test_respects_rrf_k_override(self):
        from agent.memory_retrieval import retrieve_memories

        mock_record = MagicMock()
        mock_record.memory_id = "test-789"

        key = "Memory:test-789:proj"

        with (
            patch("popoto.BM25Field") as mock_bm25,
            patch("agent.memory_retrieval.get_relevance_ranked", return_value=[(key, 100.0)]),
            patch("agent.memory_retrieval.get_confidence_ranked", return_value=[]),
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = [(key, 5.0)]
            mock_memory_cls.query.get.return_value = mock_record

            result = retrieve_memories("test", "project", rrf_k=20)

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
            patch("models.memory.Memory") as mock_memory_cls,
        ):
            mock_bm25.search.return_value = [("Memory:missing:proj", 5.0)]
            mock_memory_cls.query.get.return_value = None  # Not found

            result = retrieve_memories("test query", "project")

        assert result == []
