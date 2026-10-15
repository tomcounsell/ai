---
tracking: none
slug: ask-reconciliation
type: build
status: planning
critique_rounds: 0
review_rounds: 0
governance_grant: required, one tap per instance through python -m core grant
---

# Asks: reconciled before they start, and an inbox with backpressure

(draft: sections being filled)

## The request

On 2026-10-15 the lead filed `backlog:slack-dm-reply-context` with
`python -m core routine verify --finding`, and it started task
`133f2ae0965b` with no look at the other open tasks. Asked how such tasks
are reconciled with other open tasks of the same scope, the answer was that
they are not. Tom then asked for:

> agent orchestration so that the task writer gets feedback from the
> reconciler confirming the shape of the task, for which the requestor might
> have additional useful context and an unknown level of importance on what
> is being asked for. at the same time, there should be some backpressure on
> the inbox for asks

This plan reads that request as three parts:

1. **Reconciliation.** Before an ask becomes work, a reconciler reads it
   against the open tasks on its project and proposes a shape: start it,
   fold it into a task that already covers it, supersede a narrower task, or
   split it.
2. **The writer confirms.** The proposal goes back to whoever wrote the
   ask, with the scope restated, the importance the reconciler assumes, and
   only the questions that would change the decision. The writer confirms or
   amends it, adding context and importance.
3. **Backpressure.** A project has a bounded number of asks in flight.
   Asks past the bound wait in a queue, ordered by importance, and are
   reconciled again when a place frees.

## Freshness check

The baseline is `8f8d0ae3f`, the head of `main` on 2026-10-15. The
claims below were read at that commit (the skeleton is `d1aade8cc`).
Disposition: **unchanged**. No other plan in `docs/plans/` touches intake,
the judge's inputs, or scheduling order.

## What exists

**Dedupe is an exact key match.** `findings.start_fix`
(`core/findings.py:131-170`) takes `finding:<key>` locks and starts
nothing when a task holds a key. Keys are free-text slugs, so two
spellings of one defect start two tasks. Only routine and hand-filed
findings carry keys. Tom's chat asks (`core/intake.py:590-612`) and CLI
`start` (`core/__main__.py:694-714`) have no dedupe at all.

**The judge sees one ask alone.** Site `intake.underspecified`
(`core/judgement_tasks.py:17-73`) reads the instruction, an empty thread,
and the project name (`core/judgement_sites.py:94-96`). It has no input
naming another task. `precise` goes to `plan`, and `thin` to `clarify`
(`core/machine.py:447-453`).

**Clarify asks only Tom.** A `question.asked` row moves the task to
`waiting`. The question notice goes to the operator chat
(`core/notices.py:85-86`), whoever wrote the ask. Anyone can answer
through `python -m core answer --by B` (`core/session.py:741-777`), and the
row carries provenance. No routine answers.

**Folding has no landing for an open task.** `session.feedback`
(`core/session.py:780-830`) accepts `merge`, `merged`, and `ended` only.
`message.steered` reaches a running task in any non-terminal state
(`core/machine.py:418-439`), but only intake writes it
(`core/intake.py:542-556`). The kernel never stops a task itself. Stops
come from Tom (`core/tasks.py:534-576`).

**Scheduling has no importance.** One foreground and one background
harness job run per process (`core/serve.py:425-426`). Each takes the
ready task with the oldest latest activity (`core/serve.py:706-712`).
Routine and replay subtrees are background (`core/tasks.py:473-488`).
No priority field exists.

**There is no queue or cap.** `docs/machine.md:179` says "an incoming
request becomes a queued task", but no queue is built. `docs/mission.md`
item 6 counts attention and never blocks on it, and `m1-4u-caps` removed
the earlier invented caps. The steering inbox in
`docs/judgement-layer.md:387-395` is designed, not built.

## Governance: these are guards

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

The reconciler is a guard, because it can hold an ask for the writer's
confirmation, and `governance.adds` counts "judging a request too thin and
sending it back" (`core/judgement_tasks.py:137-138`). A hard cap on asks in
flight would be a guard too. Importance ordering is not one: it changes
which ready task runs next and never refuses one.

**Mission items.** Item 6 (spend attention as carefully as money) and
item 1 (own outcomes across the whole job, which includes knowing what else
is already being built).

**Incidents that already happened without the reconciler** (read from the
ledger on 2026-10-15):

