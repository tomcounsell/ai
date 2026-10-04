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
- **Test, review, and docs run in parallel.** After every build and every
  patch, the three check the same candidate commit, each in a fresh
  session, and their verdicts join before the task moves ("docs and test
  and pr review can always run in parallel").
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
| `build` | a candidate that does what the plan says | working session | `candidate`, `asked`, `idle`, `failed` | `checks`, `waiting`, Tom |
| `checks` | test, review, and docs agree on one candidate | three branches at once (see `checks` below) | the join | `merge` or `patch` |
| `checks.test` | the suite passes at head where it passed at base, and the tests exercise what the diff changes | deterministic suite, then judgement tier | `pass`, `red`, `gaps` | the join |
| `checks.review` | an independent verdict on the candidate | blind verifier, fresh session | `pass`, `changes`, `governance_refused` | the join |
| `checks.docs` | no doc says something the candidate made untrue | fresh session, doc paths only | `updated`, `no_change`, `changes` | the join |
| `patch` | every finding from the join resolved | working session, resumed | `candidate`, `asked`, `idle`, `failed` | `checks`, `waiting`, Tom |
| `merge` | the delivery shown, the merge held for Tom's tap | kernel, broker, Tom | `released`, `feedback`, `governance_granted` | `merged`, `patch`, `checks` |
| `merged` | the change is on the target branch | nothing | `feedback` | `patch` |
| `stopped` | nothing more happens | nothing, ever again | | none |

"`plan` or `build`" means the critique count decides (see Loops). The
working session is the task's one harness session: the first frontier turn
opens it, and clarify, plan, build, and patch resume it. Critique, review,
and docs each run in a fresh session that never reads it.

Two rules hold across every row, both serving the constraint "reliable
stop, recovery, and correction":

- **State is a fold over the ledger, never a stored field.** Nothing
  writes a "current state" column, so a stop, a crash, or a failed turn
  leaves nothing to reconcile.
- **State belongs to the task, never to the session executing it.** The
  harness session a turn resumes is recorded on `turn.ended` and is
  replaceable; losing it loses no state.

## What exists and what is design

The machine is kernel code: `core/machine.py` holds the types, the total
fold, the join, and the merge predicate; `core/router.py` is the control
loop; `core/session.py` runs the working session; `core/verdicts.py` writes
verdict rows; `core/guards.py` holds the guards; the broker enforces the
predicate. Postgres refuses a verdict outside its enum (a `CHECK`
constraint generated from `VERDICTS`). A turn's Brief carries the stage
file for its state from `skills/sdlc/`.

Runners exist for `judge` (it asks the judgement port), `clarify`, `plan`,
`build`, `patch`, `critique` (a fresh session, `core/fresh.py`, on a
workspace the kernel provisioned), `checks.test` (`core/checks.py`), and `checks.review`
(`fresh.review_runner`). `checks.docs` has none: the docs runner (`fresh.docs_runner`) is registered once governance's judgement has a calibration record on the real ledger that passes its entry check. Where a
stage has no runner the router stops there and says so, and a person records the verdict with `python -m core verdict TASK
STAGE VERDICT` (`leg: manual`, with provenance), which refuses a stage that
has a runner.

**Tasks from before the machine.** A `task.started` without `sdlc: 1` is
legacy and folds read-only by the old kernel's precedence (stopped; a
delivery not reopened by feedback is `merge`; an unanswered question is
`waiting`; feedback after a delivery is `patch`; any turn is `build`; else
`judge`). The router, verdicts, answers, feedback, and grants refuse it;
status, stop, approve, and release work.

## Types

`core/machine.py` holds the model as frozen dataclasses over JSONB
payloads. `State` names the eleven states above and `Check` the three
branches of `checks`. `VERDICTS` maps each to its enum as the table lists
it; every working-session state also has `idle` and `failed`, since any
turn can end with neither of its stage's signals or fail. `Candidate` is a
head commit and the turn that produced it; `CheckVerdict` a check's
verdict, findings, and governance instances on one candidate; `Loops` the
two counts, 0 to 2; `Fold` the state with what it reads. `TRANSITIONS` is
the Next column, with three rules beside it: `checks` leaves only through
the join; `asked` from a working-session state goes to `waiting`, and
`answered` returns to the state named on the `question.asked` row; any
state goes to `stopped` on `task.stopped`, and nothing leaves `stopped`.

