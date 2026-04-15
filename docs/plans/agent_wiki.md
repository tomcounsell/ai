---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/728
last_comment_id: none
revision_applied: true
---

# Agent-Maintained Knowledge Wiki (LLM Wiki Pattern)

## Problem

**Current behavior:** Agents accumulate knowledge as flat, atomic memory observations stored in Redis — one-liners like "Redis is used for operational state only." These observations have no structure, no cross-references, and no synthesis. When an agent processes a meaningful source (article, architectural decision, post-merge learning), the insight either becomes a one-liner memory or disappears into conversation history. The work vault at `~/work-vault/` is human-maintained and nearly empty — the `AI Valor Engels System/` folder has only a handful of content files, confirming that human-maintained wikis go stale.

**Desired outcome:** Agents own and maintain the work vault as a structured, interlinked knowledge base following Karpathy's LLM Wiki pattern. When agents process meaningful sources (post-merge learnings, architectural decisions, articles), they write structured wiki pages — entity pages, concept pages, synthesis documents — with Obsidian-native conventions (YAML frontmatter, wikilinks, local assets). A periodic lint operation health-checks the wiki for contradictions, orphan pages, and gaps. Obsidian is the human viewing layer; agents do all the bookkeeping. The existing subconscious memory system remains for operational context recall; the wiki is for accumulated, structured knowledge.

## Freshness Check

**Baseline commit:** `0e4d41e13f35ba688cdf6817574c7f3afeb266e9`
**Issue filed at:** 2026-04-06T02:12:05Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `tools/knowledge/indexer.py` — read-only pipeline confirmed. No write path exists. Still holds.
- `tools/knowledge/scope_resolver.py` — scope resolution via `projects.json` confirmed. Still holds.
- `agent/memory_extraction.py` — post-merge extraction produces flat Memory records (`POST_MERGE_EXTRACTION_PROMPT`). No wiki page writing. Still holds.
- `~/work-vault/CLAUDE.md` — NDA isolation rules confirmed in vault root. Per-project NDA scoping via `projects.json` `knowledge_base` fields. Still holds.

**Cited sibling issues/PRs re-checked:**
- #528 — CLOSED 2026-03-30. Merged PR #605 "Add knowledge document integration system" — built the read pipeline (vault to memory). Wiki write pipeline is genuinely new work.
- #611 — CLOSED 2026-03-31. Merged PR #615 "Fix stale Haiku model ID in knowledge indexer". Indexer uses current `HAIKU` model constant from `config.models`.
- #500 — CLOSED 2026-03-25. Merged PR #517 "Cross-agent knowledge relay". Added per-session findings sharing. Separate concern.
- #586 — CLOSED 2026-03-30. "Update memory agent integration" — updated metadata-aware recall. Separate concern.

**Commits on main since issue was filed (touching referenced files):**
- `d00a1c95` feat(reflections): delete 3086-line monolith, extract reflections/ package — **Major drift for reflections.py**: the lint integration point is now `reflections/` package (individual async callables), not `scripts/reflections.py`. Wiki lint must be added as a new callable in `reflections/` (e.g., `reflections/wiki_lint.py`), not a `scripts/reflections.py` extension.
- `3accddaa` chore: remove dead SQLite dependencies (knowledge_search, DatabaseSettings) — confirms knowledge pipeline is Redis-only. No SQLite artifacts remain.
- `4db01cc1` feat: chunked document retrieval — confirms `DocumentChunk` model is live. Agent-written wiki pages will automatically get chunked when the watcher picks them up.

**Active plans in `docs/plans/` overlapping this area:** No active plans touching wiki write, reflections/wiki_lint, or post-merge wiki integration.

**Notes:** The reflections drift is the key adjustment: `scripts/reflections.py` no longer exists as a monolith. Wiki lint must be a standalone async callable in `reflections/wiki_lint.py`, registered in the YAML scheduler config.

## Prior Art

- **Issue #528 / PR #605** — "Add knowledge document integration system" — Built the read pipeline: `tools/knowledge/indexer.py` watches the vault via `KnowledgeWatcher` (watchdog), indexes markdown into `KnowledgeDocument` + `DocumentChunk` Redis records, creates companion `Memory` records. This is the foundation the wiki write pipeline builds on. Succeeded.
- **Issue #500 / PR #517** — "Cross-agent knowledge relay: persistent findings from parallel work" — Added session-level findings sharing for parallel agents. Different pattern (ephemeral findings, not persistent wiki pages). Succeeded.
- **Issue #748** — "Finish reflections unification: extract monolith units, wire memory reflections, relocate config" — Closed 2026-04-14, merged PR #967. Extracted `scripts/reflections.py` into `reflections/` package with individual async callables. This is the architectural change that defines where wiki lint lives. Succeeded.

