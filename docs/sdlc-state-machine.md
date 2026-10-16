# SDLC state machine

How a request becomes a merged result: the checkpoints a task passes through, the verdict each one
ends with, the transitions those verdicts select, and what the ledger records at each step. The
state machine is kernel code in `core/`. The work done inside a state is a skill; the kernel decides
which state a task is in, which state comes next, and what may leave the machine.

This doc serves the acceptance question in [mission.md](mission.md): Tom gives Valor an imperfectly
specified goal and returns to something that works and needed less of his attention than doing it
himself.

## The pipeline Tom set

Tom's decisions of 2026-10-01 fix the shape:

- **Every stage is a checkpoint.** Intake, the request judgement and clarify step, plan, critique,
  build, test, the test-breadth check, review, patch, docs, and merge. Each leaves a ledger row the
  next stage and the merge predicate read.
- **Each stage states a goal and its exit evidence, not steps.** A stage's skill says what must be
  true when the stage ends and what row proves it. How to get there is the agent's call.
- **The plan sets the loops.** The plan decides how many times critique may send the plan back (0,
  1, or 2) and whether the checks may send the work to patch (0 or 1), from the stakes. The replay
  baseline covered six small changes and no large high-stakes one (rebuild-baseline.md), so the
  count is a call per task, not a constant.
- **Test, review, and docs run in parallel.** After every build and every patch, the three check the
  same candidate commit, each in a fresh session, and their verdicts join before the task moves
  ("docs and test and pr review can always run in parallel").
- **Leave it cleaner than we found it.** The plan and the review may pull in scope that pays down
  known tech debt related to the change.
- **Patch is the builder.** The session that built resumes to patch, compacted if it needs to be. A
  patch never starts a new agent.

That decision is the grant for every checkpoint here that holds or redirects work (the judge, the
breadth check, critique and review loops); each is ledgered as a guard with its incident, mission
item, and a ninety-day expiry.

## The model in one table

| State | Goal | Runs | Exit verdicts | Next |
|---|---|---|---|---|
| intake | the request recorded once, verbatim, with its source | kernel | (writes `task.started`) | `judge` |
| `judge` | decide whether the request is thin before anyone builds | judgement tier | `precise`, `thin` | `plan`, `clarify` |
| `clarify` | ask only what inspection cannot settle | working session | `asked`, `no_material_question`, `ended` | `waiting`, `plan`, `ended` |
| `waiting` | Tom answers | nothing | `answered` | the state that asked |
| `plan` | an approach, its stakes, its loop counts, its scope | working session | `planned`, `asked`, `ended` | `critique`, `waiting`, `ended` |
| `critique` | find what the plan gets wrong before code exists | fresh session | `sound`, `revise` | `build`, `plan` or `build` |
| `build` | a candidate that does what the plan says | working session | `candidate`, `asked`, `ended`, `idle`, `failed` | `checks`, `waiting`, `ended` |
| `checks` | test, review, and docs agree on one candidate | three branches at once (see `checks` below) | the join | `merge`, `patch`, or `ended` |
| `checks.test` | the suite passes at head where it passed at base, and the tests exercise what the diff changes | deterministic suite, then judgement tier | `pass`, `red`, `gaps` | the join |
| `checks.review` | an independent verdict on the candidate | verifier, fresh session | `pass`, `changes` | the join |
| `checks.docs` | no doc says something the candidate made untrue | fresh session, doc paths only | `updated`, `no_change`, `changes` | the join |
| `patch` | every finding from the join resolved, or the work stopped | working session, resumed | `candidate`, `asked`, `ended`, `idle`, `failed` | `checks`, `waiting`, `ended` |
| `merge` | the delivery merged | kernel and broker | `merged`, `feedback`, `rebase` | `merged`, `patch`, `checks`, `ended` |
| `merged` | the change is on the target branch | nothing | `feedback` | `patch` |
| `ended` | Valor stopped the work, or finished it with nothing to merge; Tom has a report | nothing | `feedback` | `patch` |
| `stopped` | nothing more happens | nothing, ever again | | none |

