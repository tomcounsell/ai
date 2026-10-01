# SDLC state machine

How a request becomes a delivered result: the states a task passes through,
the verdict each state ends with, the transitions those verdicts select, and
what the ledger records at each step. The state machine is kernel code in
`core/`. The work done inside a state (how to build, how to verify) is a
skill; the kernel decides which state a task is in, which state comes next,
and what may leave the machine.

This doc serves the acceptance question in [mission.md](mission.md): Tom
gives Valor an imperfectly specified goal and returns to something that
works and needed less of his attention than doing it himself. A state or
check that does not serve that question, through a named mission item and a
named incident, is not in the model.

## The model in one table

| State | Entry | What runs | Exit verdicts | Next |
|---|---|---|---|---|
| `judge` | `task.started` | one judgement-tier call over the request | `precise`, `thin` | `build`, `clarify` |
| `clarify` | judge said `thin` | one frontier turn that inspects and changes nothing | `asked`, `no_material_question` | `waiting`, `build` |
| `waiting` | `question.asked` | nothing; the task waits for Tom | `answered` | `build` |
| `build` | judge `precise`, clarify done, an answer, feedback, or a finding sent back | frontier turns in one resumed session | `candidate`, `asked`, `idle`, `failed` | `breadth`, `waiting`, Tom |
| `breadth` | a candidate delivery | the app's suite at head and base, then one judgement-tier call over diff and tests | `broad`, `gaps` | `verify`, `build` (once) |
| `verify` | breadth finished | one blind verification of the candidate | `pass`, `fail`, `governance_refused` | `delivered`, `build` (once), Tom |
| `delivered` | verify `pass` | delivery shown to Tom; `act` effects held | `released`, `feedback` | stays `delivered`, `reopened` |
| `reopened` | `feedback.given` on a delivery | nothing; the next run resumes the build session | | `build` |
| `stopped` | `task.stopped` from any state | nothing, ever again | | none |

Two rules hold across every row:

- **State is a fold over the ledger, never a stored field.** The kernel
  computes a task's state by reading its events in order. Nothing writes a
  "current state" column, so a stop, a crash, or a failed turn at any
  instant leaves nothing to reconcile. Constraint: reliable stop, recovery,
  and correction.
- **State belongs to the task, never to the session executing it.** A task
  is keyed by its id in the events table. The harness session a turn
  resumes is recorded on `turn.ended` and is replaceable; losing it loses no
  state. Constraint: reliable stop, recovery, and correction.

## What exists and what is design

The current kernel (`core/tasks.py`, `core/session.py`) computes four
states as a fold over the ledger: `live`, `waiting for Tom`, `delivered`,
and `stopped`. In the terms above:

| Kernel state today | Model state |
|---|---|
| `live` | `build`, and `reopened` until the next run starts |
| `waiting for Tom` | `waiting` |
| `delivered` | `delivered` |
| `stopped` | `stopped` |

The kernel has the feedback loop (`python -m core feedback`), the question
and answer path, the held `act` effect with approve and release, stop, and
the attention log with provenance. It runs each task in one of two modes
recorded on the Brief: `bare` (the instruction alone) and `clarify` (the
Brief asks for an inspect-and-ask first turn). Which mode a task gets is
chosen by whoever starts it.

The `judge`, `breadth`, and `verify` states are design. Today a turn that
writes `.valor/done.md` produces `task.delivered` at once, with no breadth
check or verification between the build and Tom. The typed state model,
the verdict rows named below, and the transition table are design.

## Types

The model as the kernel will hold it. Names are design; the shapes follow
the kernel's existing convention of frozen dataclasses and JSONB payloads.

