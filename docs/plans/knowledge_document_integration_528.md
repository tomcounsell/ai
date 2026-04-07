---
status: Done
type: feature
appetite: Medium
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/528
last_comment_id:
---

# Knowledge Document Integration

## Problem

The agent's memory system only contains observations extracted from conversations. It has no awareness of the broader knowledge bases in `~/work-vault/` — business context, project notes, decisions, and assets that exist per project.

**Current behavior:**
When working on PsyOptimal, the agent has no way to recall that there's a scoring rubric doc, a competitor analysis, or client meeting notes sitting in the work-vault. It can only recall what it observed in prior conversations.

**Desired outcome:**
The agent subconsciously recalls relevant knowledge documents during work, scoped by project (NDA isolation). Company-wide docs are always accessible. The agent sees a thought like "There's a knowledge doc about PsyOptimal's assessment framework at ~/work-vault/PsyOptimal/assessment-framework.md" and can read the file on demand.

## Prior Art

### Existing `tools/knowledge_search/`

The codebase already includes `tools/knowledge_search/__init__.py` -- a SQLite-based knowledge search tool with:
- Document indexing with content + embeddings stored in SQLite (`~/.valor/knowledge.db`)
- Fixed-size chunking (default 1000 chars)
- Semantic search (cosine similarity), keyword search (SQL LIKE), and hybrid search
- OpenRouter REST API for embeddings (`openai/text-embedding-3-small`)
- Exposed as MCP tool via `tools/knowledge_search/manifest.json`

**Reconciliation strategy: Replace with KnowledgeDocument system.** The existing tool is a standalone SQLite implementation that duplicates what Popoto provides natively (Redis-backed models with ContentField + EmbeddingField). The new KnowledgeDocument system supersedes it by:
- Using Popoto models (consistent with Memory and other models in the codebase)
- Automatic embedding via EmbeddingField (no manual REST calls)
- Project-scoped isolation (the SQLite tool has no project scoping)
- Integration with the subconscious memory system (companion memories + bloom filter)

The existing `tools/knowledge_search/` will be preserved during v1 build (no deletion) but the new system is the intended replacement. A follow-up task will migrate any indexed data and remove the SQLite tool.

### Popoto Embedding API

Popoto provides a native embedding framework:
- `popoto.fields.embedding_field.EmbeddingField` -- auto-generates embeddings on save, stores as .npy files
- `popoto.embeddings.AbstractEmbeddingProvider` -- provider interface
- `popoto.embeddings.openai.OpenAIProvider` -- uses `openai` package (installed)
- `popoto.embeddings.voyage.VoyageProvider` -- uses `voyageai` package (NOT installed)
- `popoto.configure(embedding_provider=...)` -- sets global default provider

## Data Flow

1. **Indexing entry point**: File change in `~/work-vault/` detected by `watchdog` filesystem watcher (thread inside bridge process)
2. **Debounce**: Events collected for ~2 seconds, then unique file paths batch-processed
3. **KnowledgeDocument upsert**: For each changed file — read content, determine project scope from path, create/update KnowledgeDocument record (ContentField stores content on filesystem, EmbeddingField generates embedding via OpenAI provider)
4. **Companion Memory creation**: Summarize the document (Haiku call), create/refresh Memory records with `source="knowledge"` and a `reference` JSON pointer. One Memory per major section for large docs, one Memory for small docs.
5. **Bloom population**: Companion memories land in the bloom filter like any other memory
6. **Recall (existing flow)**: Tool call window triggers → bloom check → ContextAssembler query → knowledge-sourced thought injected: content summary + reference pointer (tool call with params to read the file)
7. **Agent action**: Agent reads the full file on demand using the reference pointer

## Architectural Impact

