"""End-to-end watcher → converter → indexer integration tests.

Exercises the watcher's debounced event loop against real filesystem
writes. The indexer is stubbed via ``monkeypatch`` to decouple from
Redis/Popoto availability — we assert that:

1. Dropping a PDF into the vault produces a ``.pdf.md`` sidecar (via the
   converter) in the same ``_flush`` iteration, and that sidecar is
   handed to ``index_file``.
2. A pre-existing ``weird.pdf.md.md`` file does NOT trigger infinite
   conversion (loop-prevention via the ``.md`` short-circuit in the
   converter).
3. Dropping an existing ``.md`` goes straight to ``index_file`` with no
   converter invocation.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"


pytestmark = pytest.mark.integration


@pytest.fixture
def stubbed_indexer(monkeypatch):
    """Replace tools.knowledge.indexer with a MagicMock stub."""
    calls = {"index": [], "delete": []}
    stub = types.ModuleType("tools.knowledge.indexer")
    stub.index_file = lambda p: calls["index"].append(p) or True
    stub.delete_file = lambda p: calls["delete"].append(p) or True
    monkeypatch.setitem(sys.modules, "tools.knowledge.indexer", stub)
    return calls


def _wait_for_flush(timeout: float = 6.0, poll: float = 0.1) -> None:
    """Wait for the debounce timer (2s) + processing time."""
    time.sleep(3.0)


def test_pdf_dropped_into_vault_produces_sidecar(tmp_path: Path, stubbed_indexer):
    """Drop a PDF into a watched dir → sidecar materializes, indexer invoked."""
    from bridge.knowledge_watcher import KnowledgeWatcher

    pdf_fixture = FIXTURE_DIR / "sample.pdf"
    assert pdf_fixture.exists(), "tests/fixtures/sample.pdf is missing"

    watcher = KnowledgeWatcher(vault_path=str(tmp_path))
    assert watcher.start() is True
    try:
        time.sleep(0.3)  # let watchdog wire up
        target = tmp_path / "sample.pdf"
        target.write_bytes(pdf_fixture.read_bytes())
        _wait_for_flush()

        sidecar = target.with_name(target.name + ".md")
        assert sidecar.exists(), (
            f"watcher did not produce sidecar; vault contents: "
            f"{sorted(p.name for p in tmp_path.iterdir())}"
        )
        assert stubbed_indexer["index"], "index_file was never called on the sidecar"
        assert stubbed_indexer["index"][0] == str(sidecar)
    finally:
        watcher.stop()


def test_md_md_file_does_not_loop(tmp_path: Path, stubbed_indexer):
    """A pre-existing `weird.pdf.md.md` must not trigger recursive conversion."""
    from bridge.knowledge_watcher import KnowledgeWatcher

    watcher = KnowledgeWatcher(vault_path=str(tmp_path))
    assert watcher.start() is True
    try:
        time.sleep(0.3)
        stub = tmp_path / "weird.pdf.md.md"
        stub.write_text(
            "---\ngenerated_by: markitdown\n---\nstub body\n",
            encoding="utf-8",
        )
        _wait_for_flush()

        # Loop-prevention: no `weird.pdf.md.md.md` should exist.
        for p in tmp_path.iterdir():
            assert not p.name.endswith(".md.md.md"), f"loop triggered: {p.name}"
        # The .md file gets indexed as-is (SUPPORTED_EXTENSIONS path).
        assert any(call.endswith("weird.pdf.md.md") for call in stubbed_indexer["index"])
    finally:
        watcher.stop()


def test_plain_md_still_routed_to_indexer(tmp_path: Path, stubbed_indexer):
    """Hand-written .md files still flow to index_file (no converter needed)."""
    from bridge.knowledge_watcher import KnowledgeWatcher

    watcher = KnowledgeWatcher(vault_path=str(tmp_path))
    assert watcher.start() is True
    try:
        time.sleep(0.3)
        note = tmp_path / "note.md"
        note.write_text("# Hello\n", encoding="utf-8")
        _wait_for_flush()
        assert any(call == str(note) for call in stubbed_indexer["index"])
        # No sidecar for a pre-existing .md
        assert not (tmp_path / "note.md.md").exists()
    finally:
        watcher.stop()
