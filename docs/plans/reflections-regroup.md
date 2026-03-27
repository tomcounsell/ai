---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-03-27
tracking: https://github.com/tomcounsell/ai/issues/566
last_comment_id:
---

# Reflections Regroup: 19 Steps to 14 Units

## Problem

The Reflections system runs 19 steps in a fixed sequential order, but most steps are mutually independent. Three pairs of steps have real data dependencies but are interleaved with unrelated steps. One step (Sentry check) is a permanent no-op. The docs claim "16 steps" but the code has 19.

**Current behavior:**
- 19 sequential steps, no grouping by dependency
- `completed_steps` stores integer step numbers, making any reordering a data migration
- `step_check_sentry` always logs "skipped" and returns
- `_preflight_check` uses hardcoded integer step sets (`{8, 10}`, `range(1, 20)`)
- Docs at `docs/features/reflections.md` describe a "16-step pipeline"

**Desired outcome:**
- 14 clearly-defined units: 11 independent items + 3 merged pipelines
- Dead code removed
- `completed_steps` uses string step keys instead of integers (no future data migrations)
- Documentation accurately reflects the 14-unit structure

## Prior Art

- **PR #245**: Refactor daydream to reflections -- original rename, established current structure
- **PR #389**: Reflections as first-class objects with unified scheduler -- added Redis-backed state and resumability with integer-based `completed_steps`
- **PR #386**: Add template reflection step and developer guide -- established step template pattern (step 19, disk space check)
- **PR #259**: Remove LessonLearned, add branch & plan cleanup step -- prior step cleanup work
- **PR #511**: Unified Web UI with reflections dashboard -- reads step data for display

## Data Flow

1. **Entry point**: `ReflectionRunner.__init__` builds `self.steps` list of `(int, str, callable)` tuples
2. **Runner loop** (`run()`): iterates `self.steps`, checks `completed_steps` for skip, calls `_preflight_check`, executes step, appends step number to `completed_steps`
3. **State persistence**: `ReflectionsState.save()` writes to Redis `ReflectionRun` model via delete-and-recreate pattern
4. **Resume**: `_load_state()` reads `ReflectionRun` for today's date, wraps in `ReflectionsState` dataclass
5. **Dependent pipelines**:
   - Steps 6->7->8: `step_session_analysis` writes `state.session_analysis` -> `step_llm_reflection` reads it and writes `state.reflections` -> `step_auto_fix_bugs` reads `state.reflections`
   - Steps 16->17: `step_episode_cycle_close` creates `CyclicEpisode` records -> `step_pattern_crystallization` queries them
   - Steps 9->10->telegram: `step_produce_report` aggregates `state.findings` -> `step_create_github_issue` reads findings per project and calls `step_post_to_telegram`

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on string key naming)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies beyond what already exists.

## Solution

### Key Elements

- **Step list restructuring**: Reduce 19 tuples to 14, using string keys instead of integers
- **Pipeline methods**: 3 new composite methods that call sub-steps internally
- **State migration**: Switch `completed_steps` from `list[int]` to `list[str]`, with graceful handling of old integer data
- **Dead code removal**: Delete `step_check_sentry` method entirely

### Flow

Runner init builds 14-item step list -> Run loop iterates with string keys -> Each pipeline method calls sub-steps in sequence internally -> State checkpoint after each unit

### Technical Approach

- Change step tuple format from `(int, str, callable)` to `(str, str, callable)` where first element is the step key (e.g. `"legacy_code_scan"`)
- The runner loop uses string keys for `completed_steps` tracking and `_preflight_check`
- Three new pipeline methods:
  - `step_session_intelligence()` = session_analysis + llm_reflection + auto_fix_bugs
  - `step_behavioral_learning()` = episode_cycle_close + pattern_crystallization
  - `step_daily_report_and_notify()` = produce_report + create_github_issue (which already calls post_to_telegram)
- `_preflight_check` switches from integer sets to string key sets for gh CLI checks
- `ReflectionsState.completed_steps` type changes from `list[int]` to `list[str]`
- On load, if `completed_steps` contains integers, treat as a stale run and reset to empty list (safe because reflections run daily and a partial-day reset just re-runs some steps)
- `current_step` field changes from integer to string (or becomes unused -- the completed_steps list is what drives skip logic)

### The 14 Units