## Spike Results

### spike-1: Work vault current state
- **Assumption**: "The AI Valor Engels System/ folder has only 3 content files, confirming human-maintained wikis go stale"
- **Method**: code-read
- **Finding**: `AI Valor Engels System/` contains: `Books to read.md`, `Harness/` (Claude Code Prompts.md, Cognitive Memory Design.md, MCPs.md, Migration to Pi?.md, OpenClaw Memory Comparison.md, Tool Auditing.md), `Personas/`, `Valor Engels ID and CC.md`, `secrets/`. The claim holds — content is sparse and largely identity/config files, not accumulated knowledge.
- **Confidence**: high
- **Impact on plan**: Confirms the problem is real. Agent wiki can start populating `AI Valor Engels System/` as the primary target namespace for system knowledge.

### spike-2: Reflections package structure post-PR-967
- **Assumption**: "scripts/reflections.py is the integration point for wiki lint"
- **Method**: code-read
- **Finding**: `scripts/reflections.py` was deleted by PR #967. The `reflections/` package now contains standalone async callables (`maintenance.py`, `session_intelligence.py`, `memory_management.py`, `behavioral_learning.py`, `auditing.py`, `daily_report.py`, `task_management.py`). Wiki lint must be added as `reflections/wiki_lint.py` and registered in the YAML scheduler config.
- **Confidence**: high
- **Impact on plan**: Wiki lint task targets `reflections/wiki_lint.py` instead of `scripts/reflections.py`.

### spike-3: Post-merge learning extraction current behavior
- **Assumption**: "Post-merge learning extraction writes flat memories — no wiki page writing"
- **Method**: code-read
- **Finding**: `agent/memory_extraction.py` defines `POST_MERGE_EXTRACTION_PROMPT` which extracts a single observation as a flat `Memory` record (category + importance + tags). No file writes. The integration point for wiki enhancement is `async def extract_post_merge_learning()` in this file.
- **Confidence**: high
- **Impact on plan**: Post-merge wiki writing extends `extract_post_merge_learning()` — write a wiki page AND save the memory, rather than replacing the memory.

### spike-4: NDA isolation mechanism
- **Assumption**: "projects.json knowledge_base paths define NDA isolation for wiki writes"
- **Method**: code-read
- **Finding**: `tools/knowledge/scope_resolver.py` resolves `(project_key, scope)` from file paths using `projects.json`'s `knowledge_base` fields. Wiki writer must use the same resolver — any write targeting a path under a project's `knowledge_base` must be isolated to that project's context. The vault CLAUDE.md confirms per-project isolation is the intended model.
- **Confidence**: high
- **Impact on plan**: Wiki writer reuses `scope_resolver.resolve_scope()` to validate write targets. No new isolation mechanism needed.

## Data Flow

### Write Path (Ingest)

1. **Trigger**: Post-merge hook fires in `agent/memory_extraction.py::extract_post_merge_learning()` OR agent calls wiki writer tool directly
2. **WikiWriter**: `tools/wiki/writer.py` — constructs page content (YAML frontmatter, wikilinks, structured body), determines target file path using `scope_resolver`, writes to `~/work-vault/{project}/{slug}.md`
3. **Index update**: `tools/wiki/index.py` — upserts one-line entry into `{project}/_index.md`, appends timestamped entry to `{project}/_log.md`
4. **Knowledge Watcher**: existing `bridge/knowledge_watcher.py` picks up the new file via watchdog (2s debounce), calls `indexer.index_file()` → creates `KnowledgeDocument` + `DocumentChunk` + companion `Memory` records automatically
5. **Output**: Wiki page on disk + indexed into memory system (companion Memory at importance 3.0)

### Lint Path

1. **Trigger**: `ReflectionScheduler` fires `reflections.wiki_lint.run_wiki_lint` on schedule (e.g., weekly)
2. **WikiLint** (`reflections/wiki_lint.py`): reads all pages in vault, checks `_index.md` for orphan pages, reads page pairs for contradiction detection via Haiku, checks for stale claims (source older than N days), identifies important concepts without pages
3. **Output**: `{"status": "ok", "findings": [...], "summary": str}` — findings fed into GitHub issues or Telegram alert if severity threshold exceeded

### Query Path (Read)

1. **Agent needs context**: during session, agent queries memory system (existing bloom filter recall)
2. **Companion Memory fires**: thought injected with wiki page summary + file path
3. **Agent reads page**: uses `read_file` tool on the vault path
4. **Agent follows wikilinks**: reads linked pages as needed
5. **Output**: structured knowledge from wiki pages augments session context

