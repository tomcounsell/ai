## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `echo tests-ok` | output contains tests-ok |
| Lint clean | `echo lint-ok` | output contains lint-ok |

| Anti-criterion | Command | Expected |
|-----------------|---------|----------|
| No debug prints | `grep -c "TODO_NEVER_MATCHES_ANYTHING" /dev/null` | match count == 0 |
| No stray markers | `grep -c "TODO_NEVER_MATCHES_ANYTHING_2" /dev/null` | match count == 0 |
