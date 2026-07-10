"""Generic two-stage impact finder pipeline: embedding recall + LLM reranking.

Shared infrastructure for doc_impact_finder and code_impact_finder.
Configurable via callables passed to find_affected() and build_index().
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from config.models import HAIKU
from tools.knowledge.chunking import truncate_to_tokens

logger = logging.getLogger(__name__)

# Maximum number of texts to embed in a single API call
EMBEDDING_BATCH_SIZE = 100

# Minimum cosine similarity for embedding-only fallback (no Haiku reranking)
MIN_SIMILARITY_THRESHOLD = 0.3

# Maximum chars of section content sent to Haiku for reranking
HAIKU_CONTENT_PREVIEW_CHARS = 2000

# Warn if reindex exceeds this many chunks
COST_WARNING_THRESHOLD = 1000


# ---------------------------------------------------------------------------
# Degraded-result metadata (#2004 T1.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImpactFinderMeta:
    """Diagnostic metadata returned alongside ``find_affected()`` results.

    Callers branch on ``degraded`` to distinguish "no files affected" (a clean
    run that legitimately found nothing: ``degraded=False``) from "the finder
    is broken or fell back" (``degraded=True``). Every degraded/fallback branch
    inside ``find_affected`` names its cause in ``reason``.

    Attributes:
        degraded: True when any fallback or failure branch was taken. The
            accompanying results (possibly empty, embedding-only, or partial)
            are not a full clean two-stage answer.
        reason: Machine-readable name of the degraded branch, or ``None`` on a
            clean run. One of: ``no_embedding_provider``, ``empty_index``,
            ``query_embedding_failed``, ``no_scorable_candidates``,
            ``rerank_client_init_failed``, ``rerank_all_failed``,
            ``rerank_partial_failure``.
        rerank_failures: Number of Stage-2 rerank requests that hard-failed
            with a transport/API error (0 when Stage 2 never ran).
        candidates: Number of Stage-1 candidates selected for reranking
            (0 when Stage 1 never produced any).
    """

    degraded: bool
    reason: str | None
    rerank_failures: int
    candidates: int


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

    Handles batching internally -- splits into chunks of EMBEDDING_BATCH_SIZE.
    """
    import openai

    client = openai.OpenAI()

    texts = [truncate_to_tokens(t, 8000) for t in texts]

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

    Handles batching internally -- splits into chunks of EMBEDDING_BATCH_SIZE.
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
# Index management
# ---------------------------------------------------------------------------


def _default_index() -> dict:
    """Return an empty index structure."""
    return {"version": 1, "model": "", "chunks": []}


