"""Unit tests for .claude/hooks/hook_utils/memory_bridge.py.

Tests the memory bridge module that wires Claude Code hooks to the
subconscious memory system. All tests use mocked Redis/Memory to
avoid external dependencies.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path for imports
_HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


class TestSidecar:
    """Test sidecar file load/save operations."""

    def test_load_sidecar_missing_file(self, tmp_path, monkeypatch):
        """Returns default state when sidecar file does not exist."""
        from hook_utils.memory_bridge import _load_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        result = _load_sidecar("test-session")
        assert result == {"count": 0, "buffer": [], "injected": []}

    def test_load_sidecar_corrupt_json(self, tmp_path, monkeypatch):
        """Returns default state when sidecar file contains invalid JSON."""
        from hook_utils.memory_bridge import _load_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        sidecar_dir = tmp_path / "test-session"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / "memory_buffer.json").write_text("not json{{{")

        result = _load_sidecar("test-session")
        assert result == {"count": 0, "buffer": [], "injected": []}

    def test_load_sidecar_valid(self, tmp_path, monkeypatch):
        """Loads valid sidecar state correctly."""
        from hook_utils.memory_bridge import _load_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        sidecar_dir = tmp_path / "test-session"
        sidecar_dir.mkdir(parents=True)
        state = {"count": 5, "buffer": [{"tool_name": "Read"}], "injected": []}
        (sidecar_dir / "memory_buffer.json").write_text(json.dumps(state))

        result = _load_sidecar("test-session")
        assert result["count"] == 5
        assert len(result["buffer"]) == 1

    def test_save_sidecar_atomic(self, tmp_path, monkeypatch):
        """Saves sidecar file atomically (no .tmp left over)."""
        from hook_utils.memory_bridge import _load_sidecar, _save_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        state = {"count": 3, "buffer": [], "injected": []}
        _save_sidecar("test-session", state)

        # Verify file exists and no tmp left
        sidecar_dir = tmp_path / "test-session"
        assert (sidecar_dir / "memory_buffer.json").exists()
        assert not (sidecar_dir / "memory_buffer.json.tmp").exists()

        # Verify content
        loaded = _load_sidecar("test-session")
        assert loaded["count"] == 3

    def test_load_sidecar_non_dict(self, tmp_path, monkeypatch):
        """Returns default state when sidecar file contains a non-dict JSON value."""
        from hook_utils.memory_bridge import _load_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        sidecar_dir = tmp_path / "test-session"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / "memory_buffer.json").write_text('"just a string"')

        result = _load_sidecar("test-session")
        assert result == {"count": 0, "buffer": [], "injected": []}


class TestRecall:
    """Test the recall() function."""

    def test_recall_returns_none_before_window(self, tmp_path, monkeypatch):
        """Returns None for tool calls before WINDOW_SIZE threshold."""
        from hook_utils.memory_bridge import recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        # First call (count=1, WINDOW_SIZE=3) -- should not query
        result = recall("test-session", "Read", {"file_path": "test.py"})
        assert result is None

    def test_recall_returns_none_on_exception(self):
        """Returns None when an exception occurs."""
        from hook_utils.memory_bridge import recall

        # Pass invalid session_id type to trigger an error deep in the chain
        with patch("hook_utils.memory_bridge._load_sidecar", side_effect=Exception("boom")):
            result = recall("test-session", "Read", {})
            assert result is None

    def test_recall_increments_counter(self, tmp_path, monkeypatch):
        """Each call increments the sidecar counter."""
        from hook_utils.memory_bridge import _load_sidecar, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        recall("test-session", "Read", {"file_path": "a.py"})
        recall("test-session", "Edit", {"file_path": "b.py"})

        state = _load_sidecar("test-session")
        assert state["count"] == 2
        assert len(state["buffer"]) == 2

    def test_recall_buffer_capped(self, tmp_path, monkeypatch):
        """Buffer is capped at BUFFER_SIZE entries."""
        from hook_utils.memory_bridge import BUFFER_SIZE, _load_sidecar, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        # Mock out the query path to avoid importing Memory
        with patch("utils.keyword_extraction.extract_topic_keywords", return_value=[]):
            for i in range(BUFFER_SIZE + 5):
                recall("test-session", "Read", {"file_path": f"file_{i}.py"})

        state = _load_sidecar("test-session")
        assert len(state["buffer"]) <= BUFFER_SIZE

    def test_recall_empty_tool_input(self, tmp_path, monkeypatch):
        """Returns None when tool_input is empty (no keywords to extract)."""
        from hook_utils.memory_bridge import WINDOW_SIZE, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        # Mock extract_topic_keywords to return empty
        with patch(
            "utils.keyword_extraction.extract_topic_keywords",
            return_value=[],
        ):
            # Fill up to WINDOW_SIZE to trigger query path
            for _ in range(WINDOW_SIZE):
                result = recall("test-session", "Read", {})

        assert result is None

    def test_recall_novel_territory_signal(self, tmp_path, monkeypatch):
        """Returns novel territory thought when bloom misses on many keywords."""
        from hook_utils.memory_bridge import NOVEL_TERRITORY_KEYWORD_THRESHOLD, WINDOW_SIZE, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        keywords = [f"keyword_{i}" for i in range(NOVEL_TERRITORY_KEYWORD_THRESHOLD + 1)]

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with (
            patch(
                "utils.keyword_extraction.extract_topic_keywords",
                return_value=keywords,
            ),
            patch("models.memory.Memory", mock_memory_cls),
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("test-session", "Read", {"file_path": f"f{i}.py"})
            result = recall("test-session", "Read", {"file_path": "final.py"})

        assert result is not None
        assert "new territory" in result


class TestRecallBloomGate:
    """Test recall()'s pre-cluster bloom gate with BLOOM_MIN_HITS threshold.

    The gate has three branches:
    - bloom_hits == 0 with sufficient unique keywords → emits deja-vu
      (regression guard for novel-territory signal).
    - 1 <= bloom_hits < BLOOM_MIN_HITS → returns None, no deja-vu.
    - bloom_hits >= BLOOM_MIN_HITS → proceeds to _recall_with_query.
    """

    def test_zero_hits_with_many_keywords_emits_dejavu(self, tmp_path, monkeypatch):
        """bloom_hits == 0 with NOVEL_TERRITORY_KEYWORD_THRESHOLD keywords
        still emits the deja-vu thought (preserved behavior)."""
        from hook_utils.memory_bridge import (
            NOVEL_TERRITORY_KEYWORD_THRESHOLD,
            WINDOW_SIZE,
            recall,
        )

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        keywords = [f"kw_{i}" for i in range(NOVEL_TERRITORY_KEYWORD_THRESHOLD + 1)]

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with (
            patch("utils.keyword_extraction.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("sess", "Read", {"file_path": f"f{i}.py"})
            result = recall("sess", "Read", {"file_path": "x.py"})

        assert isinstance(result, str)
        assert "new territory" in result

    def test_single_hit_returns_none_without_dejavu(self, tmp_path, monkeypatch):
        """bloom_hits == 1 (below BLOOM_MIN_HITS=2) returns None and emits
        no deja-vu thought -- the new gate's primary purpose."""
        from hook_utils.memory_bridge import WINDOW_SIZE, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        keywords = ["only_one_hits"] + [f"miss_{i}" for i in range(8)]

        # bloom returns True for the FIRST keyword only -> 1 hit
        mock_bloom = MagicMock()
        first_calls = {"n": 0}

        def selective_might_exist(model, kw):
            first_calls["n"] += 1
            return first_calls["n"] == 1

        mock_bloom.might_exist = MagicMock(side_effect=selective_might_exist)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with (
            patch("utils.keyword_extraction.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("sess-single", "Read", {"file_path": f"f{i}.py"})
            result = recall("sess-single", "Read", {"file_path": "x.py"})

        assert result is None

    def test_two_hits_proceeds_to_recall_with_query(self, tmp_path, monkeypatch):
        """bloom_hits == BLOOM_MIN_HITS proceeds past the gate and into
        _recall_with_query (verified via call-count)."""
        from hook_utils.memory_bridge import WINDOW_SIZE, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        keywords = ["alpha", "beta", "gamma"]

        # All three keywords return True -> 3 hits >= BLOOM_MIN_HITS
        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with (
            patch("utils.keyword_extraction.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
            patch("hook_utils.memory_bridge._recall_with_query", return_value=[]) as mrwq,
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("sess-pass", "Read", {"file_path": f"f{i}.py"})
            recall("sess-pass", "Read", {"file_path": "x.py"})

        # _recall_with_query was invoked at least once
        assert mrwq.call_count >= 1


class TestRecallPassesMinScore:
    """Confirm recall() passes RRF_MIN_SCORE through to _recall_with_query."""

    def test_recall_passes_min_rrf_score(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import RRF_MIN_SCORE, WINDOW_SIZE, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        captured = {}

        def fake_rwq(**kwargs):
            captured.update(kwargs)
            return []

        with (
            patch(
                "utils.keyword_extraction.extract_topic_keywords",
                return_value=["alpha", "beta", "gamma", "delta"],
            ),
            patch("models.memory.Memory", mock_memory_cls),
            patch("hook_utils.memory_bridge._recall_with_query", side_effect=fake_rwq),
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("sess-mscore", "Read", {"file_path": f"f{i}.py"})
            recall("sess-mscore", "Read", {"file_path": "x.py"})

        assert captured.get("min_rrf_score") == RRF_MIN_SCORE


class TestPrefetchPassesMinScore:
    """Confirm prefetch() passes RRF_MIN_SCORE through to _recall_with_query."""

    def test_prefetch_passes_min_rrf_score(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import RRF_MIN_SCORE, prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        captured = {}

        def fake_rwq(**kwargs):
            captured.update(kwargs)
            return []

        with patch("hook_utils.memory_bridge._recall_with_query", side_effect=fake_rwq):
            # 50+ char prompt avoids the trivial-prompt gate
            prefetch(
                "sess-prefetch",
                "Looking into the deployment migration auth flow today",
            )

        assert captured.get("min_rrf_score") == RRF_MIN_SCORE


class TestRecallWithQueryBloomThreshold:
    """Test _recall_with_query's internal bloom_hits < BLOOM_MIN_HITS gate."""

    def test_single_hit_returns_empty(self):
        """Tightened gate: 1 < BLOOM_MIN_HITS=2 returns []."""
        from hook_utils.memory_bridge import _recall_with_query

        # Three tokens; bloom returns True for the FIRST only -> 1 hit
        mock_bloom = MagicMock()
        calls = {"n": 0}

        def selective(model, tok):
            calls["n"] += 1
            return calls["n"] == 1

        mock_bloom.might_exist = MagicMock(side_effect=selective)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with patch("models.memory.Memory", mock_memory_cls):
            result = _recall_with_query(
                "alpha beta gamma", project_key="test", bloom_check_emit_dejavu=False
            )
        assert result == []

    def test_two_hits_proceeds(self):
        """bloom_hits == BLOOM_MIN_HITS proceeds to retrieve_memories."""
        from hook_utils.memory_bridge import _recall_with_query

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        rec = MagicMock()
        rec.memory_id = "rec-1"
        rec.content = "x"

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("agent.memory_retrieval.retrieve_memories", return_value=[rec]),
            patch("utils.keyword_extraction._apply_category_weights", wraps=lambda r: r),
        ):
            result = _recall_with_query("alpha beta", project_key="test")
        assert result == [rec]

    def test_min_rrf_score_threaded_to_retrieve_memories(self):
        """_recall_with_query passes min_rrf_score through to retrieve_memories."""
        from hook_utils.memory_bridge import _recall_with_query

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        captured = {}

        def fake_rm(**kwargs):
            captured.update(kwargs)
            return []

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("agent.memory_retrieval.retrieve_memories", side_effect=fake_rm),
        ):
            _recall_with_query("alpha beta gamma", project_key="test", min_rrf_score=0.0123)
        assert captured.get("min_rrf_score") == 0.0123


class TestRecallCategoryReranking:
    """Test that recall() applies category re-ranking via _apply_category_weights."""

    def test_recall_calls_apply_category_weights(self, tmp_path, monkeypatch):
        """Recall calls _apply_category_weights on results before formatting."""
        from hook_utils.memory_bridge import WINDOW_SIZE, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        keywords = ["memory", "recall", "weights", "category", "test"]

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        # Create mock records with content and score (as set by retrieve_memories)
        mock_record = MagicMock()
        mock_record.memory_id = "rec-1"
        mock_record.content = "test memory content for recall"
        mock_record.metadata = {"category": "correction"}
        mock_record.score = 0.8

        with (
            patch("utils.keyword_extraction.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
            patch("agent.memory_retrieval.retrieve_memories", return_value=[mock_record]),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
            patch(
                "utils.keyword_extraction._apply_category_weights",
                wraps=lambda records: records,
            ) as mock_rerank,
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("test-session", "Read", {"file_path": f"f{i}.py"})
            result = recall("test-session", "Read", {"file_path": "final.py"})

        # Verify _apply_category_weights was called
        mock_rerank.assert_called_once()
        # Verify we got thought output
        assert result is not None
        assert "<thought>" in result


class TestIngest:
    """Test the ingest() function."""

    def test_ingest_empty_content(self):
        """Returns False for empty content."""
        from hook_utils.memory_bridge import ingest

        assert ingest("") is False
        assert ingest(None) is False

    def test_ingest_short_content(self):
        """Returns False for content shorter than MIN_PROMPT_LENGTH."""
        from hook_utils.memory_bridge import ingest

        assert ingest("short") is False

    def test_ingest_trivial_patterns(self):
        """Returns False for trivial prompts."""
        from hook_utils.memory_bridge import ingest

        # These are all under MIN_PROMPT_LENGTH anyway, but test the pattern check
        assert ingest("yes") is False
        assert ingest("continue") is False
        assert ingest("ok") is False

    def test_ingest_returns_false_on_exception(self):
        """Returns False when Memory import or save fails."""
        from hook_utils.memory_bridge import ingest

        long_content = "x" * 100
        mock_memory = MagicMock()
        mock_memory._meta.fields.get.return_value = None  # No bloom field
        mock_memory.safe_save.side_effect = Exception("redis down")
        with (
            patch("models.memory.Memory", mock_memory),
            patch("models.memory.SOURCE_HUMAN", "human"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            result = ingest(long_content)
            assert result is False

    def test_ingest_success(self):
        """Returns True when content passes filters and saves successfully."""
        from hook_utils.memory_bridge import ingest

        long_content = (
            "This is a substantial prompt that should be saved to memory for later recall"
        )

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom
        mock_memory_cls.safe_save.return_value = MagicMock()  # Non-None = success

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("models.memory.SOURCE_HUMAN", "human"),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            result = ingest(long_content)
            assert result is True
            mock_memory_cls.safe_save.assert_called_once()

    def test_ingest_bloom_dedup(self):
        """Returns False when bloom filter indicates duplicate."""
        from hook_utils.memory_bridge import ingest

        long_content = (
            "This is a substantial prompt that should be detected as a duplicate in bloom"
        )

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)  # Already exists

        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with patch("models.memory.Memory", mock_memory_cls):
            result = ingest(long_content)
            assert result is False
            mock_memory_cls.safe_save.assert_not_called()


class TestExtract:
    """Test the extract() function."""

    def test_extract_no_transcript(self):
        """Returns None when transcript_path is None."""
        from hook_utils.memory_bridge import extract

        with patch("hook_utils.memory_bridge.cleanup_sidecar") as mock_cleanup:
            result = extract("test-session", None)
            assert result is None
            mock_cleanup.assert_called_once_with("test-session")

    def test_extract_missing_transcript(self, tmp_path):
        """Returns None when transcript file does not exist."""
        from hook_utils.memory_bridge import extract

        with patch("hook_utils.memory_bridge.cleanup_sidecar"):
            result = extract("test-session", str(tmp_path / "nonexistent.jsonl"))
            assert result is None

    def test_extract_short_transcript(self, tmp_path):
        """Returns None when transcript is too short."""
        from hook_utils.memory_bridge import extract

        transcript = tmp_path / "short.jsonl"
        transcript.write_text("tiny")

        with patch("hook_utils.memory_bridge.cleanup_sidecar"):
            result = extract("test-session", str(transcript))
            assert result is None

    def test_extract_calls_extraction_pipeline(self, tmp_path, monkeypatch):
        """Calls Haiku extraction and outcome detection on valid transcript."""
        from hook_utils.memory_bridge import extract

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / "sidecar" / sid,
        )

        # Create a transcript with enough content
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("A" * 200)

        # Create sidecar with injected thoughts
        sidecar_dir = tmp_path / "sidecar" / "test-session"
        sidecar_dir.mkdir(parents=True)
        state = {
            "count": 10,
            "buffer": [],
            "injected": [{"memory_id": "m1", "content": "thought 1"}],
        }
        (sidecar_dir / "memory_buffer.json").write_text(json.dumps(state))

        mock_extract = MagicMock()
        mock_detect = MagicMock()

        with (
            patch(
                "agent.memory_extraction.extract_observations_async",
                mock_extract,
            ),
            patch(
                "agent.memory_extraction.detect_outcomes_async",
                mock_detect,
            ),
            patch("asyncio.run") as mock_run,
            patch("hook_utils.memory_bridge.cleanup_sidecar"),
        ):
            extract("test-session", str(transcript))
            # asyncio.run should be called at least once (for extraction)
            assert mock_run.call_count >= 1

    def test_extract_cleans_up_sidecar(self, tmp_path, monkeypatch):
        """Sidecar files are cleaned up even on failure."""
        from hook_utils.memory_bridge import extract

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / "sidecar" / sid,
        )

        with patch("hook_utils.memory_bridge.cleanup_sidecar") as mock_cleanup:
            # Trigger failure by providing non-existent path
            extract("test-session", str(tmp_path / "nope.jsonl"))
            mock_cleanup.assert_called_once_with("test-session")


class TestAgentSessionSidecar:
    """Test the agent session sidecar load/save operations."""

    def test_load_missing_file(self, tmp_path, monkeypatch):
        """Returns empty dict when sidecar file does not exist."""
        from hook_utils.memory_bridge import load_agent_session_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        result = load_agent_session_sidecar("test-session")
        assert result == {}

    def test_load_corrupt_json(self, tmp_path, monkeypatch):
        """Returns empty dict when sidecar file contains invalid JSON."""
        from hook_utils.memory_bridge import load_agent_session_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        sidecar_dir = tmp_path / "test-session"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / "agent_session.json").write_text("not valid json{")

        result = load_agent_session_sidecar("test-session")
        assert result == {}

    def test_load_valid(self, tmp_path, monkeypatch):
        """Loads valid agent session sidecar data correctly."""
        from hook_utils.memory_bridge import load_agent_session_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        sidecar_dir = tmp_path / "test-session"
        sidecar_dir.mkdir(parents=True)
        data = {"agent_session_id": "session-123", "merge_detected": True}
        (sidecar_dir / "agent_session.json").write_text(json.dumps(data))

        result = load_agent_session_sidecar("test-session")
        assert result["agent_session_id"] == "session-123"
        assert result["merge_detected"] is True

    def test_save_atomic(self, tmp_path, monkeypatch):
        """Saves agent session sidecar file atomically."""
        from hook_utils.memory_bridge import load_agent_session_sidecar, save_agent_session_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        data = {"agent_session_id": "session-456"}
        save_agent_session_sidecar("test-session", data)

        # No tmp file left
        sidecar_dir = tmp_path / "test-session"
        assert (sidecar_dir / "agent_session.json").exists()
        assert not (sidecar_dir / "agent_session.json.tmp").exists()

        # Round-trip
        loaded = load_agent_session_sidecar("test-session")
        assert loaded["agent_session_id"] == "session-456"

    def test_load_non_dict(self, tmp_path, monkeypatch):
        """Returns empty dict when sidecar contains non-dict JSON value."""
        from hook_utils.memory_bridge import load_agent_session_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        sidecar_dir = tmp_path / "test-session"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / "agent_session.json").write_text('"just a string"')

        result = load_agent_session_sidecar("test-session")
        assert result == {}


class TestCleanupSidecar:
    """Test the cleanup_sidecar() function."""

    def test_cleanup_removes_sidecar_files(self, tmp_path, monkeypatch):
        """Removes memory_buffer.json and tmp files."""
        from hook_utils.memory_bridge import cleanup_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        sidecar_dir = tmp_path / "test-session"
        sidecar_dir.mkdir(parents=True)
        (sidecar_dir / "memory_buffer.json").write_text("{}")
        (sidecar_dir / "memory_buffer.json.tmp").write_text("{}")

        cleanup_sidecar("test-session")

        assert not (sidecar_dir / "memory_buffer.json").exists()
        assert not (sidecar_dir / "memory_buffer.json.tmp").exists()
        # Directory itself should remain
        assert sidecar_dir.exists()

    def test_cleanup_nonexistent_dir(self, tmp_path, monkeypatch):
        """Does not raise when sidecar directory does not exist."""
        from hook_utils.memory_bridge import cleanup_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / "nonexistent" / sid,
        )
        # Should not raise
        cleanup_sidecar("test-session")


class TestGetProjectKey:
    """Test _get_project_key() resolution with cwd parameter."""

    def test_env_var_takes_priority(self, monkeypatch):
        """VALOR_PROJECT_KEY env var overrides cwd and defaults."""
        from hook_utils.memory_bridge import _get_project_key

        monkeypatch.setenv("VALOR_PROJECT_KEY", "myproject")
        result = _get_project_key(cwd="/some/other/path")
        assert result == "myproject"

    def test_cwd_falls_back_to_dirname(self, monkeypatch):
        """When no env var or projects.json match, returns cwd basename."""
        from hook_utils.memory_bridge import _get_project_key

        monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
        # Patch projects.json path to nonexistent to skip that branch
        import hook_utils.memory_bridge as mb

        monkeypatch.setattr(mb, "_get_project_key", mb._get_project_key)

        # Use a tmp path with no projects.json
        result = _get_project_key(cwd="/home/user/myrepo")
        # Without a matching projects.json entry, returns basename
        assert result == "myrepo"

    def test_no_cwd_returns_default(self, monkeypatch):
        """Without cwd and no env var, returns DEFAULT_PROJECT_KEY (not 'dm')."""
        from hook_utils.memory_bridge import _get_project_key

        monkeypatch.delenv("VALOR_PROJECT_KEY", raising=False)
        result = _get_project_key(cwd=None)
        # Must not be "dm" -- that value is reserved for Telegram DMs
        assert result != "dm"

    def test_default_project_key_not_dm(self):
        """DEFAULT_PROJECT_KEY in config/memory_defaults.py is not 'dm'."""
        from config.memory_defaults import DEFAULT_PROJECT_KEY

        assert DEFAULT_PROJECT_KEY != "dm", (
            "DEFAULT_PROJECT_KEY must not be 'dm' -- that value is reserved for Telegram DMs. "
            "Falling back to 'dm' silently mislabels all hook-created memories."
        )


class TestRecallPassesCwd:
    """Test that recall() passes cwd to _get_project_key()."""

    def test_recall_uses_cwd_for_project_key(self, tmp_path, monkeypatch):
        """recall() passes cwd arg through to _get_project_key for project scoping."""
        from hook_utils.memory_bridge import WINDOW_SIZE, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        captured_cwd = []

        def mock_get_project_key(cwd=None):
            captured_cwd.append(cwd)
            return "valor"

        keywords = ["memory", "recall", "test", "keyword", "project"]

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        mock_record = MagicMock()
        mock_record.memory_id = "rec-1"
        mock_record.content = "test content"

        with (
            patch("utils.keyword_extraction.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
            patch("agent.memory_retrieval.retrieve_memories", return_value=[mock_record]),
            patch("hook_utils.memory_bridge._get_project_key", side_effect=mock_get_project_key),
            patch("utils.keyword_extraction._apply_category_weights", wraps=lambda r: r),
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("sess", "Read", {"file_path": f"f{i}.py"})
            recall("sess", "Read", {"file_path": "final.py"}, cwd="/home/user/ai")

        # _get_project_key should have been called with the cwd
        assert any(c == "/home/user/ai" for c in captured_cwd), (
            f"Expected _get_project_key to be called with cwd='/home/user/ai', got: {captured_cwd}"
        )


class TestIngestPassesCwd:
    """Test that ingest() passes cwd to _get_project_key()."""

    def test_ingest_uses_cwd_for_project_key(self):
        """ingest() passes cwd arg through to _get_project_key."""
        from hook_utils.memory_bridge import ingest

        long_content = "This is a substantial prompt that tests project key routing via cwd"

        captured_cwd = []

        def mock_get_project_key(cwd=None):
            captured_cwd.append(cwd)
            return "valor"

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom
        mock_memory_cls.safe_save.return_value = MagicMock()

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("models.memory.SOURCE_HUMAN", "human"),
            patch("hook_utils.memory_bridge._get_project_key", side_effect=mock_get_project_key),
        ):
            ingest(long_content, cwd="/home/user/myproject")

        assert captured_cwd == ["/home/user/myproject"], (
            f"Expected _get_project_key called with '/home/user/myproject', got {captured_cwd}"
        )


class TestStripPmBoilerplate:
    """Test _strip_pm_boilerplate() helper."""

    def test_strips_full_boilerplate(self):
        from hook_utils.memory_bridge import _strip_pm_boilerplate

        prompt = (
            "FROM: valor-session (dev)\n"
            "SCOPE: This session is scoped to the message below from this sender.\n"
            "MESSAGE: investigate auth bug from PR 800"
        )
        result = _strip_pm_boilerplate(prompt)
        assert result == "investigate auth bug from PR 800"

    def test_strips_multiline_scope(self):
        from hook_utils.memory_bridge import _strip_pm_boilerplate

        prompt = (
            "FROM: valor-session (pm)\n"
            "SCOPE: line one\n"
            "of multi-line scope\n"
            "MESSAGE: actual user message"
        )
        result = _strip_pm_boilerplate(prompt)
        assert result == "actual user message"

    def test_returns_unchanged_when_no_boilerplate(self):
        from hook_utils.memory_bridge import _strip_pm_boilerplate

        prompt = "Just a regular user prompt without any boilerplate"
        result = _strip_pm_boilerplate(prompt)
        assert result == prompt

    def test_handles_empty_string(self):
        from hook_utils.memory_bridge import _strip_pm_boilerplate

        assert _strip_pm_boilerplate("") == ""

    def test_handles_non_string(self):
        from hook_utils.memory_bridge import _strip_pm_boilerplate

        assert _strip_pm_boilerplate(None) is None


class TestFormatThoughtBlocks:
    """Test _format_thought_blocks() helper."""

    def test_empty_records(self):
        from hook_utils.memory_bridge import _format_thought_blocks

        thoughts, entries = _format_thought_blocks([])
        assert thoughts == []
        assert entries == []

    def test_formats_records(self):
        from hook_utils.memory_bridge import _format_thought_blocks

        r1 = MagicMock()
        r1.memory_id = "m1"
        r1.content = "first"
        r2 = MagicMock()
        r2.memory_id = "m2"
        r2.content = "second"

        thoughts, entries = _format_thought_blocks([r1, r2])
        assert thoughts == ["<thought>first</thought>", "<thought>second</thought>"]
        assert entries == [
            {"memory_id": "m1", "content": "first"},
            {"memory_id": "m2", "content": "second"},
        ]

    def test_excludes_ids(self):
        from hook_utils.memory_bridge import _format_thought_blocks

        r1 = MagicMock()
        r1.memory_id = "m1"
        r1.content = "first"
        r2 = MagicMock()
        r2.memory_id = "m2"
        r2.content = "second"

        thoughts, entries = _format_thought_blocks([r1, r2], exclude_ids={"m1"})
        assert thoughts == ["<thought>second</thought>"]
        assert entries == [{"memory_id": "m2", "content": "second"}]

    def test_max_results_caps_output(self):
        from hook_utils.memory_bridge import _format_thought_blocks

        records = []
        for i in range(5):
            r = MagicMock()
            r.memory_id = f"m{i}"
            r.content = f"thought_{i}"
            records.append(r)

        thoughts, entries = _format_thought_blocks(records, max_results=2)
        assert len(thoughts) == 2
        assert len(entries) == 2

    def test_skips_empty_content(self):
        from hook_utils.memory_bridge import _format_thought_blocks

        r1 = MagicMock()
        r1.memory_id = "m1"
        r1.content = ""
        r2 = MagicMock()
        r2.memory_id = "m2"
        r2.content = "valid"

        thoughts, entries = _format_thought_blocks([r1, r2])
        assert thoughts == ["<thought>valid</thought>"]


class TestRecallWithQuery:
    """Test the pure-retrieval _recall_with_query() helper."""

    def test_empty_query_returns_empty(self):
        from hook_utils.memory_bridge import _recall_with_query

        result = _recall_with_query("", project_key="test")
        assert result == []

    def test_non_string_query_returns_empty(self):
        from hook_utils.memory_bridge import _recall_with_query

        result = _recall_with_query(None, project_key="test")
        assert result == []

    def test_returns_records_with_bloom_hits(self):
        from hook_utils.memory_bridge import _recall_with_query

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        record = MagicMock()
        record.memory_id = "rec-1"
        record.content = "stored memory"

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("agent.memory_retrieval.retrieve_memories", return_value=[record]),
            patch("utils.keyword_extraction._apply_category_weights", wraps=lambda r: r),
        ):
            result = _recall_with_query("auth deployment migration", project_key="test")
        assert isinstance(result, list)
        assert result == [record]

    def test_zero_bloom_hits_returns_empty_when_dejavu_disabled(self):
        from hook_utils.memory_bridge import _recall_with_query

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with patch("models.memory.Memory", mock_memory_cls):
            result = _recall_with_query(
                "novel words alpha beta gamma delta epsilon zeta eta theta",
                project_key="test",
                bloom_check_emit_dejavu=False,
            )
        assert result == []

    def test_zero_bloom_hits_emits_dejavu_when_enabled(self):
        from hook_utils.memory_bridge import _recall_with_query

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=False)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with patch("models.memory.Memory", mock_memory_cls):
            result = _recall_with_query(
                "novel alpha beta gamma delta epsilon zeta eta theta iota kappa",
                project_key="test",
                bloom_check_emit_dejavu=True,
            )
        assert isinstance(result, str)
        assert "new territory" in result

    def test_exclude_ids_filtered_from_results(self):
        from hook_utils.memory_bridge import _recall_with_query

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        r1 = MagicMock()
        r1.memory_id = "skip-me"
        r1.content = "should be excluded"
        r2 = MagicMock()
        r2.memory_id = "keep-me"
        r2.content = "should be kept"

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("agent.memory_retrieval.retrieve_memories", return_value=[r1, r2]),
            patch("utils.keyword_extraction._apply_category_weights", wraps=lambda r: r),
        ):
            result = _recall_with_query(
                "auth deployment migration",
                project_key="test",
                exclude_ids={"skip-me"},
            )
        assert result == [r2]

    def test_retrieve_memories_exception_returns_empty(self):
        from hook_utils.memory_bridge import _recall_with_query

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch(
                "agent.memory_retrieval.retrieve_memories",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            result = _recall_with_query("auth deployment", project_key="test")
        assert result == []

    def test_bloom_check_disabled_skips_filter(self):
        from hook_utils.memory_bridge import _recall_with_query

        record = MagicMock()
        record.memory_id = "rec-1"
        record.content = "content"

        # When bloom_check=False, Memory is still imported but the bloom
        # field is not consulted. retrieve_memories is called directly.
        mock_memory_cls = MagicMock()

        with (
            patch("models.memory.Memory", mock_memory_cls),
            patch("agent.memory_retrieval.retrieve_memories", return_value=[record]),
            patch("utils.keyword_extraction._apply_category_weights", wraps=lambda r: r),
        ):
            result = _recall_with_query("auth", project_key="test", bloom_check=False)
        assert result == [record]
        # Bloom field was never consulted
        mock_memory_cls._meta.fields.get.assert_not_called()


class TestPrefetch:
    """Test the prefetch() function (UserPromptSubmit path)."""

    def test_prefetch_empty_prompt(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        assert prefetch("sess", "") is None
        assert prefetch("sess", None) is None

    def test_prefetch_short_prompt(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        # Below MIN_PROMPT_LENGTH=50
        assert prefetch("sess", "too short") is None

    def test_prefetch_trivial_prompt(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        # Trivial pattern (after lowercase + strip), padded with spaces to meet length
        assert prefetch("sess", "yes" + " " * 60) is None

    def test_prefetch_no_window_gate(self, tmp_path, monkeypatch):
        """Prefetch fires immediately -- no WINDOW_SIZE gating."""
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        called = []

        def mock_recall_with_query(query, project_key, **kwargs):
            called.append(query)
            return []

        with (
            patch(
                "hook_utils.memory_bridge._recall_with_query",
                side_effect=mock_recall_with_query,
            ),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            # First call should hit retrieval, no buffering
            prefetch("sess", "x" * 80)
            assert len(called) == 1

    def test_prefetch_no_bloom_hits_returns_none(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        with (
            patch("hook_utils.memory_bridge._recall_with_query", return_value=[]),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            result = prefetch("sess", "this is a substantive prompt about auth flow")
        assert result is None

    def test_prefetch_never_emits_dejavu(self, tmp_path, monkeypatch):
        """Prefetch passes bloom_check_emit_dejavu=False to _recall_with_query."""
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        captured_kwargs = {}

        def mock_recall(*, query, project_key, exclude_ids=None, **kwargs):
            captured_kwargs.update(kwargs)
            return []

        with (
            patch(
                "hook_utils.memory_bridge._recall_with_query",
                side_effect=mock_recall,
            ),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            prefetch("sess", "a long enough prompt about authentication flow refactor")

        assert captured_kwargs.get("bloom_check_emit_dejavu") is False

    def test_prefetch_no_retrieval_results(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        with (
            patch("hook_utils.memory_bridge._recall_with_query", return_value=[]),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            result = prefetch("sess", "a substantive prompt with enough words to pass length")
        assert result is None

    def test_prefetch_returns_thoughts(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        record = MagicMock()
        record.memory_id = "m1"
        record.content = "auth flow notes from PR 800"

        with (
            patch("hook_utils.memory_bridge._recall_with_query", return_value=[record]),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            result = prefetch(
                "sess",
                "investigate auth bug that broke after PR 800 deployment",
            )
        assert result is not None
        assert "<thought>auth flow notes from PR 800</thought>" in result

    def test_prefetch_strips_pm_boilerplate(self, tmp_path, monkeypatch):
        """Prefetch strips FROM:/SCOPE:/MESSAGE: prefix before querying."""
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        captured_query = []

        def mock_recall(*, query, project_key, **kwargs):
            captured_query.append(query)
            return []

        with (
            patch(
                "hook_utils.memory_bridge._recall_with_query",
                side_effect=mock_recall,
            ),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            prompt = (
                "FROM: valor-session (dev)\n"
                "SCOPE: This session is scoped to the message below from this sender.\n"
                "MESSAGE: investigate auth bug that broke after PR 800 deployment cycle"
            )
            prefetch("sess", prompt)

        assert len(captured_query) == 1
        assert captured_query[0] == (
            "investigate auth bug that broke after PR 800 deployment cycle"
        )

    def test_prefetch_writes_sidecar_preserving_count_and_buffer(self, tmp_path, monkeypatch):
        """Prefetch must not clobber count or buffer set by recall()."""
        from hook_utils.memory_bridge import _load_sidecar, _save_sidecar, prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        # Simulate prior recall() state
        _save_sidecar(
            "sess",
            {
                "count": 7,
                "buffer": [{"tool_name": "Read", "tool_input": {"file_path": "x"}}],
                "injected": [{"memory_id": "old-1", "content": "old thought"}],
            },
        )

        record = MagicMock()
        record.memory_id = "new-1"
        record.content = "fresh thought"

        with (
            patch("hook_utils.memory_bridge._recall_with_query", return_value=[record]),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            prefetch("sess", "investigate auth bug that broke after PR 800 deployment")

        state = _load_sidecar("sess")
        assert state["count"] == 7
        assert state["buffer"] == [{"tool_name": "Read", "tool_input": {"file_path": "x"}}]
        # injected[] now contains both the old and new entries
        injected_ids = [item["memory_id"] for item in state["injected"]]
        assert "old-1" in injected_ids
        assert "new-1" in injected_ids

    def test_prefetch_excludes_already_injected_ids(self, tmp_path, monkeypatch):
        """Prefetch passes existing sidecar injected[] memory_ids as exclude_ids."""
        from hook_utils.memory_bridge import _save_sidecar, prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        _save_sidecar(
            "sess",
            {
                "count": 0,
                "buffer": [],
                "injected": [{"memory_id": "already-shown", "content": "old"}],
            },
        )

        captured_exclude = []

        def mock_recall(*, query, project_key, exclude_ids=None, **kwargs):
            captured_exclude.append(exclude_ids)
            return []

        with (
            patch(
                "hook_utils.memory_bridge._recall_with_query",
                side_effect=mock_recall,
            ),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            prefetch("sess", "this is a substantive prompt about auth flow refactor")

        assert captured_exclude == [{"already-shown"}]

    def test_prefetch_returns_string_when_sidecar_write_fails(self, tmp_path, monkeypatch):
        """Prefetch still returns thoughts when sidecar save fails."""
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        record = MagicMock()
        record.memory_id = "m1"
        record.content = "valid thought"

        # Make the second _save_sidecar call (the one inside prefetch)
        # raise; the prefetch should still return the formatted thought.
        with (
            patch("hook_utils.memory_bridge._recall_with_query", return_value=[record]),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
            patch(
                "hook_utils.memory_bridge._save_sidecar",
                side_effect=OSError("disk full"),
            ),
        ):
            result = prefetch(
                "sess",
                "investigate auth bug that broke after PR 800 deployment",
            )
        assert result is not None
        assert "<thought>valid thought</thought>" in result

    def test_prefetch_logs_warning_when_slow(self, tmp_path, monkeypatch, caplog):
        """When elapsed exceeds PREFETCH_LATENCY_WARN_MS, a warning is logged."""
        import logging

        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        # Force time.monotonic() to advance by 500ms between start and end
        # of the _recall_with_query call inside prefetch().
        timestamps = iter([0.0, 0.5])  # 500ms elapsed

        def fake_monotonic():
            try:
                return next(timestamps)
            except StopIteration:
                return 0.5

        with (
            patch("hook_utils.memory_bridge.time.monotonic", side_effect=fake_monotonic),
            patch("hook_utils.memory_bridge._recall_with_query", return_value=[]),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
            caplog.at_level(logging.WARNING, logger="hook_utils.memory_bridge"),
        ):
            prefetch("sess", "a long enough prompt about authentication flow refactor")

        # Look for the latency warning in caplog
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("prefetch took" in m for m in warning_messages)

    def test_prefetch_returns_none_on_recall_with_query_string(self, tmp_path, monkeypatch):
        """If _recall_with_query returns a string (deja vu), prefetch returns None."""
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        with (
            patch(
                "hook_utils.memory_bridge._recall_with_query",
                return_value="<thought>some deja vu</thought>",
            ),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            result = prefetch(
                "sess",
                "investigate auth bug that broke after PR 800 deployment",
            )
        assert result is None

    def test_prefetch_returns_none_on_exception(self, tmp_path, monkeypatch):
        from hook_utils.memory_bridge import prefetch

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        with patch(
            "hook_utils.memory_bridge._recall_with_query",
            side_effect=RuntimeError("boom"),
        ):
            result = prefetch(
                "sess",
                "investigate auth bug that broke after PR 800 deployment",
            )
        assert result is None


class TestRecallSidecarExclude:
    """Test that recall() honors sidecar injected[] for de-dup."""

    def test_recall_excludes_already_injected_ids(self, tmp_path, monkeypatch):
        """When the sidecar has injected[] entries, recall skips those memory_ids."""
        from hook_utils.memory_bridge import WINDOW_SIZE, _save_sidecar, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

        # Pre-seed sidecar with already-injected memory_id
        _save_sidecar(
            "sess",
            {
                "count": 0,
                "buffer": [],
                "injected": [{"memory_id": "skip-me", "content": "prefetched"}],
            },
        )

        keywords = ["memory", "recall", "weights", "category", "test"]

        mock_bloom = MagicMock()
        mock_bloom.might_exist = MagicMock(return_value=True)
        mock_memory_cls = MagicMock()
        mock_memory_cls._meta.fields.get.return_value = mock_bloom

        skip_me = MagicMock()
        skip_me.memory_id = "skip-me"
        skip_me.content = "should be excluded"
        keep_me = MagicMock()
        keep_me.memory_id = "keep-me"
        keep_me.content = "should be kept"

        with (
            patch("utils.keyword_extraction.extract_topic_keywords", return_value=keywords),
            patch("models.memory.Memory", mock_memory_cls),
            patch("agent.memory_retrieval.retrieve_memories", return_value=[skip_me, keep_me]),
            patch("utils.keyword_extraction._apply_category_weights", wraps=lambda r: r),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("sess", "Read", {"file_path": f"f{i}.py"})
            result = recall("sess", "Read", {"file_path": "final.py"})

        assert result is not None
        # The prefetched (skip-me) record must NOT appear; only keep-me
        assert "should be kept" in result
        assert "should be excluded" not in result
