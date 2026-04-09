---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/859
last_comment_id:
---

# Embedding Token Truncation

## Problem

The OpenAI `text-embedding-3-small` model has a hard limit of 8,192 input tokens per text string. Neither of the two call sites in this project truncates text before sending it to the embeddings API, causing `BadRequestError` (HTTP 400) when long content is embedded. Sentry issue VALOR-2 recorded 42 occurrences over 14 days.

**Current behavior:**
When a document or text chunk exceeds 8,192 tokens, the OpenAI API returns:
```
BadRequestError: Error code: 400 - {'error': {'message': "Invalid 'input[0]': maximum input length is 8192 tokens.", 'type': 'invalid_request_error'}}
```
This crashes the embedding operation. In `_embed_openai()` the exception propagates through the impact finder. In `KnowledgeDocument.save()` the error is caught by the broad `except Exception` in `safe_upsert()`, silently failing the indexing operation.

**Desired outcome:**
All text is truncated to fit within the 8,192-token limit before being sent to the OpenAI embeddings API. Zero `BadRequestError` 400s from token limit violations. A warning is logged whenever truncation occurs.

## Prior Art

No prior issues found related to embedding token truncation or the 8,192 limit. No merged PRs address this problem.

## Data Flow

Two independent paths converge on the same OpenAI embeddings API:

**Path 1: Impact Finder (code/doc analysis)**
1. **Entry point**: `find_affected()` in `tools/impact_finder_core.py` receives a change summary
2. **`build_index()`**: Discovers files, chunks them via `chunk_file()`, collects `content` strings
3. **`_embed_openai()`** (line 123): Receives `texts: list[str]`, batches by count (100), sends to `client.embeddings.create()` -- **no per-item token check**
4. **Output**: Embeddings stored in JSON index under `data/`

**Path 2: Knowledge Document (work-vault indexing)**
1. **Entry point**: File change detected by `KnowledgeWatcher` (bridge startup) or `full_scan()`
2. **`index_file()`** in `tools/knowledge/indexer.py`: Reads file content, calls `KnowledgeDocument.safe_upsert()`
3. **`safe_upsert()`** in `models/knowledge_document.py`: Sets `doc.content = content` then calls `doc.save()`
4. **Popoto `EmbeddingField.on_save()`**: Auto-triggers `OpenAIProvider.embed()` which calls `client.embeddings.create()` -- **no length guard**
5. **Output**: Embedding vector stored in Redis via Popoto

Both paths fail identically when any single text exceeds 8,192 tokens.

## Architectural Impact

- **New dependencies**: `tiktoken` -- already an indirect dependency of the `openai` package, so no new install required. Adding an explicit import.
- **Interface changes**: None. The truncation is applied internally before API calls. No function signatures change.
- **Coupling**: Slightly reduces coupling -- the shared utility centralizes the token-limit concern instead of relying on each call site to handle it.
- **Data ownership**: No change.
- **Reversibility**: Trivially reversible -- remove the truncation calls and the shared utility.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a well-scoped bug fix with a clear root cause, two known call sites, and a straightforward solution (truncate before API call). The issue provides complete recon and an explicit solution sketch.

## Prerequisites

No prerequisites -- `tiktoken` is already available as a transitive dependency of `openai`, and `OPENAI_API_KEY` is already configured in the environment.

## Solution

### Key Elements

- **`truncate_for_embedding()` utility**: Shared function that counts tokens via `tiktoken` and truncates to a safe limit (8,000 tokens, leaving 192 as safety margin)
- **`_embed_openai()` guard**: Applies truncation to each text in the input list before batching to the API
- **`KnowledgeDocument` pre-save guard**: Truncates `content` before it reaches `EmbeddingField.on_save()`, avoiding any modification to the vendored Popoto package

### Flow

**Impact finder path:** `build_index()` collects texts -> `_embed_openai()` receives list -> **truncate each item** -> `client.embeddings.create()` -> embeddings stored

**Knowledge document path:** `safe_upsert()` reads file content -> **truncate content** -> `doc.content = truncated` -> `doc.save()` -> `EmbeddingField.on_save()` -> `OpenAIProvider.embed()` -> embeddings stored

### Technical Approach