A verdict outside its enum is refused when the row is written, so the fold
is total: every ledger prefix maps to exactly one state, and inside
`checks` to exactly one verdict or none per branch. The table and the join
are the whole graph. A transition not in them does not happen.

## Loops

Every send-back is counted from the ledger, never from a counter field:
the kernel counts the relevant rows since the last `task.started` or
`feedback.given`. Each send-back from the join is one patch carrying every
finding of the three branches together.

- **Critique rounds.** Critique always runs once on a plan. A `revise`
  goes back to `plan` while fewer than `critique_rounds` revisions have
  happened; after that, or with `critique_rounds` 0, its findings ride
  into `build` as part of the prompt.
- **Review rounds.** Review runs once on every candidate. A join in which
  review said `changes` sends the work to `patch` while fewer than
  `review_rounds` such send-backs have happened, and counts as one review
  round whatever test and docs said. After that the candidate reaches Tom
  as a delivery that did not pass, with every finding and Valor's
  recommendation.
- **The repair round.** A join in which review passed but test said `red`
  or `gaps`, or docs said `changes`, may send the work to `patch` once, a
  fixed allowance the plan does not set. It spends no review round: the
  reviewer accepted the change; what is left is mechanical.

So a task patches at most `review_rounds` plus one times before Tom sees
it; the join table below gives every case. The counts bound every
path, and they exist so that work that does not converge reaches Tom
with evidence instead of spending on itself: Mission item 6, and
the setup plan's Why section (53 review-round commits in eleven days).

**Setting the counts.** The plan states the stakes in a sentence and picks
both counts: the more a mistake would cost and the harder it is to undo,
the more loops. As guidance, a small reversible change in well-tested code
is a 0, and a change to stored data, money, authentication, migrations, or
the kernel itself is a 2. The kernel refuses a value outside 0 to 2.
Critique may raise either count, never lower it, and the kernel uses the
higher, so the builder's call on its own stakes is read by a session that
did not make it.

## Scope: leave it cleaner than we found it

The plan may add work that pays down known tech debt related to the change
(a workaround the change makes removable, a duplicate it touches, a test
that encodes behavior it replaces), each listed in the plan's scope with
the debt it pays. Review may do the same with a finding of kind `debt`,
which goes to `patch` inside the review rounds. The bound is the task's
Brief: added scope is metered on the same task and never raises the effect
ceiling, and unrelated debt is a new task for Tom. Mission items 1 and 5.

## The control loop

`python -m core run TASK` is the router. Each call folds the ledger to the
current state, runs the work that state calls for, records its verdict, and
continues until the task needs Tom (`waiting`, `merge`, a failed turn, or `stopped`) or reaches a stage with no
runner, then prints one status line and returns. The router reads verdicts
and follows the table; it never writes a verdict and never decides
authority. One run per task at a time: a run holds a session advisory lock
on the task for its whole run, a second returns `already running`, and one
whose lock connection died returns `lock lost` before its next turn or write.

| State | Tier | Why that tier |
|---|---|---|
| `judge`, the breadth call in `checks.test` | judgement (Jev, open-weight fallback behind the port) | a classification, well under $0.05 per call (rebuild-demonstration.md, Money) |
| `clarify`, `plan`, `build`, `patch` | frontier agent in the working session | the hard work, in one context from inspection to delivery |
| `critique`, `checks.docs` | frontier agent in a fresh session | a reading by a session that did not write the work |
| the suite in `checks.test` | deterministic suite run | running tests decides nothing by judgement |
| `checks.review` | blind verifier, Opus-class (see [architecture.md](architecture.md)) | an independent check, never the executor grading itself [4, 16] |
| `merge` | kernel and broker, then Tom | authority |

The judgement port is [judgement-layer.md](judgement-layer.md)'s; session
resume and compaction are [harnesses.md](harnesses.md)'s.

## States

Each state gives its goal, its exit evidence, and why it exists.

### intake: the request, once

**Exit evidence.** `task.started`, carrying the request verbatim, its
source (a message id, an issue, a card), and the Brief; without an issue,
the task record is the issue. Every later checkpoint reads the request as
written, never a paraphrase (Mission item 1).

