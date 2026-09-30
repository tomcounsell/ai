---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-13
tracking: https://github.com/tomcounsell/ai/issues/926
last_comment_id:
---

# Reflections Quality Pass: Scheduler Placement, Model Split, Field Conventions

## Problem

The reflections system has accumulated eight concrete correctness and hygiene issues that break dashboard features, violate architectural conventions, and make the system harder to extend.

**Current behavior:**

- `Reflection.next_due` is a persisted Redis field that is never written anywhere. The dashboard reads it for "next due" display and sort -- it is always `None`, so the overdue indicator and schedule sort are broken.
- `run_history` entries include a `log_path` key per docstring, but `mark_completed()` never sets it. The dashboard "view logs" link always returns "No log file associated with this run."
- `ReflectionScheduler` starts inside `bridge/telegram_bridge.py:2053` (the I/O-only process). The worker (`python -m worker`) has no reflection references.
- `models/reflections.py` contains three unrelated models (`ReflectionRun`, `ReflectionIgnore`, `PRReviewAudit`) under a filename matching none of them.
- Time fields (`last_run`, `started_at`, `created_at`, `expires_at`, `audited_at`) are stored as Unix float timestamps instead of `datetime` objects per project convention.
- `ReflectionRun` stores `findings`, `session_analysis`, `daily_report` as unbounded DictField/ListField values in Redis with no cap or eviction.
- `ReflectionRun.reflections` field name collides with the feature concept.
- `config/reflections.yaml` header and `docs/features/reflections.md` describe the `command` field as "Shell command" -- stale since it became a natural-language PM session prompt.

**Desired outcome:**

- `next_due` is a computed property derived from `last_run + interval`; dashboard displays correct values
- `log_path` dead code path removed from docstring and UI
- `ReflectionScheduler` starts in the worker, not the bridge
- Each model lives in its own file named after the model
- All timestamp fields use `datetime` objects with `verb_at` naming
- `ReflectionRun` large outputs written to filesystem with path reference in Redis
- `ReflectionRun.reflections` renamed to `session_observations`
- Docs and YAML comments reflect current implementation

## Freshness Check

**Baseline commit:** `f5db591283f58d955427207ff49fb39c1fd664fc`
**Issue filed at:** 2026-04-13T03:50:56Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `models/reflection.py:44` -- `next_due = Field(type=float, null=True)` -- still holds, zero write sites confirmed
- `models/reflection.py:110-115` -- `mark_completed()` run_record dict -- still has no `log_path` key, confirmed
- `bridge/telegram_bridge.py:2053` -- `from agent.reflection_scheduler import ReflectionScheduler` -- still holds
- `worker/__main__.py` -- zero reflection references -- still holds
- `models/reflections.py` -- three models in one file -- still holds
- `config/reflections.yaml:13` -- `command` field comment says "Shell command to run" -- **minor drift**: comment now says "Shell command to run (for execution_type: agent)" at line 13, still stale (it is actually a natural-language prompt, not a shell command)

**Cited sibling issues/PRs re-checked:**
- #748 -- still OPEN, "Unify reflections: single model, split monolith into 24 declarative reflections"
- #361 -- CLOSED 2026-03-13, shipped as PR #389
- #538 -- CLOSED 2026-03-26, shipped
- #413 -- CLOSED 2026-03-23, shipped as PR #511

**Commits on main since issue was filed (touching referenced files):**
- `9ef92773` "Remove linkedin_messages step; sync reflections docs to actual 18-unit pipeline" -- touched `docs/features/reflections.md` and `config/reflections.yaml`, removed linkedin step. Irrelevant to this issue's scope.
- `f5a819f1` "Remove LinkedIn step from reflections pipeline" -- same area, irrelevant.
- `5fdb588a` "feat: sentry-cli integration" -- added `sentry-issue-triage` to registry. Irrelevant.

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** Three commits landed since filing but none touch the eight items in scope. All issue claims verified as still accurate against current main.

## Prior Art