- Use `tiktoken.get_encoding("cl100k_base")` for token counting -- this is the encoding used by `text-embedding-3-small`
- Truncate at the token level (encode, slice to max_tokens, decode) rather than character-level approximation, to guarantee the result fits
- Cache the `tiktoken` encoding object at module level (it is expensive to initialize but thread-safe once created)
- Set the default max to 8,000 tokens (not 8,192) to leave a safety margin for any encoding overhead
- Log `logger.warning()` when truncation occurs, including original and truncated token counts

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `models/knowledge_document.py::safe_upsert()` line 105 has a broad `except Exception` that silently swallows embedding failures -- the truncation fix will prevent the exception from being raised in the first place, but the existing handler remains as a safety net
- [ ] `tools/impact_finder_core.py::_embed_openai()` has no try/except -- callers (`build_index` line 282) catch the exception and return an empty index. Truncation prevents the error path entirely.

### Empty/Invalid Input Handling
- [ ] `truncate_for_embedding()` must handle: empty string (return empty), None (raise TypeError or return empty), whitespace-only (return as-is, under limit)
- [ ] Inputs already under the limit must pass through unchanged (no unnecessary encoding/decoding round-trip for short texts)

### Error State Rendering
- [ ] No user-visible output -- this is a backend pipeline. Error state is logged, not rendered.

## Test Impact

No existing tests affected -- the `test_doc_impact_finder.py` tests mock `_embed_openai` via `patch("tools.impact_finder_core._embed_openai", side_effect=fake_embed)`, so they never hit the real function. The new truncation logic inside `_embed_openai` is invisible to these mocked tests. New tests will be added for the shared utility and the integration points.

## Rabbit Holes

- **Chunking with embedding averaging**: Splitting oversized text into chunks, embedding each, and averaging vectors would preserve more semantic content. This is significantly more complex and not justified by the current usage pattern (occasional oversized inputs, not systematic long-document embedding). Truncation is sufficient.
- **Modifying the Popoto package**: The `OpenAIProvider` lives in a third-party package. Patching it directly would create maintenance burden and fragile vendoring. Truncating before content reaches `EmbeddingField.on_save()` is cleaner.
- **Character-level approximation**: Estimating tokens as `len(text) / 4` and truncating by characters is simpler but unreliable -- tokenization is non-linear (code, URLs, and non-ASCII text tokenize very differently). Use `tiktoken` for correctness.

## Risks

### Risk 1: Information loss from truncation
**Impact:** Truncated documents lose trailing content, which could reduce embedding quality for very long documents.
**Mitigation:** The 8,000-token limit covers approximately 30,000 characters of English text -- more than enough for meaningful semantic representation. Documents exceeding this are rare (42 occurrences in 14 days across the entire pipeline). The alternative is a hard crash, which is strictly worse.

### Risk 2: tiktoken encoding initialization performance
**Impact:** First call to `tiktoken.get_encoding()` downloads and caches the encoding data, which could add latency on first use.
**Mitigation:** Cache the encoding object at module level so initialization happens once per process, not per call. The `tiktoken` cache persists on disk after first download.

## Race Conditions

No race conditions identified -- both call sites are synchronous within their respective execution contexts. The `tiktoken` encoding object is documented as thread-safe for read operations (encode/decode). Module-level caching of the encoding is set during import, before any concurrent access.

## No-Gos (Out of Scope)

- Chunking and embedding-averaging for long documents (separate follow-up if needed)
- Modifying the vendored Popoto package
- Adding truncation to the Voyage AI provider (`_embed_voyage`) -- not currently hitting this bug; can be added if needed
- Changing the embedding model to one with a larger token limit
- Adding token counting to Haiku summarization calls (different API, different limits)

## Update System

No update system changes required -- `tiktoken` is already an indirect dependency of `openai` and does not need to be added to requirements. The fix is purely internal code changes with no new config files or migration steps.

## Agent Integration

No agent integration required -- this is a fix to internal embedding pipeline code. No new MCP server tools, no changes to `.mcp.json`, and no bridge import changes are needed. The agent does not directly invoke `_embed_openai()` or `KnowledgeDocument.safe_upsert()`.

## Documentation

- [ ] Update `docs/features/knowledge-document-integration.md` to note the 8,192-token limit and the truncation behavior
- [ ] Add inline docstring to `truncate_for_embedding()` explaining the token limit, safety margin, and encoding choice

No external documentation site changes needed.

## Success Criteria

