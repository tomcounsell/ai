---
tracking: none
slug: m4-3-routines
type: plan
status: built
critique_rounds: 1
review_rounds: 1
---

# 4.3 Routines and the status page

Milestone 4, task 3 of [valor-rebuild.md](valor-rebuild.md). Governed by
[routines.md](../routines.md), [machine.md](../machine.md) (launchd,
Concurrency), [emulator.md](../emulator.md), and the governance constraint
in [mission.md](../mission.md).

**Goal.** Scheduled work is an objective with metered spending, never a
bare script (Mission item 5, **Bounded authority, metered spending**), and
Tom reads status on a page instead of receiving status messages
(Mission item 6).

## What it builds on

**4.1, the objective tree** (`m4-1-objective-tree.md`). This plan uses:

- `tasks.start_child(conn, parent_id, *, ceiling=None, marker=None,
  **brief_fields)`: any parent but a calibration or fenced one, a ceiling
  above the parent's refused, and `marker` merged into `task.started`
  (`{"sdlc": 1}` when None);
- `tasks.ancestors` (nearest first) and `tasks.subtree`;
- `tasks.tree_spending`, whose `charges` lists every `gateway.charged` row
  in the subtree as `{task_id, call_id, usd_micros, at}` and whose
  `tree_open_calls` lists open calls;
- the tree lock `tree:<root id>`, and stop fencing every descendant.

`Brief.routine`, `Brief.replay`, and the `{"objective": NAME}` marker are
this task's.

**2.1, the resident kernel** (`m2-1-resident-kernel.md`). `python -m core
serve` (`core/serve.py`) folds every task that is not merged or stopped
and `schedule` runs one step of each ready one. A harness step needs the
turn slot, `slot.held(holder)` in `core/slot.py`: the session lock
`turn-slot:<machine>`, FIFO by Postgres's lock queue, reentrant within a
process, taken around each `run_turn` and each check, and waited on by
`python -m core run`. Ready tasks are taken in order of the id of their
latest row. So a routine run is a task
the kernel drives: `python -m core routine` starts or continues it, and
the kernel's `schedule` runs its steps.

**1.5, the emulator** (`m1-5-emulator.md`). The driver and judge live in
`tests/emulator/`. Each run has an item task, driven through the kernel,
and one calibration task (`tasks.start_calibration(conn, "emulator",
detail=...)`) whose `task.started` carries `{"emulator": {"run": RUN,
"item_task": TASK}}` and which meters the stand-in's and the judge's
calls. A run name already in `results/` with an outcome is refused unless
`--rebuild` is given.

## Done, as evidence

Each item is a test on real Postgres in the default suite unless it says
otherwise.

1. **The command.** `python -m core routine NAME` reads
   `routines/NAME/routine.toml` from the kernel's own checkout
   (`settings.routines_dir`), registers the routine's standing objective on
   its first run, starts or continues the run as a child of it with the
   toml's ceiling, and prints one line: the run's task id and outcome, its
   metered spending, and the routine's metered spending over the last 30
   days with the number of runs in them.
2. **Period spending is reported and stops nothing.** The 30-day figure is
   the sum of `tree_spending`'s `charges` on each of the routine's
   objectives with `at` within 30 days of the report, plus the charges on
   every calibration task whose
   `task.started.emulator.item_task` is in those subtrees, with open calls
   listed apart. A charge 31 days old is out; one 29 days old is in; a
   charge on a grandchild (a replay under a sweep run) is in; a stand-in
   charge on the replay's calibration task is in. A routine whose last 30
   days hold $1,000,000 of charges starts and runs exactly as one at $0.
3. **launchd calls only that.** `python -m core routine NAME --plist`
   prints the job: `ProgramArguments` is exactly the kernel checkout's
   interpreter, `-m`, `core`, `routine`, `NAME`; the schedule comes from the
   toml; the environment carries `HOME`, a system `PATH`, and the
   `routines.PLIST_ENV` settings set when it was printed (the names in
   `backup.PLIST_ENV` that reach Postgres, and `VALOR_DEMO`), never a
   secret.
