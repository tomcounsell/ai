---
tracking: none
slug: a2-advisor
type: plan
status: done
critique_rounds: 2
review_rounds: 2
---

# The advisor

Task A2 of `docs/plans/rebuild-finish-prompt.md`, under Tom's ruling of
2026-10-09 (`docs/plans/valor-rebuild-feedback.md`, last section): "In
cases where the agent does seek a 2nd opinion, it should spawn an advisor
agent to help it decide then act." Every citation is to
the rebuild branch at 1b9cde3ed.

**Goal.** A working turn that wants a second opinion writes its question
to `.valor/advice.md` and ends its turn. The kernel runs one fresh session
on the other vendor's model, which reads the question and a blind checkout
of the task's committed work and answers in prose. The working session's
next turn opens with that answer, in the same session and the same state,
and decides. The persona's conduct says when to ask.

**Serves.** Mission item 6, "Spend attention as carefully as money"
(`docs/mission.md:43`): a second opinion that would otherwise be a question
to Tom costs metered money instead of his attention. Mission item 1
(`docs/mission.md:23`): the job stays in one working context, since the
answer lands in the session that asked. Tom's ruling names the mechanism's
existence; this plan names its shape.

## What exists now

- A working turn reaches the kernel through files under `.valor/`
  (`core/signals.py:1-33`). `question.md` ends the turn and waits for Tom
  (`core/session.py:232`); a turn with no stage-ending signal is `idle`,
  and the next turn is prompted `Continue.` (`core/session.py:258`,
  `core/machine.py:52-57`).
- Fresh sessions run critique, review, and docs (`core/fresh.py:1-45`): a
  blind checkout from the kernel mirror holding only a base commit and a
  head commit the kernel made, inputs the kernel writes under
  `.valor/inputs/`, a sandbox profile of their own with no service port
  and no database credential (`workspace.check_harness`,
  `core/workspace.py:1579`), one turn recorded `fresh: true` that is never
  resumed. The fold records a fresh turn's state as `FRESH`, so it never
  names the working session, never spends an entry prompt, and never
  consumes steering (`core/machine.py:46-48`, `396-428`).
- The model seats (`core/settings.py:170-178`): `frontier` and `reviewer`
  on Claude Code at `claude-opus-5-5`, `reviewer_openai` on Pi at
  `gpt-6.1-sol`. Pi reaches OpenAI only through the gateway's OpenAI
  route, so its spending is metered like any turn (`docs/pi.md`,
  "Provider"). The review runner supports a fresh session at
  `reviewer_openai` (its `seat` parameter; it is registered at `reviewer`
  and run on Pi by tests) and there records `review.compared`, which the
  fold never reads (`core/fresh.py:846-858`, `docs/data.md:130`).
- A working turn's committed head reaches the kernel mirror through
  `session._keep` (`core/session.py:189`), which refuses a tree holding
  `.valor`.
- The objective tree: a child is a task with its own Brief, workspace,
  and full SDLC pipeline; its report reaches the parent when it delivers
  (`docs/objective-tree.md`). A turn that starts a child is a `propose`
  performer not yet built.
- The judgement layer answers closed questions with a probability per
  label, never prose, and every task needs a calibration record before it
  routes work (`docs/judgement-layer.md:1-12`, "Calibration discipline").
- The persona's conduct (`persona/conduct.md`, "Escalate only what needs
  Tom") lists six reasons to reach Tom, wider than the ruling's.

## The mechanism, and why

**A fresh session at the seat on the other vendor**, run by the kernel
between two working turns of the same task. Against the other two:

- **A child task** is the wrong size and the wrong timing. It provisions a
  workspace (a clone, a Postgres, a Redis), runs clarify, plan, critique,
  build, and the checks, and its report reaches the parent only when it
  delivers. A second opinion is one reading and one answer; a pipeline
  built to change code is all cost for it. A child also writes its own
  plan and candidate, which an advisor must never do.
- **A judgement site** cannot hold the answer. The layer returns labels
  from a closed set and never prose (`docs/judgement-layer.md:5-7`), and
  each site needs a calibration record before it routes anything. A
  second opinion is an open question whose useful answer is reasoning,
  and there is no labelled set to calibrate an open question against.
- **A fresh session** already exists in the shape an advisor needs: it
  reads what the kernel gives it, it cannot see the working session's
  narration, it has no effect, no service, and no credential, it runs one
  turn, and its spending is a metered turn. At `reviewer_openai` the model
  is another vendor's, trained apart from the one that did the work, so
  the opinion is a second one in fact and not the same model asked twice.

