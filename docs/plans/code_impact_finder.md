---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-02-15
tracking: https://github.com/tomcounsell/ai/issues/119
---

# Code Impact Finder for /make-plan Blast Radius Analysis

## Problem

During `/make-plan` Phase 1, the planner explores the codebase with Glob/Grep to understand what a proposed change will touch. This misses non-obvious coupling — changing session scoping won't grep-match `job_queue.py` even though it depends on session IDs. The planner ends up with blind spots in the Solution, Risks, and Rabbit Holes sections.

**Current behavior:** Planner uses keyword-based search. Misses conceptual coupling between modules that share no vocabulary but depend on the same abstractions.

**Desired outcome:** Semantic search surfaces all code/config/docs coupled to a proposed change, feeding directly into the plan's Solution, Risks, Rabbit Holes, and Documentation sections.

## Appetite

**Size:** Medium — Solo dev + PM. One check-in to align on chunking strategy and make-plan integration, one review round.

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on chunking granularity and integration depth)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #110 merged (doc_impact_finder) | `python -c "from tools.doc_impact_finder import find_affected_docs; print('OK')"` | Reusable embedding/reranking pipeline |
| OpenAI or Voyage API key | `python -c "import os; assert os.getenv('OPENAI_API_KEY') or os.getenv('VOYAGE_API_KEY')"` | Embedding generation |
| numpy installed | `python -c "import numpy; print('OK')"` | Cosine similarity |

## Solution

### Key Elements

- **Shared embedding core**: Extract the embedding/similarity/reranking pipeline from `doc_impact_finder.py` into a shared module. Both finders reuse the same index-embed-recall-rerank pattern.
- **Code-aware chunker**: Use Python `ast` module to split `.py` files into function/class-level chunks. Non-Python files (JSON, YAML, shell, Markdown) use simpler heuristics.
- **Code impact finder**: New tool at `tools/code_impact_finder.py` that indexes the full codebase (not just docs) and answers "what code is coupled to this change?"
- **make-plan integration**: During Phase 1, run the code impact finder against the problem statement to surface affected files before writing the plan.

### Flow

**User invokes /make-plan** → Planner reads issue → **Code impact finder runs against problem statement** → Returns ranked list of coupled files with reasons → Planner uses results to populate Solution (files to modify), Risks (unexpected dependencies), Rabbit Holes (tangential coupling), Documentation (affected docs)

### Technical Approach

#### 1. Extract shared pipeline (`tools/impact_finder_core.py`)

Move reusable components out of `doc_impact_finder.py`:
- `get_embedding_provider()` → provider detection
- `_embed_openai()` / `_embed_voyage()` → batched embedding with `EMBEDDING_BATCH_SIZE`
- `cosine_similarity()` → numpy dot product
- `_rerank_single_candidate()` → parallel Haiku reranking
- `load_index()` / `save_index()` → content-hashed index management
- Constants: `EMBEDDING_BATCH_SIZE`, `MIN_SIMILARITY_THRESHOLD`, `HAIKU_CONTENT_PREVIEW_CHARS`

`doc_impact_finder.py` becomes a thin wrapper: doc-specific chunking + file discovery + the shared core.

#### 2. Code-aware chunking (`tools/code_impact_finder.py`)

**Python files** — Use `ast` module (stdlib, no new deps):
- Each top-level function → one chunk
- Each class → one chunk (entire class body)
- Module-level code (imports, constants, assignments) → one "preamble" chunk
- Decorators included with their function/class

**Config files** (`.json`, `.yaml`, `.toml`):
- Small files (<100 lines) → single chunk
- Larger files → split on top-level keys

**Shell scripts** (`.sh`):
- Each function → one chunk
- Non-function code → one chunk

**Markdown/Skills** (`.md`):
- Reuse `chunk_markdown()` from the shared core (already splits on `##`)

**CLAUDE.md and SKILL.md**:
- These are high-value context files — index them with priority

#### 3. File discovery

Index these patterns from repo root, excluding noise:
```
**/*.py          (exclude .venv/, __pycache__/, .worktrees/)
**/*.md          (exclude .venv/, node_modules/)
*.json           (only config/*.json, .mcp.json, .claude/*.json, tools/*/manifest.json)
*.sh             (scripts/*.sh)
*.toml           (pyproject.toml)
.claude/skills/*/SKILL.md
.claude/commands/*.md
.claude/agents/*.md
```

Skip: `agents/*/state.json`, `data/`, `logs/`, `.git/`, `.venv/`, `generated_images/`

Estimated corpus: ~400 files, ~2000 chunks.

