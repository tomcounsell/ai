---
tracking: none
slug: m4-3-routines
type: plan
status: planned
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

**4.1, the objective tree** (planned in parallel in
`m4-1-objective-tree.md`). This plan assumes 4.1 gives:

- a `parent_id` on the Brief, set at start (`tasks.start`, and
  `python -m core start --parent TASK`), with a child refused when its
  ceiling is above its parent's;
- a fold over a task's subtree (its descendants' ids), and spending rolled
  up from descendants into the parent's reported spending;
- stop of a node fencing every descendant;
- a node that runs no turn of its own and holds children (an objective
  node). If 4.1 has no such node, this task adds one the way a calibration
  task is marked: a `task.started` carrying `objective: NAME` and no
  `sdlc`, which the router and every SDLC writer refuse, and which folds
  read-only.

**2.1, the resident kernel.** This plan assumes 2.1's one turn slot held
in Postgres: a turn takes it before its harness starts and gives it back
when the turn ends, and a process outside the resident kernel (a
`python -m core run`, a `python -m core routine`) takes the same slot. If
2.1 makes the resident kernel the only turn runner, `python -m core
routine` commits the run and wakes the resident kernel with the same event
2.1 uses for a new task; the slot's order below is the same either way.

**1.5, the emulator.** This plan assumes the replay driver and judge live
in `tests/emulator/`, meter every stand-in and judge call through the
gateway onto the run's task, and take the `routed` arm. If the driver does
not pass a parent to `python -m core start`, this task adds `--parent`.

## Done, as evidence

Each item is a test on real Postgres in the default suite unless it says
otherwise.

1. **The command.** `python -m core routine NAME` reads
   `routines/NAME/routine.toml` from the kernel's own checkout
   (`settings.routines_dir`), registers the routine's standing objective on
   its first run, starts the run as a child of it with the toml's ceiling,
   runs it through the runner the toml names, and prints one line: the
   run's outcome, its metered spending, and the routine's metered spending
   over the last 30 days with the number of runs in them.
2. **Period spending is reported and stops nothing.** The 30-day figure is
   the sum of every `gateway.charged` row (judgement calls included) on
   every task under the routine's objectives whose row time is within 30
   days of the report, with open calls listed apart. A charge 31 days old
   is out; one 29 days old is in; a charge on a grandchild (a replay under
   a sweep run) is in. A routine whose last 30 days hold $1,000,000 of
   charges starts and runs exactly as one at $0.
3. **launchd calls only that.** `python -m core routine NAME --plist`
   prints the job: `ProgramArguments` is exactly the kernel checkout's
   interpreter, `-m`, `core`, `routine`, `NAME`; the schedule comes from the
   toml; the environment carries only `HOME`, a system `PATH`, and the
   kernel's own `VALOR_*` connection settings set when it was printed,
   never a secret.
4. **A routine never makes Tom's task wait for the slot.** When a turn of
   a task outside every routine is ready while a routine's turn holds the
   slot, the routine's turn is preempted: its gateway grant revoked, its
   process group killed, its calls charged, its processes reaped, and
   `turn.ended` written with outcome `preempted`, then Tom's turn takes the
   slot. A routine's turn waiting for the slot is ordered after every
   waiting turn of Tom's. The preempted run waits, then resumes in the same
   session; `tasks.audit` is empty throughout.
5. **The emulator sweep.** `routines/emulator/routine.toml` runs every item
   in `$VALOR_DEMO/items` in the `bare`, `clarify`, and `routed` arms, each
   replay a child of the run, judged, and records the run's report: per
   item and arm, fidelity, correctness, simplicity, hidden tests, attention,
   and metered spending, beside the item's bare baseline run. It requests
   no effect of its own and writes no verdict on any task; a failed item is
   recorded and the next item runs. Shown with a scripted driver in the
   default suite, and with one real item in a `VALOR_LIVE` test.
6. **The expiry sweep.** `routines/expiry/routine.toml` folds the guards
   and routines that are due (below). With nothing due it starts no task
   and runs no turn, and records the run at $0. With items due it starts
   one task on the `valor` project whose instruction lists each item with
   its incident, mission item, grant date, expiry, and last firing, and the
   router carries it through the pipeline to one merge held for Tom's tap.
   A later firing while that task is open continues it and starts no other.
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

