"""Tests for the knowledge filesystem watcher."""

import os
import time
import types
from pathlib import Path

import pytest


@pytest.mark.unit
class TestKnowledgeWatcher:
    """Test the KnowledgeWatcher class."""

    def test_watcher_importable(self):
        """KnowledgeWatcher can be imported."""
        from bridge.knowledge_watcher import KnowledgeWatcher

        assert KnowledgeWatcher is not None

    def test_watcher_init_default_path(self):
        """Watcher defaults to ~/work-vault."""
        from bridge.knowledge_watcher import KnowledgeWatcher

        watcher = KnowledgeWatcher()
        assert "work-vault" in watcher.vault_path

    def test_watcher_init_custom_path(self, tmp_path):
        """Watcher accepts custom vault path."""
        from bridge.knowledge_watcher import KnowledgeWatcher

        watcher = KnowledgeWatcher(vault_path=str(tmp_path))
        assert watcher.vault_path == os.path.normpath(str(tmp_path))

    def test_watcher_not_healthy_before_start(self):
        """Watcher reports unhealthy before start."""
        from bridge.knowledge_watcher import KnowledgeWatcher

        watcher = KnowledgeWatcher()
        assert watcher.is_healthy() is False

    def test_watcher_start_nonexistent_path(self):
        """Watcher returns False when path doesn't exist."""
        from bridge.knowledge_watcher import KnowledgeWatcher

        watcher = KnowledgeWatcher(vault_path="/nonexistent/path")
        assert watcher.start() is False

    def test_watcher_start_stop_lifecycle(self, tmp_path):
        """Watcher can start and stop cleanly."""
        from bridge.knowledge_watcher import KnowledgeWatcher

        watcher = KnowledgeWatcher(vault_path=str(tmp_path))
        started = watcher.start()
        assert started is True
        assert watcher.is_healthy() is True

        watcher.stop()
        # Give thread time to stop
        time.sleep(0.2)
        assert watcher.is_healthy() is False

    def test_watcher_double_stop(self, tmp_path):
        """Calling stop twice doesn't crash."""
        from bridge.knowledge_watcher import KnowledgeWatcher

        watcher = KnowledgeWatcher(vault_path=str(tmp_path))
        watcher.start()
        watcher.stop()
        watcher.stop()  # Should not raise


