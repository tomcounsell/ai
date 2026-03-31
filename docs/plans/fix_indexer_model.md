---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-31
tracking: https://github.com/tomcounsell/ai/issues/611
last_comment_id:
---

# Fix Knowledge Indexer Stale Model ID

## Problem

The knowledge indexer's document summarization pipeline has been broken since the Haiku model ID was retired. Every document indexing attempt fails with a 404 from the Anthropic API, causing the knowledge watcher to silently fall back to truncation-based summaries.

**Current behavior:** `tools/knowledge/indexer.py` line 104 hardcodes `claude-haiku-4-20250414`, a retired model ID. All Haiku summarization calls fail, falling back to the first-500-chars truncation path.

**Desired outcome:** The indexer imports and uses `HAIKU` from `config/models.py` (currently `claude-haiku-4-5-20251001`), so model upgrades propagate automatically. Summarization works again.

## Prior Art

Small appetite bug fix with obvious root cause -- prior art search skipped.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Model import**: Replace hardcoded string with `HAIKU` constant from `config/models.py`
- **Verification**: Confirm no other files contain the stale `claude-haiku-4-20250414` string

### Technical Approach

1. In `tools/knowledge/indexer.py`, add `from config.models import HAIKU` at the top
2. Replace `model="claude-haiku-4-20250414"` on line 104 with `model=HAIKU`
3. Grep the full codebase for `claude-haiku-4-20250414` to confirm zero remaining occurrences

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `_summarize_content` function already has a `try/except` that logs and falls back to truncation (line 121-122). The existing test suite does not test this path directly -- add a unit test that mocks a failed API call and asserts the fallback path returns truncated content.

### Empty/Invalid Input Handling
- [ ] No new functions are being added; existing empty-input behavior is unchanged.

### Error State Rendering
- [ ] Not applicable -- no user-visible output changes.

## Test Impact

No existing tests affected -- the existing `test_knowledge_indexer.py` tests helper functions and pipeline rejection paths. None of them reference the model string or mock the Anthropic API call. The fix changes only the model constant passed to `client.messages.create()`.

New tests:
- Add a unit test that verifies `_summarize_content` uses the `HAIKU` constant (by mocking `anthropic.Anthropic` and inspecting the `model` kwarg)
- Add a regression test that popoto handles long filenames (>255 chars) without crashing — the `popoto>=1.4.4` pin fixes a bug where long Redis key filenames caused errors. Test should create a Memory (or similar Popoto model) with a very long key and verify it saves/loads without error.

## Rabbit Holes

- Do not refactor the summarization pipeline or add retry logic -- this is a one-line constant fix
- Do not add integration tests that call the real Anthropic API -- unit test with mock is sufficient
- Do not audit all model usages across the codebase -- that is separate work

## Risks

### Risk 1: No risks identified
This is a single-constant replacement with zero behavioral change beyond fixing the 404.

## Race Conditions

No race conditions identified -- the model string is a module-level constant read synchronously.

## No-Gos (Out of Scope)

- Refactoring the summarization pipeline
- Adding retry/backoff logic for API failures
- Auditing all model references across the codebase (separate issue if needed)
- Changing the fallback truncation behavior

## Update System

No update system changes required -- this is a one-line code fix with no new dependencies.

## Agent Integration

No agent integration required -- the knowledge indexer is called by the knowledge watcher (bridge component), not through MCP tools.

## Documentation

- [ ] No documentation changes needed -- this is a bug fix replacing a stale constant. The `config/models.py` module docstring already explains the centralized model pattern.

## Success Criteria

- [ ] `tools/knowledge/indexer.py` imports `HAIKU` from `config/models.py` and uses it on line 104
- [ ] `grep -r "claude-haiku-4-20250414" .` returns zero results
- [ ] Unit test verifies `_summarize_content` passes `HAIKU` to the Anthropic client
- [ ] Regression test verifies popoto handles long filenames (>255 chars) without error
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (indexer-fix)**
  - Name: indexer-fixer
  - Role: Replace hardcoded model ID and add verification test
  - Agent Type: builder
  - Resume: true

- **Validator (indexer-fix)**
  - Name: indexer-validator
  - Role: Verify the fix and run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix the hardcoded model ID
- **Task ID**: build-fix-model
- **Depends On**: none
- **Validates**: tests/unit/test_knowledge_indexer.py
- **Assigned To**: indexer-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `from config.models import HAIKU` import to `tools/knowledge/indexer.py`
- Replace `model="claude-haiku-4-20250414"` with `model=HAIKU` on line 104
- Add unit test in `tests/unit/test_knowledge_indexer.py` that mocks `anthropic.Anthropic` and verifies the `model` kwarg equals `HAIKU`
- Add regression test that popoto handles long filenames (>255 chars) — create a model instance with a very long key, save and load it, verify no error
- Grep codebase to confirm no remaining `claude-haiku-4-20250414` occurrences

### 2. Validate fix
- **Task ID**: validate-fix
- **Depends On**: build-fix-model
- **Assigned To**: indexer-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_knowledge_indexer.py -v`
- Verify `grep -r "claude-haiku-4-20250414" . --include="*.py"` returns empty
- Verify `from config.models import HAIKU` appears in `tools/knowledge/indexer.py`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_knowledge_indexer.py -x -q` | exit code 0 |
| No stale model ID | `grep -r "claude-haiku-4-20250414" . --include="*.py"` | exit code 1 |
| Import present | `grep "from config.models import HAIKU" tools/knowledge/indexer.py` | exit code 0 |
| Lint clean | `python -m ruff check tools/knowledge/indexer.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the fix is straightforward and fully specified.
