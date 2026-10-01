# SDLC state machine

How a request becomes a merged result: the checkpoints a task passes
through, the verdict each one ends with, the transitions those verdicts
select, and what the ledger records at each step. The state machine is
kernel code in `core/`. The work done inside a state is a skill; the kernel
decides which state a task is in, which state comes next, and what may
leave the machine.

This doc serves the acceptance question in [mission.md](mission.md): Tom
gives Valor an imperfectly specified goal and returns to something that
works and needed less of his attention than doing it himself.

## The pipeline Tom set

Tom's decisions of 2026-10-01 fix the shape:

- **Every stage is a checkpoint.** Intake, the request judgement and
  clarify step, plan, critique, build, test, the test-breadth check,
  review, patch, docs, and merge. Each leaves a ledger row the next stage
  and the merge predicate read.
- **Each stage states a goal and its exit evidence, not steps.** A stage's
  skill says what must be true when the stage ends and what row proves it.
  How to get there is the agent's call.
- **The plan sets the loops.** The plan decides how many times critique may
  send the plan back (0, 1, or 2) and how many times review may send the
  work to patch (0, 1, or 2), from the stakes. The replay baseline covered
  six small changes and no large high-stakes one (rebuild-baseline.md), so
  the count is a call per task, not a constant.
- **Leave it cleaner than we found it.** The plan and the review may pull
  in scope that pays down known tech debt related to the change.
- **Patch is the builder.** The session that built resumes to patch,
  compacted if it needs to be. A patch never starts a new agent.

That decision is the grant for every checkpoint here that holds or
redirects work (the judge, the breadth check, critique and review loops);
each is ledgered as a guard with its incident, mission item, and a
ninety-day expiry.

## The model in one table

| State | Goal | Runs | Exit verdicts | Next |
|---|---|---|---|---|
| intake | the request recorded once, verbatim, with its source | kernel | (writes `task.started`) | `judge` |
| `judge` | decide whether the request is thin before anyone builds | judgement tier | `precise`, `thin` | `plan`, `clarify` |
| `clarify` | ask only what inspection cannot settle | working session | `asked`, `no_material_question` | `waiting`, `plan` |
| `waiting` | Tom answers | nothing | `answered` | the state that asked |
| `plan` | an approach, its stakes, its loop counts, its scope | working session | `planned`, `asked` | `critique`, `waiting` |
| `critique` | find what the plan gets wrong before code exists | fresh session | `sound`, `revise` | `build`, `plan` or `build` |
| `build` | a candidate that does what the plan says | working session | `candidate`, `asked`, `idle`, `failed` | `test`, `waiting`, Tom |
| `test` | the suite passes at head where it passed at base | deterministic | `green`, `red` | `breadth`, `patch` or Tom |
| `breadth` | the tests exercise what the diff changes | judgement tier | `broad`, `gaps` | `review`, `patch` or `review` |
| `review` | an independent verdict on the candidate | blind verifier | `pass`, `changes`, `governance_refused` | `docs`, `patch` or Tom |
| `patch` | the findings resolved | working session, resumed | `candidate`, `asked`, `idle`, `failed` | `test`, `waiting`, Tom |
| `docs` | no doc says something the change made untrue | working session | `updated`, `no_change` | `merge` |
| `merge` | the delivery shown, the merge held for Tom's tap | kernel, broker, Tom | `released`, `feedback` | `merged`, `patch` |
| `merged` | the change is on the target branch | nothing | `feedback` | `patch` |
| `stopped` | nothing more happens | nothing, ever again | | none |

"`patch` or Tom" means the send-back count decides (see Loops). The
working session is the task's one harness session: the first frontier turn
opens it, and plan, build, patch, and docs resume it. Critique and review
each run in a fresh session that never reads it.

Two rules hold across every row:

- **State is a fold over the ledger, never a stored field.** The kernel
  computes a task's state by reading its events in order. Nothing writes a
  "current state" column, so a stop, a crash, or a failed turn leaves
  nothing to reconcile. Constraint: reliable stop, recovery, and correction.
