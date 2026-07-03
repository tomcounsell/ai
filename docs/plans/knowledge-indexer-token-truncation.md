---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1876
last_comment_id: 4877536958
---

# Knowledge Indexer: Token-Aware Embedding Truncation

## Problem

On every bridge start, the knowledge watcher's full scan logs non-fatal failures for dense vault docs:

```
KnowledgeDocument upsert failed (non-fatal): Embedding provider failed for embedding:
Error code: 400 - Invalid 'input[0]': maximum input length is 8192 tokens.
```

The failing docs never get indexed. They retry on every scan, forever, and each attempt burns an OpenAI embedding call that is guaranteed to 400.

**Current behavior:**
`models/knowledge_document.py::safe_upsert()` truncates content with `content = content[:30000]` before assigning it to the `EmbeddingField` source. That char budget assumes ~3.66 chars/token. Dense content (tables, meeting transcripts, converted xlsx/pdf sidecars) packs more tokens per char, so the first 30,000 chars still tokenize past 8,192. Reproduced against `~/work-vault`: **7 of 534 docs exceed 8,192 tokens after `[:30000]`** (worst: `AI Valor Engels System/daily-logs/2026-05-06.md` at 10,016 tokens). The 2 that fail per scan are the recently-changed ones; the other 5 are shielded by the content-hash skip until they next change.

**Desired outcome:**
The document-level embedding source is truncated by **token count** (≤8,000 tokens, cl100k_base) before it reaches the embedding provider. Zero `400 - maximum input length` errors after a bridge restart + full scan. All 7 oversized docs index successfully. A warning is logged whenever truncation actually drops content.

## Freshness Check

**Baseline commit:** `fe1fe1358b50da4040ccc23ea16fb19f07303757`
**Issue filed at:** 2026-07-03T04:57:39Z (same day as planning)
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/knowledge_document.py:76` — issue claims `content = content[:30000]` — still holds (exact line).
- `tools/impact_finder_core.py:132` — sibling call site `texts = [t[:30000] for t in texts]` — still holds (surfaced during recon, not in issue body).

**Cited sibling issues/PRs re-checked:**
- #859 — CLOSED 2026-04-09. Resolution: commit `56c755a4` added the char-based `[:30000]` truncation at both call sites. That is the incomplete fix this bug corrects.
- #861 — "Chunked document retrieval" — merged; introduced `DocumentChunk` + `tools/knowledge/chunking.py` (1500-token chunks). Relevant: chunks are already token-safe.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since=<createdAt> -- models/knowledge_document.py tools/knowledge/` is empty.

**Bug reproduced against current main:** yes — tokenizer sweep confirms exactly 7 docs > 8,192 tokens after `[:30000]`, matching the issue.

