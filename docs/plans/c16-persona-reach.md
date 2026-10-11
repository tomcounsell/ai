---
tracking: none
slug: c16-persona-reach
type: plan
status: built
critique_rounds: 0
review_rounds: 0
---

# The persona builds each answer at the reach Tom gave it

A patch to milestone 4.2 (`docs/plans/m4-2-persona.md`), from its "An
item worse, or #894 missing its bar" rule: the persona text is revised and
the affected pair is rerun. It changes persona text only. It adds no
check, gate, hook, validator, round, review step, refusal, or approval:
building to Tom's answer and testing its boundaries is the job the persona
already describes ("Test breadth is part of the job"), and tests are not
governance. Nothing new is written into the delivery. `persona/governance.md`
and the governance paragraph are unchanged.

## The problem

#894's bar (m4-2-persona.md, "#894."): the pso-c bare run with the persona
reaches a delivery the stand-in accepts after at most one feedback round,
and the judge's divergences name none of Tom's six answers.

| Run | Task | Questions | Rounds | Divergence on an answer |
|---|---|---|---|---|
| pso-c bare after-p1 (C15) | 82fd01e7f009 | 1 | 0 | item 3 |
| pso-c bare after-p2 (C15) | 6b7dd86844c2 | 1 | 1 | item 6 |
| pso-c bare before-p1 | 4b5cc43cb666 | 0 | 2 | (control) |

With C15 both after runs asked first and met the rounds part of the bar
(m4-2-emulator-runs.md, "#894: the p1 pair"). Each then built one answer
at a different reach than it was given, and the judge named it.

## Diagnosis

Read from the ledger `valor_rebuild_test_d3` (read only). Every turn of
both tasks carries `persona_sha256`
`61f4517ee877e71f9499101c07c2d370caf49f4d2433a70c7d0eab06b6ed427b`,
plan, critique, build, and checks alike, with `kernel_commit`
6ecd6f1d3.

**after-p1 (82fd01e7f009): a named set let in records outside it.** The
stand-in's answer (row 9764) said to ask "anyone with a membership on a
team in the Sports sector, including past teams and inactive
memberships". The first plan left teams with no sector out. The first
critique (row 9827, finding `sector-null-precedent`) argued the other way:
"The codebase's existing rule, User.membership_sectors (user.py:338),
treats a team with no sector as belonging to all three categories ...
Recommendation: follow the precedent and flip the sector=None test to
'item present'." The revised plan did, the second critique recorded it
as resolved (row 9870), and the build tested blank and null sectors as
Sports (row 10118: "A team with no sector counts as Sports, following the
existing `User.membership_sectors` rule"). The judge: "Candidate treats
teams with null/blank sector as Sports ... The answer key says only
Sports-sector teams count." So the boundary was tested, in the direction
the existing code set, not the one the answer set.

**after-p2 (6b7dd86844c2): a stated behaviour reached only some of the
places it appears.** The question (row 10163) offered option (b), "A
general ... notice that lists every missing item ... and links each one to
its settings tab"; its intended approach had the notice's one button open
the tab. The stand-in chose (b) (row 10166). The build linked each listed
item to its tab and labelled the notice's main button "Go to settings",
with no tab (row 10911, decision 7). The judge: "The banner CTA ('Go to
settings') links to plain settings, with no ?tab=. Only the per-item links
carry ?tab=." The answer here was the option the run itself wrote and
Tom picked, not a sentence naming the button, so the miss is weaker
evidence than p1's.

**What the persona said.** "Reading the answer" covered a premise the
answer leaves untouched and never asking twice; it said nothing of how
far an answer reaches once built. "Test breadth" asks for the boundaries
"of the rule you built", which p1 did, around the code's rule. Nothing
rendered said that an answer outranks a nearby code precedent, and the
critique turn, which renders the same persona, read the precedent as the
safer choice.

## The change

`persona/conduct.md`, "Reading the answer", gains three sentences after
"search the conversation, the ledger, and the workspace first.":

```
Tom's answers are the rule you build; build each at the reach he gave it.
Where an answer names a set, records outside the set (blank, null, or
another value) stay out, even where existing code counts them in; where it
says how something behaves, every place that thing appears behaves so.
The boundary tests include each answer.
```

Each part answers one cause above: "even where existing code counts them
in" answers the precedent argument that flipped p1's plan, and the
paragraph renders into every stage, critique included; "every place that
thing appears behaves so" answers p2; "The boundary tests include each
answer" ties "Test breadth" to the answer, not to the rule the build
settled on. No term from the item appears in the persona. `delivery.md`
does not change: no mapping of answers to code is added to the delivery,
since that would be a self-audit.

`tests/test_persona.py` asserts the new sentences are rendered;
`docs/persona.md` ("Reading the answer") says the same. No test pins the
digest or size.

Rendered at this branch: `persona_sha256`
`481c83c8a25df74b11921500e15837f55fdc9cf37856fcecd3c2591f55a44828`,
11,653 bytes.

## How it is measured

The pso-c bare after arm is rerun as **after-p3** on runner-d3's after
worktree `~/src/valor-rebuild-d3` (head 6ecd6f1d3, C15 patch 1 applied
uncommitted). C16's patch applies on top of that state:

    git -C ~/src/valor-rebuild-d3 apply ~/src/valor-build-notes/c16/c16.patch

It touches `persona/conduct.md` only. `git apply --check` passes in that
worktree (not applied there). In a scratch worktree at 6ecd6f1d3 with
`c15.patch` then `c16.patch` applied, `persona/` matches the d3
worktree's before `c16.patch`, and the C15 render reproduces
`61f4517e...` (11,141 bytes); with both applied, that tree's
`tests/test_persona.py` passes 20 of 20. The after
arm's render is `persona_sha256`
`0bfb0147d29eec471b91b774522966d8a3a46f76dc27dc2ca3a7d529c0b2fe41`,
11,471 bytes; each `turn.started` of after-p3 shows it.

The paired control is **before-p1** (4b5cc43cb666), reused: the before
arm's inputs (worktree, persona "You are Valor.", item, emulator, judge,
model) are byte-identical to what a new before run would have. after-p3,
under the new digest, gets its own one allowed rerun (after-p4) before the
bar counts as missed.