- **State belongs to the task, never to the session executing it.** A task
  is keyed by its id in the events table. The harness session a turn
  resumes is recorded on `turn.ended` and is replaceable; losing it loses
  no state. Constraint: reliable stop, recovery, and correction.

## What exists and what is design

The current kernel (`core/tasks.py`, `core/session.py`) computes four
states as a fold over the ledger: `live`, `waiting for Tom`, `delivered`,
and `stopped`.

| Kernel state today | Model state |
|---|---|
| `live` | `plan`, `build`, `patch`, `docs`, and `clarify` while a turn is due |
| `waiting for Tom` | `waiting` |
| `delivered` | `merge`, `merged` |
| `stopped` | `stopped` |

The kernel has the question and answer path, the feedback loop, the held
`act` effect with approve and release, stop, the attention log with
provenance, and two first-turn modes on the Brief (`bare`, `clarify`)
chosen by whoever starts the task. A turn that writes `.valor/done.md`
produces `task.delivered` at once. Everything else here is design.

## Types

The model as the kernel will hold it. Names are design; the shapes follow
the kernel's convention of frozen dataclasses and JSONB payloads.

```python
class State(StrEnum):
    JUDGE = "judge"; CLARIFY = "clarify"; WAITING = "waiting"
    PLAN = "plan"; CRITIQUE = "critique"; BUILD = "build"
    TEST = "test"; BREADTH = "breadth"; REVIEW = "review"
    PATCH = "patch"; DOCS = "docs"; MERGE = "merge"
    MERGED = "merged"; STOPPED = "stopped"

VERDICTS: dict[State, frozenset[str]] = {  # one enum per state in code
    State.JUDGE: {"precise", "thin"}, State.CLARIFY: {"asked", "no_material_question"},
    State.PLAN: {"planned", "asked"}, State.CRITIQUE: {"sound", "revise"},
    State.TEST: {"green", "red"}, State.BREADTH: {"broad", "gaps"},
    State.REVIEW: {"pass", "changes", "governance_refused"},
    State.DOCS: {"updated", "no_change"}, ...
}

@dataclass(frozen=True)
class Loops:
    critique_rounds: Literal[0, 1, 2]
    review_rounds: Literal[0, 1, 2]

TRANSITIONS: dict[tuple[State, str], State] = {
    (State.JUDGE, "precise"): State.PLAN,
    (State.JUDGE, "thin"): State.CLARIFY,
    (State.CLARIFY, "asked"): State.WAITING,
    (State.CLARIFY, "no_material_question"): State.PLAN,
    (State.PLAN, "planned"): State.CRITIQUE,
    (State.CRITIQUE, "sound"): State.BUILD,
    (State.CRITIQUE, "revise"): State.PLAN,       # while critique rounds remain
    (State.BUILD, "candidate"): State.TEST,
    (State.TEST, "green"): State.BREADTH,
    (State.TEST, "red"): State.PATCH,             # once per pass
    (State.BREADTH, "broad"): State.REVIEW,
    (State.BREADTH, "gaps"): State.PATCH,         # once per pass
    (State.REVIEW, "pass"): State.DOCS,
    (State.REVIEW, "changes"): State.PATCH,       # while review rounds remain
    (State.PATCH, "candidate"): State.TEST,
    (State.DOCS, "updated"): State.MERGE,
    (State.DOCS, "no_change"): State.MERGE,
    (State.MERGE, "released"): State.MERGED,
    (State.MERGE, "feedback"): State.PATCH,
    (State.MERGED, "feedback"): State.PATCH,
}
# "asked" from CLARIFY, PLAN, BUILD, or PATCH: WAITING; "answered" returns
# to the state named on the question.asked row.
# Any state, on task.stopped: STOPPED. No edge leaves STOPPED.
```

A verdict outside its state's enum is refused when the row is written, so
the fold is total: every ledger prefix maps to exactly one state. The table
is the whole graph. A transition not in it does not happen.

## Loops

