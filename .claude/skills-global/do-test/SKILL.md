---
name: do-test
description: "Run the project's test suite and aggregate results. Triggered by 'run tests', 'test this', or any request about testing."
argument-hint: "[test-path-or-filter]"
---

# Do Test

You are the **test orchestrator**. You parse arguments, dispatch test runners (potentially in parallel), and aggregate results into a summary. The workflow is language-agnostic; Python/pytest is the worked example throughout, and `PYTHON.md` carries the pytest specifics. On other stacks, substitute the project's runner (`cargo test`, `npm test`, `go test ./...`, etc.).

## Repo Context Probe

If `docs/sdlc/do-test.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares its test specifics: tiers and pytest markers, the lint/format commands to run, a deterministic happy-path or scenario runner, the primary source directories the quality scans target, quality-gate thresholds, and which module parses the OUTCOME contract. When the file is absent (the common case in a foreign repo), this skill runs the conventional test runner against conventional `tests/` directories and uses `git`-based change detection — no repo-specific tooling required.

## Variables

TEST_ARGS: $ARGUMENTS

**If TEST_ARGS is empty or literally `$ARGUMENTS`**: The skill argument substitution did not run. Look at the user's original message in the conversation — they invoked this as `/do-test <argument>`. Extract whatever follows `/do-test` as the value of TEST_ARGS. Do NOT stop or report an error; just use the argument from the message.

## Sub-Files

Load these on demand — never all at once:

| Sub-file | Load when... |
|----------|-------------|
| `PYTHON.md` | The project is Python (pytest, pyproject.toml, setup.py) — runner commands, lint tools, changed-file mapping, exit codes |
| `parallel-dispatch.md` | Running all tests with 50+ test files — parallel subagent dispatch and timeout fallback |
| `baseline-verification.md` | Test failures detected on a feature branch — flaky filter, regression-vs-pre-existing classification, circuit breaker |
| `quality-gates.md` | After tests pass, before OUTCOME — quality scans and the mandatory Exception Swallow Gate |
| `special-targets.md` | Target is `frontend` or `happy-paths` |

## Step 0: Discover Additional Test Skills

Before running tests, scan for any additional test-related skill docs in the project:

```bash
ls .claude/skills/*test*/*.md .claude/skills-global/*test*/*.md 2>/dev/null
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
| `--no-lint` | Skip the repo's lint/format checks |
| `--direct` | Force direct execution, skip parallel agent dispatch |
| `frontend <url> "<scenario>"` | Run a browser-based UI test via `frontend-tester` subagent (see `special-targets.md`) |
| `happy-paths` | Run the repo's deterministic happy-path runner directly via bash (declared in the context file; see `special-targets.md`). Skip if no such runner is declared. |

**Parsing rules:**
1. Extract flags: `--changed`, `--no-lint`, `--direct` — each is combinable with any target
2. If target is `frontend` or `happy-paths`, load `special-targets.md` and route there. Neither runs the unit-test runner.
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