### `judge`: read the request before any build

**Goal.** Route a request whose intent lives only in Tom's head to
`clarify`, and let a precise one through untouched.

**What it asks.** One judgement-tier call over the request, the thread
(empty until the bridges), and the project, against three cues: a one-line
ask whose intent lives only in Tom's head; an ask leaning on an example
that may stand for a wider requirement; an ask naming existing UI or
behavior without saying what happens to it. A code summary as input is a
gap until the emulator measures it.

**Exit evidence.** `judgement.answered` (or `judgement.failed`) at site
`intake.underspecified`, read by the kernel into the one `judge.decided`
(`leg: judgement`, the `judgement_id`, P(precise), model, cost, guard id
when thin). `precise` goes to `plan`; anything short of the floor,
including both legs failing, is `thin`, to `clarify`: a wrong `thin` costs
one clarify turn, a wrong `precise` costs review rounds.

**Guard record.** Granted by Tom on 2026-10-01; mission items 3 and 6.
Incidents: psyoptimal #894 (task `32f800bce8a2`: two feedback rounds for
three decisions one message would have settled), popoto #191 (bare
fidelity 1, 3 of 11 hidden tests; clarify 3 and 7 of 11), and #188 (bare
built a feature Tom did not want). Counter-evidence: on #872, #646, and
#893 clarifying changed nothing, and on #633 a wrong-premise question made
it worse (rebuild-baseline.md, Clarify). Expiry 2026-12-30. **Gap.** n = 1
per item and arm; the emulator ([emulator.md](emulator.md)) measures
precision and recall as a Brier score with n [12].

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

**Why.** Mission items 3 and 6; asking under uncertainty follows [3]. On
#872 the clarify message raised both questions the old system had put to
Tom (rebuild-baseline.md). The turn shows `no_material_question` by
writing `.valor/no_question.md` (`skills/sdlc/clarify.md`).

### `waiting`: Tom has a question

**Goal.** Tom's answer lands in the context that asked, named on the
`question.asked` row.

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

**Exit evidence.** The plan as a file in the workspace, committed, and
`plan.written`: its commit, its digest, the stakes sentence, both counts,
and the scope additions. A revision writes a new `plan.written`. A material
question found while planning is `asked`.

**Why.** Mission items 1 and 3. Its size follows the work: on the
baseline's small items a few lines carried what the old plan documents did.

### `critique`: read the plan before code exists

**Goal.** Find what the plan gets wrong while it is cheap to fix: a wrong
premise about the code, a case the tests will miss, scope that does not
serve the request, loop counts too low for the stakes.

**Exit evidence.** `critique.decided`: the plan digest it read, the
verdict, the findings, any raised counts, the model and cost. `sound` goes
to `build`. `revise` goes back to `plan` while critique rounds remain, else
to `build` with the findings in the prompt. A plan holding `.valor` has no
checkout: the kernel records `revise` naming it (leg `kernel`), no rerun.

**Why.** Mission item 1. Incident: on popoto #633 the clarify arm built on
a wrong premise nothing read before code existed, and correctness fell from
5 to 2 (rebuild-baseline.md, pop-a). Counter-evidence: on the small items
the old critique chain bought nothing measurable, so the count may be 0.
Guard under Tom's 2026-10-01 grant, expiring ninety days from it.

### `build`: the work

**Goal.** A candidate that does what the plan says, with the tests the plan
named, committed in the workspace.

**What runs.** Frontier turns, one `claude -p` at a time, resuming the
working session so Valor keeps its context from inspection on. Each turn
gets the Brief rendered from the ledger as it starts, corrections included.
Effects beyond the workspace are requests to the broker.

**Exit evidence.**

- `candidate`: the turn wrote `.valor/done.md` saying what it delivered,
  how it verified it, and which decisions Tom might want to change,
  recorded on `turn.collected` with the head commit. Goes to `checks`.
- `asked`: the turn wrote `.valor/question.md`. Goes to `waiting`.
- `idle`: the turn finished with neither. The task stays in `build`, and
  the run goes on to the next turn, resumed with the prompt `Continue.`
  and what made any signal not count. The run ends only when a turn leaves
  the state, fails, the task is stopped, or the run loses its lock.