- [ ] No `BadRequestError` 400 from OpenAI embedding API due to token limit violations
- [ ] `_embed_openai()` in `tools/impact_finder_core.py` truncates each input text before API call
- [ ] `KnowledgeDocument.safe_upsert()` truncates content before it reaches `EmbeddingField.on_save()`
- [ ] A shared `truncate_for_embedding()` utility exists in `tools/embedding_utils.py` and is tested
- [ ] Warning log emitted when truncation occurs, with original and truncated token counts
- [ ] Unit tests cover: text under limit (no-op), text at limit (no-op), text over limit (truncated), empty string
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (truncation-utility)**
  - Name: embedding-builder
  - Role: Implement shared truncation utility and integrate into both call sites
  - Agent Type: builder
  - Resume: true

- **Validator (truncation-tests)**
  - Name: embedding-validator
  - Role: Verify truncation works correctly and all tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create shared truncation utility
- **Task ID**: build-utility
- **Depends On**: none
- **Validates**: tests/unit/test_embedding_utils.py (create)
- **Assigned To**: embedding-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/embedding_utils.py` with `truncate_for_embedding(text: str, max_tokens: int = 8000) -> str`
- Use `tiktoken.get_encoding("cl100k_base")` for token counting, cached at module level
- Encode text, slice token list to `max_tokens`, decode back to string
- Log `logger.warning("Truncated embedding input from %d to %d tokens", original, max_tokens)` when truncation occurs
- Short-circuit: if `len(text) < max_tokens * 2` (rough char estimate), encode and check; if under limit, return unchanged to avoid unnecessary decode round-trip
- Handle empty string input (return empty string immediately)

### 2. Integrate truncation into `_embed_openai()`
- **Task ID**: build-impact-finder
- **Depends On**: build-utility
- **Validates**: tests/unit/test_doc_impact_finder.py, tests/unit/test_embedding_utils.py
- **Assigned To**: embedding-builder
- **Agent Type**: builder
- **Parallel**: false
- Import `truncate_for_embedding` from `tools.embedding_utils` in `tools/impact_finder_core.py`
- In `_embed_openai()`, apply `truncate_for_embedding()` to each text in the input list before sending to the API
- Apply truncation before the batching loop so both the single-batch and multi-batch paths are covered

### 3. Integrate truncation into `KnowledgeDocument.safe_upsert()`
- **Task ID**: build-knowledge-doc
- **Depends On**: build-utility
- **Validates**: tests/unit/test_embedding_utils.py
- **Assigned To**: embedding-builder
- **Agent Type**: builder
- **Parallel**: false
- Import `truncate_for_embedding` from `tools.embedding_utils` in `models/knowledge_document.py`
- In `safe_upsert()`, truncate `content` before assigning to `doc.content` (both the create and update paths)
- This ensures the truncated text reaches `EmbeddingField.on_save()` without modifying Popoto

### 4. Write unit tests for truncation utility
- **Task ID**: build-tests
- **Depends On**: build-utility
- **Validates**: tests/unit/test_embedding_utils.py (create)
- **Assigned To**: embedding-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_embedding_utils.py`
- Test: text under limit returns unchanged
- Test: text at exactly 8000 tokens returns unchanged
- Test: text over limit is truncated to exactly 8000 tokens
- Test: empty string returns empty string
- Test: warning is logged when truncation occurs (use `caplog` fixture)
- Test: no warning when text is under limit

### 5. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-knowledge-doc
- **Assigned To**: embedding-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/knowledge-document-integration.md` to document the 8,192-token limit and truncation behavior
- Ensure `truncate_for_embedding()` has a clear docstring

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: embedding-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_embedding_utils.py -v` to verify new tests pass
- Run `pytest tests/unit/test_doc_impact_finder.py -v` to verify existing tests still pass
- Run `python -m ruff check tools/embedding_utils.py models/knowledge_document.py tools/impact_finder_core.py`
- Run `python -m ruff format --check tools/embedding_utils.py models/knowledge_document.py tools/impact_finder_core.py`
- Verify `truncate_for_embedding` is imported and called in both `_embed_openai()` and `safe_upsert()`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_embedding_utils.py -x -q` | exit code 0 |
| Existing tests pass | `pytest tests/unit/test_doc_impact_finder.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/embedding_utils.py models/knowledge_document.py tools/impact_finder_core.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/embedding_utils.py models/knowledge_document.py tools/impact_finder_core.py` | exit code 0 |
| Utility importable | `python -c "from tools.embedding_utils import truncate_for_embedding; print('OK')"` | output contains OK |
| Integration wired | `grep -l 'truncate_for_embedding' tools/impact_finder_core.py models/knowledge_document.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- the issue provides complete recon, both call sites are identified, the solution approach is well-established (tiktoken truncation), and there are no ambiguous scope decisions.
