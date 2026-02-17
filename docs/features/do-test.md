# Do-Test

Intelligent test orchestration skill that parses arguments, dispatches test runners (potentially in parallel), and aggregates results into a structured summary.

## How It Works

Invoked via `/do-test` with optional arguments. The skill determines what to run based on the input, executes tests and lint checks, and reports a summary table with pass/fail status, counts, and duration for each suite.

### Usage

| Input | Behavior |
|-------|----------|
| `/do-test` | Run **all** test directories + lint checks |
| `/do-test unit` | Run `tests/unit/` + lint |
| `/do-test integration` | Run `tests/integration/` + lint |
| `/do-test e2e` | Run `tests/e2e/` + lint |
| `/do-test tools` | Run `tests/tools/` + lint |
| `/do-test performance` | Run `tests/performance/` + lint |
| `/do-test tests/unit/test_bridge_logic.py` | Run that specific file + lint |
| `/do-test --changed` | Detect changed files, map to test files, run those + lint |
| `/do-test --no-lint` | Run all tests, skip ruff/black checks |
| `/do-test unit --no-lint` | Run `tests/unit/` without lint |
| `/do-test --changed --no-lint` | Changed-file tests without lint |

**Parsing rules:**
1. Extract flags: `--changed`, `--no-lint`
2. Whatever remains is the **target**: a test type name or a file/directory path
3. If no target and no `--changed`, target is "all"

### Test Types

| Type | Directory | Description |
|------|-----------|-------------|
| `unit` | `tests/unit/` | Fast, isolated unit tests |
| `integration` | `tests/integration/` | Tests requiring external services or APIs |
| `e2e` | `tests/e2e/` | End-to-end workflow tests |
| `performance` | `tests/performance/` | Performance and benchmark tests |
| `tools` | `tests/tools/` | Tests for MCP tool implementations |

Directories that do not exist are silently skipped.

## Changed-File Detection

The `--changed` flag enables smart branch comparison to run only tests relevant to recent changes.

**Diff base selection:**
- On `main` branch: compares against `HEAD~1` (last commit)
- On feature branches: compares against `main`

**File-to-test mapping conventions:**
- `bridge/*.py` maps to `tests/unit/test_bridge*.py`
- `tools/*.py` maps to `tests/tools/test_*.py`
- `agent/*.py` maps to `tests/unit/test_agent*.py`
- `monitoring/*.py` maps to `tests/unit/test_monitoring*.py`
- General rule: source file `foo/bar.py` maps to `tests/*/test_bar.py`
- Changed test files are included directly

Only test files that actually exist on disk are included. If no test files match, test execution is skipped (lint still runs unless `--no-lint`).

## Parallel Execution

The execution strategy adapts based on the target:

**Single target** (specific type or file): Runs directly in the current agent. The overhead of parallel dispatch is not worth it for a single runner. Lint runs sequentially after tests.

**All tests** (no target specified): Dispatches parallel subagents via the Task tool for each test directory that exists. A separate lint agent runs in parallel with the test agents. Results are collected and aggregated after all agents complete.

This fan-out approach maximizes throughput when running the full suite while keeping single-target runs simple and fast.

## Result Format

After all runners complete, the skill presents a structured summary:

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
- All suites pass: `ALL TESTS PASSED`
- Any suite fails: `TESTS FAILED` with failure details prominently displayed

## Integration with /do-build

The `/do-build` workflow invokes `/do-test` as its testing step. When called from `/do-build`, the CWD is already set to the worktree directory, so tests run against the correct code. The skill is read-only -- it runs tests but never modifies source or test files.

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Lint by default | Code quality checks should run with every test pass unless explicitly opted out |
| `--no-lint` flag | Provides escape hatch for fast iteration when only test results matter |
| CWD-relative execution | No worktree detection logic needed; works correctly whether invoked directly or via `/do-build` |
| Smart dispatch | Parallel for full suite (throughput), direct for single target (simplicity) |
| `--changed` branch awareness | On `main`, compares last commit; on feature branches, compares against `main` |
| Silent skip for missing dirs | Repositories with partial test directory structures work without configuration |
| `-v --tb=short` pytest flags | Verbose test names for clarity with concise tracebacks for debugging |
| Read-only execution | The test skill runs tests, it does not fix them -- separation of concerns |

## Components

| Component | Path | Purpose |
|-----------|------|---------|
| Skill definition | `.claude/skills/do-test/SKILL.md` | Full orchestration prompt and argument parsing rules |

## Related

- [Build Session Reliability](build-session-reliability.md) -- Build workflow that invokes do-test
- [Documentation Lifecycle](documentation-lifecycle.md) -- Another enforcement pattern in the build pipeline
