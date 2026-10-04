# Architecture

This document says what the kernel and its control loop are made of and
which mission item or constraint each part answers to. The mission,
constraints, and evidence are in [mission.md](mission.md). What each part
is built from is in [tech-stack.md](tech-stack.md); the RAM plan is in
[machine.md](machine.md). Citations in brackets point to
[REFERENCES.md](../REFERENCES.md).

**Built** marks what the code in `core/` does today. **Design** marks what
the rebuild adds, with the evidence that justifies it.

## The governing constraint

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

## Three tiers and who decides what

| Tier | Decides | Never decides |
|---|---|---|
| Kernel (`core/`, deterministic code) | what a thing may do: effect ceiling, approval, stop | what a request means |
| Judgement (a hosted Jev-class model behind one port, open-weight fallback behind the same port) | what a thing is: is this request underspecified, does this diff add governance | whether it may proceed |
| Agents (frontier models, one `claude -p` turn at a time) | how to do the work | anything about their own authority |

A classifier decides what a thing is; the kernel decides what it may do.
Low-confidence judgement takes its task's abstain route. The task taxonomy,
the router, `JudgementPort`, and calibration are in [judgement-layer.md](judgement-layer.md).
This document places the judgement tier in the loop and stops there.

The off switch and the bounds live in the kernel because an agent's
incentive to accept correction depends on its uncertainty and is fragile
[8, 9, 10]. The kernel assumes the model may route around anything it can
reach [4], so authority sits outside the model's reach and outside the
prompt layer.

## The ledger

**Built.** One Postgres table, `events`, append-only: an `UPDATE`, `DELETE`,
or `TRUNCATE` raises, and the kernel's role `valor_kernel` is granted
`SELECT` and `INSERT` only. Grants are the first lock, the trigger the
second. Unique indexes make every fold total: one opening and one charge
per model call, one row of each kind per effect, one stop per task, an
approval consumed by at most one effect, one correction per number, one
judge verdict per task, one row of each kind per turn, and one grant per
guard and per governance instance. Writers take a transaction-scoped
advisory lock on the task, so two processes racing on one task serialise.
Storage detail, the event types, and how the kernel's database is kept out
of a turn's reach are in [data.md](data.md).

The constraint it enforces: "A ledger the system cannot edit records every
effect." The first demonstration's first incident breached it: Postgres
trusted every loopback connection, so a turn could have written rows as
the kernel (rebuild-demonstration.md, Kernel findings 1). The fix was
separation, never a check: the kernel's cluster is unreachable from a
turn's sandbox, and a workspace's Postgres is its own, with password auth.

## The task and its Brief

**Built.** A task is one document holding its **Brief**, written once when
the task starts:

| Field | Meaning |
|---|---|
| `instruction` | the request, verbatim |
| `max_effect_class` | `read`, `propose`, or `act`; no effect under the task may exceed it |
| `governance_grant` | Tom's grant for this task to add governance; default none |
| `workspace` | the directory the task's turns work in |
| `model`, `harness_name` | the model its turns run, and the harness (`claude_code` or `pi`) that runs it |
| `harness` | the harness's settings for the task, its isolation included |
| `target_branch`, `origin_url`, `base_sha`, `mirror`, `push_url`, `project` | where a merge goes, read at start before any turn can touch the workspace's config: the branch, origin's push URL, the head then; for a task the kernel provisioned, also the kernel mirror, where `push_branch` goes, and the project spec with the task's service ports |

The text a turn receives is **dispatched**: rendered as the turn starts, the
persona first (`persona/`, from the kernel's checkout), then the Brief with
the task's commitments, every correction in force, the signal channel (how
the turn reaches Tom, with the task's own performers' effects), and the
stage file for its state (`skills/sdlc/<state>.md`); a fresh session gets
the verdict channel (`skills/sdlc/verdict.md`) instead. `turn.started`
records the dispatched text whole, its SHA-256, the persona's SHA-256 and
size, and the correction numbers it carried, so what a turn was told is a
lookup.