def load_index(index_name: str, repo_root: Path | None = None) -> dict:
    """Read a JSON index file from data/{index_name}.json.

    Returns empty index if file is missing or corrupt.
    """
    if repo_root is None:
        repo_root = Path.cwd()
    index_path = repo_root / "data" / f"{index_name}.json"
    if not index_path.exists():
        return _default_index()
    try:
        with open(index_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt or unreadable index file at %s", index_path)
        return _default_index()


def build_index(
    discover_files: Callable[[Path], list[Path]],
    chunk_file: Callable[[str, str], list[dict]],
    index_name: str,
    repo_root: Path | None = None,
    embed_provider: tuple | None = None,
) -> dict:
    """Walk files, chunk, diff against cache, embed new/changed chunks, save index.

    Uses content hashing for incremental re-embedding.
    Detects model mismatch and discards stale cache.
    Logs a cost warning if more than COST_WARNING_THRESHOLD chunks need embedding.

    Args:
        discover_files: Callable that takes repo_root and returns list of file paths.
        chunk_file: Callable that takes (content, rel_path) and returns list of chunk dicts.
            Each chunk dict must have: path, section, content, content_hash.
        index_name: Name for the index file (stored as data/{index_name}.json).
        repo_root: Repository root path. Defaults to cwd.
        embed_provider: Optional (embed_fn, model_name) tuple. Auto-detected if None.

    Returns:
        The saved index dict.
    """
    if repo_root is None:
        repo_root = Path.cwd()

    provider = embed_provider or get_embedding_provider()
    if provider is None:
        logger.warning(
            "No embedding API key available; skipping indexing. "
            "Set OPENAI_API_KEY or VOYAGE_API_KEY."
        )
        return _default_index()

    embed_fn, model_name = provider

    # Load existing index for hash comparison
    existing = load_index(index_name, repo_root)
    existing_by_key: dict[str, dict] = {}
    if existing.get("model") == model_name:
        for chunk in existing.get("chunks", []):
            key = f"{chunk['path']}::{chunk['section']}"
            existing_by_key[key] = chunk
    elif existing.get("model"):
        logger.info(
            "Model mismatch: index has %s, current provider is %s. Rebuilding.",
            existing.get("model"),
            model_name,
        )

    # Discover and chunk all files
    discovered_files = discover_files(repo_root)
    all_chunks: list[dict] = []
    for file_path in discovered_files:
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Could not read %s, skipping", file_path)
            continue
        rel_path = str(file_path.relative_to(repo_root))
        chunks = chunk_file(content, rel_path)
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

    if to_embed_indices:
        if len(to_embed_indices) > COST_WARNING_THRESHOLD:
            logger.warning(
                "Reindexing %d chunks (exceeds threshold of %d). This may incur costs.",
                len(to_embed_indices),
                COST_WARNING_THRESHOLD,
            )
        logger.info("Reindexing %d/%d chunks", len(to_embed_indices), len(all_chunks))
        texts_to_embed = [all_chunks[i]["content"] for i in to_embed_indices]
        try:
            embeddings = embed_fn(texts_to_embed)
            for idx, emb in zip(to_embed_indices, embeddings):
                all_chunks[idx]["embedding"] = emb
        except Exception:
            logger.exception("Failed to embed %d chunks", len(to_embed_indices))
            return _default_index()

    # Build final index -- store content preview for Haiku reranking
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
    index_path = data_dir / f"{index_name}.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    logger.info(
        "Indexed %d chunks (%d new/changed) from %d files",
        len(index_chunks),
        len(to_embed_indices),
        len(discovered_files),
    )
    return index


# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------


def _rerank_single_candidate(
    client,
    prompt: str,
    chunk: dict,
) -> tuple[float, str, dict] | None:
    """Rerank a single candidate using Claude Haiku.

    Sends the prompt to Haiku, parses the JSON response, and returns
    (score, reason, chunk) if score >= 5, else None.
    """
    try:
        response = client.messages.create(
            model=HAIKU,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present (Haiku often wraps JSON)
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        parsed = json.loads(text)
        score = float(parsed.get("score", 0))
        reason = parsed.get("reason", "")
        if score >= 5:
            # Pass through any extra fields (e.g. impact_type) via chunk
            if "impact_type" in parsed:
                chunk = {**chunk, "haiku_impact_type": parsed["impact_type"]}
            return (score, reason, chunk)
    except (json.JSONDecodeError, KeyError, IndexError, ValueError):
        # The rerank request ran but returned an unparseable or malformed
        # response (bad JSON, missing/non-numeric score). This is "ran, did not
        # qualify," not a transport failure. Return None so it is treated the
        # same as a below-threshold score, never counted as a hard failure.
        logger.warning(
            "Could not parse Haiku response for %s %s",
            chunk.get("path", "?"),
            chunk.get("section", "?"),
        )
        return None
    except Exception:
        # Transport/API error (e.g. a misconfigured ANTHROPIC_BASE_URL that 404s
        # on the Haiku model): the rerank could not run at all. Re-raise instead
        # of swallowing so the caller can distinguish "reranker is down" from
        # "nothing scored" and route to the embedding-only fallback. Logged at
        # warning (not exception) to avoid duplicate noise once find_affected
        # logs the aggregate failure.
        logger.warning(
            "Haiku rerank request failed for %s %s (re-raising for fallback detection)",
            chunk.get("path", "?"),
            chunk.get("section", "?"),
        )
        raise
    return None


def _rerank_candidates(
    client,
    change_summary: str,
    candidates: list[tuple[float, dict]],
    prompt_builder: Callable[[str, dict], str],
) -> tuple[list[tuple[float, str, dict]], int]:
    """Parallel Haiku reranking with ThreadPoolExecutor(max_workers=5).

    Args:
        client: Anthropic client instance.
        change_summary: Description of the change.
        candidates: List of (similarity_score, chunk_dict) tuples.
        prompt_builder: Callable that takes (change_summary, chunk) and returns prompt string.

    Returns:
        A ``(results, failure_count)`` tuple where ``results`` is the list of
        ``(score, reason, chunk)`` tuples for candidates scoring >= 5, and
        ``failure_count`` is the number of candidates whose rerank request raised
        a transport/API error (the reranker could not run). ``find_affected``
        uses ``failure_count == len(candidates)`` to tell "reranker is down"
        apart from "nothing scored," which look identical from the results list
        alone (both yield an empty ``results``).
    """
    results: list[tuple[float, str, dict]] = []
    failure_count = 0

    def _do_rerank(client, change_summary, chunk):
        return _rerank_single_candidate(client, prompt_builder(change_summary, chunk), chunk)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_do_rerank, client, change_summary, chunk): chunk
            for _sim_score, chunk in candidates
        }
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                # A transport/API error re-raised by _rerank_single_candidate.
                # Count it as a hard failure and keep collecting the remaining
                # futures; one bad request must not abort the whole batch.
                failure_count += 1
                continue
            if result is not None:
                results.append(result)

    # Sort by score descending
    results.sort(key=lambda x: x[0], reverse=True)
    return results, failure_count