1. **Two tasks fixed one cause.** Verify run `91564c7cc2b4` started
   `cfb11735ee36` and `5172ae8d8b4f` one minute apart on 2026-10-15, under
   different finding keys. Both reds came from one cause: the cutover moved
   the valor spec to `main`, and tests still asserted `valor-cori-rebuild`.
   `5172ae8d8b4f` merged `611404ce2` (row 32505), which also fixed
   `cfb11735ee36`'s tests. `cfb11735ee36`'s push then failed (row 33201) and
   it rebased (row 33202), with git dropping its test commit as already
   applied (row 33498). It still ran review and docs, merged a plan file
   marked superseded (row 34466), triggered a rollout (row 34468), and sent
   Tom a notice (row 34467). Cost: $5.43 and one of Tom's interruptions.
   Exact key dedupe cannot catch this.
2. **Two open tasks build the same feature.** `d734763cd94e`
   (grant-by-reply) and `85abb1026556` (notice-ceo-shape) both build
   approve-by-reply in `core/intake.py` and `core/notices.py`.
   `85abb1026556` widened its own scope to include it (row 33265) and wrote
   "whichever lands second rebases". Neither has merged. Their build spend
   ($10.73 and $16.85) overlaps, and a conflict at merge is certain. This is
   harm in progress, not yet landed.

**No incident for a hard cap.** The audit found background tasks waiting
205 to 295 minutes for a first turn on 2026-10-14. The cause was an idle
turn loop (`8ab23532a169` ran 467 idle turns, then `623c4901e1d5` ran 906),
which `da3d28405819` fixes, not inbox volume. A cap would not have
prevented it, so this plan builds no cap. See Backpressure below.

**Grants Tom will be asked for.** Review names the instances. These are
the expected ones, each pasteable once the landing task exists:

```
.venv/bin/python -m core grant LANDING_TASK INSTANCE \
  --incident "(1) cfb11735ee36 and 5172ae8d8b4f fixed one cause; cfb1 merged a superseded plan, rolled out, and noticed Tom (rows 32505, 33201-33498, 34466-34468); (2) d734763cd94e and 85abb1026556 both build approve-by-reply (row 33265)" \
  --mission-item 6 --mission-item 1 \
  --note "the ask reconciler: intake.overlap site and the writer's confirmation"
```

## Design

### The shape of the flow

```
ask written ──> task.started ──> RECONCILE ──> judge ──> clarify / plan ...
                                    │
                    reconcile.decided (site intake.overlap)
                                    │
     distinct ──────────────────────┤  no message, straight to judge
     covered by T / supersedes T /  │
     overlaps T / split ────────────┴─> shape.proposed to the writer
                                          │
                       writer confirms or amends (shape.confirmed,
                       with context and importance) ──> the decision runs
```

`task.started` stays the first row of every ask, so the ask is recorded
verbatim once (Mission item 1, intake) and every existing path that starts
a task reaches the reconciler without change. The new state sits between
intake and `judge`.

### `reconcile`: read the ask against the open tasks

