---
tracking: none
slug: c15-persona-894
type: plan
status: built
critique_rounds: 0
review_rounds: 0
---

# The persona asks before building on an example

A patch to milestone 4.2 (`docs/plans/m4-2-persona.md`), from its "An
item worse, or #894 missing its bar" rule: the persona text is revised and
the affected pair is rerun. It changes persona text only. It adds no
check, gate, hook, validator, round, review step, or refusal: asking Tom a
batched question is what the persona already prescribes, and the plan
stage already offers `.valor/question.md`. `persona/governance.md` and the
governance paragraph are unchanged.

## The problem

#894's bar (m4-2-persona.md, "#894."): the pso-c bare run with the persona
reaches a delivery the stand-in accepts after at most one feedback round.
pso-c bare is the run with the judge forced precise, so no clarify stage
runs, and only the persona can lead Valor to ask.

| Run | Task | Questions | Feedback rounds |
|---|---|---|---|
| pso-c bare after (4.2 persona) | d76dfb8bcd02 | 0 | 2 |
| pso-c bare before ("You are Valor.") | aae2e8a72b78 | 0 | 2 |
| pso-c clarify after | 7fff8509fb19 | 1 | 0 |
| pso-c clarify before | ff942c815596 | 1 | 0 |

Both bare arms missed on the same two points: round 1 said the sports
dates were only an example and the banner covers the whole profile; round
2 said the sports item goes to anyone who ever had a membership on a
Sports-sector team, not only individuals on active memberships. Both are
answers one batched question would have drawn, as the clarify arms show.

## Diagnosis

Read from the ledger `valor_rebuild_test_d3`, task d76dfb8bcd02, row 82
(the plan turn's `turn.started`, `persona_sha256`
`f64edfd3fe0e3c0bdd9b29c91d2bd36fee21aee53143246f5031acd204851392`, 10,390
bytes, `kernel_commit` 968e7aa05) and row 124 (its `turn.ended`).

**The question was offered, and the rule was rendered.** The judge
answered `precise` (row 47), so the first turn was `plan`. Its text held
the channel's "A question for Tom goes in `.valor/question.md`; then end
your turn. Ask only when the answer materially changes the outcome or the
authority the work needs.", the plan stage's "A material question found
while planning goes in `.valor/question.md` instead.", and the persona's
"**Ask before building** when a request leans on an example, ... and
another reading would build something different. This applies whenever
the channel below offers a question." No stage on the path ruled a
question out, and the request's example ("Example: At least one of the
**Sports Career Start Dates** should be completed.") is a labelled
example, not one hidden in a list.

**The turn saw the example and judged otherwise.** Its result (row 124):

> **How I read the request:** I treated the sports dates as the first of
> several possible profile checks, so the notice is built as a list. I
> didn't add any other checks, such as birthdate or phone. Built narrowly,
> the visible result would be the same.

and on stakes: "the notice only changes what's displayed. No stored data
is touched, and reverting the commit removes it."

Three sentences in what it received let it do that:

1. The rule's own condition, "and another reading would build something
   different", left the turn to choose which readings to compare. It
   compared a list of one check against a single check ("Built narrowly,
   the visible result would be the same"), not the example as the whole
   rule against the example as one case of a wider rule, which builds
   more. The rule did not say that an example always has that wider
   reading.
2. The paragraph above it, "For reversible decisions, inspect, infer,
   prototype, and show", fit the turn's own stakes sentence (display only,
   a clean revert).
3. The delivery format, "when you built on one reading of a request that
   had others, say which reading and what the others would have built",
   offered build-then-disclose as a sanctioned path, and the turn took it
   to the letter: its first decision listed is the reading.

The before arm (aae2e8a72b78, persona "You are Valor.") had none of these
sentences and did the same: "Only one rule ships, the one from the
request". So the 4.2 persona's rule did not move the bare path at all; the
clarify stage, whose exit is a choice between `question.md` and
`no_question.md` with a reason, is what made both clarify arms ask.

At the rebuild head the channel and the conduct read "Ask Tom only for
vision, priorities, the cost and benefit of a tradeoff in how the company
works, or something only he holds; for anything else, ask the advisor"
(A2). A2's plan records that a request's intent not on the page is
something only Tom holds, but no rendered persona sentence said so, so an
example's reading could also be sent to the advisor, who cannot know it.

## The fix

Persona text only: `persona/conduct.md` gains one paragraph under "Ask
before building", and `persona/delivery.md` one sentence.

```
When a request leans on an example, one reading is always that the
example is one case of a wider rule it does not state; weigh that reading
against the example as the whole requirement, not against a narrower
build of the same example. When the two would build something materially
different and neither the request nor the code settles which, ask Tom:
what a request means is something only he holds. Ask in the first turn
that can, before that turn's own work, in one batch with every other
material question. Once the reading is material, that the work is
reversible, or that the delivery would name the reading you chose, is no
reason to build first.
```

Delivery, after "say which reading and what the others would have
built.": "Naming the reading here follows the question; it does not
replace asking before building."

