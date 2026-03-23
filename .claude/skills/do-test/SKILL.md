---
name: do-test
description: "Use when running the test suite. Parses arguments, dispatches test runners (potentially in parallel), and aggregates results. Triggered by 'run tests', 'test this', or any request about testing."
argument-hint: "[test-path-or-filter]"
---

# Do Test

You are the **test orchestrator**. You parse arguments, dispatch test runners (potentially in parallel), and aggregate results into a summary.

## Variables

TEST_ARGS: $ARGUMENTS

**If TEST_ARGS is empty or literally `$ARGUMENTS`**: The skill argument substitution did not run. Look at the user's original message in the conversation — they invoked this as `/do-test <argument>`. Extract whatever follows `/do-test` as the value of TEST_ARGS. Do NOT stop or report an error; just use the argument from the message.

## Step 0: Discover Additional Test Skills

Before running tests, scan for any additional test-related skill docs in the project:

```bash
ls .claude/skills/*test*/*.md 2>/dev/null
```

**Read any discovered files.** They may define additional test runners, targets, or configurations beyond what this skill covers (e.g., mobile tests, browser tests, performance benchmarks). Incorporate their instructions alongside the defaults below.

## Argument Parsing

Parse `TEST_ARGS` to determine what to run:

| Input | Behavior |
|-------|----------|
| _(empty)_ | Run **all** test directories + lint checks |
| `unit` | Run `tests/unit/` + lint |
| `integration` | Run `tests/integration/` + lint |
| `e2e` | Run `tests/e2e/` + lint |
| `tools` | Run `tests/tools/` + lint |
| `performance` | Run `tests/performance/` + lint |
| `tests/unit/test_bridge_logic.py` | Run that specific file + lint |
| `--changed` | Detect changed files, map to test files, run those + lint |
| `--no-lint` | Skip ruff/black checks (combinable with any above) |
| `unit --no-lint` | Run `tests/unit/` without lint |
| `--changed --no-lint` | Changed-file tests without lint |
| `--direct` | Force direct execution, skip parallel agent dispatch |
| `unit --direct` | Run `tests/unit/` directly (combinable with any target) |
| `frontend <url> "<scenario>"` | Run a browser-based UI test via `frontend-tester` subagent |

**Parsing rules:**
1. Extract flags: `--changed`, `--no-lint`
2. If target is `frontend`, route to **Frontend Testing** (see below) — do not run pytest
3. Whatever remains is the **target**: a test type name (`unit`, `integration`, `e2e`, `tools`, `performance`) or a file/directory path
4. If no target and no `--changed`, target is "all"
5. Extract `--direct` flag alongside `--changed` and `--no-lint`

## Changed-File Detection (`--changed`)

When `--changed` is specified:

1. **Determine the diff base:**
   ```bash
   CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
   if [ "$CURRENT_BRANCH" = "main" ]; then
     DIFF_BASE="HEAD~1"
   else
     DIFF_BASE="main"
   fi
   ```

2. **Get changed files:**
   ```bash
   git diff --name-only "$DIFF_BASE"...HEAD -- '*.py'
   ```

3. **Map changed files to test files** using these conventions:
   - `bridge/*.py` -> `tests/unit/test_bridge*.py`
   - `tools/*.py` -> `tests/tools/test_*.py`
   - `agent/*.py` -> `tests/unit/test_agent*.py`
   - `monitoring/*.py` -> `tests/unit/test_monitoring*.py`
   - General rule: source file `foo/bar.py` -> `tests/*/test_bar.py`
   - Test files themselves: include directly if they were changed

4. **Filter to existing files** -- only include test files that actually exist on disk.

5. If no test files are found after mapping, report "No test files found for changed files" and skip test execution (lint still runs unless `--no-lint`).

## Constants

| Name | Value | Description |
|------|-------|-------------|
| `PARALLEL_DISPATCH_THRESHOLD` | 50 | Number of test files above which parallel subagent dispatch is used instead of sequential execution. Below this threshold, run tests in-process to avoid subagent overhead. |

**Integration test check:** If the plan has an Agent Integration section describing cross-component wiring (tool A feeds component B), verify at least one test exercises the full chain -- not just each component in isolation.

## Execution Strategy

### Single Target (specific type or file)

When the user requests a specific test type or file, run it directly in the current agent. No need for parallel dispatch -- the overhead is not worth it for a single runner.