4. **A routine never makes Tom's task wait for the slot.** Tom's tasks are
   foreground; a background task is routine-owned (it or an ancestor
   carries `Brief.routine`) or a replay (`Brief.replay`). `schedule` takes
   ready foreground tasks before background ones. When a foreground harness
   step is ready while a background turn holds the slot, the turn is
   preempted: its gateway grant revoked, its process group killed, its
   calls charged, its processes reaped, and `turn.ended` written with
   outcome `preempted`; the foreground step then takes the slot. The
   preempted step leaves its state unchanged and runs again, resuming the
   session, when no foreground step is ready. `tasks.audit` is empty
   throughout.
5. **The emulator sweep.** `routines/emulator/routine.toml` runs every item
   in `$VALOR_DEMO/items` in the `bare`, `clarify`, and `routed` arms, each
   replay a child of the run, judged, and records the run's report: per
   item and arm, fidelity, correctness, simplicity, hidden tests,
   attention, metered spending, and the count of preempted turns, beside
   the item's bare baseline run. It requests no effect of its own and
   writes no verdict on any task; a failed item is recorded and the next
   item runs. Shown with a scripted driver in the default suite, and with
   one real item in a `VALOR_LIVE` test.
6. **The expiry sweep.** `routines/expiry/routine.toml` folds the guards
   and routines that are due (below). With nothing due it starts no task
   and runs no turn, and records the run at $0. With items due it starts
   one task on the `valor` project whose instruction lists each item with
   its incident, mission item, grant date, expiry, and last firing (or "no
   firing record"), and the kernel carries it through the pipeline to one
   merge held for Tom's tap. A later firing while that task is open
   continues it and starts no other.
7. **The status page.** `python -m ui` serves, on 127.0.0.1 only, pages for
   tasks (state, metered spending, attention counts), one task (its status
   and ledger), pending approvals, the attention log across tasks, and
   routines (each with its 30-day spending, last run, last result, and its
   runs with their spending). Every page answers only GET; any other method
   is refused with 405. Every value from the ledger is HTML-escaped. Its
   database session is read-only, so an insert through it fails. The
   routine figures equal the command line's.

### What "due" means

One fold over kernel-written rows, `routines.due(conn, now)`:

- **A guard** (any `guard.granted` row) fires when a row of a type in
  `machine.VERDICT_ROWS` names its `guard_id` on a foreground task. A
  firing on a background task does not count: a forced `clarify` arm fires
  the request judge by construction.
- **An unfired guard** is due once `now` is past its `expires` date.
- **A guard that fired** is due 90 days after its last firing, and not
  before its `expires` date.
- **A guard a sweep listed** (an open sweep, or a merged one that kept it)
  is not due again until 90 days after that sweep listed it.
- **A seeded guard** absent from `guards.SEEDED` in the kernel's checkout
  is gone and never due.
- **An instance grant** (`guards.grant`, `guard_id` `grant-<id>`) has no
  firing record: no verdict row names it. It is due at its `expires` date,
  and the instruction shows "no firing record", not "never fired". One on a
  task whose project is not `valor` is listed under "outside this
  repository: listed, not removed".
- **A routine** is due when its first registration is more than 90 days
  old, no sweep listed it in the last 90 days, and no run in the last 90
  days led to use as [routines.md](../routines.md) defines it: an effect
  performed, a question Tom answered, or a child task that delivered. A run
  that produced only `nothing_due` is unused. The emulator sweep's replays
  deliver, so it is not listed while it runs.
- **The expiry routine never lists itself.**

## Design

### A routine

`routines/<name>/routine.toml`, changed only by a reviewed diff:

```toml
name = "expiry"
runner = "expiry"            # a runner the composition root registers
ceiling = "act"              # the merge it asks for is act, held for Tom
model = "frontier"           # a seat in core/settings.py
mission_item = "5; the governance constraint"
need = ["Mission item 5 and the governance constraint require the deletion"]
created = 2026-10-03
instruction = "..."          # the run's instruction; the runner fills in what it found

[schedule]                   # launchd StartCalendarInterval keys
hour = 4
minute = 0
```

`need` holds the two task ids (or the requirement) that show the second
need. It is stored on the registration row and shown on the page; nothing
refuses a routine on it, since refusing would be a check with no grant.

### Records

- **The objective.** A root task started with `Brief.routine` set and the
  marker `{"objective": NAME}` on its `task.started`, through a `marker`
  argument this task adds to `tasks.start`, matching `start_child`'s. Like
  a calibration task it
  carries no `sdlc`, and `schedule`, every SDLC writer, and the runners
  pass it by. Unlike one, it can be a parent and can be stopped.
- **`routine.registered`** on the `routines` stream holds the name, the
  ceiling, the toml's digest, `need`, `mission_item`, and the objective
  task id, and `replaces`: the stopped objective it follows, or null. A
  unique index on (name, ceiling, `replaces` with null read as empty) makes it one live objective
  per name and ceiling at a time. A ceiling change in the toml, or Tom's
  `--restart` after a stop, registers another; the period report sums
  every objective of the name.
- **A run** is a child of the objective (`tasks.start_child`) with
  `Brief.routine` set. For expiry it is an SDLC task (the default marker);
  for emulator it is an objective node holding the replays
  (`marker={"objective": NAME}`).
- **`routine.ran`** on the objective records each firing: the run's task
  id, the outcome, a short summary the kernel writes, and the run's
  spending. The outcome is one of `started`, `continued`, `nothing_due`,
  `finished`, `failed`, or `running`. A run's later state (waiting,
  delivered, merged, stopped) is read from its task, not from this row.