- **PR #389**: "Reflections as first-class objects with unified scheduler" -- Created the `Reflection` model, `ReflectionScheduler`, and YAML registry. Established the current architecture including the `next_due` field that was never wired.
- **PR #572**: "Reflections Regroup: 19 steps to 14 units with string keys" -- Reorganized daily pipeline steps but did not address model split or field conventions.
- **PR #511**: "Unified Web UI: Infrastructure, Reflections Dashboard" -- Created `ui/data/reflections.py` that reads the broken `next_due` field. The dashboard code was written assuming `next_due` would work.
- **PR #664**: "Fix dashboard timestamps, surface session metadata" -- Fixed timestamp display in dashboard but did not address the `next_due` computation issue.

No prior attempts to fix these specific issues were found. These are known debt items surfaced by the integration audit.

## Data Flow

### next_due computation (currently broken)

1. **Entry point**: Dashboard loads `ui/data/reflections.py:get_all_reflections()`
2. **State read**: Reads `Reflection` model from Redis -- `state.next_due` is always `None`
3. **Dashboard**: Renders `None` as empty cell, sort produces arbitrary order
4. **Fix**: Compute `next_due = state.last_run + config.interval` in `get_all_reflections()` using the registry config it already loads

### Scheduler tick (move from bridge to worker)

1. **Current**: Bridge startup -> `ReflectionScheduler.start()` -> ticks every 60s
2. **Target**: Worker startup -> `ReflectionScheduler.start()` -> ticks every 60s (same behavior, different host process)
3. **No data flow change** -- the scheduler reads the same YAML and Redis models regardless of host process

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `Reflection.next_due` field removed (breaking for any code reading it directly); replaced by computation in dashboard layer. `ReflectionRun.reflections` renamed to `session_observations`.
- **Coupling**: Decreases -- scheduler moves out of bridge (I/O) into worker (processing), aligning with documented architecture
- **Data ownership**: No change -- Redis remains the state store
- **Reversibility**: High -- all changes are mechanical renames, moves, and field conversions. Backward-compatible import shim in `models/reflections.py` makes rollback trivial.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope fully defined by issue)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are to existing code and models.

## Solution

### Key Elements

- **Scheduler relocation**: Move `ReflectionScheduler` init from bridge to worker
- **Model split**: Break `models/reflections.py` into three files with backward-compatible re-exports
- **next_due computation**: Remove field, compute in dashboard data layer
- **Dead code removal**: Remove `log_path` references from docstrings and UI
- **Field conventions**: Rename timestamps to `verb_at` with `datetime` type
- **Output externalization**: Write large `ReflectionRun` payloads to filesystem
- **Field rename**: `ReflectionRun.reflections` -> `session_observations`
- **Docs sync**: Update stale YAML comments and feature docs

### Flow

**Phase 1 (high blast radius):** Scheduler move + model split -> update all import sites -> verify imports

**Phase 2 (low blast radius):** Field renames + type conversions + dead code removal + docs sync

### Technical Approach

- **Model split strategy**: Create `models/reflection_run.py`, `models/reflection_ignore.py`, `models/pr_review_audit.py`. Keep `models/reflections.py` as a backward-compatible re-export shim (`from models.reflection_run import ReflectionRun` etc.) to avoid breaking all import sites simultaneously. Update `models/__init__.py` to import from new files. Gradually migrate direct importers to new paths.
- **Scheduler move**: Remove the two lines at `bridge/telegram_bridge.py:2053-2055`. Add equivalent `ReflectionScheduler` initialization to `worker/__main__.py` startup, creating a background asyncio task.
- **next_due removal**: Delete the `next_due` field from `Reflection` model. In `ui/data/reflections.py:get_all_reflections()`, compute `next_due = state.last_run + config["interval"]` when `state.last_run` is not None. The scheduler's `get_status()` method already computes this correctly (line 508) -- the dashboard just needs to match that pattern.
- **log_path cleanup**: Remove `log_path` from the `run_history` docstring in `Reflection`. Remove `get_log_content()` and the `log_path` branch in `get_run_detail()` from `ui/data/reflections.py`. Remove the `_read_log_file()` helper if no other callers exist.
- **Timestamp migration**: Rename `last_run` -> `ran_at` (type datetime) in `Reflection`. Rename `started_at`, `created_at`, `expires_at`, `audited_at` in the split model files. Since these are Popoto Redis fields, existing float values in Redis will need coercion on read (Popoto handles this via field type).
- **Output externalization**: Replace `findings`, `session_analysis`, `daily_report` DictField/ListField in `ReflectionRun` with a single `output_path` field. Write payload to `logs/reflections/{date}.json`. Update `scripts/reflections.py` write sites.
- **Field rename**: Rename `ReflectionRun.reflections` to `session_observations`. Update all read/write sites in `scripts/reflections.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_reflection()` has broad `except Exception` at line 300 -- already logs with `exc_info=True` and updates state. Test: verify state transitions to error on exception.
- [ ] `_enqueue_agent_reflection()` has bare `except Exception` at line 359 for project key resolution -- falls back to env var. Acceptable fail-safe pattern.

