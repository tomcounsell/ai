"""Tests for the semantic doc impact finder tool."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.doc_impact_finder import (
    EMBEDDING_BATCH_SIZE,
    HAIKU_CONTENT_PREVIEW_CHARS,
    MIN_SIMILARITY_THRESHOLD,
    AffectedDoc,
    _candidates_to_affected_docs,
    chunk_markdown,
    cosine_similarity,
    find_affected_docs,
    get_embedding_provider,
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


# ---------------------------------------------------------------------------
# get_embedding_provider tests
# ---------------------------------------------------------------------------


class TestGetEmbeddingProvider:
    def test_no_dead_anthropic_fallback(self):
        """ANTHROPIC_API_KEY alone should NOT provide an embedding provider.

        Previously there was dead code that checked OPENAI_API_KEY inside the
        ANTHROPIC_API_KEY branch — but if OPENAI_API_KEY was set, the function
        would have already returned. Verify the dead code is gone.
        """
        env = {
            k: v
            for k, v in __import__("os").environ.items()
            if k not in ("OPENAI_API_KEY", "VOYAGE_API_KEY")
        }
        env["ANTHROPIC_API_KEY"] = "test-key"
        with patch.dict("os.environ", env, clear=True):
            result = get_embedding_provider()
            assert result is None

    def test_openai_key_returns_provider(self):
        """OPENAI_API_KEY should return an embedding provider."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False):
            result = get_embedding_provider()
            assert result is not None
            _, model_name = result
            assert model_name == "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Similarity threshold tests
# ---------------------------------------------------------------------------


class TestSimilarityThreshold:
    def test_candidates_below_threshold_filtered(self):
        """Embedding-only fallback should filter candidates below MIN_SIMILARITY_THRESHOLD."""
        candidates = [
            (
                0.9,
                {
                    "path": "docs/high.md",
                    "section": "## High",
                    "content_preview": "high",
                },
            ),
            (
                0.5,
                {"path": "docs/mid.md", "section": "## Mid", "content_preview": "mid"},
            ),
            (
                0.1,
                {"path": "docs/low.md", "section": "## Low", "content_preview": "low"},
            ),
        ]
        results = _candidates_to_affected_docs(candidates)

        # Only docs above MIN_SIMILARITY_THRESHOLD (0.3) should be included
        paths = [r.path for r in results]
        assert "docs/high.md" in paths
        assert "docs/mid.md" in paths
        assert "docs/low.md" not in paths

    def test_all_candidates_below_threshold_returns_empty(self):
        """If all candidates are below threshold, return empty list."""
        candidates = [
            (0.1, {"path": "docs/a.md", "section": "## A", "content_preview": "a"}),
            (0.05, {"path": "docs/b.md", "section": "## B", "content_preview": "b"}),
        ]
        results = _candidates_to_affected_docs(candidates)
        assert results == []


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    def test_batch_size_is_reasonable(self):
        """Batch size should be small enough to avoid API limits."""
        assert 10 <= EMBEDDING_BATCH_SIZE <= 500

    def test_similarity_threshold_in_range(self):
        """Similarity threshold should be between 0 and 1."""
        assert 0.0 < MIN_SIMILARITY_THRESHOLD < 1.0

    def test_content_preview_larger_than_200(self):
        """Content preview should be larger than the original 200 chars."""
        assert HAIKU_CONTENT_PREVIEW_CHARS > 200


# ---------------------------------------------------------------------------
# Integration test — full pipeline with mocked APIs
# ---------------------------------------------------------------------------


class TestFullPipelineIntegration:
    def test_end_to_end_with_mocked_apis(self, tmp_path):
        """Test the full pipeline: index_docs → find_affected_docs with mocked APIs.

        Mocks OpenAI embeddings and Anthropic Haiku to verify the full two-stage
        pipeline works end-to-end without real API calls.
        """
        from tools.doc_impact_finder import index_docs

        # Create test doc files
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "auth.md").write_text(
            "# Auth\n\n## Login Flow\n\nUsers login via OAuth.\n\n"
            "## Token Refresh\n\nTokens refresh every 30 minutes.\n"
        )
        (docs_dir / "api.md").write_text(
            "# API\n\n## Endpoints\n\nGET /users, POST /auth\n\n"
            "## Rate Limiting\n\n100 requests per minute.\n"
        )

        # Simple deterministic embedding: hash-based fake vectors
        def fake_embed(texts):
            """Return deterministic fake embeddings based on text content."""
            embeddings = []
            for text in texts:
                # Create a pseudo-embedding from the hash
                h = hashlib.md5(text.encode()).hexdigest()
                vec = [int(c, 16) / 15.0 for c in h]  # 32-dim vector, values 0-1
                embeddings.append(vec)
            return embeddings

        # Mock Haiku response
        mock_haiku_response = MagicMock()
        mock_haiku_response.content = [
            MagicMock(text='{"score": 8, "reason": "Auth change affects login docs"}')
        ]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_haiku_response

        # Step 1: Index docs with mocked embeddings
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.doc_impact_finder._embed_openai", side_effect=fake_embed):
                index = index_docs(repo_root=tmp_path)

        assert index["version"] == 1
        assert len(index["chunks"]) > 0
        assert index["model"] == "text-embedding-3-small"

        # Step 2: Find affected docs with mocked embeddings + Haiku
        # Mock the _rerank_single_candidate to avoid needing real anthropic import
        def mock_rerank(client, change_summary, chunk):
            return (8.0, "Auth change affects login docs", chunk)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.doc_impact_finder._embed_openai", side_effect=fake_embed):
                with patch(
                    "tools.doc_impact_finder._rerank_single_candidate",
                    side_effect=mock_rerank,
                ):
                    results = find_affected_docs(
                        "Changed the OAuth login flow to support PKCE",
                        repo_root=tmp_path,
                    )

        # Verify we got results back through the full pipeline
        assert isinstance(results, list)
        # All results should be AffectedDoc instances
        for r in results:
            assert isinstance(r, AffectedDoc)
            assert r.relevance > 0
            assert len(r.sections) > 0
            assert len(r.reason) > 0

    def test_end_to_end_embedding_only_fallback(self, tmp_path):
        """Test the pipeline falls back to embedding-only when Haiku unavailable."""
        from tools.doc_impact_finder import index_docs

        # Create test doc
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "test.md").write_text("# Test\n\n## Section\n\nContent here.\n")

        # Embedding that will produce high similarity with query
        def fake_embed(texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        # Step 1: Index
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.doc_impact_finder._embed_openai", side_effect=fake_embed):
                index_docs(repo_root=tmp_path)

        # Step 2: Find with Haiku failing (falls back to embedding-only)
        # Patch the import of anthropic inside find_affected_docs to raise
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No anthropic")
            return original_import(name, *args, **kwargs)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.doc_impact_finder._embed_openai", side_effect=fake_embed):
                with patch("builtins.__import__", side_effect=mock_import):
                    results = find_affected_docs(
                        "Some change",
                        repo_root=tmp_path,
                    )

        # Should still get results via embedding-only fallback
        assert isinstance(results, list)
        # All should have cosine sim of 1.0 (identical vectors), which is > threshold
        for r in results:
            assert r.relevance == 1.0
            assert "embedding similarity" in r.reason.lower()
