# Codebase Context & RAG Strategy

## Overview

This document outlines the recommended approach for organizing and retrieving information across multiple project workspaces that the Valor AI system operates on.

## Requirements

- Must run locally on MacBook with 16GB RAM
- Should use no more than 10GB RAM (leaving headroom for other processes)
- Quality must be good enough for code understanding
- Per-workspace indexing for multiple projects

## Evaluated Options

### Option 1: Apple CLaRa (Not Recommended Yet)

[Apple's CLaRa](https://github.com/apple/ml-clara) is a state-of-the-art RAG system with impressive document compression (32x-64x).

**Pros:**
- State-of-the-art compression and retrieval
- End-to-end trained (retrieval + generation optimized together)
- From Apple, likely to have good Apple Silicon support

**Cons:**
- Requires full Mistral-7B base model (~14GB FP16)
- No official quantized or MLX version yet (announced "coming soon" Dec 2025)
- Trained on QA datasets, not code - quality for code is untested
- Total memory: ~17-20GB FP16, ~10-14GB quantized

**Recommendation:** Wait for MLX version before adopting.

### Option 2: Local Embedding + Vector DB (Recommended)

Simpler architecture using local embeddings without a second LLM.

```
Codebase Files
    |
    v
Local Embeddings (nomic-embed-text via Ollama, ~300MB)
    |
    v
Vector DB (ChromaDB or SQLite-vec)
    |
    v
Relevant chunks passed to Claude Code context
```

**Pros:**
- Uses ~2GB RAM total
- Proven for code search
- Integrates cleanly with subagent architecture
- No second LLM needed (Claude Code is the brain)
- Fast indexing and retrieval

**Cons:**
- No learned compression (just chunking)
- Retrieval not trained end-to-end with generation

**Memory Budget:**
| Component | RAM Usage |
|-----------|-----------|
| Embedding model (nomic-embed-text) | ~300MB |
| Vector DB (per workspace) | ~100-500MB |
| Index overhead | ~200MB |
| **Total** | **~1-2GB** |

### Option 3: Hosted RAG Service

Use external services for RAG capabilities.

**Options:**
- Anthropic Claude (already excellent code understanding)
- Perplexity API (already integrated for web search)
- Sourcegraph Cody (code-specific RAG)

**Pros:**
- No local resource usage
- Professionally maintained
- Code-optimized (Sourcegraph)

**Cons:**
- Requires internet
- API costs
- Data leaves local machine

## Recommended Architecture

### Phase 1: Local Embedding Approach (Implement Now)

```python
# Conceptual implementation
class WorkspaceIndexer:
    def __init__(self, workspace_path: str):
        self.workspace = workspace_path
        self.embedder = OllamaEmbeddings(model="nomic-embed-text")
        self.vector_store = ChromaDB(path=f"data/indexes/{workspace_name}")

    def index_codebase(self):
        """Index all relevant files in workspace"""
        files = self.get_indexable_files()
        for file in files:
            chunks = self.chunk_file(file)
            embeddings = self.embedder.embed(chunks)
            self.vector_store.add(chunks, embeddings, metadata={"file": file})

    def query(self, question: str, top_k: int = 5) -> list[str]:
        """Retrieve relevant chunks for a question"""
        query_embedding = self.embedder.embed(question)
        results = self.vector_store.similarity_search(query_embedding, k=top_k)
        return results
```

### File Types to Index

**High Priority:**
- `*.py` - Python source files
- `*.md` - Documentation
- `CLAUDE.md` - Project instructions
- `README.md` - Project overview
- `*.json` - Configuration files

**Medium Priority:**
- `*.js`, `*.ts` - JavaScript/TypeScript
- `*.yaml`, `*.yml` - Config files
- `*.sql` - Database schemas

**Exclude:**
- `node_modules/`, `.venv/`, `__pycache__/`
- Binary files, images
- `.git/` directory
- Large generated files

### Chunking Strategy

```python
def chunk_file(file_path: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Chunk file with overlap for context preservation"""
    content = Path(file_path).read_text()

    # For code files, try to chunk on function/class boundaries
    if file_path.endswith('.py'):
        return chunk_python_smart(content)

    # For other files, use sliding window
    return sliding_window_chunk(content, chunk_size, overlap)
```

### Integration with Subagents

Each subagent can query the workspace index:

```
User Query: "How does authentication work?"
    |
    v
Main Agent (Valor)
    |
    v
WorkspaceIndexer.query("authentication")
    |
    v
Returns: [auth.py:15-45, middleware.py:20-35, README.md:auth-section]
    |
    v
Context injected into Claude Code session
```

### Phase 2: CLaRa Integration (Future)

When Apple releases the MLX version:

1. Evaluate memory usage on M-series Mac
2. Test quality on code retrieval tasks
3. Compare against embedding approach
4. Adopt if quality > embedding approach with acceptable memory

## Per-Workspace Configuration

```json
// config/workspace_config.json
{
  "workspaces": {
    "ai": {
      "working_directory": "/Users/valorengels/src/ai",
      "index_config": {
        "enabled": true,
        "include_patterns": ["**/*.py", "**/*.md", "**/*.json"],
        "exclude_patterns": [".venv/**", "__pycache__/**"],
        "chunk_size": 1000,
        "reindex_on_change": true
      }
    }
  }
}
```

## Implementation Checklist

- [ ] Install Ollama with nomic-embed-text model
- [ ] Set up ChromaDB or SQLite-vec for vector storage
- [ ] Implement WorkspaceIndexer class
- [ ] Add indexing to workspace initialization
- [ ] Integrate query results into context building
- [ ] Add re-indexing on file changes (optional)
- [ ] Monitor CLaRa MLX release for future evaluation

## References

- [Apple CLaRa GitHub](https://github.com/apple/ml-clara)
- [CLaRa Paper](https://arxiv.org/abs/2511.18659)
- [Ollama Embedding Models](https://ollama.ai/library)
- [ChromaDB](https://www.trychroma.com/)
- [nomic-embed-text](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)
