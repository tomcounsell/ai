"""Unit tests for tools.memory_search.title_generator.

Mocks Ollama HTTP and the Memory model to verify:
  - successful flow writes title back to the record
  - silent failure on Ollama down / timeout / non-JSON
  - empty content / empty memory_id no-ops
  - whitespace / quotes / trailing periods normalized off the response

Tests exercise the synchronous worker body ``_do_generate`` directly so
they are immune to daemon-thread lifecycle (other tests in the suite
may have lingering ``memory-title-gen-*`` threads from real Ollama
calls).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestNormalizeTitle:
    def test_strips_quotes(self):
        from tools.memory_search.title_generator import _normalize_title

        assert _normalize_title('"My Title"') == "My Title"
        assert _normalize_title("'My Title'") == "My Title"

    def test_strips_trailing_period(self):
        from tools.memory_search.title_generator import _normalize_title

        assert _normalize_title("My title.") == "My title"
        assert _normalize_title("My title...") == "My title"

    def test_collapses_whitespace(self):
        from tools.memory_search.title_generator import _normalize_title

        assert _normalize_title("  multi   spaced   title  ") == "multi spaced title"

    def test_empty(self):
        from tools.memory_search.title_generator import _normalize_title

        assert _normalize_title("") == ""
        assert _normalize_title("   ") == ""


class TestGenerateTitleAsyncSpawn:
    """Public API: returns synchronously, spawns a daemon thread (or no-ops)."""

    def test_no_op_on_empty_inputs(self):
        from tools.memory_search.title_generator import generate_title_async

        # Should not raise and should not call _post_ollama_generate.
        with patch("tools.memory_search.title_generator._post_ollama_generate") as mock_post:
            generate_title_async("", "content")
            generate_title_async("mid", "")
            generate_title_async("", "")
        assert mock_post.call_count == 0

    def test_returns_synchronously_on_valid_input(self):
        """The function returns before the daemon thread completes."""
        from tools.memory_search.title_generator import generate_title_async

        # Even with no patches, the call should return without raising.
        result = generate_title_async("test-mem", "test content")
        assert result is None  # fire-and-forget contract


class TestDoGenerate:
    """Synchronous worker body — exercises the full save path deterministically."""

    def _patched_memory(self, record):
        memory_cls = MagicMock()
        memory_cls.query.filter.return_value.first.return_value = record
        return MagicMock(Memory=memory_cls)

    def test_writes_title_on_success(self):
        from tools.memory_search.title_generator import _do_generate

        record = MagicMock()
        record.title = ""
        models_mock = self._patched_memory(record)

        with (
            patch(
                "tools.memory_search.title_generator._post_ollama_generate",
                return_value="Concise label",
            ),
            patch.dict("sys.modules", {"models.memory": models_mock}),
        ):
            _do_generate("mem-1", "some content to title")

        assert record.title == "Concise label"
        record.save.assert_called_once()

    def test_silent_on_ollama_failure(self):
        from tools.memory_search.title_generator import _do_generate

        record = MagicMock()
        record.title = "prior"
        models_mock = self._patched_memory(record)

        with (
            patch(
                "tools.memory_search.title_generator._post_ollama_generate",
                return_value=None,
            ),
            patch.dict("sys.modules", {"models.memory": models_mock}),
        ):
            _do_generate("mem-1", "content")

        # No save when Ollama returns None — title left unchanged.
        assert record.title == "prior"
        record.save.assert_not_called()

    def test_silent_on_empty_normalized_title(self):
        from tools.memory_search.title_generator import _do_generate

        record = MagicMock()
        record.title = "prior"
        models_mock = self._patched_memory(record)

        # LLM returned only whitespace/quotes — normalizes to empty.
        with (
            patch(
                "tools.memory_search.title_generator._post_ollama_generate",
                return_value='   "   "  ',
            ),
            patch.dict("sys.modules", {"models.memory": models_mock}),
        ):
            _do_generate("mem-1", "content")

        record.save.assert_not_called()

    def test_silent_on_record_not_found(self):
        from tools.memory_search.title_generator import _do_generate

        memory_cls = MagicMock()
        memory_cls.query.filter.return_value.first.return_value = None
        models_mock = MagicMock(Memory=memory_cls)

        with (
            patch(
                "tools.memory_search.title_generator._post_ollama_generate",
                return_value="some title",
            ),
            patch.dict("sys.modules", {"models.memory": models_mock}),
        ):
            # Should not raise.
            _do_generate("missing-id", "content")

    def test_no_op_on_empty_inputs_in_worker(self):
        from tools.memory_search.title_generator import _do_generate

        with patch("tools.memory_search.title_generator._post_ollama_generate") as mock_post:
            _do_generate("", "content")
            _do_generate("mid", "")
        assert mock_post.call_count == 0