"`plan` or `build`" means the critique count decides (see Loops). The working session is the task's
one harness session: the first frontier turn opens it, and clarify, plan, build, and patch resume
it. Critique, review, and docs each run in a fresh session that never reads it.

Two rules hold across every row, both serving the constraint "reliable stop, recovery, and
correction":

- **State is a fold over the ledger, never a stored field.** Nothing writes a "current state"
  column, so a stop, a crash, or a failed turn leaves nothing to reconcile.
- **State belongs to the task, never to the session executing it.** The harness session a turn
  resumes is recorded on `turn.ended` and is replaceable; losing it loses no state.

## What exists and what is design

The machine is kernel code: `core/machine.py` holds the types, the total fold, the join, and the
merge predicate; `core/router.py` is the control loop; `core/session.py` runs the working session;
`core/verdicts.py` writes verdict rows; `core/guards.py` holds the guards; the broker enforces the
predicate. Postgres refuses a verdict outside its enum (a `CHECK` constraint generated from
`VERDICTS`). A turn's Brief carries the stage file for its state from `skills/sdlc/`.

Runners exist for `judge` (it asks the judgement port), `clarify`, `plan`, `build`, `patch`,
`critique` (a fresh session, `core/fresh.py`, on a workspace the kernel provisioned), `checks.test`
(`core/checks.py`), `checks.review` (`fresh.review_runner`), and `checks.docs`
(`fresh.docs_runner`). Where a stage has no runner the router stops there and says so.

**Tasks from before the machine.** A `task.started` without `sdlc: 1` is legacy and folds read-only
by the old kernel's precedence (stopped; a delivery not reopened by feedback is `merge`; an
unanswered question is `waiting`; feedback after a delivery is `patch`; any turn is `build`; else
`judge`). The router, verdicts, answers, and feedback refuse it; status and stop work.

## Types

`core/machine.py` holds the model as frozen dataclasses over JSONB payloads. `State` names the
twelve states above and `Check` the three branches of `checks`. `VERDICTS` maps each to its enum as
the table lists it; every working-session state also has `idle` and `failed`, since any turn can end
with neither of its stage's signals or fail. `Candidate` is a head commit and the turn that produced
it; `CheckVerdict` a check's verdict and findings on one candidate; `Loops`
the two counts, critique's 0 to 2 and review's 0 or 1 (`REVIEW_MAX`); `Fold` the state with what it
reads. `TRANSITIONS` is the Next column, with three rules beside it: `checks` leaves only through
the join; `asked` from a working-session state goes to `waiting`, and `answered` returns to the
state named on the `question.asked` row; any state but `merged` goes to `ended` on `task.ended`; any
state goes to `stopped` on `task.stopped`, and nothing leaves `stopped`.

A verdict outside its enum is refused when the row is written, so the fold is total: every ledger
prefix maps to exactly one state, and inside `checks` to exactly one verdict or none per branch. The
table and the join are the whole graph. A transition not in them does not happen.

## Loops

Every send-back is counted from the ledger, never from a counter field: the kernel counts the
relevant rows since the last `task.started` or `feedback.given`. Each send-back from the join is one
patch carrying every finding of the three branches together.

- **Critique rounds.** Critique always runs once on a plan. A `revise` goes back to `plan` while
  fewer than `critique_rounds` revisions have happened; after that, or with `critique_rounds` 0, its
  findings ride into `build` as part of the prompt.
- **Patch rounds.** Review runs once on every candidate. A join that did not pass (review `changes`,
  test `red` or `gaps`, or docs `changes`) sends the work to `patch` while fewer than
  `review_rounds` such send-backs have happened. One count covers all three checks.
