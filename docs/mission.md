# Mission

The canonical statement of what Valor is for, what it may never trade away,
and how anyone knows whether it is working. Other docs cite this one by item:
"Mission item 3" means item 3 of the Mission below, and a constraint is cited
by its bold name. A mechanism anywhere in the repository names the mission
item it serves or the constraint it enforces; one that names neither is not
in the design.

The mission is not ranked. Ranked goals invite a system to optimize toward
whichever goal sits first, and with corrigibility first that is a system that
spends indefinitely on becoming more governed. So the mission is the
direction, the constraints are guarantees to verify, and the evidence is what
counts as knowing. Corrigibility is a set of guarantees to verify, not a
direction to push.

## Mission

Turn Tom's intent into working things worth using. Own the journey from an
incomplete idea to a finished result, exercise taste along the way, and
compound the ability to build. Concretely:

1. **Own outcomes across the whole job.** "Build this" includes understanding
   the problem, inspecting what exists, choosing an approach, implementing,
   testing actual use, delivering within authority, and resolving discovered
   defects. Tom never coordinates the gaps between those steps.
2. **Contribute taste and invention.** Identify the simpler design, challenge
   an unnecessary requirement, produce a concrete alternative when the brief
   leaves room. Meeting expectations is the floor.
3. **Absorb ambiguity through making.** For reversible decisions: inspect,
   infer, prototype, show. Ask only when the answer materially changes the
   outcome or the authority required. An unclear brief produces a useful
   first version before it produces a questionnaire.
4. **Make larger undertakings tractable.** Capability growth means Tom can
   delegate increasingly substantial problems with less supervision: a
   feature, then a workflow, then a product.
5. **Compound leverage through completed work.** Each project may leave
   behind a tool, a tested technique, a reusable component, or knowledge of
   Tom's preferences. Extraction happens only on a demonstrated second need,
   never on a first, and anything unused after ninety days is deleted by
   default. This is the constraint that keeps `tools/` and `skills/` from
   regrowing the old system's sprawl.
6. **Spend attention as carefully as money.** A task carries an attention
   budget beside its dollar budget. Valor carries routine decisions,
   investigates failures, and brings consequential choices with evidence and
   a recommendation. Requiring Tom to adjudicate internal process is a
   product defect.

Item 3 takes its shape from Cooperative Inverse Reinforcement Learning [3]:
when the agent does not know the reward it shares with the human, learning
and asking are the rational moves. Valor borrows the shape without the
guarantee, since nothing here is trained, and item 3 bounds the asking:
a question earns its place only when its answer changes the outcome or the
authority.

## Constraints

Mandatory, verified, and never a place to spend surplus energy.

- **Bounded authority and spend.** Every task carries a money budget and an
  effect ceiling, conserved down the tree. A classifier decides what a
  thing is; the kernel decides what it may do. Cheaper capability does not
  lower the ceiling: it buys better outcomes or lets Tom authorize larger
  undertakings within the same budget. Scope expansion is Tom's choice;
  capability gains create that choice.
- **Reliable stop, recovery, and correction.** Stop is immediate and lossless.
  A ledger the system cannot edit records every effect. Corrections are
  first-class, carry provenance, and reach every session and agent.
  Autonomy shrinks automatically on evidence and grows only by Tom's
  decision.
- **Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.
- **Docs describe reality.** Enforced by a blind verifier reading the doc as
  the contract and by cheap judgement sweeps, never by the model grading its
  own narration.

The theory behind the first two constraints (least privilege [11], the
control stance [4], an off switch that lives outside the model's incentives
[8, 9, 10]) is set out in the README's corrigibility section. How the kernel
enforces them is `docs/architecture.md`. The platform constraints that every
doc respects (16 GB of RAM, Mac native, Postgres as a document store, one
identity, three tiers) are owned by `docs/machine.md`, `docs/tech-stack.md`,
`docs/data.md`, and `docs/persona.md`.

### How "bounded spend" is read

A budget bounds what the gateway meters, and every call a turn makes is
pointed at the gateway. It is not a wall: a turn that deliberately called
the provider directly with the machine's Claude login would spend outside
it, visible only on the provider's invoice. Tom accepted that risk on
2026-10-01 and chose not to close it, with no separate macOS user for
turns: budgets are for visibility and honest metering. Bounded authority is
unchanged by this. Effects on shared targets leave only through the broker,
and every `act` waits for Tom's tap.

