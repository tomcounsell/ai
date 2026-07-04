# Failure Baseline Verification

Loaded when test failures are detected on a feature branch. Do NOT claim
failures are "pre-existing" without evidence. Instead, dispatch the
`baseline-verifier` subagent to classify each failure by running it against
the default branch (`main`).

## When to Run

Run baseline verification when ALL of these are true:
- One or more tests failed (test-runner failure exit code, e.g. pytest exit code 1)
- The current branch is NOT `main` (baseline comparison only makes sense on feature branches)
- There are fewer than 50 failing tests (above this threshold, baseline verification would be too slow)

**Skip baseline verification if:**
- All tests passed (nothing to classify)
- Running on `main` (no baseline to compare against)
- More than 50 tests failed (likely a systemic issue, not individual regressions)

## Step 0.5: Flaky Filter (Retry Before Baseline)

Before dispatching failures to the baseline verifier, retry ONLY the failing
tests once more on the current branch to detect intermittent (flaky) failures:

```bash
# Python example — use the project's runner with an explicit failing-test list
python -m pytest <FAILING_TEST_IDS> -v --tb=short 2>&1
```

**Classify retry results:**
- Tests that **PASS on retry** → classify as `FLAKY`. Report them in the results table but they do NOT count as failures or regressions. Do NOT send them to baseline verification.
- Tests that **still FAIL on retry** → these are consistent failures. Send them to baseline verification as normal.
- If **ALL** failures pass on retry → skip baseline verification entirely (all are flaky).

**Why this matters:** Flaky tests that fail on the branch but pass on main get
misclassified as regressions. A single retry catches the most common
intermittent failures (timing-dependent tests, LLM classifier non-determinism,
resource contention) without the overhead of a full baseline worktree.

**Add flaky tests to the results table:**

```
### Flaky Tests (passed on retry)

| Test | Verdict | Notes |
|------|---------|-------|
| `tests/unit/test_timing.py::test_stall_detection` | FLAKY | Passed on retry (intermittent) |
| `tests/unit/test_classifier.py::test_bare_ref` | FLAKY | Passed on retry (LLM non-determinism) |

These tests are intermittently failing and should be investigated, but they do not block the pipeline.
```

**After the flaky filter**, update `FAILING_TEST_IDS` to contain only the tests
that still failed on retry. Proceed to Step 1 with this reduced list.

## Step 1: Collect Failing Test Node IDs

Parse the test-runner output to extract all failing test node IDs. For pytest these look like:
```
tests/unit/test_foo.py::test_bar FAILED
tests/integration/test_api.py::TestAuth::test_expired_token FAILED
```

Collect them into a list: `FAILING_TEST_IDS`

## Step 2: Check Regression Counter Context

If the Observer passed context from a previous `/do-test` OUTCOME (available in the prompt context), look for:
- `regression_fix_attempt`: The current attempt number (integer)
- `persistent_regressions`: The list of regression test IDs from the prior run

These are used for the circuit breaker in Step 5.

## Step 3: Dispatch Baseline Verifier

Dispatch the `baseline-verifier` subagent to classify failures:

```
Task({
  description: "Baseline verification: classify test failures against main",
  subagent_type: "baseline-verifier",
  prompt: "
    Classify these failing test node IDs by running them against main:

    failing_test_ids:
    <one test ID per line>

    worktree_path: <current CWD for reference>

    Follow the instructions in your agent definition exactly.
    Return the structured JSON classification.
  "
})
```

Wait for the subagent to complete and parse the returned JSON.

## Step 4: Integrate Classification into Results

Replace the generic "Failures" section with a **verified classification table**:

```
### Failure Classification (verified against main at <baseline_commit>)

| Test | Branch | Main | Verdict |
|------|--------|------|---------|
| tests/unit/test_foo.py::test_bar | FAILED | PASSED | **REGRESSION** |
| tests/unit/test_old.py::test_legacy | FAILED | FAILED | pre-existing |
| tests/e2e/test_flow.py::test_deleted | FAILED | N/A | inconclusive |

**Summary:**
- Regressions: 1 (blocking)
- Pre-existing: 1 (does not block merge)
- Inconclusive: 1 (manual review recommended)
```

## Step 5: Regression Circuit Breaker

Track regression fix attempts across pipeline invocations to prevent infinite test-patch-test loops.

**Reading the counter:**
1. Check if `regression_fix_attempt` was provided in the context from a prior OUTCOME
2. If not present, this is attempt 0 (first test run)

**Incrementing the counter:**
1. Compare the current `regressions` list with `persistent_regressions` from the prior run
2. If the regression sets are **identical** (same test IDs): increment `regression_fix_attempt` by 1
3. If the regression set **changed** (different test IDs, even partially): reset `regression_fix_attempt` to 1

**Circuit breaker trigger:**
- `MAX_REGRESSION_FIX_ATTEMPTS = 3`
- If `regression_fix_attempt >= MAX_REGRESSION_FIX_ATTEMPTS` and regressions still exist:
  - Emit `status: blocked` instead of `status: fail`
  - Set `next_skill: /do-plan`
  - Include `failure_reason: "Regression fix not converging after N attempts. Escalating to planning."`
  - Include `persistent_regressions` in artifacts so the planner has context

## Adjusted Final Verdict (with Baseline Verification)

After baseline verification completes, the final verdict changes:

- **ALL TESTS PASSED**: No failures at all (baseline verification was skipped)
- **REGRESSIONS FOUND**: Branch introduced new test failures. These MUST be fixed. Status: `fail`
- **PRE-EXISTING ONLY**: All failures also fail on main. Branch did not make things worse. Status: `partial`
- **BLOCKED - ESCALATING**: Regression fixes not converging after 3 attempts. Status: `blocked`
- **MIXED**: Some regressions, some pre-existing. Regressions must be fixed. Status: `fail`

**Important:** Only regressions block the pipeline. Pre-existing failures are
reported but do NOT cause `status: fail`.
