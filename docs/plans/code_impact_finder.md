x---
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

#### 1. Refactor into generic pipeline (`tools/impact_finder_core.py`)

The existing `doc_impact_finder.py` is 520 lines. Only three things are doc-specific:
- `DOC_PATTERNS` + `_discover_doc_files()` — which files to index
- `chunk_markdown()` — how to split files into chunks
- The reranking prompt in `_rerank_single_candidate()` — what question to ask Haiku

Everything else is generic: embedding providers, batching, cosine similarity, index load/save, the two-stage find pipeline, candidate grouping, fallback path. Extract all of that into `impact_finder_core.py` as a **configurable pipeline**.

```python
# The core provides a generic find() function:
def find_affected(
    change_summary: str,
    discover_files: Callable[[Path], list[Path]],   # what to index
    chunk_file: Callable[[str, str], list[dict]],    # how to chunk
    rerank_prompt: Callable[[str, dict], str],        # what to ask Haiku
    index_name: str,                                  # "doc_embeddings" or "code_embeddings"
    result_builder: Callable[[list], list[BaseModel]], # how to shape output
    top_n: int = 15,
    repo_root: Path | None = None,
) -> list[BaseModel]:
```

Each finder becomes a thin config file (~50-80 lines) that passes its specific callables to the core.

`doc_impact_finder.py` shrinks to: `DOC_PATTERNS`, `chunk_markdown()`, the doc reranking prompt, `AffectedDoc` model, and a `find_affected_docs()` that calls `find_affected()` with doc-specific config.

`code_impact_finder.py` provides: `CODE_PATTERNS`, code-aware chunking, the code reranking prompt, `AffectedCode` model, and a `find_affected_code()` that calls `find_affected()` with code-specific config.

**Shared in core (not duplicated):**
- `get_embedding_provider()` / `_embed_openai()` / `_embed_voyage()`
- `cosine_similarity()`
- `load_index()` / `save_index()` with content-hashed cache management
- `_rerank_candidates()` — parallel Haiku reranking (takes prompt builder as arg)
- `build_index()` — discover → chunk → diff against cache → embed new → save
- `_candidates_to_results()` — fallback grouping when Haiku is unavailable
- `chunk_markdown()` — used by both finders (docs are markdown; code finder indexes `.md` files too)
- Constants: `EMBEDDING_BATCH_SIZE`, `MIN_SIMILARITY_THRESHOLD`, `HAIKU_CONTENT_PREVIEW_CHARS`
- Self-healing guardrails (section 7)

#### 2. Code-aware chunking (in `code_impact_finder.py`)

The only new chunking logic needed — `chunk_markdown()` already lives in the core.

**Python files** — Use `ast` module (stdlib, no new deps):
- Each top-level function → one chunk
- Each class → **two levels of chunks**: one chunk for the entire class body (captures conceptual coupling), plus one chunk per method (captures specific behavioral dependencies). Duplicate hits on the same code are fine — a class-level hit says "this class is related" while a method-level hit says "this specific method touches the thing you're changing."
- Module-level code (imports, constants, assignments) → one "preamble" chunk
- Decorators included with their function/class

**Config files** (`.json`, `.yaml`, `.toml`):
- Small files (<100 lines) → single chunk
- Larger files → split on top-level keys

**Shell scripts** (`.sh`):
- Each function → one chunk
- Non-function code → one chunk

**Markdown/Skills** (`.md`):
- Reuse `chunk_markdown()` from core

**CLAUDE.md and SKILL.md**:
- High-value context files — indexed with priority

#### 3. Code file discovery (in `code_impact_finder.py`)

Patterns from repo root, excluding noise:
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

Estimated corpus: ~400 files, ~2500 chunks (higher than single-level due to dual class+method chunking).

#### 4. Code reranking prompt (in `code_impact_finder.py`)

The code finder indexes both code **and** docs in a single corpus. Docs are the highest level of truth in this codebase, so they must always be surfaced as context when planning changes. The reranking prompt handles both:

> Given a proposed change described as: "{change_summary}"
>
> Would this file be AFFECTED by or COUPLED TO this change? Consider:
> - Direct modifications needed
> - Behavioral dependencies (uses same abstractions, shares state)
> - Configuration coupling (reads same env vars, config keys)
> - Test coverage (tests that exercise affected paths)
> - Documentation that describes affected behavior and would need updating
>
> File: {file_path} — {section_name}
> ```
> {content_preview}
> ```
>
> Rate relevance 0.0-1.0. Respond with ONLY a JSON object: {"score": 0.X, "reason": "..."}

#### 5. Output model (in `code_impact_finder.py`)

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

#### 7. Self-maintaining guardrails (in `impact_finder_core.py`)

These guardrails live in the **shared core**, so both doc_impact_finder and code_impact_finder get identical health management for free. No finder-specific health code.