Every send-back is counted from the ledger, never from a counter field:
the kernel counts the relevant rows since the last `task.started` or
`feedback.given`.

- **Critique rounds.** Critique always runs once on a plan. A `revise`
  goes back to `plan` while fewer than `critique_rounds` revisions have
  happened; after that, its findings ride into `build` as part of the
  prompt. With `critique_rounds` 0, critique reads the plan once and its
  findings go straight to the builder.
- **Review rounds.** Review always runs once per pass. A `changes` goes to
  `patch` while fewer than `review_rounds` send-backs have happened; after
  that, the candidate reaches Tom in `merge` as a delivery that did not
  pass, with the findings and Valor's recommendation. With
  `review_rounds` 0, any `changes` goes to Tom.
- **Test and breadth.** A pass is the stretch from entering `test` to the
  next review verdict. In one pass, `red` and `gaps` may each send the work
  to `patch` once. A second `red` in the same pass goes to Tom in `merge`
  as a delivery that did not pass; a second `gaps` proceeds to `review`
  with the list attached.

The money budget bounds every path. The counts exist so that work that
does not converge reaches Tom with evidence instead of spending the budget
on itself: Mission item 6, and the setup plan's Why section (53
review-round commits in eleven days).

**Setting the counts.** The plan states the stakes in a sentence and picks
both counts: the more a mistake would cost and the harder it is to undo,
the more loops. As guidance, a small reversible change in well-tested code
is a 0, and a change to stored data, money, authentication, migrations, or
the kernel itself is a 2. The kernel refuses a value outside 0 to 2.
Critique may raise either count, never lower it, and the kernel uses the
higher, so the builder's call on its own stakes is read by a session that
did not make it.

## Scope: leave it cleaner than we found it

The plan may add work that pays down known tech debt related to the change:
a workaround the change makes removable, a duplicate the change touches, a
test that encodes behavior the change replaces. Each addition is listed in
the plan's scope with the debt it pays. Review may do the same: a finding
of kind `debt` names related debt the candidate left in place and goes to
`patch` like any other finding, inside the review rounds.

The bound is the task's Brief. Added scope spends from the same budget and
never raises the effect ceiling, and debt unrelated to the change is a new
task for Tom, not an addition. Mission items 1 ("resolving discovered
defects") and 5 (compound leverage through completed work).

## The control loop

`python -m core run TASK` is the router. Each call folds the ledger to the
current state, runs the one piece of work that state calls for, records its
verdict, and continues until the task reaches a point that needs Tom:
`waiting`, `merge`, a candidate that did not pass, the budget's end, two
idle turns, a failed turn, or `stopped`. It then prints one status line and
returns. The router never decides authority; it reads verdicts and follows
the table. A classifier decides what a thing is; the kernel decides what it
may do.

| State | Tier | Why that tier |
|---|---|---|
| `judge`, `breadth` | judgement (Jev, open-weight fallback behind the port) | a classification, well under $0.05 per call (rebuild-demonstration.md, Money) |
| `clarify`, `plan`, `build`, `patch`, `docs` | frontier agent in the working session | the hard work, in one context from inspection to delivery |
| `critique` | frontier agent in a fresh session | a reading of the plan by a session that did not write it |
| `test` | deterministic suite run | running tests decides nothing by judgement |
| `review` | blind verifier, Opus-class (see [architecture.md](architecture.md)) | an independent check, never the executor grading itself [4, 16] |
| `merge` | kernel and broker, then Tom | authority |

The judgement port is [judgement-layer.md](judgement-layer.md)'s; session
resume and compaction are [harnesses.md](harnesses.md)'s.

## States

Each state gives its goal, its exit evidence, and why it exists.

### intake: the request, once

**Goal.** One durable statement of what was asked, in the requester's
words, that every later stage reads.

**Exit evidence.** `task.started`, carrying the request verbatim, its
source (a message id, an issue, a card), and the Brief. When the request
came from an issue, the row names it; otherwise the task record is the
issue. Every later checkpoint reads the request as written, never a
paraphrase (Mission item 1).

### `judge`: read the request before any build

**Goal.** Route a request whose intent lives only in Tom's head to
`clarify`, and let a precise one through untouched.

**What it asks.** One judgement-tier call over the request and a short
summary of the code it names, against three cues the evidence identified:
a one-line ask whose intent lives only in Tom's head; an ask that leans on
an example which may stand for a wider requirement; an ask that names
existing UI or behavior without saying what happens to it.

**Exit evidence.** `judgement.answered` at site `intake.underspecified`:
the label, the confidence, the leg and model, the metered cost, and the
guard id. `precise` goes to `plan`; `thin` goes to `clarify`. A call below
its confidence floor counts as `thin`: a wrong `thin` costs one clarify
turn that may end without bothering Tom, a wrong `precise` costs review
rounds.

**Guard record.** Granted by Tom on 2026-10-01; mission items 3 and 6.
Incidents: psyoptimal #894 (task `32f800bce8a2`: two PM feedback rounds
for three decisions one message would have settled; delivery 1 read the
example as the whole requirement), popoto #191 (bare fidelity 1, 3 of 11
hidden tests; clarify 3 and 7 of 11), and #188 (bare built a feature Tom
did not want). Counter-evidence: on #872, #646, and #893 clarifying changed
nothing, and on #633 a wrong-premise question made it worse
(rebuild-baseline.md, Clarify). Expiry 2026-12-30, deleted by default if it
has not changed an outcome by then.

