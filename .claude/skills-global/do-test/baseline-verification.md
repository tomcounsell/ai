# Failure Baseline Verification

Loaded when tests fail on a feature branch. Objective: label every failure FLAKY,
REGRESSION (blocking), pre-existing, or inconclusive with evidence from a real
run. Never call a failure "pre-existing" without that evidence.

Skip it on `main` (no baseline to compare) and when more than 50 tests fail
(systemic: report the failure instead of classifying each test).

## 1. Flaky filter

Re-run only the failing tests once on the branch. Tests that pass on retry are
`FLAKY`: list them in their own table, don't count them as failures, and don't
send them on. If all pass, stop here.

## 2. Classify against main

Run the bundled script from this skill's directory with the project's own
interpreter, passing the still-failing node IDs:

```bash
<project-python> <skill-dir>/scripts/baseline_verify.py [--copy SRC[:DEST] ...] <failing-node-ids>
```

It checks out `main` in a throwaway worktree, runs only those tests there, and
prints JSON: `baseline_commit`, `regressions` (pass on main), `pre_existing`
(fail on main), `inconclusive` (error, skip, missing on main, or setup failure),
and `raw_output`. It always cleans up after itself. `--copy` brings gitignored
files the tests read into the worktree; the context file names them. `--help`
has the rest.

Report the result as a table (Test | Branch | Main | Verdict) headed with the
`baseline_commit`, plus counts per bucket.

## 3. Regression circuit breaker

Carry `regression_fix_attempt` and `persistent_regressions` in the OUTCOME
artifacts so the next run can read them from its prompt context. If the prior
run's regression set is identical, increment the attempt; if it changed, reset
to 1; with no prior context, this is attempt 0. At 3 attempts with regressions
remaining, emit `status: blocked`, `next_skill: /do-plan`, `failure_reason:
"Regression fix not converging after N attempts. Escalating to planning."`, and
the `persistent_regressions` list.

## Verdict

Only regressions block.

| Result | Status |
|---|---|
| Any regression | `fail` |
| Only pre-existing / inconclusive / flaky | `partial` |
| Circuit breaker tripped | `blocked` |