## Architectural Impact

- **New dependencies**: None beyond existing (`anthropic`, `watchdog`, `tiktoken`). Wiki pages are plain markdown files.
- **Interface changes**: `agent/memory_extraction.py::extract_post_merge_learning()` gains optional wiki page writing. `reflections/` package gains `wiki_lint.py`. New `tools/wiki/` package.
- **Coupling**: Wiki write pipeline couples `agent/memory_extraction.py` to `tools/wiki/writer.py`. This is additive — existing memory extraction behavior is unchanged.
- **Data ownership**: Agent takes write ownership of `~/work-vault/{project}/` subfolders. Human still owns `~/work-vault/CLAUDE.md`, `_index.md` (vault root), and project `README.md` files. Clear separation.
- **Reversibility**: High. `tools/wiki/` can be deleted, post-merge wiki writing can be disabled via feature flag, lint callable can be unregistered from YAML scheduler. No schema migrations. Existing vault files remain readable by Obsidian.

## Appetite

**Size:** Large

**Team:** Solo dev + PM check-ins

**Interactions:**
- PM check-ins: 2-3 (scope alignment at plan, mid-build confirmation, final review)
- Review rounds: 2 (code review, end-to-end Obsidian rendering check)

## Prerequisites

No new external services. All dependencies are already in the stack.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Work vault accessible | `python -c "import os; assert os.path.isdir(os.path.expanduser('~/work-vault'))"` | Wiki write target |
| Anthropic API key | `python -c "from config.settings import settings; assert settings.ANTHROPIC_API_KEY"` | Haiku for lint contradiction detection |
| Knowledge watcher running | `python -c "from bridge.knowledge_watcher import KnowledgeWatcher; print('ok')"` | Auto-index written pages |

## Solution

### Key Elements

- **WikiWriter** (`tools/wiki/writer.py`): Constructs and writes structured wiki pages to the vault. Handles YAML frontmatter, wikilink injection, page type templates (entity, concept, decision, synthesis). Enforces NDA isolation via `scope_resolver`. Updates `_index.md` and `_log.md`. Uses **synchronous file I/O only** — no `aiofiles`, no async methods (required by hook call site).
- **WikiIndex** (`tools/wiki/index.py`): Maintains per-project `_index.md` (content catalog with one-line summaries) and `_log.md` (chronological ingest/lint record). Idempotent upserts.
- **WikiLint** (`reflections/wiki_lint.py`): Async callable for the reflection scheduler. Reads all pages, finds orphans (not in index), detects contradictions (Haiku pairwise check), flags stale claims, reports coverage gaps. Writes JSON result to `logs/wiki_lint.log`.
- **Post-merge Wiki Integration**: Extend `agent/memory_extraction.py::extract_post_merge_learning()` to optionally write a wiki page when the extracted observation is a decision or pattern. MCP exposure of wiki operations is deferred to a follow-on issue — v1 agents invoke wiki writes via the post-merge hook only.

### Flow

**Post-merge ingest flow:**
PR merged → `extract_post_merge_learning()` fires → Haiku extracts observation → if category is "decision" or "pattern" → `WikiWriter.write_page()` → vault file written → KnowledgeWatcher picks up → indexed into memory system → companion Memory at importance 3.0 → flat Memory also saved (unchanged behavior)

**Lint flow (weekly):**
`ReflectionScheduler` fires `wiki_lint.run_wiki_lint` → reads all pages via `full_scan()` → pairwise contradiction check (sample, not exhaustive) → orphan detection → stale page detection → findings returned → if findings > threshold → GitHub issue created

### Technical Approach

