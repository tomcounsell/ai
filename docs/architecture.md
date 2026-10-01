# Architecture

This document says what the kernel and its control loop are made of and
which mission item or constraint each part answers to. The mission,
constraints, and evidence are in [mission.md](mission.md). What each part
is built from is in [tech-stack.md](tech-stack.md); the RAM budget is in
[machine.md](machine.md). Citations in brackets point to
[REFERENCES.md](../REFERENCES.md).

**Built** marks what the code in `core/` does today. **Design** marks what
the rebuild adds, with the evidence that justifies it.

## The governing constraint

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

## Three tiers and who decides what

| Tier | Decides | Never decides |
|---|---|---|
| Kernel (`core/`, deterministic code) | what a thing may do: budget, effect ceiling, approval, stop | what a request means |
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

**Built.** One Postgres table, `events`, append-only: an `UPDATE`,
`DELETE`, or `TRUNCATE` raises, and the kernel's role `valor_kernel` is
granted `SELECT` and `INSERT` only. Grants are the first lock, the trigger
the second. Unique indexes make every fold total: one reservation and one
charge per model call, one row of each kind per effect, one stop per task,
an approval consumed by at most one effect, one correction per number.
Writers take a transaction-scoped advisory lock on the task, so two
processes racing on one task serialise. Storage detail, the event types,
and how the kernel's database is kept out of a turn's reach are in
[data.md](data.md).

The constraint it enforces: "A ledger the system cannot edit records every
effect." The first demonstration's first incident was a breach of it:
the machine's Postgres trusted every loopback connection, so a turn could
have written ledger rows as the kernel (rebuild-demonstration.md, Kernel
findings 1). The fix was separation, never a check: the kernel's cluster is
unreachable from a turn's sandbox, and a workspace that needs Postgres gets
a cluster of its own with password auth.

## The task and its Brief

**Built.** A task is one document holding its **Brief**, written once when
the task starts:

| Field | Meaning |
|---|---|
| `instruction` | the request, verbatim |
| `budget_usd_micros` | the money budget, integer micro-dollars, the only budget unit |
| `max_effect_class` | `read`, `propose`, or `act`; no effect under the task may exceed it |
| `governance_grant` | Tom's grant for this task to add governance; default none |
| `workspace` | the directory the task's turns work in |
| `model` | the model its turns run |
| `harness` | the harness's settings for the task, its isolation included |
| `mode` | `bare` or `clarify`: whether the first turn builds or inspects and asks |

The Brief a turn receives is **dispatched**: rendered from the ledger as
the turn starts, carrying the task's commitments, every correction in force,
and the signal protocol (how the turn reaches Tom). `turn.started` records
the full dispatched text, its SHA-256, and the correction numbers it
carried, so what a turn was told is a lookup.

**State.** A task is `live`, `waiting for Tom` (an unanswered question),
`delivered` (the latest delivery has no feedback after it), or `stopped`.
Stop is final. A run of turns ends when the state leaves `live`, the budget
is spent, a turn fails, or two turns in a row end with neither a question
nor a delivery (the idle bound). These are the kernel's run states; the
SDLC states a task moves through between request and delivery are in
[sdlc-state-machine.md](sdlc-state-machine.md).

**Design.** The Brief gains an `attention_budget` beside the money budget
(see The attention log) and, once tasks nest, a `parent_id` and a deadline.

## Budgets

**Built.** A budget is money and nothing else. The gateway is a local
HTTP proxy speaking the Anthropic Messages wire format; each turn is pointed
at it through `ANTHROPIC_BASE_URL` with a per-turn token in the path. For
every model call it:

1. prices the model from a fixed table (an unpriced model is refused,
   since it cannot be metered);
2. reserves the call's worst case, every input token at the most expensive
   input rate plus every output token the call may produce, under the
   task's lock, refusing with a `gateway.refused` row if it exceeds what
   remains or the task is stopped;
3. forwards the call and streams the response back unchanged, reading the
   provider's reported usage as it passes;
4. charges what the provider reported in a `gateway.charged` row. A call
   cut before its usage arrives is charged its input plus every output
   token it was allowed, so the ledger never records less than the invoice.