- `failed`: the harness reported an error or the turn did not finish. The
  run returns; the next run retries from the ledger.

**Ledgered.** `turn.started`, every gateway opening and charge,
`turn.ended` (with the harness session id), `turn.collected`, effect rows.
Mission items 1 and 2.

### `checks`: test, review, and docs on one candidate

**Entry.** A `candidate` from `build` or `patch`: the head SHA and the turn
that produced it, recorded on `turn.collected`. Every branch is keyed by it.

**The fork.** The kernel starts the three branches against the candidate.
None reads another's output, so none waits on another and the order they
finish in changes nothing. Each ends with one verdict row keyed by the
candidate: `test.decided`, `review.decided`, `docs.decided`. A branch whose
turn fails or is stopped leaves no verdict, and the next run starts that
branch again, and only that one.

**Stale verdicts.** A branch's verdict is its latest row keyed by the
current candidate, the one on the latest `candidate` verdict. A row keyed by
an older candidate is stale: it stays in the ledger with its spend, and the
join and the merge predicate ignore it. A later row for the same branch and
candidate supersedes an earlier one, which is how a review rerun after Tom's
governance grant replaces the refused review and leaves the other two
standing. Keying by the producing turn as well as the SHA means a patch that
answers findings with reasons and no code change still gets fresh checks.

**Scheduling.** The three run concurrently when the machine has the slots,
and back to back when it does not; the join waits for all three either
way. On the 16 GB Air the suite and the two agent turns each hold the one
turn slot ([machine.md](machine.md), Concurrency), so it runs test, review,
then docs, with judgement calls beside whatever holds the slot.

#### `checks.test`: the suite, then breadth

The suite at base and at head, then breadth: see [sdlc-checks-test.md](sdlc-checks-test.md).

#### `checks.review`: one blind verification per candidate

**Goal.** An independent verdict on whether the candidate does what was
asked, correctly, and adds no ungranted governance.

