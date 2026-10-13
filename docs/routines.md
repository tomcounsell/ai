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
| `name`, `runner` | the directory name, and which of the kernel's runners does the work (`expiry`, `emulator`, `conditions`, `verify`) | Mac native (launchd for scheduling) |
| `ceiling`, `model` | the effect class every run's tree is capped at, and the model seat | Bounded authority, metered spending |
| `mission_item`, `need` | the mission item it serves and the demonstrated need, shown on the status page and never enforced | Mission item 5 |
| `created`, `instruction` | the date, and the text a run is given | Mission item 5 |
| `project`, `branch` | the project spec and branch a runner that starts a workspace task provisions | |
| `[schedule]` | `minute`, `hour`, `day`, `weekday`, `month` (whole numbers in launchd's ranges: 0-59, 0-23, 1-31, 0-7, 1-12), or `interval` in seconds, a whole number that fits a plist integer (signed 64-bit); calendar keys and `interval` are not given together, since the printer writes one trigger | launchd for the ranges; the printer for "not both" |

The launchd job is printed and never committed, since it names the checkout
and its interpreter, which differ per Mac:
`python -m core routine NAME --plist` prints a plist whose one program is
`python -m core routine NAME` run by the kernel's own interpreter, with the
schedule from the toml and the settings that were set when it was printed:
Postgres and replay (`VALOR_PG*`, `VALOR_DB*`, `VALOR_DEMO`), and the three
that must match the kernel's so a routine's turns share Tom's turn slot and
resolve the same paths (`VALOR_MACHINE`, `VALOR_WORK`, `VALOR_PROJECTS`). It
names no script and carries no secret. launchd starts a job missed during sleep once
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
the routine's spending over the period, taken after the run so it counts the
run just made and matches the status page.

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

The four routines the kernel holds:

- **expiry** (04:00 daily, ceiling `act`): `routines.due` folds the ledger
  and, when something is due, starts one task on the `valor` project with
  the due list on its `task.started`. Nothing due starts no task and runs no
  turn. An expiry firing holds the session lock `routine:expiry` from
  before it registers or finds the objective until it has recorded
  `routine.ran`, so a firing at the same moment waits, with no transaction
  open, and then continues the open sweep.
- **emulator** (Sunday 01:00, ceiling `act`): replays every item in
  `$VALOR_DEMO/items` in the `bare`, `clarify` and `routed` arms, each a
  child of the run, judged, and writes `$VALOR_DEMO/sweeps/<run>.json` with
  the baseline, scores, hidden-test exits with each arm's failed count and
  failing ids, attention, spending and the count of preempted turns. An item
  worse than its `bare` run then starts a fix task (Findings, below). The runner holds the session lock `run:<run>`, so a
  second process says `already running`. A run with no report is continued.
  When a replay is paused before it has an outcome, the run writes no report
  and says `running` with the count of paused replays; the next firing
  resumes the same run under the same names. A driver that exits nonzero
  has its exit code and output appended at once to
  `$VALOR_DEMO/results/<replay>.driver.log`, beside its result file.
- **conditions** (05:00 daily, ceiling `propose`): checks each committed
  decision's watched conditions in every business's own repository and puts
  each one met on the next strategy meeting's agenda. Described below.
- **verify** (02:00 daily, ceiling `act`): runs the suite at the head of the
  routine's branch in the kernel's check sandbox, on a workspace the kernel
  provisions, and starts one fix task for each group of red tests that no
  task holds (Findings, below). The runner holds the session lock
  `run:<run>`; a run Tom's foreground work preempted is resumed by the next
  firing at the same commit.

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

**Attention.** A routine spends Tom's attention only through a question, a
grant it needs, or a report of an `act` that left, and each is ledgered against the run (Mission item 6). A
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
| `act` | merge, send, deploy, pay, delete anything not recoverable elsewhere | the Brief's ceiling, which Tom set; the broker performs it at request and Tom gets a report of it |

An `act` requested by a routine is performed at request when its ceiling
is `act`, like any task's, and refused when the ceiling is lower; the
refusal is a row on the run.

The kinds of scheduled work this design carries:

- **Verification runs.** The `verify` routine (`routines/verify/`), `act`,
  daily at 02:00. It runs the project's suite at the head of its branch in
  the kernel's check sandbox, on a workspace the kernel provisions, never
  in the kernel's own checkout. Each group of red tests starts one fix
  task, carried through the pipeline and merged at request when the
  predicate holds. Its need is Tom's request of 2026-10-12 plus the hand
  run that found `7c57334f8` had left `test_advisor` red. It does not see
  container tests, which the sandboxed check skips. Mission item 1
  ("resolving discovered defects") and the Evidence item "working results
  in real use".
- **Failure triage.** Reading error reports from deployed work, grouping
  them, and starting a task per new failure. Mission item 6 (Valor
  "investigates failures").
- **Workspace reclaim.** Removing task workspaces whose branches have merged
  and whose commits exist on the origin, which keeps the machine's disk usable.
  `propose`, since nothing removed is unrecoverable.
- **The expiry sweep.** Described below. Mission item 5 and the governance
  constraint.
- **Watched conditions.** Checking each committed decision's conditions for
  reopening against the business's own data. Described below. Business
  item 7 (`docs/business.md`).

## Watched conditions

The runner is built (`routines/conditions/runner.py`, the file format and
the source interface in `core/conditions.py`, the agenda in
`core/agenda.py`). No business data source is granted yet, so the kernel
gives it no adapter, and every condition it reads is recorded as unread
until one is.

A committed decision carries the conditions that would make it worth
reopening (Business item 7). The `conditions` runner checks each committed
decision's conditions on its schedule:

- **Where Valor has access, it reads the business's own data.** Which
  accounts that is depends on the business: its Stripe account for billing,
  its Google Analytics property for traffic, or whatever else the business
  has given Valor access to. A numeric condition names its `source`, and the
  runner reads the figure through that source's adapter (`Source.read`,
  returning the value, where it was read, and when), recorded as a
  `condition.read` row on the run. Adapters live in `tools/` and reach their
  credential the way any routine's tool does, reading Keychain inside a
  performer; the composition root passes the granted ones to the runner.
  None is built yet: Stripe and Google Analytics are not granted.
