---
tracking: none
slug: c3-cancel-kills-turn
type: build
status: built
critique_rounds: 1
review_rounds: 2
governance_grant: none
---

# A cancelled turn stops its harness

Track C3 of `rebuild-finish-prompt.md`, item 1 of the follow-up triage
(`~/src/valor-build-notes/triage-followups.md`). A bug fix: it changes what
`core/runs.py` does when a turn is cancelled and adds no check, gate, hook,
round, or review step, so no `governance_grant` is needed. It is kernel
code, so it gets two review rounds.

## Incident

`Kernel._apply` (`core/serve.py:501-508`) cancels the background job before
a rollout fast-forwards, migrates and restarts; `Kernel.close`
(`core/serve.py:828-831`) cancels every job when the kernel shuts down.
When the job is inside a turn, the cancellation lands in `_run_turn` and
the harness keeps running. Until the kernel process exits, its gateway
still honours the turn's grant, so the harness keeps calling the provider
and spending; after the exit it keeps running with no gateway until the
next start's recovery reaps it.

## Cause

`core/runs.py:214-216`:

```python
            except BaseException:
                pumped.cancel()
                raise
```

When `run_turn` is cancelled (or anything inside the block raises) after
the harness started, this handler cancels only the pipe copy. It does not:

- kill the harness's process group (`_kill_group`, which the stop and
  preempt paths call at `core/runs.py:201`);
- retire the turn's gateway grant or cut its in-flight calls (the normal
  end does both at `core/runs.py:207-208`; the stop does it at `:200`);
- cancel the `finished`, `stopped` and `preempted` waiters
  (`core/runs.py:191-194`), so `_stop_heard` still reads a listener that
  the `finally` at `:217-218` closes;
- reap the processes the turn left outside its group (`reap`, `:211`);
- write `turn.ended`, so the turn has no end until a kernel restart's
  recovery (`core/serve.py:224-257`) ends it as `interrupted`.

The same gap exists for a cancellation that lands after `turn.started` is
committed (`:171`) but before `create_subprocess_exec` returns (`:178`):
nothing in `_run_turn` retires the grant then, and a harness that was
already spawned has no handle.

## Fix

In `core/runs.py`, `_run_turn` and one new helper, `_cut_short`:

1. **One handler covers the turn from its output files on.** `proc` and
   `pumped` start as None and the waiters as an empty list. The `try`
   begins before the output directory is made and the files are opened
   (through an `ExitStack`, so they close only after the handler), so a
   cancellation or a kernel error from there to the end of the pipe copy
   runs `_cut_short`. A cancellation that lands while the dispatch
   connection closes, or during the normal recording after the files
   close (`transcripts.copy`, `turn_spent`), is outside it and leaves
   today's state for recovery.
2. **Kill first, with no await before it.** So a second cancellation
   cannot skip them:
   - `_kill_group(proc.pid)` only while `proc.returncode is None`. The
     harness runs with `start_new_session=True`, so its pid is its group
     id, and the unreaped leader holds that number. Once asyncio has
     reaped the leader, the number may be recycled for another session,
     so the group is not signalled; `reap` finds what is left by the
     group, the turn's mark and its sandbox name.
   - `gateway.retire(task_id)` and `gateway.cut(task_id)`, as the normal
     end does. Not `gateway.revoke`: `revoked` is never cleared, so it
     would refuse the task's next turn in this kernel, and a cancelled
     turn is not a stopped task.
   - cancel every waiter (`finished`, `stopped`, `preempted`).
3. **Then reap and record.** `reap(turn_id, pgid)` in a thread: a second
   cancellation stops only the wait, and the reap runs to its end. Then
   `await proc.wait()`, and `await pumped` only when it is not already
   done (a cancellation that landed on the gather has cancelled it, and a
   pump that failed holds the error being handled). Then
   `gateway.drain`, and in one transaction `turn.reaped` (when anything
   was reaped) and `turn.ended` with `outcome: "interrupted"`, `reason`,
   `returncode`, `result: {}`, the two output paths and
   `metered_usd_micros`: the fields recovery writes, plus the files.
   `reason` is `"cancelled"` for a `CancelledError` and
   `"<type>: <message>"` for anything else.
4. **The original exception is re-raised.** A failure to write the rows
   is attached to it as a note and recovery ends the turn; the kill and
   the reap are outside that `try`.

The stop, preempt, done and failed paths run the same lines in the same
order as before.

Docs, status quo only:

- `core/runs.py` module docstring: a cancelled turn kills its group,
  retires its grant, reaps, and ends `interrupted`.
- `docs/data.md:117`: the `turn.ended` row's outcome list says
  `interrupted` also when the turn is cancelled (a rollout or shutdown
  cancels the running job), with `reason`.
- `docs/architecture.md` near line 449, where recovery ends turns with no
  end: one clause that a cancelled turn ends itself the same way, and
  recovery ends only those a killed kernel left.

## Done

- [x] Cancelling `run_turn` while its harness runs kills the harness and
  every process in its group before the cancellation propagates, and
  reaps processes that left the group but carry the turn's marker.
- [x] After the cancellation the turn's grant is gone: a call on its base
  URL is refused, and no call of the task is left in flight.
- [x] For a cancellation inside the handler's span, the turn's last rows
  are `turn.reaped` (when a process was reaped) and `turn.ended` with
  `outcome: "interrupted"`, `reason: "cancelled"`; recovery at the next
  start finds no turn of it to end. A kernel error in the same span is
  recorded with its own type and message.
