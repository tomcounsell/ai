---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-03-07
tracking: https://github.com/valorengels/ai/issues/293
---

# Test Coverage Gaps: Silent SDLC Failures and Response Template Rendering

## Problem

After fixing a series of session/classification issues (#276, #279, #280, #283, #285), a test review revealed structural gaps in end-to-end test coverage. The existing tests cover routing decisions, session reuse, and response formatting, but miss failure modes where SDLC processes die silently or produce empty/malformed output.

**Current behavior:**
- `except Exception: pass` blocks in `agent/job_queue.py` swallow failures in session metadata updates, lifecycle logging, and plan file resolution without any signal
- Empty agent output is classified as `STATUS_UPDATE` with confidence 1.0, triggering up to 10 silent auto-continues of an agent that produced nothing
- Auto-continue tests replicate routing logic inline rather than calling the actual `send_to_chat` closure, masking divergence between test and production code
- `_compose_structured_summary` in `bridge/summarizer.py` is tested for success and in-progress states but not error/failure states
- `/do-build` trusts sub-agents to produce output without validating commits or PR creation

**Desired outcome:**
- All 5 identified gaps have corresponding tests that catch regressions
- Silent exception swallowing is replaced with `logger.warning()` calls that tests can assert on
- Empty agent output triggers a detectable anomaly, not silent auto-continue
- The `/do-plan` and `/do-test` skills are updated to prevent similar gaps in future work

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are to existing test files, production code, and skill documentation.

## Solution

### Key Elements

- **Exception logging upgrade**: Convert critical `except Exception: pass` blocks in `job_queue.py` to `except Exception: logger.warning(...)` so failures are observable
- **Empty output anomaly detection**: Add a guard in the classify/auto-continue path that treats empty agent output as an anomaly rather than a status update
- **Error state test coverage**: Add tests for `_compose_structured_summary` failure rendering (the `X` emoji path, failed stage progress lines)
- **Skill guidance updates**: Add failure path coverage requirements to `/do-plan` and exception swallow scanning to `/do-test`

### Flow

**Test gaps identified** -> Add tests for each gap -> Fix production code where tests reveal real bugs -> Update skills to prevent recurrence -> Validate all tests pass

### Technical Approach

- **Gap 1 (exception swallowing)**: Replace `pass` with `logger.warning()` in the 6 critical locations identified in the issue (lines ~319, 358, 990, 1200, 1580, 1592 of `job_queue.py`). Write tests that use `caplog` or `unittest.mock.patch` on the logger to assert warnings are emitted when exceptions occur in these paths.

- **Gap 2 (empty output loop)**: Add a check in the auto-continue decision path (near line 1282 in `job_queue.py` where `classify_output` is called) that detects empty/whitespace output and either classifies it as an anomaly or increments a separate empty-output counter that terminates the loop early. Test with empty string, whitespace-only, and None inputs.

- **Gap 3 (mocked routing logic)**: Extract the routing logic from `_execute_job`'s `send_to_chat` closure into a standalone testable function. Update `test_auto_continue.py` to call this function instead of replicating the logic. This is a refactor -- no behavior change.

- **Gap 4 (error state rendering)**: Add test cases to `test_summarizer.py` that exercise `_compose_structured_summary` with a session in error state. Verify the failure emoji renders, the stage progress shows the failure point, and error messages reach the output.

- **Gap 5 (silent build failure)**: This is a skill-level concern. Add validation guidance to the `/do-build` skill noting that builders must verify commits exist before reporting success. Add a test that mocks a builder returning without commits and asserts an error is raised.

## Rabbit Holes

- **Refactoring all 20+ exception handlers in job_queue.py**: The issue identifies 6 critical ones. Many others are genuinely non-fatal (e.g., cleanup during shutdown). Only convert the 6 identified critical blocks. A full audit is a separate chore.
- **Building a generic exception swallow scanner as a pytest plugin**: The `/do-test` skill guidance is sufficient. A formal tool is overengineering.
- **Rewriting the auto-continue system**: The goal is to add a guard for empty output, not redesign the continuation mechanism.

## Risks

### Risk 1: Extracting the routing closure breaks production behavior
**Impact:** Auto-continue routing diverges between test and production code
**Mitigation:** Extract as a pure function with the same signature. Run existing tests before and after to confirm no behavior change. If extraction proves risky, keep the closure but add an integration-style test that calls `_execute_job` directly.

### Risk 2: Logger warning assertions are brittle
**Impact:** Tests break on log message changes
**Mitigation:** Assert on log level and presence of key identifiers (e.g., the session_id), not exact message text.

## Race Conditions

No race conditions identified. All changes are to test files, logging statements, and skill documentation. The empty output guard is a synchronous check in an already-sequential code path.

## No-Gos (Out of Scope)

- Full audit of all exception handlers in job_queue.py beyond the 6 identified critical ones
- Redesigning the auto-continue mechanism
- Adding coverage metrics or coverage enforcement tooling
- Modifying the Claude Agent SDK client behavior
- Changes to the Telegram bridge protocol

## Update System

No update system changes required -- this is purely internal test coverage and skill documentation. No new dependencies, no config file changes, no migration steps.

## Agent Integration

No agent integration required -- this is a test coverage and skill documentation change. No new MCP servers, no changes to `.mcp.json`, no bridge protocol changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/README.md` index table with entry for test coverage standards
- [ ] Create `docs/features/test-coverage-standards.md` describing the failure path coverage requirements added to `/do-plan` and `/do-test`

### Inline Documentation
- [ ] Code comments on the empty output guard explaining why empty output is treated as an anomaly
- [ ] Docstrings on any extracted routing function

## Success Criteria

- [ ] All 5 gaps have corresponding tests that pass
- [ ] Empty SDLC agent output triggers a user-visible error, not silent auto-continue
- [ ] `/do-plan` template includes failure path test requirements
- [ ] `/do-test` warns on `except.*pass` patterns without test coverage
- [ ] No `except Exception: pass` in job_queue.py critical paths without at minimum a `logger.warning()`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (test-gaps)**
  - Name: test-gap-builder
  - Role: Implement new tests for all 5 gaps and fix production code (logger upgrades, empty output guard)
  - Agent Type: builder
  - Resume: true

- **Builder (skill-updates)**
  - Name: skill-updater
  - Role: Update `/do-plan` and `/do-test` skill docs with failure path coverage requirements
  - Agent Type: builder
  - Resume: true

- **Validator (coverage)**
  - Name: coverage-validator
  - Role: Verify all 5 gaps have passing tests and production fixes are correct
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix exception swallowing in job_queue.py (Gap 1)
- **Task ID**: build-exception-logging
- **Depends On**: none
- **Assigned To**: test-gap-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `except Exception: pass` with `except Exception: logger.warning(...)` at lines ~319, 358, 990, 1200, 1580, 1592 in `agent/job_queue.py`
- Create `tests/test_silent_failures.py` with tests asserting logger warnings are emitted
- Verify exceptions in these paths do not corrupt session state

### 2. Add empty output anomaly detection (Gap 2)
- **Task ID**: build-empty-output-guard
- **Depends On**: none
- **Assigned To**: test-gap-builder
- **Agent Type**: builder
- **Parallel**: true
- Add empty/whitespace output detection near classify_output call (~line 1282 in `agent/job_queue.py`)
- Add tests in `tests/test_auto_continue.py` for empty output + SDLC interaction
- Add tests in `tests/test_enqueue_continuation.py` for loop termination on empty output

### 3. Fix auto-continue test coupling (Gap 3)
- **Task ID**: build-routing-extraction
- **Depends On**: none
- **Assigned To**: test-gap-builder
- **Agent Type**: builder
- **Parallel**: true
- Extract routing logic from `_execute_job` closure into a standalone function
- Update `tests/test_auto_continue.py` to call the extracted function
- Verify existing tests pass unchanged

### 4. Add error state rendering tests (Gap 4)
- **Task ID**: build-error-rendering
- **Depends On**: none
- **Assigned To**: test-gap-builder
- **Agent Type**: builder
- **Parallel**: true
- Add tests to `tests/test_summarizer.py` for `_compose_structured_summary` with error states
- Test failure emoji rendering, failed stage progress lines, error message propagation

### 5. Add silent build failure test (Gap 5)
- **Task ID**: build-build-validation
- **Depends On**: none
- **Assigned To**: test-gap-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tests/test_build_validation.py` testing the path where a builder returns without commits/PR
- Document in the test what the expected behavior should be

### 6. Update skill documentation (Goal 2)
- **Task ID**: build-skill-updates
- **Depends On**: none
- **Assigned To**: skill-updater
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills/do-plan/PLAN_TEMPLATE.md` to add failure path test strategy requirements
- Update `.claude/skills/do-test/SKILL.md` to add exception swallow scanning, empty input checks, closure coverage flags

### 7. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-exception-logging, build-empty-output-guard, build-routing-extraction, build-error-rendering, build-build-validation, build-skill-updates
- **Assigned To**: coverage-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/`
- Verify no `except Exception: pass` remains in critical paths
- Verify skill docs contain required sections
- Generate final report

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: skill-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/test-coverage-standards.md`
- Add entry to `docs/features/README.md` index table

## Validation Commands

- `pytest tests/test_silent_failures.py -v` - validates Gap 1 exception logging tests
- `pytest tests/test_auto_continue.py -v` - validates Gap 2 and Gap 3 tests
- `pytest tests/test_enqueue_continuation.py -v` - validates Gap 2 loop termination
- `pytest tests/test_summarizer.py -v` - validates Gap 4 error rendering tests
- `pytest tests/test_build_validation.py -v` - validates Gap 5 build validation tests
- `grep -n "except Exception:" agent/job_queue.py | grep "pass"` - should return 0 results for critical paths
- `pytest tests/ -v` - full suite passes

---

## Open Questions

1. **Gap 3 extraction scope**: Should the routing logic extraction from `_execute_job` be a minimal extraction (just the if/elif/else block) or should it also extract the auto-continue counter logic? Minimal extraction is safer but the counter logic is tightly coupled.

2. **Empty output threshold**: For Gap 2, should empty output immediately terminate the auto-continue loop, or should it allow 1-2 retries before terminating? Immediate termination is simpler but a transient empty response from the agent API could be a fluke.

3. **Gap 5 enforcement level**: The issue describes `/do-build` validating commits exist. Should this be a hard error that blocks the build, or a warning that the human sees? A hard error could cause false positives if a builder legitimately makes no file changes (e.g., a config-only change via API).