```bash
# For a type like "unit":
pytest tests/unit/ -v --tb=short

# For a specific file:
pytest tests/unit/test_bridge_logic.py -v --tb=short

# For --changed with resolved files:
pytest tests/unit/test_foo.py tests/tools/test_bar.py -v --tb=short
```

If lint is enabled, run lint sequentially after tests:
```bash
python -m ruff check .
black --check .
```

### All Tests (no target specified)

When running all tests and the total number of test files exceeds `PARALLEL_DISPATCH_THRESHOLD` (50), dispatch **parallel subagents** via the Task tool for each test directory that exists. This maximizes throughput. Below the threshold, run all suites sequentially in-process to avoid subagent overhead.

**Step 0: Decide execution mode**

Before dispatching parallel agents, determine if direct execution is more efficient:

```bash
TEST_FILE_COUNT=$(find tests/ -name "test_*.py" 2>/dev/null | wc -l | tr -d ' ')
```

**Run tests DIRECTLY (no agent dispatch) if ANY of these are true:**
- `--direct` flag is set
- Test file count is below 50
- Previous parallel dispatch in this session failed to produce output

When running directly, execute as a single command:
```bash
pytest tests/ -v --tb=short
```

Then run lint (if enabled) and skip to **Result Aggregation**.

**Only dispatch parallel agents if:**
- No `--direct` flag
- Test file count is 50 or more
- No prior agent failures in this session

**Step 1: Discover test directories**

Check which of these directories exist and contain test files:
- `tests/unit/`
- `tests/integration/`
- `tests/e2e/`
- `tests/performance/`
- `tests/tools/`

Also check for top-level test files in `tests/` (files matching `test_*.py` directly in the tests directory).

**Step 2: Dispatch parallel agents**

For each existing test directory/group, create a Task:

```
Task({
  description: "Run [suite-name] tests",
  subagent_type: "test-engineer",
  model: "sonnet",
  prompt: "Run the following test command and report results:

    cd [CWD]
    pytest [test-path] -v --tb=short

    Report: number of tests passed, failed, skipped, and any failure details.
    Output the raw pytest output.",
  run_in_background: true
})
```

If lint is enabled, dispatch a lint agent in parallel too:

```
Task({
  description: "Run lint checks",
  subagent_type: "validator",
  model: "sonnet",
  prompt: "Run lint checks in [CWD]:

    cd [CWD]
    python -m ruff check .
    black --check .

    Report: pass/fail for each tool, and any issues found.",
  run_in_background: true
})
```

**Step 3: Wait for agents with timeout fallback**

Monitor all background tasks. Set a **2-minute timeout** from dispatch.

**If all agents complete within 2 minutes:** Collect their outputs normally and proceed to Result Aggregation.

**If any agent has NOT returned output after 2 minutes:**
1. Abandon all pending agents (do not wait further)
2. Log which agents timed out: `"Agent timeout: [suite-name] test-engineer did not return within 2 minutes"`
3. **Fall back to direct execution:**
   ```bash
   pytest tests/ -v --tb=short
   ```
4. Use the direct execution output for Result Aggregation
5. Run lint directly too if lint agents also timed out:
   ```bash
   python -m ruff check .
   black --check .
   ```

This fallback ensures test results are always collected, even when agent dispatch fails.

## Result Aggregation

After all runners complete, present a summary table:

```
## Test Results

| Suite | Status | Passed | Failed | Skipped | Duration |
|-------|--------|--------|--------|---------|----------|
| unit | PASS | 42 | 0 | 2 | 3.1s |
| integration | FAIL | 8 | 1 | 0 | 12.4s |
| tools | PASS | 15 | 0 | 0 | 1.8s |
| lint (ruff) | PASS | - | - | - | 0.5s |
| lint (black) | PASS | - | - | - | 0.3s |

### Failures

**integration::test_api_auth.py::test_expired_token**
AssertionError: Expected 401, got 200
  File "tests/integration/test_api_auth.py", line 45
```

**Final verdict:**
- If ALL suites pass: report `ALL TESTS PASSED`
- If ANY suite fails: proceed to **Failure Baseline Verification** before reporting final verdict

## Failure Baseline Verification

When test failures are detected, do NOT claim failures are "pre-existing" without evidence. Instead, dispatch the `baseline-verifier` subagent to classify each failure by running it against `main`.

### When to Run

Run baseline verification when ALL of these are true:
- One or more tests failed (pytest exit code 1)
- The current branch is NOT `main` (baseline comparison only makes sense on feature branches)
- There are fewer than 50 failing tests (above this threshold, baseline verification would be too slow)

