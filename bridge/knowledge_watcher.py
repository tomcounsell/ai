"""Filesystem watcher for knowledge document indexing.

Monitors ~/work-vault/ for file changes using watchdog, with 2-second
debouncing to batch rapid saves. Runs as a thread inside the bridge process.

All exceptions are caught -- a crash in the watcher thread must never
take down the bridge.
"""

import logging
import os
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

# Supported file extensions (must match indexer)
SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown", ".text"}

# Debounce delay in seconds
DEBOUNCE_SECONDS = 2.0


class _DebouncedHandler(FileSystemEventHandler):
    """Watchdog event handler with 2-second debounce.

    Collects file change events and batch-processes unique paths
    after a debounce delay.
    """

    def __init__(self):
        super().__init__()
        self._pending_paths: set[str] = set()
        self._pending_deletes: set[str] = set()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _is_relevant(self, path: str) -> bool:
        """Check if a file event is relevant for indexing."""
        ext = os.path.splitext(path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return False
        # Skip hidden files/dirs
        parts = Path(path).parts
        for part in parts:
            if part.startswith(".") and part != ".":
                return False
            if part.startswith("_") and part.endswith("_"):
                return False
        return True

    def on_created(self, event):
        if not event.is_directory and self._is_relevant(event.src_path):
            self._schedule(event.src_path, is_delete=False)

    def on_modified(self, event):
        if not event.is_directory and self._is_relevant(event.src_path):
            self._schedule(event.src_path, is_delete=False)

    def on_deleted(self, event):
        if not event.is_directory and self._is_relevant(event.src_path):
            self._schedule(event.src_path, is_delete=True)

    def on_moved(self, event):
        if not event.is_directory:
            if self._is_relevant(event.src_path):
                self._schedule(event.src_path, is_delete=True)
            if self._is_relevant(event.dest_path):
                self._schedule(event.dest_path, is_delete=False)

    def _schedule(self, path: str, is_delete: bool) -> None:
        """Schedule a path for processing after debounce delay."""
        with self._lock:
            if is_delete:
                self._pending_deletes.add(path)
                self._pending_paths.discard(path)
            else:
                self._pending_paths.add(path)
                self._pending_deletes.discard(path)

            # Reset debounce timer
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        """Process all pending file events."""
        with self._lock:
            paths = self._pending_paths.copy()
            deletes = self._pending_deletes.copy()
            self._pending_paths.clear()
            self._pending_deletes.clear()

        # Process outside the lock
        for path in deletes:
            try:
                from tools.knowledge.indexer import delete_file

                delete_file(path)
            except Exception as e:
                logger.warning(f"Knowledge watcher: delete failed for {path}: {e}")

        for path in paths:
            try:
                from tools.knowledge.indexer import index_file

                index_file(path)
            except Exception as e:
                logger.warning(f"Knowledge watcher: index failed for {path}: {e}")

        total = len(paths) + len(deletes)
        if total > 0:
            logger.info(
                f"Knowledge watcher: processed {len(paths)} changes, {len(deletes)} deletes"
            )


class KnowledgeWatcher:
    """Filesystem watcher for the work-vault knowledge base.

    Wraps a watchdog Observer to monitor ~/work-vault/ for file changes.
    Includes debouncing, startup full scan, and health checking.

    Usage:
        watcher = KnowledgeWatcher()
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(self, vault_path: str | None = None):
        """Initialize the knowledge watcher.

        Args:
            vault_path: Path to monitor. Defaults to ~/work-vault.
        """
        if vault_path is None:
            vault_path = os.path.expanduser("~/work-vault")
        self._vault_path = os.path.normpath(vault_path)
        self._observer: Observer | None = None
        self._handler = _DebouncedHandler()
        self._started = False

    def start(self) -> bool:
        """Start the filesystem watcher and run a full scan.

        Starts watchdog first (to capture events during scan),
        then runs full_scan to catch up on changes missed while
        the bridge was down.

        Returns True if started successfully, False otherwise.
        """
        try:
            if not os.path.isdir(self._vault_path):
                logger.warning(f"Knowledge watcher: vault path not found: {self._vault_path}")
                return False

            # Start watchdog observer
            self._observer = Observer()
            self._observer.schedule(self._handler, self._vault_path, recursive=True)
            self._observer.daemon = True
            self._observer.start()
            self._started = True

            logger.info(f"Knowledge watcher started, monitoring: {self._vault_path}")

            # Run full scan in a background thread to avoid blocking bridge startup
            scan_thread = threading.Thread(
                target=self._background_scan, daemon=True, name="knowledge-scan"
            )
            scan_thread.start()

            return True

        except Exception as e:
            logger.error(f"Knowledge watcher: failed to start: {e}")
            self._started = False
            return False

    def _background_scan(self) -> None:
        """Run full scan in background thread."""
        try:
            from tools.knowledge.indexer import full_scan

            stats = full_scan(self._vault_path)
            logger.info(
                f"Knowledge watcher: initial scan complete - "
                f"{stats.get('indexed', 0)} indexed, "
                f"{stats.get('skipped', 0)} unchanged"
            )
        except Exception as e:
            logger.warning(f"Knowledge watcher: initial scan failed: {e}")

    def stop(self) -> None:
        """Stop the filesystem watcher."""
        try:
            if self._observer is not None:
                self._observer.stop()
                self._observer.join(timeout=5)
                self._observer = None
            self._started = False
            logger.info("Knowledge watcher stopped")
        except Exception as e:
            logger.warning(f"Knowledge watcher: error during stop: {e}")
            self._started = False

    def is_healthy(self) -> bool:
        """Check if the watcher is running and healthy.

        Returns True if the observer thread is alive.
        """
        if not self._started:
            return False
        if self._observer is None:
            return False
        return self._observer.is_alive()

    @property
    def vault_path(self) -> str:
        """Return the monitored vault path."""
        return self._vault_path
