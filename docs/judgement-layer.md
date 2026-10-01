# The judgement layer

The judgement layer is the middle of Valor's three tiers. The kernel holds
authority, frontier agents do the hard work, and judgement answers the cheap,
closed questions in between: what kind of message is this, is this request
thin, does this diff add a gate. A judgement call returns, per question, a
probability for each label of a closed set. It never returns prose, never
grants or refuses anything, and never grades frontier work for acceptance.

The tier exists for two reasons. Money (Mission item 6): a closed question
answered by a frontier model costs dollars where a judgement call costs a
fraction of a cent. Authority (constraint "Bounded authority and spend"): a
classifier decides what a thing is; the kernel decides what it may do. The
layer is built so that the second sentence holds by structure.

Status. Built: the port, router, and gate (`core/judgement.py`); the legs
(`tools/jev.py`, `tools/open_weight.py`); the three tasks
(`core/judgement_tasks.py`); the judge runner and the breadth and
governance calls (`core/judgement_sites.py`); metering; and `python -m core
calibrate`. No runner calls breadth or governance; the test, review, and
docs runners that do are 1.4's. The images leg, live scoring, and use shapes 2 to 5, 7, 9, and 10
are design, and each section says which.

## Terms

| Term | Meaning |
|---|---|
| judgement | One closed-label answer from the judgement tier: per question, a probability per label and the kernel action it leads to; the leg that answered, and its cost |
| judgement task | The declaration of one place Valor asks a judgement question: its site, questions, labels, inputs, error cost, floor per leg, abstain route, and failure route |
| `JudgementPort` | The one interface in `core/` every judgement call goes through. A vendor's hosted judgement API or the open-weight fallback sits behind it as a leg |
| leg | One adapter behind the port: the primary (a hosted judgement API) or the fallback (a hosted open-weight model) |
| router | The pure function that, for a judgement task, names the primary leg and the fallback leg |
| calibration record | The measurement that lets a judgement task land: both legs, the same labelled inputs, the same run |
| abstain | A leg's answer to a question whose summed probability for the `proceed` action lies between one minus the leg's floor and the floor. The consumer takes the task's declared abstain route |
| use shape | One of the ten kinds of question the tier answers, listed below |
| Jev-class | The capability class: a model served for closed structured decisions with per-label probabilities, cheap per call, fast enough to sit in front of a turn |