- **New model**: `KnowledgeDocument` in `models/` — new Popoto model, no changes to existing Memory model fields (only adds `reference` field)
- **New dependency**: `watchdog` Python package for filesystem monitoring (embeddings use `popoto.embeddings.openai.OpenAIProvider` -- `openai` package already installed)
- **Memory model extension**: Adding a `reference` StringField to Memory — generic JSON pointer, backwards-compatible (defaults to empty string)
- **Bridge extension**: Watchdog thread starts with bridge process, stops on shutdown
- **Coupling**: Low — KnowledgeDocument is a standalone model. The only touchpoint with existing code is the new `reference` field on Memory and the watchdog thread in the bridge.
- **Reversibility**: High — remove the model, remove the watchdog thread, remove the reference field. No existing behavior changes.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on reference pointer design)
- Review rounds: 1 (code review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENAI_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENAI_API_KEY')"` | OpenAI embedding generation via popoto OpenAIProvider |
| `openai` | `python -c "import openai"` | OpenAI SDK (already installed) |
| `numpy` | `python -c "import numpy"` | Required by EmbeddingField for vector operations |
| `watchdog` | `python -c "import watchdog"` | Filesystem event monitoring |

Run all checks: `python scripts/check_prerequisites.py docs/plans/knowledge_document_integration_528.md`

## Solution

### Key Elements

- **KnowledgeDocument model**: Popoto model backed by real files on disk. ContentField for content, EmbeddingField (OpenAI `text-embedding-3-small` via `popoto.embeddings.openai.OpenAIProvider`) for semantic search. Keyed by file path, scoped by project_key.
- **Generic reference pointer on Memory**: New `reference` StringField — JSON blob pointing to a tool call, URL, entity, or any actionable next step. Enables knowledge-sourced memories to tell the agent exactly how to retrieve the full content.
- **Filesystem watcher**: `watchdog` thread inside the bridge process monitors `~/work-vault/` for file changes. On startup, does a full mtime scan to catch changes missed while bridge was down.
- **Indexer**: Processes file changes — creates/updates KnowledgeDocument, generates companion Memory records with summaries and reference pointers. Handles deletes (orphan cleanup).
- **Scope resolver**: Maps file paths to project_key + scope (client vs company-wide) using projects.json `knowledge_base` field as the single source of truth. No CLAUDE.md parsing.

### Flow

**File changed in work-vault** → watchdog detects → debounce (2s) → indexer processes →
  **KnowledgeDocument upserted** (content + embedding) →
  **Companion Memory created** (summary + reference pointer + bloom) →
  **Later: agent works on project** → bloom fires → thought injected with summary + pointer →
  **Agent reads file** if needed

### Technical Approach

- KnowledgeDocument uses `ContentField(store="filesystem")` — content stays on disk, Redis holds reference hash only
- EmbeddingField with `OpenAIProvider(model="text-embedding-3-small", dim=1536)` from `popoto.embeddings.openai` -- consistent with existing knowledge_search tool's model choice, and `openai` package is already installed. Configure via `popoto.configure(embedding_provider=OpenAIProvider())` at app startup.
- Companion memories use `source="knowledge"` to distinguish from conversational observations
- Reference field is a JSON string: `{"tool": "read_file", "params": {"file_path": "/path/to/doc.md"}}` or other shapes for non-file references
- Watchdog uses `Observer` with `FileSystemEventHandler` subclass, debounced via threading.Timer
- Scope resolution: projects.json `knowledge_base` mappings are the **single source of truth** (no CLAUDE.md parsing). If path is under a project's knowledge_base directory → that project_key with scope "client". If under ~/work-vault/ root but not under any project subfolder → project_key "company" with scope "company-wide". Unknown paths → skip.
- On bridge startup: full scan compares file mtimes against KnowledgeDocument `last_modified` timestamps
- Companion Memory summarization via Haiku (cheap, fast) — one call per document, output is the memory content
- Large documents (>2000 words): split by top-level headings, one companion Memory per section
- Document deletion: watchdog detects → delete KnowledgeDocument → delete companion Memories by reference match

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Watchdog handler must catch all exceptions — a crash in the watcher thread must not take down the bridge
- [ ] OpenAI embedding API failures (rate limit, network) must log warning and skip embedding, not crash indexer
- [ ] Haiku summarization failures must fall back to first-N-chars truncation for companion memory content

### Empty/Invalid Input Handling
- [ ] Empty files produce no KnowledgeDocument or companion memories
- [ ] Binary files (images, PDFs) are skipped by the indexer (markdown/text only for v1)
- [ ] Files with only frontmatter and no body content are handled gracefully

### Error State Rendering
- [ ] If a companion Memory has a stale reference (file moved/deleted), the thought should still be useful — agent sees the summary even if the file path is wrong
- [ ] Watchdog thread health is visible in bridge status/logs

## Test Impact

No existing tests affected — this is a greenfield feature. The new `reference` field on Memory is additive with a default of empty string, so existing Memory tests continue to pass without modification.

## Rabbit Holes

- **Embedding all file types**: v1 is markdown/text only. PDFs, images, spreadsheets are future work — don't try to parse them now.
- **Semantic search at recall time**: The existing bloom → ContextAssembler flow is sufficient. Don't add a parallel EmbeddingField similarity search path to the recall flow yet — companion memories in the bloom filter handle discovery. Embedding search on KnowledgeDocument is for future direct-query use cases.
- **Real-time Obsidian plugin**: Using kernel-level fsevents via watchdog is sufficient. No need for an Obsidian plugin.
- **Write-back to work-vault**: Agent is read-only for work-vault. Don't add write capabilities.
- **Chunking strategies**: Simple heading-based splits for large docs. Don't build a sophisticated chunking pipeline.

## Risks

### Risk 1: Embedding costs
**Impact:** OpenAI embedding API costs scale with number and size of work-vault documents
**Mitigation:** Only re-embed on file change (not on every startup). Track `content_hash` to skip unchanged files. Batch embedding calls. `text-embedding-3-small` is one of the cheapest embedding models available.

### Risk 2: Companion memory pollution
**Impact:** Too many knowledge-sourced memories could crowd out conversational observations in recall
**Mitigation:** Knowledge memories get moderate importance (3.0) — below human messages (6.0) but above agent observations (1.0). Monitor the ratio after deployment.

### Risk 3: Stale references after file moves
**Impact:** Agent gets a thought pointing to a file that no longer exists at that path
**Mitigation:** Watchdog catches delete events and cleans up. Full mtime scan on startup catches anything missed. The summary in the companion memory is still useful even if the file path is stale.

## Race Conditions

### Race 1: Rapid file saves during indexing
**Location:** Watchdog handler → indexer pipeline
**Trigger:** User saves file multiple times in quick succession while indexer is processing the first save
**Data prerequisite:** Previous indexing must complete before next starts for same file
**State prerequisite:** KnowledgeDocument record must not be partially written
**Mitigation:** Debounce timer (2s) collapses rapid events. Per-file lock in indexer prevents concurrent processing of the same file.

### Race 2: Bridge startup scan vs watchdog events
**Location:** Bridge startup → full scan + watchdog start
**Trigger:** File changes during the gap between scan start and watchdog registration
**Data prerequisite:** Scan must complete before watchdog processes events
**State prerequisite:** N/A
**Mitigation:** Start watchdog first (captures events into queue), then run full scan, then process queued events. Events for already-scanned files are idempotent (mtime check).

## No-Gos (Out of Scope)

- No write-back to work-vault — read-only access
- No PDF/image/binary file indexing — markdown and plain text only for v1
- No embedding-based similarity search in the recall flow — bloom filter discovery via companion memories only
- No Obsidian plugin or Obsidian API integration
- No iCloud sync handling (user confirmed sync is off)
- No cross-machine sync of KnowledgeDocument records — each machine indexes its own local work-vault

## Update System

- New Python dependency: `watchdog` — must be added to `pyproject.toml` and propagated via update script
- `OPENAI_API_KEY` must be set in `.env` on all machines (likely already present for other OpenAI usage) — verify during setup
- `popoto.configure(embedding_provider=OpenAIProvider())` must be called at bridge startup before any KnowledgeDocument operations — add to bridge initialization
- No changes to the update script itself — the watchdog starts automatically with the bridge

## Agent Integration

No new MCP server or tool needed. The agent already has `read_file` access — the reference pointer in companion memories tells it exactly which file to read. The indexer and watchdog run inside the bridge process, not as agent-facing tools.

- Bridge change: start/stop watchdog thread in `bridge/telegram_bridge.py` (or a new `bridge/knowledge_watcher.py` imported by the bridge)
- No `.mcp.json` changes
- Integration test: verify that a file change in work-vault produces a companion Memory that surfaces in recall when the agent works on that project

## Documentation

- [ ] Create `docs/features/knowledge-documents.md` describing the KnowledgeDocument model, indexing flow, watchdog, and scope resolution
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/subconscious-memory.md` with the new `reference` field on Memory and the `source="knowledge"` type
- [ ] Update CLAUDE.md Quick Commands table with knowledge indexer commands

## Success Criteria

- [ ] KnowledgeDocument model exists with ContentField + EmbeddingField
- [ ] Memory model has a `reference` StringField (generic JSON pointer)
- [ ] Watchdog thread starts with bridge, monitors ~/work-vault/
- [ ] File changes trigger KnowledgeDocument upsert + companion Memory creation
- [ ] Companion memories have `source="knowledge"`, moderate importance, and reference pointers
- [ ] Scope isolation: client project docs only surface in that project's context
- [ ] Company-wide docs surface in any project context
- [ ] Full mtime scan on startup catches changes missed while bridge was down
- [ ] File deletion cleans up KnowledgeDocument + companion memories
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (model)**
  - Name: model-builder
  - Role: Create KnowledgeDocument model and add reference field to Memory
  - Agent Type: builder
  - Resume: true

- **Builder (indexer)**
  - Name: indexer-builder
  - Role: Build the indexer pipeline (file processing, summarization, companion memory creation, scope resolution)
  - Agent Type: builder
  - Resume: true

- **Builder (watcher)**
  - Name: watcher-builder
  - Role: Build the watchdog filesystem watcher with debouncing and bridge integration
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end flow from file change to companion memory in recall
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs and update existing memory docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add reference field to Memory model
- **Task ID**: build-memory-reference
- **Depends On**: none
- **Validates**: tests/unit/test_memory_model.py (update), tests/unit/test_memory_reference.py (create)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `reference = StringField(default="")` to Memory model
- Add `SOURCE_KNOWLEDGE = "knowledge"` constant
- Update Memory docstring to document the reference field
- Write unit tests for reference field serialization/deserialization

### 2. Create KnowledgeDocument model
- **Task ID**: build-knowledge-model
- **Depends On**: none
- **Validates**: tests/unit/test_knowledge_document.py (create)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/knowledge_document.py` with: `doc_id` (AutoKeyField), `file_path` (KeyField), `project_key` (KeyField), `scope` (StringField — "client" or "company-wide"), `content` (ContentField), `embedding` (EmbeddingField with source="content" using the global OpenAIProvider set via popoto.configure()), `content_hash` (StringField — for skip-if-unchanged), `last_modified` (FloatField — file mtime)
- Implement `safe_upsert(file_path, project_key, scope)` class method
- Write unit tests for model creation, upsert, and deletion

### 3. Build scope resolver
- **Task ID**: build-scope-resolver
- **Depends On**: none
- **Validates**: tests/unit/test_scope_resolver.py (create)
- **Assigned To**: indexer-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/knowledge/scope_resolver.py`
- Load projects.json, map `knowledge_base` paths to project_keys — projects.json is the **single source of truth** for scope resolution (no CLAUDE.md parsing)
- A file path under a project's `knowledge_base` directory maps to that project_key with scope "client"
- A file path under `~/work-vault/` root (not under any project subfolder) maps to scope "company-wide" with project_key "company"
- Paths outside known mappings return None (skip)
- Return `(project_key, scope)` for any given file path, or None if path should be skipped
- Unit tests for all scope classifications

### 4. Build indexer pipeline
- **Task ID**: build-indexer
- **Depends On**: build-knowledge-model, build-memory-reference, build-scope-resolver
- **Validates**: tests/unit/test_knowledge_indexer.py (create)
- **Assigned To**: indexer-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/knowledge/indexer.py`
- `index_file(file_path)`: read file → resolve scope → upsert KnowledgeDocument → generate summary via Haiku → create/refresh companion Memory records with reference pointers
- `delete_file(file_path)`: remove KnowledgeDocument + companion memories
- `full_scan(vault_path)`: walk directory, compare mtimes, index changed files
- Handle large docs: split by top-level headings if >2000 words
- Companion memories: `source="knowledge"`, `importance=3.0`, `reference=json.dumps({"tool": "read_file", "params": {"file_path": path}})`

### 5. Build filesystem watcher
- **Task ID**: build-watcher
- **Depends On**: build-indexer
- **Validates**: tests/unit/test_knowledge_watcher.py (create)
- **Assigned To**: watcher-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/knowledge_watcher.py`
- `KnowledgeWatcher` class: wraps watchdog Observer, watches ~/work-vault/
- Debounce: collect events for 2s via threading.Timer, then batch-process unique paths
- Filter: only .md and .txt files, skip hidden files/dirs and _archive_
- `start()` / `stop()` / `is_healthy()` methods for bridge lifecycle
- `is_healthy()` returns True if the watcher thread is alive and the Observer is running — bridge calls this every 60s and auto-restarts the watcher if dead
- On start: register watchdog, then run `full_scan()` for catch-up
- All exceptions caught — watcher crash must not affect bridge
- Log warning when watcher thread dies unexpectedly; log info when auto-restarted

### 6. Integrate watcher with bridge
- **Task ID**: build-bridge-integration
- **Depends On**: build-watcher
- **Validates**: tests/unit/test_knowledge_watcher.py (update)
- **Assigned To**: watcher-builder
- **Agent Type**: builder
- **Parallel**: false
- Import and start KnowledgeWatcher in bridge startup
- Call `popoto.configure(embedding_provider=OpenAIProvider())` before starting watcher
- Stop watcher on bridge shutdown
- Add periodic liveness check: bridge calls `watcher.is_healthy()` every 60s (piggyback on existing bridge health loop or add asyncio.create_task). If unhealthy, log warning and call `watcher.stop(); watcher.start()` to auto-restart.
- Add health logging: "Knowledge watcher started, monitoring N files"

### 7. Validate end-to-end flow
- **Task ID**: validate-integration
- **Depends On**: build-bridge-integration
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Create a test markdown file in work-vault test area
- Verify KnowledgeDocument is created with correct scope
- Verify companion Memory exists with source="knowledge" and valid reference pointer
- Verify Memory appears in bloom filter (keyword check)
- Verify file deletion cleans up both records
- Verify scope isolation: client doc not visible from other project_key

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/knowledge-documents.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/features/subconscious-memory.md` with reference field and knowledge source
- Update CLAUDE.md Quick Commands

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify documentation completeness
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| KnowledgeDocument model importable | `python -c "from models.knowledge_document import KnowledgeDocument"` | exit code 0 |
| Memory reference field exists | `python -c "from models.memory import Memory; m = Memory(content='test', reference='{}')"` | exit code 0 |
| Popoto configured | `python -c "import popoto; from popoto.embeddings.openai import OpenAIProvider; popoto.configure(embedding_provider=OpenAIProvider())"` | exit code 0 |
| Scope resolver works | `python -c "from tools.knowledge.scope_resolver import resolve_scope; print(resolve_scope('/tmp/test.md'))"` | exit code 0 |
| Feature docs exist | `test -f docs/features/knowledge-documents.md` | exit code 0 |

## Critique Results

**Plan**: docs/plans/knowledge_document_integration_528.md
**Issue**: #528
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 4 total (2 blockers, 2 concerns, 0 nits)

### Blockers

#### 1. VoyageProvider import path does not exist
- **Severity**: BLOCKER
- **Critics**: Skeptic, Adversary
- **Location**: Solution > Technical Approach, Task 2
- **Finding**: The plan specifies `EmbeddingField with VoyageProvider(model="voyage-3")` but `from popoto.providers.voyage import VoyageProvider` raises `ModuleNotFoundError: No module named 'popoto.providers'`. The `voyageai` package is also not installed. The entire embedding pipeline depends on a provider class that does not exist in the installed popoto version.
- **Suggestion**: Verify the correct import path for EmbeddingField's provider configuration by checking popoto docs or source. If VoyageProvider is not yet shipped in popoto, this is a hard dependency that must be resolved before build. The plan should include a spike task to confirm the EmbeddingField provider API and correct the import path. Also add `pip install voyageai` and `pip install popoto[voyage]` as explicit pre-build setup steps.
- **Resolution**: Switched to `OpenAIProvider` from `popoto.embeddings.openai` -- `openai` package is already installed. VoyageProvider exists in popoto but requires uninstalled `voyageai` package. OpenAIProvider uses `text-embedding-3-small` (1536-dim), consistent with the existing knowledge_search tool's model choice.

#### 2. Existing `tools/knowledge_search/` tool not addressed in Prior Art
- **Severity**: BLOCKER
- **Critics**: Archaeologist, Simplifier
- **Location**: Prior Art, Solution
- **Finding**: The plan states "No prior issues found related to work-vault integration or KnowledgeDocument modeling. This is greenfield work." However, `tools/knowledge_search/__init__.py` already implements a full knowledge base search system with document indexing, chunking, semantic/keyword/hybrid search using SQLite + OpenRouter embeddings. This is directly overlapping functionality. The plan creates a parallel system (Popoto + Voyage) without acknowledging or replacing the existing one, risking two competing knowledge search implementations.
- **Suggestion**: The plan must address the existing `tools/knowledge_search/` tool: either (a) replace it with the new KnowledgeDocument system and delete the old code, (b) integrate with it, or (c) explicitly scope the two as different layers with a clear boundary. Add this to the Prior Art section and add a task for migration/cleanup.
- **Resolution**: Prior Art section now documents the existing tool and states reconciliation strategy: KnowledgeDocument system supersedes it (Popoto-native, project-scoped, memory-integrated). Existing tool preserved during v1; follow-up task to migrate and remove.

### Concerns

#### 3. Watchdog thread crash isolation is specified but not architecturally enforced
- **Severity**: CONCERN
- **Critics**: Operator, Skeptic
- **Location**: Solution > Filesystem watcher, Task 5, Failure Path Test Strategy
- **Finding**: The plan says "watcher crash must not take down the bridge" and Task 5 says "all exceptions caught." However, the bridge startup (`bridge/telegram_bridge.py`) runs in an asyncio event loop, and the plan adds a `watchdog` thread (OS-level threading.Timer + Observer). If the watchdog thread raises an unhandled exception in its event callback, Python's default behavior is to silently kill only that thread -- but if the thread holds a lock or is mid-write to Redis/Popoto, it could leave corrupted state. The plan has no health-check or restart mechanism for a dead watcher thread.
- **Suggestion**: Add a periodic liveness check (e.g., bridge checks watcher thread `is_alive()` every 60s and restarts it if dead). Log a warning when the watcher thread dies. This is mentioned vaguely in "Watchdog thread health is visible in bridge status/logs" but should be an explicit task with a concrete implementation.
- **Resolution**: Added `is_healthy()` method to KnowledgeWatcher (Task 5) and 60s periodic liveness check with auto-restart in bridge integration (Task 6).

#### 4. Scope resolver depends on work-vault CLAUDE.md classifications but no parsing spec
- **Severity**: CONCERN
- **Critics**: Skeptic, Adversary
- **Location**: Solution > Scope resolver, Task 3
- **Finding**: The plan says the scope resolver "classifies paths as client-scoped or company-wide using work-vault CLAUDE.md rules" but does not specify how to parse that CLAUDE.md file. If the CLAUDE.md format changes, the resolver silently misclassifies files. Additionally, projects.json already has `knowledge_base` fields per project -- it is unclear why the resolver also needs to parse CLAUDE.md instead of just using the projects.json mappings exclusively.
- **Suggestion**: Specify whether scope resolution uses projects.json `knowledge_base` mappings alone (simpler, more reliable) or also parses CLAUDE.md (more complex, fragile). If CLAUDE.md is needed, document the expected format and add a validation step. Consider using projects.json as the single source of truth for scope.
- **Resolution**: Clarified that projects.json `knowledge_base` mappings are the single source of truth for scope resolution. No CLAUDE.md parsing. Paths under a project's knowledge_base dir map to that project; paths under ~/work-vault/ root map to "company-wide".

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | All 4 required sections present and non-empty |
| Task numbering | PASS | Sequential 1-9, no gaps |
| Dependencies valid | PASS | All Depends On references point to valid task IDs |
| File paths exist | PARTIAL | 5 of 15 referenced files exist (10 are intentionally new files to be created) |
| Prerequisites met | PASS | `openai` installed; `OpenAIProvider` import path verified at `popoto.embeddings.openai`; `watchdog` to be installed |
| Cross-references | PASS | Success criteria map to tasks; no-gos not in solution |

### Verdict

**REVISED** -- Both blockers resolved:
1. ~~VoyageProvider/voyageai dependency~~ -- Replaced with `OpenAIProvider` from `popoto.embeddings.openai` (uses `openai` package, already installed). Configured via `popoto.configure()` at bridge startup.
2. ~~Existing `tools/knowledge_search/` not addressed~~ -- Prior Art section now documents the existing SQLite-based tool and states the reconciliation strategy: KnowledgeDocument system supersedes it; existing tool preserved during v1, follow-up task to migrate and remove.
3. Watchdog liveness concern -- Added `is_healthy()` method and 60s periodic check with auto-restart in bridge integration task.
4. Scope resolver CLAUDE.md parsing concern -- Clarified that projects.json `knowledge_base` mappings are the single source of truth; no CLAUDE.md parsing.

---

## Open Questions

1. **Companion memory importance level**: I proposed 3.0 (between agent=1.0 and human=6.0). Does that feel right, or should knowledge docs rank higher/lower?
2. **Reference pointer format**: The JSON structure `{"tool": "read_file", "params": {"file_path": "..."}}` mirrors tool calls. For non-file references (email, person, URL), should we standardize the shape now or let it evolve? Examples discussed: `{"tool": "gmail_read_thread", "params": {"threadId": "abc"}}`, `{"entity": "person", "name": "Tom", "channel": "telegram"}`, `{"url": "https://docs.example.com"}`.
3. **Heading-based splitting threshold**: Proposed >2000 words triggers per-heading companion memories. Is that the right threshold?