```python
class State(StrEnum):
    JUDGE = "judge"
    CLARIFY = "clarify"
    WAITING = "waiting"
    BUILD = "build"
    BREADTH = "breadth"
    VERIFY = "verify"
    DELIVERED = "delivered"
    REOPENED = "reopened"
    STOPPED = "stopped"

class JudgeVerdict(StrEnum):    PRECISE = "precise";  THIN = "thin"
class ClarifyVerdict(StrEnum):  ASKED = "asked";      NO_MATERIAL_QUESTION = "no_material_question"
class BreadthVerdict(StrEnum):  BROAD = "broad";      GAPS = "gaps"
class VerifyVerdict(StrEnum):   PASS = "pass";        FAIL = "fail";  GOVERNANCE_REFUSED = "governance_refused"

TRANSITIONS: dict[tuple[State, str], State] = {
    (State.JUDGE, "precise"): State.BUILD,
    (State.JUDGE, "thin"): State.CLARIFY,
    (State.CLARIFY, "asked"): State.WAITING,
    (State.CLARIFY, "no_material_question"): State.BUILD,
    (State.WAITING, "answered"): State.BUILD,
    (State.BUILD, "asked"): State.WAITING,
    (State.BUILD, "candidate"): State.BREADTH,
    (State.BREADTH, "broad"): State.VERIFY,
    (State.BREADTH, "gaps"): State.BUILD,        # once per candidate
    (State.VERIFY, "pass"): State.DELIVERED,
    (State.VERIFY, "fail"): State.BUILD,         # once per candidate
    (State.DELIVERED, "feedback"): State.REOPENED,
    (State.REOPENED, "run"): State.BUILD,
}
# Any state, on task.stopped: State.STOPPED. No edge leaves STOPPED.
```

A verdict outside its state's enum is refused when the row is written, so
the fold is total: every ledger prefix maps to exactly one state. The table
is the whole graph. A transition not in it does not happen.

**"Once per candidate"** means a candidate delivery may go back to `build`
from `breadth` once and from `verify` once. A second `gaps` proceeds to
`verify` with the gaps listed; a second `fail`, or a `governance_refused`,
goes to Tom as a delivery that did not pass, with the verifier's findings
and Valor's recommendation. The count is a fact derived from the ledger
(how many `breadth.checked` and `verification.decided` rows follow the last
`feedback.given` or `task.started`), never a counter field. The money
budget bounds every path regardless; the count exists so that a candidate
that does not converge reaches Tom with evidence instead of spending the
budget on itself. Mission item 6, and the incident in the setup plan's Why
section: 53 review-round commits in eleven days, in a process that
"reproduced Valor's ceremony".

## The control loop

`python -m core run TASK` is the router. Each call folds the ledger to the
current state, runs the one piece of work that state calls for, records its
verdict, and continues until the task reaches a point that needs Tom:
`waiting`, `delivered`, a candidate that did not pass, the budget's end,
two idle turns, a failed turn, or `stopped`. It then prints one status line
and returns. The router never decides authority; it reads verdicts and
follows the table. A classifier decides what a thing is; the kernel decides
what it may do (README, Three tiers).

Which tier runs each state:

| State | Tier | Why that tier |
|---|---|---|
| `judge` | judgement (Jev-class, hosted, open-weight fallback behind the port) | a classification, well under $0.05 per request (rebuild-demonstration.md, Money) |
| `clarify`, `build` | frontier agent, one `claude -p` turn at a time | the hard work |
| `breadth` | deterministic suite run, then judgement | running tests is deterministic; naming untested behavior is a classification |
| `verify` | blind verifier (see [architecture.md](architecture.md)) | an independent check, never the executor grading itself [4, 16] |
| `delivered` | kernel and broker, then Tom | authority |

The judgement port, its fallback, and confidence gating are specified in
[judgement-layer.md](judgement-layer.md). This doc names only the verdicts
the state machine consumes.

## States

### `judge`: read the request before any build

**Entry.** `task.started`, carrying Tom's request verbatim.

**What runs.** One judgement-tier call over the request and a short summary
of the code it names. It asks whether the request is underspecified in one
of three ways the evidence identified:

1. a one-line ask whose intent lives only in Tom's head;
2. an ask that leans on an example which may stand for a wider requirement;
3. an ask that names existing UI or behavior without saying what happens to
   it.