### Empty/Invalid Input Handling
- [ ] `compute_next_due` when `last_run` is None -- must return None, not crash
- [ ] `get_all_reflections()` when registry is empty or state has no matching reflection
- [ ] `ReflectionRun` output file write when `logs/reflections/` directory does not exist -- must create it

### Error State Rendering
- [ ] Dashboard must gracefully render reflections with `next_due = None` (never-run reflections)
- [ ] Dashboard must handle missing `output_path` file (deleted or moved)

## Test Impact

- [ ] `tests/unit/test_reflection_model.py::test_reflection_create_minimal` -- UPDATE: remove `next_due=None` from create call, rename `last_run` references to `ran_at`
- [ ] `tests/unit/test_reflection_model.py` (all tests) -- UPDATE: rename `last_run` field references to `ran_at`
- [ ] `tests/unit/test_reflection_scheduler.py::test_get_status_with_reflection` -- UPDATE: remove `next_due` field assertions
- [ ] `tests/unit/test_ui_reflections_data.py` (all tests) -- UPDATE: `next_due` is now computed from `ran_at + interval`, not read from model field; update mock setup and assertions
- [ ] `tests/unit/test_reflections.py::test_ignore_*` -- UPDATE: rename import path from `models.reflections` to `models.reflection_ignore` (or keep working via shim)
- [ ] `tests/unit/test_pr_review_audit.py` (all tests) -- UPDATE: rename import path from `models.reflections` to `models.pr_review_audit` (or keep working via shim)
- [ ] `tests/integration/test_reflections_redis.py` (all tests) -- UPDATE: rename import paths; rename field references (`started_at` -> datetime, `created_at` -> datetime, `expires_at` -> datetime, `audited_at` -> datetime, `reflections` -> `session_observations`)

## Rabbit Holes