Each sentence answers one cause above: the wider rule is named as the
reading to weigh, against the example as the whole requirement and not a
narrower build of it (1); reversibility and disclosure are no reason once
the reading is material (2, 3); "something only he holds" sends it to
Tom, not the advisor (the head's wording); "first turn that can, before
that turn's own work" puts it in the plan turn on the bare path; "one
batch with every other material question" keeps it one message per "How
to ask". Materiality is still judged, per Mission item 3: an example
whose two readings build the same thing is inferred and shown, not asked.
No term from the item appears in the persona. No stage text changes: no
stage rules the question out.

`tests/test_persona.py` asserts the new sentences are rendered;
`docs/persona.md` ("When a request is thin", the delivery's decisions)
says the same. No test pins the digest or size; the existing ones
compare `turn.started` against a fresh render.

Rendered at this branch: `persona_sha256`
`d3d7a14f1ea751d8661ca0a0348c75383ed0ed33e229f79f9546033056849b3d`,
11,323 bytes.

## How it is measured

The m4-2 pair rerun, by runner-d3, after its queue (runner-d3.md, "Resume
rules"): d3-pso-c-bare-before-p1 and d3-pso-c-bare-after-p1. The before
arm is unchanged. The after arm is the 4.2 persona on 968e7aa05 plus this
patch.

What runner-d3 applies to its after worktree `~/src/valor-rebuild-d3`
(branch `md3-persona-after`, head 968e7aa05): the patch
`~/src/valor-build-notes/c15/c15.patch`, persona text only
(`persona/conduct.md`, `persona/delivery.md`), with

    git -C ~/src/valor-rebuild-d3 apply ~/src/valor-build-notes/c15/c15.patch

It applies cleanly at 968e7aa05 (checked with `git apply --check` in a
scratch worktree there, and that tree's `tests/test_persona.py` passes, 20
of 20). A cherry-pick of this branch's commit does not fit: the head's
`conduct.md` carries A2's advisor text, which 968e7aa05's channel lacks.
Rendered there, the after arm's `persona_sha256` is
`61f4517ee877e71f9499101c07c2d370caf49f4d2433a70c7d0eab06b6ed427b`,
11,141 bytes; each `turn.started` of the after-p1 run shows it.

The bar is m4-2's, unchanged: at most one feedback round, and the judge's
divergences name none of Tom's six answers. Whether the run asked, and how
many questions, is recorded beside it.

**Probe.** One live plan turn, outside the kernel: `claude -p` with
row 82's argv flags and its dispatched text, the persona swapped for the
968e7aa05 render with this patch (11,141 bytes), in a scratch clone of the
pso-c base 953713a3 under `sandbox-exec` denying writes outside the clone
and `~/.claude`. It wrote `.valor/question.md` and no plan: one message,
three numbered questions, each with a default ("Is the sports-dates rule
the whole requirement, or one example of a wider 'profile incomplete'
check?"; who sees it; the "(Optional)" label), the premise from the code,
and the approach. 10 turns, 47 s, $0.25 as Claude Code reported it (not
through the gateway). n = 1 and no control turn on the old text, so it
shows the text can lead to the question, not how often; nothing here
reruns a precise request to measure over-asking. Evidence in
`~/src/valor-build-notes/c15/` (`probe-p1-question.md`,
`probe-p1-out.json`, `probe-p1-brief.txt`, `probe.sb`; the build's probe,
on the text patch 1 replaced, is `probe-question.md` and its siblings,
$0.26).

## Build

At `mc15-persona-894`, from 6f3d5da64. Suite alone (`-m "not container"`):
1906 passed, 24 skipped, 53 deselected, 1 failed:
`tests/test_provision_restart_gaps.py::test_the_redo_frees_the_dead_attempts_ports`,
a port assertion untouched by this change; its file rerun alone passed 13
of 13. `uvx ruff check .` and `uvx ruff format --check .` clean.

## Patch round 1

The review (`~/src/valor-build-notes/review-c15.md`, addendum) returned
changes: the build's paragraph overreached Mission item 3 ("For
reversible decisions: inspect, infer, prototype, show. Ask only when the
answer materially changes the outcome"). It declared every example
material ("An example always has another reading ... the wider rule
builds more"), made reversibility no reason to build first
unconditionally, and closed on a cost argument that always favours
asking ("a wrong reading costs Tom a feedback round, and a question costs
him one answer").

- The paragraph is the lead's text, quoted under "The fix": the wider rule
  is the reading weighed, materiality is judged, and the reversibility
  clause holds only once the reading is material. The cost sentence is
  gone. The delivery sentence stays.
- `tests/test_persona.py` asserts the new sentences; `docs/persona.md`
  carries the same scope.
- `~/src/valor-build-notes/c15/c15.patch` regenerated from this branch's
  persona diff against 6f3d5da64; `git apply --check` and the apply pass
  at 968e7aa05, and that tree's `tests/test_persona.py` passes 20 of 20.
- Digests rerendered: head `d3d7a14f…`, 11,323 bytes; after arm
  `61f4517e…`, 11,141 bytes.
- The probe was rerun on the new after-arm render: it asked, one batch,
  $0.25 (above).
- Suite alone (`-m "not container"`): 1907 passed, 24 skipped, 53
  deselected, 0 failed. `uvx ruff check .` and `uvx ruff format --check .`
  clean.
