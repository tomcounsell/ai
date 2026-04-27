"""Tests for the knowledge document indexer pipeline."""

import json
from unittest.mock import MagicMock, patch

import pytest

from config.models import HAIKU
from tools.knowledge.chunking import chunk_document
from tools.knowledge.indexer import (
    LARGE_DOC_WORD_THRESHOLD,
    SUPPORTED_EXTENSIONS,
    _is_hidden_or_archived,
    _is_supported_file,
    _make_reference,
    _split_by_headings,
    _summarize_content,
)


@pytest.mark.unit
class TestIndexerHelpers:
    """Test indexer helper functions."""

    def test_supported_extensions(self):
        """Supported extensions include md and txt."""
        assert ".md" in SUPPORTED_EXTENSIONS
        assert ".txt" in SUPPORTED_EXTENSIONS
        assert ".markdown" in SUPPORTED_EXTENSIONS

    def test_is_supported_file_md(self):
        assert _is_supported_file("/path/to/doc.md") is True

    def test_is_supported_file_txt(self):
        assert _is_supported_file("/path/to/doc.txt") is True

    def test_is_supported_file_pdf(self):
        assert _is_supported_file("/path/to/doc.pdf") is False

    def test_is_supported_file_py(self):
        assert _is_supported_file("/path/to/code.py") is False

    def test_is_supported_file_image(self):
        assert _is_supported_file("/path/to/image.png") is False

    def test_is_hidden_dotfile(self):
        assert _is_hidden_or_archived("/path/.hidden/doc.md") is True

    def test_is_hidden_dotdir(self):
        assert _is_hidden_or_archived("/path/.obsidian/doc.md") is True

    def test_is_archived(self):
        assert _is_hidden_or_archived("/path/_archive_/doc.md") is True

    def test_normal_path_not_hidden(self):
        assert _is_hidden_or_archived("/path/to/doc.md") is False

    def test_make_reference(self):
        ref = _make_reference("/path/to/doc.md")
        parsed = json.loads(ref)
        assert parsed["tool"] == "read_file"
        assert parsed["params"]["file_path"] == "/path/to/doc.md"


@pytest.mark.unit
class TestSplitByHeadings:
    """Test heading-based document splitting."""

    def test_no_headings(self):
        content = "Just some text without headings."
        sections = _split_by_headings(content)
        assert len(sections) == 1
        assert sections[0][0] == ""
        assert "Just some text" in sections[0][1]

    def test_single_heading(self):
        content = "# Title\nSome content here."
        sections = _split_by_headings(content)
        assert len(sections) == 1
        assert "# Title" in sections[0][0]
        assert "Some content" in sections[0][1]

    def test_multiple_headings(self):
        content = "# First\nContent 1\n# Second\nContent 2"
        sections = _split_by_headings(content)
        assert len(sections) == 2
        assert "First" in sections[0][0]
        assert "Content 1" in sections[0][1]
        assert "Second" in sections[1][0]
        assert "Content 2" in sections[1][1]

    def test_h2_headings(self):
        content = "## Section A\nText A\n## Section B\nText B"
        sections = _split_by_headings(content)
        assert len(sections) == 2

    def test_content_before_first_heading(self):
        content = "Preamble text\n# Heading\nBody text"
        sections = _split_by_headings(content)
        assert len(sections) == 2
        assert "Preamble" in sections[0][1]
        assert "Body text" in sections[1][1]

    def test_empty_sections_skipped(self):
        content = "# First\n# Second\nContent"
        sections = _split_by_headings(content)
        # First heading has no content, should be skipped
        assert len(sections) == 1
        assert "Second" in sections[0][0]

    def test_h3_not_split(self):
        """H3 and below should not trigger splits."""
        content = "# Title\nIntro\n### Subsection\nDetail"
        sections = _split_by_headings(content)
        assert len(sections) == 1
        assert "Subsection" in sections[0][1]


@pytest.mark.unit
class TestIndexerPipeline:
    """Test the indexer pipeline functions."""

    def test_index_file_unsupported_extension(self):
        from tools.knowledge.indexer import index_file

        result = index_file("/path/to/image.png")
        assert result is False

    def test_index_file_nonexistent(self):
        from tools.knowledge.indexer import index_file

        result = index_file("/nonexistent/path/doc.md")
        assert result is False

    def test_index_file_hidden(self):
        from tools.knowledge.indexer import index_file

        result = index_file("/path/.hidden/doc.md")
        assert result is False

    def test_delete_file_nonexistent(self):
        from tools.knowledge.indexer import delete_file

        result = delete_file("/nonexistent/path/doc.md")
        assert result is False

    def test_large_doc_threshold(self):
        """Threshold for large doc splitting is 2000 words."""
        assert LARGE_DOC_WORD_THRESHOLD == 2000


