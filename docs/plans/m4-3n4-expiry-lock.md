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
0))`, the form `_start_project` and `slot.py` use) after `ensure` returns a
live objective. Hold it across reading `runs`, the runner, and the
`routine.ran` append. Release it in a `finally`. The connection is
autocommit (`db.connect`), so the first firing's sweep task and its
`routine.ran` row are committed before the second firing gets the lock. The
second firing then reads that row and returns `continued` for the same run.
`ensure`'s transaction-scoped lock on the same key comes first and is
released before this one is taken. Advisory locks are re-entrant within a
session, so the two never conflict. If the process dies, Postgres releases
the session lock.

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

- `docs/routines.md`, expiry section: one sentence. A firing holds
  `routine:expiry` from reading the runs to recording `routine.ran`, so a
  firing at the same moment waits and continues the open sweep.
- `docs/plans/m4-3-routines-record.md`: in the Merged section's follow-up
  list and the "Follow-ups, not planned" line, mark N4 as fixed and point to
  this plan.

## Tests (`tests/test_expiry.py`, the `world` fixture's own database)

1. **Two concurrent firings start one sweep.** Seeded guards are due at
   `NOW`. Two connections call `routines.run(conn, "expiry", {"expiry":
   routines.expiry_runner}, now=NOW, start_project=fake)` under
   `asyncio.gather`. `fake` sleeps (about 0.5 s) before it calls
   `tasks.start_child` with the sweep marker, so the window is wide. Assert:
   `fake` was called once; the objective has one child; the two printed
   lines are one `started` and one `continued`; both `routine.ran` rows name
   the same run. The builder runs this test against the unfixed `run` first
   and records that it fails there.
2. **A runner that raises releases the lock.** The first firing runs with
   `start_project=None`, so `expiry_runner` raises `Refused` with items due.
   A second firing on another connection (with `fake`) then finishes within
   a timeout and starts the sweep.
3. **Only expiry takes the lock.** Another connection holds
   `routine:<name>` for a non-expiry routine written with `write_toml` and a
   no-op runner. `routines.run` of that routine still finishes within a
   timeout.

The existing `tests/test_routines.py` and `tests/test_expiry.py` must pass,
in particular `test_an_unfinished_run_is_continued_not_started_again`,
`test_a_racing_second_restart_registers_one_objective`,
`test_a_second_emulator_process_says_already_running`, and the CLI test
`test_items_due_start_one_project_task_the_kernel_carries_to_a_held_merge`
(a real `_start_project` running under the lock). Lint as the project runs it.

## Tech debt

None added.
