"""Lightweight keyword extraction utilities for memory hooks.

Extracted from agent/memory_hook.py to break the import chain.
This module depends only on stdlib and config — no agent/bridge/models imports.
"""

from __future__ import annotations

import os
import re
from typing import Any

from config.memory_defaults import CATEGORY_RECALL_WEIGHTS

# Known project root prefixes to strip before extracting path segments.
# Falls back to CWD-based detection if env var not set.
_PROJECT_ROOT = os.environ.get("VALOR_PROJECT_ROOT", "")


def _get_project_root() -> str:
    """Resolve the project root prefix for path stripping."""
    if _PROJECT_ROOT:
        root = _PROJECT_ROOT.rstrip("/") + "/"
        return root
    # Fallback: try common patterns
    cwd = os.getcwd()
    if "/src/ai" in cwd:
        return cwd.split("/src/ai")[0] + "/src/ai/"
    return ""


# Words too generic to be useful as topic keywords
_NOISE_WORDS = frozenset(
    {
        # English stopwords
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "not",
        # Python keywords
        "def",
        "class",
        "import",
        "return",
        "true",
        "false",
        "none",
        "self",
        # Filesystem noise
        "src",
        "tmp",
        "var",
        "usr",
        "bin",
        "etc",
        "test",
        "file",
        "line",
        # Tool names
        "read",
        "write",
        "bash",
        "python",
        "git",
        "grep",
        "edit",
        "glob",
        # Project-path stopwords (directory names)
        "users",
        "valorengels",
        "home",
        "desktop",
        "agent",
        "bridge",
        "models",
        "tools",
        "config",
        "tests",
        "hooks",
        "claude",
        "scripts",
        "docs",
        "data",
        "logs",
        "utils",
        "monitoring",
        "sessions",
        # Generic dev terms
        "init",
        "main",
        "index",
        "setup",
        "base",
        "core",
        "common",
        "abstract",
        "interface",
        "module",
        "package",
    }
)


def extract_topic_keywords(tool_name: str, tool_input: Any) -> list[str]:
    """Extract topic keywords from tool name and input.

    Pulls meaningful terms from file paths, grep patterns, command
    snippets, and other tool arguments. Filters out noise words
    including project-path stopwords.
    """
    keywords: list[str] = []

    # Add tool name parts
    if tool_name:
        parts = re.split(r"[_\-.]", tool_name.lower())
        keywords.extend(p for p in parts if len(p) > 2)

    if not isinstance(tool_input, dict):
        return keywords

    # Extract from common tool input fields
    for field in ("file_path", "path", "pattern", "command", "query", "content"):
        val = tool_input.get(field)
        if not val or not isinstance(val, str):
            continue

        if field in ("file_path", "path"):
            # Strip project root prefix before splitting
            path_val = val
            project_root = _get_project_root()
            if project_root and path_val.startswith(project_root):
                path_val = path_val[len(project_root) :]

            # Extract path segments (directories)
            segments = re.split(r"[/\\]", path_val)
            segments = [s for s in segments if s]  # remove empty

            # File stem (last segment without extension) is always meaningful
            if segments:
                file_stem = re.sub(r"\.[^.]+$", "", segments[-1])
                if len(file_stem) > 2:
                    # Keep compound file stem intact (e.g., agent_session_queue)
                    keywords.append(file_stem)

                # Add directory segments, filtered by noise words
                for seg in segments[:-1]:
                    # Don't split directory names, just filter
                    if len(seg) > 2 and not seg.startswith("_"):
                        keywords.append(seg)

        elif field == "pattern":
            # Grep patterns — extract words
            words = re.findall(r"[a-zA-Z]{3,}", val)
            keywords.extend(w.lower() for w in words)
        elif field == "command":
            # Command snippets — extract first few meaningful words
            words = re.findall(r"[a-zA-Z]{3,}", val[:200])
            keywords.extend(w.lower() for w in words[:5])

    # Deduplicate while preserving order, filter noise words
    seen: set[str] = set()
    unique: list[str] = []
    for k in keywords:
        kl = k.lower()
        if kl not in seen and kl not in _NOISE_WORDS:
            seen.add(kl)
            unique.append(kl)
    return unique[:10]  # cap at 10 keywords


def _cluster_keywords(keywords: list[str], max_clusters: int = 3) -> list[list[str]]:
    """Group keywords into topical clusters for multi-query retrieval.

    Simple positional splitting: divides the keyword list into chunks of ~3-5.
    Falls back to a single cluster when keyword count is small (<=5).

    Args:
        keywords: Deduplicated keyword list.
        max_clusters: Maximum number of clusters to produce.

    Returns:
        List of keyword clusters (each cluster is a list of strings).
    """
    if not keywords:
        return []
    if len(keywords) <= 5:
        return [keywords]  # single cluster, no decomposition needed

    # Split into clusters of ~3-5 keywords
    cluster_size = max(3, len(keywords) // max_clusters)
    clusters: list[list[str]] = []
    for i in range(0, len(keywords), cluster_size):
        chunk = keywords[i : i + cluster_size]
        if chunk:
            clusters.append(chunk)

    # Merge tiny trailing cluster into previous
    if len(clusters) > 1 and len(clusters[-1]) < 2:
        clusters[-2].extend(clusters.pop())

    return clusters[:max_clusters]


def _apply_category_weights(records: list) -> list:
    """Re-rank memory records by applying category-based weight multipliers.

    After RRF fusion returns scored results, multiply each record's
    effective score by its category weight, then re-sort descending.
    Records with missing or malformed metadata get the default weight (1.0).

    Args:
        records: List of Memory records with `score` attribute (RRF score).

    Returns:
        Re-sorted list of records (same objects, new order).
    """
    if not records:
        return records

    try:
        default_weight = CATEGORY_RECALL_WEIGHTS.get("default", 1.0)

        def _get_weight(record: Any) -> float:
            try:
                meta = getattr(record, "metadata", None)
                if not isinstance(meta, dict):
                    return default_weight
                category = meta.get("category", "")
                if not isinstance(category, str):
                    return default_weight
                return CATEGORY_RECALL_WEIGHTS.get(category, default_weight)
            except Exception:
                return default_weight

        def _get_score(record: Any) -> float:
            try:
                return float(getattr(record, "score", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        # Sort by weighted score descending
        return sorted(
            records,
            key=lambda r: _get_score(r) * _get_weight(r),
            reverse=True,
        )
    except Exception:
        # Fail silent -- return unmodified order
        return records