- [x] The task's next turn in the same kernel runs (grant not fenced, turn
  slot released).
- [x] A second cancellation during the cleanup still leaves no process of
  the turn alive; the turn then has no end and recovery ends it. The two
  windows outside the handler (the dispatch connection's close, the
  normal recording) are left to recovery as before.
- [x] A turn that finishes, fails, is stopped or is preempted writes the
  same rows as before; the existing tests for those paths pass unchanged.
- [x] Docs above updated; suite and ruff clean.

## Threat model

- **Killing the wrong processes.** The kill is `os.killpg` on the
  harness's own pid, which is its group id because the kernel started it
  as a session leader; the reap uses `_turn_processes`, which matches only
  this user's processes in that group, with the turn's `VALOR_TURN` mark,
  or under a sandbox denying `valor.turn.<turn_id>`. No pattern, no name.
  The group is signalled only while the leader is unreaped, when the
  number is still the turn's; after that, a recycled pid could lead a
  stranger's session, so only `reap`'s matching applies.
- **A harness that resists.** SIGKILL to the group is not catchable. A
  process that escaped the group by `setsid` is found by its environment
  mark or sandbox mark, the same rule the normal end relies on
  (`tests/test_reap.py`).
- **Spend after cancellation.** The grant is retired and in-flight calls
  cut synchronously, before any await, so no new call is accepted after
  the handler starts; calls already in flight are charged, as on every
  other end (`gateway.drain`).
- **Fencing a live task.** Using `retire` and `cut` instead of `revoke`
  leaves the task able to run its next turn; a stopped task stays fenced
  by its `task.stopped` row, which this change does not touch.
- **Ledger consistency.** The new `turn.ended` has the shape recovery
  already writes, so no reader meets a new outcome. If it is not written,
  the state is exactly today's, and recovery handles it.

## Tests

In `tests/test_reap.py`, beside the reap tests whose helpers they share,
real subprocesses and the test database
(`VALOR_TEST_DB=valor_rebuild_test_c3build`, ports 6490-6499):

1. `test_a_cancelled_turn_kills_its_harness_and_children_and_ends_interrupted`:
   the harness starts a `sleep` in its group and a `setsid` daemon, writes
   their pids, and sleeps. The test cancels the `run_turn` task and
   asserts `CancelledError`; harness, child and daemon gone; a bystander
   alive; the base URL refused 403; the last rows `turn.reaped` (naming
   the daemon) and `turn.ended` `interrupted`/`cancelled`; `serve.recover`
   ends no turn of it; the task's next turn on the same gateway ends
   `done`.
2. `test_a_turn_cancelled_again_during_its_cleanup_leaves_nothing_running_and_recovery_ends_it`:
   the daemon ignores SIGTERM, so the reap sits in its grace. The test
   cancels once, waits until harness and child are gone and the daemon is
   still alive (the cleanup is inside the reap), cancels again, and
   asserts everything is gone, no `turn.ended`, and recovery ends the
   turn with `reason: "kernel restarted"`.
3. `test_a_turn_that_ends_on_its_own_writes_only_its_end`, exit 0 and 1:
   the only row after `turn.started` is `turn.ended` `done` or `failed`
   with no `reason`, and no grant is left.
4. `test_a_turn_the_kernel_fails_after_its_start_ends_with_the_failure_named`:
   the output directory cannot be made; the `OSError` is raised, the grant
   is gone, and `turn.ended` names the error type.

1, 2 and 4 are red on the code before the fix; 3 pins what must not
change. The existing stop, reap and exit tests run unchanged.

## Merge order

It touches `core/`, so it merges in the core order after A1. As a small
bug fix it may go before A3, A2 and B1; the lead decides.

## Questions for Tom

None.

## Records

### Critique round 1 (2026-10-09)

Verdict revise (`~/src/valor-build-notes/critic-c3-r1.md`); rounds spent,
so the findings rode into the build. Applied: the group is killed only
while the leader is unreaped (pid reuse); `pumped` starts None and is
awaited only when not done; `reason` is `cancelled` only for a
`CancelledError`, else the error's type and message; test 2 cancels
once, waits until the cleanup is inside the reap, then cancels again;
the Done items name the two windows left to recovery. Tests moved to
`tests/test_reap.py` for its daemon helpers, and test 4 added for the
non-cancellation reason.

### Build

Built on `origin/valor-cori-rebuild` at d46f115bb. `core/runs.py` gains
`_cut_short` and one handler from the output files to the end of the pipe
copy; the four tests in `tests/test_reap.py` (1, 2 and 4 red before the
fix). Docs: `core/runs.py` docstring, `docs/data.md` `turn.ended` row,
`docs/architecture.md` recovery paragraph.

Suite `-m "not container"` on `valor_rebuild_test_c3build`, ports
6490-6499: 1794 passed, 24 skipped, 2 failed; both failures
(`test_the_suite_gets_fresh_services_and_the_tasks_come_back`, a task
Postgres that did not start; `test_governance_judges_every_hunk_and_the_kernel_makes_the_instances`,
a `git remote add` in setup) pass rerun alone (2 passed). Container tests
were not run to the end: the container runtime was wedged under a
concurrent container suite of another builder (`container system status`
hung for minutes); none of them calls `run_turn`. Ruff check and format
clean.