Remaining money is always derived from the ledger: committed, minus
charges, minus open reservations. The per-call output cap
(`CLAUDE_CODE_MAX_OUTPUT_TOKENS`) keeps the reservation close to what a call
can cost.

Because the harness's base URL points at the gateway, every call the turn
makes passes through it, Claude Code's own side calls and any subagents it
starts included. In the first demonstration 69 calls were metered at
$2.972649 against a harness-reported $2.972625 (rebuild-demonstration.md,
Money): the meter sees what Claude Code spends on its own account.

Serves bounded authority and spend. Only Tom raises a committed budget; a
turn that hits a refusal ends the run as `budget exhausted`. The budget
meters what passes the gateway; it is not a wall around the provider. A
turn that deliberately called the provider directly with the machine's
Claude login would spend outside it, and Tom accepted that on 2026-10-01:
budgets are for visibility and honest metering. Effect ceilings are not
relaxed by this; effects still leave only through the broker.

**Design.** Conservation down the tree (see The objective tree), and:

- **Overrun is a question to Tom.** A task that runs out ends with what it
  spent, what it produced, and what it asks for; Tom's grant raises the
  root. Exhaustion is never silence.
- **A deadline.** A hung tool spends no money, so a wall-clock deadline per
  task catches what money cannot see. Today the idle bound plays that part.

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

- derives the idempotency key from the action (type, target, payload
  digest) and returns the earlier outcome for a repeat;
- refuses, with an `effect.refused` row, an action with no performer, on a
  stopped task, above the task's ceiling, or adding governance without a
  grant;
- performs `read` and `propose` at once;
- holds every `act` as `effect.held` for Tom.

Performing writes `effect.intent` and commits it before the performer runs,
then `effect.outcome`. A kill between the two leaves a findable dangling
intent and never a silent effect; the performer's `lookup` asks the target
whether its key landed, which is how a dangling intent is reconciled.

An action flagged as adding governance is `act` whatever its performer
declares, and is refused outright when the Brief's `governance_grant` is
none. This is the governing constraint as a kernel fact.

Three performers exist: `push_branch` (`act`), which pushes one named
commit to one branch of the workspace's `origin` and never forces, running in
the kernel's process outside the turn's sandbox with the workspace's git
hooks, credential helpers, and SSH command pinned off; and the local
`workspace_write` (`propose`) and `outbox_send` (`act`) used by the tests.

The constraint it enforces: bounded authority and spend. In the first
demonstration all three deliveries went out as held pushes that landed only
after Tom's approval (rebuild-demonstration.md, Where Tom acted as project
manager).

## Approvals: approve, then release

**Built.** An `act` leaves in two steps, so that the tap and the effect are
separate records:

1. **Approve.** `approval.granted` binds Tom's tap to the held effect's
   payload digest and stores his literal message as `note`, with `by`.
2. **Release.** The broker finds an unused approval whose digest matches,
   writes the intent with that `approval_id`, and performs. A unique index
   makes each approval good for exactly one intent: one tap, one effect. An
   effect whose payload changed after approval has no matching approval and
   is refused.

**Design.**

- **Cards rendered by the kernel.** On a bridge, an approval is a typed
  card the kernel renders from structured fields: the action, its target,
  a readable summary of the payload (a diff summary for a push), and the
  destination's audience. Agent prose appears only in a marked,
  length-capped note, because agent text on an approval card is a
  persuasion channel aimed at the one person who can widen authority.
- **Provenance on the approval.** Approvals record `role_played` beside
  `by`, as answers and feedback already do, so a stand-in's tap is never
  read as Tom's.
- **Expiry.** An unanswered approval expires into a refusal with a typed
  cause, never an indefinite wait.

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

`tasks.audit` states what a stop must leave true: every reserved call
charged, every started turn ended, no effect between intent and outcome,
nothing charged past the committed budget. The smoke test stops a task
mid-turn and asserts the audit is empty.

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
read the result. The kernel never knows which harness it runs; the Claude
Code wrapper and its flags are in [harnesses.md](harnesses.md).