**Skip baseline verification if:**
- All tests passed (nothing to classify)
- Running on `main` (no baseline to compare against)
- More than 50 tests failed (likely a systemic issue, not individual regressions)

### Step 0.5: Flaky Filter (Retry Before Baseline)

Before dispatching failures to the baseline verifier, retry ONLY the failing tests once more on the current branch to detect intermittent (flaky) failures:

```bash
python -m pytest <FAILING_TEST_IDS> -v --tb=short 2>&1
```

**Classify retry results:**
- Tests that **PASS on retry** → classify as `FLAKY`. Report them in the results table but they do NOT count as failures or regressions. Do NOT send them to baseline verification.
- Tests that **still FAIL on retry** → these are consistent failures. Send them to baseline verification as normal.
- If **ALL** failures pass on retry → skip baseline verification entirely (all are flaky).

**Why this matters:** Flaky tests that fail on the branch but pass on main get misclassified as regressions. A single retry catches the most common intermittent failures (timing-dependent tests, LLM classifier non-determinism, resource contention) without the overhead of a full baseline worktree.

**Add flaky tests to the results table:**

```
### Flaky Tests (passed on retry)

| Test | Verdict | Notes |
|------|---------|-------|
| `tests/unit/test_timing.py::test_stall_detection` | FLAKY | Passed on retry (intermittent) |
| `tests/unit/test_classifier.py::test_bare_ref` | FLAKY | Passed on retry (LLM non-determinism) |

These tests are intermittently failing and should be investigated, but they do not block the pipeline.
```

**After the flaky filter**, update `FAILING_TEST_IDS` to contain only the tests that still failed on retry. Proceed to Step 1 with this reduced list.

### Step 1: Collect Failing Test Node IDs

Parse the pytest output to extract all failing test node IDs. These look like:
```
tests/unit/test_foo.py::test_bar FAILED
tests/integration/test_api.py::TestAuth::test_expired_token FAILED
```

Collect them into a list: `FAILING_TEST_IDS`

### Step 2: Check Regression Counter Context

If the Observer passed context from a previous `/do-test` OUTCOME (available in the prompt context), look for:
- `regression_fix_attempt`: The current attempt number (integer)
- `persistent_regressions`: The list of regression test IDs from the prior run

These are used for the circuit breaker in Step 5.

### Step 3: Dispatch Baseline Verifier

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

### Step 4: Integrate Classification into Results

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

### Step 5: Regression Circuit Breaker

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

### Adjusted Final Verdict (with Baseline Verification)

After baseline verification completes, the final verdict changes:

- **ALL TESTS PASSED**: No failures at all (baseline verification was skipped)
- **REGRESSIONS FOUND**: Branch introduced new test failures. These MUST be fixed. Status: `fail`
- **PRE-EXISTING ONLY**: All failures also fail on main. Branch did not make things worse. Status: `partial`
- **BLOCKED - ESCALATING**: Regression fixes not converging after 3 attempts. Status: `blocked`
- **MIXED**: Some regressions, some pre-existing. Regressions must be fixed. Status: `fail`

**Important:** Only regressions block the pipeline. Pre-existing failures are reported but do NOT cause `status: fail`.

## Frontend Testing (`frontend` target)

When `TEST_ARGS` starts with `frontend`, route to the `frontend-tester` subagent. Do **not** run pytest.

**Input format:**
```
/do-test frontend https://myapp.com "Login form submits and shows dashboard"
/do-test frontend https://myapp.com "Checkout flow completes successfully" -- steps: click add-to-cart, click checkout, fill address, submit
```

**Dispatch a single `frontend-tester` subagent:**

```
Task({
  description: "Frontend test: <scenario>",
  subagent_type: "frontend-tester",
  prompt: "
URL: <url>
Scenario: <scenario>
Steps:
  <extracted steps if provided, otherwise infer from scenario>
Expected: <inferred from scenario>
  "
})
```

The `frontend-tester` agent owns all `agent-browser` interaction — the skill never calls `agent-browser` directly.

**When running all tests** (no target) and a `tests/frontend/` directory exists with `.json` or `.yaml` scenario files, dispatch one `frontend-tester` subagent per scenario file in parallel alongside the pytest agents.

**Scenario file format** (for `tests/frontend/`):
```json
{
  "url": "https://myapp.com/login",
  "scenario": "Login with valid credentials shows dashboard",
  "steps": [
    "Fill email field with test@example.com",
    "Fill password field with password123",
    "Click Login button"
  ],
  "expected": "Dashboard page loads with user name visible"
}
```

