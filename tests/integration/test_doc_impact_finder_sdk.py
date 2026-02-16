"""Integration tests for doc_impact_finder through the Agent SDK invocation pattern.

Tests the tool as the agent actually calls it: via python3 -c subprocess commands
matching the exact invocation in .claude/skills/do-docs/SKILL.md (Agent C).

Verifies:
1. Subprocess invocation matches skill definition
2. Graceful degradation when no embedding API key
3. Live Haiku reranking with real Anthropic API
4. Index building and incremental re-indexing
5. Full pipeline end-to-end with pre-built index
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PYTHON = sys.executable


def run_tool_subprocess(
    code: str, env_override: dict | None = None
) -> subprocess.CompletedProcess:
    """Run a python -c command the same way the agent does in /do-docs Agent C."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [PYTHON, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Subprocess invocation tests (how the agent actually calls the tool)
# ---------------------------------------------------------------------------


class TestSubprocessInvocation:
    """Test that the tool works when invoked via python3 -c, matching Agent C's pattern."""

    def test_import_succeeds(self):
        """Tool can be imported from the repo root (agent's working directory)."""
        result = run_tool_subprocess(
            "import sys; sys.path.insert(0, '.'); "
            "from tools.doc_impact_finder import index_docs, find_affected_docs; "
            "print('OK')"
        )
        assert result.returncode == 0, f"Import failed: {result.stderr}"
        assert "OK" in result.stdout

    def test_chunk_markdown_via_subprocess(self):
        """Chunking works through subprocess (no API keys needed)."""
        result = run_tool_subprocess(
            "import sys, json; sys.path.insert(0, '.'); "
            "from tools.doc_impact_finder import chunk_markdown; "
            "chunks = chunk_markdown('# Title\\n\\n## Section A\\n\\nContent A\\n\\n## Section B\\n\\nContent B', 'test.md'); "
            "print(json.dumps([{'section': c['section'], 'path': c['path']} for c in chunks]))"
        )
        assert result.returncode == 0, f"Failed: {result.stderr}"
        chunks = json.loads(result.stdout.strip())
        assert len(chunks) == 3
        assert chunks[1]["section"] == "## Section A"
        assert chunks[2]["section"] == "## Section B"

    def test_graceful_degradation_via_subprocess(self):
        """find_affected_docs returns empty list with no API keys (subprocess)."""
        result = run_tool_subprocess(
            "import sys; sys.path.insert(0, '.'); "
            "from tools.doc_impact_finder import find_affected_docs; "
            "from pathlib import Path; "
            "results = find_affected_docs('Changed session scoping', repo_root=Path('/nonexistent')); "
            "print(f'results={len(results)}')",
            env_override={
                "OPENAI_API_KEY": "",
                "VOYAGE_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
            },
        )
        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert "results=0" in result.stdout

    def test_skill_invocation_pattern_index(self):
        """The exact index_docs invocation from SKILL.md works."""
        # This matches the Agent C step 1 in SKILL.md
        code = (
            'import sys; sys.path.insert(0, "."); '
            "from tools.doc_impact_finder import index_docs; "
            "idx = index_docs(); "
            'print(f\'chunks={len(idx["chunks"])} model={idx["model"]}\')'
        )
        # Run with no embedding key to verify graceful degradation
        result = run_tool_subprocess(
            code,
            env_override={"OPENAI_API_KEY": "", "VOYAGE_API_KEY": ""},
        )
        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert "chunks=0" in result.stdout

    def test_skill_invocation_pattern_find(self):
        """The exact find_affected_docs invocation from SKILL.md works."""
        # This matches the Agent C step 2 in SKILL.md
        change_summary = "Refactored session isolation to use slug-based worktrees"
        code = (
            "import sys, json\n"
            'sys.path.insert(0, ".")\n'
            "from tools.doc_impact_finder import find_affected_docs\n"
            f"results = find_affected_docs('''{change_summary}''')\n"
            "for r in results:\n"
            "    print(f'{r.relevance:.2f} | {r.path} | {r.sections} | {r.reason}')\n"
            "print(f'total={len(results)}')"
        )
        result = run_tool_subprocess(
            code,
            env_override={"OPENAI_API_KEY": "", "VOYAGE_API_KEY": ""},
        )
        assert result.returncode == 0, f"Failed: {result.stderr}"
        assert "total=" in result.stdout