@pytest.mark.unit
class TestDebouncedHandler:
    """Test the debounced event handler."""

    def test_handler_importable(self):
        """DebouncedHandler can be imported."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        assert handler is not None

    def test_handler_relevance_md(self):
        """Handler considers .md files relevant."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        assert handler._is_relevant("/path/to/doc.md") is True

    def test_handler_relevance_txt(self):
        """Handler considers .txt files relevant."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        assert handler._is_relevant("/path/to/notes.txt") is True

    def test_handler_relevance_pdf(self):
        """Handler accepts .pdf files (routed through converter in _flush)."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        assert handler._is_relevant("/path/to/doc.pdf") is True

    def test_handler_relevance_image(self):
        """Handler accepts image extensions (per C4 Implementation Note)."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            assert handler._is_relevant(f"/path/to/img{ext}") is True, ext

    def test_handler_rejects_audio(self):
        """Handler rejects audio extensions (privacy disqualifier per spike-2)."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        for ext in (".mp3", ".wav", ".m4a"):
            assert handler._is_relevant(f"/path/to/clip{ext}") is False, ext

    def test_handler_convertible_set_excludes_audio(self):
        """Audio formats are not in CONVERTIBLE_EXTENSIONS (plan spike-2)."""
        from bridge.knowledge_watcher import CONVERTIBLE_EXTENSIONS

        for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg"):
            assert ext not in CONVERTIBLE_EXTENSIONS, ext

    def test_flush_routes_convertible_through_converter(self, tmp_path, monkeypatch):
        """_flush must call convert_to_sidecar for convertible extensions, then
        index_file on the resulting sidecar — same iteration (C3)."""
        from bridge import knowledge_watcher as kw
        from bridge.knowledge_watcher import _DebouncedHandler

        pdf_source = tmp_path / "doc.pdf"
        pdf_source.write_bytes(b"%PDF-1.4 stub")
        fake_sidecar = tmp_path / "doc.pdf.md"
        fake_sidecar.write_text("---\nsource_hash: x\n---\nbody")

        calls = {"convert": [], "index": []}

        def fake_convert(source, *, force=False):
            calls["convert"].append(Path(source).resolve())
            return fake_sidecar

        def fake_index(path):
            calls["index"].append(path)

        import sys

        converter_mod = types.ModuleType("tools.knowledge.converter")
        converter_mod.convert_to_sidecar = fake_convert
        converter_mod.CONVERTIBLE_EXTENSIONS = kw.CONVERTIBLE_EXTENSIONS
        indexer_mod = types.ModuleType("tools.knowledge.indexer")
        indexer_mod.index_file = fake_index
        indexer_mod.delete_file = lambda p: None
        monkeypatch.setitem(sys.modules, "tools.knowledge.converter", converter_mod)
        monkeypatch.setitem(sys.modules, "tools.knowledge.indexer", indexer_mod)

        handler = _DebouncedHandler()
        handler._pending_paths.add(str(pdf_source))
        handler._flush()

        assert len(calls["convert"]) == 1
        assert calls["convert"][0] == pdf_source.resolve()
        assert calls["index"] == [str(fake_sidecar)]

    def test_flush_routes_md_to_indexer_directly(self, tmp_path, monkeypatch):
        """Pre-existing .md path still goes straight to indexer (no converter)."""
        from bridge.knowledge_watcher import _DebouncedHandler

        md = tmp_path / "note.md"
        md.write_text("hello")
        indexed = []
        converted = []

        import sys

        converter_mod = types.ModuleType("tools.knowledge.converter")
        converter_mod.convert_to_sidecar = lambda *a, **kw: converted.append(a) or None
        from bridge.knowledge_watcher import CONVERTIBLE_EXTENSIONS

        converter_mod.CONVERTIBLE_EXTENSIONS = CONVERTIBLE_EXTENSIONS
        indexer_mod = types.ModuleType("tools.knowledge.indexer")
        indexer_mod.index_file = lambda p: indexed.append(p)
        indexer_mod.delete_file = lambda p: None
        monkeypatch.setitem(sys.modules, "tools.knowledge.converter", converter_mod)
        monkeypatch.setitem(sys.modules, "tools.knowledge.indexer", indexer_mod)

        handler = _DebouncedHandler()
        handler._pending_paths.add(str(md))
        handler._flush()

        assert indexed == [str(md)]
        assert converted == []

    def test_flush_survives_converter_exception(self, tmp_path, monkeypatch, caplog):
        """A converter raise must not propagate out of _flush (crash-isolation)."""
        from bridge.knowledge_watcher import _DebouncedHandler

        pdf = tmp_path / "broken.pdf"
        pdf.write_bytes(b"not a real pdf")

        import sys

        converter_mod = types.ModuleType("tools.knowledge.converter")

        def boom(*a, **kw):
            raise RuntimeError("simulated converter crash")

        converter_mod.convert_to_sidecar = boom
        from bridge.knowledge_watcher import CONVERTIBLE_EXTENSIONS

        converter_mod.CONVERTIBLE_EXTENSIONS = CONVERTIBLE_EXTENSIONS
        indexer_mod = types.ModuleType("tools.knowledge.indexer")
        indexer_mod.index_file = lambda p: None
        indexer_mod.delete_file = lambda p: None
        monkeypatch.setitem(sys.modules, "tools.knowledge.converter", converter_mod)
        monkeypatch.setitem(sys.modules, "tools.knowledge.indexer", indexer_mod)

        handler = _DebouncedHandler()
        handler._pending_paths.add(str(pdf))
        # Must not raise.
        with caplog.at_level("WARNING"):
            handler._flush()
        assert any("convert failed" in r.message for r in caplog.records)

    def test_handler_relevance_hidden(self):
        """Handler rejects hidden files."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        assert handler._is_relevant("/path/.hidden/doc.md") is False

    def test_handler_relevance_archived(self):
        """Handler rejects archived files."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        assert handler._is_relevant("/path/_archive_/doc.md") is False
