---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-03-25
tracking: https://github.com/tomcounsell/ai/issues/518
last_comment_id:
---

# Memory Search Tool

## Problem

The agent memory system (Memory + Finding models in Redis) accumulates knowledge silently but has no direct interface. Memories surface passively as `<thought>` blocks during tool calls, but there's no way to intentionally search, save, inspect, or remove memories.

**Current behavior:**
Memory is write-only from the user's perspective. The only read path is passive `<thought>` injection during agent tool calls. No way to ask "what do I know about X?" or explicitly save a piece of knowledge.

**Desired outcome:**
A `tools/memory_search/` tool exposing unified search (across both Memory and Finding), intentional save, inspection, and deletion. Usable from Telegram agent sessions, Claude Code, and the CLI.

## Prior Art

- **#514 / PR #515**: Subconscious Memory — built the Memory model, extraction, and thought injection. This tool becomes the direct-access complement to that passive system.
- **#500 / PR #517**: Cross-agent Knowledge Relay — built the Finding model, extraction, and query. This tool unifies Finding query with Memory query into one search interface.
- **#393**: Behavioral Episode Memory — closed as superseded. Was blocked on Popoto primitives that shipped.
- **#394**: Popoto Agent Memory integration — closed as superseded. Primitives now integrated.
- **#323**: MuninnDB cognitive memory layer — closed. Pre-dates Popoto adoption.
- **`tools/telegram_history/`**: Reference implementation for the tool pattern. Clean API with `search_history()`, `store_message()`, `get_recent_messages()`, `get_chat_stats()`.

## Data Flow

1. **Search entry**: User calls `search("deploy patterns")` via tool, CLI, or agent
2. **Keyword extraction**: Split query into topic keywords, filter noise words
3. **Bloom pre-check**: ExistenceFilter on both Memory and Finding — skip models with no bloom hits
4. **Memory query**: ContextAssembler with `relevance=0.6, confidence=0.3` weights
5. **Finding query**: Composite scoring with `relevance=0.4, confidence=0.3, access=0.2, topic=0.1`
6. **Score normalization**: Both score sets normalized to 0.0–1.0 range
7. **Merge + rank**: Interleave results by normalized score, deduplicate by content similarity
8. **Output**: Return unified list of `{content, score, source, confidence, access_count}`

## Architectural Impact

- **New dependency**: None — uses existing Popoto models and Redis
- **Interface changes**: New public API in `tools/memory_search/__init__.py`
- **Coupling**: Consumes `models/memory.py` and `models/finding.py` read-only. Reuses scoring logic from `agent/memory_hook.py` (ContextAssembler) and `agent/finding_query.py` (composite scoring)
- **Data ownership**: No change — Memory and Finding models remain source of truth
- **Reversibility**: Fully reversible — pure read/write wrapper, no model changes

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a wrapper tool around existing infrastructure. Both query paths already work in the agent; this packages them as a reusable library.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "import redis; redis.Redis().ping()"` | Memory/Finding storage |
| Popoto installed | `python -c "import popoto; print(popoto.__version__)"` | ORM for models |
| Memory model | `python -c "from models.memory import Memory; print('OK')"` | Memory queries |
| Finding model | `python -c "from models.finding import Finding; print('OK')"` | Finding queries |

## Solution

### Key Elements

- **Unified search**: Single `search()` function querying both Memory and Finding, returning merged ranked results
- **Intentional save**: `save()` function for explicit memory creation during conversations
- **Inspection**: `inspect()` for individual record details and `stats()` for aggregate health
- **Deletion**: `forget()` for targeted removal by ID or content match
- **CLI**: `python -m tools.memory_search` entry point for debugging

### Flow

**Search**: User query → keyword extraction → bloom pre-check → parallel Memory/Finding query → score normalization → merged ranking → results

**Save**: Content + importance → bloom dedup check → Memory.safe_save() → confirmation

**Inspect**: Memory/Finding ID → load record → format all fields (content, importance, confidence, access count, decay score)

### Technical Approach

- Follow `tools/telegram_history/` structure: `__init__.py` (public API), `cli.py` (CLI entry), `manifest.json`, `tests/`
- Reuse `ContextAssembler` for Memory queries (same as `agent/memory_hook.py`)
- Reuse composite scoring from `agent/finding_query.py` for Finding queries
- Normalize scores to 0.0–1.0 before merging (Memory scores from ContextAssembler, Finding scores from composite)
- All public functions return dicts (not model instances) for portability
- All functions follow fail-silent contract: return empty/None on Redis failure, never raise

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `search()` returns `[]` when Redis is down — test with connection refused
- [ ] `save()` returns `None` when Redis is down
- [ ] `inspect()` returns `None` when record doesn't exist
- [ ] `forget()` returns `False` when record doesn't exist

### Empty/Invalid Input Handling
- [ ] `search("")` returns `[]` (empty query)
- [ ] `search(None)` returns `[]`
- [ ] `save("")` returns `None` (empty content)
- [ ] `inspect("nonexistent-id")` returns `None`

### Error State Rendering
- [ ] CLI outputs human-readable error when Redis unavailable
- [ ] CLI outputs "no results" message for empty search

## Test Impact

No existing tests affected — this is a greenfield tool with no prior test coverage. The tool wraps existing model query paths but adds no new behavior to those models.

## Rabbit Holes

- **Custom embedding/vector search** — The models use keyword-based bloom + ContextAssembler, not embeddings. Don't build vector search; work with what Popoto provides.
- **Memory editing/updating** — Popoto models use KeyFields that make in-place updates awkward. Scope to save-new and delete, not update-in-place.
- **Cross-project search** — Memory is partitioned by project_key. Don't build cross-project search; honor partition boundaries.
- **Finding category filtering in search** — Tempting to add `category=` filters, but unified search should be simple. Category info appears in results but isn't a filter parameter.

## Risks

### Risk 1: Score normalization across models
**Impact:** Memory and Finding scores on different scales could produce inconsistent ranking
**Mitigation:** Normalize both to 0.0–1.0 before merging. Memory ContextAssembler already returns 0–1 relevance scores. Finding composite score is already 0–1. Simple min/max normalization if needed.

### Risk 2: Bloom filter false positives
**Impact:** Bloom pre-check says "might exist" but full query returns nothing — wasted query time
**Mitigation:** This is expected bloom behavior (false positives, no false negatives). The pre-check is optimization only; full query is the fallback. Already handled in existing agent code.

## Race Conditions

No race conditions identified — all operations are synchronous reads/writes to Redis. Popoto handles Redis-level atomicity. No shared mutable state between tool invocations.

## No-Gos (Out of Scope)

- **Hook integration** — That's #519 (Claude Code memory integration)
- **Memory extraction / ingestion** — That's #519
- **Model schema changes** — No changes to Memory or Finding models
- **MCP server** — The tool is a Python library; MCP wrapping is a follow-up if needed
- **Outcome detection / confidence updates** — That's existing agent infrastructure, not this tool's job

## Update System

No update system changes required — this is a new tool under `tools/` with no new dependencies or config files. Existing Redis and Popoto dependencies are already deployed.

## Agent Integration

The tool is a Python library importable from any context. For Telegram agent access:

- No MCP server needed initially — the agent can call the tool functions directly from Python
- If MCP exposure is needed later, create `mcp_servers/memory_search/` wrapping the tool's public API
- No bridge changes needed — this tool doesn't interact with Telegram
- Integration test: agent session calls `search()` and gets results

## Documentation

- [ ] Create `docs/features/memory-search-tool.md` describing the tool's API and usage
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/subconscious-memory.md` with link to the search tool as the direct-access complement