- **Page templates**: Entity pages (people, systems, tools), Concept pages (patterns, conventions, decisions), Synthesis pages (cross-source summaries). Each template has a fixed YAML frontmatter schema.
- **YAML frontmatter**: `tags`, `created`, `updated`, `source_count`, `project_key`, `page_type`. Dataview-compatible.
- **Slug sanitization (MANDATORY)**: Before any path construction, slugs MUST be sanitized via strict allowlist: `slug = re.sub(r'[^a-z0-9\-_]', '-', slug.lower())`. This prevents path traversal — `pathlib.Path(vault_dir) / f".{slug}.md.tmp"` does NOT block `..` segments in Python's pathlib. Test assertion required: `assert slugify("fix: config/../secrets") == "fix-config---secrets"`.
- **Synchronous I/O only**: WikiWriter uses synchronous file I/O exclusively — no `aiofiles`, no `async def` methods, no `await`. Required because `extract_post_merge_learning()` is called via `asyncio.run()` in `.claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()`; an async WikiWriter inside `asyncio.run()` raises `RuntimeError: This event loop is already running`.
- **Startup temp file sweep**: At the start of `write_page()`, WikiWriter sweeps the target vault directory for `*.md.tmp` files older than 5 minutes and removes them. This cleans up orphaned temp files left by SIGKILL or power loss, since `os.rename()` is atomic within the same filesystem but orphans accumulate across crashes.
- **Wikilinks**: WikiWriter scans `_index.md` for existing page titles AND verifies the corresponding `.md` file exists on disk before auto-inserting `[[Page Title]]` links. Links are only injected for titles with confirmed on-disk presence to prevent dead links in Obsidian.
- **NDA isolation**: All write operations call `scope_resolver.resolve_scope(target_path)` before writing. If scope is `"client"`, the write must be triggered from within that project's session context (verified via `project_key` parameter). Company-wide paths are writable from any context.
- **Lint contradiction detection**: Haiku prompt comparing two page excerpts. Runs on a sample (up to 20 page pairs per lint pass) to bound token cost. Full coverage over time via random sampling.
- **Feature flag**: `WIKI_WRITE_ENABLED` env var (default `true`). Set to `false` to disable post-merge wiki writing without code changes.
- **Idempotency**: WikiWriter checks for existing page by slug-based file existence (`{project}/{slug}.md`) — not title string matching (which would require scanning all files). If the file exists, the update strategy is: append new content as a dated section to the body, update frontmatter `updated` and `source_count`. Wikilinks are de-duplicated after merge to prevent accumulation across successive writes.

## Failure Path Test Strategy

### Exception Handling Coverage
- `tools/wiki/writer.py` must catch all file I/O exceptions and log warnings — wiki write failures must never crash the agent or post-merge hook
- `reflections/wiki_lint.py` must catch all exceptions per the reflections package contract and return `{"status": "error", ...}` — never raise
- `agent/memory_extraction.py` wiki extension must be wrapped in the existing try/except block — wiki failures must not break memory extraction

### Empty/Invalid Input Handling
- WikiWriter must handle empty observation strings (skip page creation, log debug)
- WikiWriter must handle missing project_key (default to company-wide namespace)
- WikiLint must handle empty vault (return `{"status": "ok", "findings": [], "summary": "No wiki pages found"}`)