**Gap.** n = 1 per item and arm; the emulator ([emulator.md](emulator.md))
measures precision and recall, reported as a Brier score with n [12].

### `clarify`: inspect, then ask or proceed

**Goal.** Every question whose answer changes what gets built, or the
authority it needs, reaches Tom in one message; nothing the code can settle
is asked.

**Entry.** `judge` said `thin`. The turn opens the working session and
changes no file.

**Exit evidence.** `question.asked` with the numbered questions, the answer
Valor will assume for each, and the intended approach in a few lines
(verdict `asked`, to `waiting`); or a statement on `turn.collected` that no
question would materially change the result (`no_material_question`, to
`plan`, with Tom spending no attention).

**Why.** Mission items 3 and 6; asking under uncertainty follows [3],
borrowed without its guarantee. On #872 the clarify message raised both
questions the old system had put to Tom (rebuild-baseline.md). Today the
mode always writes `question.md`; `no_material_question` is design.

### `waiting`: Tom has a question

**Goal.** Tom's answer lands in the context that asked.

**Entry.** `question.asked` from `clarify`, `plan`, `build`, or `patch`. The
row names the asking state.

**Exit evidence.** `question.answered` with Tom's text and provenance
(`by`, `via`, `at`, `role_played`). The next run resumes the working
session with the answer as its prompt, in the state that asked. An answer
is spent only by a turn that finishes; after a failed or stopped turn the
next turn opens with it again.

**Why.** Mission item 6. Provenance exists because demonstration rows 177
and 207 both read `"by": "tom"` though 207 was role-played
(rebuild-demonstration.md, Kernel findings, 5). **Exists in the kernel.**

### `plan`: the approach, the stakes, the loops

**Goal.** A short plan the builder, the critic, and the reviewer can all
hold the work against: what will be built and how, what is out of scope,
the stakes in one sentence, `critique_rounds` and `review_rounds`, the
tech-debt additions with the debt each pays, and the tests that will show
it works, including the cases beyond the obvious one.

**Entry.** `judge` said `precise`, `clarify` found no material question, or
Tom answered. A `revise` from critique returns here with its findings.

**Exit evidence.** The plan as a file in the workspace, committed, and
`plan.written`: its commit, its digest, the stakes sentence, both counts,
and the scope additions. A revision writes a new `plan.written`. A material
question found while planning is `asked`.

**Why.** Mission items 1 and 3, and Tom's decision that the plan sets how
much checking the work gets. Its size follows the work: on the baseline's
small items a few lines carried what the old plan documents carried.

