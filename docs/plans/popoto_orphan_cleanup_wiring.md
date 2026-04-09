---
status: Complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/860
last_comment_id:
---

# Popoto Orphan Cleanup Wiring

## Problem

Sentry shows 82 events in 14 days of the Popoto warning `"one or more redis keys points to missing objects"`. Orphaned index entries accumulate because cleanup mechanisms have gaps.

**Current behavior:**

Three distinct gaps allow orphaned Popoto index entries to accumulate:

1. **Worker startup is partial.** `worker/__main__.py:181` calls `AgentSession.rebuild_indexes()` only for `AgentSession`. The other 13 Popoto models (`Memory`, `TelegramMessage`, `Chat`, `Link`, `BridgeEvent`, `DeadLetter`, `DedupRecord`, `TeammateMetrics`, `Reflection`, `ReflectionRun`, `ReflectionIgnore`, `KnowledgeDocument`, `PRReviewAudit`) are never rebuilt at startup.

2. **`ReflectionRunner` lacks a popoto cleanup step.** The `ReflectionRunner` in `scripts/reflections.py` (run via `python scripts/reflections.py` or launchd) uses a hardcoded `self.steps` list. It has no `step_popoto_index_cleanup` entry, so the daily CLI maintenance run never cleans indexes.

3. **`_get_all_models()` misses two models.** `scripts/popoto_index_cleanup._get_all_models()` reads `models/__init__.__all__`, which does not include `KnowledgeDocument` or `PRReviewAudit`. Even when `run_cleanup()` executes, these two models are skipped.

**Note on `ReflectionScheduler`:** The bridge-hosted `ReflectionScheduler` (`agent/reflection_scheduler.py`) does read `config/reflections.yaml` and can dispatch callable functions. The `popoto-index-cleanup` entry is registered there and the scheduler CAN execute it when the bridge is running. However, the scheduler depends on the bridge being up, while `ReflectionRunner` is a standalone safety net that runs via launchd regardless. Both paths need coverage.

**Desired outcome:**

- Worker startup rebuilds indexes for ALL Popoto models, not just `AgentSession`
- `ReflectionRunner` includes a `step_popoto_index_cleanup` that calls `run_cleanup()` as a daily safety net
- `_get_all_models()` discovers all Popoto models including `KnowledgeDocument` and `PRReviewAudit`
- Sentry stops receiving orphan warnings

## Prior Art

- **Issue #617**: Popoto ORM hygiene -- Created `popoto-index-cleanup` reflection, `Meta.ttl` on AgentSession, and `scripts/popoto_index_cleanup.py`. Closed via PR #650. The cleanup script exists but `ReflectionRunner` was never wired to call it.
- **PR #650**: Popoto ORM hygiene implementation -- Merged the cleanup script, YAML config entry, and worker startup `rebuild_indexes()`. Did not add a `step_*` method to `ReflectionRunner` or expand worker startup to all models.
- **Issue #783**: AgentSession status index corruption -- Fixed the lazy-load `_saved_field_values` bug and delete-and-recreate pattern. Closed, but the Sentry warning persists because orphans from other models and TTL expiry were never addressed.
- **PR #751**: Bridge/worker separation -- Consolidated startup sequence into `worker/__main__.py`. Inherited the single-model `rebuild_indexes()` from the bridge without expanding it.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #650 | Created `run_cleanup()` script, registered YAML entry, added `AgentSession.rebuild_indexes()` to startup | Never wired `run_cleanup()` into `ReflectionRunner.steps`; startup only rebuilt one model |
| PR #783 | Fixed AgentSession lazy-load bug and delete-and-recreate pattern | Addressed one source of orphans (AgentSession corruption) but not the broader problem (TTL expiry across all models) |

**Root cause pattern:** Each fix narrowed scope to `AgentSession` specifically, while orphans accumulate across all 15 Popoto models. The cleanup infrastructure was built but the last mile of wiring was missed in both the worker startup and the `ReflectionRunner`.

## Data Flow

1. **Entry point**: Redis TTL expires a Popoto hash key (or a crash leaves a hash deleted)
2. **Orphan created**: The secondary index Set (e.g., `Memory:_all`) still contains a member pointing to the now-missing hash
3. **Detection**: Any Popoto query that encounters the orphan logs `"one or more redis keys points to missing objects"` and Sentry captures it
4. **Cleanup path A (scheduler)**: `ReflectionScheduler` tick -> `execute_function_reflection("scripts.popoto_index_cleanup.run_cleanup")` -> `run_cleanup()` -> iterates models -> `rebuild_indexes()` per model (works when bridge is running)
5. **Cleanup path B (runner, BROKEN)**: `python scripts/reflections.py` -> `ReflectionRunner.run()` -> iterates `self.steps` -> no popoto cleanup step exists -> orphans persist
6. **Cleanup path C (startup, PARTIAL)**: `worker/__main__.py` -> `AgentSession.rebuild_indexes()` -> only one model cleaned

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are to existing Python files with no new services or API keys needed.

