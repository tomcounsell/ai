"""Integration tests: DocumentChunk/KnowledgeDocument content store wiring (#2085).

Always-run assertions are network-free (store-type wiring only). A full
.save() round-trip through Redis + the embedding pipeline is guarded by
OPENAI_API_KEY since EmbeddingField calls the OpenAI provider on save.
"""

import os

import pytest

from models.length_safe_content_store import LengthSafeFilesystemStore

TEST_PROJECT_KEY = "test-length-safe-content-store"


@pytest.mark.integration
class TestContentFieldStoreWiring:
    """Store-type assertions -- no network, no Redis required."""

    def test_document_chunk_content_uses_length_safe_store(self):
        from models.document_chunk import DocumentChunk

        content_field = DocumentChunk._meta.fields["content"]
        assert isinstance(content_field.store, LengthSafeFilesystemStore)

    def test_knowledge_document_content_uses_length_safe_store(self):
        from models.knowledge_document import KnowledgeDocument

        content_field = KnowledgeDocument._meta.fields["content"]
        assert isinstance(content_field.store, LengthSafeFilesystemStore)

    def test_both_models_share_the_singleton_store_instance(self):
        """Both models route through the same store (same content dir)."""
        from models.document_chunk import DocumentChunk
        from models.knowledge_document import KnowledgeDocument

        chunk_store = DocumentChunk._meta.fields["content"].store
        doc_store = KnowledgeDocument._meta.fields["content"].store
        assert chunk_store is doc_store


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="requires OPENAI_API_KEY for EmbeddingField"
)
class TestLongPathChunkSaveRoundTrip:
    """Full model .save()/.load() with a long file_path -- exercises Redis + embeddings."""

    def teardown_method(self, method):
        """Clean up any test records via the ORM (never raw Redis)."""
        from models.document_chunk import DocumentChunk
        from models.knowledge_document import KnowledgeDocument

        for chunk in DocumentChunk.query.filter(project_key=TEST_PROJECT_KEY):
            chunk.delete()
        for doc in KnowledgeDocument.query.filter(project_key=TEST_PROJECT_KEY):
            doc.delete()

    def test_document_chunk_with_long_file_path_saves_and_loads(self):
        from models.document_chunk import DocumentChunk

        long_path = "/".join(["a-very-long-vault-directory-segment"] * 12) + "/document.pdf"
        original_text = "This is the chunk body text for the long-path regression test."
        chunk = DocumentChunk(
            document_doc_id="test-doc-id-long-path",
            chunk_index=0,
            content=original_text,
            file_path=long_path,
            project_key=TEST_PROJECT_KEY,
        )

        # Must not raise OSError: [Errno 63] File name too long.
        chunk.save()

        # The saved instance still resolves .content to the original text
        # (this in-memory instance is not lazy-loaded, so ContentField's
        # descriptor runs normally).
        assert chunk.content == original_text

        # ContentField.on_save() overwrites the in-memory attribute with the
        # $CF: reference string; read it back straight from __dict__ (bypassing
        # popoto's lazy-field attribute machinery, which is orthogonal to this
        # fix -- see docs/features/length-safe-content-store.md) and confirm
        # the underlying store round-trips it.
        reference = chunk.__dict__.get("content")
        assert isinstance(reference, str) and reference.startswith("$CF:")
        store = DocumentChunk._meta.fields["content"].store
        assert store.load(reference).decode("utf-8") == original_text

        reloaded = DocumentChunk.query.get(chunk_id=chunk.chunk_id)
        assert reloaded is not None

    def test_knowledge_document_with_long_file_path_saves_and_loads(self):
        from models.knowledge_document import KnowledgeDocument

        long_path = "/".join(["another-deeply-nested-vault-segment"] * 12) + "/report.pdf"
        original_text = "Knowledge document body text for the long-path regression test."
        doc = KnowledgeDocument(
            file_path=long_path,
            project_key=TEST_PROJECT_KEY,
            scope="client",
            content=original_text,
        )

        # Must not raise OSError: [Errno 63] File name too long.
        doc.save()

        assert doc.content == original_text

        reference = doc.__dict__.get("content")
        assert isinstance(reference, str) and reference.startswith("$CF:")
        store = KnowledgeDocument._meta.fields["content"].store
        assert store.load(reference).decode("utf-8") == original_text

        reloaded = KnowledgeDocument.query.get(doc_id=doc.doc_id)
        assert reloaded is not None

    def test_rechunk_decodes_content_reference_not_raw_cf_string(self):
        """rechunk_zero_chunk_documents chunks the DECODED text, not the $CF: ref.

        Regression guard: a query-loaded KnowledgeDocument surfaces
        doc.content as the raw ``$CF:{hash}:{relpath}`` reference string, not
        the decoded text. rechunk must load the real bytes before chunking --
        otherwise it would produce a single garbage chunk containing the
        literal reference string.
        """
        from models.document_chunk import DocumentChunk
        from models.knowledge_document import KnowledgeDocument
        from tools.knowledge.indexer import rechunk_zero_chunk_documents

        original_text = "The quick brown fox jumps over the lazy dog. Rechunk decode regression."
        doc = KnowledgeDocument(
            file_path="/vault/rechunk-decode-regression/document.md",
            project_key=TEST_PROJECT_KEY,
            scope="client",
            content=original_text,
        )
        doc.save()

        # Sanity: the doc currently has zero chunks.
        assert not DocumentChunk.query.filter(document_doc_id=doc.doc_id)

        # Re-query so doc.content is the raw $CF: reference (the buggy path).
        requeried = KnowledgeDocument.query.get(doc_id=doc.doc_id)
        assert requeried.content.startswith("$CF:")

        result = rechunk_zero_chunk_documents(project_key=TEST_PROJECT_KEY)
        assert result["rechunked"] >= 1

        chunks = list(DocumentChunk.query.filter(document_doc_id=doc.doc_id))
        assert len(chunks) >= 1

        # The stored chunk text must equal (a chunk of) the ORIGINAL content,
        # NOT the "$CF:..." reference string. Load via the chunk's store since
        # a query-loaded chunk also surfaces the raw reference.
        chunk_store = DocumentChunk._meta.fields["content"].store
        chunk_ref = chunks[0].__dict__.get("content") or chunks[0].content
        chunk_text = chunk_store.load(chunk_ref).decode("utf-8")

        assert not chunk_text.startswith("$CF:")
        assert chunk_text == original_text