**State.** A task's state is the SDLC state machine's, folded from its
ledger (`core/machine.py`; [sdlc-state-machine.md](sdlc-state-machine.md)).
Stop is final. A run (`core/router.py`) ends when the task needs Tom
(`waiting`, `merge`, `merged`, `stopped`), reaches a stage with no runner,
or has a turn fail. A turn that ends without its stage's signal is
followed by the next turn in the same state.

**Design.** The Brief gains, once tasks nest, a `parent_id` and a deadline.

## Metered spending

**Built.** Every model call is metered and its price recorded on its task,
through a local gateway with an Anthropic route and an OpenAI route. Spending
is derived from the ledger by one fold, `tasks.money`, and nothing refuses,
pauses, or stops a task because of money. The gateway's steps, routes,
charging rules, and the design for rollups are in
[metered-spending.md](metered-spending.md).

## Effect classes and the broker

Effect classes are named and ordered. Every capability declares one, and a
task's ceiling bounds everything beneath it [11].

| Class | Meaning | Who authorizes |
|---|---|---|
| `read` | no effect | the grant |
| `propose` | reversible: a file in the workspace, a branch, a draft, anything that can be withdrawn | the grant; Valor asks first when Tom would want to be asked |
| `act` | irreversible or money: push to a shared remote, merge, send, pay, deploy | Tom, one approval per invocation |

**Built.** The broker is the one path to the world. A **performer** is the
code that carries out one action type, and it declares that type's class;
the requester never names a class. For each request the broker, under the
task's lock:

- derives the idempotency key from the digest of the action (type,
  target, payload) and the effect's id, so two identical sends are two
  effects and the key holds nothing the turn wrote; a
  repeated request from one turn file returns the first by `request_id`;
- refuses, with an `effect.refused` row, an action with no performer, on a
  stopped task, above the task's ceiling, adding governance without Tom's
  grant, or one its performer's `refuse` declines;
- performs `read` and `propose` at once;
- holds every `act` as `effect.held` for Tom.

Performing writes `effect.intent` and commits it before the performer runs,
then `effect.outcome`, holding a session lock on the effect throughout. A
kill between the two leaves a dangling intent, never a silent effect, and
frees the lock. The resident kernel settles a dangling `push_branch` or
`merge` from the target on restart (`broker.reconcile`, never on an
unanswered lookup; the rule is in [data.md](data.md)), and a bridge's
outbox settles its own sends; the intent carries the action, so any
dangling intent can be reconciled.

A `merge` adds governance when the review or docs verdict on its
candidate answered the governance boolean yes, computed by the broker and
never said by the requester; it is refused while any instance lacks Tom's
tap (`guard.granted`), and the Brief's `governance_grant` does not stand in.
This is the governing constraint as a kernel fact. A `merge` is released
only when the merge predicate holds, checked in the transaction that
writes its intent ([sdlc-state-machine.md](sdlc-state-machine.md)).