#### 4. Reranking prompt

Instead of doc_impact_finder's "does this doc need updating?", use:

> Given a proposed change described as: "{change_summary}"
>
> Would this code be AFFECTED by or COUPLED TO this change? Consider:
> - Direct modifications needed
> - Behavioral dependencies (uses same abstractions, shares state)
> - Configuration coupling (reads same env vars, config keys)
> - Test coverage (tests that exercise affected paths)
>
> Code: {file_path} — {section_name}
> ```
> {content_preview}
> ```
>
> Rate relevance 0.0-1.0. Respond with ONLY a JSON object: {"score": 0.X, "reason": "..."}

#### 5. Output model

```python
class AffectedCode(BaseModel):
    path: str           # bridge/telegram_bridge.py
    section: str        # "def handle_message"
    relevance: float    # 0.0 - 1.0
    impact_type: str    # "modify" | "dependency" | "test" | "config" | "docs"
    reason: str         # "Reads session_id which is being restructured"
```

#### 6. make-plan integration

In `.claude/skills/make-plan/SKILL.md`, add to Phase 1 after "Understand the request":

```
**Impact analysis** (if code_impact_finder is available):
Run `tools/code_impact_finder.py` with the problem statement.
Use results to inform:
- Solution section: files that need modification (impact_type="modify")
- Risks section: unexpected dependencies (impact_type="dependency")
- Rabbit Holes section: tangentially coupled code that's tempting but out of scope
- Documentation section: affected docs (impact_type="docs")
```

The integration is advisory — the planner uses the output as input, not as an automated rewrite.

## Rabbit Holes

- **Tree-sitter for parsing**: `ast` module is sufficient for Python. Tree-sitter adds a native dependency for marginal benefit on a Python-heavy repo.
- **Cross-repo analysis**: Only index the current repo. Multi-repo coupling is a separate problem.
- **Real-time incremental indexing**: Content hashing already handles cache invalidation. No need for file watchers or git hooks.
- **Embedding fine-tuning**: Off-the-shelf OpenAI/Voyage embeddings are good enough. Don't fine-tune.
- **Auto-populating plan sections**: The tool surfaces information; the planner decides what to include. Don't automate plan writing.

## Risks

### Risk 1: Chunking quality for non-Python files
**Impact:** Poor chunks → irrelevant or missed results for configs, skills, shell scripts
**Mitigation:** Start with Python (ast) + Markdown (heading-split) — these cover 90% of the codebase. JSON/shell/YAML get simple chunking. Iterate later.

### Risk 2: Embedding cost for large corpus
**Impact:** ~2000 chunks at ~500 tokens each = ~1M tokens per full index. OpenAI text-embedding-3-small: ~$0.02 per full reindex.
**Mitigation:** Content hashing means only changed files get re-embedded. Typical reindex touches <50 chunks. Cost is negligible.

### Risk 3: Refactoring doc_impact_finder breaks PR #110
**Impact:** Extracting shared code changes the module that PR #110 introduces.
**Mitigation:** PR #110 must be merged first. The refactor is a follow-up commit that preserves all existing tests and public API. Run the existing 21 tests after extraction.

## No-Gos (Out of Scope)

- Modifying the `/update-docs` skill (it continues using `doc_impact_finder` as-is, just backed by shared core)
- Adding new embedding providers beyond OpenAI/Voyage
- Building a UI or dashboard for impact results
- Auto-generating plan content from impact results
- Cross-repository impact analysis
- Language-specific parsing beyond Python (no tree-sitter)

## Update System

No update system changes required — this is a new tool in `tools/` with no new system dependencies. `numpy` and `openai` are already in `pyproject.toml` from PR #110.

## Agent Integration

- **No new MCP server needed** — the code impact finder is invoked by the make-plan skill within Claude Code, not as an external tool
- The make-plan SKILL.md will be updated to call the finder during Phase 1
- The finder runs as a Python module call: `python -m tools.code_impact_finder "change summary"` or imported directly
- **No bridge changes** — this is a skill-internal tool, not a Telegram-facing capability
- Integration test: verify the finder returns results when given a known change summary against the indexed codebase

## Documentation