- **The last round.** Once the plan's rounds are spent, a join with only breadth gaps merges with
  the gaps listed. Any other join that did not pass sends the work to `patch` once more, with every
  finding and the word that the rounds are spent. In that round Valor decides, as the project
  manager, whether to patch once more (`done.md`) or stop (`stop.md`, a note to Tom), optionally
  after asking the advisor. When it stops, or that round's candidate does not pass either, the task
  ends (`ended`, which runs nothing) and Tom gets one report of what was built, what did not pass,
  and why it stopped; it asks for nothing, and feedback by reply opens a new loop window in `patch`.
  The decision is Valor's, not Tom's, under Tom's rulings of 2026-10-03 ("Valor decides who stops
  and who gets more budget") and 2026-10-09.
- **Rebases.** A merge whose push the target rejects because its branch moved is carried when the
  move is clean and disjoint: the kernel, with no turn, replays the verified candidate and its docs
  commits onto the new tip (`git.replay`) when the target's new commits touch no path the candidate
  touches, no file setup installs from (`checks.LOCKFILES`), and no schema file
  (`verdicts.SCHEMA_FILES`). The replayed patch is then the verified one byte for byte, so the
  checks' verdicts carry
  to the replayed sha in one `verdicts.carried` row (the verdicts, from which sha and
  head to which, and the target's tip), and the merge is requested again at once. The merged sha
  then sits on a tip whose other commits were never tested with the candidate; the status page
  shows the carry, naming the sha it was tested at and the sha it merged at. A carry is not a rebase.
  Otherwise the work goes to `patch` to rebase onto the new tip (`rebase.requested`, naming why it
  did not carry). A rebase is mechanical and spends no round, and has no cap: each rejection
  means another merge landed, so the rebases end. A cap of two ended three passing tasks on
  2026-10-16, when four tasks merged within half an hour.

So a task patches on findings at most `review_rounds` plus one times before it merges or ends (the
join table gives every case): work that does not converge stops instead of spending on itself.
Mission item 6, and the setup plan's Why section (53 review-round commits in eleven days).

**Setting the counts.** The plan states the stakes in a sentence and picks both counts: the more a
mistake would cost and the harder it is to undo, the more loops. As guidance, a small reversible
change in well-tested code is a 0, and a change to stored data, money, authentication, migrations,
or the kernel itself gets 2 critique rounds and 1 review round. The kernel refuses a value outside 0
to 2, and reads a review count of 2 as 1: review sends work back at most once. Critique may raise
either count, never lower it, and the kernel uses the higher, so the builder's call on its own
stakes is read by a session that did not make it.

## Scope: leave it cleaner than we found it

The plan may add work that pays down known tech debt related to the change (a workaround the change
makes removable, a duplicate it touches, a test that encodes behavior it replaces), each listed in
the plan's scope with the debt it pays. Review may do the same with a finding of kind `debt`, which
goes to `patch` inside the review rounds. The bound is the task's Brief: added scope is metered on
the same task and never raises the effect ceiling, and unrelated debt is a new task, not this one's
scope. Mission items 1 and 5.

## The control loop

`python -m core run TASK` is the router. Each call folds the ledger to the current state, runs the
work that state calls for, records its verdict, and continues until the task settles (`waiting`,
`merged`, `ended`, or `stopped`), waits in `merge`, a step fails, or it reaches a stage with no
runner, then prints one status line and returns. The router reads verdicts and follows the table; it
never writes a verdict and never decides authority. One run per task at a time: a run holds a
session advisory lock on the task for its whole run, a second returns `already running`, and one
whose lock connection died returns `lock lost` before its next turn or write.

| State | Tier | Why that tier |
|---|---|---|
| `judge`, the breadth call in `checks.test` | judgement (Jev, open-weight fallback behind the port) | a classification, well under $0.05 per call (rebuild-demonstration.md, Money) |
| `clarify`, `plan`, `build`, `patch` | frontier agent in the working session | the hard work, in one context from inspection to delivery |
| `critique`, `checks.docs` | frontier agent in a fresh session | a reading by a session that did not write the work |
| the suite in `checks.test` | deterministic suite run | running tests decides nothing by judgement |
| `checks.review` | verifier, Opus-class (see [architecture.md](architecture.md)) | an independent check, never the executor grading itself [4, 16] |
| `merge` | kernel and broker | authority |

The judgement port is [judgement-layer.md](judgement-layer.md)'s; session resume and compaction are
[harnesses.md](harnesses.md)'s.

## States

Each state gives its goal, its exit evidence, and why it exists.

### intake: the request, once

**Exit evidence.** `task.started`, carrying the request verbatim, its source (a message id, an
issue, a card), and the Brief; without an issue, the task record is the issue. Every later
checkpoint reads the request as written, never a paraphrase (Mission item 1).

### `judge`: read the request before any build

**Goal.** Route a request whose intent lives only in Tom's head to `clarify`, and let a precise one
through untouched.

**What it asks.** One judgement-tier call over the request, the thread (empty until the bridges),
and the project, against three cues: a one-line ask whose intent lives only in Tom's head; an ask
leaning on an example that may stand for a wider requirement; an ask naming existing UI or behavior
without saying what happens to it. A code summary as input is a gap until it is measured.

**Exit evidence.** `judgement.answered` (or `judgement.failed`) at site `intake.underspecified`,
read by the kernel into the one `judge.decided` (`leg: judgement`, the `judgement_id`, P(precise),
model, cost, guard id when thin). Before it, the same step asks `intake.overlap` once over the
request, the project, and the project's open tasks, and records `reconcile.decided` (the decision,
the open task it names, a proposed placement, the judgement id, `shadow: true`). The row moves no
state, and while the site is uncalibrated the judge decides as it would without it; a retried step
reuses that row and asks nothing again. The writer labels it with `python -m core shape`
(`shape.confirmed`) and places the task in the queue (`task.placed`); all three rows are quiet, so
none steps the task. `precise` goes to `plan`; anything short of the floor, including
both legs failing, is `thin`, to `clarify`: a wrong `thin` costs one clarify turn, a wrong `precise`
costs review rounds.

