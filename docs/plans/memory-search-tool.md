---
status: Planned
type: feature
appetite: Small
owner: Valor
created: 2026-03-25
tracking: https://github.com/tomcounsell/ai/issues/518
---

# Memory Search Tool

## Problem

The memory system has no direct interface. Memories accumulate silently and surface automatically via `<thought>` injection, but there is no way to intentionally search, inspect, save, or forget memories. This is the equivalent of having Telegram message history but no search tool.

**Current behavior:** Memory is write-only from the user's perspective. The only read path is passive `<thought>` injection during tool calls (via `agent/memory_hook.py`), which the user never sees directly.

**Desired outcome:** A tool at `tools/memory_search/` that exposes search, save, inspect, and forget operations on the Memory model. Usable from both agent sessions (via direct import) and Claude Code sessions (via CLI).

## Prior Art

- **`tools/telegram_history/`**: Reference pattern for tool structure (`__init__.py`, `cli.py`, `manifest.json`, `tests/`). This tool follows the same conventions.
- **`agent/memory_hook.py`**: Existing read path via ExistenceFilter bloom check and ContextAssembler. The search function reuses the same ContextAssembler pattern.
- **`agent/memory_extraction.py`**: Existing write path via `Memory.safe_save()`. The save function delegates to the same method.
- **`models/memory.py`**: The Memory model with DecayingSortedField, ConfidenceField, ExistenceFilter, WriteFilterMixin, AccessTrackerMixin.

## Data Flow

1. **search(query)**: Extract keywords from query -> bloom pre-check via ExistenceFilter -> ContextAssembler query with relevance/confidence weights -> return ranked results
2. **save(content)**: Bloom dedup check -> `Memory.safe_save()` with specified importance and source -> return saved record or None
3. **inspect(memory_id)**: Direct lookup by memory_id, or aggregate stats across all memories for a project_key
4. **forget(memory_id)**: Lookup by memory_id -> `memory.delete()` -> return confirmation

## Architectural Impact