**Which seat.** The advisor's seat is the one whose harness differs from
the task's own: a task whose working session runs on `claude_code` gets
`reviewer_openai` (Pi, OpenAI); a task on `pi` gets `reviewer` (Claude
Code, Anthropic). `fresh.advisor_seat(harness_name)` holds that rule; it
reads `SEATS` and invents no model.

## What the advisor is not

The advisor is metered like any turn, is optional, and never holds,
refuses, or redirects work.

- **Metered like any turn.** It runs through `runs.run_turn` on the
  task's own gateway token, so every model call it makes is a
  `gateway.charged` row on the task, which `tasks.spending` and the tree's
  rollup already count (`core/tasks.py:595-613`). No budget, cap, or count
  limits how often a turn asks; spending is metered only.
- **Optional.** No stage file requires it, no state waits on it, and a
  turn that never writes `advice.md` runs exactly as it does today.
- **Never holds.** The kernel runs it at once when the asking turn ends,
  under the task's existing run lock and turn slot, and the next working
  turn starts as soon as it answers or fails. Nothing waits for a human
  or a tap.
- **Never refuses or redirects.** Its answer is prose quoted into the
  next prompt under a label, as data. It writes no verdict, no finding,
  and no row the fold reads (`advice.given` is information only, like
  `review.compared`). The task's state, ceiling, effects, and checks are
  untouched by anything it says; the working session decides and acts.
- **Fails open.** An advisor that cannot run (no provisioned mirror, a
  checkout the kernel cannot make, a failed or errored turn, an empty
  final message) records `advice.given` with the reason and no answer,
  and the next working turn is told the advisor did not answer and why.
  There is no retry; the turn may ask again.

So the governance boolean, "does this add a check, gate, hook, round, or
review step", answers **no**: nothing must pass through the advisor, and
nothing it says can stop, send back, or route work. It is a capability a
turn may use, not a step work takes.

## Design

### The signal (`core/signals.py`)

`advice` joins `TEXT_SIGNALS`: `.valor/advice.md`, read through the same
descriptor walk as `question.md`, filed under `.valor/handled/<turn_id>/`.
`Signals.advice: str | None`.

### Collection (`core/session.py`)

- `_verdict`: `advice.md` counts only on a turn whose verdict is `idle`
  (it finished with no stage-ending signal). Beside `question.md`,
  `no_question.md`, `plan.json`, or `done.md` the turn has already ended
  its stage, so `advice.md` goes to `errors` as "advice.md beside a
  signal that ends the stage; not asked", and the next prompt says so. On
  a turn that failed it goes to `errors` as "advice.md from a turn that
  did not finish; not asked". No new verdict: the machine is unchanged.
- `_verdict` returns the text in `extra["advice"]` only when it counted
  (the verdict is `idle`), and `_collected` writes that, never
  `found.advice`, so a turn whose `advice.md` did not count carries no
  `advice`. A row `_reduce` cuts down to `idle` after dropping a
  `done.md` or `plan.json` keeps no `advice` either: the extra was
  computed for the verdict the turn earned, and the reduced row is
  rebuilt without it. `_parts` lists the text, so an oversized question
  is dropped with its reason as every other part is (`_storable`).

### The advisor run (`core/fresh.py`, `advise`)

`advise(fresh_for)` returns `async def (gateway, task_id, dsn, alive,
asked)` where `alive` is `session.run`'s run-lock check and `asked` is the
`turn.collected` row that carried the question. It checks `alive()` where
the critique runner does (`core/fresh.py:168-287`): before the turn and
again before the append, and returns `lock lost` when the run lock has
died, appending nothing. It:

1. Reads the Brief and the fold. A stopped task returns `stopped`. A task
   with no mirror records the "fresh session runs only in a workspace the
   kernel provisioned" failure.
2. Reads the workspace head and its uncommitted paths (`git.head`,
   `git.dirty`, as `_candidate` does), and keeps the head in the mirror
   at `refs/valor/advice/<turn_id>` through `session._keep`. A refusal
   (a `.valor` entry, a fetch refused) is the recorded reason.