- **Foreground and background.** `tasks.background(conn, task_id)` is true
  when the task or any of its `tasks.ancestors` carries `Brief.routine` or
  `Brief.replay`.

### The command

`python -m core routine NAME`:

1. Load the toml from `settings.routines_dir`. An unknown name is an error
   naming the directory.
2. Under `ledger.lock("routine:NAME")`, register the objective if the
   ledger lacks one for (name, ceiling). If the objective is stopped, print
   `routine NAME is stopped (task ID); --restart starts it again`, write
   nothing, and exit 0: Tom's stop holds across firings.
   `python -m core routine NAME --restart`, Tom's command, registers a
   fresh objective under the same name with `replaces` set to the stopped
   one, then runs as below. No diff is needed. On a routine that is not
   stopped, `--restart` runs it as without the flag.
3. If the routine's last run is unfinished (expiry: its task is neither
   merged nor stopped; emulator: no `routine.ran` with `finished` or
   `failed`), continue it. Otherwise start a run through the runner.
4. Write `routine.ran` and print the line in Done 1.

`python -m core routine NAME --plist` prints the job (Done 3), labelled
`com.valor.routine.NAME`, logging to `settings.log_dir/routine-NAME.log`.
`python -m core routines` prints every routine with its last run, last
result, and 30-day spending, the same fold the page reads.

Runners are registered in the composition root (`core/__main__.py`,
`ROUTINE_RUNNERS`), as stage runners are: `expiry` from `core/routines.py`,
`emulator` from `routines/emulator/runner.py`.

### The expiry runner

Fold `due`. Nothing due: `routine.ran` with `nothing_due`. Otherwise it
starts one child task of the objective, as `python -m core start --project
valor --branch <the rebuild branch>` provisions one. The task has ceiling
`act`, the toml's model, `Brief.routine` set, and the instruction the
kernel renders from the due list; the due list also goes on its
`task.started`, so later folds know what it proposed. The row is
`routine.ran` with `started`; continuing it gives `continued`. The kernel's
`schedule` drives the task like any other, as background work.

The working session plans, builds, and patches the deletion like any
task: the guard's code, its seed in `core/guards.py`, its tests, and the
docs that describe it, or the routine's directory. Critique, test, review,
and docs run on it. The merge waits for Tom's tap. To keep an item, Tom
gives feedback on the delivery, which sends the task to patch.

The ledger keeps every `guard.granted` row. A deleted seeded guard leaves
`guards.SEEDED`, so `migrate` does not seed it again and `due` reads it as
gone.

### The emulator runner

It starts an objective node as the run under the routine's objective, and
holds the session lock `run:<run task>` for the driver's life, as `run`
holds `run:<task>`. A second process finding that lock held prints
`already running` and exits 0.

