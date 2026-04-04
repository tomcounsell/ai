---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-04
tracking: https://github.com/tomcounsell/ai/issues/683
last_comment_id:
---

# Summarizer Integration Cleanup: Stale Docs, Config Consolidation, Test Gaps

## Problem

The summarizer is functional and well-tested (~130 unit tests), but the #676 integration audit surfaced housekeeping debt across four areas:

1. `docs/features/summarizer-format.md` references two functions (`_render_stage_progress()`, `_render_link_footer()`) that no longer exist in `bridge/summarizer.py`, and describes a deprecated `qa_mode` field instead of the current `PersonaType.TEAMMATE` mechanism
2. The OpenRouter API URL `https://openrouter.ai/api/v1/chat/completions` is hardcoded identically in 9 production files plus 1 test file, despite `config/models.py` already existing as the centralized model config module
3. No integration test exercises `classify_output()` with a real LLM, and no test verifies the `response.py -> summarizer.py` callback wiring end-to-end
4. Three behavior-controlling thresholds (`FILE_ATTACH_THRESHOLD`, `SAFETY_TRUNCATE`, `CLASSIFICATION_CONFIDENCE_THRESHOLD`) are hardcoded with no env var override

All changes are backward-compatible -- no API changes, no behavior changes with default config.

## Prior Art

- **#676**: Integration audit that produced the findings (closed)
- **#674 / PR #682**: Coaching -> nudge rename already merged -- out of scope here
- **PR #660**: Prior doc cleanup that did not cover `summarizer-format.md`

## Data Flow

No data flow changes. The OpenRouter URL consolidation is a pure refactor (import instead of hardcode). The env-configurable thresholds use current values as defaults, preserving identical runtime behavior.

## Architectural Impact

Minimal. `config/models.py` gains two new constants (`OPENROUTER_URL`, `OPENROUTER_EMBEDDINGS_URL`). Nine production files replace local definitions with imports from that module. No new modules, no new dependencies.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- all four workstreams are independent of each other and of any in-flight work.

## Solution

### Workstream 1: Stale Docs Cleanup

Edit `docs/features/summarizer-format.md`:

- **Remove** references to `_render_stage_progress()` and `_render_link_footer()` from the Implementation section (line 95). These functions no longer exist in `bridge/summarizer.py`.
- **Remove** the "Q&A Mode (Prose)" section (lines 45-52) that describes `qa_mode=True`. Replace with a section describing `PersonaType.TEAMMATE` conversational formatting.
- **Remove** the `_render_link_footer()` mention in the auto-linkification safeguards (line 89). Replace with accurate description of current link rendering.
- **Update** Adaptive Format Rules item 6 (line 170) to reference `PersonaType.TEAMMATE` instead of `qa_mode=True`.

### Workstream 2: Config Consolidation

**Add to `config/models.py`:**
```python
OPENROUTER_URL = os.environ.get(
    "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
)
OPENROUTER_EMBEDDINGS_URL = os.environ.get(
    "OPENROUTER_EMBEDDINGS_URL", "https://openrouter.ai/api/v1/embeddings"
)
```

**Replace hardcoded URLs in these files with `from config.models import OPENROUTER_URL`:**

| File | Current pattern |
|------|----------------|
| `bridge/summarizer.py` | `OPENROUTER_URL = "https://..."` (line 45) |
| `scripts/autoexperiment.py` | `OPENROUTER_URL = "https://..."` (line 40) |
| `tools/image_gen/__init__.py` | `OPENROUTER_URL = "https://..."` (line 18) |
| `tools/doc_summary/__init__.py` | `OPENROUTER_URL = "https://..."` (line 16) |
| `tools/documentation/__init__.py` | `OPENROUTER_URL = "https://..."` (line 16) |
| `tools/knowledge_search/__init__.py` | `OPENROUTER_URL = "https://..."` (line 16) + inline embeddings URL (line 68) |
| `tools/image_tagging/__init__.py` | `OPENROUTER_URL = "https://..."` (line 18) |
| `tools/test_judge/__init__.py` | `OPENROUTER_URL = "https://..."` (line 16) |
| `tools/image_analysis/__init__.py` | `OPENROUTER_URL = "https://..."` (line 18) |
| `tests/ai_judge/judge.py` | Inline `"https://..."` (line 101) |

### Workstream 3: Integration Tests

**Test 1: `test_classify_output_real_api`**
- Location: `tests/integration/test_summarizer_integration.py`
- Calls `classify_output()` with a known text snippet
- Asserts the result is a valid `ClassificationResult` with a recognized category and confidence > 0
- Requires `OPENROUTER_API_KEY` env var (skip if absent)
- Marked with `@pytest.mark.integration`