3. Makes a blind checkout of base and head
   (`workspace.blind_checkout`) under `<task>/checks/advice-<turn_id>/repo`
   and writes the inputs (`workspace.write_inputs`): `question.md` (the
   turn's words verbatim), `request.md`, `answers.md` (`_answers`, Tom's
   answers and feedback with provenance), `plan.md` (the plan's path and
   commit when there is one), `diff.patch` (base to head), and
   `uncommitted.md` (the paths the advisor cannot see, by name only).
4. Runs one turn with `workspace.check_harness(..., services=False)` at
   `advisor_seat(b.harness_name)`, `runs.run_turn(..., state=<the task's
   state>, fresh="advice")`. A stop returns `stopped`; a preemption
   returns `slot.PREEMPTED`, and the advice is asked again on the next run
   (below).
5. Reads the answer from the turn's final message (`ended["result"]
   ["text"]`), the same pipe review reads, so nothing in the checkout can
   plant it. An empty message is a failure with that reason.
6. Appends `advice.given` under the task lock, refusing nothing but a
   stopped task: `asked_turn_id`, `advisor_turn_id`, `seat`, `model`,
   `head`, `answer` or `error`, `usd_micros`. The row is first asked of
   Postgres (`ledger.unstorable`, as `_storable` does). When Postgres
   refuses it (a NUL character, a value past jsonb's size), the row is
   written with no `answer` and `error` set to Postgres's reason, so the
   question is answered once and `pending_advice` never asks it again.

### Where it runs (`core/session.py`, `run`)

`session.run` takes `advise` (default none, so tests and callers that
pass nothing behave as today). There is one call site: at the top of
each loop pass, `pending_advice(rows)` looks only at the fold's latest
collected turn (`f.last_collected`): it names that turn's question when
its verdict is `idle`, it carries `advice`, it ran in the task's current
state, no `advice.given` names it, and no working turn has finished
after it. When it names one, the loop calls `advise` before the next
turn; `lock lost`, `stopped`, and `slot.PREEMPTED` from it return from
`run` exactly as a turn's do (`core/session.py:93-94`), so a preempted
advisor leaves the question pending for the next run rather than a
working turn running past it. The git reads (`git.head`, `git.dirty`)
and the mirror fetch run in a worker thread, as `record`'s do. An older question, from a state the task has
left or overtaken by a later turn, is never asked. So a run killed or
preempted between the two asks it on the next run, without any new wake
or retry number, and only while it is still the last word.
`core/__main__.py` wires `fresh.advise(_fresh_for)` into `_working`.

### The answer reaching the turn (`core/session.py`, `next_prompt`)

`_advice_report(rows)`: the latest `advice.given` that no finished
working turn has followed, quoted line by line with `tasks.quoted` (the
function `_children_report` uses) under `# The advisor's answer` (with
the seat, model, and the commit it read), or under `# The advisor did not
answer` with the reason. It is spent only by a turn that finishes, as an
answer or findings are. It sits after the entry prompt and before the
effects report.

### The advisor's Brief (`core/tasks.py`, `skills/sdlc/advice.md`)

`dispatch(fresh="advice")` renders the persona and the Brief head as for
any fresh session, then `skills/sdlc/advice.md` in place of the verdict
channel, since the advisor answers in prose and writes no verdict. The
stage file says: you are the advisor; a working session asked the
question in `question.md`; read the inputs and the checkout; answer in
your final message with your recommendation, the evidence, and what
would change your mind; you decide nothing and run nothing that writes.
It also says the advisor asks no one: there is no advice channel and no
question channel in a fresh session, so the persona's line "when unsure,
ask the advisor" (rendered into every turn, the advisor's own included)
has nothing to act on here, and the advisor answers from what it has.

### The channel and the persona

The ruling's test for reaching Tom replaces the old one in all three
places that state it, so the new line has nothing left to contradict:

- `skills/sdlc/channel.md` gains one bullet: "A second opinion: write
  your question and the evidence in `.valor/advice.md` and end your turn.
  An advisor on another vendor's model reads it with a checkout of your
  committed work, and your next turn opens with its answer. The advisor
  informs; you decide. Commit what it should see." Its question bullet,
  "Ask only when the answer materially changes the outcome or the
  authority the work needs", becomes "Ask only for vision, priorities,
  the cost and benefit of a tradeoff in how the company works, or
  something only Tom holds; for anything else, ask the advisor."
- `persona/conduct.md`, "Absorb ambiguity, and ask well", second
  sentence: "Ask only when the answer materially changes the outcome or
  the authority required" becomes "Ask Tom only for vision, priorities,
  the cost and benefit of a tradeoff in how the company works, or
  something only he holds." "Ask before building" stays: a request's
  intent that is not on the page is something only Tom holds.