A task's frontier turns resume one working session (clarify, plan, build,
patch, docs), so Valor keeps its context across a question, a send-back,
and feedback; a patch is that session resumed, compacted when it nears the
context limit, never a new agent (Tom, 2026-10-01). Critique and review run
in fresh sessions. Each turn gets the Brief re-rendered from the ledger.
Prompts per state are in [harnesses.md](harnesses.md).

**The signal channel.** A turn reaches the kernel through files under
`.valor/` in its workspace, read when the turn ends: `question.md` (a
question for Tom; the task waits), `done.md` (a delivery: what, how it was
verified, what Tom should know), and `effects/<name>.json` (one effect
request each, passed to the broker). Each file is moved to
`.valor/handled/<turn_id>/` once read. The layout is specified in
[harnesses.md](harnesses.md). Each signal becomes a ledger row
(`question.asked`, the broker's rows, and today `task.delivered` for a
`done.md`; in the design `done.md` is a **candidate**, and `task.delivered`
waits for test, breadth, review, and docs, per [sdlc-state-machine.md](sdlc-state-machine.md)),
and `turn.collected` records everything the turn left.

**An answer or feedback is spent only by a turn that finishes.** After a
turn that fails or is stopped, the next turn opens with it again. This is
built because the first demonstration's failed turn consumed Tom's
feedback and the retry was prompted "Continue." (rebuild-demonstration.md,
Kernel findings 4).

## Execution records

Three records say what happened in a turn:

| Record | Written by | Holds | Built |
|---|---|---|---|
| Gateway rows | the gateway | every model call: model, reservation, charge, usage | yes |
| Turn record | the kernel | `turn.started` (harness, argv, the dispatched Brief, its digest, correction numbers), `turn.collected`, `turn.reaped`, `turn.ended` (outcome, return code, the harness's result, stderr tail, metered spend) | yes |
| Effect ledger | the broker | intent, outcome, refusal, hold, approval for every effect | yes |

The harness's transcript of tool calls and results is a fourth record, but
it is the agent's own account written inside the sandbox, so nothing that
decides anything trusts it. The verifier re-executes rather than reading it.

These serve legibility: a turn is reconstructable from rows the system
wrote. **Design:** the turn record gains the harness's pid with its
creation time (macOS has no pidfd, so this is detection, not a guarantee)
and the working directory, so a crashed runner leaves a record naming which
process to reap and which directory a resume belongs to.

## The turn sandbox and reaping

**Built.** A task's `harness` settings may wrap every turn in a
sandbox-exec profile; its rules and the reaper's marks in full are in
[harnesses.md](harnesses.md), and this section states what they guarantee. The first demonstration and the baseline ran every
turn under one (rebuild-demonstration.md, Setup, Isolation). The profile:

- confines reads and writes to the workspace and what the toolchain needs,
  away from Tom's other checkouts, notes, transcripts, and keys;
- denies writes to the workspace's bare `origin`, so a push leaves only
  through the broker's `push_branch`;
- on loopback, reaches only the gateway, the workspace's own Postgres, and
  the app's dev ports, and never the kernel's database;
- puts denies before allows, because sandbox-exec refused allowed ports at
  random when a network rule followed the allows (rebuild-demonstration.md,
  Kernel findings 3).

The environment is an allowlist carrying no tokens or agent sockets, with
an empty gh config and a git config without a credential helper. Web fetch
and web search are off. The public internet is reachable, so package
installs work.

**Reaping.** A turn's processes do not outlive it. Every turn runs with
`VALOR_TURN` set to its id, and its sandbox profile denies the mach name
`valor.turn.<id>`. When the turn ends, every process of this user that is
in the turn's process group, carries the marker in its environment, or sits
under a sandbox denying the turn's name receives `SIGTERM`, then `SIGKILL`
two seconds later, and `turn.reaped` lists them. The sandbox mark is the
one a daemon cannot shed: it survives `setsid`, re-parenting to launchd, and
a process overwriting its own environment. Serves: reliable stop; and the
16 GB machine, where a leaked test server holds memory a later turn needs.

**Workspace provisioning.** What the demonstration ran in: a clone holding
history only up to the base commit, a local bare repository as its only
remote, a Postgres cluster of its own with password auth, and the sandbox
above. Today scripts build it (`scripts/demo_workspace.sh`,
`scripts/replay_workspace.py`). **Design:** the kernel provisions a
workspace per task from the request's repository and the same template,
and retains its disk after a stop. Serves Mission item 1 (Valor works
without Tom setting up the gaps) and bounded authority.

**Design, the sandbox split.** This doc owns which work runs under which
sandbox. Turns run under sandbox-exec on the host, as built and as both
experiments ran. The verifier re-executes in an Apple container started
fresh from a kernel-built image, because its value is an environment the
executor never touched. The runtime's status is in [tech-stack.md](tech-stack.md),
its memory cost in [machine.md](machine.md).

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
owns them. The **exemplar ledger** is the same store with a distinct source class: work
Tom loved and why. Without it the system learns to avoid mistakes and never
what excellent looks like. Serves: Evidence, "Tom's feedback, both
directions"; Mission item 2; and corrections that are first-class, carry
provenance, and reach every session and agent.

**Design.**

- **Corrections reaching subagents.** Whether the appended system prompt
  reaches subagents a turn starts is unverified, a gap
  (rebuild-demonstration.md, Correction 1 rendering, last line).
- **Withdrawal and supersession.** A correction is withdrawn or replaced by
  a later row naming it, never by an edit.
- **Narrower scopes and relevance.** A scope beyond `global`, and
  rendering by relevance, arrive when a correction needs one or their
  length costs a turn measurably.

## The attention log

Attention spent is a ledger item. Mission item 6 puts it on the same
footing as money, and the Evidence section calls it "the one outcome number
the system cannot perform against".

**Built.** Every question is a `question.asked` row and its answer a
`question.answered` row; every piece of feedback on a delivery is a
`feedback.given` row naming the delivery it answers. Answers and feedback
carry provenance: `by`, `via`, `at`, and `role_played`, true when someone
stood in for Tom. `tasks.status` folds these into the task's attention log,
in ledger order, labelled by kind. `role_played` exists because the first
demonstration's second feedback round was role-played and the ledger could
not say so (rebuild-demonstration.md, Kernel findings 5).

**Design.**

- **Approvals in the log.** An approval is attention too; it joins the log
  with the same provenance fields.
- **Effect on the outcome.** Each entry records whether it changed the
  outcome or the authority required, the two columns of the demonstration's
  attention log. Valor proposes the label in its next delivery; Tom's
  correction overrides it.
- **An attention budget.** The Brief carries `attention_budget`, counted
  in escalations (questions, feedback rounds, approvals). The kernel never
  refuses a question for exceeding it, because a refused question makes
  Valor guess, which costs more attention later. Crossing it is a ledger
  row and is shown on the delivery. Per-task attention against budget,
  with dollars against budget, is the pair Mission item 6 asks for.

## The judgement tier in the loop

Each judgement is a Jev call through `JudgementPort` in
[judgement-layer.md](judgement-layer.md) and decides what a thing is,
nothing about what it may do. The SDLC uses three:

1. **Before any plan: is this request thin?** A thin request (a one-line
   ask, an ask that leans on an example, an ask naming existing UI without
   scope) opens with a clarify turn: Valor inspects, changes nothing, and
   sends one message with its material questions, the answer it assumes
   for each, and its intended approach. A guard Tom granted on 2026-10-01,
   ledgered with its incidents (psyoptimal #894; popoto #191 and #188), mission
   items 3 and 6, and a ninety-day expiry. Clarify raised fidelity on the
   one-line requests and changed nothing on the precise ones; asked
   unconditionally it hurt once (#633), so the classifier routes and
   clarify is never the default (rebuild-baseline.md, Aggregate).
   **Built:** the `mode` field and the clarify section of the Brief, chosen
   by hand at `start`. **Design:** the classifier and its guard row.
2. **After the tests: are they broad enough?** The breadth check in
   [sdlc-state-machine.md](sdlc-state-machine.md). **Design.**
3. **Over every diff: does this add governance?** The blind verifier's one
   boolean, "does this add a check, gate, hook, round, or review step". A
   yes with no grant is a refused merge. **Design.**

## Verification

The `review` checkpoint of [sdlc-state-machine.md](sdlc-state-machine.md):
one blind verification per pass, with as many send-backs to patch as the
plan allows (0, 1, or 2, set by the stakes).

**Design.** Nothing in the kernel verifies yet; the baseline's judge
(`scripts/judge_replay.py`) ran outside it. The verifier:

- **Reads** the request, Tom's answers and feedback, the plan, the diff, the
  deterministic check results, and the effect ledger. It never reads the
  agent's narrative (`done.md`, the transcript), so it judges what happened
  and not what Valor said happened.
- **Runs deterministic checks first** in a fresh container from a
  kernel-built image: the repository's tests and its linter. The results
  are recorded before the verifier reads any prose, so a delivery saying
  "all tests pass" is judged against the kernel's own record.
- **Is Opus-class, never cheaper** (Tom, 2026-10-01): the builder's own
  Opus model in a fresh blind session, or an Opus-class model from another
  vendor through another harness. A weaker judge underestimates a stronger
  executor [18]; the baseline's Sonnet stand-in accepted popoto #191 bare at
  fidelity 1 and #633 clarify with the stale-cache bug moved, not removed
  (rebuild-baseline.md, Review rounds). A model grading its own output
  prefers it [16]; blindness (no narration, a session that did not build,
  checks it reran itself) is what counters that when the model is the same.
- **Returns a typed verdict:** pass, changes, or governance refused, with a
  result per requirement, a `predicted_failure` probability, and the
  governance boolean. Every verdict is a ledger row from the first one.

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
workspace is a capability for Mission item 1 ("testing actual use"),
specified in [harnesses.md](harnesses.md).

## The objective tree

Serves Mission item 4: Tom delegates a feature, then a workflow, then a
product.

**Built.** One task record. Both experiments needed nothing more.

**Design.** A task is a node; a node too large for one session is split
into children, each a task with its own Brief. Rules the kernel enforces:

- A child's budget is carved from its parent's remaining, and the sum of
  children never exceeds it. Only Tom raises the root.
- A child's effect ceiling never exceeds its parent's; capabilities are
  attenuated on dispatch and the kernel refuses a request it cannot meet in
  full rather than clipping it [11].
- Decomposition cuts by outcome [13], one agent per leaf. Fan-out and depth
  are bounded by money and the leaf criterion (one session, inside its
  budget), never by a count.
- A child's report lands in the tree and the parent reads its summary.
- Stopping a node fences its subtree by generation (see Stop).

## The supervisor turn

**Built.** No separate supervisor: `python -m core run`, driven by hand,
renders the Brief, runs a turn, collects its signals, and repeats until
there is something for Tom.

**Design.** The supervisor turn is the loop step that runs whenever an
event arrives for a task: a message from a bridge, a turn ending, a
verdict, a refusal, an approval, a timer from a routine. It renders the
task's context deterministically from the store (same store state in,
byte-identical context out, ordered by volatility so the provider's cache
does the work), then advances the task one SDLC state. Deterministic
transitions are code in the state machine; decisions that are not authority
go to the judgement tier; work goes to an agent turn. The supervisor holds
no state of its own, so killing it mid-task loses nothing durable. Serves:
reliable stop and recovery; Mission item 1, since Tom never coordinates the
gaps between steps.

**Steering.** A message for a task that is mid-turn is a ledger row,
delivered as the opening of the next turn; nothing interrupts a running
turn except stop. Answers and feedback already work this way. Serves:
corrections reach every session.

## Bridges

A bridge is I/O: it turns an inbound message into a request or a reply on a
task, and renders the kernel's outbound records (questions, deliveries,
approval cards) on its medium. The kernel's loop is the one execution
engine; delivery is keyed by transport, so a task started by email answers
by email. The bridge port is owned by [bridges/telegram.md](bridges/telegram.md);
[bridges/email.md](bridges/email.md) conforms to it. Today the surface is the command line.

## How a task flows from request to merge

1. **Intake.** Tom's message reaches the kernel through a bridge. The
   kernel starts a task: a Brief with a money budget ($8 by default for a
   task started from Telegram), an attention budget, an effect ceiling
   (`act` for work that pushes), governance grant none, and a provisioned
   workspace.
2. **Judge, then clarify if thin.** One inspecting turn, one message.
3. **Plan.** The working session writes the plan: approach, stakes, how
   many critique and review loops (0 to 2 each), and any related tech debt
   pulled into scope.
4. **Critique.** A fresh session reads the plan; `revise` sends it back
   while the plan's critique rounds last.
5. **Build, test, breadth.** The working session builds until it writes
   `done.md`, a candidate; the suite runs at head and base; the breadth
   check reads the diff and tests.
6. **Review, patch.** One blind verification per pass. Findings go to the
   same working session, resumed, while the plan's review rounds last.
7. **Docs.** The working session makes every doc the change made untrue
   true again.
8. **Merge.** The delivery reaches Tom with its summary, the decisions
   Valor made that he may want to change, and its held effects. Each `act`
   is a card; his tap approves it, and release performs it. Feedback,
   before or after the merge, goes to `patch` on the same task.

The attention log, the money spent, and every verdict are read from the
ledger at every step. The states, verdicts, loops, and the merge predicate
are owned by [sdlc-state-machine.md](sdlc-state-machine.md).

## Failure points and where each is caught

| Failure | Caught by | Serves |
|---|---|---|
| Runaway spend | per-call reservation in money against the task's remaining; only Tom raises it | bounded spend |
| A call made outside the meter | the harness's base URL is the gateway; a deliberate direct call is an accepted risk (see Limits) | honest metering |
| Irreversible effect without consent | broker reads the class from the performer and holds every `act`; release needs a matching unused approval | bounded authority |
| Approval replayed or payload changed after approval | approval bound to the payload digest, consumed once | bounded authority |
| Governance added without a grant | broker refuses governance actions with no grant; verifier boolean over every diff (design) | governing constraint |
| A turn writes the ledger | ledger grants and trigger; kernel database unreachable from the sandbox | ledger the system cannot edit |
| Stop lands mid-call or mid-effect | fence row read by gateway and broker; revoke, kill, drain, reap; intent before outcome | lossless stop |
| Processes outlive their turn | reap by process group, environment marker, and sandbox mark | lossless stop; 16 GB |
| A failed turn loses Tom's answer or feedback | spent only by a turn that finishes | correction |
| A stand-in's words read as Tom's | `role_played` on answers and feedback (approvals: design) | provenance |
| Thin request built on a guess | judgement-tier routing to a clarify turn (design) | Mission 3, 6 |
| A wrong plan reaches code | critique in a fresh session, rounds set by stakes (design) | Mission 1 |
| Delivery claims success | blind verifier reading checks and the ledger, never the narrative (design) | docs describe reality |
| Verifier too lenient | Opus-class blind reviewer, never cheaper; human audit sample (design) | Evidence |
| Correction never reaches an agent | rendered from the ledger into every Brief; recorded per turn; subagents a gap | correction |

## Limits

- **A scheming model.** If the model is deceptively aligned [5, 6] and finds
  a side channel, the ledger records only what passed through the kernel.
  The effect ceiling keeps the damage survivable while the audit sample
  finds it.
- **Tool results are the agent's account.** The transcript is written
  inside the sandbox and can be falsified there; the verifier re-executes
  in a fresh container for that reason.
- **The sandbox runs as the machine's user.** A deliberate keychain read
  through the `security` tool is not fenced (rebuild-demonstration.md,
  Setup, Isolation), and the public internet is reachable, so a turn set
  on it could reach the provider with the machine's Claude login and
  bypass the meter. Tom accepted this on 2026-10-01 and chose no separate
  macOS user for turns: budgets give visibility and honest metering, not a
  hard wall. Effects on shared targets still leave only through the
  broker, which runs outside the sandbox.
- **One provider today.** Until a second is metered, the reviewer is the
  builder's model in a blind fresh session, and the audit sample carries
  more weight.