**Test 2: `test_response_summarizer_wiring`**
- Location: `tests/integration/test_summarizer_integration.py`
- Exercises the `response.py -> summarizer.py` callback chain
- Uses a mock Telegram client but calls the real `send_response_with_files()` function
- Verifies that `summarize_response()` is invoked when text exceeds the short-response threshold
- Verifies that the session object is passed through the chain

### Workstream 4: Env-Configurable Thresholds

Change three constants in `bridge/summarizer.py` from hardcoded values to env-var-backed with current defaults:

```python
FILE_ATTACH_THRESHOLD = int(os.environ.get("FILE_ATTACH_THRESHOLD", "3000"))
SAFETY_TRUNCATE = int(os.environ.get("SAFETY_TRUNCATE", "4096"))
CLASSIFICATION_CONFIDENCE_THRESHOLD = float(
    os.environ.get("CLASSIFICATION_CONFIDENCE_THRESHOLD", "0.80")
)
```

No other files need changes -- all consumers already reference these module-level constants.

## Failure Path Test Strategy

### Exception Handling Coverage
- `config/models.py` URL constants: `os.environ.get()` with string defaults -- cannot raise
- Threshold constants: `int()`/`float()` conversion of env var values could raise `ValueError` if an operator sets a non-numeric value. This is acceptable -- a bad config should fail fast at import time rather than silently using a wrong value

### Empty/Invalid Input Handling
- Empty `OPENROUTER_URL` env var: Would result in empty string URL, causing immediate HTTP failure on first API call. Acceptable fail-fast behavior.
- Zero or negative threshold values: Would cause unexpected behavior but are operator error. Document valid ranges in comments.

### Error State Rendering
- Not applicable -- no UI changes

## Test Impact

- [ ] `tests/unit/test_summarizer.py::test_classification_threshold_value` (line 1017) -- UPDATE: Currently asserts `CLASSIFICATION_CONFIDENCE_THRESHOLD == 0.80`. Change to assert the default value matches 0.80, acknowledging it can now be overridden via env var.
- [ ] `tests/unit/test_summarizer.py` imports of `FILE_ATTACH_THRESHOLD`, `CLASSIFICATION_CONFIDENCE_THRESHOLD` -- UPDATE: imports remain valid, no change needed; constants still exist at same module path.
- [ ] `tests/ai_judge/judge.py` -- UPDATE: Replace inline OpenRouter URL with import from `config.models`.

## Rabbit Holes

- Do not refactor the summarizer's internal structure -- this is housekeeping, not a rewrite
- Do not add OpenRouter URL validation (e.g., URL parsing) -- the URL is either right or the API call fails immediately
- Do not make the OpenRouter model names env-configurable in this issue -- `config/models.py` already handles that concern
- Do not touch the coaching/nudge terminology -- already handled by #674/#682

## Risks

### Risk 1: Circular imports from config/models.py
**Impact:** Adding `import os` and URL constants to `config/models.py` could create circular imports if any tool module indirectly imports from config during initialization.
**Mitigation:** `config/models.py` currently has zero imports -- adding only `import os` keeps it leaf-level. Verify with `python -c "from config.models import OPENROUTER_URL"` after the change.

### Risk 2: Test env var leakage
**Impact:** Integration tests that set env vars for thresholds could leak into other tests.
**Mitigation:** Use `monkeypatch` fixture or `unittest.mock.patch.dict(os.environ)` for any threshold-override tests. The new integration tests do not set threshold env vars.

## Race Conditions

No race conditions identified. All changes are to module-level constants evaluated at import time.

## No-Gos (Out of Scope)

