# The judgement layer

The judgement layer is the middle of Valor's three tiers. The kernel holds
authority, frontier agents do the hard work, and judgement answers the cheap,
closed questions in between: what kind of message is this, is this request
thin, does this diff add a gate. A judgement call returns one label from a
closed set, a probability per label, and a confidence. It never returns
prose, never grants or refuses anything, and never grades a frontier agent's
work for acceptance.

The tier exists for two reasons. Money (Mission item 6): a closed question
answered by a frontier model costs dollars where a judgement call costs a
fraction of a cent. Authority (constraint "Bounded authority and spend"): a
classifier decides what a thing is; the kernel decides what it may do. The
layer is built so that the second sentence holds by structure.

Status. The current kernel has no judgement layer. It has the clarify mode
the first judgement task routes to (`core/tasks.py`, `core/signals.py`), the
broker's refusal of an action flagged as adding governance without a grant
(`core/broker.py`), and the gateway that meters every model call against a
task's budget (`core/gateway.py`, `core/budget.py`). Everything else in this
doc is design, and each section says which.

## Terms

| Term | Meaning |
|---|---|
| judgement | One closed-label answer from the judgement tier: a label, a probability per label, a confidence, the leg that answered, and its cost |
| judgement task | The declaration of one place Valor asks a judgement question: its id, question, labels, inputs, error cost, confidence floor, abstain route, and fail-safe label |
| `JudgementPort` | The one interface in `core/` every judgement call goes through. A vendor's judgements API or the open-weight fallback sits behind it as a leg |
| leg | One adapter behind the port: the primary (a hosted judgements API) or the fallback (a hosted open-weight model) |
| router | The pure function that, for a judgement task, names the primary leg and the fallback leg |
| calibration record | The measurement that lets a judgement task land: both legs, the same labelled inputs, the same run |
| abstain | A judgement whose confidence is under the task's floor. The consumer takes the task's declared abstain route |
| use shape | One of the ten kinds of question the tier answers, listed below |
| Jev-class | The capability class: a model served for closed structured decisions with per-label probabilities, cheap per call, fast enough to sit in front of a turn |