**Guard record.** Granted by Tom on 2026-10-01; mission items 3 and 6.
Incidents: psyoptimal #894 (task `32f800bce8a2`: two feedback rounds for
three decisions one message would have settled), popoto #191 (bare
fidelity 1, 3 of 11 hidden tests; clarify 3 and 7 of 11), and #188 (bare
built a feature Tom did not want). Counter-evidence: on #872, #646, and
#893 clarifying changed nothing, and on #633 a wrong-premise question made
it worse (rebuild-baseline.md, Clarify). Expiry 2026-12-30. **Gap.** n = 1
per item and arm; precision and recall as a Brier score with n [12] are not yet measured.

### `clarify`: inspect, then ask or proceed

**Goal.** Every question whose answer changes what gets built, or the authority it needs, reaches
Tom in one message; nothing the code can settle is asked.

**Entry.** `judge` said `thin`. The turn opens the working session and changes no file.

**Exit evidence.** `question.asked` with the numbered questions, the answer Valor will assume for
each, and the intended approach in a few lines (verdict `asked`, to `waiting`); or a statement on
`turn.collected` that no question would materially change the result (`no_material_question`, to
`plan`, with Tom spending no attention).

**Why.** Mission items 3 and 6; asking under uncertainty follows [3]. On
#872 the clarify message raised both questions the old system had put to
Tom (rebuild-baseline.md). The turn shows `no_material_question` by
writing `.valor/no_question.md` (`skills/sdlc/clarify.md`).

**A chat session.** A team chat's session (`Brief.chat`) asks no judgement: the judge writes
`judge.decided` `thin` with `leg: chat`, and it stays in clarify for its life, one turn per wake,
resumed in the same harness session. Only `stop.md` counts there (`ended`); it answers by sends
and starts work as tasks of its own, which report to it
([bridges/telegram.md](bridges/telegram.md), Team chats).

### `waiting`: Tom has a question