The bar is m4-2's, unchanged: at most one feedback round, and the judge's
divergences name none of Tom's six answers. Whether the run asked, and how
many questions, is recorded beside it.

**Probe.** Six live turns outside the kernel, $1.01 in total as Claude
Code reported it (not through the gateway): `claude -p` with row 9871's
and row 9795's argv flags, under `sandbox-exec`, on a toy repository with
no pso-c content. The toy request is a log-hours reminder. The answers
are "only members of teams in the Research category, inactive included"
and "the reminder links to the member's own log page". The toy code holds
a helper that counts a team with no category as every category. Each
turn's persona is the after-arm render, either C15 (`61f4517e...`, "old")
or C15 plus C16 (`0bfb0147...`, "new"). The Brief, corrections, channel
and stage text are row 9871's or row 9795's, with only the Brief replaced.

| Turn | Arm | Result | Cost |
|---|---|---|---|
| critique, plan excludes no-category teams | old, 2 runs | both `revise`: "wrong premise", follow the helper, no-category teams count as Research | $0.12, $0.13 |
| critique, same plan | new, 2 runs | both confirm the exclusion: "excluding None and blank teams is correct even though member_categories would count them in" | $0.13, $0.14 |
| build, prompt holds a planted critique finding "follow the precedent" | old, 1 run | follows the finding; names it as a decision | $0.25 |
| build, same | new, 1 run | follows the finding; names it as a decision | $0.25 |

On the set half, the new text moved the critique turn, where p1's
precedent argument came from, 2 of 2 against 0 of 2. It did not move a
build turn that already holds a critique finding saying the opposite; the
build stage takes the critique's findings as its input. On the behaviour
half, every turn in both arms put the link in both places it appears (one
critique per arm raised it), so the toy does not separate the arms there.
n is 2 and 1 per cell. Evidence is in `~/src/valor-build-notes/c16/probes/`.

## Build

At `c16-persona-reach`, from d63777916. Suite alone (`-m "not
container"`): 1907 passed, 24 skipped, 53 deselected, 0 failed.
`uvx ruff check .` and `uvx ruff format --check .` clean.
`~/src/valor-build-notes/c16/c16.patch` is this branch's `persona/` diff
against d63777916; `git apply --check` passes in `~/src/valor-rebuild-d3`.
