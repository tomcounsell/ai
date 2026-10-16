---
tracking: none
slug: ask-reconciliation
type: build
status: building
critique_rounds: 2
review_rounds: 1
governance_grant: none in this task (it adds no guard); the hold path waits for Tom's grant, written here
---

# Asks: reconciled against open work, and run in start order

Task 0b11dfcde385, restarted on the stripped main from b5233f922bb0. The
2026-10-15 design is at `22b030d9e:docs/plans/ask-reconciliation.md`. Tom's
answers are under Decided, and its incidents are kept below.

## Why this is narrower than the 22b030d9e plan

That plan built a `reconcile` state that holds an ask for the writer's
confirmation, folds it into another task, or stops a superseded one. It
also shipped in shadow, so that path stays dormant until the site is
calibrated. Three facts on today's main move the holding half to the task
that calibrates the site:

1. **The hold is a guard, and there is no grant for it.** The strip deleted
   `core grant` (`72569a5f9`). A grant is now Tom's words in this file, and
   a guard merged without one is a refused merge (`CLAUDE.md`).
2. **Dormant code is not done.** Nothing could use the hold until a
   calibration record exists, and that record needs two weeks of shadow
   decisions (rule 5).
3. **The test budget.** `tests/` is at 15 files (the limit) and 4,388 of
   5,000 lines. The earlier test list, together with the updates its new
   state forced on every fold test, does not fit.

Shadow needs no new state. If the overlap call runs inside the judge step
and its row changes no state, every old task folds exactly as before. That
removes the earlier critique's findings on the fold, the schema, and the
waiting path.

## What will be built

1. **Site `intake.overlap`** in `core/judgement_tasks.py`, `calibrated=None`.
   Inputs: the ask's instruction and project, and each open task on that
   project (id, state, and instruction cut to `MESSAGE`). Open means not
   merged, ended, stopped, calibration, or legacy. When the list is too long
   for one leg, it keeps the most recently active tasks and says how many it
   left out. Answer: `distinct | covered | supersedes | overlaps | split`,
   the tasks it names, the scope restated, and a proposed placement
   (`before T`, `after T`, or none).
2. **`judgement_sites.judge_runner`** asks the overlap site before
   `intake.underspecified` and writes one `reconcile.decided` row. If an
   answer that no `reconcile.decided` has consumed is already there, it uses
   that answer, the same way the judge does. An abstain or a failure of both
   legs is recorded as `distinct` with a note. The judge then runs exactly
   as today, whatever the decision: shadow holds nothing, writes no notice,
   and leaves the verdict alone. `machine.fold` applies the row and leaves
   the state as it is. Unknown kinds already do that (`core/machine.py`,
   the end of `_apply`).
3. **`python -m core shape TASK (--confirm | --amend TEXT) [--before T | --after T]`**
   with `--by` and `--role-played` provenance, as `answer` has.
   `--confirm` and `--amend` write `shape.confirmed`, the writer's label on
   the decision. Those labels are the shadow labels that Rollout step 2
   calibrates from. `--amend` also writes its text as `message.steered`,
   so the next turn reads it through the existing steering path.
   `--before` and `--after` write `task.placed`.
4. **Queue order** in `core/serve.py:schedule`. The sort key becomes
   (foreground first, queue position, task id). The old key's middle term
   was the latest activity. `tasks.queue(conn, project)` computes positions
   once per pass. It starts from start order (the id of each active task's
   `task.started` row), then applies every `task.placed` row as a move, in
   row id order, so the latest placement wins. A chain therefore resolves
   in order, a cycle resolves to whichever placement came last, and a
   placement whose anchor is not active is skipped. The reconciler's
   proposed placement is used only when the writer gave none.
5. **The writer sees it.** `core start` and `routine --finding` print a
   queue line after the task id: open tasks on the project, how many are
   ahead, and which wait on Tom. `status TASK` shows `reconcile.decided`
   and the same line. Both read from `tasks.queue`.

Docs: `docs/judgement-layer.md` (the site), `docs/sdlc-state-machine.md`
(the judge section gains the row, and the rows table gains the three new
kinds), `core/README.md` (`shape` and queue order), and `docs/machine.md:155`
(what the "queued task" sentence means now).

## Out of scope