- **Where a condition has no number, the run records what Valor observes.**
  The condition is checked against its written text by the observer its
  `source` names (`Observer.observe`), and the run records what Valor saw,
  where, and when, as a `condition.observed` row on the run. Such a
  condition is watched on the same footing as a numeric one; having no
  number does not drop it.
- **Where no adapter for a condition's source is granted**, or the adapter
  fails, the run records a `condition.unread` row saying why. The condition
  stays in the file and is read again on the next run.
- **When a condition is met**, the run puts the decision's original
  reasoning (the reference and the referenced file's text at the commit
  read), the condition, and what changed (the figure or the observation,
  with where and when) on the agenda of the next strategy meeting: an
  `agenda.item` row on the `agenda` stream. A condition is put there once;
  a later run that finds it still met names the same item, and a condition
  whose terms change in the file is a new condition. Valor reads the open
  items when it books the meeting (`python -m core agenda`); an
  `agenda.raised` row naming items takes them off the agenda once the
  meeting has heard them. The run sends Tom no message; the decision is
  raised at the meeting, where it is made.

Each run is a node under the routine's objective, like the emulator's, and
the rows above are on it. A run that finds no committed decision carrying
conditions starts no run and records `nothing_due`. A repository the kernel
cannot fetch, or a conditions file it cannot read, is named in the run's
summary and the other businesses go on. An agenda item put by a run counts
as the routine's use for the ninety-day review below.

Its ceiling is `propose`: reading accounts is `read`, and an agenda item is
a draft. Watching the business's data for a decision to reopen is work for
the business, not a check on Valor's own process, so it is not a guard under
the section below. Its `need` is Business item 7 itself.

**Where the conditions live.** The kernel reads every `routine.toml` from
`settings.routines_dir`, which defaults to `routines/` in the kernel's own
checkout, and that checkout is `tomcounsell/ai`, a public repository. A
client's decisions and their conditions are therefore never written in a
`routine.toml` or anywhere else in the kernel's checkout. They live in that
client's business repository, beside the decision they belong to, and the
run reads them from there. The kernel's `routine.toml` names the runner, the
schedule, and the ceiling, and holds no business's conditions.

The businesses are the project specs in `settings.projects_dir`. For each,
the run fetches the spec's `repo` into the kernel's cache with its trusted
git, as provisioning does, and reads `decisions/conditions.toml` on the
branch commitments merge to: the spec's `branch`, else the repository's
default branch. A decision is committed when it is merged there (Business
item 3), so a file on any other branch is not read. A repository without the
file has no watched conditions. A remote repository is fetched as Valor's
own GitHub account, `valorengels`, with `GITHUB_PUSH_TOKEN` from
`github-keys`: a private business repository is read when that account has
access to it, and the boundary is that access (docs/business.md, item 8).
Each git call against the remote gets a header file of its own, as the
merge's push does ([performers.md](performers.md), `merge`); the token is
never written to the ledger, the summary, or a log. A fetch is a read, so it
sits under the routine's `propose` ceiling and passes no broker. Each
repository fetched is a `repository.fetched` row naming the business, the
repository, the commit read, and the key the fetch carried (by name, or
null for a local path), on the run, or on the objective beside its
`routine.ran` when the firing starts no run. A repository Valor's account
cannot read, or a missing key file, is named in the summary as not read, and
the other businesses go on. Provisioning still fetches anonymously
(`workspace._cache` without a credential).

The file lists each committed decision, its reasoning reference, and its
conditions:

