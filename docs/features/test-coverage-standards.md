# Test Coverage Standards

Standards and tooling for preventing silent failure classes in the test suite. Addresses five categories of coverage gaps discovered through production incident analysis.

## Problem

Several classes of bugs can pass all existing tests while silently degrading the system:

1. **Silent exception swallowing** -- `except Exception: pass` blocks hide failures with no logging or observable side effects
2. **Empty output loops** -- empty agent output classified as STATUS_UPDATE triggers infinite auto-continue cycles
3. **Coupled test logic** -- tests that replicate production routing logic inline rather than calling the production function, meaning tests pass even when production behavior changes
4. **Missing error state rendering** -- summarizer tests only cover success paths, not failure/error rendering
5. **Silent build completion** -- builders that produce zero commits complete without any user-visible warning

## Solution

### Gap 1: Exception Logging (agent/job_queue.py)

Replaced 7 `except Exception: pass` blocks in critical job queue functions with `except Exception as e: logger.warning(...)` calls. Each warning includes identifying context (session_id, file path, workflow_id) for debugging.

**Functions covered:**
- `_push_job` -- lifecycle transition logging
- `_pop_job` -- lifecycle transition logging
- `_enqueue_continuation` -- plan file resolution from WorkflowState
- `_execute_job` -- session re-read from Redis
- `_load_cooldowns` -- file read
- `_save_cooldowns` -- file write
- `check_revival` -- branch existence check

**Test approach:** `tests/test_silent_failures.py` uses `caplog` to assert warnings are emitted when exceptions occur. Assertions check log level and presence of key identifiers -- not exact message text -- to avoid brittle tests.

### Gap 2: Empty Output Anomaly Detection (agent/job_queue.py)

Extracted the empty output guard into a pure function `should_guard_empty_output(msg, is_sdlc, has_remaining_stages) -> bool` in `agent/job_queue.py`. The function returns True when output is empty/whitespace AND the job is SDLC with remaining stages. The production `send_to_chat` closure calls this function, and tests call it directly — following the same extraction pattern used for Gap 3.

When the guard triggers, the empty output is delivered to the user with a "(empty output)" placeholder instead of being classified as STATUS_UPDATE and auto-continued.

**Tests:** `TestEmptyOutputAnomalyDetection` in `tests/test_auto_continue.py` (5 unit tests calling `should_guard_empty_output` directly + 1 async test for `classify_output` behavior) and `TestEmptyOutputLoopTermination` in `tests/test_enqueue_continuation.py`.

### Gap 3: Routing Decision Extraction (agent/job_queue.py)

Extracted the 3-branch routing logic from `_execute_job`'s closure into a standalone pure function `classify_routing_decision()` with a `RoutingDecision` result type. The function takes classification, auto_continue_count, effective_max, is_sdlc, and msg as inputs and returns one of three actions: `AUTO_CONTINUE`, `DELIVER`, or `ERROR_BYPASS`.

**Tests:** `TestClassifyRoutingDecision` in `tests/test_auto_continue.py` with 10 test cases covering all branches, boundary conditions, and edge cases.

### Gap 4: Error State Rendering (bridge/summarizer.py)

Added 7 tests for error/failure rendering paths in `_compose_structured_summary`:
- Failed session emoji rendering
- Failed session with completion flag (error takes precedence)
- Failed stage in stage progress display
- Error message propagation to output
- Failed session with link footer
- `_get_status_emoji` with failed status
- `_render_stage_progress` with failed stages

**Tests:** `TestErrorStateRendering` in `tests/test_summarizer.py`.

### Gap 5: Build Output Validation

Tests documenting the expected behavior when builders produce no commits:
- Warning should be surfaced (not silent success)
- Pipeline should NOT hard-block (config-only changes are legitimate)
- Commit count detection via `git log --oneline main..HEAD`

**Tests:** `tests/test_build_validation.py` with `TestBuildOutputVerification` and `TestBuildValidationIntegration`.

## Skill Documentation Updates

### Plan Template (do-plan)

Added a **Failure Path Test Strategy** section to the plan template (`.claude/skills/do-plan/PLAN_TEMPLATE.md`) with three subsections:
- **Exception Handling Coverage** -- identify `except Exception: pass` blocks and require corresponding tests
- **Empty/Invalid Input Handling** -- document empty/None/whitespace behavior
- **Error State Rendering** -- test failure rendering paths, not just success

### Test Skill (do-test)

Added a **Quality Checks (Post-Test)** section to the test skill (`.claude/skills/do-test/SKILL.md`) with three automated scans:
- **Exception Swallow Scan** -- grep for bare exception handlers without logging
- **Empty Input Check** -- verify empty input edge cases are tested
- **Closure Coverage Flag** -- detect untested inner functions/closures

## Validation

Run the full test suite to verify all coverage standards are met:

```bash
python -m pytest tests/test_silent_failures.py tests/test_build_validation.py tests/test_auto_continue.py tests/test_enqueue_continuation.py tests/test_summarizer.py -v
```

Verify no bare exception handlers remain in critical paths:

```bash
grep -rn "except.*Exception.*:" --include="*.py" agent/ bridge/ | grep -v "logger\|log\.\|warning\|error\|raise\|# .*tested" | head -20
```

## Files Changed

| File | Change |
|------|--------|
| `agent/job_queue.py` | Replace 7 silent exception handlers with logger.warning; extract `should_guard_empty_output()` and `classify_routing_decision()` |
| `tests/test_silent_failures.py` | New: 8 test classes for Gap 1 exception logging |
| `tests/test_auto_continue.py` | Add `TestClassifyRoutingDecision` (10 tests) and `TestEmptyOutputAnomalyDetection` (6 tests calling production `should_guard_empty_output`) |
| `tests/test_enqueue_continuation.py` | Add `TestEmptyOutputLoopTermination` (3 tests) |
| `tests/test_summarizer.py` | Add `TestErrorStateRendering` (7 tests) |
| `tests/test_build_validation.py` | New: 6 tests for build output verification |
| `.claude/skills/do-plan/PLAN_TEMPLATE.md` | Add Failure Path Test Strategy section |
| `.claude/skills/do-test/SKILL.md` | Add Quality Checks (Post-Test) section |