- **A guard** (any `guard.granted` row) is due when `now` is past its
  `expires` date, it has not fired in the 90 days before `now`, and no sweep
  task listed it in those 90 days (an open sweep, or a merged one that kept
  it). It fires when a verdict row (`judge.decided`, `critique.decided`,
  `test.decided`, `review.decided`, `docs.decided`, the types in
  `machine.VERDICT_ROWS`) names its `guard_id`, on a task outside every
  routine. A firing on a replay does not count: a forced `clarify` arm
  fires the request judge by construction. A seeded guard absent from
  `guards.SEEDED` in the kernel's checkout is gone and never due.
- **A routine** is due when its first registration is more than 90 days
  old, nothing under it was used in the last 90 days, and no sweep listed
  it in them. Used means Tom acted on a task under it (an answer, feedback,
  or approval with `role_played` false) or a merge under it was released.
  A replay's delivery, its stand-in's role-played answers, and its local
  pushes are the routine's own work, not use.
- **The expiry routine never lists itself.**
- An instance grant on a task whose project is not `valor` is listed in the
  instruction under "outside this repository: listed, not removed".

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

- `routine.registered` on the `routines` stream: the name, the ceiling,
  the toml's digest, `need`, `mission_item`, and the objective task id. A
  unique index on (name, ceiling) makes it one objective per name and
  ceiling. A toml whose ceiling changes by a reviewed diff registers a
  second objective; the period report sums every objective of the name.
- Each run is a child of the objective: an SDLC task (expiry) or an
  objective node holding the replays (emulator). The run's `Brief` carries
  `routine: NAME`.
- `routine.ran` on the objective at a run's end: the run's task id, the
  outcome (`delivered`, `waiting`, `merged`, `nothing_due`, `failed`,
  `stopped`, `continued`), a short summary the kernel writes, and the run's
  spending. A run whose process died has no `routine.ran`; the page shows it
  from its task's state, and the next firing continues it.
- A task is routine-owned when it or an ancestor carries `routine` in its
  Brief (4.1's ancestor fold).

### The command

`python -m core routine NAME`:

1. Load the toml from `settings.routines_dir`. An unknown name is an error
   naming the directory.
2. Take a session advisory lock `routine:NAME`; a second process prints
   `already running` and exits 0, as `run` does.
3. Register the objective if the ledger lacks one for (name, ceiling).
   If the objective is stopped, print `routine NAME is stopped (task ID)`,
   write nothing, and exit 0: Tom's stop holds across firings.
4. If the routine's last run is unfinished (expiry: its task is neither
   merged nor stopped; emulator: no `routine.ran`), continue it.
   Otherwise start a run through the runner.
5. Write `routine.ran` and print the line in Done 1.

`python -m core routine NAME --plist` prints the job (Done 3), labelled
`com.valor.routine.NAME`, logging to `settings.log_dir/routine-NAME.log`.
`python -m core routines` prints every routine with its last run, last
result, and 30-day spending, the same fold the page reads.

Runners are registered in the composition root (`core/__main__.py`,
`ROUTINE_RUNNERS`), as stage runners are: `expiry` from `core/routines.py`,
`emulator` from `routines/emulator/runner.py`.

### The expiry runner

Fold `due`. Nothing due: `routine.ran` with `nothing_due`. Otherwise start
one task as `python -m core start --project valor --branch <the rebuild
branch>` does, with `--parent` the objective, ceiling `act`, the toml's
model, the instruction rendered by the kernel from the due list, and the
Brief's `routine` set; then `router.run` it with the standing runners until
it needs Tom. The working session plans, builds, and patches the deletion
like any task: the guard's code, its seed in `core/guards.py`, its tests,
and the docs that describe it, or the routine's directory. Critique,
test, review, and docs run on it. The merge waits for Tom's tap. Keeping
an item is Tom's feedback on the delivery, which sends it to patch; the
kept item is not listed again for 90 days.

The ledger keeps every `guard.granted` row; a deleted seeded guard leaves
`guards.SEEDED`, so `migrate` does not seed it again and `due` reads it as
gone.

### The emulator runner

Starts an objective node as the run, under the routine's objective. For
each item and arm, it runs the 1.5 driver as a subprocess
(`tests/emulator/replay.py ITEM --arm ARM --parent RUN --judge`), the same
code that ran by hand, so each replay is a kernel task under the run and
its turns take the slot as routine turns. It reads each result file,
writes the report to `$VALOR_DEMO/sweeps/<run>.json`, and records its
summary on `routine.ran`. The driver's held local pushes are released
under the standing permission for pushes to a run's own bare origin that
[emulator.md](../emulator.md) records, as role-played approvals; no other
effect is released.