### What the governance constraint means in practice

The kernel holds the constraint as a fact from the first turn: the Brief
carries `governance_grant`, default none (`core/tasks.py`), and the broker
treats any action that adds governance as `act`, refused without a grant and
held for Tom's tap with one. The blind verifier's boolean over every diff is
part of the design; the current kernel has no verifier.

A guard Tom grants is ledgered with three things: the incident, the mission
item, and the expiry date ninety days out. The first guard granted under
this constraint is the request judgement step described under
[First evidence](#first-evidence-the-demonstration-and-the-baseline) below.

## Evidence

What counts as knowing the mission is being met:

- **Working results in real use.** The thing runs, someone uses it, and
  defects found in use get resolved without Tom coordinating.
- **Tom's feedback, both directions.** The corrections ledger records what
  went wrong. An exemplar ledger, same store and a distinct source class,
  records work Tom loved and why: an excellent simplification, good taste,
  initiative, unusually complete delivery. Without the second, the system
  learns to avoid mistakes and never learns to build anything exceptional.
- **Independent checks.** Blind verification, the emulator's human-labelled
  cases, and the audit sample.
- **Attention spent.** Decisions escalated to Tom per finished task, logged
  from the first demonstration onward. It is the one outcome number the
  system cannot perform against: escalating less while failing shows up in
  the result.

### How each kind of evidence is read

**Working results in real use.** Read per delivery: did it run, did someone
use it, and was each defect found in use resolved without Tom coordinating.
A replay of a shipped feature is not real use; it is an emulator case. No
delivery so far has been used (rebuild-demonstration.md, "What was
delivered"), so this evidence is empty.

**Corrections and exemplars.** Both are rows in one store, told apart by
source class: `direct` for a correction Tom gave, `exemplar` for work he
loved and why (`core/corrections.py`). Every turn renders the corrections in
force from the ledger as it starts. A correction is read as a lasting
change in what Valor does: the test of one is that the next task behaves
differently. An exemplar is read the same way in the other direction. The
current kernel records `direct` corrections from the command line; the
`exemplar` class exists in the store and nothing records one yet. How the
store is laid out is `docs/architecture.md`; this doc owns what the two
ledgers count as evidence of.

**Independent checks.** Three, and none is the executor grading itself
[16]:

- The blind verifier reads the request and the result, never the executor's
  narration (`docs/architecture.md`).
- The emulator replays human-originated historical requests, labelled by
  the human merge or review decision (`docs/emulator.md`). The demonstration
  and the baseline below are its first cases.
- The audit sample is Tom reading a fraction of verified work himself, the
  only check on the cheaper checks [4, 17]. Verifier calibration is scored
  against it as a Brier score with its sample size [12].

A judge's score is a proxy, and an optimized proxy drifts from what it
stands for [14]. No score is reported alone: a fidelity number comes with the
hidden-test result and the attention it took.

**Attention spent.** Read through the attention log, below.

## The acceptance question

Can Tom give Valor a consequential, imperfectly specified goal and return to
something that works, reflects good judgement, and needs less of his
attention than doing it himself?

Every mechanism in the rebuild answers to that question. Successfully
navigating an SDLC does not.

The question has three clauses and each has its evidence: "something that
works" is real use and the independent checks; "reflects good judgement" is
Tom's feedback in both directions; "less of his attention" is the attention
log. A delivery that passes every check and took more of Tom's attention than
building it himself fails the question.

## The attention log

Mission item 6 puts attention on the same footing as money, so attention is
a ledger item, not only a reported number. Every point where Tom acts on a
task is a ledger row with its provenance, and the task's attention is a fold
over those rows, the same way its spend is a fold over gateway charges.

### What is logged

| Kind | Ledger row | What it records |
|---|---|---|
| Question | `question.asked` | Valor's question, the turn that asked it |
| Answer | `question.answered` | The answer, bound to its question by id, with provenance |
| Feedback | `feedback.given` | Feedback on a delivery, the delivery it answers, with provenance |
| Approval | `approval.granted` | Tom's tap on one held `act` effect, bound to that effect's payload digest, with his literal note |
| Correction | `correction.recorded` | A correction or exemplar, global, numbered, with provenance |

Provenance on every row Tom (or someone for him) writes: `by` (who wrote it),
`via` (the surface it came through), `at`, and `role_played`, true when
someone stood in for Tom. A role-played row is still attention the system
required; it is not live attention from Tom, and the log keeps the two apart.
The flag exists because the demonstration's second feedback round was
role-played and its row read `"by": "tom"` (rebuild-demonstration.md, kernel
finding 5), and corrections must carry provenance (**Reliable stop,
recovery, and correction**).

What the current kernel does: `question.answered` and `feedback.given`
carry `by`, `via`, `at`, and `role_played`, and `python -m core status`
returns the task's `attention` list (questions with their answers, and
feedback) in ledger order (`core/session.py`, `core/tasks.py`).
`approval.granted` carries `by` and the note but no `role_played`, and
approvals are not yet in the `attention` fold. Corrections carry `by`, `via`,
and `at`, and are global rather than per task. Approvals with `role_played`,
and approvals in the fold, are part of the design.

### How it is read

Each entry is classified on three axes after the task finishes:

1. **Kind of work.** *PM work* is judging and steering the product: an
   answer to a question about intent, feedback that sends a delivery back.
   *Authority* is a tap on an `act` effect: a merge, a send, a push. The two
   are counted separately. Authority taps are the price of **Bounded
   authority and spend** and are not a defect. PM work Valor could have
   carried is the defect Mission item 6 names.
2. **Whether it changed the outcome.** A question whose answer changed what
   got built met the bar Mission item 3 sets. A question whose answer
   changed nothing cost attention for nothing. A feedback round that changed
   the outcome marks a question Valor should have asked before building.
3. **Whether it changed the authority required.** An answer that widened or
   narrowed what Valor may do.

In the demonstration the record's author classified axes 2 and 3 by hand
against Tom's reference answers. In the design a judgement does it (use
shape 5, answer materiality, in `docs/judgement-layer.md`), and Valor
proposes the label in its next delivery for Tom's correction to override
(`docs/architecture.md`, The attention log).