For each item and arm, it runs the 1.5 driver as a subprocess:
`tests/emulator/replay.py ITEM --arm ARM --parent RUN --name
ITEM-ARM-RUN --judge`. The run name carries the run's task id, so a later
sweep never collides with an earlier result. This is the same code that ran
by hand. Each item task is started with `start_child` under the run and
with `Brief.replay` set, so its turns are background work.

It reads each result file and writes the report to
`$VALOR_DEMO/sweeps/<run>.json`. The report includes each replay's count
of `turn.ended` rows with outcome `preempted`, beside its scores and
spending, since a preempted turn's resumed session costs more and differs
from the baseline's. It writes `routine.ran` with `finished` and the
summary. A crash leaves no `finished`, and the next firing resumes the
driver, which resumes runs that hold no outcome.

The driver's held local pushes are released under the standing permission
for pushes to a run's own bare origin that [emulator.md](../emulator.md)
records, as role-played approvals. No other effect is released.

**Why it is not a check.** [routines.md](../routines.md) classes a
verification run that tests Valor's own deliverables as a check. This
sweep replays fixed items to measure. It raises nothing, writes no
verdict, and refuses or holds nothing, and the rebuild plan's Done says it
"blocks nothing".

### The slot's order

These changes live in `core/serve.py` (2.1's `schedule`), `core/slot.py`
(2.1's slot), `core/runs.py`, and the check runners:

- **The sort key.** `schedule`'s order of ready tasks becomes: background
  last, then the id of the latest row, oldest first.
- **Preempting.** `slot.held`'s holder names its task. A holder for a
  foreground task sends `pg_notify('valor_preempt', '')` before it blocks;
  `schedule` and `python -m core run` both take the slot through
  `slot.held`, so both preempt. A reentrant inner `held` sends nothing.
- **Being preempted.** A background holder's `slot.held`, once it holds
  the slot, listens on `valor_preempt` and on a notification cancels the
  body it guards. Only the holder listens, so the notice needs no id.
  `run_turn`'s cancel path is the stop path without `task.stopped`:
  revoke, kill the group, drain, reap, then `turn.ended` with outcome
  `preempted`. A check runner's cancel path kills its process group and
  writes no verdict, so the check runs again.
- **After preemption.** A preempted turn did not finish. The session and
  fresh-session runners return from the step with the answer or findings
  unspent. It does not count toward the idle bound or the failed-turn
  count, and the state is unchanged, so `schedule` runs the step again.
- **No bound on the wait** while foreground steps keep coming.
- **Lock order, the same on every path.** Session locks first, then
  transaction locks. A foreground holder takes the shared lock
  `turn-slot-fg:<machine>`, sends `valor_preempt`, then takes the slot
  `turn-slot:<machine>`; a background holder takes the slot, then tries the
  exclusive `turn-slot-fg` to find a notice sent earlier and releases it at
  once. Inside a hold, a writer takes `tree:<root>` (`tasks.lock_tree`),
  then `task:<id>`, in a transaction that waits for no session lock, as 4.1
  keeps them (tree before task everywhere). No path takes the slot or the
  shared lock while a transaction holds a tree or task lock, and
  `slot.held` opens no transaction: its connection reads
  `tasks.background` and takes session locks only.
  `tests/test_slot_priority.py` runs `stop_tree`, `start_child`, a
  foreground waiter and a background holder's tree and task writers at
  once, five times.

This is a scheduling rule, not a check on the agent
([machine.md](../machine.md), Concurrency). It looks only at which task a
turn belongs to, never at the work.

### The status page

`ui/` gains `ui/__main__.py` and `ui/app.py`: an aiohttp application
(aiohttp is already a kernel dependency) rendering HTML on the server, no
JavaScript, no forms. It imports only `core/` read models:

- `tasks.index` (new): every task with its state, spending, attention
  counts, and last row time
- `tasks.status`, `ledger.read`, `broker.pending`
- `tasks.attention_log` (new): every task's attention entries in time order
- `routines.report`

It connects as the kernel role with `default_transaction_read_only` set on
its session. It binds 127.0.0.1 on `settings.ui_port` (8790), outside the
loopback ports a turn's sandbox allows. It is started by hand when Tom
opens it ([machine.md](../machine.md): "When Tom opens it"); it has no
launchd job.

The page lists no guards and has no guard view (the governance paragraph:
no governance dashboard). The expiry sweep's runs appear among routine
runs like any other.

## Threat model

**What a turn controls:** its workspace and `.valor/` files, the text of
its questions, deliveries, and verdicts, and the commits on its branch,
including edits to `routines/`, plists, and `core/guards.py`, which reach
the kernel only through Tom's merge tap.

**What the kernel must never do.**
- Read a routine's toml, plist, or guard list from any checkout but its
  own.
- Let a run's ceiling exceed its toml's or its objective's.
- Let a background turn keep the slot from a foreground step.
- Release a sweep's merge, or any `act`, without Tom's tap.
- Count a firing from a row type outside `machine.VERDICT_ROWS` or from a
  background task.
- Put a secret in a plist.
- Let the page write, accept a method other than GET, or render ledger
  text unescaped. A delivery holding `<script>` is text on the page.

## Stakes

`critique_rounds: 1`, `review_rounds: 1`, as the rebuild plan sets for 4.3.
The sort key and the `preempted` outcome touch `core/serve.py`,
`core/slot.py`, `core/runs.py`, the check runners, the session runners, and the fold. The
schema gains one unique index. Both changes are additive, and the tests
below cover them.

**Governance.** This diff adds no check, gate, hook, round, or review
step. The expiry sweep deletes, and its merge is an `act` like every
merge. Preemption is a scheduling rule. The run lock is `run`'s lock; the
stopped-objective exit is Tom's stop holding. The emulator sweep measures
and blocks nothing. `need` is stored, never enforced. No firing hook is
added for instance grants.

## Absorbs

- [routines.md](../routines.md) "Gaps": the `routine` command, the routine
  identity on a task, the period report, and turns ordered with Tom first.
  Its plist row becomes `--plist`; its "two task ids" becomes `need`.
- `core/guards.py`'s docstring: "deleting an expired guard is a routine for
  milestone 4".
- [judgement-layer.md](../judgement-layer.md), Open: "What happens at expiry
  to a guard that did fire". `due` settles it (Decided by default).
- [machine.md](../machine.md), launchd: a firing starts a task through
  `python -m core start`. It is `python -m core routine NAME`.
- [tech-stack.md](../tech-stack.md): Scheduling and Dashboard move to in
  use, and the dashboard framework is aiohttp.
- The emulator's lock-file slots (`VALOR_DEMO_SLOTS`), if 1.5 keeps them.
  The kernel's slot orders replay turns, so the driver drops them.

## Leaves out

- **Tools and skills in the expiry sweep.** Nothing records a tool's or a
  skill's use beyond the performers, and every current tool and skill is
  used by every task. They are added when milestone 5's first tool lands.
- **A firing record for instance grants.** Adding one would be a new hook.
- **Merging the sweep's branch without Tom's tap** ([routines.md](../routines.md),
  Gaps: open; every merge is `act`).
- **Routines without a second need.** Verification runs, failure triage,
  workspace reclaim, and the cheap judgement sweeps over docs have none
  demonstrated.
- **Removing an instance grant from another project's repository.** The
  sweep's instruction lists it but does not remove it.
- **Anything that changes state from the page.** No buttons that approve,
  stop, or change state; no approval from a phone; no page beyond
  loopback; no database role of the page's own.

## Tests

`tests/test_routines.py`:

- The toml loads from `settings.routines_dir`. A toml of the same name in a
  task's workspace with a wider ceiling is never read.
- The first run registers the objective, and a second run reuses it. A
  ceiling change in the toml registers a second objective, and the report
  sums both.
- `start_child` with `marker={"objective": NAME}` under an objective
  writes a node `schedule` passes by.
- Two `routine.registered` rows for one (name, ceiling) are refused by the
  index.
- A stopped objective: the command writes no row, starts no task, prints
  the stopped line, and exits 0. `--restart` then registers a fresh
  objective with `replaces` set, under the same name and with no change to
  the toml, and its run starts; the report sums both objectives' spending.
  A second `--restart` racing the first registers one objective, not two
  (the index). Stopping the objective mid-run fences the
  run's turn and its replays (through 4.1).
- Two emulator processes at once: the second prints `already running`.
- Period spending, with `now` passed as a parameter of the fold:
  - a charge 31 days old is out and one 29 days old is in;
  - a grandchild's charge is in, and so is a judgement charge;
  - a stand-in charge on a replay's calibration task is in;
  - a `gateway.reserved` with no charge reads as open, and open calls are
    listed apart.
- With $1,000,000 of prior charges, the run starts and its turns run.
- The plist: `ProgramArguments` is exact, the schedule comes from the
  toml, `VALOR_DEMO` is carried, no environment name falls outside
  `routines.PLIST_ENV`, and no key directory content appears.
- An unfinished run is continued, and no second run starts.

`tests/test_expiry.py`, with rows written at explicit times:

- **Unfired seeded guard** (`expires` 2026-12-30): not due 12-29, due 12-31.
- **Fired before its expiry** on a foreground task: fired 2026-10-05, due
  2027-01-03 and not 01-02; fired 2026-11-01, not due 12-31, due 01-31.
- **Fired only on a background task** (a replay, or a hand-run replay with
  `Brief.replay` and no routine): due at expiry.
- **Wrong row type.** A `guard_id` in a row type outside
  `machine.VERDICT_ROWS` is not a firing.
- **Listed by a sweep.** An open sweep's item is not listed again. An item
  a merged sweep kept is due 90 days after that sweep listed it.
- A seeded guard absent from `guards.SEEDED`: not due.
- **Instance grants.**
  - Past its `expires`: due, and rendered with "no firing record".
  - On another project's task: in the "outside this repository" list only.
- **Routines.**
  - A routine registered 120 days ago whose runs were all `nothing_due`:
    due.
  - One with a child task delivered 30 days ago: not due.
  - The emulator sweep with replays delivering: not due.
  - The expiry routine: never listed.
- **Nothing due.** No task, no turn, `routine.ran` with `nothing_due`, $0.
- **Items due, with the scripted harness.**
  - One child task under the objective with `--project valor`, ceiling
    `act`, the rendered instruction, and the due list on its
    `task.started`.
  - The kernel carries it to `merge` with the merge held, and nothing is
    released without an approval.
  - A second firing gives `continued`.

`tests/test_slot_priority.py` (scripted harness, real Postgres, real
processes):

- **Preemption.** A background turn holds the slot and a foreground step is
  ready:
  - the background turn ends `preempted`, its process group is gone, and its
    calls are charged;
  - the foreground turn starts;
  - `tasks.audit` is empty for both.
- **Resuming.** The preempted step runs again after the foreground turn and
  resumes the same session. It spent no answer and did not count toward
  the idle bound or the failed-turn count.
- **Background.** A replay (a grandchild of a routine objective) is
  background, and so is a hand-run replay with `Brief.replay` and no
  routine.
- A hand-run replay's ready step sends no `valor_preempt`.
- `schedule` takes one ready foreground task before two background ones,
  and among foreground tasks the oldest latest row first.
- A preempted fresh session (critique) is rerun, and only that branch.
- A background check holding the slot is cancelled for a foreground turn,
  writes no verdict, and runs again; a reentrant `held` sends nothing.
- The emulator report counts a replay's preempted turns.

`tests/test_ui.py` (aiohttp test client):

- Each page renders against a ledger holding a task, a held effect, an
  answer, and a routine with runs.
- POST, PUT, PATCH, and DELETE on every route get 405, and the ledger's row
  count is unchanged.
- A delivery holding `<script>alert(1)</script>` renders escaped.
- An insert on the page's connection raises a read-only error.
- The page binds 127.0.0.1, and `settings.ui_port` is outside the turn
  sandbox's allowed loopback ports.
- The routine page's 30-day figure equals `python -m core routines`.

`tests/test_live_routine.py` (`VALOR_LIVE=1`): one real item in the `bare`
arm through the emulator runner, with its item spending and stand-in
spending in the routine's period figure.

## Files it changes

Other tasks also change `core/`. The files marked *kernel* merge one at a
time after 4.1 and 2.1.

| File | Change |
|---|---|
| `core/routines.py` | new: toml loading, registration, `due`, the expiry runner, the period fold over `tree_spending`'s `charges`, `report`, `PLIST_ENV`, the plist |
| `core/serve.py` (2.1's code, kernel) | `schedule`'s sort key: background last, then the latest row id |
| `core/slot.py` (2.1's code, kernel) | the holder's task; `valor_preempt` sent by a foreground holder before it blocks, heard by a background holder, which cancels its body |
| `core/runs.py` (kernel) | the cancel path writes `turn.ended` `preempted` |
| `core/checks.py` (kernel) | a cancelled check writes no verdict and runs again |
| `core/session.py`, `core/fresh.py` (kernel) | a `preempted` turn leaves the step unspent |
| `core/machine.py` (kernel) | `preempted` folds as unfinished, outside the idle and failed counts |
| `core/tasks.py` | `Brief.routine`, `Brief.replay`, `marker` on `tasks.start`, `background`, `index`, `attention_log` |
| `core/schema.sql` | unique index on `routine.registered` (name, ceiling, `replaces`) |
| `core/settings.py` | `routines_dir`, `routine_period_days` (30), `ui_port` (8790) |
| `core/__main__.py` | `routine` (with `--plist`, `--restart`), `routines`; `ROUTINE_RUNNERS` |
| `core/guards.py` | docstring |
| `routines/expiry/routine.toml`, `routines/emulator/routine.toml` | new |
| `routines/emulator/runner.py` | new |
| `routines/README.md`, `ui/README.md`, `core/README.md` | entry points |
| `ui/__main__.py`, `ui/app.py` | new |
| `tests/emulator/replay.py` | `--parent` (item task via `start_child` with `Brief.replay`), `--name`; `Brief.replay` on hand runs too; the lock-file slots dropped if 1.5 keeps them |
| `tests/test_routines.py`, `tests/test_expiry.py`, `tests/test_slot_priority.py`, `tests/test_ui.py`, `tests/test_live_routine.py` | new |
| `docs/routines.md`, `docs/machine.md`, `docs/tech-stack.md`, `docs/architecture.md`, `docs/judgement-layer.md`, `docs/emulator.md` | the docs stage |

## Rollout

On the Mac that runs the kernel, from the kernel checkout, after the merge
is pulled and the resident kernel restarted:

```bash
.venv/bin/python -m core backup
.venv/bin/python -m core migrate          # the routine.registered index
mkdir -p ~/Library/Logs/valor
for r in expiry emulator; do
  .venv/bin/python -m core routine $r --plist > ~/Library/LaunchAgents/com.valor.routine.$r.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.valor.routine.$r.plist
done
launchctl kickstart gui/$(id -u)/com.valor.routine.expiry   # one run now, by launchd
tail ~/Library/Logs/valor/routine-expiry.log
.venv/bin/python -m core routines
```

The emulator sweep first runs on its schedule. `launchctl bootout
gui/$(id -u)/com.valor.routine.NAME` removes a job. `python -m core stop`
on the objective stops a routine; `--restart` starts it again. The page
is `.venv/bin/python -m ui`, at `http://127.0.0.1:8790/`.

## Decided by default

Each is reversible; Tom can overturn any.

- **A guard that fired before its expiry** is listed 90 days after its
  last firing; an unfired one at expiry (the governance paragraph).
- **Use of a routine** is as [routines.md](../routines.md) defines it: an
  effect performed, a question Tom answered, or a child task that
  delivered. The emulator sweep delivers, so it is not listed.
- **Instance grants show "no firing record"**; no hook records firing.
- **The plist is printed, not committed.** It names the checkout and its
  interpreter, which differ per Mac; `core backup --plist` already works
  this way. The schedule is in the toml, in git.
- **One live objective per (name, ceiling)**, with runs as its children, so
  4.1's rollup gives the period figure and one stop ends the routine.
- **The period is the rolling 30 days ending at the report**, by the
  charge row's time, and includes the emulator's calibration tasks for the
  routine's replays.
- **A foreground step runs beside a background one.** `schedule` starts a
  ready foreground step even while a background step is running, and a
  background step only when no foreground step is ready. This changes how
  many steps the kernel runs at once, from one to two at most: the
  background one is only waiting for or holding the slot, and the slot
  still lets one `claude -p` run at a time. The reason is Done item 4: a
  kernel busy with a background step cannot otherwise start Tom's step to
  announce itself and preempt it. The source is the slot section above and
  [machine.md](../machine.md) (Concurrency, Tom's work first). It adds no
  bound beyond the plan's.
- **A background turn is preempted** when a foreground step is ready,
  since waiting for it would break the Done. Its spend is metered and lost,
  and counted in the sweep's report.
- **Replays are background**, whether a routine or a person started them.
  The forced `clarify` arm would otherwise keep `intake.underspecified`
  alive, and a hand-run gate replay would preempt routine turns.
- **The expiry sweep covers guards and routines**, whose use is recorded.
- **One open sweep at a time**, continued by later firings.
- **The expiry routine is not listed by itself** (Mission item 5).
- **Both routines carry ceiling `act`.** The sweep's merge and the replays'
  local pushes are `act`, and each waits for a tap. The replays' taps come
  under the standing permission emulator.md records, which this task does
  not change.
- **Schedules.** Expiry runs daily at 04:00 and costs nothing when nothing
  is due. Emulator runs weekly, Sunday 01:00, with all items in three
  arms, one run each. Spending is reported, never capped.
- **The page** is aiohttp, loopback only, port 8790, read-only, by hand.
- **A stopped routine stays stopped** across scheduled firings until Tom
  runs `python -m core routine NAME --restart`, which needs no diff.
- **No limit beyond the cited ones.** Only the emulator driver holds a
  run lock, as `run` does; the kernel, not the command, drives the expiry
  task. No routine sets a timeout, a run count, or a spending figure. The
  numbers are the 30-day period (the rebuild plan), the 90 days (the
  governance paragraph, Mission item 5), the schedules, and the port.

## Decided by default at build

Where the code at e70a91d92 differs from what the plan assumed, the build
follows the code. Each is reversible.

- **A preempted step returns `{"status": "moved", "preempted": True}`.** The
  kernel pops the task from `seen` so it runs again; `router.run` loops on
  "moved" and the kernel uses `router.step`.
- **`schedule` yields `(background, latest, task_id)`** and the kernel starts
  a foreground step beside a background one, and a background step only when
  no foreground step is ready.
- **A preempted turn** writes `turn.ended` with outcome `preempted` and result
  `{}`. `machine.fold` already counts a turn only when it is done, and
  `LAST_WORKING_ENDED` excludes preempted, so no fold changed.
- **`--name` is an alias of `--run` in the replay driver**, and `--parent`
  starts a replay under a routine's run. The driver's lock-file slot is
  gone; `machine_lock` stays in `tests/emulator/common.py` for the judge.
- **The emulator's open-run rule.** A run with no report is continued; the
  session lock `run:<run>` makes a second process say `already running`.
- **The expiry toml names the `valor` project and the
  rebuild branch**, the project and branch this checkout builds.
- **The status page answers 404 for an unknown task**, by reading the Brief
  before the status.
- **`core/__main__.py` has `_routine_runners()`** where the plan named a
  `ROUTINE_RUNNERS` table.
- **`tests/scripted.provisioned` takes `brief_kw`** to put `routine` or
  `replay` in a test task's Brief.
- **The emulator sweep runs Sunday 01:00**, as planned.

## Build record

Built on 4.1 (e70a91d92) over the plan commit ca0cfb898. New tests:
`test_routines.py`, `test_slot_priority.py`, `test_expiry.py`,
`test_ui.py`, and `test_live_routine.py` (gated on `VALOR_LIVE=1`, not run).
Docs rewritten to the built behavior: `routines.md`, `machine.md`,
`emulator.md`, `tech-stack.md`, and the `routines`, `ui` and `core` READMEs.
Suite and lint results are in the builder's report.

## Questions for Tom

None; both open points are decided by default above.
