# Test Baseline Verification

Verified classification of test failures as regressions vs pre-existing by running failing tests against `main`.

## Problem

A feature-branch test failure is classified as regression vs pre-existing by running the failing tests against `main`, rather than trusting an unverified claim. This prevents regressions slipping through and keeps flaky tests from getting free passes.

## How It Works

### Verification Flow

1. **do-test detects failures** -- pytest returns exit code 1 with failing test node IDs
2. **Flaky filter (Step 0.5)** -- retry failing tests once on the feature branch; tests that pass on retry are classified as `FLAKY` and excluded from baseline verification (see [Flaky Filter](test-reliability-flaky-filter.md))
3. **Run the baseline script** -- do-test passes the remaining (non-flaky) failing test IDs to its bundled `scripts/baseline_verify.py`, run with the project's own interpreter
4. **Script creates a worktree** -- a throwaway detached worktree at `main` under a unique temp dir, always removed afterwards
5. **Run failing tests against main** -- only those IDs, with `--junitxml` into the temp dir for structured output
6. **Parse results deterministically** -- `xml.etree.ElementTree` parses the junitxml file; no LLM interpretation of console output
7. **Completeness** -- every input test ID lands in exactly one bucket; an ID that errors, skips, or is missing on main goes to `inconclusive`, and a setup failure marks every ID inconclusive
8. **Print structured JSON** -- `regressions`, `pre_existing`, `inconclusive` arrays plus `baseline_commit` and `raw_output`
9. **do-test integrates results** -- a verified classification table

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

- **Script, not subagent**: `baseline_verify.py` does the worktree operations and pytest run and returns only JSON, so do-test's context never holds raw pytest output
- **Only failing tests**: Only the specific failing tests are run against main, not the full suite. This keeps verification fast
- **Deterministic classification**: No LLM judgment in the classification step. The rules are mechanical: if it fails on both, it is pre-existing; if it passes on main, it is a regression
- **junitxml over console parsing**: Pytest's `--junitxml` flag produces structured XML that is parsed with `xml.etree.ElementTree`. This eliminates vulnerabilities to output truncation, test ID format mismatches, status keywords in test names, and traceback interleaving
- **Flaky filter pre-step**: A single retry on the feature branch catches the most common intermittent failures before incurring the cost of baseline verification (worktree creation + test re-run on main)
- **Completeness validation**: Every input test ID must appear in exactly one classification bucket. Missing or duplicate IDs are caught and resolved deterministically
- **Additive OUTCOME fields**: The artifact fields (`regressions`, `pre_existing`, etc.) are additive to the OUTCOME contract. Consumers that do not read these fields are unaffected

## Files

| File | Purpose |
|------|---------|
| `.claude/skills-global/do-test/scripts/baseline_verify.py` | Worktree creation, test execution, classification |
| `.claude/skills-global/do-test/baseline-verification.md` | Flaky filter, script invocation, circuit breaker, verdict |

## Conditions for Running

Baseline verification runs when ALL conditions are true:
- One or more tests failed (pytest exit code 1)
- Current branch is not `main`
- Fewer than 50 tests failed

Skipped when: all tests pass, running on main, more than 50 failures (systemic issue), or all failures were classified as flaky by the Step 0.5 retry.

## Related

- [Test Reliability: Flaky Filter](test-reliability-flaky-filter.md) -- Flaky filter, junitxml parsing, and completeness validation details
- [Do-Test](do-test.md) -- The orchestration skill that dispatches baseline verification
