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
| `/do-test --no-lint` | Run all tests, skip ruff checks |
| `/do-test unit --no-lint` | Run `tests/unit/` without lint |
| `/do-test --changed --no-lint` | Changed-file tests without lint |
| `/do-test --direct` | Force direct execution, skip parallel agent dispatch |
| `/do-test unit --direct` | Run `tests/unit/` directly (combinable with any target) |

**Parsing rules:**
1. Extract flags: `--changed`, `--no-lint`, `--direct`
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

**All tests** (no target specified): Uses a smart dispatch decision based on suite size. If the test file count is below 50 or the `--direct` flag is set, tests run directly in the current agent (no subagent dispatch). For larger suites (50+ test files), dispatches parallel subagents via the Task tool using `model: "sonnet"` for reliable bash execution. A separate lint agent runs in parallel with the test agents.

**Timeout fallback**: When parallel agents are dispatched, a 2-minute timeout applies. If any agent hasn't returned output within 2 minutes, all pending agents are abandoned and tests fall back to direct execution. This ensures test results are always collected, even when agent dispatch fails.

This adaptive approach maximizes throughput for large suites while keeping small-suite and single-target runs simple and fast.

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
| lint (ruff format) | PASS | - | - | - | 0.3s |

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

## Pytest Configuration

The project's pytest configuration is in `pyproject.toml` under `[tool.pytest.ini_options]`. Notable settings:

| Setting | Value | Reason |
|---------|-------|--------|
| `addopts` | `-v --tb=short -p no:postgresql` | Verbose output with short tracebacks; disables pytest-postgresql plugin |
| `asyncio_mode` | `auto` | Async tests run without explicit markers |

The `-p no:postgresql` flag prevents the `pytest-postgresql` plugin (installed at the system level) from auto-loading. Without this flag, the plugin attempts to import `psycopg` which fails with an `ImportError` when `libpq` is not available, crashing the entire test runner before any tests execute. See [issue #265](https://github.com/valorengels/ai/issues/265).

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Lint by default | Code quality checks should run with every test pass unless explicitly opted out |
| `--no-lint` flag | Provides escape hatch for fast iteration when only test results matter |
| CWD-relative execution | No worktree detection logic needed; works correctly whether invoked directly or via `/do-build` |
| Smart dispatch | Direct for small suites (<50 files) or `--direct`; parallel with sonnet agents + 2-min timeout fallback for large suites |
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