Read per finished task, the log answers: how many decisions reached Tom,
of which kind, how many of those were his to make, and how many rounds a
delivery took. Read across tasks, it is the trend Mission item 4 needs: the
same class of request reaching Tom less often.

The log is always read beside the result. Fewer questions with a worse
result is not progress, and the result shows it: that is what makes attention
hard to perform against. A count of questions alone is a proxy and would
drift [14].

### The attention budget

Mission item 6 gives each task an attention budget beside its money budget.
The current kernel's only budget unit is money (`core/tasks.py`, `Brief`);
attention is recorded and reported. In the design the Brief carries
`attention_budget`, counted in escalations (questions, feedback rounds,
approvals). The kernel never refuses a question for exceeding it, because a
refused question makes Valor guess, which costs more attention later;
crossing it is a ledger row, shown on the delivery. The mechanism is
`docs/architecture.md`'s (The attention log). Refusing or stopping on the
attention budget would be a gate, and under the governance constraint it
would need an incident and Tom's grant. Whether escalations are the right
unit is open (`docs/plans/rebuild-open-questions.md`).

## First evidence: the demonstration and the baseline

Two records hold the first numbers: the demonstration
(`docs/plans/rebuild-demonstration.md`) and the baseline
(`docs/plans/rebuild-baseline.md`). Both ran inside the minimal kernel on
Claude Opus 5.5 with every model call metered by the gateway, effect ceiling
`act`, and no governance grant.

### The demonstration: psyoptimal #894

Tom's request, verbatim: "Home page notification if user profile settings not
complete. Example: At least one of the **Sports Career Start Dates** should
be completed."

| Measure | Value |
|---|---|
| Questions Valor asked | 0 |
| PM feedback rounds | 2: round 1 from Tom, round 2 role-played from his recorded answers |
| Authority taps | 3 pushes to the local bare origin, approved and released |
| Deliveries | 3; delivery 3 matched all six of Tom's reference answers |
| Turns | 4, one failed and metered $0 |
| Model calls | 69 |
| Spend | $2.97 of a $15.00 budget |
| Used | No; a replay of a shipped feature |

Valor inferred three of Tom's six decisions and missed three, and the two
feedback rounds carried exactly those three. Delivery 1 read the example as
the whole requirement and covered one of seven profile items. One message
before building, holding three questions, would have collapsed three rounds
into one (rebuild-demonstration.md, "Attention log"). Rounds 2 and 3 cost
$1.74.

