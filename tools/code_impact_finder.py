"""Semantic code impact finder: thin wrapper over impact_finder_core.

Provides code-specific configuration for the two-stage pipeline:
- CODE_PATTERNS + _discover_code_files() define which files to index
- _chunk_python() uses AST-based parsing for Python files
- _code_rerank_prompt() builds the code-specific Haiku reranking prompt
- _build_affected_code() converts reranked results to AffectedCode models

All generic pipeline infrastructure lives in impact_finder_core.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import re
from pathlib import Path

from pydantic import BaseModel

from tools.impact_finder_core import (
    MIN_SIMILARITY_THRESHOLD,
    _embed_openai,
    _embed_voyage,
    chunk_markdown,
)
from tools.impact_finder_core import build_index as _core_build_index
from tools.impact_finder_core import find_affected as _core_find_affected
from tools.impact_finder_core import get_index_status as _core_get_index_status
from tools.impact_finder_core import load_index as _core_load_index

logger = logging.getLogger(__name__)

INDEX_NAME = "code_embeddings"

# ---------------------------------------------------------------------------
# Code file patterns and exclusions
# ---------------------------------------------------------------------------

CODE_PATTERNS: dict[str, str | list[str]] = {
    "python": "**/*.py",
    "markdown": "**/*.md",
    "json_config": [
        "config/*.json",
        ".mcp.json",
        ".claude/*.json",
        "tools/*/manifest.json",
    ],
    "shell": "scripts/*.sh",
    "toml": "pyproject.toml",
    "skills": ".claude/skills/*/SKILL.md",
    "commands": ".claude/commands/*.md",
    "agents": ".claude/agents/*.md",
}

EXCLUDE_DIRS = {
    ".venv",
    "__pycache__",
    ".worktrees",
    "node_modules",
    ".git",
    "data",
    "logs",
    "generated_images",
}


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


class AffectedCode(BaseModel):
    """A code file/section affected by a proposed change."""

    path: str  # bridge/telegram_bridge.py
    section: str  # "def handle_message"
    relevance: float  # 0.0 - 1.0
    impact_type: str  # "modify" | "dependency" | "test" | "config" | "docs"
    reason: str  # "Reads session_id which is being restructured"


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def _discover_code_files(repo_root: Path) -> list[Path]:
    """Find all code files matching CODE_PATTERNS, excluding EXCLUDE_DIRS."""
    files: set[Path] = set()
    for patterns in CODE_PATTERNS.values():
        if isinstance(patterns, str):
            patterns = [patterns]
        for pattern in patterns:
            for path in repo_root.glob(pattern):
                if not any(part in EXCLUDE_DIRS for part in path.parts):
                    files.add(path)
    return sorted(files)


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------


def _make_chunk(content: str, file_path: str, section: str = "") -> dict:
    """Create a chunk dict with path, section, content, and content_hash."""
    return {
        "path": file_path,
        "section": section,
        "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
    }


def _chunk_python(content: str, file_path: str) -> list[dict]:
    """AST-based Python chunking.

    - Module-level code (imports, constants) -> preamble chunk (section="")
    - Each top-level function -> one chunk (section="def function_name")
    - Each class -> full body chunk (section="class ClassName") +
      one chunk per method (section="class ClassName.method_name")
    - Decorators are included with their function/class.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return [_make_chunk(content, file_path, "")]

    lines = content.split("\n")
    chunks: list[dict] = []

    # Collect line ranges for top-level nodes
    top_level_ranges: list[tuple[int, int, ast.AST]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Include decorators
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            end = node.end_lineno or node.lineno
            top_level_ranges.append((start, end, node))

    # Sort by start line
    top_level_ranges.sort(key=lambda x: x[0])

    # Extract preamble: everything before the first top-level def/class
    if top_level_ranges:
        first_start = top_level_ranges[0][0]
        preamble_lines = lines[: first_start - 1]
    else:
        preamble_lines = lines

    preamble_text = "\n".join(preamble_lines)
    if preamble_text.strip():
        chunks.append(_make_chunk(preamble_text, file_path, ""))

    # Extract each top-level node
    for start, end, node in top_level_ranges:
        node_text = "\n".join(lines[start - 1 : end])

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(_make_chunk(node_text, file_path, f"def {node.name}"))

        elif isinstance(node, ast.ClassDef):
            # Full class body chunk
            chunks.append(_make_chunk(node_text, file_path, f"class {node.name}"))

            # Per-method chunks
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_start = (
                        item.decorator_list[0].lineno if item.decorator_list else item.lineno
                    )
                    method_end = item.end_lineno or item.lineno
                    method_text = "\n".join(lines[method_start - 1 : method_end])
                    chunks.append(
                        _make_chunk(
                            method_text,
                            file_path,
                            f"class {node.name}.{item.name}",
                        )
                    )

    # If no top-level nodes and no preamble was added, return single chunk
    if not chunks:
        chunks.append(_make_chunk(content, file_path, ""))

    return chunks


def _chunk_config(content: str, file_path: str) -> list[dict]:
    """Chunk config files (JSON, TOML, YAML).

    Small files (<100 lines) -> single chunk.
    Larger JSON -> chunk per top-level key.
    Larger TOML -> split on [section] headers.
    """
    line_count = content.count("\n") + 1

    if line_count < 100:
        return [_make_chunk(content, file_path, "")]

    # Try JSON chunking
    if file_path.endswith(".json"):
        try:
            import json

            data = json.loads(content)
            if isinstance(data, dict):
                chunks = []
                for key, value in data.items():
                    chunk_content = json.dumps({key: value}, indent=2)
                    chunks.append(_make_chunk(chunk_content, file_path, key))
                if chunks:
                    return chunks
        except (json.JSONDecodeError, ValueError):
            pass

    # Try TOML chunking
    if file_path.endswith(".toml"):
        sections: list[tuple[str, list[str]]] = []
        current_section = ""
        current_lines: list[str] = []

        for line in content.split("\n"):
            if re.match(r"^\[[\w.-]+\]", line):
                if current_lines:
                    sections.append((current_section, current_lines))
                current_section = line.strip().strip("[]")
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            sections.append((current_section, current_lines))

        if len(sections) > 1:
            return [
                _make_chunk("\n".join(lines), file_path, section) for section, lines in sections
            ]

    return [_make_chunk(content, file_path, "")]


def _chunk_shell(content: str, file_path: str) -> list[dict]:
    """Chunk shell scripts on function definitions.

    Lines matching `name() {` or `function name` start new chunks.
    Non-function code at the top becomes its own chunk.
    """
    chunks: list[dict] = []
    func_pattern = re.compile(r"^(\w+)\s*\(\)\s*\{|^function\s+(\w+)")

    current_name = ""
    current_lines: list[str] = []

    def _flush():
        text = "\n".join(current_lines)
        if text.strip():
            chunks.append(_make_chunk(text, file_path, current_name))

    for line in content.split("\n"):
        match = func_pattern.match(line)
        if match:
            _flush()
            current_name = match.group(1) or match.group(2)
            current_lines = [line]
        else:
            current_lines.append(line)

    _flush()
    return chunks


def chunk_code_file(content: str, file_path: str) -> list[dict]:
    """Route file to appropriate chunker based on extension.

    - .py -> _chunk_python (AST-based)
    - .md -> chunk_markdown (heading-based)
    - .json, .toml, .yaml -> _chunk_config
    - .sh -> _chunk_shell
    - Other -> single chunk
    """
    if file_path.endswith(".py"):
        return _chunk_python(content, file_path)
    elif file_path.endswith(".md"):
        return chunk_markdown(content, file_path)
    elif file_path.endswith((".json", ".toml", ".yaml", ".yml")):
        return _chunk_config(content, file_path)
    elif file_path.endswith(".sh"):
        return _chunk_shell(content, file_path)
    else:
        return [_make_chunk(content, file_path, "")]


# ---------------------------------------------------------------------------
# Impact type classification
# ---------------------------------------------------------------------------


def _classify_impact_type(path: str) -> str:
    """Classify impact type based on file path.

    Returns one of: "test", "config", "docs", "modify".
    """
    if path.startswith("tests/") or "/tests/" in path:
        return "test"

    if (
        path.startswith("config/")
        or path.endswith((".json", ".toml", ".yaml", ".yml"))
        or path == ".mcp.json"
    ):
        return "config"

    if path.startswith("docs/") or (
        path.endswith(".md")
        and not path.startswith(("bridge/", "tools/", "agent/", "tests/", "scripts/"))
    ):
        return "docs"

    return "modify"


# ---------------------------------------------------------------------------
# Reranking prompt
# ---------------------------------------------------------------------------


def _code_rerank_prompt(change_summary: str, chunk: dict) -> str:
    """Build the code-specific Haiku reranking prompt."""
    return (
        f'Given a proposed change described as: "{change_summary}"\n\n'
        f"Would this file/section be AFFECTED by or COUPLED TO this change? Consider:\n"
        f"- Direct modifications needed\n"
        f"- Behavioral dependencies (uses same abstractions, shares state)\n"
        f"- Configuration coupling (reads same env vars, config keys)\n"
        f"- Test coverage (tests that exercise affected paths)\n"
        f"- Documentation that describes affected behavior\n\n"
        f"File: {chunk['path']} — {chunk['section']}\n"
        f"```\n{chunk['content_preview']}\n```\n\n"
        f"Rate relevance 0-10. Respond with ONLY a JSON object: "
        f'{{"score": <0-10>, "reason": "..."}}'
    )


# ---------------------------------------------------------------------------
# Reranking (backward compat wrapper)
# ---------------------------------------------------------------------------


def _rerank_single_candidate(
    client,
    change_summary: str,
    chunk: dict,
) -> tuple[float, str, dict] | None:
    """Rerank a single code candidate using Claude Haiku.

    Backward-compatible wrapper: builds the code-specific prompt and delegates
    to the core reranker.
    """
    from tools.impact_finder_core import (
        _rerank_single_candidate as _core_rerank,
    )

    prompt = _code_rerank_prompt(change_summary, chunk)
    return _core_rerank(client, prompt, chunk)


# ---------------------------------------------------------------------------
# Embedding provider (module-level for patchability)
# ---------------------------------------------------------------------------


def get_embedding_provider() -> tuple | None:
    """Detect available embedding API and return (embed_function, model_name).

    Priority order: OPENAI_API_KEY, VOYAGE_API_KEY.
    Returns None if no provider is available.

    References module-level _embed_openai / _embed_voyage so that
    tests can patch tools.code_impact_finder._embed_openai.
    """
    import os

    if os.environ.get("OPENAI_API_KEY"):
        return _embed_openai, "text-embedding-3-small"

    if os.environ.get("VOYAGE_API_KEY"):
        try:
            import voyageai  # noqa: F401

            return _embed_voyage, "voyage-3-lite"
        except ImportError:
            logger.warning("VOYAGE_API_KEY set but voyageai package not installed")

    return None


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------


def _build_affected_code(
    results: list[tuple[float, str, dict]],
) -> list[AffectedCode]:
    """Convert reranked results to AffectedCode list.

    Each (score, reason, chunk) tuple becomes one AffectedCode entry.
    impact_type is classified based on file path.
    """
    affected: list[AffectedCode] = []
    for score, reason, chunk in results:
        affected.append(
            AffectedCode(
                path=chunk["path"],
                section=chunk.get("section", ""),
                relevance=score / 10.0,
                impact_type=_classify_impact_type(chunk["path"]),
                reason=reason,
            )
        )

    affected.sort(key=lambda x: x.relevance, reverse=True)
    return affected


def _candidates_to_affected_code(
    candidates: list[tuple[float, dict]],
) -> list[AffectedCode]:
    """Convert embedding-only candidates to AffectedCode (fallback when Haiku unavailable).

    Applies MIN_SIMILARITY_THRESHOLD to filter out irrelevant results.
    """
    affected: list[AffectedCode] = []
    for sim, chunk in candidates:
        if sim < MIN_SIMILARITY_THRESHOLD:
            continue
        affected.append(
            AffectedCode(
                path=chunk["path"],
                section=chunk.get("section", ""),
                relevance=round(sim, 3),
                impact_type=_classify_impact_type(chunk["path"]),
                reason="Matched by embedding similarity (LLM reranking unavailable)",
            )
        )

    affected.sort(key=lambda x: x.relevance, reverse=True)
    return affected


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_code_index(repo_root: Path | None = None) -> dict:
    """Load the code embedding index."""
    return _core_load_index(INDEX_NAME, repo_root)


def index_code(repo_root: Path | None = None) -> dict:
    """Index the codebase for semantic search.

    Uses content hashing to skip re-embedding unchanged chunks.
    Returns the saved index dict.
    """
    return _core_build_index(
        discover_files=_discover_code_files,
        chunk_file=chunk_code_file,
        index_name=INDEX_NAME,
        repo_root=repo_root,
        embed_provider=get_embedding_provider(),
    )


def find_affected_code(
    change_summary: str,
    top_n: int = 20,
    repo_root: Path | None = None,
) -> list[AffectedCode]:
    """Find code affected by a proposed change.

    Stage 1: Embed the change summary, compute cosine similarity against all
             indexed code chunks, take top-N candidates.
    Stage 2: For each candidate, ask Claude Haiku to score relevance (0-10)
             and explain why. Calls are parallelized for speed.

    Returns a list of AffectedCode sorted by relevance (highest first).
    Returns empty list if no embedding API key is available.
    """
    return _core_find_affected(
        change_summary=change_summary,
        discover_files=_discover_code_files,
        chunk_file=chunk_code_file,
        rerank_prompt_builder=_code_rerank_prompt,
        index_name=INDEX_NAME,
        result_builder=_build_affected_code,
        fallback_builder=_candidates_to_affected_code,
        top_n=top_n,
        repo_root=repo_root,
        embed_provider=get_embedding_provider(),
        reranker=_rerank_single_candidate,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli():
    """CLI entry point for code impact finder."""
    import argparse

    parser = argparse.ArgumentParser(description="Find code affected by a proposed change.")
    parser.add_argument(
        "change_summary",
        nargs="?",
        help="Description of the proposed change",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print index status",
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="Just rebuild the index",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Number of top candidates (default: 20)",
    )

    args = parser.parse_args()

    if args.status:
        status = _core_get_index_status(INDEX_NAME)
        print(f"Index: {INDEX_NAME}")
        print(f"  Exists: {status['exists']}")
        print(f"  Chunks: {status['chunk_count']}")
        print(f"  Model: {status['model']}")
        print(f"  Path: {status['index_path']}")
        return

    if args.index_only:
        print("Indexing codebase...")
        result = index_code()
        print(f"Indexed {len(result.get('chunks', []))} chunks.")
        return

    if not args.change_summary:
        parser.error("change_summary is required unless --status or --index-only is used")

    # Index if needed
    status = _core_get_index_status(INDEX_NAME)
    if not status["exists"]:
        print("No index found. Building index first...")
        index_code()

    print(f"Finding affected code for: {args.change_summary}")
    results = find_affected_code(args.change_summary, top_n=args.top_n)

    if not results:
        print("No affected code found.")
        return

    for r in results:
        print(f"\n  {r.path} :: {r.section}")
        print(f"    Relevance: {r.relevance:.2f}  Type: {r.impact_type}")
        print(f"    Reason: {r.reason}")


if __name__ == "__main__":
    _cli()