| # | Key | Name | Source Steps |
|---|-----|------|-------------|
| 1 | `legacy_code_scan` | Clean Up Legacy Code | 1 |
| 2 | `log_review` | Review Previous Day's Logs | 2 |
| 3 | `task_management` | Clean Up Task Management | 4 |
| 4 | `documentation_audit` | Audit Documentation | 5 |
| 5 | `skills_audit` | Skills Audit | 11 |
| 6 | `redis_ttl_cleanup` | Redis TTL Cleanup | 12 |
| 7 | `redis_data_quality` | Redis Data Quality | 13 |
| 8 | `branch_plan_cleanup` | Branch and Plan Cleanup | 14 |
| 9 | `feature_docs_audit` | Feature Docs Audit | 15 |
| 10 | `principal_staleness` | Principal Context Staleness | 18 |
| 11 | `disk_space_check` | Disk Space Check | 19 |
| 12 | `session_intelligence` | Session Intelligence | 6+7+8 |
| 13 | `behavioral_learning` | Behavioral Learning | 16+17 |
| 14 | `daily_report_and_notify` | Daily Report & Notify | 9+10+telegram (must be last) |

**Removed:** Step 3 (`step_check_sentry`) -- permanent no-op

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The runner loop already has a per-step `try/except` that logs and continues -- no changes needed to that pattern
- [ ] Pipeline methods (session_intelligence, behavioral_learning, daily_report_and_notify) must NOT add inner try/except -- let the outer runner catch failures so partial pipeline failures are visible

### Empty/Invalid Input Handling
- [ ] Old integer `completed_steps` data: gracefully reset to empty list on load (tested)
- [ ] Mixed-type `completed_steps` (e.g. `[1, "legacy_code_scan"]`): treat as stale, reset

### Error State Rendering
- [ ] No user-visible UI changes -- reflections output goes to logs and GitHub issues

## Test Impact

- [ ] `tests/integration/test_reflections_redis.py::TestReflectionRunModel::test_create_and_query` -- UPDATE: change `completed_steps=[1, 2]` to `["legacy_code_scan", "log_review"]`
- [ ] `tests/integration/test_reflections_redis.py::TestReflectionRunModel::test_load_or_create_existing` -- UPDATE: change `completed_steps=[1, 2, 3, 4]` to string keys
- [ ] `tests/integration/test_reflections_redis.py::TestReflectionRunModel::test_save_checkpoint` -- UPDATE: change `completed_steps=[1, 2, 3, 4, 5, 6]` to string keys
- [ ] `tests/integration/test_reflections_redis.py::TestReflectionsStateSave::test_save_to_redis` -- UPDATE: change `completed_steps=[1, 2, 3, 4]` to string keys
- [ ] `tests/integration/test_reflections_redis.py::TestRedisDataQuality::test_step_registered_as_step_13` -- UPDATE: change assertion from `step_names.get(13) == "Redis Data Quality"` to key-based lookup
- [ ] `tests/unit/test_reflections_preflight.py::TestReflectionsPreflight::test_preflight_fails_without_gh_cli` -- UPDATE: change step number 8 to string key `"session_intelligence"` (or whichever key maps to gh-dependent steps)
- [ ] `tests/unit/test_reflections_preflight.py::TestReflectionsPreflight::test_preflight_passes_with_gh_cli` -- UPDATE: same as above

## Rabbit Holes

- **Parallel execution of independent steps**: Not in scope. The sequential runner is fine; parallelism can be added later if performance matters. Do not add asyncio.gather or threading.
- **Renaming `ReflectionsState` fields**: Keep the dataclass fields as-is (session_analysis, reflections, etc.) -- only change `completed_steps` type.
- **Redis schema migration tool**: Do not build a general migration framework. The graceful reset on integer detection is sufficient.
- **Changing the `ReflectionRun` Popoto model fields**: The `completed_steps` ListField already accepts any list items. The `current_step` IntField may need to change to a plain Field to hold string keys, but keep changes minimal.

## Risks

### Risk 1: Web UI reads step data
**Impact:** If the web UI (`ui/app.py` or dashboard) reads `completed_steps` as integers, it could break.
**Mitigation:** Search for all consumers of `completed_steps` and `current_step` before implementing. The dashboard likely just displays counts, not individual step numbers.

### Risk 2: In-flight run during deployment
**Impact:** If reflections is running when the new code deploys, the current run has integer completed_steps and the new code expects strings.
**Mitigation:** The graceful reset handles this -- worst case, a few steps re-run on the same day, which is harmless.

## Race Conditions

No race conditions identified -- reflections runs as a single sequential process with no concurrent access to state. The launchd plist ensures only one instance runs at a time.

## No-Gos (Out of Scope)

- Parallel step execution (future work)
- Changing any step's internal logic -- only restructure groupings
- Modifying `scripts/reflections_report.py` or `scripts/docs_auditor.py`
- Changing the launchd plist (just re-enable after merge)
- Adding new reflection steps

## Update System

No update system changes required -- this refactors internal structure of an existing script. The launchd plist path and invocation command (`python scripts/reflections.py`) remain unchanged.

