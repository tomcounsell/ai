"""Semantic doc impact finder: two-stage pipeline for identifying docs affected by code changes.

Stage 1: Embedding recall — cosine similarity between change summary and doc chunks.
Stage 2: LLM reranking — Claude Haiku scores each candidate for relevance.

Chunks docs on ## headings, caches embeddings with content hashing for efficiency.
Gracefully degrades when no embedding API key is available.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Maximum number of texts to embed in a single API call
EMBEDDING_BATCH_SIZE = 100

# Minimum cosine similarity for embedding-only fallback (no Haiku reranking)
MIN_SIMILARITY_THRESHOLD = 0.3

# Maximum chars of section content sent to Haiku for reranking
HAIKU_CONTENT_PREVIEW_CHARS = 2000


class AffectedDoc(BaseModel):
    """A documentation file affected by a code change."""

    path: str  # docs/features/session-isolation.md
    relevance: float  # 0.0 - 1.0
    sections: list[str]  # ["## Tier 1 (thread-scoped)", "## Git worktrees"]
    reason: (
        str  # "Describes thread-scoped task lists; change alters thread ID derivation"
    )


# ---------------------------------------------------------------------------
# Markdown chunking
# ---------------------------------------------------------------------------


def chunk_markdown(content: str, file_path: str) -> list[dict]:
    """Split markdown content on ## headings into chunks.

    Each chunk contains:
        - path: the file path
        - section: the heading text (e.g. "## Overview") or "" for preamble
        - content: the full text of that section (including the heading line)
        - content_hash: SHA-256 hex digest of the content
    """
    lines = content.split("\n")
    chunks: list[dict] = []
    current_section = ""
    current_lines: list[str] = []

    def _flush():
        text = "\n".join(current_lines)
        if text.strip():
            chunks.append(
                {
                    "path": file_path,
                    "section": current_section,
                    "content": text,
                    "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                }
            )

    for line in lines:
        if line.startswith("## "):
            _flush()
            current_section = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    _flush()
    return chunks


# ---------------------------------------------------------------------------
# Embedding provider detection
# ---------------------------------------------------------------------------


def get_embedding_provider() -> tuple | None:
    """Detect available embedding API and return (embed_function, model_name).

    Priority order: OPENAI_API_KEY, VOYAGE_API_KEY.
    Returns None if no provider is available.
    """
    if os.environ.get("OPENAI_API_KEY"):
        return _embed_openai, "text-embedding-3-small"

    if os.environ.get("VOYAGE_API_KEY"):
        try:
            import voyageai  # noqa: F401

            return _embed_voyage, "voyage-3-lite"
        except ImportError:
            logger.warning("VOYAGE_API_KEY set but voyageai package not installed")

    return None


def _embed_openai(texts: list[str]) -> list[list[float]]:
    """Embed texts using OpenAI's text-embedding-3-small model.

    Handles batching internally — splits into chunks of EMBEDDING_BATCH_SIZE.
    """
    import openai

    client = openai.OpenAI()

    if len(texts) <= EMBEDDING_BATCH_SIZE:
        response = client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [item.embedding for item in response.data]

    # Batch large requests
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[i : i + EMBEDDING_BATCH_SIZE]
        response = client.embeddings.create(model="text-embedding-3-small", input=batch)
        all_embeddings.extend(item.embedding for item in response.data)
    return all_embeddings


def _embed_voyage(texts: list[str]) -> list[list[float]]:
    """Embed texts using Voyage AI.

    Handles batching internally — splits into chunks of EMBEDDING_BATCH_SIZE.
    """
    import voyageai

    client = voyageai.Client()

    if len(texts) <= EMBEDDING_BATCH_SIZE:
        result = client.embed(texts, model="voyage-3-lite")
        return result.embeddings

    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[i : i + EMBEDDING_BATCH_SIZE]
        result = client.embed(batch, model="voyage-3-lite")
        all_embeddings.extend(result.embeddings)
    return all_embeddings


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors using numpy.

    Returns dot(a, b) / (norm(a) * norm(b)).
    """
    va = np.array(a, dtype=np.float64)
    vb = np.array(b, dtype=np.float64)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Doc locations to index
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
# Index management
# ---------------------------------------------------------------------------


def _default_index() -> dict:
    """Return an empty index structure."""
    return {"version": 1, "model": "", "chunks": []}


