# A test's workspace removal no longer kills another worker's provisioning

**Defect (observed, verify run d0df4be7c865, 2026-11-14, head 812a0ae8f).** Under
`pytest -n auto`, the `made` fixture of
`test_the_clone_origin_and_mirror_borrow_the_caches_history` errored at setup:
`Refused: provisioning failed: git fetch -q --no-tags:` followed only by git's
`info/grafts` deprecation hint (every kernel git call prints it, since
`core/git.py` sets `GIT_GRAFT_FILE=/dev/null`). A git that exits non-zero with
no error of its own was killed.

**Cause.** `workspace.provision` marks its git calls `VALOR_TURN=provision-<task>`,
and `workspace.remove(task)` calls `runs.reap("provision-<task>")`, which stops
every process of this user carrying that mark, machine-wide. Every test in
`tests/test_small_workspaces.py` provisions the same id `a1b2c3d4e5f6` (so does
`tests/test_check_dirs.py:82`), and
`test_an_upstream_force_push_and_gc_in_the_cache_leave_the_workspace_whole`
calls `workspace.remove("a1b2c3d4e5f6", lay)` (line 99). When that runs on one
xdist worker while another worker's fixture is mid-provision, the reap kills
the other worker's `git fetch`. Production is right: task ids are unique, so a
mark names one task. Only the tests share an id.

**Fix (test only).** The `made` fixture provisions a fresh id per test
(`ledger.new_id()`), and the tests read it from the fixture instead of the
literal: the `remove` at line 99 then reaps only its own test's processes. The
second provisioning in that test (`b1b2c3d4e5f6`) also takes a fresh id. No
production code changes; no governance is added.

**Out of scope.** The deprecation hint that pads every kernel git error, and
`GitError` not naming a kill signal, made the cause hard to read; both are
noted for Tom, not changed here. The verify runner's "JUnit report could not be
read again" is a separate issue.

**Tests.**
- `tests/test_small_workspaces.py` passes alone.
- The file with `tests/test_check_dirs.py` under `-n 4`, repeated 10 times, has
  no setup error.
- The full suite, `uv run pytest -q -n auto -p no:cacheprovider`, is green.
- Before the fix, a script that provisions `a1b2c3d4e5f6` in a loop while
  another process calls `workspace.remove("a1b2c3d4e5f6", ...)` reproduces the
  killed fetch. That shows the race is real; the script is not committed.

**Loops.** 0 critique, 0 review: a reversible, test-only change.
