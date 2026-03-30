"""Unit tests for the memory hook (thought injection)."""


class TestExtractTopicKeywords:
    """Test agent/memory_hook.py extract_topic_keywords()."""

    def test_extracts_from_file_path(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Read", {"file_path": "/src/deploy/config.yaml"})
        assert "deploy" in keywords
        assert "config" in keywords

    def test_extracts_from_grep_pattern(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Grep", {"pattern": "rollback.*strategy"})
        assert "rollback" in keywords
        assert "strategy" in keywords

    def test_extracts_from_command(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Bash", {"command": "kubectl get pods"})
        assert "kubectl" in keywords

    def test_empty_input(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("", {})
        assert isinstance(keywords, list)

    def test_non_dict_input(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Read", "just a string")
        assert isinstance(keywords, list)

    def test_filters_noise_words(self):
        from agent.memory_hook import extract_topic_keywords

        keywords = extract_topic_keywords("Read", {"file_path": "/usr/bin/test/file"})
        assert "usr" not in keywords
        assert "bin" not in keywords
        assert "test" not in keywords

    def test_caps_at_10_keywords(self):
        from agent.memory_hook import extract_topic_keywords

        # Long path with many segments
        long_path = "/a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q/r/s/t/u/v/w/x/y/z"
        keywords = extract_topic_keywords("Read", {"file_path": long_path})
        assert len(keywords) <= 10


class TestClusterKeywords:
    """Test agent/memory_hook.py _cluster_keywords()."""

    def test_empty_list(self):
        from agent.memory_hook import _cluster_keywords

        assert _cluster_keywords([]) == []

    def test_small_list_single_cluster(self):
        from agent.memory_hook import _cluster_keywords

        keywords = ["redis", "memory", "bloom"]
        result = _cluster_keywords(keywords)
        assert len(result) == 1
        assert result[0] == keywords

    def test_five_keywords_single_cluster(self):
        from agent.memory_hook import _cluster_keywords

        keywords = ["a", "b", "c", "d", "e"]
        result = _cluster_keywords(keywords)
        assert len(result) == 1

    def test_six_keywords_triggers_decomposition(self):
        from agent.memory_hook import _cluster_keywords

        keywords = [f"kw{i}" for i in range(6)]
        result = _cluster_keywords(keywords)
        assert len(result) >= 2

    def test_large_list_caps_at_max_clusters(self):
        from agent.memory_hook import _cluster_keywords

        keywords = [f"kw{i}" for i in range(15)]
        result = _cluster_keywords(keywords, max_clusters=3)
        assert len(result) <= 3

    def test_all_keywords_preserved(self):
        from agent.memory_hook import _cluster_keywords

        keywords = [f"kw{i}" for i in range(12)]
        result = _cluster_keywords(keywords)
        all_kws = [kw for cluster in result for kw in cluster]
        assert set(all_kws) == set(keywords)

    def test_no_tiny_trailing_cluster(self):
        """Trailing clusters with < 2 items are merged into previous."""
        from agent.memory_hook import _cluster_keywords

        # 7 keywords with max_clusters=3 -> cluster_size=3 -> [3, 3, 1]
        # The trailing [1] should be merged into the second cluster
        keywords = [f"kw{i}" for i in range(7)]
        result = _cluster_keywords(keywords, max_clusters=3)
        for cluster in result:
            assert len(cluster) >= 2


class TestCheckAndInject:
    """Test agent/memory_hook.py check_and_inject()."""

    def test_returns_none_before_window(self):
        from agent.memory_hook import _tool_counts, check_and_inject

        # Reset state
        session = "test-inject-1"
        _tool_counts.pop(session, None)

        result = check_and_inject(session, "Read", {"file_path": "/test.py"})
        # First call (count=1) should return None (not multiple of WINDOW_SIZE=3)
        assert result is None

    def test_returns_none_for_empty_keywords(self):
        from agent.memory_hook import _tool_buffers, _tool_counts, check_and_inject

        session = "test-inject-2"
        _tool_counts[session] = 2  # Next call will be count=3 (window trigger)
        _tool_buffers[session] = [{"tool_name": "", "tool_input": {}}] * 2

        result = check_and_inject(session, "", {})
        # No meaningful keywords -> None
        assert result is None

    def test_never_crashes(self):
        from agent.memory_hook import check_and_inject

        # Should never raise, even with bad inputs
        result = check_and_inject("bad-session", None, None)
        assert result is None


class TestDejaVuSignals:
    """Test deja vu signal paths in check_and_inject()."""

    def test_novel_territory_signal(self):
        """Returns novel territory thought when zero bloom hits and many keywords."""
        from unittest.mock import MagicMock, patch

        from agent.memory_hook import _tool_buffers, _tool_counts, check_and_inject
        from config.memory_defaults import (
            INJECTION_WINDOW_SIZE,
            NOVEL_TERRITORY_KEYWORD_THRESHOLD,
        )

        session = "test-novel-territory"
        _tool_counts.pop(session, None)
        _tool_buffers.pop(session, None)

        keywords = [f"keyword_{i}" for i in range(NOVEL_TERRITORY_KEYWORD_THRESHOLD + 1)]

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with (
            patch("agent.memory_hook.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
        ):
            # Fill up to WINDOW_SIZE to trigger query
            for i in range(INJECTION_WINDOW_SIZE - 1):
                check_and_inject(session, "Read", {"file_path": f"f{i}.py"})
            result = check_and_inject(session, "Read", {"file_path": "final.py"})

        assert result is not None
        assert "new territory" in result

        # Cleanup
        _tool_counts.pop(session, None)
        _tool_buffers.pop(session, None)

    def test_vague_recognition_signal(self):
        """Returns vague recognition thought when bloom hits but no strong results."""
        from unittest.mock import MagicMock, patch

        from agent.memory_hook import _tool_buffers, _tool_counts, check_and_inject
        from config.memory_defaults import (
            DEJA_VU_BLOOM_HIT_THRESHOLD,
            INJECTION_WINDOW_SIZE,
        )

        session = "test-vague-recognition"
        _tool_counts.pop(session, None)
        _tool_buffers.pop(session, None)

        keywords = [f"kw_{i}" for i in range(DEJA_VU_BLOOM_HIT_THRESHOLD + 2)]

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        mock_result = MagicMock()
        mock_result.records = []  # No strong results

        mock_assembler_instance = MagicMock()
        mock_assembler_instance.assemble.return_value = mock_result

        mock_assembler_cls = MagicMock(return_value=mock_assembler_instance)

        with (
            patch("agent.memory_hook.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
            patch("popoto.ContextAssembler", mock_assembler_cls),
        ):
            for i in range(INJECTION_WINDOW_SIZE - 1):
                check_and_inject(session, "Read", {"file_path": f"f{i}.py"})
            result = check_and_inject(session, "Read", {"file_path": "final.py"})

        assert result is not None
        assert "encountered something related" in result

        # Cleanup
        _tool_counts.pop(session, None)
        _tool_buffers.pop(session, None)

    def test_no_signal_below_thresholds(self):
        """Returns None when bloom hits are below threshold and no strong results."""
        from unittest.mock import MagicMock, patch

        from agent.memory_hook import _tool_buffers, _tool_counts, check_and_inject
        from config.memory_defaults import INJECTION_WINDOW_SIZE

        session = "test-below-threshold"
        _tool_counts.pop(session, None)
        _tool_buffers.pop(session, None)

        # Only 2 keywords -- below DEJA_VU_BLOOM_HIT_THRESHOLD (3)
        keywords = ["kw_0", "kw_1"]

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        mock_result = MagicMock()
        mock_result.records = []

        mock_assembler_instance = MagicMock()
        mock_assembler_instance.assemble.return_value = mock_result

        mock_assembler_cls = MagicMock(return_value=mock_assembler_instance)

        with (
            patch("agent.memory_hook.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
            patch("popoto.ContextAssembler", mock_assembler_cls),
        ):
            for i in range(INJECTION_WINDOW_SIZE - 1):
                check_and_inject(session, "Read", {"file_path": f"f{i}.py"})
            result = check_and_inject(session, "Read", {"file_path": "final.py"})

        assert result is None

        # Cleanup
        _tool_counts.pop(session, None)
        _tool_buffers.pop(session, None)


class TestApplyCategoryWeights:
    """Test agent/memory_hook.py _apply_category_weights()."""

    def test_empty_list_returns_empty(self):
        from agent.memory_hook import _apply_category_weights

        assert _apply_category_weights([]) == []

    def test_none_metadata_gets_default_weight(self):
        from unittest.mock import MagicMock

        from agent.memory_hook import _apply_category_weights

        record = MagicMock()
        record.metadata = None
        record.score = 1.0
        result = _apply_category_weights([record])
        assert len(result) == 1
        assert result[0] is record

    def test_missing_category_gets_default_weight(self):
        from unittest.mock import MagicMock

        from agent.memory_hook import _apply_category_weights

        record = MagicMock()
        record.metadata = {}
        record.score = 1.0
        result = _apply_category_weights([record])
        assert len(result) == 1

    def test_correction_ranks_higher_than_pattern(self):
        from unittest.mock import MagicMock

        from agent.memory_hook import _apply_category_weights

        correction = MagicMock()
        correction.metadata = {"category": "correction"}
        correction.score = 1.0

        pattern = MagicMock()
        pattern.metadata = {"category": "pattern"}
        pattern.score = 1.0

        result = _apply_category_weights([pattern, correction])
        # correction (weight 1.5) should rank before pattern (weight 1.0)
        assert result[0] is correction
        assert result[1] is pattern

    def test_decision_ranks_higher_than_pattern(self):
        from unittest.mock import MagicMock

        from agent.memory_hook import _apply_category_weights

        decision = MagicMock()
        decision.metadata = {"category": "decision"}
        decision.score = 1.0

        pattern = MagicMock()
        pattern.metadata = {"category": "pattern"}
        pattern.score = 1.0

        result = _apply_category_weights([pattern, decision])
        assert result[0] is decision

    def test_high_score_pattern_beats_low_score_correction(self):
        from unittest.mock import MagicMock

        from agent.memory_hook import _apply_category_weights

        correction = MagicMock()
        correction.metadata = {"category": "correction"}
        correction.score = 0.5

        pattern = MagicMock()
        pattern.metadata = {"category": "pattern"}
        pattern.score = 1.0

        result = _apply_category_weights([correction, pattern])
        # pattern (1.0 * 1.0 = 1.0) beats correction (0.5 * 1.5 = 0.75)
        assert result[0] is pattern

    def test_non_dict_metadata_gets_default_weight(self):
        from unittest.mock import MagicMock

        from agent.memory_hook import _apply_category_weights

        record = MagicMock()
        record.metadata = "not a dict"
        record.score = 1.0
        result = _apply_category_weights([record])
        assert len(result) == 1

    def test_non_string_category_gets_default_weight(self):
        from unittest.mock import MagicMock

        from agent.memory_hook import _apply_category_weights

        record = MagicMock()
        record.metadata = {"category": 123}
        record.score = 1.0
        result = _apply_category_weights([record])
        assert len(result) == 1

    def test_preserves_all_records(self):
        from unittest.mock import MagicMock

        from agent.memory_hook import _apply_category_weights

        records = []
        for i in range(5):
            r = MagicMock()
            r.metadata = {"category": "pattern"}
            r.score = float(i)
            records.append(r)

        result = _apply_category_weights(records)
        assert len(result) == 5
        assert set(id(r) for r in result) == set(id(r) for r in records)


class TestGetInjectedThoughts:
    """Test agent/memory_hook.py get_injected_thoughts()."""

    def test_returns_empty_for_unknown_session(self):
        from agent.memory_hook import get_injected_thoughts

        result = get_injected_thoughts("nonexistent-session")
        assert result == []

    def test_returns_list(self):
        from agent.memory_hook import _injected_thoughts, get_injected_thoughts

        _injected_thoughts["test-session"] = [("key1", "thought1")]
        result = get_injected_thoughts("test-session")
        assert len(result) == 1
        assert result[0] == ("key1", "thought1")
        # Cleanup
        del _injected_thoughts["test-session"]


class TestClearSession:
    """Test agent/memory_hook.py clear_session()."""

    def test_clears_all_state(self):
        from agent.memory_hook import (
            _injected_thoughts,
            _tool_buffers,
            _tool_counts,
            clear_session,
        )

        session = "test-clear"
        _tool_buffers[session] = [{"tool_name": "test", "tool_input": {}}]
        _tool_counts[session] = 5
        _injected_thoughts[session] = [("k", "v")]

        clear_session(session)

        assert session not in _tool_buffers
        assert session not in _tool_counts
        assert session not in _injected_thoughts

    def test_clear_nonexistent_session(self):
        from agent.memory_hook import clear_session

        # Should not raise
        clear_session("nonexistent-clear-session")