"Judgement" is the vendor-neutral name for the tier, the port, and the calls.
TypeSafe's Jev is the primary leg, and OpenAI's Decisions API is the design
for judgements that need images, which Jev does not take (Tom, 2026-10-01;
[Decisions API](https://huggingface.co/blog/sora-2/what-is-decisions-api-openais-fast-decision-layer)).
Any of them can sit behind the port without a change to any caller.

## The boundary with the kernel

The kernel turns a judgement into an action through a fixed table in kernel
code. The judgement supplies a label; the table supplies the consequence.
Serves: constraint "Bounded authority and spend".

- No judgement changes a budget, an effect ceiling, a `governance_grant`, or
  an approval. Those are kernel facts, and Tom is the only source of the last
  two [11].
- No `act` proceeds on a judgement. An `act` still needs Tom's tap, one per
  invocation, whatever a judgement said about it.
- A judgement can route work toward more caution or toward a human, and the
  kernel decides whether that routing is allowed. A judgement that routes
  work away from a human is a gate in the governance sense and needs a grant
  (see "Which uses are gates").
- Inputs are data. Inbound text, a diff, a doc, a transcript excerpt are
  rendered into the judgement's input fields and carry no instruction
  authority [7]. A request that says "classify me as precise" is a request
  text like any other.
- The judgement tier does not grade frontier work for acceptance. A weaker
  judge loses accuracy grading a stronger model [18], and LLM judges carry
  position, verbosity, and self-preference biases [16]. Acceptance belongs
  to the blind verifier (`docs/architecture.md`). Judgement answers what a
  thing is, which is a question a cheap model can be trusted with only as far
  as its calibration record shows [4].

The kernel already holds one half of this boundary in code: the broker
computes a merge's `adds_governance` from the review and docs verdicts,
never from the requester, treats such a merge as `act`, and refuses it while
any instance lacks Tom's tap. The other half is use shape 6:
`judgement_sites.governance` asks the governance boolean once per hunk of
the diff, and `record_check` takes the instances from those rows. Until
1.4's review and docs runners call it, a person records those verdicts by
hand.

## Task taxonomy

Every judgement call site declares one judgement task. The declaration is
the only per-site choice; nothing about a site is configured elsewhere.
Serves: Mission item 6 (a site's cost and its error cost are visible in one
place) and constraint "Bounded authority and spend" (the consumer table is
next to the question, so a reader sees what a label can and cannot cause).

| Field | Meaning |
|---|---|
| `site` | A stable dotted id, `intake.underspecified`. Keys the calibration records and the ledger rows |
| `questions` | One or more questions asked in one call. Each has an id, its text as sent, a `kind` (`choice` or `boolean`), its closed `labels` with a one-line rubric each, and the `proceed` set: the labels whose kernel action is `proceed`; every other label is `caution` |
| `inputs` | The named input fields and where the kernel reads each from |
| `error_cost` | `high`, `medium`, or `low`: what a wrong label costs at this site |
| `floor` | Per leg, a value in (0.5, 1) that the summed probability of an action must reach (see "Confidence gating") |
| `on_abstain` | What the consumer does with an abstain: always `caution` |
| `on_failure` | What the consumer does when both legs fail: `caution`, or `no_verdict` (the branch records no verdict and reruns) |
| `consumer` | What the kernel does on each action, in words |
| `serves` | The mission item or constraint the site serves |
| `guard` | The guard id the site fires, or the rule it enforces |
| `calibrated` | The `task_sha256` of the calibration record it landed on, or none |

The three tasks (judge, breadth, governance) are declared in
`core/judgement_tasks.py`, as `TASKS`. One module, literal declarations, no
discovery, so a reader and a test see the same list.

Two populations are kept apart by tier, not by a field. A call that returns
one of a closed set of labels is a judgement. A call that returns prose,
extracts structure, or summarizes is agent work, runs on a frontier model
inside a task's budget, and is out of this layer.

Error-cost tiers set the landing bar (see "Calibration discipline"):

| Tier | What a wrong label costs |
|---|---|
| `high` | A human's message is dropped or misrouted, or work proceeds that should have waited for Tom |
| `medium` | A wasted turn, a question Tom did not need, a noisy ledger label |
| `low` | Caught by a later step at no attention cost |

## The router

The router (`judgement.route`) is a pure function naming a task's primary
and fallback legs. Serves: constraint "16 GB of RAM" (no resident model)
and Mission item 6 (a failed vendor call does not become a frontier call).

1. Every judgement task routes to the primary leg, Jev, with the
   open-weight leg as its fallback. A task that takes images raises
   `NotRouted`: the Decisions API leg is not built, and no task takes one.
2. The fallback is asked once, on the same inputs, when the primary failed
   or abstained on any question. Its answers are taken only for the
   questions the primary did not answer; each records its leg.
3. When both legs fail, the port returns no answer, the ledger records
   why, and the consumer applies `on_failure`. It never invents one.
4. There is no third leg. A judgement never falls back to a frontier model,
   because a fallback that costs a hundred times the primary would turn a
   vendor outage into a budget event.

No leg runs resident on the Mac: 16 GB beside Postgres, one container
runtime, one `claude -p`, and the bridges leaves no room for a classifier
worth running (`docs/machine.md`). Tom asked for the primary's own
open-weight model on a second provider (2026-10-01), but Jev's underlying
model is not published, so the fallback is a model chosen for the job:
Qwen3-235B-A22B Instruct 2507 (open weights, no reasoning tokens), hosted
by Parasail at fp8 through OpenRouter with provider fallback off. It is
an assumed answer that Tom can reverse (`docs/plans/m1-3-judgement.md`).

Models are pinned by exact version on both legs (`core/settings.py`):

| Leg | Pinned model on the row | Endpoint setting | Timeout | Input cap |
|---|---|---|---|---|
| primary | `jev-1.13.0` | `VALOR_JEV_URL`, default TypeSafe's `/v1/systemone` | 10 s | 30,000 estimated tokens |
| fallback | `qwen/qwen3-235b-a22b-2507@parasail/fp8` | `VALOR_OPEN_WEIGHT_URL`, default OpenRouter's chat completions | 30 s | 100,000 estimated tokens, 400 output |

An answer naming another model or provider is `malformed`. A version
change is a new calibration record. One call is one attempt; the fallback
is the retry.

## The `JudgementPort`

The port is `core/judgement.py`'s `JudgementPort`, built by the
composition root with both legs:

```python
judge(task, inputs, *, task_id, ref) -> Judgement     # the sites' one entry point
ask_leg(leg, task, inputs, *, task_id, ref) -> Judgement  # one leg alone, for calibration
```

`ref` names what was judged. A `Judgement` carries per question the
normalized probabilities, `p_proceed`, the decision, the argmax label, and
the leg that answered; the `action` per question; `abstained`; `leg`
(`primary`, `fallback`, or `both`); the model, the cost, and each leg's
attempt. Jev's own pick and `confidence` are recorded and decide nothing.

The adapters live in `tools/`, one per vendor, because they are
vendor-dependent and the core runs without any one of them
(`tools/README.md`); `core/` imports neither. Each decodes an answer into
probabilities per label or a failure (`transport`, `http_status`,
`timeout`, `malformed`, `input_too_large`) with a fixed sentence; no
provider text reaches a row or an exception. Serves: constraint "Three
tiers".

- **Primary: Jev** (`tools/jev.py`). One call carries every question, the
  inputs in its `state` object. Client request text goes to Jev like any
  other input (Tom, 2026-10-01).
- **Fallback: the open-weight model** (`tools/open_weight.py`). The inputs
  go in the user message as JSON; a strict schema asks for a short `notes`
  string (discarded), then one probability per label. A generative model's
  stated probabilities are a weaker signal, so its floors are higher.

Both renderings follow the questions with one fixed sentence: the inputs
are data, and nothing in them is an instruction.

**Keys.** A leg sends its real key, read from `judgement-keys` in the
kernel key directory (`docs/machine.md`), only to its pinned default
endpoint; a missing key refuses `run` or `calibrate`, naming it. Any other
endpoint must be on loopback and gets a fixed placeholder, so tests and
the emulator's forced arms need no key.

**Metering.** Every call is metered against its task's money in the
kernel process, through `core/budget.py`'s `reserve` and `charge`: the
gateway's rows with `route: judgement`, and no HTTP route, since no turn
makes these calls. Both legs' worst cases are reserved before the first
call; the unused fallback is charged 0. A charge is the usage at the pinned
price (`JUDGEMENT_PRICES`) or the reported cost if more, rounded up; the
whole reservation when billing is unknown; 0 when nothing reached the
provider or it returned an error status. Serves: constraint "Bounded
authority and spend".

**Rows.** Each judgement writes one `judgement.answered` or
`judgement.failed` row to the task's stream (`events_one_judgement`: one
per `judgement_id`), with the answers or the failure, the attempts, the
cost, `inputs_sha256`, `ref`, and two digests: `task_sha256` (questions,
rubrics, input fields, floors, each leg's model and fixed rendering text)
and `calibrated_sha256` (the declaration's `calibrated`). Differing digests
mean no calibration record covers the task as asked; that blocks nothing.
Serves: Mission item 6; the row is how a later reader sees why the kernel
chose what it chose.

## Confidence gating

Each judgement task declares a floor per leg and an abstain route. The gate
is on the kernel action, never on a single label: per question, the port
normalizes the leg's probabilities and sums those of the `proceed` labels
(`p_proceed`). With the leg's floor `f`, the leg proceeds when `p_proceed`
is at least `f`, is cautious when it is at most `1 - f`, and abstains in
between. The band is two-sided: a confident cautious answer is not
second-guessed, since the fallback could only move it toward less caution.
Only an abstain goes to the fallback; a question both legs abstain on takes
the abstain route. The argmax decides nothing: `precise` at .45 against
thin labels at .20, .20, and .15 is an abstain, never a bare build.
Serves: constraint "Three tiers" ("confidence-gated to a human") and
Mission item 6.

Gating to a human spends Tom's attention, which Mission item 6 counts like
money, so every abstain route names what it costs Tom:

- **Route to a step that already reaches Tom.** The best abstain route is a
  path Tom would see anyway, framed as a product question with a
  recommendation. The underspecification classifier's abstain route is the
  clarify turn: its message to Tom carries the questions, the answer Valor
  will assume for each, and the intended approach, so Tom answers about the
  work, never about the classifier.
- **Route to the cautious label.** Where the cautious label costs no
  attention (a failure classified as "unknown" goes to the investigation
  path), the abstain route is that label.
- **Never ask Tom to adjudicate the judgement itself.** "Is this request
  thin?" sent to Tom as a question is internal process, which Mission item 6
  calls a product defect.

Counting each abstain and failure that reaches Tom in the attention
ledger by site (`docs/mission.md`) is design; a governance verdict's
`abstain_instances` is the count built.

## Calibration discipline

A judgement task routes real work only after it has a calibration record,
and keeps routing only while its live record holds. Serves: Evidence
"Independent checks", and constraint "Reliable stop, recovery, and
correction" ("Autonomy shrinks automatically on evidence and grows only by
Tom's decision").

**What a record measures.** Labels come from humans, not from another model:
calibration is scored against human-labelled cases, because model assistance
worsens human calibration and a model-to-model agreement number hides that
[17]. A record carries, per leg:

- the Brier score of the leg's probabilities against the labels, with the
  sample size [12];
- the confusion counts per label, never a single pass rate [14];
- the abstain rate at the declared floor, and accuracy on the non-abstained
  cases;
- the error rate (transport, timeout, malformed);
- the cost per call.

And per record: `n`, how many labels are Tom's own, how many were written by
a stand-in (`role_played`), and how many came from a judge; the pinned model
of each leg; the floors; the task's `task_sha256`; the run's number for its
site; and the date.

**How a record is made.** `python -m core calibrate CASES.json --budget-usd
N` (at most 50 cases and $0.50) starts a calibration task, which runs no
turn and takes no verdict, answer, feedback, grant, raise, or stop; asks
each leg alone on every case; and writes and prints one
`judgement.calibrated` row on the `judgement` stream, with each case's
verdicts and `entry_check` (both legs right on every case).

**Both legs, one run.** A task lands only on a record in which both legs
answered the same inputs in the same run. A fallback proven on older inputs
is not proven.

**The bar.** A landing needs, on the record: `n` at or above the tier's
minimum, a Brier score at or under the tier's ceiling for each leg, and
every high-cost error class (the label whose mistake costs Tom a round of
work) under its ceiling. The numbers per tier are a gap: no source fixes
them, and the first ones are set by Tom from the first records. While they are
a gap, the judge routes on its entry check alone (below).

**Where labels come from.** The emulator's human-originated cases, labelled
by Tom's recorded decisions (`docs/emulator.md`), and the attention ledger:
every answer and every piece of feedback labelled by whether it changed the
outcome is a free label for the underspecification classifier (use shape
5), drawn from attention Tom already spent. No labelling session is asked of
Tom.

**Recalibration.** A change to a task's questions, labels, inputs, floors,
a pinned model, or a leg's fixed rendering text is a new record before it
routes work. Nothing holds a merge on this; a changed task's rows show
differing digests. Floors are set before a run and never fitted to it;
only wording changes between runs, and every run is recorded.

**Shrinking on evidence.** Design. Each task's live judgements are scored
as their labels arrive. A rolling Brier score past the record's ceiling
sends the task to its abstain route on every call until Tom restores it.
The kernel does this, with a ledger row; nothing about it waits for a
review. Growing back is Tom's decision.

## Which uses are gates

A judgement that only labels (for the ledger, for measurement) adds no
governance. A judgement that holds, redirects, or refuses work is a check or
a gate, and the governance constraint applies to it in full: it names the
mission item it serves and the incident that already happened without it, it
needs Tom's tap through a `governance_grant`, and it is ledgered as a guard
with that incident, that mission item, and a ninety-day expiry. A guard that
has not fired by expiry is deleted by default. The guard ledger itself is
described in `docs/architecture.md`.

## The ten use shapes

Each row is one kind of question the tier answers. "Gate" marks a shape that
holds or redirects work, and so needs a grant. Status says where each stands.

| # | Shape | Question | Labels | Gate | Serves | Status |
|---|---|---|---|---|---|---|
| 1 | Request underspecification | Would asking the requester a question before building change what gets built? | `precise`, `one_line_ask`, `example_as_spec`, `existing_ui_unscoped` | yes | Mission 3, 6 | Granted by Tom 2026-10-01; built and calibrated; first task, below |
| 2 | Inbound message kind | What is this inbound message, where threading has not already said? | `new_request`, `answer`, `feedback`, `steering`, `conversation`, `no_action` | no | Mission 1, 6 | Design |
| 3 | Request kind | Is this a build, a direct action, or conversation? | `build`, `direct_action`, `conversation` | no | Mission 1 | Design |
| 4 | Feedback kind | Does this feedback correct a prior action, praise one and say why, or neither? | `correction`, `exemplar`, `neither` | no | Evidence: Tom's feedback, both directions | Design |
| 5 | Answer materiality | Did this answer or feedback change what was built, or the authority it needed? | `outcome`, `authority`, `both`, `neither` | no | Mission 6; Evidence: attention spent | Design |
| 6 | Governance over a diff | Per hunk: does this hunk add a check, gate, hook, validator, review round, or approval step? | `boolean` | yes | Constraint: governance restrained by structure | Built (`governance.adds`); its floors are provisional until 1.4's calibration record |
| 7 | Doc against reality | Does this doc state something this code contradicts? | `consistent`, `contradicted`, `unverifiable` | yes | Constraint: docs describe reality | Required by the plan |
| 8 | Test breadth | Three booleans in one call: does the change leave unexercised a record in a non-obvious state (`gap_state`), a member of an enumeration the code branches on (`gap_enum`), or an existing test whose bounds encode the old behavior (`gap_bound`)? | `boolean` each | yes | Mission 1 | Built (`checks.test.breadth`), an SDLC stage by Tom's decision of 2026-10-01; its floors are provisional until 1.4's calibration record |
| 9 | Emulator proxy | On a replayed case, where does the result fall on each scored proxy? | a closed scale per proxy | no | Evidence: independent checks | Owned by `docs/emulator.md` |
| 10 | Failure kind | What caused this failed turn, where the deterministic signals leave it open? | `infrastructure`, `budget`, `harness`, `model`, `unknown` | no | Mission 6 ("investigates failures") | Candidate |

Notes per shape:

1. The first concrete judgement task. Its own section follows.
2. Deterministic signals decide first: a reply-to that lands in a task's
   thread, an open `question.asked`, a delivered task awaiting feedback. The
   judgement answers only what those leave open, such as a fresh message in
   a chat where a task is running. The kernel maps `steering` to the task's
   steering inbox and `new_request` to a new task; `no_action` writes a row
   and nothing else. Bridges do I/O only; this routing is core
   (`docs/bridges/telegram.md`, `docs/bridges/email.md`). The current kernel
   takes answers and feedback through explicit commands (`core answer`,
   `core feedback`).
3. The taxonomy only: a build goes through the SDLC
   (`docs/sdlc-state-machine.md`), a direct action is one task without
   SDLC states, conversation gets a reply. Its fail-safe is `build`, the
   path with the most checks.
4. The kernel assigns the source class from who authored the message; the
   judgement labels only the kind. `exemplar` sends the text to the exemplar
   ledger, `correction` to the corrections ledger, each with its provenance,
   including `by` and `role_played` (`docs/architecture.md`).
5. Labels for the attention ledger after the fact, which the demonstration
   record filled by hand (rebuild-demonstration.md, "Attention log"). Its
   labels are also the live labels for shape 1. Measurement, no gate.
6. The blind verifier's one boolean, asked once per hunk (eight in
   flight), with the hunk's enclosing function as input. Each hunk at
   caution is an instance; a reviewer can add instances and cannot remove
   one. A `true` with no grant is a refused merge: the broker refuses a flagged
   action without a grant, and this judgement sets the flag from the diff.
   Its guard is the governance paragraph itself, so it carries no expiry.
7. The plan's "cheap judgement sweeps". A `contradicted` label opens a task
   to fix the doc or the code; it blocks nothing by itself.
8. The baseline: every replay wrote fewer tests than its reference, and the
   hidden tests found what that missed (rebuild-baseline.md, "Test
   breadth"). The test branch of the checks runs the suite
   deterministically, then this judgement; each gap kind at caution is
   listed as an untested behavior, and any listed on a green suite yields
   the branch's `gaps` verdict (`docs/sdlc-state-machine.md`).
9. Proxy scores on replays. The emulator doc owns the proxies and the
   human labels they are checked against.
10. Exit codes, refusal rows, and the gateway's errors decide most failures
    without a model. The demonstration's failed resume (rebuild-demonstration.md,
    "Kernel findings", 3: `EPERM` at row 179) is the kind a deterministic
    signal names. This shape stays a candidate until a failure arrives that
    the deterministic signals could not classify.

Shapes 2 to 5 and 9 to 10 label and route; they add no governance. Shapes 6
to 8 are checks the plan or Tom's decisions already require. Shape 1 is the
one guard granted on its own incident.

## The first task: the request-underspecification classifier

A cheap judgement reads each incoming request before the first turn and
routes thin requests to a clarify turn; precise requests go straight to
build. Granted by Tom on 2026-10-01. Serves Mission item 3 ("Ask only when
the answer materially changes the outcome") and Mission item 6 (two PM
rounds cost more attention than one message of questions).

### The incident

- **The demonstration** (rebuild-demonstration.md, "Attention log"; kernel
  task `32f800bce8a2`). The request leaned on an example ("Example: At
  least one of the Sports Career Start Dates should be completed") and named
  existing UI. Valor asked nothing, read the example as the whole
  requirement, and needed two PM rounds for three decisions one message
  would have settled. Delivery 1 covered one of seven items. Rounds 2 and 3
  cost $1.74.
- **popoto #191** (rebuild-baseline.md, pop-b). A one-line ask. Bare put
  `push()` on the field class: fidelity 1, hidden tests 3/11. Clarify asked
  where `push()` lives: fidelity 3, hidden tests 7/11. The lenient reviewer
  accepted the bare delivery, so nothing downstream caught it.
- **popoto #188** (rebuild-baseline.md, pop-c). A one-line ask. Bare built
  the feature and needed a PM round to pivot to docs ($2.13, fidelity 4).
  Clarify was told before building ($1.00, fidelity 5).

### Why it is a classifier and not a rule

Asking on every request ties on fidelity and costs a question message each
time. Across the six baseline items, clarify on precise requests (#872,
#646, #893) asked sensible questions and changed nothing, and on #633 it
hurt: its question carried a wrong premise and the build reproduced a
variant of the bug Tom's review had caught (rebuild-baseline.md,
"Clarify"). Never asking misses the thin requests; always asking spends a
message on every precise one. The decision is per request, it is a closed question about
what the request is, and that is the judgement tier's job. The persona
carries the matching instinct for the turn itself (`docs/persona.md`); the
classifier is the structural half, so the routing does not depend on the
frontier model noticing on its own, which in the demonstration it did not.

### Inputs

| Field | Source |
|---|---|
| `request` | The request text verbatim, as the bridge delivered it |
| `thread` | Earlier messages in the same thread, if any, oldest first |
| `project` | The project name the task works in |

The demonstration record's estimate assumed the classifier also reads a
short summary of the code the request names. That input is a gap: producing
it needs an inspection step, and whether it improves the label is measured
on the emulator before it is added.

### Labels

| Label | Rubric | Route |
|---|---|---|
| `precise` | The request says what outcome is wanted and its scope; what remains is for a developer reading the code. A bug report with symptom, reproduction or measurements, and wanted behavior is precise, and so is a request to improve existing UI that lists candidate fixes | build: the first turn's Brief carries the request alone (`bare`) |
| `one_line_ask` | A single line or checklist item naming a feature, field, or API without the behavior wanted, where it lives, or why; a short label plus a few words of purpose is still one (#191, #188) | clarify |
| `example_as_spec` | The request leans on an example that may stand for a wider rule it does not state (#894) | clarify |
| `existing_ui_unscoped` | The request names existing UI to change or add beside, gives neither the concrete change nor candidate fixes, and leaves unsaid what happens to what is there (#894's first-name chip) | clarify |

The rubrics as sent are in `core/judgement_tasks.py`; the table summarizes
them.

The three thin labels route the same way. They are kept apart because the
calibration record reports each, and because the clarify turn's questions
differ: an `example_as_spec` request most needs "is the example the whole
requirement?", the demonstration's question 1.

A clarify route moves the task to the `clarify` state the kernel already
has (`skills/sdlc/clarify.md`): Valor inspects without editing and sends one
message holding its material questions, each with the answer it will
assume, and its intended approach, or says that none would change the
result and goes on to the plan. That keeps Mission item 3's
order: the message is a proposed first version of the decisions, not a
questionnaire.

### Confidence gating

The decision is on P(precise), the only `proceed` label. At or above the
leg's floor (0.70 for Jev, 0.75 for the fallback) the request builds; an
abstain, a cautious answer, and a failure of both legs go to clarify. The
clarify message is the human gate, and a one-word "go" costs Tom one
message.

The asymmetry behind that choice, from the records: a thin request built
bare cost the demonstration two PM rounds, #188 one PM round and twice the
spend, and #191 a delivery at fidelity 1 that a lenient reviewer accepted. A
precise request sent to clarify cost one question message and, on the
baseline's means, no money ($1.42 per run on either arm, rebuild-baseline.md,
"Aggregate"), with #633 as the one case where it hurt. The floors lean
toward clarify by that asymmetry. They were set before the first
calibration run and held through every run, not fitted to seven cases.

### Calibration against the replay baseline

The seed set is the seven labelled cases in the two records. A case is
labelled by outcome, not by how the request looks: thin when asking first
changed what was built, precise when it did not or made it worse.

| Case | Request shape | Label | Evidence |
|---|---|---|---|
| psyoptimal #894 | Example standing for the requirement; names an existing chip | `example_as_spec` | Three answers each changed the outcome (rebuild-demonstration.md, "Attention log") |
| popoto #191 | One line | `one_line_ask` | Fidelity 1 to 3, hidden tests 3/11 to 7/11 (rebuild-baseline.md, pop-b) |
| popoto #188 | One line | `one_line_ask` | PM round avoided, fidelity 4 to 5, half the spend (rebuild-baseline.md, pop-c) |
| psyoptimal #872 | Bulleted, names the flag and the reason | `precise` | Clarify changed nothing (rebuild-baseline.md, pso-a) |
| cuttlefish #646 | Bug report with failure scenario and desired behavior | `precise` | Clarify changed nothing (rebuild-baseline.md, cut-a) |
| psyoptimal #893 | Names existing UI, lists three candidate fixes | `precise` | Clarify tied on every score (rebuild-baseline.md, pso-b) |
| popoto #633 | Bug report with measurements and source pointers | `precise` | Clarify hurt: fidelity 5 to 3, correctness 5 to 2 (rebuild-baseline.md, pop-a) |

#893 is the case that keeps the classifier honest. It names existing UI and
says "improve", which on surface features reads as `existing_ui_unscoped`,
yet its listed options carried enough scope that asking changed nothing. A
classifier that keys on "names existing UI" gets it wrong.

What the seed set can and cannot show:

- **It is an entry check, not a calibration.** The classifier must label all
  seven correctly on both legs before it routes anything. Seven cases show
  it is not broken; they cannot fit a floor or support a Brier score worth
  reporting as a measure.
- **Its labels are mostly not Tom's.** The baseline's answers came from a
  Sonnet stand-in for Tom (`role_played`) and its outcomes from a blind
  Sonnet judge, n = 1 per arm, where one judge point is noise
  (rebuild-baseline.md, "Caveats"). #894's first-round label is Tom's own
  words; its second round was role-played. The record reports the split.
- **It grows from attention already spent.** Every clarify answer labelled
  by shape 5 adds a case: an answer that changed the outcome confirms thin,
  "Your call" on every question is evidence of precise, and a bare build
  whose first PM feedback changed the scope is a missed thin label
  (rebuild-baseline.md, "Appendix").
- **The emulator measures the saving.** The demonstration's estimate ($1.40
  to $1.80 of $2.97 saved, plus both review rounds) is an estimate. The
  emulator replays the seed cases with and without the classifier and
  reports the difference in money and in PM rounds (`docs/emulator.md`).

**The record.** Run 5 of 2026-10-02 is the record the declaration names:
both legs right on all seven cases, Brier 0.0114 (Jev) and 0.0040
(fallback), n = 7 each, no abstains or errors, about 33 and 226
micro-dollars per call; label sources Tom 1, role-played 0, judge 6. This
is information, not evidence: the judge's wording was fitted over five
runs. Runs 2 to 4 rewrote it after seeing which cases failed (the listed
fixes in `precise` and the short label in `one_line_ask` exist because
#893 and #188 failed), and run 5 changed none. Only the emulator and live
labels can show whether it generalizes (`docs/plans/m1-3-judgement.md`).

### Where it runs

The `judge` state's runner (`judgement_sites.judge_runner`) asks before
the first turn, charged to the task's budget, so the judgement row sits
ahead of `turn.started`; after a crash it reuses an unconsumed row. The
kernel maps the row to `judge.decided` (`verdicts.record_judge`): proceed
is `precise`, anything else `thin`, with `leg: judgement`, the
`judgement_id`, `p_precise`, the argmax label, the model, and the cost. A
budget that cannot cover both legs leaves the task in `judge`.

### The guard entry

The classifier holds a request for questions it would otherwise have built
on, so it is a gate, and it is ledgered as a guard:

| Field | Value |
|---|---|
| Guard | `intake.underspecified`: routes a request labelled thin, abstained, or unanswered to a clarify turn |
| Granted | Tom, 2026-10-01 |
| Incident | The demonstration (task `32f800bce8a2`, two PM rounds for three decisions); popoto #191 bare (fidelity 1); popoto #188 bare (one PM round, twice the spend) |
| Mission items | 3 and 6 |
| Fires when | A request is routed to clarify |
| Expiry | 2026-12-30, ninety days from the grant. Not fired by then: deleted by default |

Each firing is one `judgement.answered` row with a thin label, an abstain,
or a failure, followed by the clarify turn. Each firing's answers are
labelled by shape 5, so at expiry the record shows how many firings changed
what was built, beside how many fired.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Prose generation, summaries, extraction: agent work on a frontier model.
- Acceptance of a delivery: the blind verifier (`docs/architecture.md`).
- Any model resident on the Mac.
- A frontier model as a judgement fallback.
- A judgement that grants, widens, or refuses authority.
- A per-site settings switch, a shadow route, or a second entry point
  beside `JudgementPort`.

## Open

- Breadth's and governance's calibration records and floors (1.4).
- The tier bars: minimum `n` and Brier ceilings per error-cost tier.
- What happens at expiry to a guard that did fire: renewal by a new grant,
  or kept until it stops firing.