### `critique`: read the plan before code exists

**Goal.** Find what the plan gets wrong while it is cheap to fix: a wrong
premise about the code, a case the tests will miss, scope that does not
serve the request, loop counts too low for the stakes.

**Entry.** `plan.written`.

**Exit evidence.** `critique.decided`: the plan digest it read, the
verdict, the findings, any raised counts, the model and cost. `sound` goes
to `build`. `revise` goes back to `plan` while critique rounds remain, and
otherwise to `build` with the findings in the prompt.

**Why.** Mission item 1. Incident: on popoto #633 the clarify arm built on
a wrong premise nothing read before code existed, and correctness fell from
5 to 2 (rebuild-baseline.md, pop-a). Counter-evidence: on the small items
the old critique chain bought nothing measurable, so the count may be 0.
Guard under Tom's 2026-10-01 grant, expiring ninety days from it.

### `build`: the work

**Goal.** A candidate that does what the plan says, with the tests the plan
named, committed in the workspace.

**Entry.** `critique` finished.

**What runs.** Frontier turns, one `claude -p` at a time, resuming the
working session so Valor keeps its context from inspection on. Each turn
gets the Brief rendered from the ledger as it starts, corrections included.
Effects beyond the workspace are requests to the broker.

**Exit evidence.**

- `candidate`: the turn wrote `.valor/done.md` saying what it delivered,
  how it verified it, and which decisions Tom might want to change,
  recorded on `turn.collected` with the head commit. Goes to `test`.
- `asked`: the turn wrote `.valor/question.md`. Goes to `waiting`.
- `idle`: two consecutive turns ended with neither. The run returns so Tom
  can look. **Exists in the kernel** (`IDLE_TURNS = 2`), because pso-a's
  bare run ended 2 of 3 turns idle (rebuild-baseline.md, Caveats).
- `failed`: the harness reported an error or the turn did not finish. The
  run returns; the next run retries from the ledger.

**Ledgered.** `turn.started`, every gateway reservation and charge,
`turn.ended` (with the harness session id), `turn.collected`, effect rows.

**Why.** Mission items 1 and 2 (the demonstration's delivery was under half
the reference's size, rebuild-demonstration.md, What was delivered).

### `test`: the suite at head and base

**Goal.** Nothing that passed before the change fails after it.

**Entry.** A candidate from `build` or `patch`.

**What runs.** The app's relevant suite in the workspace at the candidate's
head and at the task's base commit. Deterministic.

**Exit evidence.** `test.run`: both commits, the command, the failures at
head that do not fail at base. None is `green`, to `breadth`. Any is `red`,
to `patch` once per pass, then to Tom.

**Why.** Mission item 1 ("testing actual use"). On popoto #633 the clarify
arm broke a bound in an existing test and was accepted anyway, with one new
broad-suite failure (rebuild-baseline.md, Test breadth).

**Gap.** The suite needs the app's environment in the workspace; the
demonstration's workspace had none (rebuild-demonstration.md, What was
delivered). Provisioning is in [architecture.md](architecture.md).

### `breadth`: are the tests broad enough

**Goal.** Every behavior the diff changes is exercised by a test.

**What runs.** One judgement call (use shape 8 in
[judgement-layer.md](judgement-layer.md)) over the diff and the tests the
candidate added or changed. It lists behaviors no test exercises: records
in states other than the obvious one, every member of an enumeration the
code branches on, existing tests whose bounds encode the old behavior.

**Exit evidence.** `breadth.checked`: the head commit, the listed
behaviors, the call's model, confidence, and cost, the verdict, the guard
id. `broad` goes to `review`; `gaps` to `patch` once per pass, then to
`review` with the list attached.

**Guard record.** Granted by Tom's lean-SDLC decision of 2026-10-01,
expiring ninety days from it. Mission item 1. Incident: every replay wrote
fewer tests than its reference and the hidden tests found what that missed.
psyoptimal #872 missed the archived-team guards; popoto #191 missed the
list key name and hash exclusion; the demonstration wrote 10 tests against
the reference's 35 and left archived teams and per-sector rules untested
(rebuild-baseline.md, Test breadth; rebuild-demonstration.md, What was
delivered).

