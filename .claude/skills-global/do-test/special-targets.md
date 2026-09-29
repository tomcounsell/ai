# Special Targets: Frontend and Happy Paths

Neither target runs the project's unit-test runner.

## `frontend <url> "<scenario>" [-- steps: ...]`

Dispatch one `frontend-tester` subagent; it owns all browser interaction, and
this skill never drives the browser itself.

```
Task({
  description: "Frontend test: <scenario>",
  subagent_type: "frontend-tester",
  prompt: "
URL: <url>
Scenario: <scenario>
Steps:
  <steps if given, otherwise inferred from the scenario>
Expected: <inferred from scenario>
  ",
  run_in_background: false
})
```

On an all-tests run, if `tests/frontend/` holds scenario files (`.json`/`.yaml`
with `url`, `scenario`, `steps`, `expected`), dispatch one `frontend-tester` per
file in the same message as the other runners. Report frontend rows (Status,
Passed, Failed, Screenshot path) in the summary table.

## `happy-paths`

Applies only when the context file declares a deterministic happy-path runner
and scenario directory; otherwise report "no happy-path runner configured in this
repo" and skip. Run it directly with Bash (no subagent) and include its pass/fail
counts in the summary table. On an all-tests run, include it when the declared
scenario directory has scripts.