## Solution

### Key Elements

- **Worker startup expansion**: Replace single-model `AgentSession.rebuild_indexes()` with a loop over all models via `_get_all_models()` from the existing cleanup script
- **ReflectionRunner step**: Add `step_popoto_index_cleanup` to the hardcoded `self.steps` list, calling the existing `run_cleanup()` function
- **Model registry fix**: Add `KnowledgeDocument` and `PRReviewAudit` to `models/__init__.py` so `_get_all_models()` discovers them

### Flow

**Worker starts** -> rebuild indexes for all models -> **Bridge runs** -> ReflectionScheduler ticks popoto-index-cleanup daily -> **Launchd runs** -> ReflectionRunner runs step_popoto_index_cleanup daily -> **All three paths** keep indexes clean

### Technical Approach

- Reuse `scripts.popoto_index_cleanup.run_cleanup()` in the new `ReflectionRunner` step -- no duplication of logic
- Reuse `scripts.popoto_index_cleanup._get_all_models()` in worker startup instead of importing individual models
- Place the new step after `redis_ttl_cleanup` (step 12) since TTL cleanup deletes records and popoto cleanup removes their orphaned index entries
- Add `KnowledgeDocument` and `PRReviewAudit` to `models/__init__.__all__` so the existing `_get_all_models()` function picks them up automatically

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `run_cleanup()` already has per-model try/except with logging -- no silent exception swallowing. Existing test `test_one_model_error_does_not_abort` validates this.
- [x] The new `step_popoto_index_cleanup` method must wrap the call in try/except with `logger.warning()` to match the pattern of other steps (e.g., `step_redis_cleanup`). Test will verify the step is registered.

### Empty/Invalid Input Handling
- [x] `_get_all_models()` already returns `[]` on import failure and `run_cleanup()` handles the empty-list case with `{"status": "no_models"}`. Existing tests cover this.

### Error State Rendering
- [x] Not applicable -- this is a background maintenance task with no user-visible output. Errors are logged to the reflections log.

## Test Impact

- [x] `tests/unit/test_worker_entry.py::test_worker_calls_rebuild_indexes` -- UPDATE: currently asserts `rebuild_indexes` appears in source; update to also verify `_get_all_models` or `run_cleanup` is used (ensuring all-model rebuild, not just AgentSession)
- [x] `tests/unit/test_worker_entry.py::test_worker_startup_sequence_order` -- UPDATE: the rebuild step now uses `run_cleanup()` or `_get_all_models()` instead of `AgentSession.rebuild_indexes()`, so the regex pattern `\.rebuild_indexes\(\)` may need updating
- [x] `tests/integration/test_reflections_redis.py::TestPopotoIndexCleanupReflection` -- UPDATE: add a test that `step_popoto_index_cleanup` exists in `ReflectionRunner.steps`

## Rabbit Holes

- **Implementing a generic YAML callable dispatcher in `ReflectionRunner`**: The `ReflectionScheduler` already handles YAML callables. Adding a second generic dispatcher to `ReflectionRunner` adds complexity for no gain. The hardcoded step pattern is simple and testable -- just add the step.
- **Migrating `ReflectionRunner` to use `ReflectionScheduler`**: These are two separate systems with different lifecycles (bridge-hosted vs. standalone CLI). Merging them is a larger architectural change unrelated to this bug.
- **Adding TTL to all models**: Only `AgentSession` currently has `Meta.ttl`. Adding TTL to other models is a separate decision that should be evaluated model-by-model.

## Risks

### Risk 1: Worker startup takes longer with all-model rebuild
**Impact:** Slower worker boot time if many models have large index sets
**Mitigation:** `rebuild_indexes()` is SCAN-based (cursor-based, non-blocking). Even with 15 models, the overhead is minimal. Log the total time so we can monitor.

### Risk 2: Importing `KnowledgeDocument`/`PRReviewAudit` in `__init__.py` triggers unexpected side effects
**Impact:** Circular imports or import-time errors
**Mitigation:** Both models already exist and are imported elsewhere. Adding them to `__init__.py` is low-risk. Test with `python -c "import models"` after the change.

## Race Conditions

No race conditions identified -- `rebuild_indexes()` is SCAN-based and idempotent. Concurrent runs from the scheduler and runner simply overlap harmlessly. The ReflectionRunner uses per-step completion tracking that prevents re-running within the same day.

## No-Gos (Out of Scope)

- Removing the YAML `callable`/`execution_type` fields from `reflections.yaml` -- the `ReflectionScheduler` actively uses them; they are NOT dead config
- Adding `Meta.ttl` to additional models beyond `AgentSession`
- Merging `ReflectionRunner` and `ReflectionScheduler` into a single system
- Investigating which specific models are generating the Sentry orphan warnings (the fix is comprehensive -- all models get cleaned)

