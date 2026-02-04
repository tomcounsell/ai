"""
Knowledge Search Tool

Local knowledge base search with semantic understanding using embeddings.
"""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Literal

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
DEFAULT_DB_PATH = Path.home() / ".valor" / "knowledge.db"


class KnowledgeSearchError(Exception):
    """Knowledge search operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def _get_db_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get or create database connection."""
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Create tables if needed
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            path TEXT,
            content TEXT,
            embedding TEXT,
            file_type TEXT,
            indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT,
            content TEXT,
            embedding TEXT,
            chunk_index INTEGER,
            FOREIGN KEY (document_id) REFERENCES documents(id)
        )
    """
    )
    conn.commit()

    return conn


def _compute_embedding(text: str, api_key: str) -> list[float] | None:
    """Compute embedding for text using OpenRouter."""
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": EMBEDDING_MODEL,
                "input": text[:8000],  # Limit text length
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("data", [{}])[0].get("embedding")
    except Exception:
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def index_document(
    path: str,
    chunk_size: int = 1000,
    db_path: Path | None = None,
) -> dict:
    """
    Index a document for searching.

    Args:
        path: Path to document file
        chunk_size: Size of chunks for indexing
        db_path: Custom database path

    Returns:
        dict with indexing result
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY environment variable not set"}

    file_path = Path(path)
    if not file_path.exists():
        return {"error": f"File not found: {path}"}

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": f"Failed to read file: {str(e)}"}

    doc_id = hashlib.md5(str(file_path.absolute()).encode()).hexdigest()

    conn = _get_db_connection(db_path)

    # Compute document embedding
    embedding = _compute_embedding(content[:8000], api_key)

    conn.execute(
        """
        INSERT OR REPLACE INTO documents (id, path, content, embedding, file_type)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            str(file_path.absolute()),
            content,
            json.dumps(embedding) if embedding else None,
            file_path.suffix,
        ),
    )

    # Chunk and index
    chunks = []
    for i in range(0, len(content), chunk_size):
        chunk_content = content[i : i + chunk_size]
        chunk_id = f"{doc_id}_{i}"
        chunk_embedding = _compute_embedding(chunk_content, api_key)

        conn.execute(
            """
            INSERT OR REPLACE INTO chunks (id, document_id, content, embedding, chunk_index)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                doc_id,
                chunk_content,
                json.dumps(chunk_embedding) if chunk_embedding else None,
                i // chunk_size,
            ),
        )
        chunks.append(chunk_id)

    conn.commit()
    conn.close()

    return {
        "document_id": doc_id,
        "path": str(file_path.absolute()),
        "chunks_indexed": len(chunks),
        "content_length": len(content),
    }


def search_knowledge(
    query: str,
    search_type: Literal["semantic", "keyword", "hybrid"] = "semantic",
    max_results: int = 10,
    knowledge_bases: list[str] | None = None,
    file_types: list[str] | None = None,
    similarity_threshold: float = 0.7,
    db_path: Path | None = None,
) -> dict:
    """
    Search the knowledge base.

    Args:
        query: Search query
        search_type: Type of search (semantic, keyword, hybrid)
        max_results: Maximum results (1-100)
        knowledge_bases: Paths to search (default: all)
        file_types: Filter by file type (e.g., [".md", ".txt"])
        similarity_threshold: Minimum similarity score (0-1)
        db_path: Custom database path

    Returns:
        dict with search results
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key and search_type in ("semantic", "hybrid"):
        return {"error": "OPENROUTER_API_KEY required for semantic search"}

    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    max_results = max(1, min(100, max_results))

    conn = _get_db_connection(db_path)

    results = []

    if search_type == "keyword":
        # Simple keyword search
        cursor = conn.execute(
            """
            SELECT id, path, content, file_type
            FROM documents
            WHERE content LIKE ?
            LIMIT ?
            """,
            (f"%{query}%", max_results),
        )

        for row in cursor:
            results.append(
                {
                    "document_id": row["id"],
                    "path": row["path"],
                    "snippet": _extract_snippet(row["content"], query),
                    "file_type": row["file_type"],
                    "score": 1.0,
                }
            )

    else:  # semantic or hybrid
        query_embedding = _compute_embedding(query, api_key)
        if not query_embedding:
            return {"error": "Failed to compute query embedding"}

        # Get all chunks with embeddings
        cursor = conn.execute(
            """
            SELECT c.id, c.document_id, c.content, c.embedding, d.path, d.file_type
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE c.embedding IS NOT NULL
            """
        )

        scored_results = []
        for row in cursor:
            chunk_embedding = json.loads(row["embedding"])
            similarity = _cosine_similarity(query_embedding, chunk_embedding)

            if similarity >= similarity_threshold:
                scored_results.append(
                    {
                        "document_id": row["document_id"],
                        "path": row["path"],
                        "snippet": row["content"][:500],
                        "file_type": row["file_type"],
                        "score": similarity,
                    }
                )

        # Sort by score and deduplicate by document
        scored_results.sort(key=lambda x: x["score"], reverse=True)
        seen_docs = set()
        for r in scored_results:
            if r["document_id"] not in seen_docs and len(results) < max_results:
                results.append(r)
                seen_docs.add(r["document_id"])

    conn.close()

    return {
        "query": query,
        "search_type": search_type,
        "results": results,
        "total_matches": len(results),
    }


def _extract_snippet(content: str, query: str, context_chars: int = 200) -> str:
    """Extract a snippet around the query match."""
    lower_content = content.lower()
    lower_query = query.lower()

    pos = lower_content.find(lower_query)
    if pos == -1:
        return content[: context_chars * 2]

    start = max(0, pos - context_chars)
    end = min(len(content), pos + len(query) + context_chars)

    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet


def list_indexed_documents(db_path: Path | None = None) -> dict:
    """List all indexed documents."""
    conn = _get_db_connection(db_path)

    cursor = conn.execute(
        """
        SELECT id, path, file_type, indexed_at
        FROM documents
        ORDER BY indexed_at DESC
        """
    )

    documents = [dict(row) for row in cursor]
    conn.close()

    return {
        "documents": documents,
        "total": len(documents),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.knowledge_search 'search query'")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f"Searching for: {query}")

    result = search_knowledge(query)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"\nFound {result['total_matches']} results:")
        for r in result["results"]:
            print(f"\n  {r['path']}")
            print(f"  Score: {r['score']:.2f}")
            print(f"  {r['snippet'][:100]}...")
