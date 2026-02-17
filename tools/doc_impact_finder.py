"""Semantic doc impact finder: thin wrapper over impact_finder_core.

Provides doc-specific configuration for the two-stage pipeline:
- DOC_PATTERNS + _discover_doc_files() define which files to index
- _doc_rerank_prompt() builds the doc-specific Haiku reranking prompt
- _build_affected_docs() converts reranked results to AffectedDoc models

All generic pipeline infrastructure lives in impact_finder_core.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from tools.impact_finder_core import (
    EMBEDDING_BATCH_SIZE,
    HAIKU_CONTENT_PREVIEW_CHARS,
    MIN_SIMILARITY_THRESHOLD,
    chunk_markdown,
    cosine_similarity,
    get_embedding_provider,
)
from tools.impact_finder_core import build_index as _core_build_index
from tools.impact_finder_core import find_affected as _core_find_affected
from tools.impact_finder_core import load_index as _core_load_index

__all__ = [
    "EMBEDDING_BATCH_SIZE",
    "HAIKU_CONTENT_PREVIEW_CHARS",
    "MIN_SIMILARITY_THRESHOLD",
    "AffectedDoc",
    "chunk_markdown",
    "cosine_similarity",
    "find_affected_docs",
    "get_embedding_provider",
    "index_docs",
    "load_index",
]


class AffectedDoc(BaseModel):
    """A documentation file affected by a code change."""

    path: str  # docs/features/session-isolation.md
    relevance: float  # 0.0 - 1.0
    sections: list[str]  # ["## Tier 1 (thread-scoped)", "## Git worktrees"]
    reason: str  # "Describes thread-scoped task lists; change alters thread ID derivation"


# ---------------------------------------------------------------------------
# Doc-specific file discovery
# ---------------------------------------------------------------------------

DOC_PATTERNS = [
    "docs/**/*.md",
    "CLAUDE.md",
    ".claude/skills/*/SKILL.md",
    ".claude/commands/*.md",
    "config/SOUL.md",
]


def _discover_doc_files(repo_root: Path) -> list[Path]:
    """Find all documentation files matching DOC_PATTERNS."""
    files: list[Path] = []
    for pattern in DOC_PATTERNS:
        files.extend(repo_root.glob(pattern))
    # Deduplicate and sort for deterministic ordering
    return sorted(set(files))


# ---------------------------------------------------------------------------
# Doc-specific reranking prompt
# ---------------------------------------------------------------------------


def _doc_rerank_prompt(change_summary: str, chunk: dict) -> str:
    """Build the doc-specific Haiku reranking prompt."""
    return (
        f"You are evaluating whether a documentation section needs updating "
        f"given a code change.\n\n"
        f"## Code Change\n{change_summary}\n\n"
        f"## Documentation Section\n"
        f"File: {chunk['path']}\n"
        f"Section: {chunk['section']}\n"
        f"Content preview: {chunk['content_preview']}\n\n"
        f"Does this doc section need updating given this change? "
        f"Respond with ONLY a JSON object: "
        f'{{"score": <0-10>, "reason": "<brief explanation>"}}'
    )


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------


def _build_affected_docs(results: list[tuple[float, str, dict]]) -> list[AffectedDoc]:
    """Convert reranked results to AffectedDoc list, grouping by file and joining reasons."""
    doc_map: dict[str, dict] = {}
    for score, reason, chunk in results:
        path = chunk["path"]
        if path not in doc_map:
            doc_map[path] = {
                "path": path,
                "max_score": score,
                "sections": [],
                "reasons": [],
            }
        doc_map[path]["sections"].append(chunk["section"])
        doc_map[path]["reasons"].append(reason)
        doc_map[path]["max_score"] = max(doc_map[path]["max_score"], score)

    affected: list[AffectedDoc] = []
    for info in doc_map.values():
        affected.append(
            AffectedDoc(
                path=info["path"],
                relevance=info["max_score"] / 10.0,
                sections=info["sections"],
                reason="; ".join(info["reasons"]),
            )
        )

    affected.sort(key=lambda x: x.relevance, reverse=True)
    return affected


def _candidates_to_affected_docs(
    candidates: list[tuple[float, dict]],
) -> list[AffectedDoc]:
    """Convert embedding-only candidates to AffectedDoc (fallback when Haiku unavailable).

    Applies MIN_SIMILARITY_THRESHOLD to filter out irrelevant results.
    """
    doc_map: dict[str, dict] = {}
    for sim, chunk in candidates:
        if sim < MIN_SIMILARITY_THRESHOLD:
            continue
        path = chunk["path"]
        if path not in doc_map:
            doc_map[path] = {
                "path": path,
                "max_sim": sim,
                "sections": [],
            }
        doc_map[path]["sections"].append(chunk["section"])
        doc_map[path]["max_sim"] = max(doc_map[path]["max_sim"], sim)

    affected: list[AffectedDoc] = []
    for info in doc_map.values():
        affected.append(
            AffectedDoc(
                path=info["path"],
                relevance=round(info["max_sim"], 3),
                sections=info["sections"],
                reason="Matched by embedding similarity (LLM reranking unavailable)",
            )
        )

    affected.sort(key=lambda x: x.relevance, reverse=True)
    return affected


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_index(repo_root: Path | None = None) -> dict:
    """Read the doc embedding index. Returns empty index if file is missing."""
    return _core_load_index("doc_embeddings", repo_root)


def index_docs(repo_root: Path | None = None) -> dict:
    """Walk doc locations, chunk on ## headings, embed, and save index.

    Uses content hashing to skip re-embedding unchanged chunks.
    Batches embedding calls to stay within API limits.
    Returns the saved index dict.
    """
    return _core_build_index(
        discover_files=_discover_doc_files,
        chunk_file=chunk_markdown,
        index_name="doc_embeddings",
        repo_root=repo_root,
        embed_provider=get_embedding_provider(),
    )


def find_affected_docs(
    change_summary: str,
    top_n: int = 15,
    repo_root: Path | None = None,
) -> list[AffectedDoc]:
    """Find documentation affected by a code change using a two-stage pipeline.

    Stage 1: Embed the change summary, compute cosine similarity against all
             indexed doc chunks, take top-N candidates.
    Stage 2: For each candidate, ask Claude Haiku to score relevance (0-10)
             and explain why. Calls are parallelized for speed. Filter to score >= 5.

    Returns a list of AffectedDoc sorted by relevance (highest first).
    Returns empty list if no embedding API key is available.
    """
    return _core_find_affected(
        change_summary=change_summary,
        discover_files=_discover_doc_files,
        chunk_file=chunk_markdown,
        rerank_prompt_builder=_doc_rerank_prompt,
        index_name="doc_embeddings",
        result_builder=_build_affected_docs,
        fallback_builder=_candidates_to_affected_docs,
        top_n=top_n,
        repo_root=repo_root,
        embed_provider=get_embedding_provider(),
    )