- **Full unification (#748)**: This is a quality pass, not the full model unification. Do not attempt to merge `Reflection` and `ReflectionRun` into a single model -- that is #748 scope.
- **Stuck-detection threshold tuning**: The issue mentions this was dropped; do not add configurable thresholds.
- **Redis data migration**: Do not write a migration script for existing Redis data. Popoto field type changes are handled on read. Old float timestamps will coerce to datetime on access. Old `reflections` field data will simply be inaccessible under the new `session_observations` name, but ReflectionRun records have a 30-day TTL so stale data expires naturally.
- **Dashboard UI redesign**: Only fix the data layer. Do not redesign the reflections dashboard template.

## Risks

### Risk 1: Import breakage from model split
**Impact:** Any file importing from `models.reflections` breaks if not updated
**Mitigation:** Keep `models/reflections.py` as a backward-compatible re-export shim. All existing imports continue to work. New code imports from the specific model files.

### Risk 2: Popoto datetime field coercion on existing Redis data
**Impact:** Existing float timestamps in Redis may not deserialize cleanly to datetime
**Mitigation:** Add a `__post_init__` or field coercion that handles float -> datetime conversion. Test with actual Redis data before merging.

### Risk 3: Scheduler missing from worker on some machines
**Impact:** Reflections stop running until worker is restarted with the new code
**Mitigation:** The `/update` skill restarts the worker service. Document in the PR that a worker restart is required.

## Race Conditions

No race conditions identified -- the scheduler is a single asyncio loop with skip-if-running guards already in place. Moving it from bridge to worker does not introduce concurrent access because only one process runs the scheduler at a time. The model split is purely structural and does not change any concurrent access patterns.

## No-Gos (Out of Scope)

- Full reflection model unification (#748)
- Stuck-detection threshold configuration
- Redis data migration scripts
- Dashboard UI redesign
- Adding new reflections to the registry
- Changing scheduler tick interval or execution semantics

## Update System

The `/update` skill runs `./scripts/valor-service.sh restart` which cycles the bridge, watchdog, and worker. After this change ships:
- The bridge will no longer start the scheduler (the import is removed), so restarting the bridge is safe.
- The worker must be restarted to pick up the scheduler. The existing `worker-restart` in the update flow handles this.
- No new dependencies or config files.
- No migration steps beyond the standard `git pull && restart`.

## Agent Integration

No agent integration required -- this is an internal refactoring of models, scheduler placement, and dashboard data layer. No new tools, MCP servers, or bridge changes are needed. The agent-type reflections (`system-health-digest`, `sentry-issue-triage`) continue to work unchanged since the scheduler API is not modified.

## Documentation

- [ ] Update `docs/features/reflections.md`: fix architecture diagram (bridge -> worker), fix registry format table (`command` description), fix state model table (remove `next_due`, rename `last_run` to `ran_at`), update registered reflections table (add sustainability/hibernation/sentry reflections), fix "17-unit" reference to "18-unit", update Key Files table for split model files
- [ ] Update `docs/features/reflections-dashboard.md`: fix state description to note `next_due` is computed, not stored
- [ ] Update `config/reflections.yaml` header comment: change `command` description from "Shell command to run" to "Natural-language prompt for PM session"
- [ ] Update inline docstrings in `models/reflection.py`: remove `next_due` from Fields docstring, remove `log_path` from run_history docstring, rename `last_run` to `ran_at`

## Success Criteria

- [ ] `ReflectionScheduler` starts in `worker/__main__.py`, not `bridge/telegram_bridge.py`
- [ ] `models/reflections.py` is a re-export shim; models live in `models/reflection_run.py`, `models/reflection_ignore.py`, `models/pr_review_audit.py`
- [ ] All import sites work (both old `from models.reflections import X` and new `from models.reflection_run import ReflectionRun`)
- [ ] `Reflection.next_due` field removed; dashboard computes next_due from `ran_at + config.interval`
- [ ] Dashboard "next due" column displays correct values for reflections that have run
- [ ] `log_path` removed from docstring and UI code paths (`get_log_content`, `get_run_detail` log_path branch, `_read_log_file`)
- [ ] `Reflection.last_run` renamed to `ran_at` with type `datetime`
- [ ] `ReflectionRun`, `ReflectionIgnore`, `PRReviewAudit` timestamp fields use `datetime` type
- [ ] `ReflectionRun` large-payload fields replaced with `output_path`; output written to `logs/reflections/{date}.json`
- [ ] `ReflectionRun.reflections` renamed to `session_observations`
- [ ] `config/reflections.yaml` header comment updated
- [ ] `docs/features/reflections.md` updated per Documentation section
- [ ] All existing reflection-related tests pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (phase-1)**
  - Name: model-split-builder
  - Role: Scheduler relocation and model file split with import updates
  - Agent Type: builder
  - Resume: true

- **Builder (phase-2)**
  - Name: field-cleanup-builder
  - Role: Field renames, type conversions, dead code removal, output externalization
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: reflections-validator
  - Role: Verify imports, field access, dashboard rendering, test suite
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update reflections docs and YAML comments
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Move ReflectionScheduler from bridge to worker
- **Task ID**: build-scheduler-move
- **Depends On**: none
- **Validates**: `tests/unit/test_reflection_scheduler.py`
- **Assigned To**: model-split-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `from agent.reflection_scheduler import ReflectionScheduler` and the two initialization lines from `bridge/telegram_bridge.py:2053-2055`
- Add `ReflectionScheduler` import and `asyncio.create_task(scheduler.start())` to `worker/__main__.py` startup alongside the session processing loop
- Verify scheduler starts when worker starts

### 2. Split models/reflections.py into separate files
- **Task ID**: build-model-split
- **Depends On**: none
- **Validates**: `python -c "from models.reflections import ReflectionRun, ReflectionIgnore; from models.reflection_run import ReflectionRun; from models.reflection_ignore import ReflectionIgnore; from models.pr_review_audit import PRReviewAudit"`
- **Assigned To**: model-split-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/reflection_run.py` with `ReflectionRun` class
- Create `models/reflection_ignore.py` with `ReflectionIgnore` class
- Create `models/pr_review_audit.py` with `PRReviewAudit` class
- Convert `models/reflections.py` to a backward-compatible re-export shim
- Update `models/__init__.py` to import from new files
- Verify all existing import sites still work via the shim

### 3. Remove next_due field, compute in dashboard
- **Task ID**: build-next-due
- **Depends On**: build-model-split
- **Validates**: `tests/unit/test_ui_reflections_data.py`, `tests/unit/test_reflection_model.py`
- **Assigned To**: field-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `next_due = Field(type=float, null=True)` from `models/reflection.py`
- Remove `next_due=None` from `get_or_create()` defaults
- Update `ui/data/reflections.py:get_all_reflections()` to compute `next_due = state.last_run + config["interval"]` when `state.last_run is not None`
- Remove the duplicate `get_schedule()` computation (it duplicates `get_all_reflections()` logic)
- Update tests to reflect computed next_due

### 4. Remove log_path dead code
- **Task ID**: build-logpath-cleanup
- **Depends On**: build-model-split
- **Validates**: `tests/unit/test_ui_reflections_data.py`
- **Assigned To**: field-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `log_path` from `Reflection` docstring run_history description
- Remove `get_log_content()` function from `ui/data/reflections.py`
- Remove `log_path` / `log_content` logic from `get_run_detail()` in `ui/data/reflections.py`
- Remove `_read_log_file()` helper from `ui/data/reflections.py`
- Remove any template references to log viewing

### 5. Rename timestamp fields to datetime convention
- **Task ID**: build-timestamp-rename
- **Depends On**: build-model-split
- **Validates**: `tests/unit/test_reflection_model.py`, `tests/integration/test_reflections_redis.py`
- **Assigned To**: field-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename `Reflection.last_run` to `ran_at`, change type to datetime
- Update all read/write sites for `ran_at` (scheduler, dashboard, tests)
- Rename `ReflectionRun.started_at` type to datetime (name already follows convention)
- Rename `ReflectionIgnore.created_at` and `expires_at` types to datetime
- Rename `PRReviewAudit.audited_at` type to datetime
- Add float->datetime coercion for backward compatibility with existing Redis data

### 6. Externalize ReflectionRun large payloads
- **Task ID**: build-output-externalize
- **Depends On**: build-model-split, build-timestamp-rename
- **Validates**: `tests/integration/test_reflections_redis.py`
- **Assigned To**: field-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `findings`, `session_analysis`, `daily_report` fields in `ReflectionRun` with `output_path = Field(null=True)`
- Update `scripts/reflections.py` to write payload to `logs/reflections/{date}.json` and store only the path
- Update `save_checkpoint()` to handle the new field
- Create `logs/reflections/` directory if missing on write

### 7. Rename ReflectionRun.reflections to session_observations
- **Task ID**: build-field-rename
- **Depends On**: build-model-split
- **Validates**: `tests/integration/test_reflections_redis.py`
- **Assigned To**: field-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `reflections` field to `session_observations` in `ReflectionRun` model
- Update all read/write sites in `scripts/reflections.py`
- Update `save_checkpoint()` field list

### 8. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-scheduler-move, build-model-split, build-next-due, build-logpath-cleanup, build-timestamp-rename, build-output-externalize, build-field-rename
- **Assigned To**: reflections-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_reflection_model.py tests/unit/test_reflection_scheduler.py tests/unit/test_ui_reflections_data.py tests/unit/test_reflections.py tests/unit/test_pr_review_audit.py -x`
- Verify `python -c "from models import Reflection, ReflectionRun, ReflectionIgnore, PRReviewAudit"` works
- Verify `python -c "from models.reflections import ReflectionRun, ReflectionIgnore, PRReviewAudit"` works (shim)
- Verify no `grep -rn "from models.reflections" --include="*.py" | grep -v reflections.py | grep -v __pycache__ | grep -v worktree` produces errors

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` per Documentation section checklist
- Update `docs/features/reflections-dashboard.md`
- Update `config/reflections.yaml` header comment
- Update model docstrings

### 10. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: reflections-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_reflection_model.py tests/unit/test_reflection_scheduler.py tests/unit/test_ui_reflections_data.py tests/unit/test_reflections.py tests/unit/test_pr_review_audit.py tests/integration/test_reflections_redis.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check models/reflection.py models/reflection_run.py models/reflection_ignore.py models/pr_review_audit.py models/reflections.py ui/data/reflections.py agent/reflection_scheduler.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/ ui/data/reflections.py agent/reflection_scheduler.py` | exit code 0 |
| Models importable (new paths) | `python -c "from models.reflection_run import ReflectionRun; from models.reflection_ignore import ReflectionIgnore; from models.pr_review_audit import PRReviewAudit"` | exit code 0 |
| Models importable (shim) | `python -c "from models.reflections import ReflectionRun, ReflectionIgnore, PRReviewAudit"` | exit code 0 |
| next_due not a field | `python -c "from models.reflection import Reflection; assert not hasattr(Reflection, 'next_due') or not isinstance(getattr(Reflection, 'next_due'), type(Reflection.name))"` | exit code 0 |
| Scheduler not in bridge | `grep -c 'ReflectionScheduler' bridge/telegram_bridge.py` | exit code 1 |
| Scheduler in worker | `grep -c 'ReflectionScheduler' worker/__main__.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-13. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | Output externalization (Task 6) understates complexity: `scripts/reflections.py` builds `findings`, `session_analysis`, `daily_report`, `reflections` incrementally across 18 steps via `ReflectionsState` dataclass (lines 2946-2948) and flushes to Redis via `save()` (line 2964-2970). Builder must keep in-memory dicts and flush to `logs/reflections/{date}.json` only at checkpoint, not replace fields outright. | build-output-externalize | `ReflectionsState` keeps its four dict/list fields in memory as-is. `ReflectionsState.save()` writes `{findings, session_analysis, daily_report, reflections}` to `logs/reflections/{date}.json` and stores only `output_path` in the `ReflectionRun` Redis record. `_load_state()` reverses: reads `output_path`, loads JSON, populates the dataclass. `save_checkpoint()` in the model must mirror this -- write JSON first, then persist path. |
| CONCERN | Skeptic, Adversary | `scripts/docs_auditor.py` (lines 960, 972) imports `ReflectionRun` from `models.reflections` but is not listed in Test Impact or any task. Model split must cover this import site or the shim must re-export correctly. | build-model-split | The backward-compatible shim in `models/reflections.py` already covers this (`from models.reflection_run import ReflectionRun`). Add `scripts/docs_auditor.py` to Task 2's import-site verification grep and to Test Impact as an UPDATE note. |
| CONCERN | Operator | No post-deploy verification that the scheduler actually started in the worker. If the import or `asyncio.create_task` fails silently, reflections stop running with no alert. | build-scheduler-move | Add a log line grep to Verification table: `grep -c 'Reflection scheduler started' logs/worker.log` expects output > 0. The scheduler's `start()` already emits `[reflection] Scheduler started with N reflection(s)` (line 477). Also add the scheduler start inside a try/except with `logger.error` matching the bridge pattern at line 2058. |
| CONCERN | Skeptic | Timestamp rename `last_run` -> `ran_at` has 5+ read sites in `agent/reflection_scheduler.py` (`is_reflection_due` line 196, `tick()` stuck-detection line 420, `get_status()` line 507-508, `mark_started()` sets it at `models/reflection.py:84`). Task 5 says "update all read/write sites" but does not enumerate the scheduler file explicitly. | build-timestamp-rename | Explicit list: rename in `models/reflection.py` field + `mark_started()` (line 84) + `get_or_create()` defaults (line 72) + `cleanup_expired()` (line 144). Rename in `agent/reflection_scheduler.py`: `is_reflection_due()` (line 196-198), `tick()` stuck check (lines 420-421), `get_status()` (line 507-508). Rename in `ui/data/reflections.py`: `get_all_reflections()` (line 65). Rename in `scripts/reflections.py`: `ReflectionsState` references. |
| NIT | Simplifier | Two-builder orchestration (model-split-builder, field-cleanup-builder) adds coordination overhead. The dependency graph already serializes phases; a single builder following task order is simpler. |
| NIT | User | Success criteria are all technical (tests pass, lint clean, grep checks). No user-facing verification that the dashboard actually renders correct next-due values. Consider adding a manual or automated check of `curl localhost:8500/dashboard.json` post-deploy. |

---

## Open Questions

No open questions -- the issue scope is fully defined with all claims verified against current code. The solution is a mechanical refactoring with no design decisions requiring human input.