### The slot's order

In the slot module from 2.1 and `core/runs.py`:

- A waiter carries whether its task is routine-owned. The next holder is
  the oldest waiter outside every routine, else the oldest routine waiter.
- A turn outside every routine that finds a routine turn holding the slot
  sends a Postgres notification on `valor_preempt` naming that turn.
  `run_turn` listens on it beside `valor_stop` and, hearing its own turn,
  runs the stop path without `task.stopped`: revoke, kill the group, drain,
  reap, `turn.ended` with outcome `preempted`.
- The session runner and the fresh-session runners treat `preempted` as a
  turn that did not finish: the answer or findings are not spent, it does
  not count toward the idle bound or the failed-turn count, and the runner
  waits for the slot and runs the turn again, resuming the session.
- A routine waiter waits as long as Tom's turns keep arriving. Nothing
  bounds the wait.

### The status page

`ui/` gains `ui/__main__.py` and `ui/app.py`: an aiohttp application
(aiohttp is already a kernel dependency) rendering HTML on the server, no
JavaScript, no forms. It imports only `core/` read models:
`tasks.index` (new: every task with its state, spending, attention counts,
and last row time), `tasks.status`, `ledger.read`, `broker.pending`, `tasks.attention_log` (new: every task's attention
entries in time order), and `routines.report`. It connects as the kernel
role with `default_transaction_read_only` set on its session. It binds
127.0.0.1 on `settings.ui_port` (8790), outside the loopback ports a turn's
sandbox allows. It is started by hand when Tom opens it
([machine.md](../machine.md): "When Tom opens it"); no launchd job.

The page lists no guards and has no guard view (the governance paragraph:
no governance dashboard). The expiry sweep's runs appear among routine
runs like any other.

## Threat model

A turn controls its workspace, its `.valor/` files, the text of its
questions, deliveries, and verdicts, and the commits on its branch,
including edits to `routines/`, plists, and `core/guards.py` that reach
the kernel only through Tom's merge tap. The kernel must never: read a
routine's toml, plist, or guard list from any checkout but its own; let a
run's ceiling exceed its toml's or its objective's; let a routine-owned
turn keep the slot from a turn of Tom's; release a sweep's merge or any
`act` without Tom's tap; count a firing from a row type a turn can shape
or from a routine-owned task; put a secret in a plist; or let the page
write, accept a non-GET, or render ledger text unescaped (a delivery
holding `<script>` is text on the page).

## Stakes

`critique_rounds: 1`, `review_rounds: 1`, as the rebuild plan sets for 4.3.
The slot's order and the preempted outcome touch `core/runs.py`, the
session runner, and the fold; the schema gains one unique index. Both are
additive, and the tests below cover them.