# ---------------------------------------------------------------------------
# Two-stage pipeline
# ---------------------------------------------------------------------------


def find_affected(
    change_summary: str,
    discover_files: Callable[[Path], list[Path]],
    chunk_file: Callable[[str, str], list[dict]],
    rerank_prompt_builder: Callable[[str, dict], str],
    index_name: str,
    result_builder: Callable[[list[tuple[float, str, dict]]], list],
    fallback_builder: Callable[[list[tuple[float, dict]]], list],
    top_n: int = 15,
    repo_root: Path | None = None,
    embed_provider: tuple | None = None,
) -> tuple[list, ImpactFinderMeta]:
    """Two-stage impact finder: embed query, cosine recall, Haiku rerank, build results.

    Args:
        change_summary: Description of the change to find impact for.
        discover_files: Callable to find files to scan (takes repo_root).
        chunk_file: Callable to chunk a file (takes content, rel_path).
        rerank_prompt_builder: Callable to build Haiku prompt (takes change_summary, chunk).
        index_name: Name of the embedding index file.
        result_builder: Callable to convert reranked results to output models.
            Takes list of (score, reason, chunk) tuples.
        fallback_builder: Callable to convert embedding-only candidates to output models.
            Takes list of (similarity, chunk) tuples. Used when Haiku is unavailable.
        top_n: Number of top candidates to pass to Haiku for reranking.
        repo_root: Repository root path. Defaults to cwd.
        embed_provider: Optional (embed_fn, model_name) tuple. Auto-detected if None.

    Returns:
        A ``(results, meta)`` tuple. ``results`` is the list of result models
        (type depends on result_builder); ``meta`` is an :class:`ImpactFinderMeta`
        whose ``degraded``/``reason`` fields let the caller distinguish "no
        files affected" (``degraded=False``) from "the finder is broken or fell
        back" (``degraded=True``) on every branch — a bare ``[]`` is never
        ambiguous.

    Fallback behavior:
        The embedding-only ``fallback_builder`` is used in two cases: (1) the
        Anthropic client cannot be constructed (``rerank_client_init_failed``),
        and (2) *every* Stage 2 rerank request hard-fails with a transport/API
        error (``rerank_all_failed``, e.g. a misconfigured ``ANTHROPIC_BASE_URL``
        that 404s on the Haiku model). The second case is an all-or-nothing gate
        by design: a partial failure where at least one candidate still reranks
        keeps the reranked results (flagged ``rerank_partial_failure``), and a
        clean run where nothing scores >= 5 legitimately returns ``[]`` with
        ``degraded=False`` (never a false-positive fallback dump).
    """
    if repo_root is None:
        repo_root = Path.cwd()

    provider = embed_provider or get_embedding_provider()
    if provider is None:
        logger.warning(
            "No embedding API key available; cannot find affected files. "
            "Set OPENAI_API_KEY or VOYAGE_API_KEY."
        )
        return [], ImpactFinderMeta(
            degraded=True, reason="no_embedding_provider", rerank_failures=0, candidates=0
        )

    embed_fn, _model_name = provider

    # Load index
    index = load_index(index_name, repo_root)
    chunks = index.get("chunks", [])
    if not chunks:
        logger.warning("Index '%s' is empty. Run build_index() first.", index_name)
        return [], ImpactFinderMeta(
            degraded=True, reason="empty_index", rerank_failures=0, candidates=0
        )

    # Stage 1: Embedding recall
    try:
        query_embedding = embed_fn([change_summary])[0]
    except Exception:
        logger.exception("Failed to embed change summary")
        return [], ImpactFinderMeta(
            degraded=True, reason="query_embedding_failed", rerank_failures=0, candidates=0
        )

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
        # The index had chunks but none carried an embedding — an unusable
        # index is a broken finder, not "no files affected".
        logger.warning("Index '%s' has no embedded chunks; cannot score candidates.", index_name)
        return [], ImpactFinderMeta(
            degraded=True, reason="no_scorable_candidates", rerank_failures=0, candidates=0
        )

    # Stage 2: LLM reranking with Claude Haiku (parallelized)
    try:
        import anthropic

        client = anthropic.Anthropic()
    except Exception:
        logger.exception("Failed to initialize Anthropic client for reranking")
        # Fall back to embedding-only results
        return fallback_builder(candidates), ImpactFinderMeta(
            degraded=True,
            reason="rerank_client_init_failed",
            rerank_failures=0,
            candidates=len(candidates),
        )

    results, failure_count = _rerank_candidates(
        client, change_summary, candidates, rerank_prompt_builder
    )

    # All-or-nothing fallback gate: only when *every* rerank request hard-failed
    # (transport/API error, e.g. a misconfigured ANTHROPIC_BASE_URL that 404s on
    # the Haiku model) do we route to the embedding-only fallback. A clean run
    # where nothing scored >= 5 legitimately returns []. A partial failure (some
    # requests fail, at least one succeeds) keeps the reranked results and does
    # NOT fall back — but is flagged degraded so the caller can see the gap.
    if failure_count == len(candidates):
        logger.warning(
            "All %d Haiku rerank requests failed (check ANTHROPIC_BASE_URL / "
            "model id); falling back to embedding-only candidates.",
            len(candidates),
        )
        return fallback_builder(candidates), ImpactFinderMeta(
            degraded=True,
            reason="rerank_all_failed",
            rerank_failures=failure_count,
            candidates=len(candidates),
        )

    return result_builder(results), ImpactFinderMeta(
        degraded=failure_count > 0,
        reason="rerank_partial_failure" if failure_count else None,
        rerank_failures=failure_count,
        candidates=len(candidates),
    )


# ---------------------------------------------------------------------------
# Index status
# ---------------------------------------------------------------------------


def get_index_status(index_name: str, repo_root: Path | None = None) -> dict:
    """Return status information about an index.

    Returns dict with:
        - exists: whether the index file exists
        - chunk_count: number of chunks in the index
        - model: embedding model used
        - index_path: path to the index file
    """
    if repo_root is None:
        repo_root = Path.cwd()

    index_path = repo_root / "data" / f"{index_name}.json"
    if not index_path.exists():
        return {
            "exists": False,
            "chunk_count": 0,
            "model": "",
            "index_path": str(index_path),
        }

    index = load_index(index_name, repo_root)
    return {
        "exists": True,
        "chunk_count": len(index.get("chunks", [])),
        "model": index.get("model", ""),
        "index_path": str(index_path),
    }
