---
name: plan-m0
description: Orchestrate the M0 planning stage end to end as the lead. Writes the seams document, runs one planning agent per component in parallel, reconciles, critiques, and reports the build order. Triggered by '/plan-m0'.
---

# /plan-m0

You are the lead for planning Cori's M0. You write two documents yourself, the seams and the integration plan, and you orchestrate agents for the rest. You do not write a component plan. The stage is one serial step, one parallel step, one serial step, and this skill walks it.

Everything a planning agent needs is in the `plan` skill (`.claude/skills/plan/`) and the index (`docs/plans/README.md`). Read both now, then `CLAUDE.md` and the reading order it gives, fully, then `docs/reviews/`, `docs/prereqs.md`, and `git log --oneline`. Do not start the stage until the working tree is clean and you are on `main`.

## Step 1. Seams

Run `/plan seams` yourself. It writes `docs/plans/00-seams.md` and commits it. This is the only contract the parallel agents share, so it is exact: schemas as fields, Protocols as signatures, the kernel API, event types, the three record shapes, table ownership, and a version line. The `Space` schema already exists in `schemas/space.py`; the seams document reconciles with it rather than replacing it.

Before you fan out, check the seams document against the index: every package and table the index assigns to a component appears in the ownership section, and nothing is assigned twice.

## Step 2. Fan out

Spawn one agent per component slug in the index, all eleven in a single message so they run concurrently: `events`, `tree`, `spaces`, `gateway`, `worker`, `sandbox`, `broker`, `supervisor`, `memory`, `surface`, `verifier`. Use `isolation: "worktree"` for each, so eleven agents committing at once never contend for one index, and name each agent by its slug.

Each agent's prompt is the same apart from the slug:

> Run the project skill `/plan <slug>` in this repository and follow it exactly. You own `docs/plans/NN-<slug>.md` and nothing else. Read everything the skill lists before writing. Cite the section and status of every decision. Mark anything the documents did not decide with "Decided here:". M0 only. Every task gets an acceptance check. Commit your one file on your branch with a plain message that says what the plan decided; no push, no co-author. When done, report the file path, what the plan decided that the documents had not, and what it left out, in five lines or fewer.

Do not poll. You are re-invoked when each agent finishes. Until then, do nothing that touches `docs/plans/`.

## Step 3. Accept or return each plan

As each agent reports, merge its branch into `main` (each adds one file, so there is nothing to resolve) and read the file against this checklist:

- The header is complete, the status is `ready`, and the seams version matches the current one.
- Every decision in Design either appears in "What the documents say" with a section and status, or is marked "Decided here:" with a reason.
- Every task has an acceptance check that is a command, a test name, or an observable.
- Every line the index carries for the slug under "Requirements carried from the spikes" has a task with an acceptance check. Today that is `sandbox` (kill then probe; host-side hashes), `worker` (pair the tool log by `seq`; a terminal record closes open asks), and `verifier` (a resolved citation records its excerpt, or a clean document fails the screen).
- Anything under `kernel/`, `gateway/`, or `broker/` has its invariants as Hypothesis properties with test names.
- Nothing later than M0 is planned. TODOs from the refinement record that touch the component are listed under "Out of scope", not designed.
- The agent changed nothing outside its one file. If it did, discard those changes before merging and say so.
- The plan is one to three days of work. If it is larger, it split itself and proposed a slug.

If a plan fails the checklist, send the agent the specific failures with `SendMessage` and wait for the revision. Do not fix a plan yourself; the agent has the context and you do not. Two returns is the limit; on the third, mark the plan `in progress` in the index and carry on, and the reconcile step will surface it.

## Step 4. Reconcile

When all eleven are merged, run `/plan reconcile` yourself. It merges seam amendments and bumps the seams version, writes the plan findings into `docs/reviews/`, answers what the documents can answer, writes `docs/plans/99-integration.md`, and fills the build order in the index.

A seam amendment accepted at reconcile can invalidate a plan that was written against the old seam. After bumping the version, list every plan that consumed the changed seam and send its agent one message naming the change; the agent updates its file and reports. Merge those revisions before moving on.

Then take the gathered "Questions for the architect" to `/ask-me`, one at a time, ranked by how many plans the answer touches. Record each answer in the plans it affects, in the Questions section, as "Answered <date>:" with the answer.

## Step 5. Critique

Spawn one critique agent per component plan, in parallel, each running the `do-plan-critique` skill on its file with the instruction to write its findings into the plan's Findings section rather than a separate document, and to change nothing else. Merge the results. Where a critique finds a defect, return the plan to its original agent with the critique attached, as in step 3. Where two critiques disagree about a seam, the seams document wins and the critique is noted, not applied.

## Step 6. Report

Your final message stands on its own for someone who saw none of the above:

- The build order, as the index now shows it, with the first plan named.
- Every question the architect answered and what it changed.
- Every open question, if any remain, with the assumption the plans proceed under.
- The findings record's path and the count of findings that require a design document edit, which is the next thing you will do, in one commit, after this report.
- Plans that did not reach `reconciled` and why.

## Rules that hold throughout

- Never push. Never add a co-author. Commit messages say what was decided.
- Design documents are edited only after reconcile, by you, in one commit, from the findings record, never by a planning agent and never mid-stage.
- When an agent's report contradicts the documents, the plan builds against the more recent source and the contradiction is a finding. You do not re-open decisions the commit pass or refinement pass closed; a plan that argues for a dropped option is returned with a pointer to the review record.
- If an agent stops responding, spawn a fresh one for the same slug with the same prompt. Its file, if any, is discarded.
- The stage is done when every plan is `reconciled`, the build order is filled, and the integration plan exists. Building starts with the first plan in the build order and the project's `do-build` skill, and that is a separate invocation the architect makes.