## Success Criteria

- [ ] `search(query)` returns unified results from both Memory and Finding models
- [ ] `save(content, importance)` creates memories with bloom registration
- [ ] `inspect(id)` returns full record details for Memory or Finding
- [ ] `stats()` returns aggregate counts, average confidence, source breakdown
- [ ] `forget(id)` deletes individual records
- [ ] All functions return empty/None on Redis failure (never raise)
- [ ] CLI entry point (`python -m tools.memory_search search "query"`) works
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (memory-search)**
  - Name: memory-builder
  - Role: Implement the tool library, CLI, and tests
  - Agent Type: builder
  - Resume: true

- **Validator (memory-search)**
  - Name: memory-validator
  - Role: Verify tool API, fail-silent behavior, and test coverage
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: memory-docs
  - Role: Create feature docs and update index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build tool library
- **Task ID**: build-tool
- **Depends On**: none
- **Validates**: tests/unit/test_memory_search.py (create)
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/memory_search/__init__.py` with public API: `search()`, `save()`, `inspect()`, `stats()`, `forget()`
- Create `tools/memory_search/manifest.json` following telegram_history pattern
- `search()`: bloom pre-check on both models, ContextAssembler for Memory, composite scoring for Finding, normalize and merge
- `save()`: validate content, bloom dedup check, Memory.safe_save() with source="human" and importance=6.0
- `inspect()`: load by ID from Memory or Finding, return all fields as dict
- `stats()`: count records by source/project, average confidence, bloom saturation estimate
- `forget()`: delete by ID from Memory or Finding
- All functions return dicts/lists, never model instances
- All functions wrapped in try/except, return None/[] on failure

### 2. Build CLI
- **Task ID**: build-cli
- **Depends On**: build-tool
- **Validates**: manual CLI test
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/memory_search/cli.py` with argparse subcommands: search, save, inspect, stats, forget
- Human-readable output formatting (table for search results, detail view for inspect)
- `python -m tools.memory_search` entry point via `__main__.py`

### 3. Build tests
- **Task ID**: build-tests
- **Depends On**: build-tool
- **Validates**: tests/unit/test_memory_search.py, tests/integration/test_memory_search_redis.py
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Unit tests for each public function with real Redis
- Integration test: save → search → find → inspect → forget → search again (not found)
- Fail-silent tests: verify functions return empty on connection error
- Edge cases: empty query, empty content, nonexistent ID

### 4. Validate implementation
- **Task ID**: validate-tool
- **Depends On**: build-tests
- **Assigned To**: memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify fail-silent contract (no exceptions propagate)
- Verify unified search returns results from both models
- Run ruff format + lint check

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-tool
- **Assigned To**: memory-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/memory-search-tool.md`
- Add to `docs/features/README.md` index
- Update `docs/features/subconscious-memory.md` with cross-reference

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_memory_search.py tests/integration/test_memory_search_redis.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/memory_search/` | exit code 0 |
| Format clean | `python -m ruff format --check tools/memory_search/` | exit code 0 |
| Tool imports | `python -c "from tools.memory_search import search, save, inspect, stats, forget; print('OK')"` | output contains OK |
| CLI runs | `python -m tools.memory_search --help` | exit code 0 |
| Search works | `python -m tools.memory_search search "test"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Slug resolution for Finding search** — When searching from Claude Code (no active slug), should `search()` query all Finding slugs or only the default project? Leaning toward: query Memory by project_key always, query Finding only when a slug is provided or discoverable from git branch name.

2. **Score normalization strategy** — ContextAssembler returns relevance scores and composite scoring returns 0–1 floats. Are these comparable enough to merge directly, or do we need empirical calibration? Leaning toward: merge directly and tune if ranking feels off in practice.