- **The hold path**: `shape.proposed` waiting on the writer, `ask.folded`,
  folding into T, and stopping a superseded T. It is built in the task
  that calibrates `intake.overlap`, which holds Tom's grant written in this
  file. The grant would name the incidents below and mission items 6 and 1.
- **A cap on asks in flight.** Tom ruled it out on 2026-10-15, and it has
  no incident.
- **Message binding** (`623c4901e1d5`), and work that lives only on hand
  branches.

## Governance

This diff adds no guard. The overlap site holds, refuses, and redirects
nothing; the judge decides as today. Queue order picks which ready task
runs next and never refuses one. The hold path is the guard. These are the
incidents its grant will name:
(1) `cfb11735ee36` and `5172ae8d8b4f` fixed one cause, and the first then
merged a plan already superseded, ran a rollout, and sent Tom a notice
(rows 32505, 33201-33498, 34466-34468; $5.43);
(2) `d734763cd94e` and `85abb1026556` both build approve-by-reply
(row 33265).

## Stakes

The scheduler's order changes for every task, and every ask costs one more
judgement call. Two risks follow:

- **A looping old task can hold a slot.** Under start order, the oldest
  ready task wins every pass, so an old task stuck in a loop keeps its
  slot (`623c4901e1d5` ran 906 turns). With `max_turns` at 2, it holds one
  of the two slots and the other slot still moves. The queue line names
  the task holding each slot.
- **Cost.** The extra call costs about what the judge costs (46 micros on
  `133f2ae0965b`).

## Tests

Tests go into the existing files, about 250 lines in all.

- `test_serve.py`:
  - ready background work runs in start order;
  - `--before` and `--after` move a task;
  - a chain of placements resolves in order;
  - a cycle resolves to the latest placement;
  - an anchor that is not active is skipped;
  - foreground work runs ahead of placed background work;
  - a proposed placement applies only when the writer gave none.
- `test_pipeline.py` (scripted upstream):
  - `reconcile.decided` comes before `judge.decided`;
  - a `covered` decision still reaches the judge, with no notice and no
    wait;
  - an abstain or a failure of both legs is recorded as `distinct`;
  - the open-task inputs leave out other projects and tasks that are
    terminal, calibration, or legacy, and cut a long list with a count.
- `test_machine.py`:
  - a pre-rollout row sequence folds as before;
  - `reconcile.decided`, `shape.confirmed`, and `task.placed` leave the
    state unchanged.
- `test_kernel.py`: `core shape` writes `shape.confirmed` with provenance,
  `--amend` steers its text, and `--before` writes `task.placed`.
- `tests/scripted.py` gains the scripted `intake.overlap` answer, so the
  existing pipeline tests change in one place. `test_budget.py` stays
  green.

## Success criteria (unchanged from 22b030d9e)

1. Every `sdlc` task started after rollout has one `reconcile.decided` row
   before `judge.decided`.
2. The instructions of `cfb11735ee36` and `5172ae8d8b4f`, replayed through
   the live site, decide `covered` or `overlaps` naming the other. So do
   those of `d734763cd94e` and `85abb1026556`. The replay outputs are
   recorded below.
3. A `distinct` ask from Tom produces no notice that today's flow would
   not.
4. Ready tasks run in queue order, checked on `status` and on the
   scheduler's log line.
5. The suite is green, and `ruff check` and `ruff format --check` are clean.

## Decided (Tom, 2026-10-15)

1. No cap for now. Backpressure means visibility, folding, and queue order.
2. Valor is the PM. As an agent writer he places work, and he confirms
   `supersedes`, without asking Tom.
3. Order is first in, first out, placed by the writer, with the
   reconciler's proposal used when the writer gives none. There is no 1 to
   10 score.

## Rollout

1. **Shadow (this task).** Rows are recorded and shown to the writer, and
   queue order is live.
2. **Calibrate (next task).** Labelled pairs come from incidents 1 and 2,
   the disk reclaim group (`87fd35370d9c`, `8426bdd16707`, `e0063d753740`),
   and the `shape.confirmed` labels. The same task builds the hold path
   under Tom's grant.

## Record

- 2026-10-15: drafted, and Tom answered (`22b030d9e`). Under b5233f922bb0,
  the first critique raised seven findings against the `reconcile` state.
- 2026-10-16: re-planned on the stripped main. The hold moves to the
  calibration task, and shadow runs inside the judge step (see "Why this is
  narrower").