def load_index(repo_root: Path | None = None) -> dict:
    """Read the flat JSON index file. Returns empty index if file is missing."""
    if repo_root is None:
        repo_root = Path.cwd()
    index_path = repo_root / "data" / "doc_embeddings.json"
    if not index_path.exists():
        return _default_index()
    try:
        with open(index_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt or unreadable index file at %s", index_path)
        return _default_index()


def index_docs(repo_root: Path | None = None) -> dict:
    """Walk doc locations, chunk on ## headings, embed, and save index.

    Uses content hashing to skip re-embedding unchanged chunks.
    Batches embedding calls to stay within API limits.
    Returns the saved index dict.
    """
    if repo_root is None:
        repo_root = Path.cwd()

    provider = get_embedding_provider()
    if provider is None:
        logger.warning(
            "No embedding API key available; skipping doc indexing. "
            "Set OPENAI_API_KEY or VOYAGE_API_KEY."
        )
        return _default_index()

    embed_fn, model_name = provider

    # Load existing index for hash comparison
    existing = load_index(repo_root)
    existing_by_key: dict[str, dict] = {}
    if existing.get("model") == model_name:
        for chunk in existing.get("chunks", []):
            key = f"{chunk['path']}::{chunk['section']}"
            existing_by_key[key] = chunk

    # Discover and chunk all docs
    doc_files = _discover_doc_files(repo_root)
    all_chunks: list[dict] = []
    for doc_file in doc_files:
        try:
            content = doc_file.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Could not read %s, skipping", doc_file)
            continue
        rel_path = str(doc_file.relative_to(repo_root))
        chunks = chunk_markdown(content, rel_path)
        all_chunks.extend(chunks)

    # Determine which chunks need embedding
    to_embed_indices: list[int] = []
    for i, chunk in enumerate(all_chunks):
        key = f"{chunk['path']}::{chunk['section']}"
        cached = existing_by_key.get(key)
        if cached and cached.get("content_hash") == chunk["content_hash"]:
            # Reuse cached embedding
            chunk["embedding"] = cached["embedding"]
        else:
            to_embed_indices.append(i)

    # Batch embed new/changed chunks (embed_fn handles internal batching)
    if to_embed_indices:
        texts_to_embed = [all_chunks[i]["content"] for i in to_embed_indices]
        try:
            embeddings = embed_fn(texts_to_embed)
            for idx, emb in zip(to_embed_indices, embeddings):
                all_chunks[idx]["embedding"] = emb
        except Exception:
            logger.exception("Failed to embed %d chunks", len(to_embed_indices))
            return _default_index()

    # Build final index — store more content for Haiku reranking
    index_chunks = []
    for chunk in all_chunks:
        index_chunks.append(
            {
                "path": chunk["path"],
                "section": chunk["section"],
                "content_hash": chunk["content_hash"],
                "embedding": chunk.get("embedding", []),
                "content_preview": chunk["content"][:HAIKU_CONTENT_PREVIEW_CHARS],
            }
        )

    index = {"version": 1, "model": model_name, "chunks": index_chunks}

    # Save
    data_dir = repo_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    index_path = data_dir / "doc_embeddings.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    logger.info(
        "Indexed %d chunks (%d new/changed) from %d files",
        len(index_chunks),
        len(to_embed_indices),
        len(doc_files),
    )
    return index


# ---------------------------------------------------------------------------
# Two-stage doc impact finding
# ---------------------------------------------------------------------------


def _rerank_single_candidate(
    client,
    change_summary: str,
    chunk: dict,
) -> tuple[float, str, dict] | None:
    """Rerank a single candidate using Claude Haiku. Returns (score, reason, chunk) or None."""
    prompt = (
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
    try:
        response = client.messages.create(
            model="claude-haiku-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        parsed = json.loads(text)
        score = float(parsed.get("score", 0))
        reason = parsed.get("reason", "")
        if score >= 5:
            return (score, reason, chunk)
    except (json.JSONDecodeError, KeyError, IndexError):
        logger.warning(
            "Could not parse Haiku response for %s %s",
            chunk["path"],
            chunk["section"],
        )
    except Exception:
        logger.exception(
            "Haiku reranking failed for %s %s",
            chunk["path"],
            chunk["section"],
        )
    return None


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
    if repo_root is None:
        repo_root = Path.cwd()

    provider = get_embedding_provider()
    if provider is None:
        logger.warning(
            "No embedding API key available; cannot find affected docs. "
            "Set OPENAI_API_KEY or VOYAGE_API_KEY."
        )
        return []

    embed_fn, _model_name = provider

    # Load index
    index = load_index(repo_root)
    chunks = index.get("chunks", [])
    if not chunks:
        logger.warning("Doc index is empty. Run index_docs() first.")
        return []

    # Stage 1: Embedding recall
    try:
        query_embedding = embed_fn([change_summary])[0]
    except Exception:
        logger.exception("Failed to embed change summary")
        return []

    scored: list[tuple[float, dict]] = []
    for chunk in chunks:
        emb = chunk.get("embedding", [])
        if not emb:
            continue
        sim = cosine_similarity(query_embedding, emb)
        scored.append((sim, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = scored[:top_n]

    if not candidates:
        return []

    # Stage 2: LLM reranking with Claude Haiku (parallelized)
    try:
        import anthropic

        client = anthropic.Anthropic()
    except Exception:
        logger.exception("Failed to initialize Anthropic client for reranking")
        # Fall back to embedding-only results
        return _candidates_to_affected_docs(candidates)

    results: list[tuple[float, str, dict]] = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(
                _rerank_single_candidate, client, change_summary, chunk
            ): chunk
            for _sim_score, chunk in candidates
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

    # Sort by score descending
    results.sort(key=lambda x: x[0], reverse=True)

    # Group by file path
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

    # Convert to AffectedDoc list — join all reasons
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