**Result aggregation:** Include frontend results in the summary table alongside pytest suites:

```
| Suite           | Status | Passed | Failed | Screenshot |
|-----------------|--------|--------|--------|------------|
| frontend/login  | PASS   | 1      | 0      | /tmp/...   |
| frontend/checkout | FAIL | 0      | 1      | /tmp/...   |
```

## CWD-Relative Execution

All commands run relative to the current working directory. Do not attempt to detect or navigate to worktrees. When `/do-test` is invoked:
- From `/do-build`: CWD is already the worktree -- commands run there
- Directly by user: CWD is the main repo -- commands run there

Simply use the CWD as-is. Run `pwd` once at the start to confirm and log it.

## Error Handling

- If `pytest` is not installed, report the error clearly
- If a test directory does not exist, skip it silently (do not fail)
- If git commands fail for `--changed`, fall back to running all tests
- Parse pytest exit codes: 0 = all passed, 1 = some failed, 2 = error, 5 = no tests collected

## Quality Checks (Post-Test)

After tests pass, run these additional quality scans and include results in the report:

### Exception Swallow Scan

Scan for `except Exception: pass` patterns that lack test coverage:

```bash
grep -rn "except.*Exception.*:" --include="*.py" agent/ bridge/ | grep -v "logger\|log\.\|warning\|error\|raise\|# .*tested" | head -20
```

Report any bare exception handlers found. Each should either:
1. Have a corresponding test asserting observable behavior (logger.warning, state change)
2. Be documented with a comment explaining why bare `pass` is acceptable (e.g., cleanup during shutdown)

### Empty Input Check

If the test suite covers agent output processing code, verify that empty/None/whitespace inputs are tested:

```bash
grep -rn "def test.*empty\|def test.*none\|def test.*whitespace" tests/ --include="*.py" | wc -l
```

Flag if the changed files include output processing code but the test suite has zero empty input tests.

### Closure Coverage Flag

If any changed files contain inner functions or closures (functions defined inside other functions), flag whether those closures have dedicated test coverage:

```bash
grep -rn "def .*(" --include="*.py" agent/ bridge/ | grep "^.*:.*def .*:$" | head -10
```

Closures that replicate logic already tested elsewhere (e.g., inline routing logic that should call a shared function) are a test smell. Note them in the report.

### Stale xfail Hygiene Scan

After tests pass, scan for xfail-marked tests that are now passing (xpass). When a bug fix lands, the corresponding xfail marker should be removed and converted to a hard assertion. Stale xfails indicate the fix landed but the test wasn't updated.

**Two forms of xfail exist and require different detection:**

1. **Decorator form** (`@pytest.mark.xfail`): Pytest reports these as `XPASS` in test output when the test unexpectedly passes. Check the pytest output for `XPASS` entries.

2. **Runtime form** (`pytest.xfail("reason")` called inside the test body): These are **invisible to XPASS detection** because the call short-circuits the test before it reaches the assertion. A test with a runtime `pytest.xfail()` will show as `xfail` even when the underlying bug is fixed — it never gets a chance to pass. **This is the more dangerous form** because it silently hides regressions.

```bash
# Find ALL xfail markers (both decorator and runtime forms)
grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/ --include="*.py" | head -20
```

**For decorator xfails:** Check if pytest reports `XPASS` in the test output.

**For runtime xfails:** These ALWAYS require manual review. Flag every `pytest.xfail(` call found in test bodies:
1. If the call is guarded by a condition (e.g., `if broken: pytest.xfail(...)`), check whether the condition is still true
2. If the call is unconditional, flag it as "runtime xfail — cannot detect if bug is fixed, must be reviewed"

For each stale xfail detected (either form):
1. Flag it prominently in the quality report: "STALE XFAIL: tests/foo/test_bar.py:LINE — [decorator|runtime] form"
2. Include the file and line number for easy removal
3. Suggest: "This test should have its xfail marker removed and converted to a hard assertion"

**Important:** Runtime `pytest.xfail()` is a stronger smell than decorator `@pytest.mark.xfail`. If `--changed` mode is active and the changed files include a bug fix, runtime xfails in related test files should be flagged as **blockers**, not just warnings.

**Skip if:** No xfail markers found in the test suite.

## Notes

- No temporary files in the repo -- use `/tmp` for any scratch work
- Do not modify any source or test files -- this skill is read-only (it runs tests, it does not fix them)
- Keep pytest output visible -- developers need to see the raw output for debugging
- The `-v --tb=short` flags provide verbose test names with concise tracebacks