@pytest.mark.unit
class TestSummarizeContent:
    """Test the _summarize_content function."""

    @pytest.fixture(autouse=True)
    def isolated_cache(self, monkeypatch, tmp_path):
        """Replace the indexer's module-level cache singleton with a tmp_path-rooted
        instance. See test_intent_classifier.TestClassifyIntent.isolated_cache for
        the rationale; the hasattr guard keeps this a no-op until the wire-up lands.
        """
        from tools.knowledge import indexer

        if not hasattr(indexer, "_cache"):
            return
        from utils.json_cache import JsonCache

        monkeypatch.setattr(
            indexer,
            "_cache",
            JsonCache(tmp_path / "summary_cache.json", max_entries=10),
        )

    @patch("anthropic.Anthropic")
    def test_summarize_uses_haiku_constant(self, mock_anthropic_cls):
        """Verify _summarize_content passes the HAIKU model constant to the API."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="A summary of the document.")]
        mock_client.messages.create.return_value = mock_response

        result = _summarize_content("Some document content here.", "/path/to/doc.md")

        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == HAIKU
        assert result == "A summary of the document."

    @patch("anthropic.Anthropic")
    def test_summarize_fallback_on_api_failure(self, mock_anthropic_cls):
        """Verify _summarize_content falls back to truncation on API failure."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error")

        content = "A" * 1000
        result = _summarize_content(content, "/path/to/doc.md")

        # Should fall back to truncated content (500 chars + "...")
        assert len(result) <= 504


# ---------------------------------------------------------------------------
# Sidecar parity: markitdown-generated .md files must be indexed identically
# to hand-written .md files. The frontmatter block (per converter._frontmatter_block)
# does NOT cause indexing to fail or skip the file, and the chunks produced are
# equivalent to a hand-written .md with the same body.
# ---------------------------------------------------------------------------

# Sample frontmatter exactly matching the shape converter._frontmatter_block emits.
_SIDECAR_FRONTMATTER = (
    "---\n"
    "source_hash: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n"
    "source_path: foo.pdf\n"
    "generated_by: markitdown\n"
    "generated_at: 2026-04-24T12:00:00Z\n"
    "regenerated_at: 2026-04-24T12:00:00Z\n"
    "llm_model: none\n"
    "---\n"
)

_SHARED_BODY = (
    "# Quarterly Report\n"
    "\n"
    "Revenue grew 25% year over year.\n"
    "\n"
    "## Key Drivers\n"
    "\n"
    "- New enterprise contracts\n"
    "- Lower churn\n"
)