**Exit verdicts.** `precise` goes to `build`. `thin` goes to `clarify`. A
call below its confidence threshold is treated as `thin`: the cost of a
wrong `thin` is one clarify turn that may end without bothering Tom (see
`no_material_question` below); the cost of a wrong `precise` is review
rounds.

**Ledgered.** `judgement.answered` at site `intake.underspecified`: the
label (which cue fired), the confidence, the leg and model, the metered
cost, and the guard id ([judgement-layer.md](judgement-layer.md)).

**Mission items.** 3 (ask only when the answer materially changes the
outcome) and 6 (attention spent as carefully as money).

**This is a guard, and it is ledgered as one.** Tom granted it on
2026-10-01. Its guard record carries:

- **Incidents.** The first demonstration (rebuild-demonstration.md,
  Attention log): psyoptimal #894, kernel task `32f800bce8a2`, three
  decisions settled by two PM feedback rounds that one message of three
  questions before building would have settled; delivery 1 covered one of
  seven profile items because it read the example as the whole
  requirement. The replay baseline (rebuild-baseline.md, pop-b and pop-c):
  on popoto #191 the bare arm scored fidelity 1 and passed 3 of 11 hidden
  tests, the clarify arm fidelity 3 and 7 of 11; on popoto #188 the bare
  arm built a feature Tom did not want and needed a PM round to pivot,
  while the clarify arm finished at fidelity 5 for half the spend.