## Agent Integration

No agent integration required -- reflections is a standalone maintenance script invoked by launchd, not by the agent or MCP tools.

## Documentation

- [ ] Update `docs/features/reflections.md` to describe the 14-unit structure instead of "16-step pipeline"
- [ ] Update the module docstring at top of `scripts/reflections.py` to list the 14 units
- [ ] Verify `docs/features/README.md` entry for reflections is still accurate

## Success Criteria

- [ ] `ReflectionRunner.steps` list contains exactly 14 entries
- [ ] `step_check_sentry` method is deleted with no references remaining
- [ ] `step_session_intelligence` merges session analysis + LLM reflection + bug filing
- [ ] `step_behavioral_learning` merges episode cycle-close + pattern crystallization
- [ ] `step_daily_report_and_notify` merges report + GitHub issues + Telegram, is last in steps
- [ ] `completed_steps` stores string keys (e.g. `["legacy_code_scan", "log_review"]`)
- [ ] Old integer `completed_steps` data handled gracefully (reset, no crash)
- [ ] `_preflight_check` uses string keys instead of hardcoded integer sets
- [ ] Module docstring lists 14 units accurately
- [ ] `docs/features/reflections.md` updated to 14-unit structure
- [ ] All existing reflections tests pass or are updated
- [ ] `python scripts/reflections.py --dry-run` completes all 14 steps without error
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (reflections-restructure)**
  - Name: reflections-builder
  - Role: Restructure step list, create pipeline methods, update state tracking
  - Agent Type: builder
  - Resume: true

- **Validator (reflections-verify)**
  - Name: reflections-validator
  - Role: Verify step count, test updates, dry-run success
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using builder + validator pair. Single component, straightforward restructuring.

## Step by Step Tasks

### 1. Restructure ReflectionRunner
- **Task ID**: build-restructure
- **Depends On**: none
- **Validates**: tests/unit/test_reflections_preflight.py, tests/integration/test_reflections_redis.py
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `step_check_sentry` method entirely
- Create `step_session_intelligence` that calls session_analysis, llm_reflection, auto_fix_bugs in sequence
- Create `step_behavioral_learning` that calls episode_cycle_close, pattern_crystallization in sequence
- Create `step_daily_report_and_notify` that calls produce_report, create_github_issue in sequence
- Change step tuple format from `(int, str, callable)` to `(str, str, callable)` with string keys
- Update `self.steps` list to 14 entries with `daily_report_and_notify` last
- Update runner loop in `run()` to use string keys for completed_steps tracking
- Update `_preflight_check` to use string key sets instead of integer sets
- Update `ReflectionsState.completed_steps` type annotation from `list[int]` to `list[str]`
- Add graceful migration in `_load_state`: if completed_steps contains integers, reset to empty list
- Update `current_step` handling (change from int to string or remove if redundant)
- Update module docstring to list 14 units

### 2. Update Tests
- **Task ID**: build-tests
- **Depends On**: build-restructure
- **Validates**: pytest tests/unit/test_reflections_preflight.py tests/integration/test_reflections_redis.py
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: false
- Update all 7 test assertions that use integer completed_steps to use string keys
- Update `test_step_registered_as_step_13` to use key-based lookup
- Update preflight tests to use string step keys instead of integer step numbers
- Add test for graceful migration of integer completed_steps to empty list

### 3. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: reflections-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `ReflectionRunner.steps` has exactly 14 entries
- Verify `step_check_sentry` is fully deleted (no references)
- Verify pipeline methods exist and call sub-methods
- Run `python scripts/reflections.py --dry-run` and confirm 14 steps complete
- Run full test suite for reflections tests
- Verify all success criteria met

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: reflections-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` with 14-unit table
- Verify `docs/features/README.md` entry is accurate

### 5. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: reflections-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met including documentation
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_reflections_preflight.py tests/unit/test_reflections.py tests/integration/test_reflections_redis.py -x -q` | exit code 0 |
| Step count | `python -c "from scripts.reflections import ReflectionRunner; r = ReflectionRunner(); print(len(r.steps))"` | output contains 14 |
| No sentry refs | `grep -r 'step_check_sentry\|check_sentry' scripts/reflections.py` | exit code 1 |
| Lint clean | `python -m ruff check scripts/reflections.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/reflections.py` | exit code 0 |
| Dry run | `python scripts/reflections.py --dry-run 2>&1 \| tail -5` | output contains Reflections completed |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **current_step field**: The `ReflectionRun` model has `current_step = IntField()`. Changing it to store string keys requires changing the field type to `Field(type=str)`. Alternatively, `current_step` could be dropped entirely since `completed_steps` is what actually drives skip logic. Which approach do you prefer -- keep as string, or drop it?
