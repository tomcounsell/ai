"""Semantic doc impact finder: thin wrapper over impact_finder_core.

Provides doc-specific configuration for the two-stage pipeline:
- DOC_PATTERNS + _discover_doc_files() define which files to index
- _doc_rerank_prompt() builds the doc-specific Haiku reranking prompt
- _build_affected_docs() converts reranked results to AffectedDoc models

All generic pipeline infrastructure lives in impact_finder_core.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel

from tools.impact_finder_core import (
    EMBEDDING_BATCH_SIZE,
    HAIKU_CONTENT_PREVIEW_CHARS,
    MIN_SIMILARITY_THRESHOLD,
    ImpactFinderMeta,
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
    "ImpactFinderMeta",
    "chunk_doc",
    "chunk_markdown",
    "cosine_similarity",
    "find_affected_docs",
    "get_embedding_provider",
    "index_docs",
    "load_index",
    "preprocess_html",
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
    "config/identity.json",
    "config/personas/segments/identity.md",
    "config/personas/segments/work-patterns.md",
    "config/personas/segments/tools.md",
    # Published docs site pages (valorengels.com). Scoped to *.html so the
    # 38k-line generated site/assets/graph.js never reaches the embedder.
    "site/*.html",
]


def _discover_doc_files(repo_root: Path) -> list[Path]:
    """Find all documentation files matching DOC_PATTERNS."""
    files: list[Path] = []
    for pattern in DOC_PATTERNS:
        files.extend(repo_root.glob(pattern))
    # Deduplicate and sort for deterministic ordering
    return sorted(set(files))


# ---------------------------------------------------------------------------
# HTML preprocessing (site/*.html → heading-delimited text for chunk_markdown)
# ---------------------------------------------------------------------------


class _HtmlToText(HTMLParser):
    """Flatten HTML to heading-delimited plain text.

    Heading-mapping contract: ``<h2>`` text is emitted as a ``## `` line and
    ``<h3>`` as ``### `` so the existing :func:`chunk_markdown` splitter (which
    breaks on ``## ``) produces one chunk per top-level site section. Bodies of
    ``<script>`` and ``<style>`` are dropped entirely so JS/CSS never pollutes
    the embedding index.
    """

    _HEADING_PREFIX = {"h2": "## ", "h3": "### "}
    _SKIP_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._heading_tag: str | None = None
        self._heading_text: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: D102
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._HEADING_PREFIX and self._heading_tag is None:
            self._heading_tag = tag
            self._heading_text = []

    def handle_endtag(self, tag):  # noqa: D102
        if tag in self._SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
        elif tag == self._heading_tag:
            text = " ".join("".join(self._heading_text).split())
            self._parts.append(f"\n{self._HEADING_PREFIX[tag]}{text}\n")
            self._heading_tag = None
            self._heading_text = []

    def handle_data(self, data):  # noqa: D102
        if self._skip_depth:
            return
        if self._heading_tag is not None:
            self._heading_text.append(data)
        elif data.strip():
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def preprocess_html(content: str) -> str:
    """Convert HTML to heading-delimited plain text for :func:`chunk_markdown`.

    Strips all tags; ``<h2>``/``<h3>`` become ``## ``/``### `` lines (see the
    :class:`_HtmlToText` heading-mapping contract) and ``<script>``/``<style>``
    bodies are discarded. Tolerant of malformed or empty input: stdlib
    ``HTMLParser`` never raises on garbage, so the worst case is empty output,
    never an exception.
    """
    parser = _HtmlToText()
    parser.feed(content)
    parser.close()
    return parser.get_text()


def chunk_doc(content: str, path: str) -> list[dict]:
    """Chunk a doc file, dispatching ``.html`` through the HTML preprocessor.

    Single-suffix dispatch (no plugin/registry abstraction): ``.html`` files are
    flattened to heading-delimited text first; everything else is chunked as
    markdown. This is the one seam that teaches the doc-impact index about
    ``site/*.html`` pages.
    """
    if path.endswith(".html"):
        return chunk_markdown(preprocess_html(content), path)
    return chunk_markdown(content, path)


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
        chunk_file=chunk_doc,
        index_name="doc_embeddings",
        repo_root=repo_root,
        embed_provider=get_embedding_provider(),
    )


def find_affected_docs(
    change_summary: str,
    top_n: int = 15,
    repo_root: Path | None = None,
) -> tuple[list[AffectedDoc], ImpactFinderMeta]:
    """Find documentation affected by a code change using a two-stage pipeline.

    Stage 1: Embed the change summary, compute cosine similarity against all
             indexed doc chunks, take top-N candidates.
    Stage 2: For each candidate, ask Claude Haiku to score relevance (0-10)
             and explain why. Calls are parallelized for speed. Filter to score >= 5.

    Returns a ``(results, meta)`` tuple: ``results`` is a list of AffectedDoc
    sorted by relevance (highest first); ``meta`` is the core pipeline's
    :class:`ImpactFinderMeta`, propagated verbatim so degradation stays visible
    through this wrapper (#2004 T1.4). Check ``meta.degraded`` before trusting
    an empty result — ``([], degraded=False)`` means "no docs affected", while
    ``([], degraded=True)`` means the finder itself could not run cleanly
    (``meta.reason`` names the branch, e.g. ``no_embedding_provider`` or
    ``empty_index``).
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