- **New dependencies**: None. Uses existing popoto primitives (ContextAssembler, ExistenceFilter) already imported in `agent/memory_hook.py`.
- **Interface changes**: New `tools/memory_search/` module with 4 public functions. New CLI entry point `python -m tools.memory_search`.
- **Coupling**: Imports `models/memory.py` and `popoto.ContextAssembler`. No coupling to bridge or agent hooks.
- **Data ownership**: Read-only access to existing Memory records, plus write via `Memory.safe_save()` (same path as existing ingestion).
- **Reversibility**: Trivial. Delete the `tools/memory_search/` directory. No other code depends on it.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites. The Memory model is live on main (PR #515 merged). ContextAssembler and ExistenceFilter are available via popoto.

## Solution

### Key Elements

- **`tools/memory_search/__init__.py`**: Core module with `search()`, `save()`, `inspect()`, `forget()` functions
- **`tools/memory_search/cli.py`**: CLI entry point with argparse subcommands
- **`tools/memory_search/manifest.json`**: Tool metadata following the existing pattern
- **`tools/memory_search/tests/`**: Unit and integration tests

### Flow

**search("deploy patterns")** -> extract keywords -> bloom check via `Memory.bloom` -> ContextAssembler query with `score_weights={"relevance": 0.6, "confidence": 0.3}` -> return list of `{content, score, confidence, source, access_count, memory_id}`

**save("API X requires auth header Y")** -> bloom dedup check -> `Memory.safe_save(content=..., importance=6.0, source="human", ...)` -> return `{memory_id, content}` or `None`

**inspect(memory_id="abc123")** -> `Memory.query.filter(memory_id=...)` -> return full record fields. With `stats=True` and no ID: aggregate counts by source, average confidence, total count.

**forget(memory_id="abc123")** -> `Memory.query.filter(memory_id=...)` -> `memory.delete()` -> return `{deleted: True, memory_id}`

### Technical Approach

#### `search(query, project_key=None, limit=10)`

```python
def search(query, project_key=None, limit=10):
    # 1. Resolve project_key from env if not provided
    # 2. Bloom pre-check: if ExistenceFilter says "definitely not present", skip assembly
    # 3. ContextAssembler.assemble(query_cues={"topic": query}, agent_id=project_key)
    # 4. Format results: content, score, confidence, source, access_count, memory_id
    # 5. Return list of dicts, capped at limit
```

Uses the same ContextAssembler pattern as `agent/memory_hook.py:check_and_inject()` but with the raw query string instead of extracted keywords. The assembler handles relevance scoring internally via DecayingSortedField weights.

#### `save(content, importance=None, project_key=None, source="human")`

```python
def save(content, importance=None, project_key=None, source="human"):
    # 1. Default importance to 6.0 (InteractionWeight.HUMAN)
    # 2. Resolve project_key from env if not provided
    # 3. Bloom dedup check: if content fingerprint already exists, log and skip
    # 4. Memory.safe_save(content=content, importance=importance, source=source, ...)
    # 5. Return {memory_id, content} or None if filtered/failed
```

Delegates to `Memory.safe_save()` which handles WriteFilterMixin thresholds and error handling. The bloom dedup check prevents exact-duplicate memories.

#### `inspect(memory_id=None, project_key=None, stats=False)`

```python
def inspect(memory_id=None, project_key=None, stats=False):
    # If memory_id: direct lookup, return full record details
    # If stats=True: aggregate across project_key
    #   - total count, source breakdown, average confidence
    # If neither: return error guidance
```

#### `forget(memory_id)`

```python
def forget(memory_id):
    # 1. Lookup by memory_id
    # 2. Call memory.delete()
    # 3. Return {deleted: True, memory_id} or {error: "not found"}
```

#### CLI (`cli.py`)

Follows the `tools/telegram_history/cli.py` pattern with argparse subcommands:

```
python -m tools.memory_search search "deploy patterns"
python -m tools.memory_search search "deploy patterns" --project dm --limit 5
python -m tools.memory_search save "API X requires auth header Y"
python -m tools.memory_search save "important note" --importance 6.0 --source human
python -m tools.memory_search inspect --id abc123
python -m tools.memory_search inspect --stats --project dm
python -m tools.memory_search forget --id abc123
python -m tools.memory_search forget --id abc123 --confirm
```

Both `--json` and human-readable output modes. The `forget` command requires `--confirm` flag to prevent accidental deletion.

#### Fail-Silent Contract

Every public function wraps its body in try/except. Redis failures, popoto errors, and import failures return empty results or None. No function raises exceptions to callers. This matches the existing pattern in `Memory.safe_save()`, `agent/memory_hook.py`, and `agent/memory_extraction.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] All four functions (`search`, `save`, `inspect`, `forget`) return graceful results when Redis is unavailable
- [ ] `search()` returns `{"results": [], "error": None}` on Redis failure (not an exception)
- [ ] `save()` returns `None` on Redis failure
- [ ] `inspect()` returns `{"error": "..."}` on lookup failure
- [ ] `forget()` returns `{"error": "..."}` when memory_id not found

### Empty/Invalid Input Handling
- [ ] `search("")` returns empty results (not crash)
- [ ] `save("")` returns None (empty content filtered by WriteFilterMixin)
- [ ] `inspect()` with no arguments returns usage guidance
- [ ] `forget("")` returns error dict

### Error State Rendering
- [ ] CLI prints human-readable errors to stderr, returns nonzero exit code
- [ ] JSON mode outputs structured error in all failure cases

## Test Impact

No existing tests affected. This is a greenfield feature creating a new tool module. The existing memory tests (`tests/unit/test_memory_model.py`, `tests/unit/test_memory_hook.py`, `tests/unit/test_memory_extraction.py`, `tests/unit/test_memory_ingestion.py`) test the underlying Memory model and hook system, which this tool consumes but does not modify.

## Rabbit Holes

- Exposing memory through an MCP server for Claude Code -- that is issue #519, not this issue
- Building a web UI for memory browsing -- separate concern
- Adding full-text search beyond ContextAssembler -- popoto handles this, do not reinvent
- Implementing memory editing (update content) -- out of scope, memories are append-only observations
- Adding `query` parameter to `forget()` for bulk deletion by content match -- too dangerous for v1, stick to ID-based deletion only

## Risks

### Risk 1: ContextAssembler API mismatch
**Impact:** search() fails if popoto ContextAssembler signature differs from what memory_hook uses
**Mitigation:** Copy the exact invocation pattern from `agent/memory_hook.py:check_and_inject()` (lines 192-201). Integration test validates the full path.

### Risk 2: Bloom filter false positives on search
**Impact:** search() may attempt expensive ContextAssembler queries when bloom says "maybe" but no real matches exist
**Mitigation:** This is expected bloom behavior (false positives, no false negatives). ContextAssembler returns empty results quickly. No correctness issue, just a minor perf concern.

## Race Conditions

No significant race conditions. All operations are single-record reads/writes to Redis. The Memory model uses Popoto's atomic key operations. Concurrent saves of the same content are handled by bloom dedup (at-most-once, not exactly-once -- acceptable for memory records).

## No-Gos (Out of Scope)

- MCP server integration (that is issue #519)
- Bulk operations (bulk delete, bulk export)
- Memory editing/mutation (memories are immutable observations)
- Query-based forget (too dangerous without confirmation workflow)
- Cross-project search (always scoped to one project_key)

## Update System

No update system changes required. This is a new Python module under `tools/` with no new dependencies, no new config files, and no new system services. The existing `pip install -e .` and `scripts/remote-update.sh` handle it automatically.

## Agent Integration

No agent integration required for this issue. The tool is a standalone Python library + CLI. Issue #519 (Claude Code memory integration) will wire this tool into the agent via an MCP server or direct import in Claude Code hooks. For now, the tool is usable via:

1. Direct Python import: `from tools.memory_search import search, save, inspect, forget`
2. CLI: `python -m tools.memory_search search "query"`
3. Agent sessions can import it directly (no MCP needed for Python-native tools)

## Documentation

- [ ] Create `docs/features/memory-search-tool.md` describing the tool's capabilities, API, and CLI usage
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/tools-reference.md` with memory_search tool documentation
- [ ] Add CLI examples to CLAUDE.md quick commands table

## Success Criteria

- [ ] `search()` returns ranked results from Memory model via ContextAssembler
- [ ] `save()` creates memories with correct importance and bloom registration
- [ ] `inspect()` shows individual records and aggregate stats
- [ ] `forget()` removes memories by ID
- [ ] All operations fail silently when Redis is unavailable (return empty/None, never raise)
- [ ] CLI entry point works for manual debugging
- [ ] Unit tests with real Redis (no mocks)
- [ ] Integration test: save -> search -> find the saved memory

## Step by Step Tasks

### 1. Create core module with four operations
- **Task ID**: build-core
- **Depends On**: none
- **Validates**: tests/unit/test_memory_search.py (create)
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/memory_search/__init__.py` with `search()`, `save()`, `inspect()`, `forget()`
- Create `tools/memory_search/manifest.json` following telegram_history pattern
- Implement `search()` using ContextAssembler (copy pattern from memory_hook.py lines 192-201)
- Implement `save()` delegating to `Memory.safe_save()` with bloom dedup check
- Implement `inspect()` with single-record lookup and stats aggregation
- Implement `forget()` with ID-based deletion
- Wrap every function body in try/except for fail-silent contract

### 2. Create CLI entry point
- **Task ID**: build-cli
- **Depends On**: build-core
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/memory_search/cli.py` with argparse subcommands: search, save, inspect, forget
- Support both `--json` and human-readable output modes
- Add `--confirm` flag requirement for forget command
- Add `__main__.py` or `if __name__` block for `python -m tools.memory_search` invocation

### 3. Create tests
- **Task ID**: build-tests
- **Depends On**: build-core
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/memory_search/tests/__init__.py`
- Create `tools/memory_search/tests/test_memory_search.py`
- Unit tests: each function with valid input, empty input, error cases
- Integration test: save -> search -> find (real Redis, no mocks)
- Fail-silent test: verify no exceptions propagate on Redis failure simulation
- CLI test: verify `python -m tools.memory_search search --help` exits 0

### 4. Validate and lint
- **Task ID**: validate
- **Depends On**: build-tests
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m ruff check tools/memory_search/`
- Run `python -m ruff format --check tools/memory_search/`
- Run `pytest tools/memory_search/tests/ -v`
- Run `pytest tests/unit/test_memory_model.py -v` to confirm no regression

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tools/memory_search/tests/ -v` | exit code 0 |
| Lint clean | `python -m ruff check tools/memory_search/` | exit code 0 |
| Format clean | `python -m ruff format --check tools/memory_search/` | exit code 0 |
| CLI works | `python -m tools.memory_search search --help` | exit code 0 |
| Import works | `python -c "from tools.memory_search import search, save, inspect, forget; print('OK')"` | prints OK |
| Memory model unaffected | `pytest tests/unit/test_memory_model.py -v` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions. The scope is narrow and well-defined from the issue spec. The Memory model, ContextAssembler, and ExistenceFilter are all proven and in production.
