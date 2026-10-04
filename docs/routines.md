# Routines

A routine is work Valor does on a schedule instead of on request. Each run is
a task like any other: a Brief with an effect ceiling and a
governance grant (default none), committed by the kernel before the first
turn, metered by the gateway, recorded in the ledger, and stoppable at any
instant. launchd supplies the time. The kernel supplies everything else. A
routine is never a bare script, because a script outside the kernel has no
metering, no ceiling, and no ledger, and so sits outside the constraint
"Bounded authority, metered spending" (`docs/mission.md`).

The code lives in `routines/`; its README states the scope and the import
rules.

## What a routine is

A routine is a directory under `routines/<name>/` in the kernel's own
checkout holding `routine.toml`, changed only by a reviewed diff. The kernel
reads it from `settings.routines_dir` and never from a task's workspace.

| Key | Holds | Serves |
|---|---|---|
| `name`, `runner` | the directory name, and which of the kernel's runners does the work (`expiry`, `emulator`) | Mac native (launchd for scheduling) |
| `ceiling`, `model` | the effect class every run's tree is capped at, and the model seat | Bounded authority, metered spending |
| `mission_item`, `need` | the mission item it serves and the demonstrated need, shown on the status page and never enforced | Mission item 5 |
| `created`, `instruction` | the date, and the text a run is given | Mission item 5 |
| `project`, `branch` | the project spec and branch a runner that starts a workspace task provisions | |
| `[schedule]` | `minute`, `hour`, `day`, `weekday`, `month`, or `interval` in seconds | launchd |

The launchd job is printed and never committed, since it names the checkout
and its interpreter, which differ per Mac:
`python -m core routine NAME --plist` prints a plist whose one program is
`python -m core routine NAME` run by the kernel's own interpreter, with the
schedule from the toml and the Postgres and replay settings that were set
when it was printed (`VALOR_PG*`, `VALOR_DB*`, `VALOR_DEMO`). It names no
script and carries no secret. launchd starts a job missed during sleep once
on wake and coalesces several missed intervals into one run, so a laptop
that sleeps through a schedule gets one late run, never a burst.

A run that needs a credential reaches it through a tool that reads Keychain,
inside a performer the broker calls (Mac native: Keychain for secrets).

## How a run starts

launchd runs `python -m core routine NAME`. The command loads the toml and
finds the routine's standing objective: a root task with `Brief.routine` set
and the marker `{"objective": NAME}`, registered by a `routine.registered`
row on the `routines` stream the first time it runs. A unique index keeps
one live objective per name and ceiling. A change of the toml's ceiling
registers a second objective, and the period report sums both.

The objective is a node: it folds as a task without the state machine, so
the kernel passes it by, and stop and fencing still work on it. Each run is
a child of the objective, so the tree's rollup is the routine's spending.
The runner named in the toml starts the run or continues the open one, and a
`routine.ran` row on the objective records the firing with its outcome
(`started`, `continued`, `nothing_due`, `finished`, `failed`, `running`).
The command prints one line: the run, its outcome, its metered spending, and
the routine's spending over the period.

Tasks a runner starts through `start --project` are children of the
objective with `Brief.routine` set. The resident kernel drives them like any
task, as background work (below). A task of the run ends the way any task
ends: a delivery, a question, or a stop.

Tom's stop holds. `python -m core stop <objective>` fences the whole tree;
the next launchd firing writes nothing and says the routine is stopped.
`python -m core routine NAME --restart` registers a fresh objective whose
`replaces` names the stopped one; on a routine that is not stopped it
changes nothing. `python -m core routines` lists every routine with its last
run and its period spending.

The two routines the kernel holds:

- **expiry** (04:00 daily, ceiling `act`): `routines.due` folds the ledger
  and, when something is due, starts one task on the `valor` project with
  the due list on its `task.started`. Nothing due starts no task and runs no
  turn.
- **emulator** (Sunday 01:00, ceiling `act`): replays every item in
  `$VALOR_DEMO/items` in the `bare`, `clarify` and `routed` arms, each a
  child of the run, judged, and writes `$VALOR_DEMO/sweeps/<run>.json` with
  the baseline, scores, hidden-test exits, attention, spending and the count
  of preempted turns. The runner holds the session lock `run:<run>`, so a
  second process says `already running`. A run with no report is continued.

## Metered spending and ceiling

**Per-run spending.** Every model call a run makes is metered by the
gateway and recorded on the run's task, and money never stops the run. A
routine that makes no model call still runs as a task with metered spending
of zero, so its effects still pass through the broker and land in the
ledger. Constraint: Bounded authority, metered spending.

**Period spending.** The routine's spending over its rolling thirty-day
period (`settings.routine_period_days`) is the metered charges of every
objective of the name and their subtrees, plus the calibration tasks that
meter the emulator's stand-in and judge for items in those subtrees.
Charges are counted by their own `at`. Open `gateway.reserved` calls are
listed apart and never summed. `python -m core routines` and the status page
print the same figure. Nothing stops a routine on money: a million dollars
of earlier charges changes nothing about whether the next run starts.
Constraint: Bounded authority, metered spending.

**Ceiling.** The ceiling is set in `routine.toml`, copied into the
objective's Brief, and never widens at run time; a child's ceiling is at or
below its parent's. Least privilege [11] sets the default: a routine gets the
lowest class its job needs.

**Attention.** A routine spends Tom's attention only through a question or a
held `act`, and both are ledgered against the run (Mission item 6). A
routine's attention is counted on the same footing as its money when the
ninety-day review below asks whether it earned its place.