**Auto-heal on every run** (inside `build_index()`):
- Before querying, check index age and staleness. If >30% of chunks have changed content hashes, log a one-line note in output ("reindexing 847/2100 chunks") but proceed automatically.
- If the index file is missing or corrupt, rebuild from scratch silently. This is the expected path on a fresh clone.
- If the embedding model in the index doesn't match the current provider, discard and rebuild (model switch = full invalidation, unavoidable).

**Cost ceiling** (inside `build_index()`):
- If a reindex would embed >1000 chunks in one run, emit a warning in the output with estimated cost and proceed anyway. The warning is informational, not blocking — the tool should never hang waiting for confirmation when invoked by make-plan or update-docs.
- At current pricing (~$0.02/1M tokens), even a full 2000-chunk reindex costs <$0.05. The ceiling exists so future codebase growth doesn't silently 10x the cost.

**`--status` flag** (generic, in core):
- `python -m tools.impact_finder_core --status doc` / `--status code` prints a one-screen summary for the given index: index age, chunk count, embedding model, estimated staleness (% of files modified since last index), estimated reindex cost. No side effects.
- Both `python -m tools.doc_impact_finder --status` and `python -m tools.code_impact_finder --status` delegate to the core's status function with their respective index name.
- This is a diagnostic escape hatch, not something anyone should need to run routinely.

**No new infrastructure:** No database, no scheduler, no monitoring service. Each index is a JSON file. The guardrails are `if` statements inside the shared `build_index()` path.

## Rabbit Holes

- **Tree-sitter for parsing**: `ast` module is sufficient for Python. Tree-sitter adds a native dependency for marginal benefit on a Python-heavy repo.
- **Cross-repo analysis**: Only index the current repo. Multi-repo coupling is a separate problem.
- **Real-time incremental indexing**: Content hashing already handles cache invalidation. No need for file watchers or git hooks.
- **Embedding fine-tuning**: Off-the-shelf OpenAI/Voyage embeddings are good enough. Don't fine-tune.
- **Auto-populating plan sections**: The tool surfaces information; the planner decides what to include. Don't automate plan writing.
- **External monitoring for index health**: The guardrails are inline `if` statements, not a separate health-check system. Don't build a monitoring service for a JSON cache file.

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

- [ ] `tools/impact_finder_core.py` contains the full generic pipeline: embedding, indexing, reranking, guardrails
- [ ] `doc_impact_finder.py` is a thin wrapper (~50-80 lines): doc patterns, doc reranking prompt, `AffectedDoc` model
- [ ] `code_impact_finder.py` is a thin wrapper (~80-120 lines): code patterns, AST chunking, code reranking prompt, `AffectedCode` model
- [ ] Both finders share identical health management — guardrails are in core, not duplicated
- [ ] All 21 existing doc_impact_finder tests still pass after refactor
- [ ] Python chunking uses `ast` module for function/class-level granularity
- [ ] Running against "change session ID derivation" surfaces `bridge/telegram_bridge.py`, `agent/sdk_client.py`, and session-related code
- [ ] make-plan SKILL.md updated with Phase 1 impact analysis step
- [ ] Integration test: index repo, query with known change, verify relevant files returned
- [ ] Missing/corrupt index auto-rebuilds silently on next run (both finders)
- [ ] Model mismatch (provider change) triggers full rebuild without manual intervention (both finders)
- [ ] Large reindex (>1000 chunks) emits cost warning in output but does not block (both finders)
- [ ] `--status` flag works on both: `python -m tools.doc_impact_finder --status` and `python -m tools.code_impact_finder --status`
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
- Implement self-healing guardrails: auto-rebuild on missing/corrupt index, model mismatch detection, cost warning on large reindex
- Add `--status` CLI flag for diagnostic output
- Write tests for chunking, discovery, guardrails, and end-to-end pipeline

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
- `python -m tools.doc_impact_finder --status` — doc index health summary
- `python -m tools.code_impact_finder --status` — code index health summary
- `rm data/doc_embeddings.json && python -m tools.doc_impact_finder "test query"` — doc finder rebuilds silently
- `rm data/code_embeddings.json && python -m tools.code_impact_finder "test query"` — code finder rebuilds silently
- `grep -q "impact" .claude/skills/make-plan/SKILL.md` — make-plan references the tool
- `test -f docs/features/code-impact-finder.md` — feature doc exists
- `grep -q "code-impact-finder" docs/features/README.md` — indexed in README

## Open Questions

_All resolved._

1. ~~**Chunking granularity for large classes**~~ — **Resolved: both.** Every class gets a full class-level chunk AND per-method chunks. Duplicate hits on the same code are a feature, not a bug — class-level catches conceptual coupling, method-level catches specific behavioral dependencies. The planner benefits from both signals.

2. ~~**Should the code finder also surface docs?**~~ — **Resolved: yes (option a).** The code finder indexes docs alongside code in a single corpus, returning doc hits with `impact_type="docs"`. Docs are the highest level of truth and must always be in the planner's context. During make-plan, only the code finder runs — no need to invoke both finders separately.