**Goal.** Tom's answer lands in the context that asked, named on the `question.asked` row.

**Exit evidence.** `question.answered` with Tom's text and provenance (`by`, `via`, `at`,
`role_played`). The next run resumes the working session with the answer as its prompt, in the state
that asked. An answer is spent only by a turn that finishes; after a failed or stopped turn the next
turn opens with it again.

**Why.** Mission item 6. Provenance exists because demonstration rows 177 and 207 both read `"by":
"tom"` though 207 was role-played (rebuild-demonstration.md, Kernel findings, 5). **Exists in the
kernel.**

### `plan`: the approach, the stakes, the loops

**Goal.** A short plan the builder, the critic, and the reviewer can all hold the work against: what
will be built and how, what is out of scope, the stakes in one sentence, `critique_rounds` and
`review_rounds`, the tech-debt additions with the debt each pays, and the tests that will show it
works, including the cases beyond the obvious one.

**Exit evidence.** The plan as a file in the workspace, committed, and `plan.written`: its commit,
its digest, the stakes sentence, both counts, and the scope additions. A revision writes a new
`plan.written`. A material question found while planning is `asked`.

**Why.** Mission items 1 and 3. Its size follows the work: on the baseline's small items a few lines
carried what the old plan documents did.

### `critique`: read the plan before code exists

**Goal.** Find what the plan gets wrong while it is cheap to fix: a wrong premise about the code, a
case the tests will miss, scope that does not serve the request, loop counts too low for the stakes.

**Exit evidence.** `critique.decided`: the plan digest it read, the verdict, the findings, any
raised counts, the model and cost. `sound` goes to `build`. `revise` goes back to `plan` while
critique rounds remain, else to `build` with the findings in the prompt.

**Why.** Mission item 1. Incident: on popoto #633 the clarify arm built on a wrong premise nothing
read before code existed, and correctness fell from 5 to 2 (rebuild-baseline.md, pop-a).
Counter-evidence: on the small items the old critique chain bought nothing measurable, so the count
may be 0. Guard under Tom's 2026-10-01 grant, expiring ninety days from it.

### `build`: the work

**Goal.** A candidate that does what the plan says, with the tests the plan named, committed in the
workspace.

**What runs.** Frontier turns, resuming the working session so Valor
keeps its context from inspection on. Each turn gets the Brief rendered from the ledger as it
starts, corrections included. Effects beyond the workspace are requests to the broker.

**Exit evidence.**

- `candidate`: the turn wrote `.valor/done.md` saying what it delivered, how it verified it, and
  which decisions Tom might want to change, recorded on `turn.collected` with the head commit. Goes
  to `checks`.
- `asked`: the turn wrote `.valor/question.md`. Goes to `waiting`.
- `ended`: the turn wrote `.valor/stop.md` and no other signal, in any working state: recorded as
  `task.ended` (`kind: decided`, its text the note to Tom). A note opening `Done:` is work finished
  with nothing to merge (an effect performed), and Tom's report says so; any other is work stopped.
- `idle`: the turn finished with none of these. The task stays in `build` and the step ends, so the
  kernel can give the turn slot to any other ready task (oldest latest row first) before the task's
  next turn, resumed with the prompt `Continue.` and what made any signal not count. There is no
  bound on idle turns (Tom, 2026-10-03, `idle_turns` removed). An `idle` turn that left
  `.valor/advice.md` goes on in the same step: it first gets the advisor's answer (`advice.given`),
  quoted in the next turn.
- `failed`: the harness reported an error or the turn did not finish. The step writes `step.failed`
  (the state, the check, the reason, the turn) and the run returns. The resident kernel steps the
  task again after a delay that doubles from a minute (`router.retry_due`), the next turn opening
  with the reason; at four failures in a row of one state and check it writes `task.ended`.

**Ledgered.** `turn.started`, every gateway opening and charge, `turn.ended` (with the harness
session id), `turn.collected`, effect rows. Mission items 1 and 2.