**Governance.** This diff adds no check, gate, hook, round, or review step.
The expiry sweep deletes; its branch is an ordinary task whose merge is an
`act` like every merge. Preemption orders turns on the 16 GB machine
([machine.md](../machine.md), Concurrency: "a scheduling rule, not a check
on the agent"). The run lock and the stopped-objective exit are the same
as `run`'s lock and stop. The emulator sweep measures and blocks nothing.
`need` is stored, never enforced.

## Absorbs

- [routines.md](../routines.md) "Gaps": the `routine` command, the routine
  identity on a task, the period report, and turns serialized across tasks
  with Tom first. Its plist row becomes the generated plist (`--plist`), and
  its "two task ids" becomes the `need` field.
- `core/guards.py`'s docstring: "deleting an expired guard is a routine for
  milestone 4".
- [judgement-layer.md](../judgement-layer.md), Open: "What happens at expiry
  to a guard that did fire", settled by `due` (question 1 below).
- [machine.md](../machine.md) launchd section saying a firing starts a task
  through `python -m core start`; it is `python -m core routine NAME`.
- [tech-stack.md](../tech-stack.md): Scheduling and Dashboard to in use,
  the dashboard framework aiohttp.
- The emulator's lock-file slots (`VALOR_DEMO_SLOTS`), if 1.5 keeps them:
  the kernel's slot serializes replay turns, so the driver drops them.

## Leaves out

- Tools and skills in the expiry sweep. No record of a tool's or a skill's
  use exists beyond the performers, and every current tool and skill is
  used by every task. Added when milestone 5's first tool lands.
- A merge of the sweep's branch without Tom's tap ([routines.md](../routines.md),
  Gaps: open, and every merge is `act`).
- Verification runs, failure triage, workspace reclaim, and the cheap
  judgement sweeps over docs: none has a demonstrated second need.
- Removing an instance grant from another project's repository: listed in
  the sweep's instruction, not removed.
- Restarting a stopped routine. A reviewed diff that renames it registers
  a new one.
- Buttons that approve, stop, or change state; approval from a phone; any
  page beyond loopback; a database role of the page's own.
- A power assertion (`caffeinate -i`) per turn ([machine.md](../machine.md),
  Sleep).
- A bound on how long a routine waits for the slot.

## Tests

`tests/test_routines.py`:

- The toml loads from `settings.routines_dir`; a toml of the same name in a
  task's workspace with a wider ceiling is never read.
- First run registers the objective; a second run reuses it; a ceiling
  change in the toml registers a second objective and the report sums both.
- Two `routine.registered` rows for one (name, ceiling) are refused by the
  index.
- A stopped objective: the command writes no row, starts no task, prints
  the stopped line, exits 0. Stopping the objective mid-run fences the run's
  turn and its replays (through 4.1).
- Two processes at once: the second prints `already running`.
- Period spending: 31 days out, 29 days in, a grandchild's charge in, a
  judgement charge in, a `gateway.reserved` open read as open, open calls
  listed apart; `now` is a parameter of the fold.
- $1,000,000 of prior charges: the run starts and its turns run.
- The plist: exact `ProgramArguments`, schedule from the toml, no
  environment name outside the allowed list, no key directory content.
- An unfinished run is continued and no second run starts.

`tests/test_expiry.py`, rows written with explicit times:

- Granted 2026-10-01, never fired, `now` 2026-12-31: due.
- Fired on Tom's task 2026-11-01: not due on 2026-12-31; due on 2027-01-31.
- Fired only on a replay (routine-owned): due.
- A `guard_id` in a row type outside `machine.VERDICT_ROWS`: not a firing.
- Listed by an open sweep: not listed again. Kept by a merged sweep: due
  90 days after that sweep listed it.
- A seeded guard absent from `guards.SEEDED`: not due.
- An instance grant on another project's task: in the "outside this
  repository" list only.
- The expiry routine with no use for 120 days: not listed.
- A routine whose only use is role-played answers, replay deliveries, and
  local pushes: due. One with Tom's non-role-played approval 30 days ago:
  not due.
- Nothing due: no task, no turn, `routine.ran` `nothing_due`, $0.
- Items due, with the scripted harness: one task under the objective with
  `--project valor`, ceiling `act`, the rendered instruction; the router
  carries it to `merge` with the merge held; no release without an
  approval; a second firing continues it.

`tests/test_slot_priority.py` (scripted harness, real Postgres, real
processes):

- A routine turn holding the slot and a ready turn of Tom's: the routine
  turn ends `preempted` with its process group gone and its calls charged,
  Tom's turn starts, `tasks.audit` is empty for both.
- The preempted routine run resumes the same session after Tom's turn and
  finishes; the preempted turn did not spend an answer and did not count
  toward the idle bound or the failed-turn count.
- A replay (grandchild of a routine objective) is routine-owned.
- Waiters: two routine waiters and one of Tom's, Tom's first; among Tom's,
  oldest first.
- A preempted fresh session (critique) is rerun, and only that branch.

`tests/test_ui.py` (aiohttp test client):

- Each page renders against a ledger holding a task, a held effect, an
  answer, a routine with runs.
- POST, PUT, PATCH, DELETE on every route: 405, and the ledger's row count
  is unchanged.
- A delivery holding `<script>alert(1)</script>` renders escaped.
- An insert on the page's connection raises a read-only error.
- The page binds 127.0.0.1, and `settings.ui_port` is outside the turn
  sandbox's allowed loopback ports.
- The routine page's 30-day figure equals `python -m core routines`.

`tests/test_live_routine.py` (`VALOR_LIVE=1`): one real item in the `bare`
arm through the emulator runner, its spending rolled up to the run and the
objective.

## Files it changes

Other tasks also change `core/`; the ones marked kernel merge one at a
time after 4.1.

| File | Change |
|---|---|
| `core/routines.py` | new: toml loading, registration, `due`, the expiry runner, the period fold, `report`, the plist |
| `core/runs.py` (kernel) | `valor_preempt` listen; the `preempted` outcome |
| 2.1's slot module (kernel) | waiter order, the preempt notification |
| `core/session.py`, `core/fresh.py` (kernel) | wait and rerun after `preempted` |
| `core/machine.py` (kernel) | `preempted` folds as unfinished, outside the idle and failed counts |
| `core/tasks.py` | `index`, `attention_log`, the routine-owned predicate |
| `core/schema.sql` | unique index on `routine.registered` (name, ceiling) |
| `core/settings.py` | `routines_dir`, `routine_period_days` (30), `ui_port` (8790) |
| `core/__main__.py` | `routine`, `routines`; `ROUTINE_RUNNERS` |
| `core/guards.py` | docstring |
| `routines/expiry/routine.toml`, `routines/emulator/routine.toml` | new |
| `routines/emulator/runner.py` | new |
| `routines/README.md`, `ui/README.md`, `core/README.md` | entry points |
| `ui/__main__.py`, `ui/app.py` | new |
| `tests/emulator/replay.py` | `--parent`, and the lock-file slots dropped, if 1.5 lacks either |
| `tests/test_routines.py`, `tests/test_expiry.py`, `tests/test_slot_priority.py`, `tests/test_ui.py`, `tests/test_live_routine.py` | new |
| `docs/routines.md`, `docs/machine.md`, `docs/tech-stack.md`, `docs/architecture.md`, `docs/judgement-layer.md`, `docs/emulator.md` | the docs stage |

## Rollout

On the Mac that runs the kernel, from the kernel checkout, after the merge
is pulled:

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

The emulator sweep's first run comes on its schedule. `launchctl bootout
gui/$(id -u)/com.valor.routine.NAME` removes a job; `python -m core stop`
on the routine's objective stops the routine. The page is
`.venv/bin/python -m ui`, then `http://127.0.0.1:8790/`.

## Decided by default

Each is reversible; Tom can overturn any.

- **The plist is printed, not committed.** It names the checkout and its
  interpreter, which differ per Mac; `core backup --plist` already works so.
  The toml holds the schedule, in git.
- **One objective per (name, ceiling)**, runs as its children, so 4.1's
  rollup gives the period figure and one stop ends the routine.
- **The period is the rolling 30 days ending at the report**, by the
  charge row's time.
- **A routine turn is preempted** when Tom's turn is ready. The Done item
  says Tom's task never waits for the slot; waiting for the routine's turn
  to end would break it. The preempted turn's spend is metered and lost.
- **Firings on routine-owned tasks do not count.** The emulator forces the
  judge's answer in two arms, so a weekly sweep would keep every guard
  alive.
- **The expiry sweep covers guards and routines**, the two whose use the
  ledger records.
- **One open sweep at a time**; later firings continue it, so Tom sees one
  deletion branch.
- **The expiry routine is not listed by itself**: the constraint and
  Mission item 5 require it.
- **Both routines carry ceiling `act`**: the sweep's merge and the replays'
  local pushes are `act`, and each waits for a tap (the replays' under the
  standing permission emulator.md records, which this task does not change).
- **Schedules:** expiry daily at 04:00, which costs nothing when nothing is
  due; emulator weekly, Sunday 01:00, all items in three arms, one run
  each. A bare run averages about $1.42 metered; the sweep's total is
  reported, never capped.
- **The page:** aiohttp, server-rendered, loopback only, port 8790, a
  read-only session as the kernel role, started by hand.
- **`need` is stored and shown, never enforced.**
- **A stopped routine stays stopped** across firings.

## Questions for Tom

Intent only; each with the answer this plan assumes.

1. **A guard that fired: when does it expire?** Assumed: it is due 90 days
   after its last firing on one of Tom's tasks, and never while it keeps
   firing, so "has not fired by expiry" is read as "has not fired in the
   last 90 days". A kept guard is not listed again for 90 days.
2. **What counts as using a routine?** Assumed: Tom acting on a task under
   it (an answer, feedback, or approval of his own, not role-played) or a
   merge under it released. A replay's own delivery and local push are not
   use, so the emulator sweep is listed for deletion 90 days after it is
   first registered unless Tom acts on one of its runs or keeps it by
   feedback on the sweep's delivery.
