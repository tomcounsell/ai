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

import threading
from unittest.mock import MagicMock, patch

import pytest


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
        """The function returns before the daemon thread completes.

        Patch the worker body itself so the spawned daemon does NO real work
        (no network call, no Memory.save). Otherwise the generation model is
        now reachable on signed-in cloud hosts, and the daemon would race past
        the torn-down patch and leak a save() into a later test's patched
        models.memory mock.
        """
        from tools.memory_search import title_generator

        done = threading.Event()

        with patch.object(title_generator, "_do_generate", side_effect=lambda *a, **k: done.set()):
            result = title_generator.generate_title_async("test-mem", "test content")
            assert result is None  # fire-and-forget contract
            assert done.wait(timeout=2.0)  # daemon ran the (no-op) body and finished


class TestDoGenerate:
    """Synchronous worker body — exercises the full save path deterministically."""

    @pytest.fixture(autouse=True)
    def _generation_available(self):
        """Treat the generation model as available so these tests are hermetic.

        ``_do_generate`` now consults ``ensure_generation_model`` before the HTTP
        call; pin it True here so the save-path tests don't depend on the host's
        Ollama Cloud signin state.
        """
        with patch("config.models.ensure_generation_model", return_value=(True, "ok")):
            yield

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


class TestGenerationModelConfig:
    """The generation model id comes from settings, default gemma4:31b-cloud."""

    def test_resolve_uses_generation_setting_default(self, monkeypatch):
        from config.settings import ModelSettings, settings
        from tools.memory_search.title_generator import _resolve_ollama_config

        # The `settings` singleton reflects the machine-local env override
        # MODELS__OLLAMA_GENERATION_MODEL (written to ~/.zshenv by /setup).
        # Pin the singleton to the code default so this test asserts BOTH
        # that the default is gemma4:31b-cloud AND that _resolve wires it
        # through settings.models — independent of the host machine.
        default_model = ModelSettings().ollama_generation_model
        assert default_model == "gemma4:31b-cloud"
        monkeypatch.setattr(settings.models, "ollama_generation_model", default_model)

        _base, model, _timeout = _resolve_ollama_config()
        assert model == "gemma4:31b-cloud"


class TestDefensivePrivateStrip:
    """Cloud is the default generation target — _do_generate must never egress
    raw <private> content even if a caller forgot to strip it."""

    def test_strips_private_before_truncation(self):
        from tools.memory_search.title_generator import _do_generate

        captured = {}

        def _capture(base, model, prompt, timeout):
            captured["prompt"] = prompt
            return "A Title"

        with (
            patch("config.models.ensure_generation_model", return_value=(True, "ok")),
            patch("agent.private_tag.strip_private", side_effect=lambda s: "CLEAN CONTENT"),
            patch(
                "tools.memory_search.title_generator._post_ollama_generate",
                side_effect=_capture,
            ),
            patch.dict(
                "sys.modules",
                {"models.memory": MagicMock(Memory=MagicMock())},
            ),
        ):
            _do_generate("mid", "leading <private>SECRET</private> trailing")

        assert "SECRET" not in captured.get("prompt", "")
        assert "CLEAN CONTENT" in captured.get("prompt", "")

    def test_aborts_on_unmatched_private_opener(self):
        from tools.memory_search.title_generator import _do_generate

        with (
            patch("config.models.ensure_generation_model", return_value=(True, "ok")),
            patch("agent.private_tag.strip_private", side_effect=lambda s: s),
            patch(
                "tools.memory_search.title_generator._post_ollama_generate",
            ) as mock_post,
        ):
            _do_generate("mid", "text <private>leak with no close tag")

        mock_post.assert_not_called()


class TestGenerationUnavailableSkips:
    """Typed signal: title-gen skips persistence when the model is unavailable."""

    def test_skips_when_generation_unavailable(self):
        from tools.memory_search.title_generator import _do_generate

        with (
            patch("config.models.ensure_generation_model", return_value=(False, "down")),
            patch(
                "tools.memory_search.title_generator._post_ollama_generate",
            ) as mock_post,
        ):
            _do_generate("mid", "some content")

        mock_post.assert_not_called()