### `checks`: test, review, and docs on one candidate

**Entry.** A `candidate` from `build` or `patch`: the head SHA and the turn that produced it,
recorded on `turn.collected`. Every branch is keyed by it.

**The fork.** The kernel starts the three branches against the candidate. None reads another's
output, so none waits on another and the order they finish in changes nothing. Each ends with one
verdict row keyed by the candidate: `test.decided`, `review.decided`, `docs.decided`. A branch whose
turn fails or is stopped leaves no verdict, and the next run starts that branch again, and only that
one; a failure also leaves `step.failed` naming the branch and why.

**Stale verdicts.** A branch's verdict is its latest row keyed by the current candidate, the one on
the latest `candidate` verdict. A row keyed by an older candidate is stale: it stays in the ledger
with its spend, and the join and the merge predicate ignore it. A later row for the same branch and
candidate supersedes an earlier one. Keying by the producing turn as well
as the SHA means a patch that answers findings with reasons and no code change still gets fresh
checks.

**Scheduling.** The three run concurrently when the machine has the slots, and back to back when it
does not; the join waits for all three either way. On the 16 GB Air, `max_turns` is 2, the suite and the two agent
turns each hold a slot, so two run at once and the third waits ([machine.md](machine.md),
Concurrency), with judgement calls beside them.

#### `checks.test`: the suite, then breadth

The suite at base and at head, then breadth: see [sdlc-checks-test.md](sdlc-checks-test.md).

#### `checks.review`: one verification per candidate

**Goal.** An independent verdict on whether the candidate does what was asked, correctly, and adds
no governance (CLAUDE.md).

**What runs.** The registered runner is `fresh.review_runner`, the verifier
([architecture.md](architecture.md)). The kernel sets up a git worktree of the candidate with fresh
services and runs one fresh session there, which may run the suite: the builder's Opus model, or an
Opus-class model from another vendor through another harness, never a cheaper class. It reads the
request, Tom's answers and feedback, the plan, the diff, and its setup's exit, never the executor's
narration, reruns any test itself, reads the diff for governance, and answers `pass` or `changes` as
its final message (the object bare, or the fenced block that ends the message, prose before it
ignored), which the kernel reads from the turn's result, not from a file in the checkout. The spec's
setup runs in that worktree under the turn profile.

**Exit evidence.** `review.decided`: the candidate, the verdict and the reviewer's own, the findings
(each with a kind, `debt` among them), the kernel's run, the predicted failure, each requirement met
or not, the verifier's model and cost. A `changes` that names added governance goes to `patch` like
any other finding.

**Why.** Mission item 1 and the Evidence item "Independent checks". One review is a constraint of
the setup plan with no expiry. Sending work back is the guard `review.loop`, on popoto #633: the
stale-cache bug was moved, not removed, and the Sonnet stand-in accepted it, as it accepted #191
bare at fidelity 1 (rebuild-baseline.md, Review rounds), hence the Opus class.

#### `checks.docs`: the docs still describe reality

**Goal.** No doc says something the candidate made untrue.

