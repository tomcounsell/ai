---
tracking: none
slug: c3-cancel-kills-turn
type: build
status: planned
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

In `core/runs.py`, `_run_turn` only:

1. **One cleanup covers the whole turn after `turn.started`.** `proc`,
   `finished`, `stopped` and `preempted` start as None. The `try` that
   today begins after the subprocess starts begins before
   `create_subprocess_exec`, so a cancellation at any point after the
   `turn.started` commit runs the same handler.
2. **Kill first, with no await before it.** The handler's first acts are
   synchronous, so a second cancellation cannot skip them:
   - `_kill_group(proc.pid)` when the harness started. The harness runs
     with `start_new_session=True`, so its pid is its process group id;
     the kernel kills only that group, by number, never by pattern.
   - `gateway.retire(task_id)` and `gateway.cut(task_id)`, as the normal
     end does. Not `gateway.revoke`: revoke adds the task to `revoked`
     for the rest of the process, which would refuse the task's next turn
     in this kernel, and a cancelled turn is not a stopped task.
   - cancel the `finished`, `stopped` and `preempted` waiters that exist.
3. **Then reap and record, as the other ends do.**
   `reaped = await asyncio.to_thread(reap, turn_id, proc.pid if proc else None)`
   finds what left the group (a `setsid` daemon, a process under the
   turn's sandbox mark) by the turn's marker and sandbox name, the same
   rule as every other end. In a thread, a second cancellation stops only
   the wait: the reap itself runs to its end. Then `await proc.wait()`,
   `await pumped` (both pipes are at EOF once every process of the turn
   has ended), `await gateway.drain(task_id)`, and in one transaction
   `turn.reaped` (when anything was reaped) and `turn.ended` with
   `outcome: "interrupted"`, `reason: "cancelled"`, `returncode`,
   `result: {}`, the two output paths, and `metered_usd_micros`. These are
   the fields recovery writes for a turn with no end, plus the files, so
   the fold, `recollect` and `LAST_WORKING_ENDED` read it exactly as they
   read recovery's row. If the job is cancelled again during this second
   part, or the database is gone, the turn is left with no end and
   recovery ends it at the next start, as it does today.
4. **The original exception is re-raised.** A failure inside the cleanup's
   recording part does not replace it: the recording runs in its own
   `try`, and its exception is dropped in favour of the one being handled
   (recovery covers the missing row). The kill and the reap are not
   inside that `try`.

The stop, preempt, done and failed paths are untouched; a turn that ends
on its own runs the same lines in the same order as now.

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

- [ ] Cancelling `run_turn` while its harness runs kills the harness and
  every process in its group before the cancellation propagates, and
  reaps processes that left the group but carry the turn's marker.
- [ ] After the cancellation the turn's grant is gone: a call on its base
  URL is refused, and no call of the task is left in flight.
- [ ] The cancelled turn's last rows are `turn.reaped` (when a process was
  reaped) and `turn.ended` with `outcome: "interrupted"`,
  `reason: "cancelled"`; recovery at the next start finds no turn of it
  to end.
- [ ] The task's next turn in the same kernel runs (grant not fenced, turn
  slot released).
- [ ] A cancellation during the cleanup still leaves no process of the turn
  alive; the turn then has no end and recovery ends it.
- [ ] A turn that finishes, fails, is stopped or is preempted writes the
  same rows as before; the existing tests for those paths pass unchanged.
- [ ] Docs above updated; suite and ruff clean.

## Threat model

- **Killing the wrong processes.** The kill is `os.killpg` on the
  harness's own pid, which is its group id because the kernel started it
  as a session leader; the reap uses `_turn_processes`, which matches only
  this user's processes in that group, with the turn's `VALOR_TURN` mark,
  or under a sandbox denying `valor.turn.<turn_id>`. No pattern, no name.
  A group id cannot be handed to a new process while any member of the
  group lives, so the kill cannot reach another group while the turn's
  processes exist; once they are all gone the kill finds nothing, as on
  the stop path today.
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

In `tests/test_kernel.py`, next to the stop test, real subprocesses and
the test database (`VALOR_TEST_DB=valor_rebuild_test_c3build`, ports
6490-6499):

1. **A cancelled turn whose harness spawned children.** The harness is
   `python -c` that starts a child in its group (`sleep 60`) and a
   daemon that calls `setsid` and writes its pid to a file, then sleeps.
   The test waits for the pidfile, cancels the `run_turn` task, and
   asserts: `CancelledError` is raised; the harness, the child and the
   daemon are all gone within `reap_grace_s` plus a margin; a bystander
   `sleep` the test started itself is alive; a request to the turn's base
   URL gets 403; the task's last rows are `turn.reaped` (naming the
   daemon) and `turn.ended` with `outcome: "interrupted"`,
   `reason: "cancelled"`. Then a second `run_turn` on the same task with a
   harness that exits 0 ends `done` (grant not fenced, slot released), and
   `serve.recover` ends no turn of the task.
2. **A cancellation during the cleanup.** As above, but the test cancels
   the task twice in a row. Asserts the harness group and the daemon are
   gone, and that recovery then ends the turn `interrupted` with
   `reason: "kernel restarted"`.
3. **A finished turn is unchanged.** Parametrized over a harness that
   exits 0 and one that exits 1: the rows after `turn.started` are
   `turn.ended` with `done` or `failed`, no `reason`, and the grant is
   retired. The existing
   `test_stop_from_another_connection_kills_the_turn_and_leaves_a_consistent_ledger`,
   `test_a_daemon_the_turn_leaves_behind_is_reaped_and_ledgered` and
   `test_a_turn_that_exits_cuts_its_silent_calls` run unchanged and pass.

Each test is red on the current code except 3, which pins behaviour the
fix must not change.

## Merge order

It touches `core/`, so it merges in the core order after A1. As a small
bug fix it may go before A3, A2 and B1; the lead decides.

## Questions for Tom

None.

## Records