### Error State Rendering
- If WikiWriter fails to write a page, the post-merge hook continues (memory saved, wiki page not written) — partial success is acceptable
- WikiLint findings are surfaced through the existing reflections dashboard, not directly to Telegram — no user-visible error path

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py` — UPDATE: add test for `extract_post_merge_learning()` with wiki write path enabled and disabled (via `WIKI_WRITE_ENABLED`)
- [ ] `agent/memory_extraction.py` integration tests (if any) — UPDATE: verify wiki page is written for "decision" category extractions

New tests to create:
- `tests/unit/test_wiki_writer.py` — NEW: test page creation, idempotent slug-based upsert (no duplicate wikilinks), slug sanitization assertion, YAML frontmatter, wikilink injection (only for files confirmed on disk), NDA isolation enforcement, atomic write, startup temp sweep
- `tests/unit/test_wiki_index.py` — NEW: test `_index.md` upsert, `_log.md` append, idempotency, empty vault
- `tests/unit/test_wiki_lint.py` — NEW: test orphan detection, stale page detection, empty vault handling, exception contract (never raises), log output written

## Rabbit Holes

- **Embedding-based wiki search**: Karpathy mentions embedding-based query for scale. Our vault is moderate size; the companion Memory + bloom filter path handles query. Defer until wiki exceeds hundreds of pages.
- **Real-time contradiction detection**: Checking every page pair on every write is O(n²) token cost. Lint pass with random sampling is sufficient for v1.
- **Git versioning of wiki pages**: Tracking wiki page history via git. The vault is not a git repo. Obsidian's file history via filesystem is sufficient. Defer.
- **Obsidian plugin integration**: Custom Obsidian plugins for agent-facing UI. Obsidian is the human viewing layer only. No plugin work.
- **Multi-machine vault sync**: The vault syncs via iCloud (standard Obsidian setup). Agent writes are local — iCloud handles propagation. No custom sync logic needed.
- **Automatic image/asset download**: Karpathy mentions downloading article images locally. v1 handles text-only pages. Defer asset downloads.

## Risks

### Risk 1: KnowledgeWatcher picks up partial writes
**Impact:** An agent writes a large page incrementally; the watcher fires on a half-written file, creating a corrupt `KnowledgeDocument`.
**Mitigation:** WikiWriter writes to a temp file in the same directory (`.{slug}.md.tmp`), then renames atomically. The rename is a single filesystem op — watcher sees only the complete file.

### Risk 2: NDA isolation violation via path traversal
**Impact:** An agent working on project A writes a wiki page to project B's vault folder, leaking cross-project context.
**Mitigation:** WikiWriter calls `scope_resolver.resolve_scope(target_path)` before every write and compares the resolved `project_key` against the caller's `project_key`. Mismatches raise `PermissionError` (caught by the caller's try/except, logged as warning). No write proceeds.

### Risk 3: Lint pass token cost
**Impact:** Haiku contradiction detection runs on N page pairs per lint pass. With 100+ pages, random sampling of 20 pairs per pass is 20 × (2 × ~1000 tokens) = ~40K tokens per lint pass. At Haiku pricing this is negligible, but could grow.
**Mitigation:** Lint pass caps at 20 page pairs (configurable constant `LINT_MAX_PAIRS = 20`). Log token usage per lint pass.

### Risk 4: Wikilink staleness
**Impact:** Agent writes `[[Page Title]]` links. If the linked page is renamed or deleted, the link breaks in Obsidian.
**Mitigation:** WikiIndex maintains a canonical title registry in `_index.md`. WikiWriter only inserts links for titles found in the registry. Broken links from manual renames are surfaced by the WikiLint orphan check.

## Race Conditions

### Race 1: Concurrent post-merge writes to same wiki page
**Location:** `tools/wiki/writer.py::write_page()`
**Trigger:** Two PRs merge nearly simultaneously; both extractions try to update the same concept page
**Data prerequisite:** File must exist and be fully written before the second write reads it for merge
**State prerequisite:** No concurrent write in progress
**Mitigation:** WikiWriter uses a file-level advisory lock (`fcntl.flock`) before reading+writing. Lock is held for the duration of the read-merge-write cycle. Temp file + rename pattern prevents partial reads.

## No-Gos (Out of Scope)

- Replacing the subconscious memory system — wiki and memory are complementary, not competing
- Writing to vault root `_index.md` (human-maintained, agent writes per-project `_index.md` only)
- Writing to `README.md` files in project folders (human-owned)
- Vault-root CLAUDE.md modifications
- Cross-project wiki page linking (NDA boundary)
- Embedding-based wiki search (v2)
- Obsidian plugin development
- Wiki page deletion (pages are append-only in v1; deprecation is handled by frontmatter `deprecated: true` field)
- MCP exposure of wiki tools (`wiki_write`, `wiki_query`) — the `mcp_servers/` directory does not exist; creating entire MCP infrastructure is out of scope for v1. The write pipeline is triggered automatically via the post-merge hook. MCP integration is a follow-on issue.

## Update System

The wiki write feature is local to the bridge machine where `~/work-vault/` lives. No multi-machine deployment concerns for v1 (the vault is a single-machine Obsidian vault synced by iCloud).

- **Update script**: No changes needed. `tools/wiki/` is a new package, no migration of existing data.
- **New env vars**: `WIKI_WRITE_ENABLED` (default `true`) — add to `.env.example` with documentation.
- **Reflections YAML**: The wiki lint callable must be registered in the reflections scheduler YAML config. The update script must propagate the new YAML entry if it's managed centrally. Check `config/reflections.yaml` or equivalent for the scheduler config location.
- **Migration**: None — wiki pages start from empty on first ingest. Existing vault files are not modified.

## Agent Integration

No agent integration required for v1. The wiki write pipeline is triggered automatically via the post-merge hook in `.claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()` — no explicit agent invocation is needed for the core write path.

The `mcp_servers/` directory does not currently exist in this repo. Creating MCP infrastructure to expose `wiki_write` and `wiki_query` tools would require building an entire new server layer, which is out of scope for v1. Agents can observe wiki content through the existing companion Memory recall (KnowledgeWatcher auto-indexes written pages within seconds, creating Memory records that surface via bloom filter recall).

MCP tool exposure (`wiki_write`, `wiki_query`) is scoped to a follow-on issue after the write pipeline is validated in production.

## Documentation

- [ ] Create `docs/features/agent-wiki.md` describing the LLM Wiki pattern implementation, data flow, page templates, NDA isolation enforcement, and lint schedule
- [ ] Update `docs/features/knowledge-document-integration.md` to add a "Write Path" section referencing the new agent-wiki feature
- [ ] Update `docs/features/subconscious-memory.md` to clarify the wiki/memory boundary (wiki = structured knowledge; memory = operational context)
- [ ] Add entry to `docs/features/README.md` index table for `agent-wiki`
- [ ] Add `WIKI_WRITE_ENABLED` to `.env.example` with description

## Success Criteria

### Technical Criteria
- [ ] `tools/wiki/writer.py` creates well-formed Obsidian pages with YAML frontmatter, wikilinks, and correct project_key isolation
- [ ] Slug sanitization enforced: `slugify("fix: config/../secrets")` returns a safe string with no `/` or `..` components
- [ ] WikiWriter is synchronous-only: no `aiofiles`, no `async def` methods — verified by `grep -n "async def\|aiofiles" tools/wiki/writer.py` returning no results
- [ ] Post-merge learning extraction writes a wiki page for "decision" and "pattern" category extractions (when `WIKI_WRITE_ENABLED=true`)
- [ ] `_index.md` and `_log.md` are maintained per project with correct entries after each ingest
- [ ] `reflections/wiki_lint.py` reports orphan pages, stale pages, and (via sampling) contradictions; results written to `logs/wiki_lint.log`
- [ ] NDA isolation is enforced: writing to a client project's folder from a different project context raises a logged error and no file is written
- [ ] Existing knowledge indexer continues working — agent-written pages appear in `KnowledgeDocument` and companion `Memory` records within 5 seconds of write
- [ ] All new tests pass (`pytest tests/unit/test_wiki_*.py tests/unit/test_memory_extraction.py`)
- [ ] Lint clean (`python -m ruff check .`)

### User-Facing Criteria
- [ ] After a PR merges with a "decision" or "pattern" extraction, a structured wiki page appears in Obsidian within 30 seconds (visible in Obsidian's file explorer and graph view)
- [ ] Weekly wiki lint produces a `logs/wiki_lint.log` entry with at least one actionable finding (orphan, stale page, or potential contradiction) within the first month of active wiki use
- [ ] Agent-written pages render correctly in Obsidian: YAML frontmatter is parsed (visible in Properties panel), wikilinks resolve to existing pages (no unresolved purple links for auto-injected links)

## Team Orchestration

### Team Members

- **Builder (wiki-core)**
  - Name: wiki-core-builder
  - Role: Implement `tools/wiki/writer.py`, `tools/wiki/index.py`, `tools/wiki/__init__.py`, and `tools/wiki/templates.py` (page templates)
  - Agent Type: builder
  - Resume: true

- **Builder (reflections-lint)**
  - Name: wiki-lint-builder
  - Role: Implement `reflections/wiki_lint.py` and register it in the reflections YAML scheduler config
  - Agent Type: builder
  - Resume: true

- **Builder (post-merge-integration)**
  - Name: post-merge-builder
  - Role: Extend `agent/memory_extraction.py::extract_post_merge_learning()` with wiki page writing (feature-flagged)
  - Agent Type: builder
  - Resume: true

- **Validator (wiki-core)**
  - Name: wiki-core-validator
  - Role: Verify wiki writer creates correct pages, enforces NDA isolation, and maintains index/log correctly
  - Agent Type: validator
  - Resume: true

- **Validator (integration)**
  - Name: wiki-integration-validator
  - Role: Verify end-to-end flow: post-merge → wiki write → KnowledgeWatcher → memory indexed; page renders in Obsidian
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: wiki-documentarian
  - Role: Create `docs/features/agent-wiki.md` and update related docs
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 — Core: builder, validator, documentarian

## Step by Step Tasks

### 1. Build Wiki Core (writer + index + templates)
- **Task ID**: build-wiki-core
- **Depends On**: none
- **Validates**: `tests/unit/test_wiki_writer.py`, `tests/unit/test_wiki_index.py` (create)
- **Informed By**: spike-1 (vault structure), spike-4 (NDA isolation via scope_resolver)
- **Assigned To**: wiki-core-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/wiki/__init__.py` with `write_page()` and `query_index()` public API
- Create `tools/wiki/templates.py` with page type templates (entity, concept, decision, synthesis) — YAML frontmatter schema per type
- Create `tools/wiki/writer.py`: `write_page(title, content, page_type, project_key, source_ref)` — sanitizes slug via `re.sub(r'[^a-z0-9\-_]', '-', slug.lower())` before any path construction; resolves vault path via scope_resolver; runs startup sweep removing `*.md.tmp` older than 5 min; writes with atomic temp+rename; updates `_index.md` and `_log.md`; enforces NDA isolation; uses synchronous file I/O only (no aiofiles)
- Create `tools/wiki/index.py`: `upsert_index_entry(project_key, title, summary, file_path)`, `append_log_entry(project_key, event_type, detail)` — idempotent, creates `_index.md`/`_log.md` if absent
- Create `tests/unit/test_wiki_writer.py` — test creation, idempotent slug-based update (verify no wikilink duplication), slug sanitization assertion (`slugify("fix: config/../secrets")` has no `/` or `..`), NDA isolation enforcement, atomic write, startup temp sweep
- Create `tests/unit/test_wiki_index.py` — test index upsert, log append, idempotency, empty vault

