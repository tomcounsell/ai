"""Tests for the knowledge filesystem watcher."""

import os
import time

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
        """Handler rejects .pdf files."""
        from bridge.knowledge_watcher import _DebouncedHandler

        handler = _DebouncedHandler()
        assert handler._is_relevant("/path/to/doc.pdf") is False

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