- [ ] Create `docs/features/code-impact-finder.md` describing the tool, its architecture, and how it integrates with make-plan
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/tools-reference.md` with code_impact_finder entry
- [ ] Add inline docstrings to all public functions in new modules

## Success Criteria

- [ ] `tools/impact_finder_core.py` extracted with shared pipeline; `doc_impact_finder.py` refactored to use it
- [ ] All 21 existing doc_impact_finder tests still pass
- [ ] `tools/code_impact_finder.py` indexes Python, Markdown, config, shell, and SKILL files
- [ ] Python chunking uses `ast` module for function/class-level granularity
- [ ] Running against "change session ID derivation" surfaces `bridge/telegram_bridge.py`, `agent/sdk_client.py`, and session-related code
- [ ] make-plan SKILL.md updated with Phase 1 impact analysis step
- [ ] Integration test: index repo, query with known change, verify relevant files returned
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (shared core)**
  - Name: core-extractor
  - Role: Extract shared embedding/reranking pipeline from doc_impact_finder into impact_finder_core.py
  - Agent Type: builder
  - Resume: true

- **Validator (shared core)**
  - Name: core-validator
  - Role: Verify doc_impact_finder still works after extraction, all 21 tests pass
  - Agent Type: validator
  - Resume: true

- **Builder (code finder)**
  - Name: code-finder-builder
  - Role: Implement code_impact_finder.py with ast-based chunking and code-specific reranking
  - Agent Type: builder
  - Resume: true

- **Builder (make-plan integration)**
  - Name: plan-integrator
  - Role: Update make-plan SKILL.md to invoke code impact finder in Phase 1
  - Agent Type: builder
  - Resume: true

- **Validator (end-to-end)**
  - Name: e2e-validator
  - Role: Verify full pipeline works: index → query → results feed into plan context
  - Agent Type: validator
  - Resume: true

- **Builder (docs)**
  - Name: docs-writer
  - Role: Create feature documentation and update references
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Extract shared pipeline
- **Task ID**: build-core
- **Depends On**: none (but PR #110 must be merged first)
- **Assigned To**: core-extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/impact_finder_core.py` with: embedding providers, batched embed, cosine similarity, reranking, index management
- Refactor `tools/doc_impact_finder.py` to import from core instead of defining inline
- Preserve all public APIs and behavior

### 2. Validate shared core extraction
- **Task ID**: validate-core
- **Depends On**: build-core
- **Assigned To**: core-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all 21 existing doc_impact_finder tests
- Verify no import errors
- Verify `impact_finder_core.py` has no doc-specific logic

### 3. Build code impact finder
- **Task ID**: build-code-finder
- **Depends On**: validate-core
- **Assigned To**: code-finder-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `tools/code_impact_finder.py` with ast-based Python chunking
- Implement file discovery for Python, Markdown, config, shell, SKILL files
- Implement code-specific reranking prompt
- Add `AffectedCode` model with impact_type classification
- Write tests for chunking, discovery, and end-to-end pipeline

### 4. Integrate with make-plan
- **Task ID**: build-integration
- **Depends On**: build-code-finder
- **Assigned To**: plan-integrator
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills/make-plan/SKILL.md` Phase 1 with impact analysis step
- Add CLI entry point: `python -m tools.code_impact_finder "change summary"`
- Document expected output format for planner consumption

### 5. End-to-end validation
- **Task ID**: validate-e2e
- **Depends On**: build-integration
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Index the repo, query with "change session ID derivation"
- Verify session-related files appear in results
- Verify make-plan SKILL.md references the tool correctly
- Run all tests (doc_impact_finder + code_impact_finder)

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-e2e
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/code-impact-finder.md`
- Add entry to `docs/features/README.md` index
- Update `docs/tools-reference.md`

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify all success criteria met
- Verify documentation exists and is indexed
- Generate final report

## Validation Commands

- `python -m pytest tests/test_doc_impact_finder.py -v` — existing tests still pass after core extraction
- `python -m pytest tests/test_code_impact_finder.py -v` — new code finder tests pass
- `python -m tools.code_impact_finder "change session ID derivation"` — returns relevant files
- `grep -q "impact" .claude/skills/make-plan/SKILL.md` — make-plan references the tool
- `test -f docs/features/code-impact-finder.md` — feature doc exists
- `grep -q "code-impact-finder" docs/features/README.md` — indexed in README

## Open Questions

1. **Chunking granularity for large classes**: Should a 200-line class be one chunk, or should it be split into methods? One chunk preserves class context but may be too large for embedding quality. Methods lose class context but are more focused. Leaning toward: one chunk per class (with method-level sub-chunks only if class exceeds 100 lines).

2. **Should the code finder also surface docs?** The issue mentions surfacing docs for the Documentation section. We could either (a) have the code finder index docs too and return them with `impact_type="docs"`, or (b) keep the two finders separate and run both during make-plan. Leaning toward (a) — single index, single query, simpler integration.
