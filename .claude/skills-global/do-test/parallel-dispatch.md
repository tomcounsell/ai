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

**Make every Task call in this step in a SINGLE message, all with
`run_in_background: false`.** The harness executes same-message foreground
calls concurrently and blocks until all of them return, so that is how
parallelism is achieved here. Never use `run_in_background: true`: this skill
can run in a forked context that gets exactly one turn, and a fork has no later
turn on which to receive a background notification (issue #1915). Background
dispatch is also refused outright at the tool boundary in some sessions
(issue #2420), which turns the whole step into an error rather than a slow path.

**Every dispatched group carries a hang bound (`GROUP_TIMEOUT`, 10 minutes).**
Foreground dispatch means the parent blocks until the child returns, so a child
that hangs holds the whole run until the session's turn cap (7200s) fires and
destroys the result. The bound belongs inside the child's own command, which is
the only place that can end a hang. Carry the `HARD BOUND` paragraph verbatim in
every dispatch prompt below.

For each existing test directory/group, create a Task:

```
Task({
  description: "Run [suite-name] tests",
  subagent_type: "test-engineer",
  model: "sonnet",
  prompt: "Run the following test command and report results:

    cd [CWD]
    <test-runner command for [test-path]>   # e.g. pytest [test-path] -v --tb=short

    HARD BOUND: finish within 10 minutes. Pass the runner's own timeout flag
    when it has one (pytest: `--timeout=<seconds>`). If the command is still
    running at the bound, kill it and report `TIMEOUT` on the first line along
    with whatever output you captured. Never wait indefinitely.

    Report: number of tests passed, failed, skipped, and any failure details.
    Output the raw test-runner output.",
  run_in_background: false
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

    HARD BOUND: finish within 10 minutes. If a check is still running at the
    bound, kill it and report `TIMEOUT` on the first line with whatever output
    you captured. Never wait indefinitely.

    Report: pass/fail for each tool, and any issues found.",
  run_in_background: false
})
```

## Step 3: Collect results

Because every Task in Step 2 ran in the foreground, all outputs are already in
hand when the calls return. There is no polling loop to manage; the hang bound
lives inside each child's command. Proceed straight to Result Aggregation
(SKILL.md).

**If a Task returns an error, a `TIMEOUT`, or anything other than test output**
(dispatch refused, agent died, the runner never started, or the group hit
`GROUP_TIMEOUT`), do not retry it blindly. Fall back to direct execution for the
groups that failed to report, e.g.:

```bash
pytest [test-path] -v --tb=short
```

Run the repo's lint/format checks directly too if the lint Task failed to report
(commands per the context file; generic default `ruff check .` /
`ruff format --check .` when available). Name which groups fell back in the
aggregated result, so a partial dispatch never reads as a full parallel run.
