"""Tests for the semantic doc impact finder tool."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.doc_impact_finder import (
    EMBEDDING_BATCH_SIZE,
    HAIKU_CONTENT_PREVIEW_CHARS,
    MIN_SIMILARITY_THRESHOLD,
    AffectedDoc,
    ImpactFinderMeta,
    _candidates_to_affected_docs,
    _discover_doc_files,
    chunk_doc,
    chunk_markdown,
    cosine_similarity,
    find_affected_docs,
    get_embedding_provider,
    load_index,
    preprocess_html,
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
        content = "## First\n\nParagraph A\nParagraph B\n\n## Second\n\nParagraph C\n"
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
        content = "# Top Level Title\n\nJust some text\nwith no second-level headings.\n"
        chunks = chunk_markdown(content, "simple.md")

        assert len(chunks) == 1
        assert chunks[0]["section"] == ""
        assert "Just some text" in chunks[0]["content"]
        assert chunks[0]["path"] == "simple.md"


# ---------------------------------------------------------------------------
# HTML site-page support (site/*.html)
# ---------------------------------------------------------------------------


class TestHtmlDocs:
    """The doc-impact finder discovers and chunks site/*.html pages (#2058)."""

    def test_preprocess_html_maps_headings(self):
        """<h2>/<h3> become ## / ### lines; tags stripped; scripts/styles dropped."""
        html = (
            "<html><head><style>.x{color:red}</style></head><body>"
            "<h2>Runtime</h2><p>The worker executes sessions.</p>"
            "<h3>Nudge loop</h3><p>Bridge routes output.</p>"
            "<script>console.log('ignored')</script>"
            "</body></html>"
        )
        text = preprocess_html(html)

        assert "## Runtime" in text
        assert "### Nudge loop" in text
        assert "The worker executes sessions." in text
        assert "Bridge routes output." in text
        # script/style bodies are dropped
        assert "console.log" not in text
        assert "color:red" not in text

    def test_chunk_doc_html_splits_on_h2(self):
        """chunk_doc dispatches .html through the preprocessor, chunking on h2."""
        html = (
            "<body><h2>Section One</h2><p>Content one.</p>"
            "<h2>Section Two</h2><p>Content two.</p></body>"
        )
        chunks = chunk_doc(html, "site/runtime.html")

        sections = [c["section"] for c in chunks]
        assert "## Section One" in sections
        assert "## Section Two" in sections
        assert all(c["path"] == "site/runtime.html" for c in chunks)

    def test_chunk_doc_markdown_unchanged(self):
        """Non-.html paths chunk as plain markdown (no preprocessing)."""
        content = "# Title\n\n## Alpha\n\nA\n\n## Beta\n\nB\n"
        assert chunk_doc(content, "docs/x.md") == chunk_markdown(content, "docs/x.md")

    def test_chunk_doc_html_no_headings_single_chunk(self):
        """An HTML page with no <h2> yields a single preamble chunk."""
        html = "<body><p>Just a paragraph with no second-level headings.</p></body>"
        chunks = chunk_doc(html, "site/tour.html")

        assert len(chunks) == 1
        assert chunks[0]["section"] == ""
        assert "Just a paragraph" in chunks[0]["content"]

    def test_chunk_doc_empty_html_no_crash(self):
        """An empty HTML file yields zero chunks and never raises."""
        assert chunk_doc("", "site/empty.html") == []

    def test_preprocess_html_malformed_no_crash(self):
        """Malformed/garbage HTML yields text output, never an exception."""
        # Unclosed tags, stray brackets, no structure.
        assert isinstance(preprocess_html("<h2>Broken<p>text <<< &amp;"), str)

    def test_discover_finds_site_html_not_assets(self, tmp_path):
        """site/*.html is discovered; site/assets/graph.js is never indexed."""
        site = tmp_path / "site"
        (site / "assets").mkdir(parents=True)
        (site / "index.html").write_text("<h2>Home</h2>")
        (site / "runtime.html").write_text("<h2>Runtime</h2>")
        (site / "assets" / "graph.js").write_text("// 38k lines of data\n")
        (site / "sitemap.xml").write_text("<urlset></urlset>")

        discovered = {str(p.relative_to(tmp_path)) for p in _discover_doc_files(tmp_path)}

        assert "site/index.html" in discovered
        assert "site/runtime.html" in discovered
        # The generated graph.js and non-HTML files never match site/*.html
        assert not any("graph.js" in d for d in discovered)
        assert "site/sitemap.xml" not in discovered


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
        """With no embedding keys: ([], meta(degraded=True, reason=named))."""
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
                result, meta = find_affected_docs(
                    "Changed the thread ID derivation logic",
                    repo_root=Path("/nonexistent"),
                )
                assert result == []
                assert meta == ImpactFinderMeta(
                    degraded=True,
                    reason="no_embedding_provider",
                    rerank_failures=0,
                    candidates=0,
                )


class TestDegradedMetaBranches:
    """Every degraded/fallback branch must be distinguishable from 'no docs affected'.

    Issue #2004 T1.4: a bare [] can mean either "nothing is affected" (clean run,
    degraded=False) or "the finder is broken" (degraded=True with a named reason).
    """

    def test_empty_index_returns_degraded_meta(self, tmp_path):
        """A missing/empty index is degraded, never a silent []."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            results, meta = find_affected_docs("Some change", repo_root=tmp_path)

        assert results == []
        assert meta.degraded is True
        assert meta.reason == "empty_index"
        assert meta.rerank_failures == 0
        assert meta.candidates == 0

    def test_query_embedding_failure_returns_degraded_meta(self, tmp_path):
        """A query-embedding transport failure is degraded, never a silent []."""
        from tools.doc_impact_finder import index_docs

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "test.md").write_text("# Test\n\n## Section\n\nContent here.\n")

        def fake_embed(texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                index_docs(repo_root=tmp_path)

        def failing_embed(texts):
            raise RuntimeError("embedding API down")

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.impact_finder_core._embed_openai", side_effect=failing_embed):
                results, meta = find_affected_docs("Some change", repo_root=tmp_path)

        assert results == []
        assert meta.degraded is True
        assert meta.reason == "query_embedding_failed"


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
            with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                index = index_docs(repo_root=tmp_path)

        assert index["version"] == 1
        assert len(index["chunks"]) > 0
        assert index["model"] == "text-embedding-3-small"

        # Step 2: Find affected docs with mocked embeddings + Haiku
        # Mock the core reranker to avoid needing real anthropic import
        def mock_rerank(client, prompt, chunk):
            return (8.0, "Auth change affects login docs", chunk)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                with patch(
                    "tools.impact_finder_core._rerank_single_candidate",
                    side_effect=mock_rerank,
                ):
                    results, meta = find_affected_docs(
                        "Changed the OAuth login flow to support PKCE",
                        repo_root=tmp_path,
                    )

        # Verify we got results back through the full pipeline
        assert isinstance(results, list)
        # A clean run is NOT degraded
        assert meta.degraded is False
        assert meta.reason is None
        assert meta.rerank_failures == 0
        assert meta.candidates > 0
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
            with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
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
            with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                with patch("builtins.__import__", side_effect=mock_import):
                    results, meta = find_affected_docs(
                        "Some change",
                        repo_root=tmp_path,
                    )

        # Should still get results via embedding-only fallback — visibly degraded
        assert isinstance(results, list)
        assert meta.degraded is True
        assert meta.reason == "rerank_client_init_failed"
        assert meta.candidates > 0
        # All should have cosine sim of 1.0 (identical vectors), which is > threshold
        for r in results:
            assert r.relevance == 1.0
            assert "embedding similarity" in r.reason.lower()


class TestRerankEndpointFailureFallback:
    """Regression tests for issue #1950.

    Distinguish "the rerank endpoint could not run at all" (every request raises
    a transport/API error -> embedding-only fallback) from "the reranker ran and
    nothing scored >= 5" (-> empty result, no fallback). A misconfigured
    ``ANTHROPIC_BASE_URL`` that 404s on the Haiku model must degrade to
    embedding-only results, never a silent empty list.
    """

    @staticmethod
    def _index_two_section_doc(tmp_path):
        """Index a doc with two ## sections; returns a fake_embed for reuse.

        Every chunk embeds identically to the query (cosine sim 1.0), so both
        sections survive Stage 1 recall as candidates.
        """
        from tools.doc_impact_finder import index_docs

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "test.md").write_text(
            "# Test\n\n## Alpha\n\nContent A here.\n\n## Beta\n\nContent B here.\n"
        )

        def fake_embed(texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                index_docs(repo_root=tmp_path)

        return fake_embed

    def test_all_rerank_requests_fail_uses_fallback(self, tmp_path, caplog):
        """Every rerank request raises a transport error -> embedding-only fallback."""
        fake_embed = self._index_two_section_doc(tmp_path)

        def failing_rerank(client, prompt, chunk):
            raise RuntimeError("404 Not Found: model claude-haiku-4-5 not found")

        with caplog.at_level(logging.WARNING, logger="tools.impact_finder_core"):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
                with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                    with patch(
                        "tools.impact_finder_core._rerank_single_candidate",
                        side_effect=failing_rerank,
                    ):
                        results, meta = find_affected_docs("Some change", repo_root=tmp_path)

        # Fallback produced embedding-only results instead of [] — visibly degraded
        assert isinstance(results, list)
        assert len(results) > 0
        assert meta.degraded is True
        assert meta.reason == "rerank_all_failed"
        assert meta.rerank_failures == meta.candidates
        assert meta.candidates > 0
        for r in results:
            assert "embedding similarity" in r.reason.lower()

        # Warning naming the likely cause fires exactly once (aggregate, not per
        # candidate).
        fallback_warnings = [
            rec
            for rec in caplog.records
            if "rerank requests failed" in rec.getMessage()
            and "ANTHROPIC_BASE_URL" in rec.getMessage()
        ]
        assert len(fallback_warnings) == 1

    def test_rerank_ran_all_below_threshold_returns_empty_no_fallback(self, tmp_path, caplog):
        """Reranker runs, nothing scores >= 5 -> [] with no fallback dump."""
        fake_embed = self._index_two_section_doc(tmp_path)

        # Below-threshold scores surface as None from _rerank_single_candidate.
        def below_threshold_rerank(client, prompt, chunk):
            return None

        with caplog.at_level(logging.WARNING, logger="tools.impact_finder_core"):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
                with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                    with patch(
                        "tools.impact_finder_core._rerank_single_candidate",
                        side_effect=below_threshold_rerank,
                    ):
                        results, meta = find_affected_docs("Some change", repo_root=tmp_path)

        assert results == []
        # "No docs affected" on a clean run is NOT degraded — this is the
        # distinguishable counterpart of every degraded branch.
        assert meta.degraded is False
        assert meta.reason is None
        assert meta.rerank_failures == 0
        # No false-positive fallback warning.
        assert not [rec for rec in caplog.records if "rerank requests failed" in rec.getMessage()]

    def test_mixed_failure_and_success_no_fallback(self, tmp_path, caplog):
        """Some requests fail but at least one scores -> real results, no fallback."""
        fake_embed = self._index_two_section_doc(tmp_path)

        def mixed_rerank(client, prompt, chunk):
            if chunk.get("section") == "## Alpha":
                return (8.0, "Alpha is relevant", chunk)
            raise RuntimeError("404 Not Found")

        with caplog.at_level(logging.WARNING, logger="tools.impact_finder_core"):
            with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-key"}, clear=False):
                with patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed):
                    with patch(
                        "tools.impact_finder_core._rerank_single_candidate",
                        side_effect=mixed_rerank,
                    ):
                        results, meta = find_affected_docs("Some change", repo_root=tmp_path)

        # The surviving Haiku result is returned (relevance 0.8 from score/10),
        # not the embedding-only fallback text.
        assert len(results) == 1
        assert results[0].relevance == 0.8
        assert "embedding similarity" not in results[0].reason.lower()
        assert "Alpha is relevant" in results[0].reason

        # Partial results are flagged degraded so the caller can see the gap.
        # The fixture doc chunks into 3 candidates (preamble + Alpha + Beta);
        # only Alpha reranks successfully.
        assert meta.degraded is True
        assert meta.reason == "rerank_partial_failure"
        assert meta.rerank_failures == 2
        assert meta.candidates == 3

        # Partial failure must NOT trigger the fallback warning.
        assert not [rec for rec in caplog.records if "rerank requests failed" in rec.getMessage()]


class TestRerankSingleCandidateExceptionSplit:
    """Directly exercise the three-way except split in _rerank_single_candidate.

    The integration tests above patch _rerank_single_candidate out wholesale, so
    they never touch the actual except branches that are the #1950 fix. These
    tests hit the real function with a mocked Anthropic client so a regression
    that reverts the transport branch to `return None` (the original bug) is
    caught.
    """

    @staticmethod
    def _client_returning(text):
        """Build a mock Anthropic client whose messages.create returns `text`."""
        client = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(text=text)]
        client.messages.create.return_value = response
        return client

    def test_transport_error_reraises(self):
        """A transport/API error from client.messages.create is re-raised, not swallowed."""
        import pytest

        from tools.impact_finder_core import _rerank_single_candidate

        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("404 Not Found: model missing")
        chunk = {"path": "docs/x.md", "section": "## S"}

        with pytest.raises(RuntimeError, match="404"):
            _rerank_single_candidate(client, "prompt", chunk)

    def test_malformed_json_returns_none(self):
        """Unparseable JSON is 'ran but did not qualify' -> None, never a hard failure."""
        from tools.impact_finder_core import _rerank_single_candidate

        client = self._client_returning("this is not json")
        chunk = {"path": "docs/x.md", "section": "## S"}

        assert _rerank_single_candidate(client, "prompt", chunk) is None

    def test_non_numeric_score_returns_none(self):
        """A malformed (non-numeric) score raises ValueError internally -> None, not re-raised."""
        from tools.impact_finder_core import _rerank_single_candidate

        client = self._client_returning('{"score": "n/a", "reason": "unsure"}')
        chunk = {"path": "docs/x.md", "section": "## S"}

        assert _rerank_single_candidate(client, "prompt", chunk) is None

    def test_below_threshold_returns_none(self):
        """A clean score below 5 returns None (unchanged behavior)."""
        from tools.impact_finder_core import _rerank_single_candidate

        client = self._client_returning('{"score": 3, "reason": "weak match"}')
        chunk = {"path": "docs/x.md", "section": "## S"}

        assert _rerank_single_candidate(client, "prompt", chunk) is None

    def test_score_at_or_above_threshold_returns_tuple(self):
        """A score >= 5 returns the (score, reason, chunk) tuple (unchanged behavior)."""
        from tools.impact_finder_core import _rerank_single_candidate

        client = self._client_returning('{"score": 8, "reason": "strong match"}')
        chunk = {"path": "docs/x.md", "section": "## S"}

        result = _rerank_single_candidate(client, "prompt", chunk)
        assert result is not None
        score, reason, returned_chunk = result
        assert score == 8.0
        assert reason == "strong match"
        assert returned_chunk["path"] == "docs/x.md"
