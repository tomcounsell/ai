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

A routine is a directory under `routines/<name>/` holding two files, both in
git and both changed only by a reviewed diff:

| File | Holds | Serves |
|---|---|---|
| `routine.toml` | the instruction, the effect ceiling, the model, the mission item it serves, the two task ids that show its second need, and the date it was created | Bounded authority, metered spending; Mission item 5 |
| `<label>.plist` | the launchd job: a `StartCalendarInterval` or `StartInterval` and one program argument list that calls the kernel CLI with the routine's name | Mac native (launchd for scheduling) |

The plist never names a script of its own. Its program is the kernel's entry
point, so the only path from a schedule to an effect runs through the broker.
launchd starts a job missed during sleep once on wake and coalesces several
missed intervals into one run, so a laptop that sleeps through a schedule
gets one late run, never a burst.

The plist carries no secrets in its environment. A run that needs a
credential reaches it through a tool that reads Keychain, inside a performer
the broker calls (Mac native: Keychain for secrets).

## How a run starts

Design: launchd runs `python -m core routine <name>`. The kernel reads
`routine.toml`, commits a Brief for this run, and runs its turns exactly as
`python -m core run` does. The run ends the way any task ends: a delivery,
a question, or a stop. One
`python -m core stop <task>` stops it.

The current kernel has no `routine` command and no routine record. It has
`start` and `run`, which already give a scheduled job everything that
matters: a plist that calls `python -m core start ...
--ceiling read` and then `python -m core run <task>` gets a
ceiling, the gateway, and the ledger. What it lacks is the routine's
identity on the task (so the ledger can say which routine a run belongs to)
and the period report of spending.

## Metered spending and ceiling

**Per-run spending.** Every model call a run makes is metered by the
gateway and recorded on the run's task, and money never stops the run. A
routine that makes no model call still runs as a task with metered spending
of zero, so its effects still pass through the broker and land in the
ledger. Constraint: Bounded authority, metered spending.

**Period spending.** Design: a routine is a standing objective, and each
run is a child whose metered spending rolls up into the routine's. The
routine's spending over its thirty-day period is reported, so a routine that
runs more often than expected shows it; nothing stops it on money. The
current kernel has a single task record and no objective tree, so the
period report is design.
Constraint: Bounded authority, metered spending.

**Ceiling.** The ceiling is set in `routine.toml`, copied into each run's
Brief, and never widens at run time. Least privilege [11] sets the default:
a routine gets the lowest class its job needs.

**Attention.** A routine spends Tom's attention only through a question or a
held `act`, and both are ledgered against the run (Mission item 6). A
routine's attention is counted on the same footing as its money when the
ninety-day review below asks whether it earned its place.

**One turn at a time.** On a 16 GB machine one `claude -p` runs at a time
(`docs/machine.md`). A scheduled run that finds a turn already running waits
for it; Tom's tasks are never queued behind a routine. Design: the current
kernel does not serialize turns across tasks.

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
- **The dashboard.** `ui/` shows routine runs beside Tom's tasks: spending
  over the period, last run, last result. Read-only.
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
produced only "nothing found" is unused.

**Deletion.** An unused routine is deleted by default at ninety days. The
expiry sweep is itself a routine: on a schedule it reads the ledger for
unused routines, expired guards, and tools and skills unused for ninety
days, and opens one branch removing all of them. Opening the branch is
`propose`. Merging it is `act`, and waits for Tom's tap like any merge.
Keeping something past expiry takes a reason in the branch's review, given
by Tom; the default is the deletion (Mission item 5; the governance
constraint).

## Gaps

- The `routine` command, the routine identity on a task, and the period
  spending report do not exist in the current kernel.
- Serializing turns across tasks so a routine never runs beside Tom's work
  is design; the current kernel does not do it.
- Whether the expiry sweep's deletion branch should merge without a tap is
  open. The constraint makes every merge `act`, and the kernel has no class
  for a merge Tom has granted in advance.
- No routine ran in the demonstration or the replay baseline, so nothing in
  this design has been exercised.
