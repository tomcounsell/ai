# Flaky suite: tests reading each other's state in the shared database

A bug fix (hard rule 9). This page exists only for the stage record; the
build deletes it in its commit, so main gets no plan file.

**Stakes.** Test-only: a wrong fix reddens or hides a test, with no
production effect, and is undone by a revert.

## Cause (observed 2026-11-24, reproduced here)

A session's (or xdist worker's) one database outlives each test, so which
tests share a worker decided the result:

1. `test_kernel` Slack dev-channel test and `test_handon` both name the room
   `T0ABC/C0DEV`; a chat has one session, so the second to run binds into the
   first's session. kernel→handon: 4 handon tests fail, every run; handon→kernel:
   the kernel test fails (both at base, both orders).
2. handon case 3 asserts no `effect.held`/`release.requested` anywhere in the
   database.
3. `test_own_posts` reuses `test_workspace_dms`'s Kim DM and message ids
   (failed under `--dist loadfile`; reproduces running the two files in order).
4. `test_call` restart tests assert on everything `call.sweep` ended; another
   test's call opened >90 s earlier is swept too (failed in a serial run). They
   also drop the database before the orders lane takes its starting row
   (forced with a 0.5 s delay in `db.listen`: both fail).
5. Time: the review's 6:42 is a serial run (here 6:41). `-n auto` took
   1:28–1:49. One rollout test waits out `BRIDGE_WAIT_S` (60 s) for nothing.

## Fix (commit b74c30cf2, tests only)

Own room ids (per-run tag in handon; uuid in the kernel test; own DM in
own_posts); case 3 scoped to its two sessions; call tests look only at their
own call (as `test_meet` already does) and wait for the lane's listener via the
`restart` fixture; the rollout test sets `BRIDGE_WAIT_S` to 0.

Out of scope: production code (the orders lane's start row, the global
sweep); no retries, skips, reruns or new gates.

## Tests that show it

- kernel→handon and handon→kernel, workspace_dms↔own_posts: pass both orders.
- call restart tests: pass with the forced 0.5 s listener delay (fail without).
- Full suite after the commit: `-n auto` 5/5 (61–66 s), `--dist loadfile`
  `-n 3`/`-n 7` 2/2, serial 1/1 (5:04); 964 passed, 21 skipped each.

Build: delete this file, rerun the full suite, merge.