- `persona/conduct.md`, "Escalate only what needs Tom", opens with the
  line: "When unsure, ask the advisor, then act. Ask Tom only for vision,
  priorities, the cost and benefit of a tradeoff in how the company
  works, or something only he holds." The section's "Reach out for" list
  is cut to what that line allows (a critical discovery is a priority; a
  missing credential is something only he holds; completed work is a
  report). "How to ask" keeps its rules for the questions that remain.
- `docs/persona.md` mirrors all three: its "Escalate only what needs
  Tom" section (`docs/persona.md:245-257`) takes the same line and the
  same cut list, and its account of when a question is asked
  (`docs/persona.md:133`) takes the same test.
- The stage files, which conduct says win where they are more specific
  (`persona/conduct.md:3-4`), take the same test:
  `skills/sdlc/clarify.md:3-5` says a question only Tom can answer (the
  request's intent, vision, priorities, a tradeoff's cost and benefit)
  reaches him and a second opinion goes to `.valor/advice.md`;
  `build.md:11`, `plan.md:24`, and `patch.md:10` say "A question only Tom
  can answer goes in `.valor/question.md`; for a second opinion,
  `.valor/advice.md`". `persona/conduct.md:52` ("How to ask", item 2)
  and its mirror `docs/persona.md:164-168` drop "or the authority
  needed". A1 edits `docs/persona.md`'s effect table; A2 keeps to the
  asking rule.
