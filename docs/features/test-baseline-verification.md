# Test Baseline Verification

Verified classification of test failures as regressions vs pre-existing by running failing tests against `main`. Replaces unverified LLM claims with deterministic evidence.

## Problem

During `/do-test`, when tests fail on a feature branch, the agent would claim "these failures are pre-existing on main" without ever verifying the claim. This led to regressions slipping through, low reviewer confidence, and flaky tests getting free passes.

## How It Works

### Verification Flow

1. **do-test detects failures** -- pytest returns exit code 1 with failing test node IDs
2. **Dispatch baseline-verifier** -- do-test sends failing test IDs to the `baseline-verifier` subagent
3. **Subagent creates worktree** -- temporary worktree at main HEAD (`/tmp/baseline-verify-<timestamp>`)
4. **Run failing tests against main** -- only the failing tests, not the full suite
5. **Classify each failure** -- deterministic rules, no LLM judgment
6. **Return structured JSON** -- `regressions`, `pre_existing`, `inconclusive` arrays
7. **do-test integrates results** -- verified classification table replaces vague claims

### Classification Rules

| Branch Status | Main Status | Verdict | Pipeline Impact |
|--------------|-------------|---------|-----------------|
| FAILED | PASSED | **Regression** | Blocks merge -- must fix |
| FAILED | FAILED | Pre-existing | Reported but does not block |
| FAILED | ERROR | Inconclusive | Manual review recommended |
| FAILED | SKIPPED | Inconclusive | Manual review recommended |
| FAILED | NOT FOUND | Inconclusive | Test does not exist on main |

### OUTCOME Status Mapping

| Condition | Status | Next Skill |
|-----------|--------|------------|
| All tests pass | `success` | `/do-pr-review` |
| Regressions found | `fail` | `/do-patch` |
| Only pre-existing failures | `partial` | `/do-pr-review` |
| 3 fix attempts exhausted | `blocked` | `/do-plan` |

## Regression Circuit Breaker

To prevent infinite test-patch-test loops when regression fixes are not converging:

- **Counter tracking**: `regression_fix_attempt` increments when the same regressions persist across `/do-test` invocations
- **Counter reset**: Resets to 1 when the set of regressions changes (different test IDs = new problem)
- **Threshold**: After 3 attempts with identical persistent regressions, do-test emits `status: blocked` with `next_skill: /do-plan`
- **Escalation**: The Observer routes back to planning so a human can reassess the approach

## Key Design Decisions

- **Subagent isolation**: The baseline-verifier runs in its own context to avoid polluting do-test's context window with worktree operations and raw pytest output
- **Only failing tests**: Only the specific failing tests are run against main, not the full suite. This keeps verification fast
- **Deterministic classification**: No LLM judgment in the classification step. The rules are mechanical: if it fails on both, it is pre-existing; if it passes on main, it is a regression
- **Additive OUTCOME fields**: New artifact fields (`regressions`, `pre_existing`, etc.) are additive to the existing OUTCOME contract. Consumers that do not read these fields are unaffected

## Files

| File | Purpose |
|------|---------|
| `.claude/agents/baseline-verifier.md` | Subagent definition: worktree creation, test execution, classification |
| `.claude/skills/do-test/SKILL.md` | Orchestrator: dispatches verifier, integrates results, circuit breaker |

## Conditions for Running

Baseline verification runs when ALL conditions are true:
- One or more tests failed (pytest exit code 1)
- Current branch is not `main`
- Fewer than 50 tests failed

Skipped when: all tests pass, running on main, or more than 50 failures (systemic issue).
