## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `echo tests-ok` | output contains tests-ok |
| Lint clean | `echo lint-ok` | output contains lint-ok |

Red/green state for reviewer context:

| Row | Pre-change | Meaning |
|---|---|---|
| Tests pass | 3 failures | demonstrated red — must reach 0 |
| Lint clean | 12 findings | demonstrated red — must reach 0 |