**One turn at a time, Tom first.** On a 16 GB machine one `claude -p` runs at
a time (`docs/machine.md`). Work under a task or ancestor with `Brief.routine`
or `Brief.replay` is background; everything else is foreground. A foreground
holder of the turn slot takes the shared lock `turn-slot-fg:<machine>` and
sends `valor_preempt` before it waits. A background holder listens, and when
it hears the notice or finds the lock held, its turn or check ends: the
gateway grant is revoked, the process group is killed, the turn ends with
outcome `preempted`, and no verdict is written. The task is ready again at
once and runs after Tom's work. The kernel starts a foreground step beside a
background one for this reason, and starts a background step only when no
foreground step is ready. A replay run by hand is background too but sends no
notice.

## What a routine may do

| Class | Examples | Who authorizes |
|---|---|---|
| `read` | run a test suite against real Postgres and report; read error reports from a service; list workspaces whose branches have merged | the routine's committed Brief |
| `propose` | open a branch with a fix for a failure it found; draft a summary; start a child task, inside its own ceiling, to investigate a failure; remove a workspace whose commits are already on the origin | the Brief; the performer declares the class |
| `act` | merge, send, deploy, pay, delete anything not recoverable elsewhere | Tom, one approval per action, through the same approval surface as any task |

A schedule is not a standing approval. An `act` requested by a routine is
held in `python -m core pending` until Tom approves and releases it, exactly
as the demonstration's pushes were held, approved, and released
(rebuild-demonstration.md, "Where Tom acted as project manager").

The kinds of scheduled work this design carries:

- **Verification runs.** The test suite against real services on a schedule,
  `read`. A failure starts a child task to investigate and propose a fix.
  Mission item 1 ("resolving discovered defects") and the Evidence item
  "working results in real use".
- **Failure triage.** Reading error reports from deployed work, grouping
  them, and starting a task per new failure. Mission item 6 (Valor
  "investigates failures").
- **Workspace reclaim.** Removing task workspaces whose branches have merged
  and whose commits exist on the origin, which keeps the machine's disk usable.
  `propose`, since nothing removed is unrecoverable.
- **The expiry sweep.** Described below. Mission item 5 and the governance
  constraint.

## What a routine may not do

- Widen its ceiling, or start a child with a higher one than it holds.
  The kernel refuses; a routine has no path to ask for more
  except a question to Tom.
- Send Tom a status message. Status is read on the dashboard. A message to
  Tom is a question with a decision in it, ledgered as attention, or it is
  not sent (Mission item 6: requiring Tom to adjudicate internal process is a
  product defect).
- Kill or restart other processes on a heuristic. Stop and recovery live in
  `core/`, not in a scheduled sweep.
- Act as a check on Valor's own process without a grant (next section).

## Routines that are checks

A routine whose job is to look for something wrong and raise it is a guard.
It falls under the governance constraint, in full: it names the incident that
already happened without it and the mission item it serves, it is added
only under a `governance_grant` with Tom's tap, it is ledgered with a
ninety-day expiry, and it is deleted by default if it has not fired by
expiry. The blind verifier's boolean ("does this add a check, gate, hook,
round, or review step") reads a diff that adds such a routine the same way
it reads any other diff.

A verification run that tests Valor's own deliverables is a check under this
rule. Workspace reclaim, which removes things, is not; it is work.

## How results surface

- **The ledger.** Every run is a task, so its start, its turns, its spend,
  its effects, and its end are ledger rows like any task's. A run's result
  is its delivery, read with `python -m core status <task>`.
- **The status page.** `python -m ui` serves `http://127.0.0.1:8790` (set by
  `VALOR_UI_PORT`): every task, one task with its ledger, the effects held
  for Tom, the attention log, and each routine with its last run, its last
  result and its period spending. It is read-only and answers GET only; the
  address is the loopback one, on a port no sandbox profile opens.
- **Attention.** A question from a routine reaches Tom through the same
  path as any task's question and is ledgered with its answer and
  provenance. A held `act` appears in the pending approvals.

Nothing a routine finds is pushed to Tom unless it needs a decision.

## When a routine exists and when it is deleted

**Existence.** A routine is added on a demonstrated second need: the same
work done by hand, as a task, twice. `routine.toml` names both task ids.
A first occurrence is a task, not a routine (Mission item 5).

**Use.** A routine is in use when a run in the last ninety days led to
something outside itself: an effect performed, a question Tom answered, or a
child task that delivered. A routine that has run for ninety days and
produced only "nothing found" is unused. The emulator sweep's replays
deliver, so it is in use while it runs.

**Deletion.** An unused routine is deleted by default at ninety days. The
expiry sweep is itself a routine, and it never lists itself. `routines.due`
reads kernel-written rows only and proposes:

- a seeded guard unfired at its expiry, or fired only on background work; a
  guard that fired on Tom's work, ninety days after its last firing and past
  its expiry;
- an instance grant past its expiry, shown with "no firing record" since no
  hook records one; one on another project's task is listed apart as
  outside this repository and not removed;
- a routine whose first registration is over ninety days old and whose runs
  in that window led to no use.

An item a sweep listed is not listed again for ninety days. The sweep's one
task has the ceiling `act` and opens one branch removing everything due.
Opening the branch is `propose`. Merging it is `act`, and waits for Tom's tap
like any merge. Keeping something past expiry takes a reason in the branch's
review, given by Tom; the default is the deletion (Mission item 5; the
governance constraint).

## Gaps

- Whether the expiry sweep's deletion branch should merge without a tap is
  open. The constraint makes every merge `act`, and the kernel has no class
  for a merge Tom has granted in advance.
