---
tracking: none
slug: critique-issues
type: plan
status: draft
---

# Issues from Tom's SDLC autonomy critique: plan

Task 6fd4e0439ac1. Tom's critique of the old Valor
(`home/from-tom/valor-sdlc-autonomy-critique.md`, recommendations R1 to
R11) was written against `main`. This task reads each recommendation
against this branch and writes the ones that still apply as GitHub issues,
in one document the lead posts.

## What will be built

One file, `docs/plans/critique-issues-out.md`, with two sections:

1. **Issues.** One per point that still applies. Each has a title and a
   body with four parts: the problem on this branch, the evidence (paths
   and line numbers on this branch, or a doc sentence quoted with its
   file), the mission item or constraint from `docs/mission.md` it serves,
   and a proposed change sized to the rebuild's rules.
2. **Dropped.** Every R-number not shaped into an issue, one short
   paragraph each saying why: the rebuild already answers it (with the
   mechanism that answers it), or it adds a check, gate, hook, review
   round, or approval step with no incident on this branch, or it
   contradicts a ruling Tom made (cited with date).

A point can split: the part that is plain code or a missing record becomes
an issue, the part that is a gate is dropped, and both halves say so.

## How each point is judged

For each R1 to R11, in order:

- Find the rebuild's counterpart by reading code and docs on this branch,
  not by recalling `main`. `main` is read only to confirm what the
  critique cited, with `git show main:<path>`.
- Ask the governance question of the proposed change: does it add a check,
  gate, hook, validator, review round, or approval step? If yes, it is
  shaped into an issue only when an incident on this branch is named;
  otherwise the gate part is dropped. A test is not governance.
- Ask Tom's rulings: spending never stops a task (2026-10-03); merges are
  Valor's call (2026-10-03); extraction only on a second need (Mission
  item 5); Tom's queue holds vision, priority, and cost-benefit only.
- Name the mission item. A point that serves none is dropped.

Preliminary reading, for the builder to confirm or overturn with
evidence:

| Point | Likely outcome | Why to check |
|---|---|---|
| R1 runtime verification | Issue, without a new stage | Mission item 1 "testing actual use" is unmeasured (mission.md); a headless browser is milestone 3; a new VERIFY stage is a gate |
| R2 door-class merge gate | Drop the gate; check whether a migration on release runs without a fresh dump | Gate with no incident; merges are Valor's call |
| R3 post-merge watch, auto revert | Issue if nothing implements "autonomy shrinks automatically on evidence" | A constraint in mission.md with no mechanism found yet |
| R4 #3597 drain on status | Likely dropped as answered | Check that a release restart resumes live turns from the ledger (2.1) |
| R5 decide/record split | Likely dropped as answered | Typed state machine folded from the ledger (1.2); check for prose supervisors in `skills/sdlc/` |
| R6 outcome measurement | Issue for the record; drop the sampled re-review round | "Working results in real use" is empty in mission.md; audit sample is Tom's |
| R7 failure-to-guard miner | Likely dropped | Auto-proposed guards run against the governance paragraph; corrections already reach every turn |
| R8 per-issue budget breaker | Drop | Tom, 2026-10-03: spending never stops a task |
| R9 lint ratchets, god files | Drop the ratchets; check large files as plain code | Ratchets are gates; `core/workspace.py` is 2,261 lines |
| R10 outer-loop clustering | Likely dropped | No second need; no Sentry or bug intake on this branch |
| R11 context, not control | Check the objective tree's child Brief | May be answered by 4.1 |

## Out of scope

- Posting to GitHub (the lead does it).
- Any code change. An issue proposes; it does not build.
- Points the critique makes that are not recommendations (its sections 1
  to 3 and the ranking) except as context for an issue.
- Re-ranking. Each issue carries a priority only if the evidence earns
  one; the critique's P0 to P2 are not carried over.

## Stakes

Proposal only, reversible, and nothing executes, but every issue will be
public under Valor's name and a wrong evidence citation or a disguised
gate sends work in the wrong direction.

Critique 0: the plan is a reading procedure. Review 1: the reviewer checks
the document, not this plan.

## How it is shown to work

No code, so no suite. The document is checked against these, each by
reading or `grep`, by the builder before committing and by the reviewer:

- Every R1 to R11 appears exactly once, as an issue, as dropped, or split
  with both halves named.
- Every evidence citation resolves on this branch: the path exists and
  the cited line or quoted sentence is there. Citations to `main` say
  `main`.
- Every issue names one mission item or constraint by its name in
  `docs/mission.md`.
- No issue proposes a check, gate, hook, validator, review round, or
  approval step without an incident on this branch; a proposal that
  needs a grant says so in its body.
- Every drop names its reason class (answered, ungranted gate, Tom's
  ruling, no mission item) and the artifact behind it.
- Edge cases: a point the rebuild answers in a plan but not yet in code is
  not "answered"; it is an issue against the plan or noted as pending
  with the milestone that builds it. A point the rebuild answers only in a
  doc that the code contradicts is an issue under **Docs describe
  reality**.

## Tech debt

None added or paid.