2. **Get changed files** (Python example — adjust the glob for the project's language):
   ```bash
   git diff --name-only "$DIFF_BASE"...HEAD -- '*.py'
   ```

3. **Map changed files to test files.** General rule: source file `foo/bar.py` -> `tests/*/test_bar.py`. Test files themselves: include directly if they were changed. `PYTHON.md` has the full Python mapping table; the context file may declare repo-specific source-to-test mappings.

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
# For a type like "unit" (pytest example; see PYTHON.md):
pytest tests/unit/ -v --tb=short

# For a specific file:
pytest tests/unit/test_bridge_logic.py -v --tb=short

# For --changed with resolved files:
pytest tests/unit/test_foo.py tests/tools/test_bar.py -v --tb=short
```

If lint is enabled, run the repo's lint/format checks sequentially after tests. The context file names the exact commands; the generic default is whatever the repo configures (commonly `ruff check .` and `ruff format --check .` for Python when available). Skip lint cleanly if no linter is configured.

### All Tests (no target specified)

**Decide execution mode first.** Count the test files:

```bash
TEST_FILE_COUNT=$(find tests/ -name "test_*.py" 2>/dev/null | wc -l | tr -d ' ')
```

**Run tests DIRECTLY (no agent dispatch) if ANY of these are true:**
- `--direct` flag is set
- Test file count is below `PARALLEL_DISPATCH_THRESHOLD` (50)
- Previous parallel dispatch in this session failed to produce output

When running directly, execute as a single command (e.g. `pytest tests/ -v --tb=short`), then run lint (if enabled) and skip to **Result Aggregation**.

**Otherwise** (50+ test files, no `--direct`, no prior agent failures), load `parallel-dispatch.md` and dispatch parallel subagents per test directory, with the 2-minute timeout fallback to direct execution it describes.

## Result Aggregation

After all runners complete, present a summary table:

```
## Test Results

| Suite | Status | Passed | Failed | Skipped | Duration |
|-------|--------|--------|--------|---------|----------|
| unit | PASS | 42 | 0 | 2 | 3.1s |
| integration | FAIL | 8 | 1 | 0 | 12.4s |
| tools | PASS | 15 | 0 | 0 | 1.8s |
| lint (check) | PASS | - | - | - | 0.5s |
| lint (format) | PASS | - | - | - | 0.3s |

### Failures

**integration::test_api_auth.py::test_expired_token**
AssertionError: Expected 401, got 200
  File "tests/integration/test_api_auth.py", line 45
```

**Final verdict:**
- If ALL suites pass: report `ALL TESTS PASSED`
- If ANY suite fails: load `baseline-verification.md` and run **Failure Baseline Verification** before reporting the final verdict. It classifies each failure as FLAKY (passes on retry), REGRESSION (fails on branch, passes on main — blocking), or pre-existing (fails on main too — non-blocking), and applies a 3-attempt regression circuit breaker that escalates to `status: blocked` / `next_skill: /do-plan` when fixes are not converging. Only regressions block the pipeline.

## CWD-Relative Execution

All commands run relative to the current working directory. Do not attempt to detect or navigate to worktrees. When `/do-test` is invoked:
- From `/do-build`: CWD is already the worktree -- commands run there
- Directly by user: CWD is the main repo -- commands run there

Simply use the CWD as-is.

## Error Handling

- If the test runner is not installed (e.g. `pytest` missing), report the error clearly
- If a test directory does not exist, skip it silently (do not fail)
- If git commands fail for `--changed`, fall back to running all tests
- Parse the runner's exit codes (pytest: 0 = all passed, 1 = some failed, 2 = error, 5 = no tests collected; see `PYTHON.md`)

## Quality Checks and Exception Swallow Gate (Post-Test)

After tests pass, load `quality-gates.md` and:

1. Run the quality scans (exception swallow scan, empty-input check, closure coverage flag, stale xfail hygiene) and include results in the report. These are advisory.
2. Run the **Exception Swallow Gate** on the diff. This is **mandatory** — always, after tests pass and before OUTCOME emission. If the gate fails, emit `<!-- OUTCOME {"status":"fail","stage":"TEST","artifacts":{"swallow_gate":"failed","new_swallows":[...]}} -->` and stop. Do NOT emit a success OUTCOME.

## OUTCOME Contract Emission

As the very last line of your final response, emit an OUTCOME contract so the pipeline can classify the test result programmatically:

- **Success** (all tests passed): `<!-- OUTCOME {"status":"success","stage":"TEST","artifacts":{"passed":<N>,"failed":0}} -->`
- **Fail** (test failures found): `<!-- OUTCOME {"status":"fail","stage":"TEST","artifacts":{"passed":<N>,"failed":<N>}} -->`
- **Partial** (tests passed but with flaky tests or warnings): `<!-- OUTCOME {"status":"partial","stage":"TEST","artifacts":{"passed":<N>,"failed":0,"flaky":<N>}} -->`

This structured output is parsed by the repo's pipeline harness (Tier 0) before any text pattern matching — the context file names the exact parser when the repo has an SDLC pipeline.

## Notes

- No temporary files in the repo -- use `/tmp` for any scratch work
- Do not modify any source or test files -- this skill is read-only (it runs tests, it does not fix them)
- Keep the raw test-runner output visible -- developers need to see it for debugging