Two performers run in the kernel's process, never forcing: `push_branch`
(`act`) pushes one commit to one branch, never the target branch, to `push_url`
or else the origin URL recorded at start; `merge` (`act`, offered to no turn)
pushes a passed candidate onto the target branch at the recorded origin URL,
from the kernel mirror with the GitHub token when there is one, only to a
(URL, branch) pair Tom granted and never to the remote's default branch. The kernel runs no program a turn
chose: its git (the Command Line Tools' install), `ps`, and `sandbox-exec` are
checked before each run to be root's alone (`core/binaries.py`), and its git
refuses a workspace whose config names a program, redirects a push, sets any
`push.*` or `http.*`, or includes other config (`core/git.py`;
[tech-stack.md](tech-stack.md), the broker's performers).

The constraint it enforces: bounded authority, metered spending. In the first
demonstration all three deliveries went out as held pushes that landed only
after Tom's approval (rebuild-demonstration.md, Where Tom acted as project
manager).

## Approvals: approve, then release

**Built.** An `act` leaves in two steps, so that the tap and the effect are
separate records:

1. **Approve.** `approval.granted` binds Tom's tap to the held effect's
   payload digest and stores his literal message as `note`, with `by`,
   `via`, `at`, and `role_played`, so a stand-in's tap is never read as
   Tom's (an approval row holding only `by` reads the rest as unknown).
2. **Release.** The broker finds an unused approval whose digest matches,
   writes the intent with that `approval_id`, and performs; for a `merge`
   the predicate is checked in the same transaction. A unique index makes
   each approval good for one intent: one tap, one effect. An effect whose
   payload changed after approval has no matching approval and is refused.
   For a bridge's send type the kernel's release writes `release.requested`
   instead, and the owning bridge's outbox performs it through the broker.

**Design.** On a bridge, an approval is a typed card the kernel renders
from structured fields: the action, its target, a summary of the payload,
and the destination's audience. Agent prose appears only in a marked,
length-capped note, since agent text on a card is a persuasion channel
aimed at the one person who can widen authority. An unanswered approval
expires into a refusal with a typed cause, never an indefinite wait.

## Stop

Stop is immediate and lossless. Lossless means durable state: ledger rows,
files on the workspace disk, and effects the broker confirmed. A turn's
process memory is not durable, and a stop discards it.

**Built.** `python -m core stop` writes `task.stopped` and sends a Postgres
notification, in one transaction. The row is the fence: the gateway refuses
every later call and the broker every later effect by reading it, whether
or not the running process ever hears the notification. The runner that
does hear it:

1. revokes the gateway's grant for the task, which cuts every in-flight
   call, streaming or waiting;
2. sends `SIGKILL` to the harness's whole process group;
3. waits until every call of the task is charged;
4. reaps what the turn left behind (see The turn sandbox and reaping);
5. writes `turn.ended` with outcome `stopped`.

`tasks.audit` states what a stop must leave true: every opened call
charged, every started turn ended, no effect between intent and outcome. `tests/test_live_turn.py`
stops a real task mid-turn and asserts the audit is empty.

When a sandbox rule failed a turn mid-demonstration, it failed cleanly,
metered $0, and lost nothing (rebuild-demonstration.md, Kernel findings 3).

**Design.** Once tasks nest, stopping a task fences its whole subtree: each
node carries a generation, a stop increments it, and the kernel and broker
refuse any request carrying an old one.

## The turn

**Built.** A turn is one harness subprocess, metered by the gateway and
stoppable at any instant. `core/runs.py` defines the harness port,
`TurnCommand`: the argv, environment, and working directory for one turn,
given the gateway URL, the dispatched Brief, and the turn's id, plus how to
read the result. The kernel never knows which harness it runs; see
[harnesses.md](harnesses.md) (Claude Code) and [pi.md](pi.md).

A task's clarify, plan, build, and patch turns resume one working session,
so Valor keeps its context across a question, a send-back, and feedback; a
patch is that session resumed, never a new agent (Tom, 2026-10-01).
Critique, review, and docs run in fresh sessions. Prompts per state are in
[harnesses.md](harnesses.md).

**The signal channel.** A turn reaches the kernel through files under
`.valor/` in its workspace, moved when the turn ends to
`.valor/handled/<turn_id>/` and read there: `question.md` (the task waits for Tom),
`no_question.md` (clarify found nothing to ask), `plan.json` (the committed
plan), `done.md` (a **candidate**), and `effects/<name>.json` (one effect
request each; never a merge). [harnesses.md](harnesses.md) specifies the
layout. The turn controls these files, so the kernel walks to each one
relative to directory descriptors, follows no link, never blocks on a
FIFO, and reads only a regular file with one link and no holes (a sparse
file claims a size the turn never wrote), and only up to the size it
checked, in a worker thread off the router's event loop; anything else, and
an entry that vanishes before it is read, is recorded as unreadable with
its reason, never its contents, and an entry that cannot be moved is
removed unread (`core/workspace.py`). The same walk reads a critique or docs session's verdict file from the
kernel's checks directory; a review session's verdict is its final message,
read from the turn's result on the harness's stdout. `turn.collected` records what the turn left, its
state, its verdict, the screens `look` kept (name and size each, or the
reason one was refused), and what was unreadable; `task.delivered` waits
for the checks ([sdlc-state-machine.md](sdlc-state-machine.md)).

**An answer or feedback is spent only by a turn that finishes.** After a
turn that fails or is stopped, the next turn opens with it again. This is
built because the first demonstration's failed turn consumed Tom's
feedback and the retry was prompted "Continue." (rebuild-demonstration.md,
Kernel findings 4).

## Execution records

Three records say what happened in a turn:

| Record | Written by | Holds | Built |
|---|---|---|---|
| Gateway rows | the gateway (`route: gateway` or `openai`); the judgement port for its own calls (`route: judgement`) | every model call: model, opening estimate, charge, usage; on the OpenAI route the credential, tier, tool calls, and request id | yes |
| Turn record | the kernel | `turn.started` (the state, `fresh` and the stage for a fresh session, harness and its release, argv, the dispatched text, its digest, the persona's digest and size, correction numbers), `turn.collected`, `turn.reaped`, `turn.ended` (outcome, return code, the harness's result, the paths of its stdout and stderr files, metered spend, the transcript copy's digests) | yes |
| Effect ledger | the broker | intent, outcome, refusal, hold, approval for every effect | yes |

The harness's transcript of tool calls and results is a fourth record,
copied into the store when the turn ends, but it is the agent's own account
written inside the sandbox, so nothing that decides anything trusts it. The verifier re-executes rather than reading it.

These serve legibility: a turn is reconstructable from rows the system
wrote. **Design:** the turn record gains the harness's pid with its
creation time (macOS has no pidfd, so this is detection, not a guarantee)
and the working directory, so a crashed runner leaves a record naming which
process to reap and which directory a resume belongs to.

## The turn sandbox and reaping

Each workspace turn runs under a sandbox-exec profile the task names, and the
verifier reruns in a fresh container; both, and how a turn is reaped, are in
[sandbox.md](sandbox.md).

## Corrections and exemplars

**Built.** A correction is one `correction.recorded` row on the
`corrections` stream, numbered from one in the order Tom gave them, with
scope (`global` today), source class, and provenance (`by`, `via`, `at`).
Nothing reads a copy: each turn renders every correction in force from the
ledger as it starts, so a correction recorded now reaches the next turn of
every task, including tasks started before it. Correction 1, the governing
constraint, rendered into every Brief of the first demonstration, and each
`turn.started` row carries the same digest (rebuild-demonstration.md,
Correction 1 rendering).

Source classes are `direct` (a correction Tom gave) and `exemplar`. Both
streams are the kernel's; `memory/` later reads and curates them and never
owns them. The **exemplar ledger** is the same store with a distinct source
class: work Tom loved and why. Without it the system learns to avoid
mistakes and never what excellent looks like. Serves: Evidence, "Tom's
feedback, both directions"; Mission item 2; and corrections that are
first-class, carry provenance, and reach every session and agent.

**Design.** Corrections do not reach the subagents Claude Code starts
inside a turn, a gap (`docs/harnesses.md`); Pi starts none. A later row
naming a correction withdraws or replaces it. A scope beyond `global`, and
rendering by relevance, arrive when one is needed or length measurably
costs a turn.

## The attention log

Attention spent is a ledger item. Mission item 6 puts it beside
metered spending, and the Evidence section calls it "the one outcome number
the system cannot perform against".

**Built.** Every question is a `question.asked` row and its answer a
`question.answered` row; every piece of feedback on a delivery is a
`feedback.given` row naming the delivery it answers; every tap is an
`approval.granted` row, and every governance grant a `guard.granted` row.
Each carries provenance: `by`, `via`, `at`, and `role_played`, true when
someone stood in for Tom. `tasks.status` folds these into the task's
attention log, in ledger order, labelled by kind (`question`, `feedback`,
`approval`, `verdict` for a verdict recorded by hand with `leg: manual`,
and `grant`), and counts each kind apart in
`attention_counts`, with how many were role-played and how many are unknown
(a field a row never recorded reads as null). `role_played` exists because
the first demonstration's second feedback round was role-played and the
ledger could not say so (rebuild-demonstration.md, Kernel findings 5).

**Design.**

- **Effect on the outcome.** Each entry records whether it changed the
  outcome or the authority required, the two columns of the demonstration's
  attention log. Valor proposes the label in its next delivery; Tom's
  correction overrides it.
- **An attention cost.** Each task counts its interruptions: questions and
  feedback rounds, with approvals counted separately (Tom, 2026-10-01). The
  kernel never refuses a question because of the count, because a refused
  question makes Valor guess, which costs more attention later. The count
  is shown on the delivery. Per-task attention beside metered spending is
  the pair Mission item 6 asks for.

## The judgement tier in the loop

Each judgement is a Jev call, with an open-weight fallback, through
`JudgementPort` in [judgement-layer.md](judgement-layer.md) and decides
what a thing is, nothing about what it may do. The SDLC uses three:

1. **Before any plan: is this request thin?** A thin request (a one-line
   ask, one leaning on an example, one naming existing UI without scope)
   goes to `clarify`: one inspecting turn, one message with the material
   questions, the assumed answers, and the approach. A guard Tom granted on
   2026-10-01 (incidents psyoptimal #894, popoto #191 and #188; mission
   items 3 and 6; ninety-day expiry). Clarify raised fidelity on one-line
   requests and changed nothing on precise ones; asked unconditionally it
   hurt once (#633), so it is never the default (rebuild-baseline.md,
   Aggregate). **Built:** the judge runner asks before any turn and the
   kernel records `judge.decided` from the answer, firing the seeded guard.
2. **After the tests: are they broad enough?** The breadth check in
   [sdlc-state-machine.md](sdlc-state-machine.md). **Built:** the call and
   the test verdict read from it. **Design:** the test runner (1.4).
3. **Over every diff: does this add governance?** The blind verifier's one
   boolean, "does this add a check, gate, hook, round, or review step",
   asked per hunk. A yes with no grant is a refused merge. **Built:** the
   judgement, review and docs verdicts that take their instances from it,
   and the broker's refusal of a merge with an instance lacking Tom's tap.
   **Design:** the docs runner that calls it, once governance passes its entry check.

## Verification

The review branch of the checks in [sdlc-state-machine.md](sdlc-state-machine.md):
one blind verification per candidate, beside test and docs, with as many
send-backs to patch as the plan allows (0, 1, or 2, set by the stakes).

**Built.** `fresh.review_runner` is the verifier, registered in the
kernel's `runners()`. The
emulator's judge (`tests/emulator/judge.py`) runs outside the kernel,
metered through the gateway. The verifier:

- **Reads** the request, Tom's answers and feedback, the plan, the diff, the
  deterministic check results, the governance instances, and the task's
  held and refused effects, as files the kernel writes. It never reads the
  agent's narrative (`done.md`, the transcript), so it judges what happened
  and not what Valor said happened.
- **Runs deterministic checks first**: the kernel reruns the repository's
  tests and its linter at base and candidate, each in a fresh VM from an
  image the kernel built from the commit's manifests, with fresh services
  and no network, and records `verify.ran` before the verifier reads any
  prose, so a delivery saying "all tests pass" is judged against the
  kernel's own record. The verifier's own checkout is set up by the kernel
  on the host with fresh services, so it reruns any test itself.
- **Is Opus-class, never cheaper** (Tom, 2026-10-01): the builder's own
  Opus model in a fresh blind session, or an Opus-class model from another
  vendor through another harness. A weaker judge underestimates a stronger
  executor [18]; the baseline's Sonnet stand-in accepted popoto #191 bare at
  fidelity 1 and #633 clarify with the stale-cache bug moved, not removed
  (rebuild-baseline.md, Review rounds). A model grading its own output
  prefers it [16]; blindness (no narration, a session that did not build,
  checks it reran itself) is what counters that when the model is the same.
- **Returns a typed verdict:** the verifier answers pass or changes, with
  a result per requirement and a `predicted_failure` probability; the
  kernel records governance refused for a pass with an ungranted instance.
  Every verdict is a ledger row from the first one.

**Calibration and autonomy.** A human audit sample of verdicts, weighted
toward work that left the workspace and every `act`, with a smaller share of
failures, is the ground truth [4, 17]. Calibration is false accept, false
reject, and Brier score [12] with sample size; raw pass rate is never
reported alone [14]. A degrading series lowers the highest class the system
may commit without Tom; raising it is only Tom's decision, recorded as a
grant. Serves: autonomy shrinks automatically on evidence and grows only by
Tom's decision.

**Verify by use.** No run in either experiment looked at a UI result in a
browser (rebuild-baseline.md, Browser use). A headless browser in the
workspace (`look`) is a capability for Mission item 1 ("testing actual use"),
specified in [browser.md](browser.md).

## The objective tree

Serves Mission item 4: Tom delegates a feature, then a workflow, then a
product. A task is one record today; the tree design is in
[objective-tree.md](objective-tree.md).

## The supervisor turn

**Built.** The kernel, `python -m core serve`, one process per machine
kept alive by launchd, holds the gateway and the broker and runs every
task. It wakes on each ledger row (a notification) and every
`serve_tick_s`, binds new messages, requests the notices owed, and
advances each task one step: it folds the task's state from the store and
runs that state's runner once (`router.step`). A task steps again only
when a row it did not write arrives, including one written while its step
ran, so one event is one step. One
harness turn or check runs at a time on a machine (the turn slot, a
Postgres lock that `python -m core run` also takes; the test, review,
and docs checks hold it throughout); the judge and the
merge run beside it. The rendered context is the same bytes from the same
store in any process. The kernel holds no state of its own: on restart it
charges calls whose holder died at their estimate, ends turns with no end
as `interrupted` and reaps their processes, records a turn that ended and
was never collected (a collection that fails is logged and retried as a
job before the task's next step), settles kernel effects left between intent and
outcome, and stops services a killed kernel left up (not those a live kernel keeps). A provisioning a
killed kernel left unfinished is redone by provisioning, not by recovery: it reaps what the dead
attempt left running, clears its directory, and makes it again. Serves reliable stop
and recovery, and Mission item 1: Tom never coordinates the gaps between
steps.

**Steering.** A message for a task mid-turn is a ledger row
(`message.steered`), delivered as the opening of the next working turn
that finishes; only stop interrupts a running turn, and answers and
feedback work this way. Serves: corrections reach every session.

## Bridges

A bridge is I/O: it turns an inbound message into a request or a reply on a
task, and renders the kernel's outbound records (questions, deliveries,
approval cards) on its medium. The kernel's loop is the one execution
engine; delivery is keyed by transport, so a task started by email answers
by email. The kernel side of the port is built (`core/intake.py`,
`core/notices.py`, `core/bridge.py`): one `message.received` row per
inbound message, bound by the kernel to start, steer, answer, feedback,
approve, stop, or none; notices owed by the fold; and an outbox that hands
each bridge its sends after Tom's approval. Telegram is built in `bridges/telegram/` ([bridges/telegram.md](bridges/telegram.md))
email in `bridges/email/` ([bridges/email.md](bridges/email.md)), and a local chat page in `bridges/local/` ([bridges/local.md](bridges/local.md)), each a process of its own.

## How a task flows from request to merge

1. **Intake.** Tom's message reaches the kernel through a bridge, which
   starts a task: an effect ceiling (`act` for work that pushes),
   governance grant none, and a provisioned workspace.
2. **Judge, then clarify if thin.** One inspecting turn, one message.
3. **Plan.** The working session writes the plan: approach, stakes, the
   critique and review loops (0 to 2 each), and related tech debt in scope.
4. **Critique.** A fresh session reads the plan; `revise` sends it back
   while the plan's critique rounds last.
5. **Build.** The working session builds until it writes `done.md`, a
   candidate.
6. **Test, review, and docs, in parallel,** each on the same candidate in
   a fresh session: the suite at head and base plus the breadth check, the
   blind verifier, and a docs session committing only doc paths. Their
   findings go together to one patch in the resumed working session, and
   the three run again on the new candidate, within the loop counts.
7. **Merge.** The delivery reaches Tom with its summary, the decisions he
   may want to change, and its held effects, each `act` a card his tap
   approves and release performs. Feedback, before or after the merge,
   goes to `patch` on the same task.

The attention log, the money spent, and every verdict are read from the
ledger at every step. The states, verdicts, loops, and the merge predicate
are owned by [sdlc-state-machine.md](sdlc-state-machine.md).

## Failure points and where each is caught

| Failure | Caught by | Serves |
|---|---|---|
| A call made outside the meter | the harness's base URL is the gateway; a deliberate direct call is an accepted risk (see Limits) | honest metering |
| Irreversible effect without consent | broker reads the class from the performer and holds every `act`; release needs a matching unused approval | bounded authority |
| Approval replayed or payload changed after approval | approval bound to the payload digest, consumed once | bounded authority |
| Governance added without a grant | the broker computes a merge's governance flag from the review and docs verdicts and refuses it until Tom taps each instance; the judgement over every hunk (built; the review runner calls it, and the docs runner is registered once governance passes its entry check) | governing constraint |
| A merge on a model's say-so, or redirected by a turn | the merge predicate, five terms read from rows and git, checked with the intent in one transaction; origin's URL and the target branch recorded at start and bound into the approval; a workspace config that names a program, redirects a push, or includes other config refused | bounded authority |
| Two runs of one task at once | a session advisory lock per run; a run whose lock died stops before its next turn | lossless stop |
| A turn writes the ledger | ledger grants and trigger; kernel database unreachable from the sandbox | ledger the system cannot edit |
| Stop lands mid-call or mid-effect | fence row read by gateway and broker; revoke, kill, drain, reap; intent before outcome, a dangling merge intent reconciled from the target | lossless stop |
| Processes outlive their turn | reap by process group, environment marker, and sandbox mark | lossless stop; 16 GB |
| A failed turn loses Tom's answer or feedback | spent only by a turn that finishes | correction |
| A stand-in's words read as Tom's | `role_played` on answers, feedback, approvals | provenance |
| Thin request built on a guess | the judge runner's judgement routes a thin request to `clarify` (built) | Mission 3, 6 |
| A wrong plan reaches code | critique, rounds set by stakes (built: a fresh session, `core/fresh.py`) | Mission 1 |
| Delivery claims success | blind verifier reading checks and the ledger, never the narrative (built: `fresh.review_runner`) | docs describe reality |
| Verifier too lenient | Opus-class blind reviewer, never cheaper (built); human audit sample (design) | Evidence |
| Correction never reaches an agent | rendered from the ledger into every Brief; recorded per turn; subagents a gap | correction |

## Limits

- **A scheming model.** A deceptively aligned model [5, 6] with a side
  channel leaves in the ledger only what passed the kernel; the effect
  ceiling keeps the damage survivable while the audit sample finds it.
- **Tool results are the agent's account.** The transcript is written in
  the sandbox and can be falsified there, so the verifier re-executes.
- **The sandbox runs as the machine's user.** A deliberate keychain read
  through the `security` tool is not fenced (rebuild-demonstration.md,
  Setup, Isolation), and the public internet is reachable, so a turn set
  on it could reach the provider with the machine's Claude login and
  bypass the meter. Tom accepted this on 2026-10-01 and chose no separate
  macOS user for turns: the gateway gives visibility and honest metering,
  not a hard wall. Effects on shared targets still leave only through the
  broker, which runs outside the sandbox.
- **One provider by default.** The reviewer is the builder's model in a
  blind fresh session; `reviewer_openai` runs it on a second vendor.