### `review`: one blind verification per pass

**Goal.** An independent verdict on whether the candidate does what was
asked, correctly, and adds no ungranted governance.

**What runs.** The blind verifier, specified in
[architecture.md](architecture.md). It runs on the same Opus model as the
builder in a fresh session, or on an Opus-class model from another vendor
through another harness; never on a cheaper class. It never sees the
executor's narration: it reads the request, Tom's answers and feedback, the
plan, the diff, the test and breadth rows, and the docs the diff touches as
the contract. It reruns the checks itself before any judgement, and asks
the governance boolean over the diff.

**Exit evidence.** `review.decided`: the head commit, the verdict, the
findings (each with a kind, `debt` among them), the governance boolean and
its answer, the verifier's model and cost.

- `pass` goes to `docs`.
- `changes` goes to `patch` while review rounds remain, and otherwise to
  Tom in `merge` as a delivery that did not pass.
- `governance_refused`: the diff adds a check, gate, hook, round, or review
  step without a grant. It goes to Tom in `merge` with the hunk named; his
  grant, one tap per instance, sends it back to `review`, and his feedback
  sends it to `patch` to take it out.

**Why.** Mission item 1, the Evidence item "Independent checks", and the
constraint "Docs describe reality". One review is a constraint of the
setup plan and carries no expiry; review rounds beyond it are guards under
Tom's 2026-10-01 grant. Incident for the reviewer's strength: the Sonnet
stand-in accepted popoto #191 bare at fidelity 1 and #633 clarify with the
stale-cache bug moved, not removed (rebuild-baseline.md, Review rounds).
The one review in the old record that mattered was Tom's on #633, which
found a subtle state bug.

### `patch`: the builder resolves the findings

**Goal.** Every finding sent back is resolved or answered with a reason,
and the result is a new candidate.

**Entry.** `test` said `red`, `breadth` said `gaps`, `review` said
`changes`, or Tom gave feedback in `merge` or `merged`.

**What runs.** The working session, resumed, with the findings or the
feedback as the prompt. Never a new agent: the session that built carries
why each line is there, and a send-back costs one more turn of it. When the
session nears the model's context limit it is compacted in place and
resumed ([harnesses.md](harnesses.md)).

**Exit evidence.** Same as `build`: `candidate` goes to `test` for a new
pass; `asked`, `idle`, and `failed` as in `build`.

**Why.** Tom's decision of 2026-10-01. The demonstration's two send-backs
were one resumed turn each, at $0.89 and $0.85 (rebuild-demonstration.md,
Money). Feedback goes to the same task (Mission item 1).

### `docs`: the docs still describe reality

**Goal.** No doc says something the change made untrue.

**Entry.** `review` said `pass`.

**What runs.** The working session, resumed, which knows what changed.
Docs commits after the reviewed head may touch only doc paths: Markdown
files and the paths the plan names as docs.

**Exit evidence.** `docs.updated`: the reviewed head, the docs commits
after it, their paths, and the governance boolean over their diff (a doc
can add a rule). `no_change` records that no doc needed one. A docs commit
that touches a path outside the doc paths is not a docs commit: the kernel
sends the task to `test` for a new pass.

**Why.** The constraint "Docs describe reality": review reads docs as the
contract, so an untrue doc misleads the next review.

### `merge`: shown to Tom, merge held for his tap

**Goal.** Tom sees the result and decides; nothing irreversible happens
without his tap.

**Entry.** `docs` finished, or a candidate that did not pass is shown as
such.

**What runs.** `task.delivered` with the delivery summary reaches Tom
through a bridge: what was delivered, how it was verified, the test,
breadth, and review verdicts, the tech-debt additions, and the decisions
Valor made that Tom may want to change, the one that most changes the
outcome first. The merge, and every other `act` effect the task requested,
sits held in the broker.