"Judgement" is the vendor-neutral name for the tier, the port, and the calls.
TypeSafe's Jev is the primary leg, and OpenAI's Decisions API answers the
judgements that need images, which Jev does not take (Tom, 2026-10-01; [Decisions API](https://huggingface.co/blog/sora-2/what-is-decisions-api-openais-fast-decision-layer)). The
port is named so that either one, or the open-weight fallback, can sit
behind it without a change to any caller.

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

The kernel already holds one half of this boundary in code: the broker treats
an action flagged `adds_governance` as `act` and refuses it when the Brief
carries no `governance_grant`. Today the requester sets that flag. Setting it
from a judgement over the diff is use shape 6 and is design.

## Task taxonomy

Every judgement call site declares one judgement task. The declaration is
the only per-site choice; nothing about a site is configured elsewhere.
Serves: Mission item 6 (a site's cost and its error cost are visible in one
place) and constraint "Bounded authority and spend" (the consumer table is
next to the question, so a reader sees what a label can and cannot cause).

| Field | Meaning |
|---|---|
| `site` | A stable dotted id, `intake.underspecified`. Keys the calibration records and the ledger rows |
| `question` | The question as sent, in plain words |
| `kind` | `choice` (one of a closed set of string labels) or `boolean` |
| `labels` | The closed set, each with a one-line rubric |
| `inputs` | The named input fields and where the kernel reads each from |
| `error_cost` | `high`, `medium`, or `low`: what a wrong label costs at this site |
| `confidence_floor` | Under this confidence, the judgement abstains |
| `abstain_route` | What the consumer does with an abstain, and its attention cost |
| `fail_safe` | The label the consumer applies when both legs fail |
| `consumer` | The kernel table that maps each label to an action |
| `serves` | The mission item or constraint the site serves |
| `guard` | For a site that is a gate: the guard ledger entry that granted it. Empty otherwise |

All judgement tasks are declared in one module under `core/`. One module,
literal declarations, no discovery: the set of sites is whatever that module
lists, so a reader and a test see the same list.

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

The router is a pure function of the judgement task. It returns the primary
leg and the fallback leg, and the port runs the fallback once when the
primary fails or abstains. Serves: constraint "16 GB of RAM" (no resident
model) and Mission item 6 (a failed vendor call does not become a frontier
call).

1. Every judgement task routes to the primary leg, Jev, with the
   open-weight leg as its fallback. A task whose inputs include an image
   routes to OpenAI's Decisions API instead, with the same fallback.
2. The fallback runs once, on the same inputs, when the primary returns a
   transport error, a timeout, a malformed answer, or an abstain.
3. When both legs fail, the port returns no label and says why. The
   consumer applies the task's `fail_safe` label, and the ledger records the
   failure. The port never invents an answer.
4. There is no third leg. A judgement never falls back to a frontier model,
   because a fallback that costs a hundred times the primary would turn a
   vendor outage into a budget event.

No leg runs resident on the Mac. The design hosts the open-weight fallback
on an endpoint: on 16 GB, beside Postgres, one container runtime, one
`claude -p`, and the bridges, there is no room for a resident classifier
worth running (`docs/machine.md`). Which provider hosts it, or whether a
local copy loads only while no turn holds the slot, is open (see "Open").

Models are pinned by exact version on both legs. A version change is a new
calibration record before the task routes to it.

## The `JudgementPort`

The port is one async call in `core/`:

```python
judge(task: JudgementTask, inputs: dict[str, str]) -> Judgement
```

A `Judgement` carries `site`, `label` (absent on failure), `probabilities`
(label to probability), `confidence`, `abstained`, `leg` (`primary` or
`fallback`), `model` (the pinned version that answered), `usage`,
`cost_usd_micros`, and `error` when both legs failed.

The adapters live in `tools/`, one per vendor, because they are
vendor-dependent and the core runs without any one of them
(`tools/README.md`). Each adapter turns a `choice` or `boolean` judgement
task into its vendor's request and the vendor's answer back into a
`Judgement`. Serves: constraint "Three tiers".

- **Primary: Jev.** Jev answers a closed-choice question with a
  probability per option and a confidence, and a boolean question with a
  probability; that is the shape the port's `Judgement` mirrors. Request
  text from client repositories goes to Jev like any other input (Tom,
  2026-10-01).
- **Images: OpenAI's Decisions API.** Jev takes no images, so a judgement
  whose inputs include one goes here (Tom, 2026-10-01). Its request and
  response shapes are not verified here; the adapter is written with the
  first judgement task that needs an image.
- **Fallback: a hosted open-weight model.** Prompted with the same question
  and labels, asked for structured output with a probability per label. A
  generative model's stated probabilities are a weaker signal than a
  decision endpoint's, so the fallback's confidence floor is its own,
  measured in the same calibration record as the primary's.

Every judgement call is metered against the money budget of the task it
serves, reserved before the call and charged after, with a ledger row for
each, the same way the gateway meters a turn's model calls. Serves:
constraint "Bounded authority and spend". The current gateway meters
Anthropic Messages calls only; metering the judgement legs through the same
reservation and charge in `core/budget.py` is design.

Each call writes one `judgement.answered` row to the ledger: site, label,
probabilities, confidence, abstained, leg, model, cost, and a digest of the
inputs. A failure writes `judgement.failed` with the reason. Serves: Mission
item 6 and the constraint that the ledger records every effect; a judgement
is not an effect, but the routing it causes is, and the row is how a later
reader sees why the kernel chose what it chose.

## Confidence gating

Each judgement task declares a confidence floor and an abstain route. Under
the floor, the judgement abstains on the primary leg, the fallback answers,
and if the fallback also falls under its floor, the consumer takes the
abstain route. Serves: constraint "Three tiers" ("confidence-gated to a
human") and Mission item 6.

Gating to a human spends Tom's attention, and Mission item 6 counts that
attention on the same footing as money. So an abstain route is chosen by
what it costs Tom, and every route names that cost:

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

Every abstain and every fail-safe that reaches Tom is counted in the attention
ledger with its site, so a site whose abstains cost attention shows up there
(`docs/mission.md`).

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
of each leg; the question text's digest; and the date.

**Both legs, one run.** A task lands only on a record in which both legs
answered the same inputs in the same run. A fallback proven on older inputs
is not proven.

**The bar.** A landing needs, on the record: `n` at or above the tier's
minimum, a Brier score at or under the tier's ceiling for each leg, and
every high-cost error class (the label whose mistake costs Tom a round of
work) under its ceiling. The numbers per tier are a gap: no source fixes
them, and the first ones are set by Tom from the first records.

**Where labels come from.** The emulator's human-originated cases, labelled
by Tom's recorded decisions (`docs/emulator.md`), and the attention ledger:
every answer and every piece of feedback labelled by whether it changed the
outcome is a free label for the underspecification classifier (use shape
5), drawn from attention Tom already spent. No labelling session is asked of
Tom.

**Recalibration.** A change to a task's question, labels, inputs, or either
leg's pinned model is a new record before the change routes work.

**Shrinking on evidence.** Each task's live judgements are scored as their
labels arrive. A rolling Brier score past the record's ceiling sends the
task to its abstain route on every call until Tom restores it. The kernel
does this, with a ledger row; nothing about it waits for a review. Growing
back is Tom's decision.

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
| 1 | Request underspecification | Does this request leave a choice that would change what gets built to Tom's head? | `precise`, `one_line_ask`, `example_as_spec`, `existing_ui_unscoped` | yes | Mission 3, 6 | Granted by Tom 2026-10-01; first task, below |
| 2 | Inbound message kind | What is this inbound message, where threading has not already said? | `new_request`, `answer`, `feedback`, `steering`, `conversation`, `no_action` | no | Mission 1, 6 | Design |
| 3 | Request kind | Is this a build, a direct action, or conversation? | `build`, `direct_action`, `conversation` | no | Mission 1 | Design |
| 4 | Feedback kind | Does this feedback correct a prior action, praise one and say why, or neither? | `correction`, `exemplar`, `neither` | no | Evidence: Tom's feedback, both directions | Design |
| 5 | Answer materiality | Did this answer or feedback change what was built, or the authority it needed? | `outcome`, `authority`, `both`, `neither` | no | Mission 6; Evidence: attention spent | Design |
| 6 | Governance over a diff | Does this diff add a check, gate, hook, round, or review step? | `boolean` | yes | Constraint: governance restrained by structure | Required by the plan |
| 7 | Doc against reality | Does this doc state something this code contradicts? | `consistent`, `contradicted`, `unverifiable` | yes | Constraint: docs describe reality | Required by the plan |
| 8 | Test breadth | Does the change imply a case its tests leave unexercised? | `covered`, `gap` | yes | Mission 1 | An SDLC stage by Tom's decision of 2026-10-01 |
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
6. The blind verifier's one boolean. A `true` with no grant is a refused
   merge. The kernel's broker already refuses a flagged action without a
   grant; this judgement is what sets the flag from the diff.
7. The plan's "cheap judgement sweeps". A `contradicted` label opens a task
   to fix the doc or the code; it blocks nothing by itself.
8. The baseline: every replay wrote fewer tests than its reference, and the
   hidden tests found what that missed (rebuild-baseline.md, "Test
   breadth"). The `breadth` state runs the suite deterministically, then
   this judgement; a `gap` label yields the state's `gaps` verdict
   (`docs/sdlc-state-machine.md`).
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
| `precise` | The request states what to build and its scope; the choices left open can be settled by reading the code | build: the first turn's Brief carries the request alone (`bare`) |
| `one_line_ask` | A single line naming a feature or change, with its intent left to Tom's head (#191, #188) | clarify |
| `example_as_spec` | The request leans on an example that may stand for a wider requirement (#894) | clarify |
| `existing_ui_unscoped` | The request names existing UI to change or add beside, without saying what happens to what is there (#894's first-name chip) | clarify |

The three thin labels route the same way. They are kept apart because the
calibration record reports each, and because the clarify turn's questions
differ: an `example_as_spec` request most needs "is the example the whole
requirement?", the demonstration's question 1.

A clarify route starts the first turn in the `clarify` mode the kernel
already has (`core/signals.py`, `CLARIFY`): Valor inspects without editing
and sends one message holding its material questions, each with the answer
it will assume, and its intended approach. That keeps Mission item 3's
order: the message is a proposed first version of the decisions, not a
questionnaire.

### Confidence gating

The decision is on the probability of `precise`. At or above the floor the
request builds; under it, the request goes to clarify. An abstain and a
failure of both legs also go to clarify. The clarify message is the human
gate: Tom sees the questions and the intended approach and answers about the
work, and a one-word "go" costs him one message.

The asymmetry behind that choice, from the records: a thin request built
bare cost the demonstration two PM rounds, #188 one PM round and twice the
spend, and #191 a delivery at fidelity 1 that a lenient reviewer accepted. A
precise request sent to clarify cost one question message and, on the
baseline's means, no money ($1.42 per run on either arm, rebuild-baseline.md,
"Aggregate"), with #633 as the one case where it hurt. The floor leans
toward clarify by that asymmetry. Its value is set from the calibration
record and is a gap until the record exists.

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
  by shape 5 adds a case: an answer that changed the outcome confirms a thin
  label, a "Your call" on every question is evidence the request was
  precise. Every bare build whose first PM feedback changed the scope is a
  missed thin label. The baseline appendix shows both kinds already: #872's
  questions 3 and 4, #646's question 2, and #893's question 1 drew "Your
  call", while #191's question 2 and #188's answer redirected the work
  (rebuild-baseline.md, "Appendix").
- **The emulator measures the saving.** The demonstration's estimate ($1.40
  to $1.80 of $2.97 saved, plus both review rounds) is an estimate. The
  emulator replays the seed cases with and without the classifier and
  reports the difference in money and in PM rounds (`docs/emulator.md`).

### Where it runs

The SDLC opens with this judgement, after intake and before the plan
(`docs/sdlc-state-machine.md`). The call runs after the task record exists
and before its first turn, charged to the task's budget, and its
`judgement.answered` row sits in the task's ledger ahead of `turn.started`.
The kernel maps the label to the first turn's mode. The current kernel takes
the mode from `core start --mode`, written into the Brief at start;
recording it from the judgement is design.

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

**Governance is restrained by structure, not sentiment.** Before any check,
gate, hook, validator, review round, or approval step is added, the change
names the mission item it serves and the incident that already happened
without it; missing either, it is not added. A bug fix never adds a guard;
it fixes the code. Adding governance is an `act`-class effect: the Brief
carries a `governance_grant` field, default none, and a diff that adds any
of the above needs Tom's tap, one approval per instance, through the same
approval surface as a merge or a send. Every guard is ledgered with the
incident it prevents, the mission item it serves, and a ninety-day expiry; a
guard that has not fired by expiry is deleted by default. The blind verifier
asks one Jev-class boolean over every diff, "does this add a check, gate,
hook, round, or review step", and a yes with no grant is a refused merge.
The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in
the persona rendered into every turn, and in the Not-here section of every
directory README. No restraint skill, no hook that blocks hooks, no
governance dashboard: each is the disease presenting as the cure.

- Prose generation, summaries, extraction: agent work on a frontier model.
- Acceptance of a delivery: the blind verifier (`docs/architecture.md`).
- Any model resident on the Mac.
- A frontier model as a judgement fallback.
- A judgement that grants, widens, or refuses authority.
- A per-site settings switch, a shadow route, or a second entry point
  beside `JudgementPort`.

## Open

- Which provider hosts the open-weight fallback, or whether a local copy
  loads only while no turn holds the slot.
- The tier bars: minimum `n` and Brier ceilings per error-cost tier.
- What happens at expiry to a guard that did fire: renewal by a new grant,
  or kept until it stops firing.