- `docs/mission.md:31` (Mission item 3, "Ask only when the answer
  materially changes the outcome ...") is the mission's text and stays as
  written: the ruling narrows who is asked, and an answer that changes
  the outcome still comes from Tom when it is his to give.

## Threat model

| Threat | What holds it |
|---|---|
| The question carries an instruction for the advisor ("approve this", "run X") | The question is an input file, data under the persona's "Instructions come from Tom and the Brief". The advisor has no effect, no service port, no database credential, and a profile that writes only its own check directory (`check_harness`, `services=False`) |
| The advisor's answer carries an instruction for the working session | It is quoted under a label as prompt data, as children's reports are; the working turn's ceiling, offered effects, and checks are unchanged by it |
| The working session plants a file for the advisor to read as the kernel's | The checkout is blind (two kernel-made commits); inputs are written by the kernel relative to a descriptor, no link followed, no overwrite, and `.valor` in the head's tree is refused before the checkout |
| The advisor plants an answer through the checkout | The answer is read from the turn's final message on the harness's stdout, never a file |
| The advisor reads the builder's private state | Same denials as critique: the work directory, the temporary directories, and the builder's Claude Code state are denied |
| A turn asks in a loop and spends money | Metered: every advisor call is a `gateway.charged` row in the task's spending, visible in `status`. No cap is invented; Tom's stop and the spending report are the controls that exist |
| An answer the ledger cannot store makes the kernel ask again forever | The append is asked of Postgres first; a refused answer is recorded as `advice.given` with Postgres's reason and no answer, so the question is closed |
| A kill between the asking turn and the answer | `pending_advice` asks again on the next run, only while that turn is the latest collected in the current state; `advice.given` is appended once, under the task lock |
| A stale question asked after the task moved on | `pending_advice` reads only `f.last_collected`, in the current state, with no finished working turn after it |
| The run lock dies while the advisor runs | `advise` checks `alive()` before the turn and before the append and returns `lock lost`, as critique does |
| A stop during the advisor's turn | `run_turn` raises `TaskStopped`; the run returns `stopped` and nothing is appended |
| Secrets | Pi's per-turn gateway token and placeholder key, as for review at `reviewer_openai`; no key reaches the advisor's environment |

## Governance

The diff adds no check, gate, hook, round, review step, or approval: the
advisor is invoked only by a turn's choice, holds nothing, refuses
nothing, and routes nothing (see "What the advisor is not"). The Brief's
`governance_grant` stays none. The governance paragraph is untouched
everywhere it appears.

## Stakes

Kernel code and a stored row type: critique 2, review 2.

## Done, as evidence

1. A working turn that writes `.valor/advice.md` and nothing else is
   followed by one fresh turn at the other vendor's seat
   (`turn.started` with `fresh: true`, the seat's harness and model), one
   `advice.given` row with the answer, and a next working turn in the
   same session and state whose prompt carries `# The advisor's answer`.
2. The advisor's spending appears in the task's metered spending and its
   tree's.
3. With the advisor failing (a scripted error), the next working turn
   runs at once and is told why; the task never waits.
4. `advice.md` beside `done.md` (or `question.md`, `plan.json`,
   `no_question.md`) runs no advisor and is reported in `errors`.
5. A run killed after `turn.collected` and before `advice.given` asks on
   its next run, once.
6. The advisor's checkout holds no `.valor` from the workspace, and a
   `.valor` the advisor writes or a file it leaves changes nothing.
7. The persona renders the conduct line; the governance and tests
   paragraphs are byte-identical to `CLAUDE.md`'s.
8. The review's governance boolean answers no on the diff.

## Tests

Test database `valor_rebuild_test_a2build`, ports 6440 to 6449. Each test
uses the scripted harness (`tests/scripted.py`) the critique tests use;
none calls a model.

- `tests/test_signals.py`: `advice.md` is collected and filed like
  `question.md`; a link or FIFO under its name is `unreadable`.
- `tests/test_session.py`, parametrized over the stage-ending signals:
  `advice.md` with any of them runs no advisor and lands in `errors`;
  alone on a finished turn it is carried on `turn.collected`; on a failed
  turn it is in `errors`.
- `tests/test_session.py`: the loop runs `advise` once after an asking
  turn; the next prompt holds the quoted answer; a finished turn spends
  it; a failed turn does not; `pending_advice` reruns after a kill and
  not after an `advice.given`; it never names a question from a turn that
  is not the latest collected, from a state the task has left, or with a
  finished working turn after it; an `advise` that returns `lock lost`
  ends `run` with `lock lost`.
- `tests/test_fresh.py`: `advise` makes a blind checkout of the kept
  head, writes the six inputs, runs at `advisor_seat` (Claude Code task
  to `reviewer_openai`, Pi task to `reviewer`), reads the final message,
  and appends `advice.given` with `usd_micros`; each failure path (no
  mirror, `.valor` in the head, a failed turn, an empty message) appends
  the reason and no answer; a stop appends nothing; a dead run lock
  before the turn or before the append returns `lock lost` and appends
  nothing.
- `tests/test_fresh.py`, the storable row: an advisor whose final message
  holds a NUL character records `advice.given` with Postgres's reason and
  no answer, and a second run asks nothing (one advisor turn in all).
- `tests/test_tasks.py` or `tests/test_persona.py`: `dispatch(fresh=
  "advice")` carries `advice.md` (with its line that the advisor asks no
  one) and not the verdict channel; the conduct line renders, and the
  old test ("materially changes the outcome or the authority", "the
  authority needed", "A material question goes") appears in neither the
  rendered persona, `channel.md`, nor any stage file.
- `tests/test_session.py`, the reduced row: a `candidate` turn that also
  wrote `advice.md`, whose row `_reduce` cuts to `idle`, carries no
  `advice` and runs no advisor; a preempted advisor returns
  `slot.PREEMPTED` from `run` and no working turn runs.
- `tests/test_machine.py`: a fold over rows holding `advice.given` and an
  advisor's fresh turn equals the fold without them (state, session,
  entry, steering).
- The full suite and `uvx ruff check .`, `uvx ruff format --check .`.

## Files it changes

- `core/signals.py`, `core/session.py`, `core/fresh.py`, `core/tasks.py`,
  `core/__main__.py`.
- `skills/sdlc/advice.md` (new), `skills/sdlc/channel.md`,
  `skills/sdlc/clarify.md`, `build.md`, `plan.md`, `patch.md`,
  `skills/README.md`.
- `persona/conduct.md`.
- `docs/persona.md` ("Escalate only what needs Tom" and the question
  test at line 133), `docs/data.md`
  (`advice.given`), `docs/architecture.md` (fresh sessions),
  `docs/sdlc-state-machine.md` (an idle turn with `advice.md`),
  `core/README.md` (the fresh-session sentence).
- Tests above.

## Order with A1

A1 (`docs/plans/a1-autonomous-act.md`) removes the act hold and rewrites `skills/sdlc/channel.md`'s
"holds it for Tom's approval", `_effects_report`'s "held for Tom's
approval", and the persona and mission docs on approvals. A2 touches the
same `channel.md` and `core/session.py` and the same persona section.
A2 merges after A1, rebases onto it, and reruns its checks; it takes no
interface from A1. A1 edits `docs/persona.md`'s effect table and
`channel.md`'s effect lines; A2 keeps its persona edits to the asking
rule.

## Rollout

Kernel code: the rollout restarts the kernel through the existing
rollout command after merge, as A1's does. No schema change and no
migration: `advice.given` is a ledger row type like `review.compared`.

## Questions for Tom

1. Does the conduct section's list of reasons to reach Tom narrow to the
   ruling's four (vision, priorities, the cost and benefit of a tradeoff
   in how the company works, something only he holds), plus reports?
   Assumed: yes. The edit is in three places (the conduct list,
   "Absorb ambiguity", `channel.md`'s question bullet), mirrored in
   `docs/persona.md`.

## Decided by default

- The advisor is always the other vendor's model. Settled by the finish
  prompt (`docs/plans/rebuild-finish-prompt.md`, A2: "the second vendor
  makes it a real second opinion").
- The mechanism is a fresh session, not a child task or a judgement site,
  for the reasons under "The mechanism, and why".
- The advisor reads committed work only; uncommitted paths are named,
  not shown, because the blind checkout is made from the mirror and
  the working tree is the turn's to change.
- `advice.md` counts only on an idle turn: a turn that ended its stage
  has already decided.
- The answer is prose from the final message, not a JSON verdict: an
  opinion has no closed shape, and the final message is the one channel
  the checkout cannot rewrite.
- A failed advisor is reported and not retried; the turn may ask again.
- The seat rule picks by harness, not by a new seat: `SEATS` already
  names a pinned model on each vendor.

## Critique round 1 (of 2): revise

The mechanism held; six findings, each answered in this revision.

1. An answer the ledger cannot store would loop at cost. The append is
   asked of Postgres first (`ledger.unstorable`); a refused answer is
   recorded with Postgres's reason and no answer. Test added.
2. `pending_advice` could fire stale. It reads only `f.last_collected`, in
   the current state, with no finished working turn after it. Tests
   added.
3. No run-lock check. `advise` takes `alive` and checks it before the turn
   and before the append, returning `lock lost`, as critique does.
4. The persona still held the old test in two more places. The edit
   covers the conduct list, "Absorb ambiguity", and `channel.md`'s
   question bullet, with `docs/persona.md` mirroring all three; Question
   2 stays assumed yes.
5. Question 1 was settled by the finish prompt; moved to "Decided by
   default".
6. `skills/sdlc/advice.md` says the advisor asks no one, so the persona
   line rendered into its own Brief has nothing to act on.

Also: spending is named as the gateway's `gateway.charged` rows, and the
answer is quoted with `tasks.quoted`.

## Critique round 2 (of 2): revise

Rounds are spent; the three findings ride into the build, each written
into the design above.

1. The row carried the raw `advice.md` text whatever the verdict.
   `_verdict` returns it in `extra` only on `idle`; `_collected` writes
   that; `pending_advice` requires `idle`. Test: a `candidate` row reduced
   to `idle` runs no advisor.
2. The stage files still held the old asking rule and win over conduct.
   `clarify.md`, `build.md`, `plan.md`, `patch.md`, conduct's "How to
   ask" item 2, and `docs/persona.md:164-168` take the ruling's test; the
   persona test covers the stage files. `docs/mission.md` stays.
3. A preempted advisor was not returned. `run` returns `slot.PREEMPTED`
   from `advise` as from a preempted turn. Test added.

Notes taken: git reads in a worker thread; one call site
(`pending_advice` at the top of the loop); "supports" for the review
runner at `reviewer_openai`; the A1 hedge dropped. Steering is not an
advisor input; the working session holds it when it decides.

## Build

Built in `a2-advisor`. The tests sit in one file, `tests/test_advisor.py`,
rather than split across `test_session.py` and `test_fresh.py`; the
scripted harness (`tests/scripted.py`) gains an `advise` script and
`advising_runners`. Decisions taken in the build:

- The seat is the first of `ADVISOR_SEATS` (`reviewer_openai`, then
  `reviewer`) whose harness is not the task's.
- Advice counts only from an `idle` turn with no question, no-question
  statement, plan signal, or delivery note beside it; a stage signal
  present makes it not count even when it did not count itself.
- `advise` returns `advised` once `advice.given` is written, answer or
  error, and the loop goes on; `lock lost`, `stopped`, and
  `slot.PREEMPTED` end the run as a working turn's would.
- `core/__main__.py` is the one call site that wires `fresh.advise` into
  every working state's runner.