### 2. Build Wiki Lint Reflection
- **Task ID**: build-wiki-lint
- **Depends On**: build-wiki-core
- **Validates**: `tests/unit/test_wiki_lint.py` (create)
- **Informed By**: spike-2 (reflections package structure post-PR-967)
- **Assigned To**: wiki-lint-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/wiki_lint.py`: async callable `run_wiki_lint()` returning `{"status", "findings", "summary"}` — orphan detection, stale page detection, Haiku contradiction sampling (max `LINT_MAX_PAIRS = 20`)
- Register `reflections.wiki_lint.run_wiki_lint` in the reflections YAML scheduler config (weekly cadence)
- Create `tests/unit/test_wiki_lint.py` — test orphan detection, stale detection, empty vault, exception handling contract

### 3. Extend Post-Merge Learning Extraction
- **Task ID**: build-post-merge-integration
- **Depends On**: build-wiki-core
- **Validates**: `tests/unit/test_memory_extraction.py` (update)
- **Informed By**: spike-3 (post-merge extraction current behavior)
- **Assigned To**: post-merge-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `WIKI_WRITE_ENABLED` env var support to `config/settings.py` (default `true`)
- Extend `agent/memory_extraction.py::extract_post_merge_learning()`: after extracting observation, if `category in ("decision", "pattern")` and `WIKI_WRITE_ENABLED`, call `tools.wiki.writer.write_page()` with the observation as content — wrapped in try/except, failure is non-fatal
- Add `WIKI_WRITE_ENABLED=true` to `.env.example`
- Update `tests/unit/test_memory_extraction.py`: add test for wiki write path enabled (verify `write_page` called), wiki write path disabled (verify `write_page` not called), and wiki write failure (verify memory still saved)

### 4. Validate Wiki Core
- **Task ID**: validate-wiki-core
- **Depends On**: build-wiki-core, build-wiki-lint, build-post-merge-integration
- **Assigned To**: wiki-core-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_wiki_writer.py tests/unit/test_wiki_index.py tests/unit/test_wiki_lint.py -v`
- Verify NDA isolation: attempt write to wrong project, confirm PermissionError logged and no file created
- Verify atomic write: confirm temp file cleaned up, final file is complete
- Report pass/fail

