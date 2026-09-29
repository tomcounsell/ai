# Parallel Dispatch (All-Tests Runs)

Loaded for an all-tests run with at least 50 test files and no `--direct`.
Dispatch one runner per existing test directory (`tests/unit/`,
`tests/integration/`, `tests/e2e/`, `tests/performance/`, `tests/tools/`, plus
top-level test files in `tests/`), and one lint runner if lint is enabled.

Constraints:

- **All dispatches in one message, all `run_in_background: false`.** Same-message
  foreground calls run concurrently. Background dispatch is unsafe here: a forked
  context gets one turn and never receives the completion notice (#1915), and
  some sessions refuse background spawns outright (#2420).
- **Every runner carries the 10-minute `HARD BOUND` inside its own command.** A
  hung foreground child holds the whole run until the session turn cap destroys
  the result; only the child can end its own hang.
- Runners execute one command and report, so they run on `haiku` with a
  Bash-capable agent type.

```
Task({
  description: "Run [suite-name] tests",
  subagent_type: "general-purpose",
  model: "haiku",
  prompt: "In [CWD], run: <test-runner command for [test-path]>

    HARD BOUND: finish within 10 minutes. Pass the runner's own timeout flag
    when it has one (pytest: `--timeout=<seconds>`). If the command is still
    running at the bound, kill it and report `TIMEOUT` on the first line with
    whatever output you captured.

    Report passed, failed, skipped counts, failure details, and the raw output.
    Do not edit files, fix failures, or run anything else.",
  run_in_background: false
})
```

```
Task({
  description: "Run lint checks",
  subagent_type: "general-purpose",
  model: "haiku",
  prompt: "In [CWD], run: <repo lint/format check commands>

    HARD BOUND: finish within 10 minutes; if still running, kill it and report
    `TIMEOUT` on the first line with whatever output you captured.

    Report pass/fail per tool and any issues. Do not edit files or apply fixes.",
  run_in_background: false
})
```

If a runner returns an error, a `TIMEOUT`, or anything other than test output,
don't retry the dispatch: run that group directly, and name the groups that fell
back in the aggregated result so a partial dispatch never reads as a full
parallel run.
