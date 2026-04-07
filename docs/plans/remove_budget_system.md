---
status: Complete
type: chore
appetite: Small
owner: Valor
created: 2026-03-21
tracking: https://github.com/tomcounsell/ai/issues/458
last_comment_id:
---

# Remove max_budget_usd System

## Problem

Every Claude Code session launched via ValorAgent includes a `$5.00` hard budget cap (`max_budget_usd`). When the cap is hit, the SDK emits `stop_reason="budget_exceeded"`, the Observer treats it as terminal, and partial output is delivered to Telegram — even if the agent was mid-task and making real progress.

**Current behavior:**
The budget system kills productive sessions that happen to exceed an arbitrary $5 threshold. In the planned single-session SDLC architecture, most full pipelines cost $2.50–4.50 for a single stage — making the $5 cap unworkable.

**Desired outcome:**
- No session is ever killed due to budget limits
- Activity-based stall detection (PR #451) remains the sole runaway-prevention mechanism
- All budget-related code, env vars, and test assertions are cleanly removed

## Prior Art

- **Issue #94 / PR #96**: SDK Modernization — introduced `max_budget_usd` with $5 default and `_COST_WARN_THRESHOLD` as part of the upgrade to SDK 0.1.35. No documented rationale for the $5 figure.
- **Issue #371 / PR #373**: Pass stop_reason to Observer — wired `budget_exceeded` into Observer deterministic routing as a terminal deliver action.
- **Issue #440 / PR #451**: Session watchdog and Observer reliability — introduced activity-based stall detection, which is the actual safety mechanism for runaway sessions.

## Data Flow

The budget system touches two data paths:

1. **Session launch**: `ValorAgent.__init__()` reads `SDK_MAX_BUDGET_USD` env var (default $5.00) → stores as `self.max_budget_usd` → passes to `ClaudeAgentOptions(max_budget_usd=...)` in `_create_options()` → SDK enforces the hard cap during execution
2. **Session termination**: SDK emits `stop_reason="budget_exceeded"` → stored in `_session_stop_reasons` registry → passed to `Observer.__init__(stop_reason=...)` → Observer.run() Phase 1 deterministic routing delivers immediately with "Worker budget exceeded" message → `PipelineStateMachine.classify_outcome()` also classifies non-end_turn stop_reasons as "fail"

Additionally, `_COST_WARN_THRESHOLD` ($0.50) triggers a `logger.warning` when a single query's cost exceeds it — this is a soft observability signal, not a hard cap.

## Architectural Impact

- **New dependencies**: None — this is pure removal
- **Interface changes**: `ValorAgent.__init__()` loses the `max_budget_usd` parameter. No external callers depend on this outside this repo.
- **Coupling**: Decreases coupling — removes a code path between sdk_client.py and Observer
- **Data ownership**: No change
- **Reversibility**: Trivially reversible — re-add the parameter and Observer branch

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a straightforward removal of ~20 lines across 4 files plus test updates.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **ValorAgent cleanup**: Remove `max_budget_usd` parameter, `SDK_MAX_BUDGET_USD` env var reading, and the `max_budget_usd=` line in `_create_options()`
- **Cost warning removal**: Remove `_COST_WARN_THRESHOLD` constant, `SDK_COST_WARN_THRESHOLD` env var, and the cost warning log branch
- **Observer cleanup**: Remove `budget_exceeded` deterministic routing branch and `budget_exceeded` from the `stop_is_terminal` check
- **Pipeline state cleanup**: Remove `budget_exceeded` from docstring/comment references (the `classify_outcome` logic is generic — it classifies any non-end_turn as fail, so no code change needed there)
- **Docs update**: Remove "Cost Budgeting" section from `docs/features/sdk-modernization.md`

### Flow

No new flow — this removes an existing flow path. After removal:

**Session launch** → no budget parameter → SDK runs without hard cap → session ends via `end_turn`, `rate_limited`, or stall detection timeout

### Technical Approach

- Pure deletion: remove budget-related code, env vars, constants, and test assertions
- Keep `stop_reason` plumbing intact (it handles `rate_limited` and other reasons)
- Keep `_COST_WARN_THRESHOLD` removal separate from the `stop_reason` pass-through — the cost warning is an independent observability feature being removed because it's tied to the budget mental model
- The `PipelineStateMachine.classify_outcome()` method classifies any non-`end_turn` stop_reason as "fail" generically — no budget-specific code to remove there, but update the docstring that mentions `budget_exceeded`

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is pure code removal

### Empty/Invalid Input Handling
- No new functions being added — removal only
- Verify that `ValorAgent.__init__()` still works without `max_budget_usd` parameter

### Error State Rendering
- No user-visible output changes — the "budget exceeded" message path is being removed entirely

## Test Impact

- [ ] `tests/unit/test_observer.py::TestStopReasonRouting::test_budget_exceeded_delivers` — DELETE: tests removed feature
- [ ] `tests/unit/test_stop_reason_observer.py::TestObserverStopReasonRouting::test_budget_exceeded_delivers_with_warning` — DELETE: tests removed feature
- [ ] `tests/unit/test_stop_reason_observer.py::TestObserverStopReasonRouting::test_read_session_includes_stop_reason` — UPDATE: change stop_reason from `budget_exceeded` to `rate_limited` (still tests stop_reason plumbing)
- [ ] `tests/unit/test_stop_reason_observer.py::TestStopReasonRegistry::test_get_stop_reason_returns_and_clears` — UPDATE: change test value from `budget_exceeded` to `rate_limited`
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_non_end_turn_is_fail` — UPDATE: change `budget_exceeded` example to another non-end_turn value (e.g., `"timeout"`)
- [ ] `tests/unit/test_observer.py::TestStopReasonRouting` — UPDATE: remove `test_budget_exceeded_delivers`, keep `test_rate_limited_steers` (line 315 reference also uses budget_exceeded — update to different stop_reason)

## Rabbit Holes

- **Replacing budget with per-stage cost tracking** — Budget is being removed entirely (#458 is explicit: no replacement mechanism). Per-stage cost tracking belongs to the SDLC redesign (#457) if ever needed.
- **Refactoring the entire stop_reason system** — The stop_reason plumbing is sound; only the budget_exceeded branch needs removal. Don't touch rate_limited or other stop_reason handling.
- **Removing cost logging entirely** — The `_COST_WARN_THRESHOLD` warning is being removed because it's budget-adjacent, but the underlying cost calculation in the query loop (turns, cost, duration) is useful observability. Keep the cost summary log line.

## Risks

### Risk 1: Runaway session without budget cap
**Impact:** A session could theoretically run indefinitely and accumulate high API costs
**Mitigation:** Activity-based stall detection (300s inactivity timeout from PR #451) is the primary safety mechanism and is unaffected by this change. The SDK also has internal rate limiting.

## Race Conditions

No race conditions identified — all changes are to synchronous initialization code and deterministic routing branches. No shared mutable state is affected.

## No-Gos (Out of Scope)

- No replacement budget mechanism — stall detection is the safety net
- No per-stage cost tracking — that's a separate concern for the SDLC redesign
- No changes to `rate_limited` handling — that remains useful
- No changes to the activity-based stall detection system
- No changes to the autoexperiment budget system (`scripts/autoexperiment.py`) — that's an independent budget for experiment loops, not session budgets

## Update System

No update system changes required — this removes code and env vars. Existing installations that set `SDK_MAX_BUDGET_USD` or `SDK_COST_WARN_THRESHOLD` in `.env` will simply have unused env vars, which is harmless.

## Agent Integration

No agent integration required — this is a bridge-internal change that removes a parameter from the SDK client. No MCP servers, tools, or bridge message handling are affected.

## Documentation

- [ ] Update `docs/features/sdk-modernization.md` — remove "Cost Budgeting" section
- [ ] Update `docs/guides/sdlc-storyline-example.md` — remove or rewrite the "budget exceeded mid-build" failure scenario

### Inline Documentation
- [ ] Update docstring in `PipelineStateMachine.classify_outcome()` that mentions `budget_exceeded`
- [ ] Update Observer class docstring that mentions `budget_exceeded`

## Success Criteria

- [ ] `max_budget_usd` is not passed to `ClaudeAgentOptions`
- [ ] No references to `SDK_MAX_BUDGET_USD` or `_COST_WARN_THRESHOLD` remain in Python code
- [ ] Observer does not handle `budget_exceeded` as a stop reason
- [ ] All existing tests pass with budget-related assertions removed/updated
- [ ] `docs/features/sdk-modernization.md` no longer references budget configuration
- [ ] Activity-based stall detection continues to function (verified by existing tests)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (budget-removal)**
  - Name: budget-remover
  - Role: Remove all budget-related code, env vars, constants, and update tests
  - Agent Type: builder
  - Resume: true

- **Validator (budget-removal)**
  - Name: budget-validator
  - Role: Verify no budget references remain and all tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove budget code from sdk_client.py
- **Task ID**: build-sdk-cleanup
- **Depends On**: none
- **Validates**: `grep -rn 'max_budget_usd\|SDK_MAX_BUDGET_USD\|COST_WARN' agent/sdk_client.py` returns nothing
- **Assigned To**: budget-remover
- **Agent Type**: builder
- **Parallel**: true
- Remove `_COST_WARN_THRESHOLD` constant (line 303)
- Remove `max_budget_usd` parameter from `ValorAgent.__init__()` (line 780) and its assignment (line 820)
- Remove `max_budget_usd` from docstring
- Remove `max_budget_usd=self.max_budget_usd` from `_create_options()` (line 999)
- Remove cost warning branch `if cost >= _COST_WARN_THRESHOLD:` (line 1096-1097), keep the info-level log

### 2. Remove budget_exceeded from Observer
- **Task ID**: build-observer-cleanup
- **Depends On**: none
- **Validates**: `grep -rn 'budget_exceeded' bridge/observer.py` returns nothing
- **Assigned To**: budget-remover
- **Agent Type**: builder
- **Parallel**: true
- Remove the `budget_exceeded` branch in `Observer.run()` Phase 1 (lines 603-624)
- Remove `budget_exceeded` from `stop_is_terminal` check (line 671)
- Update Observer class docstring (line 393)

### 3. Update tests
- **Task ID**: build-test-updates
- **Depends On**: build-sdk-cleanup, build-observer-cleanup
- **Validates**: `pytest tests/unit/test_observer.py tests/unit/test_stop_reason_observer.py tests/unit/test_pipeline_state_machine.py -x -q`
- **Assigned To**: budget-remover
- **Agent Type**: builder
- **Parallel**: false
- Delete `test_budget_exceeded_delivers` from `tests/unit/test_observer.py`
- Delete `test_budget_exceeded_delivers_with_warning` from `tests/unit/test_stop_reason_observer.py`
- Update `test_read_session_includes_stop_reason` to use `rate_limited` instead of `budget_exceeded`
- Update `test_get_stop_reason_returns_and_clears` to use `rate_limited` instead of `budget_exceeded`
- Update `test_non_end_turn_is_fail` in pipeline state machine tests to use `timeout` instead of `budget_exceeded`
- Fix line 315 reference in test_observer.py

### 4. Update documentation
- **Task ID**: build-docs-update
- **Depends On**: build-sdk-cleanup, build-observer-cleanup
- **Validates**: `grep -rn 'max_budget_usd\|Cost Budgeting\|budget_exceeded' docs/features/sdk-modernization.md` returns nothing
- **Assigned To**: budget-remover
- **Agent Type**: builder
- **Parallel**: true
- Remove "Cost Budgeting" section from `docs/features/sdk-modernization.md`
- Update `docs/guides/sdlc-storyline-example.md` budget failure scenario
- Update docstrings in `bridge/pipeline_state.py`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-test-updates, build-docs-update
- **Assigned To**: budget-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn 'max_budget_usd\|budget_exceeded\|COST_WARN' agent/ bridge/ --include='*.py'` — expect exit code 1
- Run `pytest tests/ -x -q` — expect exit code 0
- Run `python -m ruff check .` — expect exit code 0
- Verify stall detection tests still pass

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No budget refs in code | `grep -rn 'max_budget_usd\|SDK_MAX_BUDGET_USD\|COST_WARN_THRESHOLD' agent/ bridge/ --include='*.py'` | exit code 1 |
| No budget_exceeded handling | `grep -rn 'budget_exceeded' bridge/observer.py` | exit code 1 |
| SDK modernization doc clean | `grep -c 'Cost Budgeting' docs/features/sdk-modernization.md` | exit code 1 |

---

## Open Questions

No open questions — the issue is well-scoped and all decisions are made. This is a pure removal with no design choices to make.
