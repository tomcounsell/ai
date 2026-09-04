## Verification

Every command below is instantaneous, hermetic, and deterministic (the
`sleep` row is bounded by the short timeout the parity test passes both
runners). Used by `test_both_runners_agree_on_execution_fixture` to assert
`validate_build`'s per-check verdicts equal `run_checks`' verdicts row for
row, across every `evaluate_expectation` branch **and** across the two
dispositions the runners used to disagree on: a timeout (`FAIL` at 120s vs
`SKIP` at 30s) and an expectation neither grammar recognises.

| Check | Command | Expected |
|-------|---------|----------|
| exit code N | `true` | exit code 0 |
| exit code != N | `false` | exit code != 0 |
| output contains X | `echo hello` | output contains hello |
| output does not contain X | `echo hello` | output does not contain goodbye |
| match count == 0 | `grep -c zzz /dev/null` | match count == 0 |
| output > N | `echo 1` | output > 0 |
| exit N | `true` | exit 0 |
| prints N | `echo 0` | prints `0` |
| >= N | `echo 5` | >= 1 |
| == N | `echo 0` | == 0 |
| empty output | `true` | empty output |
| a real failure | `echo 1` | == 0 |
| timeout | `sleep 30` | exit code 0 |
| unparseable expectation | `true` | banana |
| no backticked span | echo hi | exit code 0 |
