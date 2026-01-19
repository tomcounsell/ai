# Knowledge Search Tool

Local knowledge base search with semantic understanding using embeddings.

## Overview

This tool provides semantic and keyword search capabilities for local documents:
- Semantic search using embeddings
- Keyword-based search
- Document indexing and chunking
- SQLite-backed storage

## Installation

For semantic search, configure your API key:

```bash
export OPENROUTER_API_KEY=your_api_key
```

Keyword search works without an API key.

## Quick Start

```python
from tools.knowledge_search import search_knowledge, index_document

# Index a document
index_document("docs/guide.md")

# Search
result = search_knowledge("How to configure the system?")
for r in result["results"]:
    print(f"{r['path']}: {r['snippet']}")
```

## API Reference

### search_knowledge()

```python
def search_knowledge(
    query: str,
    search_type: Literal["semantic", "keyword", "hybrid"] = "semantic",
    max_results: int = 10,
    knowledge_bases: list[str] | None = None,
    file_types: list[str] | None = None,
    similarity_threshold: float = 0.7,
    db_path: Path | None = None,
) -> dict
```

**Parameters:**
- `query`: Search query
- `search_type`: Search method (semantic uses AI embeddings)
- `max_results`: Maximum results (1-100)
- `knowledge_bases`: Paths to search
- `file_types`: Filter by extension (e.g., [".md", ".txt"])
- `similarity_threshold`: Minimum similarity for semantic search
- `db_path`: Custom database location

**Returns:**
```python
{
    "query": str,
    "search_type": str,
    "results": [
        {
            "document_id": str,
            "path": str,
            "snippet": str,
            "file_type": str,
            "score": float
        }
    ],
    "total_matches": int
}
```

### index_document()

```python
def index_document(
    path: str,
    chunk_size: int = 1000,
    db_path: Path | None = None,
) -> dict
```

Index a document for searching.

**Returns:**
```python
{
    "document_id": str,
    "path": str,
    "chunks_indexed": int,
    "content_length": int
}
```

### list_indexed_documents()

```python
def list_indexed_documents(db_path: Path | None = None) -> dict
```

List all indexed documents.

## Workflows

### Index and Search
```python
# Index documents
index_document("README.md")
index_document("docs/guide.md")

# Search
results = search_knowledge("installation steps")
```

### Keyword Search (No API Key)
```python
results = search_knowledge(
    "error handling",
    search_type="keyword"
)
```

### Filtered Search
```python
results = search_knowledge(
    "configuration",
    file_types=[".md", ".txt"],
    max_results=5
)
```

## Storage

Documents are indexed in a SQLite database at `~/.valor/knowledge.db` by default.

## Error Handling

```python
result = search_knowledge(query)

if "error" in result:
    print(f"Search failed: {result['error']}")
else:
    for r in result["results"]:
        print(r["snippet"])
```

## Troubleshooting

### API Key Not Set
Semantic search requires OPENROUTER_API_KEY. Use keyword search as a fallback.

### No Results Found
- Check if documents are indexed
- Lower the similarity_threshold
- Try keyword search instead