### 5. Validate Integration
- **Task ID**: validate-integration
- **Depends On**: validate-wiki-core
- **Assigned To**: wiki-integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_extraction.py -v`
- Write a test page via `tools.wiki.writer.write_page()`, wait 5s, verify `KnowledgeDocument` record exists and companion `Memory` record exists
- Verify `_index.md` entry present and `_log.md` entry appended
- Verify YAML frontmatter is well-formed: `python -c "import yaml; yaml.safe_load(open(path))"` returns without error
- Verify `logs/wiki_lint.log` is written after a manual `run_wiki_lint()` call
- Report pass/fail

### 6. Documentation
- **Task ID**: document-wiki
- **Depends On**: validate-integration
- **Assigned To**: wiki-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-wiki.md` covering: LLM Wiki pattern overview, write/lint/query data flows, page templates, NDA isolation enforcement, YAML scheduler registration, and `WIKI_WRITE_ENABLED` flag
- Update `docs/features/knowledge-document-integration.md` — add "Write Path" section referencing agent-wiki
- Update `docs/features/subconscious-memory.md` — add wiki/memory boundary clarification
- Add entry to `docs/features/README.md` index table
- Add `WIKI_WRITE_ENABLED=true  # Enable agent wiki page writing on post-merge` to `.env.example`

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-wiki
- **Assigned To**: wiki-integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/test_wiki_*.py tests/unit/test_memory_extraction.py -v`
- Lint: `python -m ruff check .`
- Format: `python -m ruff format --check .`
- Verify all success criteria checked
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_wiki_writer.py tests/unit/test_wiki_index.py tests/unit/test_wiki_lint.py tests/unit/test_memory_extraction.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Wiki write feature flag exists | `grep -r "WIKI_WRITE_ENABLED" config/settings.py` | output > 0 |
| Wiki writer module exists | `python -c "from tools.wiki.writer import write_page; print('ok')"` | output contains ok |
| Wiki lint callable exists | `python -c "from reflections.wiki_lint import run_wiki_lint; print('ok')"` | output contains ok |
| Slug sanitization enforced | `grep -n "re.sub" tools/wiki/writer.py` | output contains `[^a-z0-9` |
| No async I/O in WikiWriter | `grep -n "async def\|aiofiles" tools/wiki/writer.py` | no output |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-15 -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Adversary | Slug path traversal in temp file naming — slug containing `..` or `/` can escape vault directory | Task build-wiki-core must sanitize slug: `re.sub(r'[^a-z0-9\-_]', '-', slug.lower())` before any path construction | `pathlib.Path(vault_dir) / f".{slug}.md.tmp"` does NOT prevent traversal if slug contains `..` — Python's pathlib resolves `..` |
| CONCERN | Simplifier + Structural | MCP server layer (mcp_servers/ dir) does not exist; issue Recon Summary already stated "no MCP needed for v1" — plan contradicts its own recon | Remove wiki_write/wiki_query MCP tools from v1; scope to follow-on issue | mcp_servers/ directory and .mcp.json both absent; creating entire MCP infrastructure is multi-hour unresolved work |
| CONCERN | Skeptic | WikiWriter idempotent merge strategy (append to body, update frontmatter) not spiked — naive append will duplicate wikilinks and corrupt frontmatter | Spike the merge strategy: write test with two sequential writes to same slug before build | Idempotency check must use slug-based file existence, not title string matching (which requires scanning all files on every write) |
| CONCERN | Archaeologist | extract_post_merge_learning() is called inside asyncio.run() in .claude/hooks/hook_utils/memory_bridge.py::post_merge_extract() — WikiWriter must be synchronous only | Explicitly constrain WikiWriter to synchronous file I/O in the Technical Approach section | async WikiWriter inside asyncio.run() in a hook process raises RuntimeError: This event loop is already running |
| CONCERN | Operator | Orphaned temp files (.{slug}.md.tmp) accumulate if process is killed between write and rename — no cleanup specified | Add startup sweep in WikiWriter: remove *.md.tmp files older than 5 minutes from vault before each write | os.rename() is atomic within same filesystem — temp files orphan only on SIGKILL or power loss |
| CONCERN | User | No user-facing acceptance criteria — all success criteria are technical (tests pass, lint clean) | Add 2-3 user-facing criteria: e.g., wiki page appears in Obsidian within 30s of PR merge; lint report surfaces actionable finding | Without user criteria, build could ship technically-complete code that delivers no observable value to Valor |
| NIT | Operator | WikiLint has no log output or dashboard integration — operator cannot know if it ran or errored silently | WikiLint should write JSON result to logs/wiki_lint.log; daily-report reflection should surface summary if findings non-empty | N/A |
| NIT | Archaeologist | Companion Memory importance for wiki-written pages (3.0) should be confirmed against indexer's value for vault docs | Confirm tools/knowledge/indexer.py KNOWLEDGE_IMPORTANCE before setting wiki page companion Memory importance | Indexer uses KNOWLEDGE_IMPORTANCE = 3.0 — consistent, no conflict |
| NIT | Simplifier | WikiIndex (tools/wiki/index.py) may not justify a standalone module — two 10-line operations that WikiWriter could handle directly | Inline index/log update into WikiWriter unless WikiLint needs to call WikiIndex independently | Only extract if second consumer appears; WikiLint reads pages directly, not through index writer |
| NIT | User | WikiWriter auto-inserts wikilinks for entries in _index.md even if the target .md file doesn't exist yet — produces dead links in Obsidian | Only auto-inject wikilinks for titles with corresponding .md files, or suppress wikilink injection until vault has >50 pages | Dead links show as unresolved purple in Obsidian graph view |

---

## Open Questions

1. **Reflections YAML config location**: RESOLVED — `config/reflections.yaml`. Builder must add `wiki-lint` entry with `callable: "reflections.wiki_lint.run_wiki_lint"`, `interval: 604800` (weekly), `priority: low`, `execution_type: function`.

2. **MCP server target**: RESOLVED by critique — MCP exposure removed from v1 scope. The `mcp_servers/` directory does not exist; creating it is out of scope. Agents invoke wiki operations via `python -m tools.wiki.writer` CLI or direct file tools. MCP integration is a follow-on issue.

3. **Post-merge hook trigger**: RESOLVED — `.claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()` calls `asyncio.run(extract_post_merge_learning(...))`. WikiWriter MUST use synchronous file I/O only (no aiofiles, no async) to avoid RuntimeError inside asyncio.run().
