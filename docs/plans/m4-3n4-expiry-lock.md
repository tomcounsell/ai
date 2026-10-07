# 4.3 follow-up N4: one expiry sweep per moment

Review N4 (`m4-3-routines-record.md`, Merged section): two expiry firings at
the same moment can each start a sweep. This is a bug fix. The lock it adds
is concurrency control, not a check, gate, hook, round, or review step.

Stakes: medium. A race in the kernel's own routine code that can open two
deletion branches. No stored data or schema changes, and it is easy to
revert. Critique rounds: 1. Review rounds: 1.

## The race

`routines.run` (`core/routines.py`) reads the objective's `routine.ran`
rows (`runs`) and then calls the runner. It appends this firing's
`routine.ran` only after the runner returns. `expiry_runner` decides whether a
sweep is open with `open_run(ctx)`, which reads those rows. Starting a sweep
(`_start_project`) clones and sets up a workspace, which takes seconds to
minutes. A second firing in that window sees no open sweep, folds `due`, and
starts a second sweep task on the same items. `ensure` already takes
`routine:NAME`, but only for the duration of its own transaction.

## The fix

In `routines.run`, when the routine's runner is `EXPIRY_RUNNER`, take the
session advisory lock `routine:NAME` (`pg_advisory_lock(hashtextextended(…,
0))`, the form `_start_project` and `slot.py` use) before `ensure`, outside
any transaction. Hold it across `ensure`, reading `runs`, the runner, and the
`routine.ran` append. Release it in a `finally`, which also covers the
stopped return and a runner that raises (`Refused`, or the `SystemExit` that
`_start_project` raises). `ensure`'s transaction-scoped lock on the same key
is then requested by the session that already holds the session lock, and
Postgres grants it at once: advisory locks held by one session never
conflict with each other.

Where the wait happens: a second expiry firing blocks on `pg_advisory_lock`
before `ensure`, with no transaction open. (Had the session lock been taken
after `ensure`, the second firing would instead block inside `ensure`'s
transaction, holding it open for as long as the first firing provisions.)
The connection is autocommit (`db.connect`), so by the time the first firing
releases the lock, its sweep task and its `routine.ran` row are committed.
The second firing then runs `ensure` (finding the live objective), reads
that row, and returns `continued` for the same run. If the process dies,
Postgres releases the session lock.

The second firing waits; it does not try once and give up. Waiting is what
N4 asks for: the second firing has to see the first one's open sweep.

## Out of scope

- **The emulator routine.** Its runner drives the whole replay sweep inline,
  for hours, under `run:<run>`. If it also held `routine:emulator`, a second
  firing would wait for the whole sweep, find the first run's report, and
  start a fresh sweep. Today that firing says `already running`. So the lock
  is expiry-only. The emulator has a narrower race of its own, between
  `_open` and `start_child`. It is not N4 and is not fixed here; the delivery
  names it as a product note.
- The other follow-ups (N3, N5, N7, the test check's breadth list).

## Docs

- `docs/routines.md`, expiry section: one sentence. An expiry firing holds
  the session lock `routine:expiry` from before it registers or finds the
  objective until it has recorded `routine.ran`, so a firing at the same
  moment waits, with no transaction open, and then continues the open
  sweep.
- `docs/plans/m4-3-routines-record.md`: in the Merged section's follow-up
  list and the "Follow-ups, not planned" line, mark N4 as fixed and point to
  this plan.

## Tests (`tests/test_expiry.py`, the `world` fixture's own database)

1. **Two overlapping firings start one sweep.** Seeded guards are due at
   `NOW`. `fake` (the `start_project`) sets an `asyncio.Event` `entered` when
   it is called, sleeps about 0.5 s, then calls `tasks.start_child` with the
   sweep marker. Firing A runs on its own connection as a task:
   `routines.run(conn, "expiry", {"expiry": routines.expiry_runner},
   now=NOW, start_project=fake)`. Firing B, on another connection, starts
   only after `entered` is set, so it is certain to overlap A's start, not
   run after it. Assert: `fake` was called once; the objective has one
   child; A's line says `started` and B's says `continued`; both
   `routine.ran` rows name the same run. The builder runs this test against
   the unfixed `run` first and records that it fails there (B calls `fake`
   a second time).
2. **A runner that raises releases the lock.** Firing A runs with
   `start_project=None` while items are due, so `expiry_runner` raises
   `Refused`. Assert that A raised `Refused` and that A's connection still
   answers a query (the `finally` unlock ran on it after the exception).
   Also assert that the failed firing wrote no `routine.ran` row. That is
   current behaviour (N7 is out of scope); the test pins it without
   changing it. Then, with A's connection still open, firing B on another
   connection with `fake` finishes within a timeout and starts the sweep.
3. **Only expiry holds the lock across the runner.** A non-expiry routine
   (written with `write_toml`) has a runner that sets its own
   `asyncio.Event` and then waits, with a timeout, for the other firing's
   event. Two firings of it on two connections under `asyncio.gather` both
   finish, each having reached its runner while the other was inside its
   own. Under a lock taken for every routine, the first runner would wait
   for an event that never comes, and the test would fail on the timeout.
   `ensure`'s short transaction lock serializes the two firings only while
   they register.

The existing `tests/test_routines.py` and `tests/test_expiry.py` must pass,
in particular `test_an_unfinished_run_is_continued_not_started_again`,
`test_a_racing_second_restart_registers_one_objective`,
`test_a_second_emulator_process_says_already_running`, and the CLI test
`test_items_due_start_one_project_task_the_kernel_carries_to_a_held_merge`
(a real `_start_project` running under the lock). Lint as the project runs it.

## Tech debt

None added.
