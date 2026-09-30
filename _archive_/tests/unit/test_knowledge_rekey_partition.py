"""Regression tests for cross-project partition on a knowledge-doc re-key (#3210).

``KnowledgeDocument.project_key``, ``DocumentChunk.project_key`` and
``Memory.project_key`` are all ``KeyField``s, and each model is searched on
its *own* key: ``DocumentChunk.search()`` filters on the chunk's key without
consulting its parent document. ``tools.knowledge.indexer.index_file`` writes
all three from one ``resolve_scope()`` result, so the three must move together
or one file ends up answering two projects' searches.

The re-key is ordinary configuration maintenance: ``resolve_scope`` is a pure
longest-prefix path map read from ``projects.json``, so adding a project whose
``knowledge_base`` is a subfolder of an already-indexed root re-keys every
file beneath it.

These tests drive the real ``index_file`` against real Popoto rows (autouse
``redis_test_db`` isolation) and assert the *partition* after a divergent
re-index -- that no document, chunk or companion memory for the path is left
behind under the old project key -- rather than merely that the code path ran.
Both halves matter and fail differently:

* unchanged content, new key -- the content-hash skip means chunks are not
  resynced at all, so they stay on the old key while the memories are deleted
  and recreated on the new one;
* changed content, new key -- the chunks follow whichever key the caller
  passes down, which must be the document's persisted key, not the local one.

The embedding provider is stubbed (deterministic vectors, no network) and
``_summarize_content`` is stubbed so companion-memory creation makes no LLM
call; everything else is the real pipeline.
"""

from __future__ import annotations

import pytest

from models.document_chunk import DocumentChunk
from models.knowledge_document import KnowledgeDocument
from models.memory import SOURCE_KNOWLEDGE, Memory
from tools.knowledge.indexer import _make_reference, delete_file, index_file

OLD_KEY = "test-3210-old"
NEW_KEY = "test-3210-new"

_BODY = """# Partition Fixture

This document exists to prove that a knowledge document, its chunks and its
companion memories all live under exactly one project key at a time.

## Second Section

Enough prose here to produce at least one chunk from the chunker without
tripping the large-document heading split.
"""

_CHANGED_BODY = _BODY + "\n## Third Section\n\nAppended text forces a new content hash.\n"


@pytest.fixture
def stub_embeddings():
    """Deterministic in-process embedding provider -- no network."""
    from popoto.embeddings import AbstractEmbeddingProvider
    from popoto.fields.embedding_field import (
        get_default_provider,
        invalidate_cache,
        set_default_provider,
    )

    class _FixedProvider(AbstractEmbeddingProvider):
        def embed(self, texts, input_type=None):
            return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

        @property
        def dimensions(self):
            return 4

        @property
        def max_batch_size(self):
            return 32

    prior = get_default_provider()
    set_default_provider(_FixedProvider())
    invalidate_cache()
    try:
        yield
    finally:
        set_default_provider(prior)
        invalidate_cache()


@pytest.fixture
def doc_file(tmp_path, monkeypatch, stub_embeddings):
    """A real file on disk, indexed through the real pipeline, with the scope
    resolver and the summarizer under test control.
    """
    path = tmp_path / "partition-fixture.md"
    path.write_text(_BODY, encoding="utf-8")

    monkeypatch.setattr(
        "tools.knowledge.indexer._summarize_content",
        lambda content, file_path: "stub summary",
    )
    try:
        yield str(path)
    finally:
        delete_file(str(path))


def _set_scope(monkeypatch, project_key: str, scope: str = "client") -> None:
    monkeypatch.setattr(
        "tools.knowledge.scope_resolver.resolve_scope",
        lambda fp: (project_key, scope),
    )


def _partition(abs_path: str) -> tuple[set[str], set[str], set[str]]:
    """Return (doc keys, chunk keys, companion-memory keys) for a file path."""
    doc_keys = {d.project_key for d in KnowledgeDocument.query.filter(file_path=abs_path)}
    chunk_keys = {c.project_key for c in DocumentChunk.query.filter(file_path=abs_path)}
    reference = _make_reference(abs_path)
    mem_keys = {
        m.project_key
        for m in Memory.query.filter(source=SOURCE_KNOWLEDGE)
        if m.reference == reference
    }
    return doc_keys, chunk_keys, mem_keys


@pytest.mark.unit
class TestRekeyPartition:
    def test_initial_index_partitions_everything_under_one_key(self, doc_file, monkeypatch):
        """Baseline: doc, chunks and memories all land on the resolved key."""
        _set_scope(monkeypatch, OLD_KEY)
        assert index_file(doc_file) is True

        doc_keys, chunk_keys, mem_keys = _partition(doc_file)
        assert doc_keys == {OLD_KEY}
        assert chunk_keys == {OLD_KEY}, "chunks must exist and carry the resolved key"
        assert mem_keys == {OLD_KEY}, "companion memories must carry the resolved key"

    def test_rekey_with_unchanged_content_moves_chunks_and_memories(self, doc_file, monkeypatch):
        """A projects.json re-map with byte-identical content must move all three.

        The content-hash skip means nothing about the *content* changed, but
        the row identity did. Leaving the chunks behind on the old key is the
        cross-project leak: the old project's chunk search still answers with
        this document's text.
        """
        _set_scope(monkeypatch, OLD_KEY)
        assert index_file(doc_file) is True

        _set_scope(monkeypatch, NEW_KEY, scope="company-wide")
        assert index_file(doc_file) is True

        # Asserted as one tuple so a failure names the actual split -- which of
        # the three models moved and which stayed behind.
        assert _partition(doc_file) == ({NEW_KEY}, {NEW_KEY}, {NEW_KEY}), (
            "document, chunks and companion memories are split across projects "
            "(doc keys, chunk keys, memory keys)"
        )

        docs = KnowledgeDocument.query.filter(file_path=doc_file)
        assert len(docs) == 1, "the re-key migrated the row; it must not fork it"
        assert docs[0].scope == "company-wide", "scope must not disagree with project_key"

    def test_rekey_with_changed_content_moves_chunks_and_memories(self, doc_file, monkeypatch):
        """The same partition guarantee when the content changed too."""
        _set_scope(monkeypatch, OLD_KEY)
        assert index_file(doc_file) is True

        with open(doc_file, "w", encoding="utf-8") as f:
            f.write(_CHANGED_BODY)

        _set_scope(monkeypatch, NEW_KEY)
        assert index_file(doc_file) is True

        assert _partition(doc_file) == ({NEW_KEY}, {NEW_KEY}, {NEW_KEY}), (
            "document, chunks and companion memories are split across projects "
            "(doc keys, chunk keys, memory keys)"
        )

    def test_reindex_without_rekey_is_stable(self, doc_file, monkeypatch):
        """The common case -- same key twice -- still lands on exactly one row."""
        _set_scope(monkeypatch, OLD_KEY)
        assert index_file(doc_file) is True
        assert index_file(doc_file) is True

        doc_keys, chunk_keys, mem_keys = _partition(doc_file)
        assert doc_keys == {OLD_KEY}
        assert chunk_keys == {OLD_KEY}
        assert mem_keys == {OLD_KEY}
        assert len(KnowledgeDocument.query.filter(file_path=doc_file)) == 1
