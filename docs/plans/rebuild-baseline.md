---
tracking: none
slug: rebuild-baseline
type: record
status: recorded
---

# Rebuild baseline: six replays, two arms

Six requests from Tom's recent work (psyoptimal, popoto, cuttlefish), each
replayed twice inside the minimal kernel from a clean base commit: **bare**
(the request alone) and **clarify** (the Brief asks Valor to inspect, then
send its material questions and intended approach before editing). The old
system's real record for the same request is the comparison column. The first
demonstration (psyoptimal #894) is included from its own record.

## Setup

- Model claude-opus-5-5 for every turn, metered by the kernel's gateway,
  budget $8 per run, effect ceiling `act`, no governance grant. Pushes went
  only to each run's local bare origin.
- A stand-in for Tom (Sonnet, `scripts/role_play_tom.py`) answered from a
  per-item answer key and reviewed each delivery as project manager, at most
  2 feedback rounds. A blind judge (Sonnet, `scripts/judge_replay.py`) scored
  the final commit 0 to 5 on fidelity, correctness, simplicity against the
  answer key and the merged PR.
- Hidden checks: the reference PR's own test file, dropped into the exported
  final commit and run there (psyoptimal #872, popoto #633, cuttlefish #646,
  popoto #191). Broad suite: the app's relevant suite, reported as failures
  not already failing at the base. #893 and #188 have no usable hidden test
  (exact-copy assertions; docs only).
- Clean bases: #872 at `207457fc` and #893 at `0beab246` (parents of the plan
  commits); #633 at its base `e5190353`; #646, #191, #188 as one squashed
  commit of the PR's base tree minus the plan docs, with no history.
  The repos' own `CLAUDE.md` and `.claude/` stayed in, identical across arms.
- Opaque item names (`pso-a`, ...), mapped in `valor-demo/items/INDEX.md`.
  Everything is under `/Users/tomcounsell/src/valor-demo/`; the long form
  with every question verbatim is `valor-demo/results/SUMMARY.md`.

## Results per item

"Questions" is the number of question messages and, after it, how many
numbered questions the message held. F / C / S is the judge's fidelity,
correctness, simplicity. Kernel spend is gateway-metered Opus; stand-in and
judge spend is Sonnet outside the kernel.

### pso-a: psyoptimal #872: coaches assign evaluations

| Arm | Questions | PM rounds | Outcome | Kernel spend | Stand-in + judge | Wall | Judge F / C / S | Hidden tests | Broad suite | Diff vs reference | Leak suspect |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bare | 0 | 0 | accepted | $2.10 | $0.39 | 36 min | 4 / 4 / 4 | 20/22 | 0 new | +146/-24 (14 files) vs +629/-49 (20 files) | no |
| clarify | 1 msg, 4 Qs | 0 | accepted | $1.37 | $0.30 | 13 min | 4 / 3 / 4 | 20/22 | 0 new | +203/-23 (12 files) vs +629/-49 (20 files) | no |
| bare (rerun) | 0 | 0 | accepted | $1.30 | $0.30 | 21 min | 4 / 3 / 4 | 20/22 | 0 new | +191/-23 (14 files) vs +629/-49 (20 files) | no |

Old system: 2 questions before build (Tom answered both in one issue comment). Stages: plan (skeleton and 4 sections), questions resolved, critique, revision, re-critique, build, test, PR, 3 review rounds (2 changes requested, then approved), merge, plan delete, docs. 8 plan commits on main plus 8 PR commits. Issue to merge 1 h 52 min. +629/-49.

### pop-a: popoto #633: list() hydrates every row twice

| Arm | Questions | PM rounds | Outcome | Kernel spend | Stand-in + judge | Wall | Judge F / C / S | Hidden tests | Broad suite | Diff vs reference | Leak suspect |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bare | 0 | 0 | accepted | $1.32 | $0.16 | 23 min | 5 / 5 / 5 | 14/14 | 0 new | +131/-9 (4 files) vs +321/-11 (4 files) | no |
| clarify | 1 msg, 2 Qs | 1 | accepted | $1.94 | $0.24 | 7 min | 3 / 2 / 3 | 14/14 | 1 new | +206/-12 (3 files) vs +321/-11 (4 files) | no |

Old system: 0 questions recorded; Tom's own Claude Code, no plan or critique. Build, test, PR, 1 review round by Tom (found the stale len() park), patch, merge. 4 commits. Request (ai#2639, 2026-08-07) to merge 29 days; first commit to merge 18 h 16 min. +321/-11.

### cut-a: cuttlefish #646: one bad figure drops the report

| Arm | Questions | PM rounds | Outcome | Kernel spend | Stand-in + judge | Wall | Judge F / C / S | Hidden tests | Broad suite | Diff vs reference | Leak suspect |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bare | 0 | 0 | accepted | $0.48 | $0.91 | 5 min | 4 / 4 / 4 | 8/9 | 0 failing | +54/-2 (2 files) vs +279/-13 (3 files) | no |
| clarify | 1 msg, 2 Qs | 0 | accepted | $0.91 | $0.92 | 6 min | 4 / 4 / 5 | 8/9 | 0 failing | +78/-2 (2 files) vs +279/-13 (3 files) | no |

Old system: 0 questions to Tom (the plan recorded 2 and settled them itself). Plan, build, PR, merge, no review; docs cascade later in #649. 1 PR commit. Issue to merge 5 days; plan to merge 2 days; PR open 1 min 39 s. +279/-13.

### pop-b: popoto #191: capped ListField with push()

| Arm | Questions | PM rounds | Outcome | Kernel spend | Stand-in + judge | Wall | Judge F / C / S | Hidden tests | Broad suite | Diff vs reference | Leak suspect |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bare | 0 | 0 | accepted | $1.57 | $0.16 | 10 min | 1 / 3 / 2 | 3/11 | 0 failing | +379/-2 (4 files) vs +559/-4 (7 files) | no |
| clarify | 1 msg, 4 Qs | 0 | accepted | $1.75 | $0.18 | 7 min | 3 / 3 / 2 | 7/11 | 0 failing | +380/-6 (6 files) vs +559/-4 (7 files) | no |

Old system: 0 questions answered (the plan left 3 open). Plan, build, 2 review rounds by the agent itself (approve with one tech-debt item, then a second pass), review fix, merge. 2 PR commits. Issue to merge 6 h 35 min. +559/-4.

### pso-b: psyoptimal #893: Individual 180 names the missing requirement

| Arm | Questions | PM rounds | Outcome | Kernel spend | Stand-in + judge | Wall | Judge F / C / S | Hidden tests | Broad suite | Diff vs reference | Leak suspect |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bare | 0 | 0 | accepted | $0.93 | $0.14 | 3 min | 4 / 4 / 3 | n/a | 0 new | +167/-40 (6 files) vs +284/-207 (6 files) | no |
| clarify | 1 msg, 4 Qs | 0 | accepted | $1.59 | $0.17 | 12 min | 4 / 4 / 3 | n/a | 0 new | +212/-70 (6 files) vs +284/-207 (6 files) | no |

Old system: 0 questions (Tom's card already listed 3 options). Plan, critique, revision, build, patch, merge, docs. 2 PR commits plus 2 plan commits on main. Issue to merge 15 min; card to merge 20 days. +284/-207.

### pop-c: popoto #188: Meta.namespace (push-back test)

| Arm | Questions | PM rounds | Outcome | Kernel spend | Stand-in + judge | Wall | Judge F / C / S | Hidden tests | Broad suite | Diff vs reference | Leak suspect |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bare | 0 | 1 | accepted | $2.13 | $0.11 | 8 min | 4 / 3 / 4 | n/a | 0 failing | +99/-0 (1 files) vs +149/-316 (5 files, docs only) | no |
| clarify | 1 msg, 3 Qs | 0 | accepted | $1.00 | $0.10 | 3 min | 5 / 4 / 4 | n/a | 0 failing | +182/-1 (4 files) vs +149/-316 (5 files, docs only) | no |

Old system: 0 questions; the plan was a 4-open-question spec to build it. Decision to decline (recorded by valorengels), docs-only PR, merge. 2 commits. Issue to merge 1 h 55 min. +149/-316.

### First demonstration: psyoptimal #894 (profile completion banner)

| Arm | Questions | PM rounds | Outcome | Kernel spend | Judge | Hidden tests | Diff vs reference |
|---|---|---|---|---|---|---|---|
| bare | 0 | 2 (round 1 by Tom himself, round 2 role-played) | delivery 3 matched all of Tom's answers | $2.97 | not judged | not run | +312/-6 (9 files) vs +683/-19 (12 files) |

Old system: 6 questions before build, answered by Tom on the Notion card;
every stage (plan, critique, revise, build, docs, i18n, test, review, patch,
re-review, merge, ship note); 9 PR commits; active pipeline 20 min, request
to merge 174 days. Spend is unknown for every old-system row: the sessions
ran on the MacBook Air and their cost is not readable from this machine.

## Aggregate

| | Bare | Clarify |
|---|---|---|
| Runs | 6 | 6 |
| Question messages (numbered questions) | 0 | 6 (19) |
| PM feedback rounds | 1 | 1 |
| Fidelity, mean (sum) | 3.67 (22) | 3.83 (23) |
| Correctness, mean (sum) | 3.83 (23) | 3.33 (20) |
| Simplicity, mean (sum) | 3.67 (22) | 3.50 (21) |
| Kernel spend, total (mean) | $8.53 ($1.42) | $8.55 ($1.42) |
| Wall time, total | 85 min | 49 min |

Deltas, clarify minus bare: +6 question messages (19 numbered questions),
0 PM rounds, fidelity +1 point summed over six items, correctness -3,
simplicity -1, kernel spend +$0.02. Per item, clarify won on fidelity where
the request was a one-liner (#191 +2, #188 +1), lost on #633 (-2), and tied
on the three precise requests. The spend tie hides two opposite effects:
clarify spent a question turn on every item, and bare paid a PM round on #188
and idle turns on #872. With the #872 bare rerun (no idle turns) in place of
the first, bare totals $7.73 against clarify's $8.55.

## What this says about the old SDLC stages

**Plan, critique, revise.** Neither arm wrote a plan document, and both reached
judge fidelity 4 on the two psyoptimal items whose old pipeline ran a full
plan chain (#872: 8 plan, critique and revision commits). What the chain
added that no arm did was recon-driven hardening nobody asked for: the
archived-team guards in #872, the only 2 hidden tests either arm failed
there. On the four other items the chain bought nothing measurable. The
models make the plan document and its critique rounds redundant for work of
this size; a short statement of intended approach, sent before building,
carries what the plan carried.

**Clarify.** It earned its place where the request was a one-line ask whose
intent lived only in Tom's head. popoto #191: bare put `push()` on the field
class and the judge gave fidelity 1; clarify asked where `push()` lives and
reached 3, hidden tests 3/11 to 7/11. popoto #188: bare built the feature and
needed a PM round to pivot to docs; clarify was told before building and
finished at fidelity 5 for half the spend. Where the request was already
precise (#872, #646, #893) clarify asked sensible questions, included both open
questions the old system put to Tom on #872, and changed nothing in the
outcome. On #633 it hurt: its question carried a wrong premise about the bug,
the answer did not correct it, and the build reproduced a variant of the stale-cache
defect Tom's review had caught in the original. Neither arm pushed back on its own
in the push-back test; clarify only got there because the answer did.

**Review rounds.** The old #872 took 3 review rounds; no replay needed one to
be accepted, and the judge's main finding in both arms (the archived-team
guards) traces to the old system's recon, not to those reviews. The one review in the old record that mattered, Tom's on
#633, found a subtle state bug; bare avoided it unprompted, clarify did not,
and the stand-in reviewer accepted clarify after one round with the bug moved
rather than removed. Review earns its place on stateful, subtle changes, and
it has to be a strong reviewer: the Sonnet stand-in also accepted the #191
bare delivery the judge scored 1 for fidelity.

**Test breadth.** Every replay wrote fewer tests than its reference, and the
hidden tests found what that missed: archived-team guards (#872), the list key
name and hash exclusion (#191), a bound in an existing test calibrated to
the old 2x cost (#633 clarify). The old
pipeline's broad test matrices earned their place; the models did not
produce them unasked.

**Docs.** Replays wrote little documentation; the judge counted that as a
minor divergence each time. Nothing in these runs depended on it.

**Browser use.** No run looked at a page in a browser, including the two UI
items (#872, #893). Not exercised, so not measured.

## Caveats

- n = 1 per item and arm. Differences of one judge point are noise.
- Tom was role-played by Sonnet from answer keys. The stand-in sometimes said
  more than was asked (it quoted Tom's #633 contract in answer to a design
  question) and once leaked meta ("the key doesn't settle"). As a reviewer it
  was lenient: every run ended accepted, including #191 bare (judge fidelity
  1) and #633 clarify (correctness 2).
- Answer keys: #872 and #633 are Tom's words; #646 answers and half of #191
  are inferred from the merged code; #893 has no key ("Your call."); #188's
  decision was recorded by the agent account, author of the call unknown.
- The judge is one Sonnet call per run, blind to the arm; it saw the
  reference diff, which favors the reference's shape.
- popoto is public, the others private but on GitHub. Turns ran with web fetch
  and search off and an empty gh config but with internet access. A scan of
  every tool call in every run's transcript for the PR, issue, GitHub API,
  `gh`, or a PyPI install of popoto found no hit (two regex false positives,
  both editable local installs, cleared by hand). `/tmp` is shared between
  runs; common file names (`/tmp/before.txt`) recurred across items, and
  files from earlier runs of the same item were deleted before each run.
- pso-a bare ran before turns were forced to run commands in the foreground;
  2 of its 3 turns ended idle waiting on killed background tests. A rerun after the fix (row 'bare (rerun)') cost $1.30 kernel, judge 4 / 3 / 4, hidden 20/22; it is not in the aggregate.
- Spend: $18.37 kernel (gateway-metered Opus) plus $4.08 stand-in and judge (Sonnet), $22.46 in all, against a $60 cap. It includes one judge call ($0.10) on a run that failed before any model call (the dash bug below).

## Infrastructure fixed during the series

Committed on this branch, each with a test: (1) `valor-demo/bin` (uv) on
the turn's and verify's PATH, read-only in the sandbox; (2) the sandbox lets
a run stat its ancestors, so uv can build a virtualenv under `~/src`; (3) the
prompt follows `--`, since a request starting "- Create new flag" was read
by `claude -p` as an unknown option and failed the first run; (4)
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, since a `-p` turn that stops kills
its background commands; (5) concurrent runs: slot locks, a Redis server and
a Django test database per run. Outside the repository, pgvector 0.8.1 was
built into `valor-demo/pgext` for the replay cluster only (cuttlefish needs
`vector`).

## Appendix: questions asked and the stand-in's answers

Bare runs asked nothing. Each clarify run sent one message; its numbered
questions (headlines) and the answer, condensed. Verbatim in SUMMARY.md.

- **pso-a**: (1) Can Coaches revoke? (2) assign core values? (3) Coach
  cascades to sub-teams, intended? (4) Keep assign for people granted
  MANAGE_EVALUATIONS directly? Answer: 1 and 2 yes, in Tom's words; 3 and 4
  "Your call."
- **pop-a**: (1) Caching: one-shot handoff from `__len__` to the next
  iteration, or a per-builder result cache? (2) Fix here in popoto? Answer:
  (a), plus Tom's contract (a bare `len()` still executes, mutators drop the
  park); yes, upstream. Feedback 1: a later bare `len()` can still read a
  stale park; `len()` must always execute.
- **cut-a**: (1) Drop an unknown figure or show a placeholder? (2) Also make
  the four pipeline steps tolerant? Answer: drop, server-side warning; 2
  "Your call."
- **pop-b**: (1) Separate Redis list only when capped? (2) Where does
  `push()` live? (3) Newest first? (4) Keep the name `max_length`? Answer:
  yes; on the field value (`session.tool_sequence.push(...)`); yes, LPUSH;
  yes; and skip async push.
- **pso-b**: (1) Ship all three suggestions? (2) Ineligible Generate button
  enabled, with an inline error? (3) How to mark blocking items? (4) Link to
  where Core Values are assigned? Answer: "Your call; pick a coherent set";
  confirmed the rule (Core Values plus one evaluation).
- **pop-c**: (1) When is the prefix resolved? (2) Should `env_partition_name`
  start prefixing keys? (3) Key format? Answer: don't build `Meta.namespace`;
  document the KeyField pattern with a ContextVar recipe; 1 to 3 moot.
  Bare's feedback 1 said the same after it had built the feature.