- No coaching/nudge terminology changes (already done in #674/#682)
- No summarizer refactoring or feature changes
- No changes to the summarizer's LLM prompt or tool schema
- No new MCP servers or bridge entry points
- No changes to the OpenRouter model name constants (already centralized)

## Update System

No update system changes required -- `config/models.py` is already deployed, and `os.environ.get()` reads are purely additive. The new env vars (`FILE_ATTACH_THRESHOLD`, `SAFETY_TRUNCATE`, `CLASSIFICATION_CONFIDENCE_THRESHOLD`, `OPENROUTER_URL`, `OPENROUTER_EMBEDDINGS_URL`) are optional with backward-compatible defaults.

## Agent Integration

No agent integration required -- all changes are internal refactoring of existing bridge/tool code. No new MCP servers, no `.mcp.json` changes, no new tool endpoints. The summarizer is already called through the existing bridge callback chain.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` to remove stale function references (`_render_stage_progress()`, `_render_link_footer()`) and replace `qa_mode` with `PersonaType.TEAMMATE`
- [ ] Add inline comments in `config/models.py` documenting the new URL constants and their env var overrides
- [ ] Add inline comments in `bridge/summarizer.py` documenting valid ranges for the three threshold env vars

## Success Criteria

- [ ] `docs/features/summarizer-format.md` contains no references to `_render_stage_progress()`, `_render_link_footer()`, or `qa_mode`
- [ ] `OPENROUTER_URL` is defined exactly once in `config/models.py` with `os.environ.get()` pattern
- [ ] `OPENROUTER_EMBEDDINGS_URL` is defined exactly once in `config/models.py` with `os.environ.get()` pattern
- [ ] All 10 files that previously hardcoded the URL import from `config.models` instead
- [ ] `grep -r 'openrouter.ai/api' --include='*.py' | grep -v config/models.py | grep -v docs/ | grep -v '.pyc'` returns zero results
- [ ] Integration test exists that calls `classify_output()` with a real API and asserts a valid `ClassificationResult`
- [ ] Integration test exists that exercises `response.py -> summarizer.py` callback chain
- [ ] `FILE_ATTACH_THRESHOLD`, `SAFETY_TRUNCATE`, `CLASSIFICATION_CONFIDENCE_THRESHOLD` are readable from env vars with current defaults
- [ ] All existing tests pass after changes
- [ ] Lint clean (`python -m ruff check .`)
- [ ] Format clean (`python -m ruff format --check .`)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: summarizer-cleanup-builder
  - Role: Execute all four workstreams
  - Agent Type: builder
  - Resume: true

- **Validator (verification)**
  - Name: summarizer-cleanup-validator
  - Role: Run verification checks and confirm success criteria
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Stale Docs Cleanup
- **Task ID**: docs-cleanup
- **Depends On**: none
- **Validates**: `grep -c '_render_stage_progress\|_render_link_footer\|qa_mode' docs/features/summarizer-format.md` returns 0
- **Assigned To**: summarizer-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true (with tasks 2 and 4)
- Edit `docs/features/summarizer-format.md` to remove stale references
- Replace `qa_mode` section with `PersonaType.TEAMMATE` description

### 2. Config Consolidation
- **Task ID**: config-consolidation
- **Depends On**: none
- **Validates**: `python -c "from config.models import OPENROUTER_URL, OPENROUTER_EMBEDDINGS_URL; print('OK')"` succeeds
- **Assigned To**: summarizer-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true (with tasks 1 and 4)
- Add `OPENROUTER_URL` and `OPENROUTER_EMBEDDINGS_URL` to `config/models.py`
- Replace hardcoded URLs in all 10 files with imports
- Verify no circular imports

### 3. Integration Tests
- **Task ID**: integration-tests
- **Depends On**: config-consolidation (tests may import from config.models)
- **Validates**: `pytest tests/integration/test_summarizer_integration.py -v` passes
- **Assigned To**: summarizer-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/integration/test_summarizer_integration.py`
- Add `test_classify_output_real_api` and `test_response_summarizer_wiring`

### 4. Env-Configurable Thresholds
- **Task ID**: env-thresholds
- **Depends On**: none
- **Validates**: `python -c "from bridge.summarizer import FILE_ATTACH_THRESHOLD; print(FILE_ATTACH_THRESHOLD)"` prints 3000
- **Assigned To**: summarizer-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true (with tasks 1 and 2)
- Replace hardcoded constants with `os.environ.get()` backed equivalents
- Update the threshold assertion in `tests/unit/test_summarizer.py`

### 5. Verification
- **Task ID**: verification
- **Depends On**: docs-cleanup, config-consolidation, integration-tests, env-thresholds
- **Assigned To**: summarizer-cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all success criteria checks
- Run full test suite
- Run lint and format checks

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No stale doc refs | `grep -c '_render_stage_progress\|_render_link_footer\|qa_mode' docs/features/summarizer-format.md` | 0 |
| URL centralized | `python -c "from config.models import OPENROUTER_URL; print(OPENROUTER_URL)"` | prints URL |
| No hardcoded URLs | `grep -r 'openrouter.ai/api' --include='*.py' \| grep -v config/models.py \| grep -v docs/ \| grep -v .pyc` | empty |
| Thresholds configurable | `FILE_ATTACH_THRESHOLD=5000 python -c "from bridge.summarizer import FILE_ATTACH_THRESHOLD; assert FILE_ATTACH_THRESHOLD == 5000"` | exit 0 |
| Integration tests | `pytest tests/integration/test_summarizer_integration.py -v` | pass |
| Unit tests | `pytest tests/unit/test_summarizer.py -v` | pass |
| Lint clean | `python -m ruff check .` | exit 0 |
| Format clean | `python -m ruff format --check .` | exit 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- all four workstreams are well-defined with clear acceptance criteria from the issue.