## Update System

No update system changes required -- this modifies existing Python files only. After `git pull`, the worker restart will pick up the new startup behavior and the next reflections run will include the new step. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is an internal maintenance fix to existing background systems (`worker/__main__.py` and `scripts/reflections.py`). No MCP server changes, no `.mcp.json` changes, no bridge import changes needed.

## Documentation

- [x] Update `docs/features/popoto-index-hygiene.md` to document the new `ReflectionRunner` step and all-model worker startup rebuild
- [x] Add inline docstring to the new `step_popoto_index_cleanup` method

## Success Criteria

- [x] `ReflectionRunner.steps` includes `("popoto_index_cleanup", "Popoto Index Cleanup", self.step_popoto_index_cleanup)`
- [x] Worker startup calls `run_cleanup()` or iterates `_get_all_models()` instead of only `AgentSession.rebuild_indexes()`
- [x] `models/__init__.__all__` includes `KnowledgeDocument` and `PRReviewAudit`
- [x] `python -c "from scripts.popoto_index_cleanup import _get_all_models; models = _get_all_models(); names = [m.__name__ for m in models]; assert 'KnowledgeDocument' in names and 'PRReviewAudit' in names"`
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)
- [x] After worker restart + one reflections run, Sentry orphan warnings stop

## Team Orchestration

### Team Members

- **Builder (cleanup-wiring)**
  - Name: cleanup-builder
  - Role: Wire popoto cleanup into ReflectionRunner, expand worker startup, fix model registry
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup-wiring)**
  - Name: cleanup-validator
  - Role: Verify all three fixes are correct and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix model registry and expand worker startup
- **Task ID**: build-wiring
- **Depends On**: none
- **Validates**: tests/unit/test_popoto_cleanup_reflection.py, tests/unit/test_worker_entry.py
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `KnowledgeDocument` and `PRReviewAudit` to `models/__init__.py` imports and `__all__`
- In `worker/__main__.py`, replace the `AgentSession.rebuild_indexes()` block (Step 1) with a call to `run_cleanup()` from `scripts.popoto_index_cleanup` (or use `_get_all_models()` to iterate all models). Keep the try/except with non-fatal logging.
- Add `step_popoto_index_cleanup` method to `ReflectionRunner` in `scripts/reflections.py` that calls `run_cleanup()` and appends results to `self.state.daily_report`. Follow the pattern of `step_redis_cleanup` (try/except, logger.warning on failure).
- Add the new step to `self.steps` list after `redis_ttl_cleanup`: `("popoto_index_cleanup", "Popoto Index Cleanup", self.step_popoto_index_cleanup)`
- Update `tests/unit/test_worker_entry.py::test_worker_calls_rebuild_indexes` to verify all-model rebuild
- Update `tests/unit/test_worker_entry.py::test_worker_startup_sequence_order` if the regex pattern changes
- Add test in `tests/integration/test_reflections_redis.py` verifying `popoto_index_cleanup` is in `ReflectionRunner.steps`

### 2. Validate all changes
- **Task ID**: validate-wiring
- **Depends On**: build-wiring
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -c "import models; print(models.__all__)"` and verify `KnowledgeDocument` and `PRReviewAudit` are present
- Run `python -c "from scripts.popoto_index_cleanup import _get_all_models; print([m.__name__ for m in _get_all_models()])"` and verify all 15 models
- Verify `ReflectionRunner.steps` contains `popoto_index_cleanup`
- Run `pytest tests/unit/test_popoto_cleanup_reflection.py tests/unit/test_worker_entry.py tests/integration/test_reflections_redis.py -x -q`
- Run `python -m ruff check . && python -m ruff format --check .`

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-wiring
- **Assigned To**: cleanup-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/popoto-index-hygiene.md` to document the ReflectionRunner step and all-model worker startup
- Add entry to `docs/features/README.md` if not already present

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite `pytest tests/ -x -q`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Models complete | `python -c "from scripts.popoto_index_cleanup import _get_all_models; assert len(_get_all_models()) >= 15"` | exit code 0 |
| Runner step exists | `python -c "from scripts.reflections import ReflectionRunner; r = ReflectionRunner(); assert any(s[0] == 'popoto_index_cleanup' for s in r.steps)"` | exit code 0 |
| Worker uses all models | `grep -c '_get_all_models\|run_cleanup' worker/__main__.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. The issue mentions considering removing the YAML `callable` pattern as "dead infrastructure." However, the `ReflectionScheduler` actively uses it to dispatch function-type reflections. Should we add a code comment to `config/reflections.yaml` clarifying that these fields ARE used by `agent/reflection_scheduler.py`, to prevent future confusion?
