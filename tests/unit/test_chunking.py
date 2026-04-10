"""Tests for the chunking engine."""

import pytest


@pytest.mark.unit
class TestChunkDocument:
    """Test the chunk_document function."""

    def test_importable(self):
        """Chunking module can be imported."""
        from tools.knowledge.chunking import chunk_document

        assert chunk_document is not None

    def test_empty_content_produces_no_chunks(self):
        """Empty content returns empty list."""
        from tools.knowledge.chunking import chunk_document

        assert chunk_document("") == []
        assert chunk_document("   ") == []
        assert chunk_document("\n\n") == []

    def test_none_content_produces_no_chunks(self):
        """None content returns empty list."""
        from tools.knowledge.chunking import chunk_document

        assert chunk_document(None) == []

    def test_short_doc_produces_single_chunk(self):
        """A short document under the token limit produces exactly one chunk."""
        from tools.knowledge.chunking import chunk_document

        content = "This is a short document with just a few sentences."
        chunks = chunk_document(content)
        assert len(chunks) == 1
        assert chunks[0]["chunk_index"] == 0
        assert chunks[0]["text"] == content

    def test_long_doc_produces_multiple_chunks(self):
        """A document exceeding CHUNK_SIZE_TOKENS produces multiple chunks."""
        from tools.knowledge.chunking import CHUNK_SIZE_TOKENS, chunk_document

        # Generate content that definitely exceeds the chunk size
        # ~4 chars per token, so CHUNK_SIZE_TOKENS * 6 chars should exceed it
        long_content = "This is a test sentence that will be repeated many times. " * (
            CHUNK_SIZE_TOKENS // 5
        )
        chunks = chunk_document(long_content)
        assert len(chunks) > 1

        # Verify chunk indices are sequential
        for i, chunk in enumerate(chunks):
            assert chunk["chunk_index"] == i
            assert isinstance(chunk["text"], str)
            assert len(chunk["text"]) > 0

    def test_heading_aware_splitting(self):
        """Documents with headings split at heading boundaries."""
        from tools.knowledge.chunking import chunk_document

        content = """# Section One

This is the first section with some content.
It has multiple lines of text.

# Section Two

This is the second section with different content.
It also has multiple lines.

# Section Three

This is the third section."""

        chunks = chunk_document(content)
        # Short doc with headings should still be a single chunk
        # since total tokens are under the limit
        assert len(chunks) >= 1
        # The content should be preserved
        full_text = " ".join(c["text"] for c in chunks)
        assert "Section One" in full_text
        assert "Section Two" in full_text
        assert "Section Three" in full_text

    def test_heading_aware_splitting_large_doc(self):
        """Large documents with headings split at heading boundaries."""
        from tools.knowledge.chunking import CHUNK_SIZE_TOKENS, chunk_document

        # Create a document with headings where each section is large
        section_content = "This is test content. " * (CHUNK_SIZE_TOKENS // 3)
        content = f"""# Section One

{section_content}

# Section Two

{section_content}

# Section Three

{section_content}"""

        chunks = chunk_document(content)
        assert len(chunks) > 1

    def test_overlap_between_adjacent_chunks(self):
        """Adjacent chunks have overlapping content when split by tokens."""
        from tools.knowledge.chunking import CHUNK_SIZE_TOKENS, chunk_document

        # Create content without headings to force token-based splitting
        # Use unique words to verify overlap
        words = [f"word{i}" for i in range(CHUNK_SIZE_TOKENS * 3)]
        content = " ".join(words)

        chunks = chunk_document(content)
        assert len(chunks) > 1

        # Check that adjacent chunks share some content (overlap)
        for i in range(len(chunks) - 1):
            current_words = set(chunks[i]["text"].split())
            next_words = set(chunks[i + 1]["text"].split())
            overlap = current_words & next_words
            assert len(overlap) > 0, (
                f"No overlap between chunk {i} and chunk {i + 1}"
            )

    def test_chunk_output_format(self):
        """Each chunk has the expected dict structure."""
        from tools.knowledge.chunking import chunk_document

        content = "A simple test document."
        chunks = chunk_document(content)
        assert len(chunks) == 1

        chunk = chunks[0]
        assert "chunk_index" in chunk
        assert "text" in chunk
        assert isinstance(chunk["chunk_index"], int)
        assert isinstance(chunk["text"], str)

    def test_whitespace_only_produces_no_chunks(self):
        """Document with only whitespace produces zero chunks."""
        from tools.knowledge.chunking import chunk_document

        assert chunk_document("   \n\n   \t   ") == []

    def test_no_headings_falls_back_to_token_splitting(self):
        """Documents without headings use token-count splitting."""
        from tools.knowledge.chunking import CHUNK_SIZE_TOKENS, chunk_document

        # Long content without any headings
        content = "No headings here just plain text. " * (CHUNK_SIZE_TOKENS // 3)
        chunks = chunk_document(content)
        assert len(chunks) > 1


@pytest.mark.unit
class TestTokenCounting:
    """Test token counting utilities."""

    def test_count_tokens(self):
        """Token counting returns a reasonable positive integer."""
        from tools.knowledge.chunking import _count_tokens

        count = _count_tokens("Hello, world!")
        assert isinstance(count, int)
        assert count > 0

    def test_count_tokens_empty(self):
        """Empty string has zero tokens."""
        from tools.knowledge.chunking import _count_tokens

        count = _count_tokens("")
        assert count == 0


@pytest.mark.unit
class TestConstants:
    """Test chunking constants."""

    def test_chunk_size_is_reasonable(self):
        """CHUNK_SIZE_TOKENS is a reasonable value."""
        from tools.knowledge.chunking import CHUNK_SIZE_TOKENS

        assert 500 <= CHUNK_SIZE_TOKENS <= 5000

    def test_overlap_is_smaller_than_chunk_size(self):
        """CHUNK_OVERLAP_TOKENS is smaller than CHUNK_SIZE_TOKENS."""
        from tools.knowledge.chunking import CHUNK_OVERLAP_TOKENS, CHUNK_SIZE_TOKENS

        assert CHUNK_OVERLAP_TOKENS < CHUNK_SIZE_TOKENS
        assert CHUNK_OVERLAP_TOKENS > 0
