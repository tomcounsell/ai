---
name: plan
description: Write one M0 build plan for a named Cori component, or the seams document, or the reconcile pass. Built for many agents planning in parallel with strict file ownership. Triggered by '/plan <slug>', '/plan seams', '/plan reconcile'.
---

# /plan

Cori is greenfield and the design is agreed. Planning turns `docs/architecture.md` and `docs/tech-stack.md` into build plans a builder can execute one at a time, without re-deciding anything the documents decided. Several agents plan at once, so the skill is mostly rules about what you may touch.

`$ARGUMENTS` is one of: `seams`, a component slug from `docs/plans/README.md`, or `reconcile`.

## The stage, in order

1. **`/plan seams`**, once, by one agent, before anything else. Fixes the interfaces every component plan cites: the Pydantic schemas, the three Protocols, the kernel API workers call, the event types, the record shapes of the tool log and effect ledger, and which component owns which tables. Output is `docs/plans/00-seams.md`. Nothing fans out until it is committed.
2. **`/plan <slug>`**, one agent per component, all at once. Each writes exactly one file, `docs/plans/NN-<slug>.md`, from the template. Twelve slugs are listed in the index; run as many in parallel as the machine allows, since they share nothing but read access.
3. **`/plan reconcile`**, once, by the lead, after every component plan is committed. Reads all of them, resolves the seam amendments they proposed, bumps the seams document's version, writes `docs/plans/99-integration.md` for the M0 walkthrough, and fills the build order in the index.
4. **Critique**, per plan, with the existing `do-plan-critique` skill, after reconcile. Not part of this skill.

## Rules for every mode

- **You own one file.** The one named by your mode. You never edit a design document, the seams document (except in `seams` and `reconcile` modes), another plan, the index (except in `reconcile`), or any code. What you would change elsewhere goes in your plan's Findings, Questions, or Seam amendments sections, and the lead carries it.
- **Read before writing**, in this order, fully: `CLAUDE.md`, `README.md`, `VOICE.md`, `docs/architecture.md`, `docs/tech-stack.md`, every file in `docs/reviews/`, `spikes/README.md` and the README of every spike your component cites, `docs/prereqs.md` for what already exists, `docs/plans/README.md`, `docs/plans/00-seams.md`, and the code under the packages your component owns. Then `git log --oneline`.
- **Pick up what the spikes left you.** The index has a table, "Requirements carried from the spikes", keyed by slug. Every line for your slug appears in your Design and has a task with an acceptance check. A plan that leaves one out is returned.
- **Plan only what the documents decided.** Every design choice in your plan cites the section it comes from and that section's status. If the documents are silent, you may decide, and you say so in a line that begins "Decided here:" with the reason, so the lead can see every decision the plan added. If the documents contradict each other or the code, that is a Finding, and you plan against the more recent of the two.
- **Commit rather than hedge.** The architect's rule for this project: build one thing, add the port when the second implementation arrives, because refactoring is cheap and simplicity gets to learning faster. Where the documents leave a TODO with a trigger, the TODO is out of scope. You do not design for it and you do not leave a hook for it.
- **M0 only.** The milestone table in `docs/tech-stack.md` §10 says what M0 ships and proves. Anything M1 or later is out of scope, even when it would be easy. Say what you left out and why, so the M1 planner starts from your list.
- **Every task has an acceptance check.** A command, a test name, or an observable, not a description. A task a builder cannot tell is done is not a task. Tests are named in the plan before they exist.
- **Kernel invariants are properties.** For anything under `kernel/`, `gateway/`, or `broker/`, state the invariant as a Hypothesis property with the operations it ranges over, the way spikes 01 and 05 did. An example-based test is welcome as well; it is never enough alone.
- **Size.** A plan is one to three days of one builder's work with the fewest new concepts. Bigger than that, split it: keep the first slice in your file and list the rest under "Out of scope" with a proposed slug, and the lead decides.
- **Writing.** No em dashes. Say what is true rather than what is not. Cori is the system, never a character; the loop is the supervisor. Plain and short. A plan is read by a builder who wants to start, not by a reviewer who wants to be persuaded.
- **Commit your own file only**, with a plain message that says what the plan decided. No push. No co-author.

## `seams` mode

Write `docs/plans/00-seams.md`. It is the contract between the parallel plans, so it is exact where a plan may be rough:

- Every schema in `schemas/`, as the Pydantic model fields with types, taken from architecture §2, §3.1, §6, §9 and tech stack §2, reconciled with `schemas/space.py`, which already exists. Where the documents disagree on a field, choose and say why.
- The three Protocols in `ports/`, as signatures, from tech stack §5, §6, §13.
- The kernel API a worker can call, as function signatures: delegate, report, ask and answer, request an effect, read a slice. This is the only door into the store, and it is the seam between `workers/` and `kernel/` that the import rule enforces.
- Event types with `type` and `schema_version`, one line each, grouped by the component that emits them.
- The three execution records (gateway log, tool log, effect ledger) as row shapes, from tech stack §4 and §7.
- Table ownership: which component's migrations create which tables, so two plans never create the same one.
- A version line at the top. Reconcile bumps it.

Nothing else. No tasks, no design prose. A component plan cites this file by section.

## `<slug>` mode

Copy `PLAN_TEMPLATE.md` from this skill's directory to `docs/plans/NN-<slug>.md`, with `NN` from the index, and fill every section. Delete a section only if it is empty and say so in one line. Claim the slug by committing the file early with the header filled and the body marked "in progress", so no other agent takes it, then commit again when it is done.

## `reconcile` mode

For the lead. Read every `docs/plans/NN-*.md`. Then:

1. Merge every "Seam amendments" section into `00-seams.md`, accepting or refusing each with a reason recorded in the plan that proposed it, and bump the version.
2. Collect every "Findings" entry into `docs/reviews/<date>-plan-findings.md` in the format of the earlier reviews, with a disposition each. Design document edits that follow are a separate commit, made by the lead, not by this skill.
3. Answer every "Questions for the architect" entry you can from the documents, in place, and gather the rest into one list for the architect, asked with `/ask-me`.
4. Write `docs/plans/99-integration.md`: the M0 walkthrough from tech stack §10 (a code change in the client's repository, then a drafted reply to a client mail, both at `propose` inside the sandbox), the chaos test, the gateway kill rerun, and the cache measurement, each as tasks with acceptance checks, citing which component plans they depend on.
5. Fill the build order in `docs/plans/README.md` from the dependencies each plan declared, and mark every plan `reconciled`.

## Output

For `seams` and `<slug>`: the path of the committed file and, in five lines or fewer, what the plan decided that the documents had not, and what it left out. For `reconcile`: the build order and the list of questions for the architect.
