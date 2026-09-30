---
name: do-test
description: "Run and interpret meaningful tests for a change, plan, or failure using the repository’s actual test environment."
---

# Do Test

Identify the requested target and changed behavior. Read the repository's test guidance, interpreter pin, fixtures, and meaningful existing checks. Choose the smallest suite that establishes correctness, widening only for unresolved risks or failures. Do not write tests that merely mirror wording or implementation.
In Valor use `scripts/pytest-clean.sh`, never bare pytest, with a correctly provisioned worktree venv matching `.python-version`. The wrapper pins PYTHONPATH and controls worker cleanup. Never kill processes by pattern; use only the documented scoped reap utility. Test Redis uses `tests/db_claim.py` and ORM access; do not point an ambient debug shell at production through `setdefault`.
Capture command, exit status, and results. For a failure determine whether it is introduced, pre-existing, flaky, or environmental with evidence; do not infer a clean baseline. Re-run targeted checks after fixes, not the entire suite by reflex. Track long-running processes to completion within the task.
For UI or integration behavior exercise the actual boundary when feasible, without unauthorized external writes. Report passed/failed/skipped counts, concrete failures, and untested limits. Never mark an interrupted, unavailable, or still-running test as passed. Emit managed stage markers only when the current lane's caller assigns that responsibility.