**What runs.** The registered runner is
`fresh.review_runner`, the blind verifier
([architecture.md](architecture.md)). The kernel first asks the governance
boolean per hunk (use shape 6), then runs the suite and the lint itself at
base and candidate (`verify.ran`), then sets up a checkout of the candidate
with fresh services and runs one fresh session there: the builder's Opus
model, or an Opus-class model from another vendor through another harness,
never a cheaper class. It reads the request, Tom's answers and feedback,
the plan, the diff, the kernel's run, and the instances, never the
executor's narration, reruns any test itself, and answers `pass` or
`changes` as its final message, which the kernel reads from the turn's
result, not from a file in the checkout; it adds instances, never removes one, and a line it names in a
flagged hunk merges into it. Both legs failing leaves no verdict; after two
such runs the unjudged hunks become one instance. The kernel computes the
recorded verdict: a `pass` with an ungranted instance is
`governance_refused`; a `changes` lists each such instance as a finding.
The spec's setup runs in that checkout under a profile that writes the
checkout (except the checkout directory itself and every `.git` in it), its
caches, and its own `setup-tmp/`, and reads the services' password file,
never the session's config or `TMPDIR`. A candidate whose tree holds a
`.valor` entry, or whose setup leaves `.valor`, `.pi`, or no directory where
the checkout was, or leaves the checkout so the kernel gets an error
reading or writing it, has no checkout
to review: after governance and the kernel's run, the kernel records
`changes` naming it (leg `kernel`, no session, no reviewer's verdict).

**Exit evidence.** `review.decided`: the candidate, the verdict and the
reviewer's own, the findings (each with a kind, `debt` among them), the
governance answer, the kernel's run, the predicted failure, each
requirement met or not, the verifier's model and cost. `governance_refused`
means the diff adds a check, gate, hook, round, or review step without a
grant: the join sends it to Tom in `merge` with the hunk named. His grant,
one tap per instance, starts the review branch again on the same candidate,
which reuses the governance answers and the kernel's run; his feedback
sends the work to `patch` to take it out.

**Why.** Mission item 1 and the Evidence item "Independent checks". One
review is a constraint of the setup plan with no expiry. Sending work back
is the guard `review.loop`, on popoto #633: the stale-cache bug was moved,
not removed, and the Sonnet stand-in accepted it, as it accepted #191 bare
at fidelity 1 (rebuild-baseline.md, Review rounds), hence the Opus class.
A second round has no incident and expires 2026-12-30 unless one occurs.

#### `checks.docs`: the docs still describe reality

**Goal.** No doc says something the candidate made untrue.

**What runs.** A fresh session in its own clone of the candidate, cloned
from the kernel mirror, never the builder's clone. It reads the request,
the plan, the diff, and after a send-back the previous docs commits as a
patch, and changes only Markdown that instructs no turn: never a
`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, or `AGENTS.override.md`, nor
anything under `skills/`, `persona/`, or `.claude/`, in any letter case,
since this Mac's file system ignores case (`machine.is_doc_path`). No plan
widens this, so no code rides in an unreviewed docs commit.

**Exit evidence.** The session writes a verdict, its findings, and its
head. The kernel fetches that head from the session's clone into the mirror
under the check profile and keeps the longest prefix of commits on the
candidate that touch doc paths alone, with regular file modes (`100644`,
`100755`, read from `git diff-tree -r`); a merge, a commit off the
candidate, or a tree holding `.valor` keeps nothing. It records
`docs.kept`, so a rerun asks only governance again, then judges governance
over the kept diff (a doc can add a rule) and records `docs.decided`: the
candidate, the kept head, the dropped commits and their paths, the
governance answer, and the verdict it computes. `updated` or `no_change`
passes. `changes` means a doc states something the code should still honor
and the candidate breaks it, a doc cannot be made true without a code
change, the kernel dropped a commit, or the candidate's tree holds `.valor`
(no clone or session; the kernel records it, leg `kernel`). Docs commits
belong to their candidate: after a send-back they are not merged, and the
next docs session reads them as `previous-docs.patch`, keeping what holds.

**Why.** "Docs describe reality": review reads docs as the contract.

### The join

When all three verdicts exist for the current candidate, the kernel reads
them together, with the loop counts (see Loops), and moves the task once:

| Review | Test and docs | Rounds | Goes to |
|---|---|---|---|
| `pass` | test `pass`; docs `updated` or `no_change` | | `merge`, as a delivery that passed |
| `governance_refused` | anything | | `merge`, to Tom with the hunk and every other finding |
| `changes` | anything | a review round left | `patch`, with every finding |
| `changes` | anything | none left | `merge`, as a delivery that did not pass |
| `pass` | test `red` or `gaps`, or docs `changes` | the repair round unspent | `patch`, with every finding |
| `pass` | test `red`, or docs `changes` | the repair round spent | `merge`, as a delivery that did not pass |
| `pass` | test `gaps`, docs passing | the repair round spent | `merge`, with the gaps listed |

The rows are read in order and the first match decides, so a review
`governance_refused` reaches Tom before a red test is looked at, and a
review `changes` with a round left goes to `patch` whatever docs answered
on governance. Row 2 reads review only; a docs instance awaiting a tap
reaches Tom in `merge` and holds the merge until he grants it (predicate
term 4). No row records the join. The verdict that completes a join to
`merge` writes `task.delivered` with it, and the kernel requests the merge
for a delivery that passed (or passed with gaps); the router requests it
again on any run that finds the task in `merge` without one, so a crash
between the two strands nothing. A delivery that did not pass, or whose
review refused governance, gets no merge request.
The findings the join passes to `patch` are the next turn's prompt.

### `patch`: the builder resolves the findings

**Goal.** Every finding the join sent back is resolved or answered with a
reason, and the result is a new candidate.

**Entry.** The join sent work back, or Tom gave feedback in `merge` or
`merged`.

**What runs.** The working session, resumed, with all of the join's
findings or Tom's feedback as one prompt. Never a new agent: the session
that built carries why each line is there, and a send-back costs one more
turn of it. When the session nears the model's context limit it is
compacted in place and resumed ([harnesses.md](harnesses.md)).

**Exit evidence.** Same as `build`: `candidate` goes to `checks`, all three
branches again on the new candidate; `asked`, `idle`, and `failed` as in
`build`.

**Why.** Tom's decision of 2026-10-01; the demonstration's two send-backs
were one resumed turn each, $0.89 and $0.85 (rebuild-demonstration.md, Money).

### `merge`: shown to Tom, merge held for his tap

**Goal.** Tom sees the result and decides; nothing irreversible happens
without his tap.

**Entry.** The join sent the task here, as a delivery that passed or one
that did not.

**What runs.** `task.delivered` with the delivery summary reaches Tom
through a bridge: what was delivered, how it was verified, the test,
review, and docs verdicts with any breadth gaps, the tech-debt additions,
and the decisions Valor made that Tom may want to change, the one that most
changes the outcome first. The merge, and every other `act` effect the task
requested, sits held in the broker.

**Exit evidence.** `released`: `approval.granted` bound to the effect's
payload digest, carrying Tom's literal message, then `effect.intent` and
`effect.outcome`. One tap, one effect. `feedback`: `feedback.given`, to
`patch`. `governance_granted`: Tom's grant on a refused review, back to
`checks`, where only review reruns.

**The merge predicate.** The broker performs a merge only when all of
these are facts in the task's ledger:

1. one candidate `C`, the current one, with a `test.decided`, a
   `review.decided`, and a `docs.decided` row each keyed by `C`; no
   verdict for any other candidate counts;
2. the review verdict `pass`, its governance boolean answered no, or every
   governance instance it names granted by Tom's own tap (a
   `guard.granted` bound to the instance); the Brief's `governance_grant`
   does not stand in for a tap;
3. the test verdict `pass`, or `gaps` with the repair round spent and the
   gaps on the delivery; never `red`;
4. the docs verdict `updated` or `no_change`, its commits running from `C`
   to the head being merged with no merge commit between, every path `git
   diff --no-renames` names between them a doc path (so a rename out of a
   code path counts the old path), read from git at release, and its
   governance boolean answered no or each instance granted;
5. an unused `approval.granted` bound to this merge effect's digest.

Each term is deterministic: a row exists or a git fact holds, or not. No
model call decides whether a merge may happen. The broker checks all five
and writes the merge's intent in one transaction under the task's lock. A
merge whose performing process died leaves an intent with no outcome; a
later run settles it from the target once it answers (`broker.reconcile`).

**Where a merge goes.** At start the kernel records origin's push URL (as
an absolute path) and the target branch (the flag, or the branch origin's
`HEAD` names). The merge's payload carries both with the head and the
candidate, so Tom's approval binds them, and the push goes to that URL
whatever the workspace's config says later; a workspace whose own config
names a program, redirects a push, or includes other config is refused
(`core/git.py`, `HOSTILE`). For a task the kernel provisioned, the origin
is its own bare one, and the predicate's git facts and the merge's push
come from the kernel mirror, which no turn writes. A turn cannot request a
merge, and its `push_branch` cannot target the target branch.

**Governance instances.** A review or docs verdict names each instance by
a path and a line inside its hunk; the kernel reads the hunk from the
candidate's real diff and refuses one it does not find. An instance's id
digests its path, the hunk header's function context, and its added lines,
without line numbers, so the same hunk on a rerun or a later candidate
keeps its id and its grant; a changed, moved, or split hunk needs a new
tap, and identical added lines in one function context of one file share
an id. After the release the task is
`merged`; a defect found in use comes back as `feedback.given` on the same
task and goes to `patch` (Mission item 1, "resolving discovered defects").

**Why.** Mission items 1 and 6; `act` needs Tom per action [11]. Delivery
1 of the demonstration left out the decision that mattered most
(rebuild-demonstration.md, Attention log), hence the order rule. **Exists
in the kernel:** held `act` effects, approve, release, one-time approval.

### `stopped`

**Entry.** `task.stopped` from any state, written by `python -m core stop`.

**What runs.** Nothing. The row is the fence: the gateway refuses every
later model call and the broker every later effect by reading it. A stopped
task takes no answer and no feedback. The off switch lives in the kernel,
not in the model's incentives [8, 9, 10]; the demonstration's failed resume
failed cleanly and metered $0 (rebuild-demonstration.md, Kernel findings,
3). **Exists in the kernel.**

## Spending and attention

Metered spending and the attention rows Tom's actions leave are in
[spending-and-attention.md](spending-and-attention.md).
