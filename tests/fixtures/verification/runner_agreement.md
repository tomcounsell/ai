## Verification

Every command below is instantaneous, hermetic, and deterministic. Used by
`test_both_runners_agree_on_execution_fixture` to assert `validate_build`'s
per-check verdicts equal `run_checks`' verdicts row for row, across all six
`evaluate_expectation` branches.

| Check | Command | Expected |
|-------|---------|----------|
| exit code N | `true` | exit code 0 |
| exit code != N | `false` | exit code != 0 |
| output contains X | `echo hello` | output contains hello |
| output does not contain X | `echo hello` | output does not contain goodbye |
| match count == 0 | `grep -c zzz /dev/null` | match count == 0 |
| output > N | `echo 1` | output > 0 |
