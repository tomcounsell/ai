# Parallel Dispatch (All-Tests Runs)

Loaded when running **all** tests and the test file count is at or above
`PARALLEL_DISPATCH_THRESHOLD` (50). Below the threshold, or when `--direct` is
set, run everything in-process instead (see SKILL.md "Execution Strategy").

## Step 1: Discover test directories

Check which of these directories exist and contain test files:
- `tests/unit/`
- `tests/integration/`
- `tests/e2e/`
- `tests/performance/`
- `tests/tools/`

Also check for top-level test files in `tests/` (files matching the project's
test-file convention, e.g. `test_*.py` for Python, directly in the tests directory).

## Step 2: Dispatch parallel agents

For each existing test directory/group, create a Task:

```
Task({
  description: "Run [suite-name] tests",
  subagent_type: "test-engineer",
  model: "sonnet",
  prompt: "Run the following test command and report results:

    cd [CWD]
    <test-runner command for [test-path]>   # e.g. pytest [test-path] -v --tb=short

    Report: number of tests passed, failed, skipped, and any failure details.
    Output the raw test-runner output.",
  run_in_background: true
})
```

If lint is enabled, dispatch a lint agent in parallel too:

```
Task({
  description: "Run lint checks",
  subagent_type: "validator",
  model: "sonnet",
  prompt: "Run the repo's configured lint/format checks in [CWD] (the context file names them; generic default is `ruff check .` and `ruff format --check .` when available):

    cd [CWD]
    <repo lint/format commands>

    Report: pass/fail for each tool, and any issues found.",
  run_in_background: true
})
```

## Step 3: Wait for agents with timeout fallback

Monitor all background tasks. Set a **2-minute timeout** from dispatch.

**If all agents complete within 2 minutes:** Collect their outputs normally and
proceed to Result Aggregation (SKILL.md).

**If any agent has NOT returned output after 2 minutes:**
1. Abandon all pending agents (do not wait further)
2. Log which agents timed out: `"Agent timeout: [suite-name] test-engineer did not return within 2 minutes"`
3. **Fall back to direct execution** of the full suite, e.g.:
   ```bash
   pytest tests/ -v --tb=short
   ```
4. Use the direct execution output for Result Aggregation
5. Run the repo's lint/format checks directly too if lint agents also timed out
   (commands per the context file; generic default `ruff check .` /
   `ruff format --check .` when available).

This fallback ensures test results are always collected, even when agent
dispatch fails.