**State.** `State.RECONCILE`, entered by every `task.started` carrying
`sdlc`. It is `AT_ONCE` (`core/serve.py:112`), like `judge`, and needs no
harness slot. Calibration and legacy tasks never enter it. A child whose
parent is open on the same request (a plan's own children) skips it: the
parent already reconciled the scope.

**What it asks.** One judgement call at a new site, `intake.overlap`,
declared in `core/judgement_tasks.py` with `calibrated=None`. The inputs:

- the ask's instruction and its project;
- each open task on the same project: id, state, its instruction (the
  first `MESSAGE` characters), finding keys, and the paths its candidate
  changes when it has one (from `effect.intent`'s `landed`, or the
  candidate diff's names);
- the subjects of the target branch's commits since the oldest open
  task's `base_sha`, so work that already landed counts as covered.

"Open" is every task not in `merged`, `ended`, `stopped`, calibration, or
legacy.

**The answer.** One of:

| Decision | Meaning |
| --- | --- |
| `distinct` | No open task shares the scope. |
| `covered` | Task T already does this. Names T. |
| `supersedes` | This ask covers T's scope and more. Names T. |
| `overlaps` | Both should go ahead, but they touch the same code. Names T. |
| `split` | The ask holds parts that belong to different tasks. Names the parts. |

It also returns the scope restated in one or two sentences, the importance
it assumes (`low`, `normal`, `high`) with one line of reasoning, and at
most two questions whose answers would change the decision.

**Exit evidence.** `reconcile.decided` on the task: the decision, the
named tasks, the restated scope, the assumed importance, the questions,
the judgement id, and the cost. An abstain or a failure of both legs
reads as `distinct` with a note: the reconciler never holds an ask
because it could not answer.

### `shape.proposed`: the writer confirms

`distinct` goes to `judge` with no message. Tom spends no attention on an
ask that collides with nothing.

Any other decision writes `shape.proposed` and moves the task to
`waiting`, addressed to the ask's writer, read from `provenance.by`:

- **Tom (chat).** The proposal is sent as one notice in the chat the ask
  came from. It restates the scope, names the overlapping task and the
  proposed action, states the assumed importance, and asks at most two
  questions. Tom's reply binds as `answer` (`core/intake.py:533-536`). If
  the judge would also send the ask to clarify, clarify's questions travel
  in the same notice. One ask costs Tom at most one interruption, as today.
- **An agent at the command line** (`core start`, `routine --finding`).
  The command prints the proposal and the task id, and exits. The agent
  answers with `python -m core shape TASK --confirm` or
  `--amend "TEXT" [--importance high]`, which writes `shape.confirmed` with
  provenance. No notice goes to Tom. An unanswered proposal waits; `status`
  lists it under `attention`.
- **A routine.** A routine has no context to add. The proposal is applied
  as written (`shape.confirmed` with `by: routine`, `auto: true`), except
  `supersedes`, which a routine may not apply: it downgrades to `overlaps`.

An amend reruns the reconciler once with the writer's text added to the
inputs. The second answer is applied as it stands.

### What a confirmed decision does

| Decision | Effect |
| --- | --- |
| `distinct` | to `judge` |
| `covered`, T open | The ask's text goes to T as `ask.folded` (consumed like `message.steered`, `core/machine.py:418-439`), and the new task ends with outcome `folded` naming T. |
| `covered`, T in `merge`, `merged`, or `ended` | The ask goes to T as `feedback.given` through `session.feedback`, and the new task ends `folded`. |
| `supersedes` | T is stopped through `tasks.stop_tree`, carrying the writer's provenance. The new task goes to `judge`. Only Tom or an agent writer can confirm this. |
| `overlaps` | Both tasks get an `ask.folded` note naming the other, so their plans know who else touches the code. The new task goes to `judge`. |
| `split` | The writer starts the parts, each reconciled on its own. The original ends `split`. |

`ask.folded` is a new row kind that the working session reads at the top
of T's next turn, the same way it reads a steer. The kernel gains no new
power to stop work on its own: a stop happens only on a writer's confirmed
`supersedes`.

### Importance

`shape.confirmed` carries `importance`. When the writer gives none, the
reconciler's assumption stands, and a `distinct` ask records the
assumption on `reconcile.decided`. The scheduler's key at
`core/serve.py:706-712` becomes (foreground first, importance high to low,
oldest latest activity, task id). Importance orders ready work and never
refuses any. A writer can change it later with
`python -m core shape TASK --importance X`.

### Backpressure

Tom asked for backpressure on the inbox for asks. This plan builds the
part that has evidence behind it and leaves the hard cap to Tom's ruling
(Open questions):

1. **The writer sees the queue.** Every proposal, and the line a
   `distinct` ask prints at the command line, says how many tasks are open
   on the project, how many are ahead of this one at its importance, and
   which tasks are waiting on Tom. On 2026-10-15 nine tasks sat in `merge`
   waiting on Tom's grants, one since 2026-10-13. A writer who sees that
   can hold an ask back.
2. **Folding shrinks the queue.** `covered` asks become notes on existing
   tasks rather than new tasks.
3. **Importance decides who waits.** With ordering, a `low` routine
   finding waits behind `high` work rather than behind whatever happened to
   be touched least recently.

A hard cap (asks past N per project held as `queued` and reconciled again
when a place frees) is designed in Open questions but not built. It needs
an incident, and the audit found none: the 2026-10-14 starvation came from
an idle turn loop.

### Docs

- `docs/sdlc-state-machine.md`: a `reconcile` state section in the
  existing format (goal, what it asks, exit evidence, guard record), and
  the new rows in the model table.
- `docs/judgement-layer.md`: the `intake.overlap` site.
- `core/README.md`: `reconcile`, `shape`, `ask.folded`, and the
  scheduler's importance key.
- `docs/machine.md:179`: the "queued task" sentence says what is built.

## Out of scope

## Stakes

## Tests

## Done

## Rollout

## Open questions

## Record