**Active plans in `docs/plans/` overlapping this area:** `embedding_token_truncation.md` (status Planning, tracks the already-**closed** #859, created 2026-04-09). It predates chunking (#861) and the char-truncation fix, and its tracking issue is closed — it is orphaned cruft, not a live overlap. This plan supersedes it; a task below removes it.

## Prior Art

- **Issue/PR #859 / commit `56c755a4`**: "fix: truncate embedding input to 30K chars to prevent 8192 token limit errors". Added `content[:30000]` in `safe_upsert()` and `[t[:30000]]` in `_embed_openai()`. Closed the issue but only addressed the symptom at a char granularity — the root cause (token count, not char count) survives for dense docs. This is the fix being corrected.
- **Issue #861**: "Chunked document retrieval: per-chunk embeddings for long documents". Merged. Introduced `DocumentChunk` and `tools/knowledge/chunking.py`, which already truncates/splits by **token** count (`_split_by_tokens`, 1,500-token chunks). Its `_get_encoding()` (cl100k_base) and `_count_tokens()` are the reusable primitives this fix builds on.
- No other issues or merged PRs address the 8,192-token limit.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| commit `56c755a4` (#859) | Truncated embedding input to `[:30000]` **characters** at both call sites | Char count is a poor proxy for token count. The 30k-char budget assumes ≥3.66 chars/token; dense content (tables, transcripts, converted sidecars) tokenizes denser, so 30k chars still exceeds 8,192 tokens. Fixed the average doc, missed the dense tail. |

**Root cause pattern:** the limit being defended is measured in **tokens**, but the guard was written in **characters**. Any char-based cap is wrong for content whose char/token ratio varies. The correct guard counts tokens with the same tokenizer the embedding model uses (cl100k_base for `text-embedding-3-small`).

## Data Flow

**Path 1: Knowledge document indexing (the reported failure)**
1. **Entry point**: `KnowledgeWatcher` full scan (bridge startup) or file change → `index_file()` in `tools/knowledge/indexer.py`.
2. `index_file()` reads `raw_content`, resolves scope, calls `KnowledgeDocument.safe_upsert()`.
3. **`safe_upsert()`** (`models/knowledge_document.py:76`): sets `content = content[:30000]`, assigns to `doc.content` (the `EmbeddingField` source), calls `doc.save()`.
4. **popoto `EmbeddingField.on_save()`**: reads the source, calls the configured provider's `embed()`.
5. **Provider**: at the bridge this is popoto's built-in **OpenAI** `text-embedding-3-small` (our `agent/embedding_provider.py` only sets a *local* Ollama provider and returns None when Ollama is unreachable — the bridge case — so popoto falls back to OpenAI). OpenAI 400s when the input > 8,192 tokens. The 400 propagates up and is swallowed by `safe_upsert()`'s broad `except Exception` → "non-fatal" log, doc unindexed.
6. **Chunks** (unaffected): `index_file()` separately calls `_sync_chunks(doc, raw_content, ...)` with the **full raw file**, and `chunk_document()` bounds each chunk to 1,500 tokens. Chunks never exceed the limit, and they retain full-doc coverage independent of the doc-level truncation.

**Path 2: Impact finder (identical latent defect)**
1. `find_affected()` → `build_index()` chunks files, collects `texts`.
2. **`_embed_openai()`** (`tools/impact_finder_core.py:132`): `texts = [t[:30000] for t in texts]` then `client.embeddings.create(...)`. Same char-based guard, same defect. Rarely hit (texts are pre-chunked) but the same root-cause line.

## Architectural Impact

- **New dependencies**: none. `tiktoken` 0.12.0 is already installed and already a hard import of `tools/knowledge/chunking.py`.
- **Interface changes**: adds one public helper `truncate_to_tokens(text, max_tokens)` to `tools/knowledge/chunking.py`. No signature changes to `safe_upsert` or `_embed_openai`.
- **Coupling**: reduces it. The token-limit concern is centralized in one helper instead of duplicated as a magic `30000` at two call sites.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — revert the helper and the two call-site edits.
- **Provider-agnostic by design**: truncation happens at the source assignment, before content reaches whichever provider is active (OpenAI at the bridge, Ollama locally). Both nomic-embed-text and text-embedding-3-small accept up to 8,192 tokens, so an 8,000-token cap is safe for both; counting with cl100k_base is exact for OpenAI and a safe over-estimate for Ollama.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

A focused root-cause bug fix: one helper, two one-line call-site swaps, targeted tests, one doc update.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `tiktoken` installed | `.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"` | Token counting for truncation |

## Solution

### Key Elements

- **`truncate_to_tokens(text, max_tokens=8000)`** (new, in `tools/knowledge/chunking.py`): encodes with the cached cl100k_base encoding, and if the token count exceeds `max_tokens`, decodes the first `max_tokens` tokens back to a string and returns it (logging a warning with the before/after token counts). Returns `text` unchanged when already within budget. On tiktoken failure, falls back to a conservative char cap (`text[:max_tokens * 4]`) so the path never crashes — mirrors the existing `_count_tokens` fallback in the same module.
- **`safe_upsert()`** (`models/knowledge_document.py`): replace `content = content[:30000]` with `content = truncate_to_tokens(content, 8000)`.
- **`_embed_openai()`** (`tools/impact_finder_core.py`): replace `texts = [t[:30000] for t in texts]` with `texts = [truncate_to_tokens(t, 8000) for t in texts]` — eliminates the identical root-cause line at the sibling call site.
- **Remove** the orphaned `docs/plans/embedding_token_truncation.md` (tracks the closed #859, superseded by this plan).

### Flow

Bridge full scan → `index_file()` reads dense doc → `safe_upsert()` truncates to ≤8,000 tokens → `EmbeddingField.on_save()` → OpenAI `embed()` accepts input → doc indexed, no 400. (Chunks continue to build from the full raw file, unchanged.)

### Technical Approach

- **8,000-token cap** (not 8,192): 192-token headroom absorbs any off-by-a-few in provider-side tokenization and the BOS/EOS accounting some models add. cl100k_base is exact for `text-embedding-3-small`, so this is a comfortable, not tight, margin.
- **Helper home**: `tools/knowledge/chunking.py` already owns and caches the cl100k_base encoding (`_get_encoding()`), so adding `truncate_to_tokens` there avoids a second encoding init and keeps all token logic in one module. Both call sites import it (`from tools.knowledge.chunking import truncate_to_tokens`).
- **No binary-search-halve retry.** The issue floats a halve-on-400 fallback "for environments without tiktoken." tiktoken is a hard dependency here (chunking.py imports it unconditionally), so the no-tiktoken path is effectively dead; the char-cap fallback inside the helper covers the theoretical case without a retry loop against a provider we do not own. `safe_upsert()`'s existing broad `except` remains as the final non-fatal backstop, but the goal is zero 400s, so it should never fire on this path.
- **Warning only on actual truncation**: log at WARNING with `file`/token counts only when content is dropped, so the log signals real oversized docs rather than every save.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `safe_upsert()` has a broad `except Exception` that logs "non-fatal". A test asserts that after the fix, a dense >8,192-token doc does **not** hit that handler (i.e., `safe_upsert` returns a non-None doc and no "non-fatal" warning is logged). This proves the truncation prevents the swallowed 400.
- [ ] `truncate_to_tokens` tiktoken-failure branch: test with tiktoken patched to raise, assert it falls back to the char cap and returns a string (no exception).

### Empty/Invalid Input Handling
- [ ] `truncate_to_tokens("")` returns `""`; `truncate_to_tokens("   ")` returns the input unchanged (under budget); `truncate_to_tokens(None)` — decide and test: return `None`/`""` without raising (guard at the top).
- [ ] Under-budget text is returned byte-for-byte unchanged (no accidental re-encoding artifacts).

### Error State Rendering
- [ ] No user-visible surface. The observable "error state" is the non-fatal log line; the test asserts its **absence** for the previously-failing docs, and its **presence** (WARNING) when truncation actually drops content.

## Test Impact

- [ ] `tests/unit/test_chunking.py` — UPDATE: add `TestTruncateToTokens` cases (over-budget truncates to ≤max, under-budget unchanged, empty/None, tiktoken-failure char fallback, warning-on-drop). Purely additive to an existing file.
- [ ] `tests/unit/test_knowledge_document.py` — UPDATE: add a case that a synthetic dense >8,000-token doc is stored with a token count ≤8,000 after `safe_upsert` (additive; existing `safe_upsert` tests unchanged).
- [ ] `tests/unit/test_code_impact_finder.py` — UPDATE: add a case asserting `_embed_openai` truncates an oversized text by token count before the provider call (additive; existing `_embed_openai` patch-based tests unchanged).

No existing test asserts the `30000`-char behavior, so nothing needs DELETE/REPLACE — the char constant is not pinned by any current test, making this a clean swap.

## Rabbit Holes

- **Embedding-averaging / multi-vector doc representation.** Splitting oversized docs into chunks, embedding each, and averaging to preserve full semantic content is real work and already partially served by `DocumentChunk`. Out of scope — truncation of the coarse doc-level vector is sufficient; chunks already provide fine-grained coverage.
- **Forking popoto's `EmbeddingField`.** The "central" fix lives in vendored `.venv/.../popoto/fields/embedding_field.py`, which we do not own. Do not edit it. Truncating at the source is provider- and library-agnostic and stays in our code.
- **Making the token cap provider-aware / configurable.** A single 8,000-token constant is correct for both providers in play. Do not build a per-provider limit registry.
- **Chasing the impact-finder path as if it were failing in production.** It is a defensive twin fix riding on the shared helper, not a reported failure. One-line swap; no deep investigation of impact-finder chunking.

## Risks

### Risk 1: cl100k_base count diverges from the provider's actual tokenization
**Impact:** A doc truncated to 8,000 cl100k tokens could still exceed the provider's limit, re-introducing 400s.
**Mitigation:** `text-embedding-3-small` uses cl100k_base exactly, so counting is exact, and the 192-token margin absorbs any framing tokens. For Ollama/nomic (8,192 context), cl100k is a conservative over-count. Verification step greps the bridge log for zero `maximum input length` entries after a real full scan.

### Risk 2: Truncating `doc.content` shortens companion memories for the 7 dense docs
**Impact:** `_create_companion_memories(..., doc.content)` builds heading summaries from the stored (now token-truncated) content, so very long docs get summaries from ≤8,000 tokens instead of ≤30,000 chars.
**Mitigation:** 8,000 tokens ≈ 30k–40k chars for normal prose, so for most docs this is a no-op or a slight increase in retained content vs. the old char cap. Companion memories are coarse heading summaries anyway; chunks (built from the full raw file) remain the fine-grained retrieval surface. Acceptable and noted; not a regression worth new machinery.

## Race Conditions

No race conditions identified — `safe_upsert` and `_embed_openai` are synchronous; the truncation is a pure in-memory transform of a local string before any save or network call. No shared mutable state, no cross-process ordering.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1876] Nothing is deferred to another issue. This plan fully resolves #1876.
- Embedding-averaging / multi-vector document representation — see Rabbit Holes (a genuinely larger project, not a same-appetite deferral). Filing a separate issue is unwarranted unless retrieval quality on long docs is later measured as insufficient.

Nothing else deferred — the helper, both call-site fixes, the stale-plan removal, tests, and docs are all in scope for this plan.

## Update System

No update system changes required. The fix touches only application code (`models/`, `tools/`) with no new dependency (`tiktoken` already installed and propagated), no config file, and no Popoto schema change — `KnowledgeDocument`/`DocumentChunk` field definitions are untouched, so no migration in `scripts/update/migrations.py` is needed.

## Agent Integration

No agent integration required. This is a bridge-internal indexing fix. No new CLI entry point, no MCP tool, no `.mcp.json` change, and the bridge already calls this path via the knowledge watcher. The agent reaches indexed docs through existing memory/knowledge search tools, whose interface is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/knowledge-document-integration.md` — document that the document-level embedding source is truncated by token count (≤8,000 cl100k_base tokens) via `truncate_to_tokens`, why (8,192 provider limit), and that chunks are independently token-bounded and built from the full raw file.

### External Documentation Site
- [ ] Not applicable — this repo has no separate docs site.

### Inline Documentation
- [ ] Docstring on `truncate_to_tokens` (args, return, fallback behavior).
- [ ] Update the `content = content[:30000]` comment in `safe_upsert` to reflect token-aware truncation, and the module docstring line in `models/knowledge_document.py` if it references the char cap.

## Success Criteria

- [ ] `truncate_to_tokens(text, max_tokens=8000)` exists in `tools/knowledge/chunking.py`, returns ≤`max_tokens` tokens for over-budget input, returns input unchanged when under budget, handles empty/None, and falls back to a char cap when tiktoken raises.
- [ ] `models/knowledge_document.py::safe_upsert` uses `truncate_to_tokens` (no `[:30000]` char cap remains in that file).
- [ ] `tools/impact_finder_core.py::_embed_openai` uses `truncate_to_tokens` (no `[:30000]` char cap remains in that file).
- [ ] The 7 oversized vault docs (reproducible via the tokenizer sweep) each index successfully; a scripted check embeds each after truncation with no 400.
- [ ] `grep "maximum input length" logs/bridge.log` shows no new entries after a bridge restart + full scan.
- [ ] `docs/plans/embedding_token_truncation.md` removed.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (truncation)**
  - Name: `trunc-builder`
  - Role: Add `truncate_to_tokens` helper, swap both call sites, update inline docs, remove stale plan.
  - Agent Type: builder
  - Resume: true

- **Test-engineer (truncation)**
  - Name: `trunc-tester`
  - Role: Add token-truncation tests across the three test files; add the oversized-doc index proof.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `trunc-validator`
  - Role: Verify success criteria, run the verification table, confirm zero 400s on a real scan.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `trunc-docs`
  - Role: Update `docs/features/knowledge-document-integration.md`.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add the shared helper and swap call sites
- **Task ID**: build-truncation
- **Depends On**: none
- **Validates**: tests/unit/test_chunking.py, tests/unit/test_knowledge_document.py, tests/unit/test_code_impact_finder.py
- **Assigned To**: trunc-builder
- **Agent Type**: builder
- **Parallel**: false
- **Domain**: data (Popoto/EmbeddingField source assignment)
- Add `truncate_to_tokens(text, max_tokens=8000)` to `tools/knowledge/chunking.py` reusing `_get_encoding()`; log WARNING only when content is actually dropped; char-cap fallback (`text[:max_tokens*4]`) on tiktoken failure; guard empty/None.
- In `models/knowledge_document.py::safe_upsert`, replace `content = content[:30000]` with `content = truncate_to_tokens(content, 8000)`; import the helper; update the adjacent comment and the module docstring line referencing the char cap.
- In `tools/impact_finder_core.py::_embed_openai`, replace `texts = [t[:30000] for t in texts]` with the `truncate_to_tokens` list comprehension; import the helper.
- Delete `docs/plans/embedding_token_truncation.md` (orphaned, tracks closed #859).

### 2. Add tests
- **Task ID**: build-tests
- **Depends On**: build-truncation
- **Assigned To**: trunc-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestTruncateToTokens` to `tests/unit/test_chunking.py`: over-budget truncates to ≤max tokens, under-budget unchanged, empty/`None`, tiktoken-failure char fallback, warning emitted on drop.
- Add to `tests/unit/test_knowledge_document.py`: a synthetic dense >8,000-token doc yields stored content ≤8,000 tokens and a non-None doc with no "non-fatal" warning.
- Add to `tests/unit/test_code_impact_finder.py`: `_embed_openai` truncates an oversized text by token count before the provider call.
- Add an oversized-doc index proof (a small script or test) that runs `truncate_to_tokens` over the 7 reproducible docs and asserts each is ≤8,000 tokens.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-truncation
- **Assigned To**: trunc-docs
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/knowledge-document-integration.md` per the Documentation section.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: trunc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm all Success Criteria; confirm zero `maximum input length` log entries after a full scan.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `.venv/bin/pytest tests/unit/test_chunking.py tests/unit/test_knowledge_document.py tests/unit/test_code_impact_finder.py -q` | exit code 0 |
| Helper exists | `grep -c "def truncate_to_tokens" tools/knowledge/chunking.py` | output contains 1 |
| safe_upsert uses helper | `grep -c "truncate_to_tokens" models/knowledge_document.py` | output > 0 |
| No char cap in knowledge_document | `grep -c "\[:30000\]" models/knowledge_document.py` | match count == 0 |
| No char cap in impact_finder | `grep -c "\[:30000\]" tools/impact_finder_core.py` | match count == 0 |
| Stale plan removed | `test ! -f docs/plans/embedding_token_truncation.md` | exit code 0 |
| Oversized docs fit after truncation | `.venv/bin/python -c "import tiktoken,glob,os; from tools.knowledge.chunking import truncate_to_tokens; enc=tiktoken.get_encoding('cl100k_base'); v=os.path.expanduser('~/work-vault'); bad=[p for p in glob.glob(v+'/**/*.md',recursive=True) if len(enc.encode(truncate_to_tokens(open(p,encoding='utf-8',errors='replace').read(),8000)))>8192]; print(len(bad))"` | output contains 0 |
| Format clean | `python -m ruff format --check tools/knowledge/chunking.py models/knowledge_document.py tools/impact_finder_core.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

## Open Questions

1. Scope of the impact-finder twin fix: include `_embed_openai` in this PR (same root-cause line, one-line swap on the shared helper — my default), or keep the PR strictly to the reported knowledge-indexer path and leave impact-finder as-is? I recommend including it — leaving the identical defective line is the exact symptom-vs-cause anti-pattern the fix corrects.
2. Removing `docs/plans/embedding_token_truncation.md`: delete as orphaned cruft (my default, since it tracks the closed #859 and is superseded), or leave it in place for historical reference?
