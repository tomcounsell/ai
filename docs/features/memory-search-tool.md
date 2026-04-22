# Memory Search Tool

Direct interface for searching, saving, inspecting, and forgetting memories from the Memory model.

## Overview

The memory system previously had no direct interface -- memories accumulated silently and surfaced automatically via `<thought>` injection in the PostToolUse hook. This tool provides intentional access to the Memory model through five operations: search, save, inspect, forget, and status.

## API

### `status(project_key=None, deep=False)`

Return a health summary of the memory system. Fast path (<1s): Redis ping, total count, category breakdown, superseded count, avg confidence, last-write timestamp, EmbeddingField detection. Deep path (behind `deep=True`): orphan index count and per-category confidence averages.

```python
from tools.memory_search import status

result = status(project_key="default")
# {"healthy": True, "redis": {"ok": True}, "total": 80, "by_category": {...},
#  "superseded": 3, "avg_confidence": 0.71, "last_write": "2026-04-15T02:37:04",
#  "embedding_field": "not_configured"}

result = status(deep=True)
# adds: "orphan_index_count": 0, "by_category_confidence": {"correction": {"count": 5, "avg_confidence": 0.80}}
```

Returns `{"healthy": False, "error": "Redis unreachable: ..."}` when Redis is down.

### `search(query, project_key=None, limit=10, ..., assess_quality=False)`

Search memories using BM25 + RRF fusion with bloom pre-check. Optionally run a `RetrievalQuality` probe after retrieval.

```python
from tools.memory_search import search

result = search("deploy patterns", project_key="dm", limit=5)
# {"results": [{"content": "...", "score": 0.8, "confidence": 0.5, ...}], "error": None}

# With quality probe (popoto v1.5.0):
result = search("deploy patterns", assess_quality=True)
# {"results": [...], "error": None, "quality": {"avg_confidence": 0.72, "fok_score": 0.58, ...}}
```

Returns a dict with `results` list and `error` key. Each result contains: content, score, confidence, source, access_count, memory_id.

When `assess_quality=True`, a `"quality"` key is added with `avg_confidence`, `score_spread`, `fok_score`, and `staleness_ratio` from `ContextAssembler.assess()`. The probe makes one additional Redis read and is non-fatal — on error the result is returned without `"quality"`. Default is `False` (backward-compatible).

### `save(content, importance=None, project_key=None, source="human")`

Save a new memory record via `Memory.safe_save()`.

```python
from tools.memory_search import save

result = save("API X requires auth header Y", importance=6.0)
# {"memory_id": "abc123", "content": "API X requires auth header Y"}
```

Returns a dict with memory_id and content, or None if filtered/failed. Default importance is 6.0 (human weight).

### `inspect(memory_id=None, project_key=None, stats=False)`

Inspect a specific memory by ID or get aggregate statistics.

```python
from tools.memory_search import inspect

# Single record
details = inspect(memory_id="abc123")
# {"memory_id": "abc123", "content": "...", "importance": 6.0, ...}

# Aggregate stats
stats = inspect(stats=True, project_key="dm")
# {"project_key": "dm", "total": 42, "by_source": {"human": 30, "agent": 12}, "avg_confidence": 0.55}
```

### `forget(memory_id)`

Delete a memory record by ID.

```python
from tools.memory_search import forget

result = forget("abc123")
# {"deleted": True, "memory_id": "abc123"}
```

## CLI Usage

```bash
# Status (health check)
python -m tools.memory_search status
python -m tools.memory_search status --json
python -m tools.memory_search status --deep
python -m tools.memory_search status --project dm

# Search
python -m tools.memory_search search "deploy patterns"
python -m tools.memory_search search "deploy patterns" --project dm --limit 5 --json

# Save
python -m tools.memory_search save "API X requires auth header Y"
python -m tools.memory_search save "important note" --importance 6.0 --source human

# Inspect
python -m tools.memory_search inspect --id abc123
python -m tools.memory_search inspect --stats --project dm

# Forget (requires --confirm)
python -m tools.memory_search forget --id abc123 --confirm
```

All commands support `--json` for structured output.

## Fail-Silent Contract

Every public function wraps its body in try/except. Redis failures, popoto errors, and import failures return empty results or None. No function raises exceptions to callers. This matches the existing pattern in `Memory.safe_save()`, `agent/memory_hook.py`, and `agent/memory_extraction.py`.

## Architecture

- **No new dependencies**: Uses existing popoto primitives (ContextAssembler, ExistenceFilter) already imported in `agent/memory_hook.py`
- **Coupling**: Imports `models/memory.py` and `popoto.ContextAssembler`. No coupling to bridge or agent hooks
- **Data ownership**: Read-only access to existing Memory records, plus write via `Memory.safe_save()`
- **Reversibility**: Delete `tools/memory_search/` directory. No other code depends on it

## Files

- `tools/memory_search/__init__.py` -- Core module with four public functions
- `tools/memory_search/cli.py` -- CLI entry point with argparse subcommands
- `tools/memory_search/__main__.py` -- Enables `python -m tools.memory_search`
- `tools/memory_search/manifest.json` -- Tool metadata
- `tools/memory_search/tests/test_memory_search.py` -- Unit + integration tests (real Redis)

## Related

- [Subconscious Memory](subconscious-memory.md) -- The underlying memory system this tool interfaces with
- Issue #519 -- Future MCP server integration for Claude Code access
