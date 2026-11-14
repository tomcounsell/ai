# Verify red: one test's provisioning git killed by another worker; the red's message lost

## What happened (observed, verify run 275ce027cdd0, 2026-11-14, at 812a0ae8f)

The run's `suite.ran` row keeps the pytest tail. It shows the real error:
`ERROR at setup of test_the_clone_origin_and_mirror_borrow_the_caches_history`.
The `made` fixture's `workspace.provision` raised
`Refused: provisioning failed: git push -q --no-verify:`. The push failed
and its stderr was empty. The test passes alone, and the full suite passed
locally with `-n auto` (731 passed).

**Cause 1: tests share a task id across xdist workers.** `tests/test_small_workspaces.py`
provisions as `"a1b2c3d4e5f6"` in every test, and so does `tests/test_check_dirs.py`
(`TASK`). Provisioning git carries the mark `VALOR_TURN=provision-<task>`.
`workspace.remove(task)` calls `runs.reap("provision-<task>")`, which kills
every process of this user that carries that mark, on the whole machine.
`test_an_upstream_force_push_and_gc_in_the_cache_leave_the_workspace_whole`
calls `workspace.remove("a1b2c3d4e5f6")`. When it runs on one worker while
another worker's fixture is pushing, it kills that push. Reproduced here: a
process started with `VALOR_TURN=provision-a1b2c3d4e5f6` is killed by
`workspace.remove('a1b2c3d4e5f6', <any layout>)` run from another process, and
Xcode's git is visible to `runs._turn_processes`. `test_check_dirs` has the
same exposure: `checks.suite(task_id=TASK)` marks and reaps `test-<TASK>-head`
across its tests. Real task ids are unique, so production is not affected.

**Cause 2: the verify routine no longer sees any failure message.** Since
6908ea3c1 (2026-11-10), `checks.suite` runs inside `workspace.fresh`, which
removes the check directory, `tmp/junit.xml` included, when the run ends. The
runner then calls `checks.junit_failures` on that directory
(`routines/verify/runner.py:283`), gets nothing, and falls back to `UNREAD`.
Every verify red since 2026-11-10 says "could not be read again" (tasks
1895b1f411ca, c185bfc58611, b77bf5a044c1, this one).

## Fix (one commit with its tests, hard rule 9)

1. `tests/test_small_workspaces.py` and `tests/test_check_dirs.py` give each
   test its own task id from `ledger.new_id()`, in place of the literals
   `a1b2c3d4e5f6` / `b1b2c3d4e5f6` / `TASK`. Pass it through the fixtures
   (`made` returns it). No production change for this cause.
2. `core/checks.py`: `checks.suite` keeps the report beside its logs. After
   the run and before `fresh` removes the directory, it moves
   `<check>/tmp/junit.xml` to `checks/<check>.junit.xml`:
   `os.rename(..., src_dir_fd=, dst_dir_fd=)`, with `<check>` and `tmp` each
   opened with `O_NOFOLLOW | O_DIRECTORY`. The rename moves a link as a link
   and follows none. A missing file or a `tmp` that is not a plain directory
   means nothing is kept. `suite` deletes any earlier
   `checks/<check>.junit.xml` before it runs, so a run that writes no report
   never reads the last one. `_junit_tree` reads `<name>.junit.xml` from the
   checks directory with the same `workspace.read_turn_file`. The unused
   `relpath` parameter goes, and so does the second directory open.
   `read_junit` and `junit_failures` keep their signatures apart from that,
   so the runner's call stands as it is. Refusal reasons (link, FIFO, sparse
   file, DOCTYPE) still reach `suite.ran`'s `junit` field.

No check, gate, guard or validator is added. No governance.

## Tests

- `tests/test_small_workspaces.py`: all four tests pass, alone and with
  `-n 4`, repeated 10 times.
- `tests/test_check_dirs.py`: extend
  `test_a_suite_runs_checkout_goes_after_its_junit_is_read`. The suite writes
  a report with one `<failure message="boom">`. After the run, the checkout
  is gone, `checks/<check>.junit.xml` exists, and
  `checks.junit_failures(lay.checks, name)` returns that case with message
  `boom`.
- Beyond the obvious case: a second run at the same commit that writes no
  report gives `tests is None` and `junit_failures == []`, so the stale
  report is not read. A suite that writes `tmp/junit.xml` as a symlink to a
  real report gets a refusal in `junit` that says it is a link, and the
  target is not read. A suite that replaces `tmp` with a link to a directory
  holding a `junit.xml` keeps nothing, and the target is not moved.
- The full suite with `uv run pytest -q -n auto -p no:cacheprovider`, and
  ruff.

## Out of scope

- Making `runs.reap` marks unique per machine user or per process tree. In
  production a task's mark is unique, and the overlap comes from the tests.
- `GitError` naming git's exit status or signal. Useful, but not this defect.
- The resumed-run fallback (`failures_of` / `UNREAD`). It stays for a run
  whose workspace is gone.

**Stakes.** A wrong move would lose or misreport verify findings and merge
checks. It is reversible, and covered by `test_check_dirs`. Critique 0,
review 0 (a bug fix, hard rule 9).