- **Counter-evidence it must respect.** On the three precise requests
  (#872, #646, #893) clarifying changed nothing, and on #633 a question
  with a wrong premise made the result worse (rebuild-baseline.md,
  Clarify). The judge exists to route only thin requests, so precise ones
  go straight to `build`.
- **Expiry.** 2026-12-30, ninety days from the grant. If it has not routed
  a request to `clarify` that changed the outcome by then, it is deleted by
  default.

**Gap.** The three cues come from one demonstration and six replays, n = 1
per item and arm (rebuild-baseline.md, Caveats). The judge's precision and
recall are unmeasured. The emulator ([emulator.md](emulator.md)) measures
them by replaying the same cases with and without the judge, and its
calibration is reported as a Brier score with sample size [12].

### `clarify`: inspect, then ask or proceed

**Entry.** `judge` said `thin`.

**What runs.** One frontier turn in the task's workspace that reads the code
and changes no file. It ends by writing either `.valor/question.md` or a
statement that no question would materially change the result. A question
message holds the numbered questions whose answers change what gets built
or the authority it needs, each with the answer Valor will assume if Tom
leaves it open, plus the approach Valor intends to take in a few lines.
Nothing the code can settle is asked.

**Exit verdicts.** `asked` writes `question.asked` and enters `waiting`.
`no_material_question` goes to `build` with the stated approach in the
session's context, and Tom spends no attention.

**Ledgered.** `turn.started` and `turn.ended` for the turn, `turn.collected`
for what it left, and `question.asked` with the question text when it
asks.

**Mission items.** 3 ("An unclear brief produces a useful first version
before it produces a questionnaire" is respected by asking only what
inspection cannot settle) and 6. The drive to ask under uncertainty about
Tom's intent follows Cooperative Inverse Reinforcement Learning [3]; Valor
borrows the shape without its guarantee.

**Evidence.** The question message in the baseline's clarify arm carried
what the old plan document carried: a short statement of intended approach
sent before building (rebuild-baseline.md, Plan, critique, revise). On #872
it raised both questions the old system had put to Tom.

**Current kernel.** The `clarify` mode's Brief section asks for exactly
this turn and always writes `question.md`, even when it holds no question.
The `no_material_question` exit is design.

### `waiting`: Tom has a question

**Entry.** `question.asked`, from `clarify` or from any `build` turn.

**What runs.** Nothing. The run returns and prints the question. A bridge
delivers it to Tom (see [bridges/telegram.md](bridges/telegram.md)).

**Exit verdict.** `answered`: `question.answered` with Tom's text. The next
run resumes the same harness session with the answer as its prompt.

**Ledgered.** `question.answered` carries provenance: `by` (who wrote it),
`via` (the surface), `at`, and `role_played` (true when someone stood in
for Tom). An answer is spent only by a turn that finishes; after a failed
or stopped turn the next turn opens with it again.

**Mission item.** 6. Every question and answer is an attention-log entry,
and the attention a task cost is a fold over its ledger
([mission.md](mission.md)). Provenance is the constraint "corrections carry
provenance": in the demonstration, rows 177 and 207 both read
`"by": "tom"` though 207 was role-played (rebuild-demonstration.md, Kernel
findings, 5). **Exists in the kernel.**

### `build`: the work

**Entry.** `judge` said `precise`; `clarify` found no material question;
Tom answered; Tom's feedback reopened the task; or `breadth` or `verify`
sent a candidate back once.

**What runs.** Frontier turns, one `claude -p` at a time, in the task's
workspace, all resuming the session the task's first turn opened so Valor
keeps its working context. Each turn gets the Brief rendered from the
ledger as it starts, corrections included. The skill for this state covers
the whole job named in Mission item 1: understand the problem, inspect what
exists, choose an approach, implement, run the app's tests, and update the
docs the change makes untrue. Effects beyond the workspace are requests to
the broker.

The turn's prompt is the instruction on the first turn, Tom's answer after
a question, his feedback after a delivery, the breadth gaps or verifier
findings after a send-back, and "Continue." otherwise, each followed by
what became of the effects the previous turn requested.

**Exit verdicts.**

- `candidate`: the turn wrote `.valor/done.md` saying what it delivered,
  how it verified it, and which decisions Tom might want to change. Goes to
  `breadth`.
- `asked`: the turn wrote `.valor/question.md`. Goes to `waiting`. A build
  turn may ask whenever it meets a decision that materially changes the
  outcome or the authority required (Mission item 3).
- `idle`: two consecutive turns ended with neither a question nor a
  candidate. The run returns so Tom can look.
- `failed`: the harness reported an error or the turn did not finish. The
  run returns; nothing is lost and the next run retries from the ledger.

**Ledgered.** `turn.started` (with the Brief's digest and the correction
numbers it carried), every gateway reservation and charge, `turn.ended`
(with the harness session id and outcome), `turn.collected`, each effect's
`effect.held`, `effect.intent`, `effect.outcome`, or `effect.refused`, and
`question.asked` or the candidate's summary.

**Mission items.** 1 (own the whole job) and 2 (the simpler design; the
demonstration's delivery was under half the reference's size,
rebuild-demonstration.md, What was delivered).

**Evidence for one session per task.** Delivery 2 and delivery 3 of the
demonstration were each one resumed turn at $0.89 and $0.85
(rebuild-demonstration.md, Money). A send-back to `build` costs one turn
in the same session, which is why the model has no separate patch stage.
The cost of resuming grows with the session (about 30,000 input tokens on
the first call, 109,000 on the last); when to start fresh with a summary is
specified in [harnesses.md](harnesses.md).

**Evidence for the idle exit.** In the baseline, pso-a's bare run ended 2
of its 3 turns idle, waiting on background tests the turn's end had killed
(rebuild-baseline.md, Caveats). **Exists in the kernel** (`IDLE_TURNS = 2`).

**Current kernel.** A `done.md` produces `task.delivered` directly. In the
model it produces a candidate, recorded on `turn.collected`, and
`task.delivered` is written only by `verify` passing.

### `breadth`: are the tests broad enough

**Entry.** A candidate from `build`.

**What runs.** Two steps.

1. **Suite at head and base.** The app's relevant test suite runs in the
   workspace at the candidate's head and at the task's base commit. The
   result is the list of failures at head that do not fail at base.
   Deterministic.
2. **Missing cases.** One judgement call (use shape 8) reads the diff and the tests
   the candidate added or changed, and lists behaviors the diff changes
   that no test exercises: records in states other than the obvious one
   (archived, inactive, past), every member of an enumeration the code
   branches on, and existing tests whose bounds encode the old behavior.

**Exit verdicts.** `broad`: no new failures and no listed behavior. Goes to
`verify`. `gaps`: new failures or untested behaviors, with the list. Goes
back to `build` once with the list as the prompt; a second `gaps` on the
same candidate goes to `verify` with the list attached, and the verifier
and Tom see it.

**Ledgered.** `breadth.checked`: the head and base commits, the new
failures, the listed behaviors, the judgement call's model, confidence, and
cost, the verdict, and the guard id.

**Mission item.** 1 ("testing actual use" and "resolving discovered
defects").

**This is a guard.** Its incident (rebuild-baseline.md, Test breadth):
every replay wrote fewer tests than its reference, and the hidden tests
found what that missed. psyoptimal #872 missed the archived-team guards
(the only 2 hidden tests either arm failed there); popoto #191 missed the
list key name and hash exclusion (bare 3 of 11 hidden tests); popoto #633
clarify broke a bound in an existing test calibrated to the old cost and
was accepted anyway, with 1 new broad-suite failure. The demonstration's
delivery wrote 10 tests against the reference's 35 and left archived teams
and per-sector rules untested (rebuild-demonstration.md, What was
delivered). Its expiry is ninety days from the grant recorded in its guard
row; see Open for Tom below.

**Gap.** The suite run needs the app's Python environment in the
workspace. The demonstration's workspace kept none, so the reference PR's
tests could not run against Valor's branch there
(rebuild-demonstration.md, What was delivered). The baseline provisioned
one per run. Workspace provisioning is specified in
[architecture.md](architecture.md).

### `verify`: one blind verification

**Entry.** `breadth` finished on a candidate.

**What runs.** The blind verifier, specified in
[architecture.md](architecture.md). The state machine relies on three of
its properties:

- It never sees the executor's narration. It reads the request, Tom's
  answers and feedback, the diff, the test results, and the docs the diff
  touches as the contract. An LLM judge favors its own and verbose output
  [16]; the control stance assumes the executor may present its work
  favorably [4].
- It runs deterministic checks before any judgement.
- It asks the one governance boolean over every diff: "does this add a
  check, gate, hook, round, or review step". A yes with no
  `governance_grant` on the Brief is a refused merge.

**Exit verdicts.**

- `pass`: writes `task.delivered` and enters `delivered`.
- `fail`: findings go back to `build` once as the prompt. A second `fail`
  on the same candidate goes to Tom as a delivery that did not pass, with
  the findings and Valor's recommendation.
- `governance_refused`: the candidate adds governance without a grant. It
  goes to Tom with the diff hunk named; he either grants it, one tap per
  instance, or it goes back to `build` to come out.

**Ledgered.** `verification.decided`: the candidate's head commit, the
verdict, the findings, the governance boolean and its answer, the
verifier's model and cost. The audit sample and calibration against it are
in [architecture.md](architecture.md) [12, 17].

**Mission item and constraints.** Mission item 1, the Evidence item
"Independent checks", the constraint "Docs describe reality" (the verifier
reads the doc as the contract), and the governance constraint (the
boolean). The blind verifier is a constraint of the setup plan, not a
granted guard, and carries no expiry.

**Evidence.** A reviewer that sees the deliverable as presented is
lenient: the Sonnet stand-in accepted popoto #191 bare at judge fidelity 1
and #633 clarify at correctness 2, with the stale-cache bug moved rather
than removed (rebuild-baseline.md, Review rounds and Caveats). The one
review in the old record that mattered was Tom's on #633, which found a
subtle state bug. Review earns its place on stateful, subtle changes, and
only from a strong reviewer.

### `delivered`: shown to Tom, `act` held for his tap

**Entry.** `verify` said `pass`, or a candidate that did not pass is shown
to Tom as such.

**What runs.** The delivery summary reaches Tom through a bridge: what was
delivered, how it was verified, the breadth and verification verdicts, and
the decisions Valor made that Tom may want to change, with the one that
most changes the outcome first. Every `act` effect the task requested (a
push to a shared remote, a merge, a send) sits held in the broker.

**Exit verdicts.**

- `released`: Tom approves a held effect (`approval.granted`, bound to the
  effect's payload digest, carrying his literal message), and the release
  performs it (`effect.intent`, then `effect.outcome`). One tap, one
  effect. The task stays `delivered`.
- `feedback`: Tom sends the delivery back (`feedback.given`). Enters
  `reopened`.

**The merge predicate.** A merge is an `act` effect like any other. The
broker performs it only when all of these are facts in the task's ledger,
evaluated over the head commit being merged:

1. a `verification.decided` row with verdict `pass` for that commit;
2. that row's governance boolean answered no, or the Brief carries a
   `governance_grant` and Tom tapped each instance;
3. a `breadth.checked` row for that commit;
4. an unused `approval.granted` bound to this merge effect's digest.

Each term is deterministic: a row exists or it does not. No model call
decides whether a merge may happen.

**Ledgered.** `task.delivered` with the summary; `effect.held`,
`approval.granted`, `effect.intent`, `effect.outcome` per effect.

**Mission items and constraints.** Mission item 1 ("delivering within
authority") and 6. Constraint: bounded authority and spend; `act` needs Tom
per action [11].

**Evidence.** The demonstration's three pushes went requested, held,
approved, released, performed (rows 174 and 176, 204 and 206, 253 and 255;
rebuild-demonstration.md, Where Tom acted as project manager). Delivery 1
listed decisions Tom might change and left out the one that mattered most,
its reading of the example as the whole spec (rebuild-demonstration.md,
Attention log); hence the order rule for the decisions list.

**Exists in the kernel:** held `act` effects, approve, release, and the
digest-bound one-time approval. **Design:** the merge predicate's first
three terms, which need the `breadth` and `verify` rows.

There is no closed state after a merge. Defects found in use come back as
feedback on the same task (Mission item 1, "resolving discovered
defects").

### `reopened`: Tom's feedback on a delivery

**Entry.** `feedback.given` on a `delivered` task. A stopped task takes no
feedback; a task with an open question takes an answer instead.

**What runs.** Nothing until the next run, which resumes the build session
with the feedback as its prompt. The new candidate goes through `breadth`
and `verify` again, with the once-per-candidate counts starting fresh, and
a later delivery is a new `task.delivered`.

**Ledgered.** `feedback.given` with the delivery it is on and provenance
(`by`, `via`, `at`, `role_played`). The attention log labels each entry by
kind and records whether it changed the outcome or the authority
([mission.md](mission.md)).

**Mission item.** 1 (Tom never coordinates the gaps, so a defect he finds
goes back to the same task, not a new one) and the Evidence item "Tom's
feedback, both directions". Feedback recording work Tom loved, the
exemplar ledger, is in [architecture.md](architecture.md).

**Evidence.** The demonstration had no feedback path until Tom needed one
after delivery 1 (rebuild-demonstration.md, Kernel findings, 2); two
feedback rounds carried the three decisions Valor inferred wrongly. A
failed turn consumed pending feedback until it was fixed so that only a
finishing turn spends it (Kernel findings, 4). **Exists in the kernel.**

### `stopped`

**Entry.** `task.stopped` from any state, written by `python -m core stop`.

**What runs.** Nothing. The row is the fence: the gateway refuses every
later model call and the broker every later effect by reading it, whether
or not the running process hears the notification. A stopped task takes no
answer and no feedback. No edge leaves it.

**Ledgered.** `task.stopped` with the reason and who stopped it.

**Constraint.** Reliable stop, recovery, and correction: stop is immediate
and lossless. The off switch lives in the kernel, not in the model's
incentives [8, 9, 10].

**Evidence.** The demonstration's failed resume (turn `79ff46fd3a58`, rows
178 to 180) failed cleanly, metered $0, and lost nothing
(rebuild-demonstration.md, Kernel findings, 3). **Exists in the kernel.**

## Budget exhaustion

Not a state. Every model call in every state is metered by the gateway
against the task's one money budget. When the remaining budget cannot cover
a call, the gateway refuses it, the run returns "budget exhausted", and the
task keeps its state. Spending more is Tom's choice, since scope expansion
is his (constraint: bounded authority and spend). **Gap:** the kernel has
no command to raise a task's budget.

## Attention in the ledger

Mission item 6 makes attention a ledger item, not only a reported number.
Every point where Tom acts on a task is a row with provenance:

| Row | State that produces it | Provenance |
|---|---|---|
| `question.asked`, `question.answered` | `clarify`, `build`, `waiting` | `by`, `via`, `at`, `role_played` |
| `feedback.given` | `delivered` | `by`, `via`, `at`, `role_played` |
| `approval.granted` | `delivered` | `by`, Tom's literal message |
| a guard grant | before `judge` or a governance-adding merge | Tom's literal message, incident, mission item, expiry |

**Gap.** `approval.granted` records `by` and Tom's message but not
`role_played`. In the demonstration two of three pushes were approved under
Tom's standing permission rather than a live tap (rebuild-demonstration.md,
Where Tom acted as project manager); the row cannot say so.

The attention log's format and its "changed the outcome, changed the
authority" labels are specified in [mission.md](mission.md).

## Stages the model does not have

Each line is the current design and the evidence for it.

- **The model has no issue stage.** A task starts from Tom's request
  verbatim (`task.started`); the demonstration ran from the Notion card's
  two sentences with no issue written first (rebuild-demonstration.md, The
  request, verbatim).
- **The model has no plan stage.** Neither baseline arm wrote a plan
  document, and both reached judge fidelity 4 on the two psyoptimal items
  whose old pipeline ran a full plan chain; on the four other items the
  chain bought nothing measurable (rebuild-baseline.md, Plan, critique,
  revise). The intended approach travels in the clarify message or the
  candidate's `done.md`.
- **The model has no critique or revision rounds.** psyoptimal #872's old
  record holds 8 plan, critique, and revision commits for a result both
  replay arms matched on fidelity without one (rebuild-baseline.md, pso-a);
  across the six weeks before the rebuild, 1,199 of 1,571 commits on
  `main` (76%) were plan, critique, revision, and migrate-plan commits
  (setup plan, Why).
- **The model has no separate docs stage.** Replays wrote little
  documentation, the judge counted it as a minor divergence each time, and
  nothing in the runs depended on it (rebuild-baseline.md, Docs). Docs a
  change makes untrue are part of `build`, and `verify` reads them as the
  contract.
- **The model has no multi-round review.** The old #872 took 3 review
  rounds and no replay needed one to be accepted; the judge's main finding
  traced to recon, not to those reviews (rebuild-baseline.md, Review
  rounds). One blind verification per candidate, plus Tom's feedback on the
  delivery, carries what review carried.
- **The model has no patch loop.** A breadth gap or a verifier finding goes
  back to `build` as one more turn of the same session, once per
  candidate; the demonstration's two send-backs were one resumed turn each
  (rebuild-demonstration.md, Money).
- **The model has no merge stage.** A merge is an `act` effect held in
  `delivered` and released by Tom's tap under the merge predicate.

## Open for Tom

1. **Test-breadth grant.** The lean SDLC you set on 2026-10-01 includes the
   breadth check. This doc treats that decision as its grant, ledgered with
   a ninety-day expiry like the judge step. Confirm, or say it needs its own
   tap.
2. **Low-confidence judge calls.** This doc routes them to `clarify`, which
   costs Tom nothing when inspection finds no material question. The
   alternative is routing them to `build`, which the baseline's tie on
   precise requests also supports.
3. **Verifier strength.** The baseline's lenient reviewer was Sonnet-class.
   Whether `verify` runs on a frontier model, and what that adds to a
   task's spend, is open; [architecture.md](architecture.md) owns the
   verifier.
4. **Feedback through the judge.** Feedback goes straight to `build`. Thin
   feedback ("closer, but not right") could be judged like a request.
   Nothing in the evidence shows thin feedback yet.
