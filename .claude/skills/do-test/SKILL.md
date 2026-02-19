---
name: do-test
description: "Run the test suite. Use when the user says 'run tests', 'test this', or anything about testing."
---

# Do Test

You are the **test orchestrator**. You parse arguments, dispatch test runners (potentially in parallel), and aggregate results into a summary.

## Variables

TEST_ARGS: $ARGUMENTS

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
| `frontend <url> "<scenario>"` | Run a browser-based UI test via `frontend-tester` subagent |

**Parsing rules:**
1. Extract flags: `--changed`, `--no-lint`
2. If target is `frontend`, route to **Frontend Testing** (see below) — do not run pytest
3. Whatever remains is the **target**: a test type name (`unit`, `integration`, `e2e`, `tools`, `performance`) or a file/directory path
4. If no target and no `--changed`, target is "all"

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
ruff check .
black --check .
```

### All Tests (no target specified)

When running all tests, dispatch **parallel subagents** via the Task tool for each test directory that exists. This maximizes throughput.

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
  prompt: "Run lint checks in [CWD]:

    cd [CWD]
    ruff check .
    black --check .

    Report: pass/fail for each tool, and any issues found.",
  run_in_background: true
})
```

**Step 3: Wait for all agents to complete**

Monitor all background tasks. Collect their outputs.

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
- If ANY suite fails: report `TESTS FAILED` with failure details prominently displayed

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

## Notes

- No temporary files in the repo -- use `/tmp` for any scratch work
- Do not modify any source or test files -- this skill is read-only (it runs tests, it does not fix them)
- Keep pytest output visible -- developers need to see the raw output for debugging
- The `-v --tb=short` flags provide verbose test names with concise tracebacks