# ---------------------------------------------------------------------------
# Live Haiku reranking tests (requires ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping live Haiku tests",
)
class TestLiveHaikuReranking:
    """Test Stage 2 (Haiku reranking) with real API calls."""

    def test_rerank_relevant_chunk_scores_high(self):
        """Haiku should score a clearly relevant doc section >= 5."""
        from tools.doc_impact_finder import _rerank_single_candidate

        import anthropic

        client = anthropic.Anthropic()
        change = "Refactored session isolation to use slug-based worktrees instead of thread IDs"
        chunk = {
            "path": "docs/features/session-isolation.md",
            "section": "## Technical Implementation",
            "content_preview": (
                "## Technical Implementation\n\n"
                "Session isolation uses two tiers:\n"
                "- Tier 1: Thread-scoped task lists keyed by thread-{chat_id}-{root_message_id}\n"
                "- Tier 2: Slug-scoped task lists for planned work items\n\n"
                "Git worktrees provide filesystem isolation under .worktrees/{slug}/"
            ),
        }
        result = _rerank_single_candidate(client, change, chunk)
        assert result is not None, "Haiku should score this chunk >= 5"
        score, reason, _ = result
        assert score >= 5, f"Expected score >= 5, got {score}: {reason}"
        assert len(reason) > 10, "Reason should be substantive"

    def test_rerank_irrelevant_chunk_scores_low(self):
        """Haiku should score a clearly irrelevant doc section < 5."""
        from tools.doc_impact_finder import _rerank_single_candidate

        import anthropic

        client = anthropic.Anthropic()
        change = "Refactored session isolation to use slug-based worktrees instead of thread IDs"
        chunk = {
            "path": "docs/recipes/banana-bread.md",
            "section": "## Ingredients",
            "content_preview": (
                "## Ingredients\n\n"
                "- 3 ripe bananas\n"
                "- 1/3 cup melted butter\n"
                "- 3/4 cup sugar\n"
                "- 1 egg, beaten\n"
                "- 1 teaspoon vanilla\n"
                "- 1 teaspoon baking soda\n"
                "- 1.5 cups all-purpose flour"
            ),
        }
        result = _rerank_single_candidate(client, change, chunk)
        # Should return None (score < 5) for banana bread recipe
        assert (
            result is None
        ), f"Haiku should not flag banana bread as relevant: {result}"

    def test_rerank_parallel_execution(self):
        """Verify parallel reranking works with ThreadPoolExecutor."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from tools.doc_impact_finder import _rerank_single_candidate

        import anthropic

        client = anthropic.Anthropic()
        change = "Added new CLI flag --verbose to bridge startup"

        chunks = [
            {
                "path": "docs/features/bridge-self-healing.md",
                "section": "## Restart Escalation",
                "content_preview": "## Restart Escalation\n\nThe bridge watchdog monitors health every 60s and uses a 5-level escalation: restart, kill stale, clear locks, revert commit, alert human.",
            },
            {
                "path": "config/SOUL.md",
                "section": "## Communication Style",
                "content_preview": "## Communication Style\n\nDirect, no fluff. Prefer action over discussion. Never apologize for being thorough.",
            },
            {
                "path": "docs/testing/testing-strategy.md",
                "section": "## Test Categories",
                "content_preview": "## Test Categories\n\nUnit tests for isolated logic. Integration tests with real APIs. E2E tests through Telegram flow.",
            },
        ]

        results = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(_rerank_single_candidate, client, change, c): c
                for c in chunks
            }
            for future in as_completed(futures):
                r = future.result()
                if r is not None:
                    results.append(r)

        # At least the bridge doc should be relevant, SOUL.md should not
        scored_paths = {r[2]["path"] for r in results}
        assert (
            "config/SOUL.md" not in scored_paths
        ), "SOUL.md shouldn't be flagged for a CLI flag change"

    def test_json_fence_stripping(self):
        """Verify the code fence stripping fix works with real Haiku responses."""
        from tools.doc_impact_finder import _rerank_single_candidate

        import anthropic

        client = anthropic.Anthropic()
        # Use a prompt that's clearly relevant to force a response
        result = _rerank_single_candidate(
            client,
            "Completely rewrote the deployment guide with new instructions",
            {
                "path": "docs/deployment.md",
                "section": "## Deployment Steps",
                "content_preview": "## Deployment Steps\n\n1. Clone the repo\n2. Run setup script\n3. Configure .env\n4. Start bridge",
            },
        )
        # If fence stripping works, we get a parsed result (not a parse error → None)
        assert (
            result is not None
        ), "JSON fence stripping should handle Haiku's markdown-wrapped responses"


# ---------------------------------------------------------------------------
# Index lifecycle tests (with real file system, mocked embeddings)
# ---------------------------------------------------------------------------


class TestIndexLifecycle:
    """Test the indexing pipeline with real filesystem operations."""

    def test_index_builds_from_repo_docs(self, tmp_path):
        """Index discovers and chunks real doc files."""
        from tools.doc_impact_finder import chunk_markdown, _discover_doc_files

        # Use the real repo to discover files
        files = _discover_doc_files(REPO_ROOT)
        assert len(files) > 50, f"Expected 50+ doc files, got {len(files)}"

        # Chunk a sample file
        sample = files[0]
        content = sample.read_text()
        chunks = chunk_markdown(content, str(sample.relative_to(REPO_ROOT)))
        assert len(chunks) >= 1
        for c in chunks:
            assert "content_hash" in c
            assert "section" in c
            assert len(c["content"]) > 0

    def test_incremental_reindex_skips_unchanged(self, tmp_path):
        """Content hashing means unchanged chunks are not re-embedded."""
        from unittest.mock import patch

        from tools.doc_impact_finder import index_docs

        # Create docs
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "stable.md").write_text(
            "# Stable\n\n## Section\n\nUnchanged content.\n"
        )
        (docs_dir / "changing.md").write_text(
            "# Changing\n\n## Section\n\nVersion 1.\n"
        )

        embed_call_count = 0
        original_texts = []

        def counting_embed(texts):
            nonlocal embed_call_count
            embed_call_count += len(texts)
            original_texts.extend(texts)
            return [[1.0, 0.0, 0.0] for _ in texts]

        # First index: embeds all chunks
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake"}, clear=False):
            with patch(
                "tools.doc_impact_finder._embed_openai", side_effect=counting_embed
            ):
                idx1 = index_docs(repo_root=tmp_path)

        first_count = embed_call_count
        assert first_count > 0, "Should have embedded some chunks"

        # Modify one file
        (docs_dir / "changing.md").write_text(
            "# Changing\n\n## Section\n\nVersion 2.\n"
        )

        embed_call_count = 0

        # Second index: should only re-embed the changed chunk
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake"}, clear=False):
            with patch(
                "tools.doc_impact_finder._embed_openai", side_effect=counting_embed
            ):
                idx2 = index_docs(repo_root=tmp_path)

        assert (
            embed_call_count < first_count
        ), f"Incremental re-index should embed fewer chunks: {embed_call_count} vs {first_count}"
        assert embed_call_count == 1, "Only the changed chunk should be re-embedded"

    def test_index_file_persistence(self, tmp_path):
        """Index is saved to disk and loadable."""
        from unittest.mock import patch

        from tools.doc_impact_finder import index_docs, load_index

        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "test.md").write_text("# Test\n\n## Section\n\nContent.\n")

        def fake_embed(texts):
            return [[0.5, 0.5, 0.5] for _ in texts]

        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake"}, clear=False):
            with patch("tools.doc_impact_finder._embed_openai", side_effect=fake_embed):
                index_docs(repo_root=tmp_path)

        # Verify file exists
        index_path = tmp_path / "data" / "doc_embeddings.json"
        assert index_path.exists()

        # Verify loadable
        loaded = load_index(repo_root=tmp_path)
        assert loaded["version"] == 1
        assert loaded["model"] == "text-embedding-3-small"
        assert len(loaded["chunks"]) > 0


# ---------------------------------------------------------------------------
# Full pipeline test with pre-built index + live Haiku
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — skipping live pipeline test",
)
class TestFullPipelineLive:
    """End-to-end test: build index with mocked embeddings, rerank with real Haiku."""

    def test_pipeline_with_real_haiku(self, tmp_path):
        """Full pipeline: mock embeddings, real Haiku reranking, real results."""
        from unittest.mock import patch

        from tools.doc_impact_finder import find_affected_docs, index_docs

        # Create docs that are clearly relevant/irrelevant to the query
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "session.md").write_text(
            "# Session Management\n\n"
            "## Thread Scoping\n\n"
            "Sessions are scoped by Telegram thread ID. Each thread gets an isolated "
            "task list keyed by thread-{chat_id}-{root_message_id}. This prevents "
            "cross-contamination between parallel conversations.\n\n"
            "## Worktree Isolation\n\n"
            "Planned work items get git worktrees under .worktrees/{slug}/ for "
            "filesystem isolation during builds.\n"
        )
        (docs_dir / "recipes.md").write_text(
            "# Cooking Recipes\n\n"
            "## Pancakes\n\n"
            "Mix flour, eggs, and milk. Cook on a hot griddle.\n\n"
            "## Spaghetti\n\n"
            "Boil pasta. Add tomato sauce.\n"
        )

        # Embeddings: make session.md chunks similar to query, recipes dissimilar
        def smart_embed(texts):
            embeddings = []
            for text in texts:
                if (
                    "session" in text.lower()
                    or "thread" in text.lower()
                    or "worktree" in text.lower()
                ):
                    embeddings.append([0.9, 0.1, 0.0])  # Similar to query
                else:
                    embeddings.append([0.0, 0.0, 0.9])  # Dissimilar
            return embeddings

        # Build index
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake"}, clear=False):
            with patch(
                "tools.doc_impact_finder._embed_openai", side_effect=smart_embed
            ):
                index_docs(repo_root=tmp_path)

        # Run full pipeline with REAL Haiku reranking
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake"}, clear=False):
            with patch(
                "tools.doc_impact_finder._embed_openai", side_effect=smart_embed
            ):
                results = find_affected_docs(
                    "Refactored session isolation to use slug-based worktrees instead of thread IDs",
                    repo_root=tmp_path,
                )

        # Session doc should be found, recipes should not
        result_paths = [r.path for r in results]
        assert any(
            "session" in p for p in result_paths
        ), f"Expected session.md in results, got: {result_paths}"
        assert not any(
            "recipes" in p for p in result_paths
        ), f"Recipes should NOT be in results: {result_paths}"

        # Verify result quality
        for r in results:
            assert 0.0 < r.relevance <= 1.0
            assert len(r.reason) > 10
            assert len(r.sections) > 0