@pytest.mark.unit
class TestSidecarParity:
    """A markitdown-generated `.md` sidecar (with YAML frontmatter) must be
    indexed identically to a hand-written `.md` file with the same body.

    The frontmatter MUST NOT cause indexing to fail or skip the file. The
    chunk output (the only public observable of the chunking pipeline) must
    be equivalent in structure and content for the heading-bearing body.
    """

    def test_sidecar_extension_is_supported(self):
        """A `.md` sidecar is treated as a supported file regardless of frontmatter."""
        assert _is_supported_file("/vault/foo.pdf.md") is True

    def test_sidecar_not_hidden(self):
        """Conventional sidecar paths are not classified as hidden/archived."""
        assert _is_hidden_or_archived("/vault/Consulting/foo.pdf.md") is False

    def test_sidecar_with_frontmatter_chunks_match_handwritten(self):
        """The chunker treats sidecar (frontmatter + body) and hand-written
        (body only) as producing equivalent chunk structure for the body.

        Frontmatter is small enough that for a short doc both inputs collapse
        to a single chunk. The hand-written chunk's text must be a substring
        of the sidecar's chunk's text (the body content is preserved
        verbatim, frontmatter sits ahead of it).
        """
        sidecar_content = _SIDECAR_FRONTMATTER + _SHARED_BODY
        handwritten_content = _SHARED_BODY

        sidecar_chunks = chunk_document(sidecar_content)
        handwritten_chunks = chunk_document(handwritten_content)

        # Both produce non-empty chunk lists — frontmatter does not cause skip.
        assert len(sidecar_chunks) > 0, "frontmatter caused empty chunk list"
        assert len(handwritten_chunks) > 0

        # Same chunk count for the same body — frontmatter doesn't fragment chunking.
        assert len(sidecar_chunks) == len(handwritten_chunks)

        # The body content is preserved in the sidecar's chunks.
        sidecar_combined = "\n".join(c["text"] for c in sidecar_chunks)
        for handwritten_chunk in handwritten_chunks:
            # Each line of the handwritten body content appears in the sidecar's output.
            for line in handwritten_chunk["text"].splitlines():
                stripped = line.strip()
                if stripped:
                    assert stripped in sidecar_combined, (
                        f"body line missing from sidecar chunks: {stripped!r}"
                    )

    def test_sidecar_index_file_completes_same_path_as_handwritten(self, tmp_path, monkeypatch):
        """`index_file` walks the same code path for a sidecar and a hand-written
        `.md`: read → resolve_scope → safe_upsert → _sync_chunks → companion memories.

        We monkeypatch the persistence boundary (resolve_scope + safe_upsert +
        chunk sync + companion memory creation) and assert that for both inputs,
        the same set of side effects fires with the same content.
        """
        from tools.knowledge import indexer as indexer_mod

        # Two .md files: a sidecar and a hand-written, both readable on disk.
        sidecar_path = tmp_path / "foo.pdf.md"
        sidecar_path.write_text(_SIDECAR_FRONTMATTER + _SHARED_BODY, encoding="utf-8")
        handwritten_path = tmp_path / "notes.md"
        handwritten_path.write_text(_SHARED_BODY, encoding="utf-8")

        # Capture what the persistence layer sees for each call.
        upsert_calls: list[tuple[str, str, str]] = []
        sync_chunk_calls: list[tuple[str, str]] = []
        companion_memory_calls: list[tuple[str, str, str]] = []

        # Stub resolve_scope to a deterministic project_key/scope.
        monkeypatch.setattr(
            "tools.knowledge.scope_resolver.resolve_scope",
            lambda fp: ("test-project", "client"),
        )

        # Stub KnowledgeDocument.safe_upsert to record the call and return a
        # minimal stand-in object exposing the attributes the indexer touches.
        class _FakeDoc:
            def __init__(self, file_path: str):
                self.file_path = file_path
                self.doc_id = f"doc-{file_path}"
                self.content = ""
                self.content_hash = ""

        # The existing-doc lookup must report "no existing doc" so the
        # content-changed branch fires for both inputs.
        monkeypatch.setattr(
            "models.knowledge_document.KnowledgeDocument.query",
            MagicMock(filter=lambda **kw: []),
        )

        def _fake_upsert(file_path, project_key, scope):
            upsert_calls.append((file_path, project_key, scope))
            return _FakeDoc(file_path)

        monkeypatch.setattr(
            "models.knowledge_document.KnowledgeDocument.safe_upsert",
            classmethod(lambda cls, *a, **kw: _fake_upsert(*a, **kw)),
        )

        # Stub _sync_chunks: just record (file_path, content_passed_in).
        def _fake_sync(doc, content, project_key):
            sync_chunk_calls.append((doc.file_path, content))

        monkeypatch.setattr(indexer_mod, "_sync_chunks", _fake_sync)

        # Stub _create_companion_memories: record path + scope + content snippet.
        def _fake_companions(file_path, project_key, scope, content):
            companion_memory_calls.append((file_path, project_key, content))

        monkeypatch.setattr(indexer_mod, "_create_companion_memories", _fake_companions)

        # Run the indexer against both files.
        sidecar_result = indexer_mod.index_file(str(sidecar_path))
        handwritten_result = indexer_mod.index_file(str(handwritten_path))

        # Both succeed — frontmatter does NOT cause indexing to fail or skip.
        assert sidecar_result is True, "indexer skipped/failed on sidecar"
        assert handwritten_result is True

        # Both took the upsert path with the same project_key/scope.
        assert len(upsert_calls) == 2
        assert all(call[1:] == ("test-project", "client") for call in upsert_calls)

        # Both triggered chunk sync (content_changed branch).
        assert len(sync_chunk_calls) == 2

        # The sidecar's chunk-sync content includes the full body verbatim.
        sidecar_sync_content = next(c for fp, c in sync_chunk_calls if fp == str(sidecar_path))
        handwritten_sync_content = next(
            c for fp, c in sync_chunk_calls if fp == str(handwritten_path)
        )
        # Body is fully present in both.
        assert "Revenue grew 25%" in sidecar_sync_content
        assert "Revenue grew 25%" in handwritten_sync_content

        # Both produced companion memories.
        assert len(companion_memory_calls) == 2