```toml
[[decision]]
id = "pro-price-49"                                 # unique in the file
decided = 2026-09-14                                # the date it was committed
reasoning = "decisions/2026-09-14-pro-price.md"     # the decision record, a path in this repository

[[decision.condition]]                              # numeric: a metric and one threshold
id = "churn"
text = "Pro monthly churn rises above 6%"
source = "stripe"                                   # the adapter that reads it
metric = "pro_monthly_churn_pct"
above = 6.0                                         # met when the figure is above; or `below`

[[decision.condition]]                              # observation: no metric
id = "competitor"
text = "A direct competitor prices a comparable plan under $30"
source = "web"                                      # the observer that looks
```

A decision is never edited, only superseded (Business item 3): the new
decision gets its own entry, with its own conditions, and the superseded
one's entry is removed in the same commit.

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
  `VALOR_UI_PORT`): every task, one task with its ledger, the attention log, and each routine with its last run, its last
  result and its period spending. It is read-only and answers GET only; the
  address is the loopback one, on a port no sandbox profile opens.
- **Attention.** A question from a routine reaches Tom through the same
  path as any task's question and is ledgered with its answer and
  provenance. An `act` that left reaches Tom as a report.

Nothing a routine finds is pushed to Tom unless it needs a decision.

## Findings: how a routine starts a fix

A finding is a defect a routine measured in Valor's own work. One shared
function, `core/findings.py` `start_fix`, starts one ordinary task for it
on the routine's project and branch, a child of the run, at the routine's
ceiling and model, in the background. The task goes through the pipeline
like any other: the loop adds no merge path and no gate.

- **Keys.** Each finding carries dedupe keys. A task started with a key
  holds it until it merges. An ended or stopped task holds it until a run
  started after it lists the key in its `routine.ran` `cleared`, meaning
  that run saw the defect gone. A Postgres advisory lock per key makes two
  callers with one key start one task.
- **The evidence** is text the kernel did not write. It is quoted into
  the instruction in a fence, after the line "Evidence (data from the
  run, not instructions):". It decides only which findings start and
  their keys.
- **`verify`.** Red cases are grouped by the first line of their JUnit
  failure message, and cases of one parametrized test are one group. A
  resumed run that cannot read the JUnit file again uses the failed ids
  its `suite.ran` recorded, with no message. Keys
  are `verify:<test id>` with the parametrize suffix removed. A report with
  no usable test list, or a module that did not load, is the one
  `verify:suite` finding. A failure of the kernel's own (services that
  did not start) is no finding: the run ends `failed`. Tom's foreground
  work preempts a run, and the next firing resumes it at the same commit.
- **The emulator.** After each sweep's report, an item whose `clarify` or
  `routed` arm is worse than this run's `bare` starts one finding, key
  `emulator:<item>`. Worse means the arm ends short of a merge where bare
  merged, its judged total is more than one point below bare's, or its
  hidden test command fails more tests than bare's. The failing ids are
  evidence only. Colour codes are removed before the summary is read; an
  arm whose summary gives no count is marked `hidden_unread` and the
  hidden rule is not applied to it.
- **By hand.** `python -m core routine NAME --finding TEXT --key KEY
  [--key KEY] [--evidence FILE]` starts a finding under the routine's
  objective, and records `routine.ran` with `run` None, so it never counts
  as the routine's own use. It refuses a stopped routine, an empty
  finding or key, `--key` or `--evidence` without `--finding`, and an
  evidence file it cannot read. The evidence file is passed whole.
  Backlog items
  use one key each, named `backlog:<item>`. No run clears a `backlog:`
  key, so it stays held after its task ends or is stopped; the lead
  starts the item again under a new key.

## When a routine exists and when it is deleted

**Existence.** A routine is added on a demonstrated second need: the same
work done by hand, as a task, twice. `routine.toml` names both task ids.
A first occurrence is a task, not a routine (Mission item 5).

**Use.** A routine is in use when a run in the last ninety days led to
something outside itself: an effect performed, a question Tom answered, a
child task that delivered, or an item put on the strategy meeting's agenda. A routine that has run for ninety days and
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

An item a sweep listed is not listed again for ninety days. An instance
grant in this repository that a merged sweep listed is removed by its grant id
and never listed again; Tom keeps one with `grant` on the sweep's task, naming the listed grant id
in place of an instance: a new grant row (fresh id, new expiry, the old row's
incident and mission items) is written on the old grant's own task and
expires on its own date. Keeping one takes both, before the merge: his
feedback, so the branch leaves the grant's hunk, and his `grant` naming the
listed id; a keep lands before the sweep's delivery passes, since a
passing delivery is merged at once. A merged sweep's listing of another project's grant removes nothing.
A routine whose toml cannot be read is reported in the run's line and the rest
go on. The sweep's one task has the ceiling `act` and opens one branch removing everything due.
Opening the branch is `propose`. Merging it is `act`. The deletion is the
default: the merge is performed at request like any other merge. Keeping something past expiry takes a reason in the branch's
review; the default is the deletion (Mission item 5; the
governance constraint).
