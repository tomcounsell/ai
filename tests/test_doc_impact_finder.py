"""Tests for the semantic doc impact finder tool."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from tools.doc_impact_finder import (
    chunk_markdown,
    cosine_similarity,
    find_affected_docs,
    load_index,
)

# ---------------------------------------------------------------------------
# chunk_markdown tests
# ---------------------------------------------------------------------------


class TestChunkMarkdown:
    def test_chunk_markdown_splits_on_headings(self):
        """Verify ## splitting produces correct chunks with proper section names."""
        content = (
            "# Title\n\nIntro text\n\n"
            "## Section One\n\nContent one\n\n"
            "## Section Two\n\nContent two\n"
        )
        chunks = chunk_markdown(content, "test.md")

        assert len(chunks) == 3
        assert chunks[0]["section"] == ""
        assert chunks[1]["section"] == "## Section One"
        assert chunks[2]["section"] == "## Section Two"

        # All chunks have correct path
        for chunk in chunks:
            assert chunk["path"] == "test.md"

    def test_chunk_markdown_preserves_content(self):
        """Verify content is complete in each chunk."""
        content = (
            "## First\n\nParagraph A\nParagraph B\n\n" "## Second\n\nParagraph C\n"
        )
        chunks = chunk_markdown(content, "doc.md")

        assert len(chunks) == 2
        assert "Paragraph A" in chunks[0]["content"]
        assert "Paragraph B" in chunks[0]["content"]
        assert "## First" in chunks[0]["content"]
        assert "Paragraph C" in chunks[1]["content"]
        assert "## Second" in chunks[1]["content"]

    def test_content_hash_deterministic(self):
        """Same content should produce the same hash."""
        content = "## Hello\n\nWorld\n"
        chunks_a = chunk_markdown(content, "a.md")
        chunks_b = chunk_markdown(content, "a.md")

        assert len(chunks_a) == 1
        assert len(chunks_b) == 1
        assert chunks_a[0]["content_hash"] == chunks_b[0]["content_hash"]

        # Verify the hash is actually a SHA-256
        expected = hashlib.sha256(chunks_a[0]["content"].encode()).hexdigest()
        assert chunks_a[0]["content_hash"] == expected

    def test_chunk_markdown_no_headings(self):
        """File with no ## headings returns a single chunk."""
        content = (
            "# Top Level Title\n\nJust some text\nwith no second-level headings.\n"
        )
        chunks = chunk_markdown(content, "simple.md")

        assert len(chunks) == 1
        assert chunks[0]["section"] == ""
        assert "Just some text" in chunks[0]["content"]
        assert chunks[0]["path"] == "simple.md"


# ---------------------------------------------------------------------------
# cosine_similarity tests
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_cosine_similarity_identical_vectors(self):
        """Identical vectors should return 1.0."""
        v = [1.0, 2.0, 3.0]
        result = cosine_similarity(v, v)
        assert abs(result - 1.0) < 1e-9

    def test_cosine_similarity_orthogonal_vectors(self):
        """Orthogonal vectors should return 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        result = cosine_similarity(a, b)
        assert abs(result) < 1e-9

    def test_cosine_similarity_opposite_vectors(self):
        """Opposite vectors should return -1.0."""
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        result = cosine_similarity(a, b)
        assert abs(result - (-1.0)) < 1e-9

    def test_cosine_similarity_zero_vector(self):
        """Zero vector should return 0.0 (handled gracefully)."""
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        result = cosine_similarity(a, b)
        assert result == 0.0


# ---------------------------------------------------------------------------
# Graceful degradation tests
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_graceful_degradation_no_api_key(self):
        """With no embedding keys, find_affected_docs returns empty list."""
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "",
                "VOYAGE_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
            clear=False,
        ):
            # Also clear the keys entirely in case they are set
            env = {
                k: v
                for k, v in __import__("os").environ.items()
                if k not in ("OPENAI_API_KEY", "VOYAGE_API_KEY", "ANTHROPIC_API_KEY")
            }
            with patch.dict("os.environ", env, clear=True):
                result = find_affected_docs(
                    "Changed the thread ID derivation logic",
                    repo_root=Path("/nonexistent"),
                )
                assert result == []


# ---------------------------------------------------------------------------
# load_index tests
# ---------------------------------------------------------------------------


class TestLoadIndex:
    def test_load_index_missing_file(self, tmp_path):
        """Returns empty index dict when file does not exist."""
        index = load_index(repo_root=tmp_path)
        assert index["version"] == 1
        assert index["chunks"] == []
        assert index["model"] == ""

    def test_load_index_valid_file(self, tmp_path):
        """Reads a valid index file correctly."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        index_data = {
            "version": 1,
            "model": "text-embedding-3-small",
            "chunks": [
                {
                    "path": "docs/test.md",
                    "section": "## Test",
                    "content_hash": "abc123",
                    "embedding": [0.1, 0.2],
                    "content_preview": "Test content",
                }
            ],
        }
        with open(data_dir / "doc_embeddings.json", "w") as f:
            json.dump(index_data, f)

        index = load_index(repo_root=tmp_path)
        assert index["version"] == 1
        assert len(index["chunks"]) == 1
        assert index["chunks"][0]["path"] == "docs/test.md"

    def test_load_index_corrupt_file(self, tmp_path):
        """Returns empty index when file is corrupt."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        with open(data_dir / "doc_embeddings.json", "w") as f:
            f.write("not valid json{{{")

        index = load_index(repo_root=tmp_path)
        assert index["version"] == 1
        assert index["chunks"] == []
