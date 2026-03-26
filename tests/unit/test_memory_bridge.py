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
        with patch("agent.memory_hook.extract_topic_keywords", return_value=[]):
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
            "agent.memory_hook.extract_topic_keywords",
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
                "agent.memory_hook.extract_topic_keywords",
                return_value=keywords,
            ),
            patch("models.memory.Memory", mock_memory_cls),
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("test-session", "Read", {"file_path": f"f{i}.py"})
            result = recall("test-session", "Read", {"file_path": "final.py"})

        assert result is not None
        assert "new territory" in result

    def test_recall_deja_vu_signal(self, tmp_path, monkeypatch):
        """Returns deja vu thought when bloom hits but no strong results."""
        from hook_utils.memory_bridge import DEJA_VU_BLOOM_HIT_THRESHOLD, WINDOW_SIZE, recall

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )

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
            patch(
                "agent.memory_hook.extract_topic_keywords",
                return_value=keywords,
            ),
            patch("models.memory.Memory", mock_memory_cls),
            patch("popoto.ContextAssembler", mock_assembler_cls),
            patch("hook_utils.memory_bridge._get_project_key", return_value="test"),
        ):
            for i in range(WINDOW_SIZE - 1):
                recall("test-session", "Read", {"file_path": f"f{i}.py"})
            result = recall("test-session", "Read", {"file_path": "final.py"})

        assert result is not None
        assert "encountered something related" in result


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
        data = {"agent_session_job_id": "job-123", "merge_detected": True}
        (sidecar_dir / "agent_session.json").write_text(json.dumps(data))

        result = load_agent_session_sidecar("test-session")
        assert result["agent_session_job_id"] == "job-123"
        assert result["merge_detected"] is True

    def test_save_atomic(self, tmp_path, monkeypatch):
        """Saves agent session sidecar file atomically."""
        from hook_utils.memory_bridge import load_agent_session_sidecar, save_agent_session_sidecar

        monkeypatch.setattr(
            "hook_utils.memory_bridge._get_sidecar_dir",
            lambda sid: tmp_path / sid,
        )
        data = {"agent_session_job_id": "job-456"}
        save_agent_session_sidecar("test-session", data)

        # No tmp file left
        sidecar_dir = tmp_path / "test-session"
        assert (sidecar_dir / "agent_session.json").exists()
        assert not (sidecar_dir / "agent_session.json.tmp").exists()

        # Round-trip
        loaded = load_agent_session_sidecar("test-session")
        assert loaded["agent_session_job_id"] == "job-456"

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