Read against the mission: delivery 1 was a useful first version, as item 3
asks, but it did not name the reading that mattered most, so the attention
cost landed as PM rounds instead of one short question. Under the rule that
Tom does nothing but answer Valor's questions, the demonstration would have
ended at delivery 1, which Tom judged wrong on its most important line.

### The baseline: six replays, two arms

Six requests from Tom's recent work, each replayed twice from a clean base:
**bare** (the request alone) and **clarify** (the Brief asks Valor to
inspect, then send its material questions and intended approach before
editing). A Sonnet stand-in for Tom answered from answer keys and reviewed
each delivery; a blind Sonnet judge scored fidelity, correctness, and
simplicity from 0 to 5.

| | Bare | Clarify |
|---|---|---|
| Question messages (numbered questions) | 0 | 6 (19) |
| PM feedback rounds | 1 | 1 |
| Fidelity, sum over six | 22 | 23 |
| Correctness, sum | 23 | 20 |
| Simplicity, sum | 22 | 21 |
| Kernel spend | $8.53 | $8.55 |
| Wall time | 85 min | 49 min |

Read per item, not in aggregate (rebuild-baseline.md, "Clarify"):

- **One-line asks.** popoto #191: bare scored fidelity 1 and passed 3 of 11
  hidden tests; clarify asked where `push()` lives and reached fidelity 3 and
  7 of 11. popoto #188: bare built the feature and needed a PM round to pivot
  to docs; clarify was told before building and finished at fidelity 5 for
  half the spend.
- **Precise requests** (#872, #646, #893). Clarify asked sensible questions
  and changed nothing in the outcome.
- **A wrong premise** (#633). Clarify's question carried a wrong premise,
  the answer did not correct it, and the build reproduced a variant of the
  defect Tom's original review caught. Bare avoided it unprompted.

### How the first numbers are read

Questions are not a cost to drive to zero. A question that changes the
outcome is the cheapest form of the attention Tom spends anyway; a feedback
round on a wrong delivery is the most expensive form of the same decision.
The demonstration and #191 and #188 show the expensive form; the precise
requests show questions that bought nothing.

That reading is the incident behind the first guard Tom granted: a request
judgement step. A cheap Jev-class classifier reads each incoming request and
routes underspecified ones (one-line asks, asks that lean on an example, asks
naming existing UI without scope) to a clarify turn; precise requests go
straight to build. It is ledgered with its incident (the demonstration, task
`32f800bce8a2`, and the baseline runs on #191 and #188), the mission items it
serves (3 and 6), and a ninety-day expiry. The classifier is
`docs/judgement-layer.md`; where it sits in the work is
`docs/sdlc-state-machine.md`. The demonstration estimated it at well under
$0.05 per request against a saving of $1.40 to $1.80 and both review rounds
on #894; the emulator measures that by replaying the case with and without
it.

The same records are why each stage's instructions are light: neither arm
wrote a plan document, and both reached fidelity 4 on the items whose old
pipeline ran a full plan, critique, and revision chain (rebuild-baseline.md,
"Plan, critique, revise"). They covered six small changes and no large,
high-stakes one, so Tom kept every stage as a checkpoint and let each plan
set how many critique and review loops its stakes earn (2026-10-01). Every
replay wrote fewer tests than its reference, and the hidden tests found
what that missed (rebuild-baseline.md, "Test breadth"). Both readings shape
`docs/sdlc-state-machine.md`.

**Spend.** Money is gateway-metered and exact: the demonstration's harness
and gateway agreed to $0.000024. Each resumed turn carries the whole session
(about 30,000 input tokens on the first call, 109,000 on the last), so a
round of feedback costs more than the first build's share of tokens
suggests. The old system's spend on the same requests is unknown, so these
numbers have no prior to compare against; they are the baseline.

**Limits on what the first numbers show.** One run per item and arm, so a
one-point judge difference is noise. The stand-in was lenient: every run
ended accepted, including #191 bare at fidelity 1. Half the answer keys are
inferred from merged code. No run looked at its result in a browser, so
"testing actual use" in Mission item 1 is unmeasured. No delivery was used,
no exemplar was recorded, and no audit sample exists. Of the four kinds of
evidence, only attention spent and the emulator's first cases have numbers.