**Exit evidence.** `released`: `approval.granted` bound to the effect's
payload digest, carrying Tom's literal message, then `effect.intent` and
`effect.outcome`. One tap, one effect. `feedback`: `feedback.given`, to
`patch`.

**The merge predicate.** The broker performs a merge only when all of
these are facts in the task's ledger:

1. a `review.decided` row with verdict `pass` for a commit `R`;
2. that row's governance boolean answered no, or the Brief carries a
   `governance_grant` and Tom tapped each instance;
3. `test.run` green and `breadth.checked` rows for `R`;
4. a `docs.updated` row whose commits run from `R` to the head being
   merged, touching only doc paths, with its governance boolean answered
   no or granted;
5. an unused `approval.granted` bound to this merge effect's digest.

Each term is deterministic: a row exists or it does not. No model call
decides whether a merge may happen. After the release the task is
`merged`; a defect found in use comes back as `feedback.given` on the same
task and goes to `patch` (Mission item 1, "resolving discovered defects").

**Why.** Mission items 1 and 6; `act` needs Tom per action [11]. The
demonstration's three pushes went requested, held, approved, released,
performed; delivery 1 left out the decision that mattered most
(rebuild-demonstration.md, Attention log), hence the order rule. **Exists
in the kernel:** held `act` effects, approve, release, one-time approval.

### `stopped`

**Entry.** `task.stopped` from any state, written by `python -m core stop`.

**What runs.** Nothing. The row is the fence: the gateway refuses every
later model call and the broker every later effect by reading it, whether
or not the running process hears the notification. A stopped task takes no
answer and no feedback.

**Why.** Stop is immediate and lossless; the off switch lives in the
kernel, not in the model's incentives [8, 9, 10]. The demonstration's
failed resume failed cleanly, metered $0, and lost nothing
(rebuild-demonstration.md, Kernel findings, 3). **Exists in the kernel.**

## Budget exhaustion

Not a state. Every model call in every state is metered by the gateway
against the task's one money budget. When the remaining budget cannot
cover a call, the gateway refuses it, the run returns "budget exhausted",
and the task keeps its state with a question to Tom asking for more.
Spending more is Tom's choice. **Gap:** the kernel has no command to raise
a task's budget.

The budget meters what passes the gateway. A turn that deliberately called
the provider directly with the machine's Claude login would spend outside
it. Tom accepted that on 2026-10-01: budgets are for visibility and honest
metering, not a hard wall (see [architecture.md](architecture.md), Limits).

## Attention in the ledger

Mission item 6 makes attention a ledger item. Every point where Tom acts on
a task is a row with provenance:

| Row | State that produces it | Provenance |
|---|---|---|
| `question.asked`, `question.answered` | `clarify`, `plan`, `build`, `patch`, `waiting` | `by`, `via`, `at`, `role_played` |
| `feedback.given` | `merge`, `merged` | `by`, `via`, `at`, `role_played` |
| `approval.granted` | `merge` | `by`, Tom's literal message |
| a guard grant | before a guarded state runs, or a governance-adding merge | Tom's literal message, incident, mission item, expiry |

Interruptions are counted and shown on the delivery and never block: the
kernel never refuses a question for crossing the attention budget (Tom,
2026-10-01). **Gap.** `approval.granted` records `by` and Tom's message but
not `role_played`; in the demonstration two of three pushes were approved
under Tom's standing permission rather than a live tap
(rebuild-demonstration.md, Where Tom acted as project manager).

The attention log's format and its "changed the outcome, changed the
authority" labels are specified in [mission.md](mission.md).

## Open for Tom

1. **Low-confidence judge calls.** This doc routes them to `clarify`, which
   costs Tom nothing when inspection finds no material question. The
   alternative is routing them to `plan`, which the baseline's tie on
   precise requests also supports.
2. **Feedback through the judge.** Feedback goes straight to `patch`. Thin
   feedback ("closer, but not right") could be judged like a request.
   Nothing in the evidence shows thin feedback yet.