**What runs.** A fresh session in its own clone of the candidate, cloned from the kernel mirror,
never the builder's clone. It reads the request, the plan, the diff, and after a send-back the
previous docs commits as a patch, and changes only Markdown that instructs no turn: never a
`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, or `AGENTS.override.md`, nor anything under `skills/`,
`persona/`, or `.claude/`, in any letter case, since this Mac's file system ignores case
(`machine.is_doc_path`). No plan widens this, so no code rides in an unreviewed docs commit.

**Exit evidence.** The session writes a verdict, its findings, and its head: a commit id or an
abbreviation of one. The kernel resolves an abbreviation in the clone, fetches the commit into the
mirror under the turn profile, and keeps the longest prefix of commits on the candidate that touch
doc paths alone, with regular file modes (`100644`, `100755`, read from `git diff-tree -r`); a
merge, a commit off the candidate, or a head naming no single commit keeps
nothing. It records `docs.kept` with the full id and `docs.decided`: the candidate, the kept head,
the dropped commits and their paths, and the verdict it computes. This cut is the one place docs commits
are read; the verdict and the merge take its head as kept. `updated` or `no_change` passes, at the
kept head: a dropped commit is a `dropped` finding, noted on the delivery, and the merge goes on at
what was kept. `changes` means a doc states something the code should still honor and the candidate
breaks it, or a doc cannot be made true without a code change. Docs commits belong to their candidate:
after a send-back they are not merged, and the next docs session reads them as
`previous-docs.patch`, keeping what holds.

**Why.** "Docs describe reality": review reads docs as the contract.

### The join

When all three verdicts exist for the current candidate, the kernel reads them together, with the
loop counts (see Loops), and moves the task once:

| Review | Test and docs | Rounds | Goes to |
|---|---|---|---|
| `pass` | test `pass`; docs `updated` or `no_change` | | `merge`, as a delivery that passed |
| anything else | anything | a plan's round left | `patch`, with every finding |
| `pass` | test `gaps`, docs passing | none left | `merge`, with the gaps listed |
| anything else | anything | the last round unspent | `patch`, with every finding, to patch or stop |
| anything else | anything | the last round spent | `ended`, with a report to Tom |

The rows are read in order and the first match decides. No row records the join. The
verdict that completes a join to `merge` or `ended` writes `task.delivered` with it, and the kernel
requests the merge for a delivery that passed (or passed with gaps); the router requests it again on
any run that finds the task in `merge` without one, so a crash between the two strands nothing. The findings the join passes to
`patch` are the next turn's prompt.

### `patch`: the builder resolves the findings

**Goal.** Every finding the join sent back is resolved or answered with a reason, and the result is
a new candidate.

**Entry.** The join sent work back, a merge's target moved (`rebase.requested`), or Tom gave
feedback in `merge`, `merged`, or `ended`.

**What runs.** The working session, resumed, with all of the join's findings or Tom's feedback as
one prompt. Never a new agent: the session that built carries why each line is there, and a
send-back costs one more turn of it. When the session nears the model's context limit it is
compacted in place and resumed ([harnesses.md](harnesses.md)). After a merge, before each turn the
kernel fast-forwards the work branch to the merged head (the candidate and the docs check's commits,
kept in its mirror), through the bare origin, under the turn's profile (`session.merged_into_work`),
so the round's merge lands on top of the first; when it cannot, the prompt names the head and why.

**Exit evidence.** Same as `build`: `candidate` goes to `checks`, all three branches again on the
new candidate; `asked`, `ended`, `idle`, and `failed` as in `build`.

**Why.** Tom's decision of 2026-10-01; the demonstration's two send-backs were one resumed turn
each, $0.89 and $0.85 (rebuild-demonstration.md, Money).

### `merge`: merged at request

**Goal.** A delivery that passed lands on the target branch, and Tom hears what landed.

**Entry.** The join sent the task here, as a delivery that passed (or passed with gaps).

**What runs.** The kernel writes `task.delivered` with the delivery summary: what was delivered, how
it was verified, the test, review, and docs verdicts with any breadth gaps, the tech-debt additions,
and the decisions Valor made that Tom may want to change, the one that most changes the outcome
first. It then requests the merge, and the broker performs it at request when the predicate below
holds. A done merge owes Tom a `report` notice of what shipped: the note the delivering turn wrote
above the `---` line of `done.md`, led by the request's first line; a reply to it is
`feedback.given`.

**Exit evidence.** `merged`: `effect.intent` and `effect.outcome` `done` for the merge. `feedback`:
`feedback.given`, to `patch`. `verdicts.carried`: the target moved disjointly, and the merge
is requested again for the replayed candidate, staying in `merge` (see Loops). `rebase`: the target
moved and the candidate did not carry, to `patch`. `ended`: the merge cannot land.

**The merge predicate.** The broker performs a merge only when all of these are facts in the task's
ledger:

1. one candidate `C`, the current one, with a `test.decided`, a
   `review.decided`, and a `docs.decided` row each keyed by `C`, the merge naming `C` and the docs
   head (or `C` when docs kept none); no verdict for any other candidate counts;
2. the test verdict `pass`, or `gaps` with the plan's rounds spent and the
   gaps on the delivery; never `red`;
3. the review verdict `pass`.

The docs verdict is no term: docs run, and the merge lands the head they kept, or `C` when they kept
none. What that head holds was read once, when the docs runner cut the docs commits to the doc-path
prefix it keeps (`fresh.kept_prefix`: doc paths only, `git diff-tree --no-renames` so a rename out of
a code path counts the old path, regular file modes, no merge commit, and descending from `C`).

A `verdicts.carried` row makes the replayed candidate `C`: the fold rekeys the three verdicts to it
and the docs head to the replayed head, so the predicate reads the carried verdicts for the replayed
sha and none for the one it replaced.

Each term is deterministic: a row exists, or not. No model call decides whether a merge may happen.
The broker checks all three and writes the merge's intent in one transaction under the task's lock. A
term that does not hold is an `effect.refused` naming it. A merge that does not land is acted on by
why (`verdicts.ensure_merge`): a failure is tried again after a delay doubling from a minute, and
the fifth ends the task; a push the moved target rejected is a carry or a rebase; a refusal ends the
task; a missing or refused push credential, which only Tom holds, sends him one request for it per
candidate and is tried every ten minutes. Each try is its own request
(`merge:<digest>:<grants>:<tries>`). A merge whose performing process died leaves an intent with no
outcome; a later run settles it from the target once it answers (`broker.reconcile`).

**Where a merge goes.** At start the kernel records origin's push URL (as an absolute path) and the
target branch (the flag, or the branch origin's `HEAD` names). The merge's payload carries both with
the head and the candidate, so the request binds them, and the push goes to that URL whatever the
workspace's config says later; the kernel's own merge runs git with `core.hooksPath` off (`core/git.py`, `PINNED`). For a task the kernel provisioned, the
origin is the project spec's `merge_url`, else its own bare one, and the target branch the spec's;
any branch may be the target. The docs cut and the merge's push read the kernel mirror, which no
turn writes. A turn cannot request a merge, and its `push_branch` cannot target the target branch.

After the merge the task is `merged`; a defect found in use comes back as
`feedback.given` on the same task and goes to `patch` (Mission item 1, "resolving discovered defects").
After a merge, `python -m core status` reads what came after it
(`core/outcomes.py`): the feedback given after it and before the next
delivery, the `delivery.used` marks, the later merges of other tasks to the
same url and branch that changed the same paths, and whether the head is
still on its branch and which later commits revert it, read from the
kernel's cache of the target as last fetched. None of it moves the
fold or decides anything.

**Why.** Mission items 1 and 6; `act` runs inside the ceiling Tom set. Delivery 1 of the
demonstration left out the decision that mattered most (rebuild-demonstration.md, Attention log),
hence the order rule. **Exists in the kernel:** the merge at request, its report, and the predicate.

### `stopped`

**Entry.** `task.stopped` from any state, written by `python -m core stop`, which writes it on the
task and on every descendant not already stopped or merged (architecture.md, Stop).

**What runs.** Nothing. The row is the fence: the gateway refuses every later model call and the
broker every later effect by reading it, on the task or on any ancestor. A stopped task takes no
answer and no feedback, and a task under a stopped ancestor takes no feedback. The off switch lives
in the kernel, not in the model's incentives [8, 9, 10]; the demonstration's failed resume failed
cleanly and metered $0 (rebuild-demonstration.md, Kernel findings, 3). **Exists in the kernel.**

## Spending and attention

Metered spending and the attention rows Tom's actions leave are in
[spending-and-attention.md](spending-and-attention.md).
