# M0 build plans

One plan per component, written with the project's `/plan` skill (`.claude/skills/plan/`). The stage is one serial step, one parallel step, one serial step:

1. `00-seams.md` is written first, alone. It is the contract every other plan cites.
2. The component plans below are written in parallel, one agent per slug, one file per agent.
3. `99-integration.md` is written last by the lead in `/plan reconcile`, which also fills the build order below.

The plans cover M0 only (tech stack §10). M1 plans are written after M0 has run, from what it taught.

## Components

Ownership is by package and table. Two plans never create the same table or module. "Depends on" is the seam a plan consumes, not the build order; the build order is filled at reconcile.

| NN | Slug | Owns | Depends on | Covers | Status |
|---|---|---|---|---|---|
| 00 | `seams` | `schemas/`, `ports/` (signatures only) | | schemas, Protocols, kernel API, event types, record shapes, table ownership | reconciled |
| 01 | `events` | `kernel/events.py`, `migrations/` for the event table, single-flight locks | seams | append-only event store with `type` and `schema_version`, upcasting readers, advisory locks per thread and objective (tech stack §3, §10) | built |
| 02 | `tree` | `kernel/tree.py`, objective and budget tables | events | Objective nodes, states, the budget in money per objective with `raise_budget` for the person, conservation under the advisory lock from spike 01, capability attenuation from spike 05 over the named classes, generation fencing (architecture §2, §3.1, §4; tech stack §4) | built |
| 03 | `spaces` | `kernel/spaces.py`, read tokens, RLS policies, connector routing, the RLS conformance test | events; tree for `schemas/capability.py` | manifests with a named effect ceiling, row-level security keyed by minted read tokens, inbound routing to a space, the `unassigned` space (architecture §9; tech stack §3; prereq findings 1, 2) | built |
| 04 | `gateway` | `gateway/` | tree | per-Brief tokens, budget pre-check in money at the seat's price, turn tokens with a cap, revoke, forced streaming, stable-prefix caching rule, content-hash request bodies, usage rows with space (tech stack §4; spikes 03, 04) | built |
| 05 | `worker` | `workers/` except `verifier.py`, `adapters/pydantic_ai.py`, `kernel/api.py`, `kernel/runs.py`, the tool log | seams, gateway, sandbox | the PydanticAI loop, five tools bridged to the Sandbox port, tool log written before and after each call, `ask` and `answer`, Report validation (tech stack §5; spike 08) | built |
| 06 | `sandbox` | `adapters/apple_container.py`, `infra/sandbox/` | seams | the three profiles, image build from the lockfile, host-only network, stop with disk retained, snapshot of the mount (tech stack §6; spikes 06, 07) | built |
| 07 | `broker` | `broker/`, the effect ledger | tree, spaces | effect protocol of intent, action, outcome; idempotency keys; `push_branch` as it exists; allowed targets by space; connector reads outside the sandbox; the Gmail connector as it exists (tech stack §7; spike 02) | built |
| 08 | `supervisor` | `kernel/supervisor.py`, `kernel/context.py`, the context manifest | tree, spaces, memory | the turn loop, event triggers, deterministic volatility-ordered render, slice caps as placeholders, the framing turn with the estimate and its basis, self-approval or the commit card by judgment, the overrun card, corrections rendered by relevance, standing prompt compiled per turn, implicit Scribe trigger (architecture §1, §2, §3.5, §4; tech stack §8) | building |
| 09 | `memory` | `kernel/memory.py`, popoto models | spaces | episodic memory as raw turns and Scribe summaries with provenance, partitioned by space, on Redis; the operator record's schema only, since reading it is M1 (architecture §6; tech stack §3.1) | built |
| 10 | `surface` | `adapters/cli.py`, approval records | tree, spaces | the CLI: a space on every conversation, typed cards rendered by the kernel, kernel-minted approvals bound to argument digest and contract revision, consumed once, expiry (architecture §7; tech stack §13) | built |
| 11 | `verifier` | `workers/verifier.py`, verdict records | worker, sandbox | the `verify` profile, deterministic checks per artifact kind recorded before prose, blind context from the tool log and effect ledger, typed verdicts with `predicted_failure`, sampling for sandbox-only work (architecture §5) | built |
| 99 | `integration` | `kernel/__main__.py`, `tests/chaos/`, `tests/e2e/` | all | the two first objectives end to end, the chaos test, the gateway kill rerun, the cache measurement (tech stack §10, §14) | reconciled |

## Requirements carried from the spikes

Each spike ended with a requirement for the component that lifts its code. A plan for one of these slugs picks up each line under its Design and gives it a task with an acceptance check. The lead's checklist refuses a plan that leaves one out.

| Slug | Requirement | Source |
|---|---|---|
| `sandbox` | Stop is `container kill`, then a probe from outside the VM confirms execution is dead; the CLI's exit is never the confirmation. | spike 07 recommendation |
| `sandbox` | Artifact hashes are computed on the host side of the mount, never by a command inside the container. | spike 08 surprise 2; spike 06 (snapshot the mount) |
| `worker` | Tool calls in one turn run concurrently, so the tool log pairs `tool.start` with `tool.end` by `seq`, and no reader assumes the records alternate. | spike 08 surprise 1 |
| `worker` | A `terminal` record closes every open invocation, including an `ask` that was waiting when the run aborted. | spike 08 surprise 3 |
| `verifier` | A citation that resolves records the excerpt it resolved to, not only that it resolved; a document whose citations carry no excerpt fails the deterministic screen before prose. | prereqs item 18 (the clean brief scored 8/9 until its URL citation carried content) |

## Build order

Filled at reconcile (2026-09-19) from the dependencies each plan declares. Schema modules are tiny and cross plan boundaries (the tree's `brief.py` imports `schemas/sandbox.py`; its `objective.py` imports `schemas/report.py`), so one rule applies: the first plan in this order that needs a schema module another plan owns writes it verbatim from the seams, and the owning plan keeps it. Migration numbers follow this order (seams §7).

1. `events` (01): the store, ids, single-flight. Nothing depends on anything else.
2. `sandbox` (06): the adapter and `schemas/sandbox.py`, which the tree's `Brief` needs; testable on its own against the container runtime.
3. `tree` (02): capabilities, budgets, objectives, briefs, delegate, stop, projection. Writes `schemas/report.py` and `schemas/trace.py` verbatim if the worker plan has not; injectables stand in for the gateway and the manifests.
4. `spaces` (03): manifests, root capabilities (needs `schemas/capability.py`), read tokens, routing, the RLS conformance test that retires `tests/test_grants.py`.
5. `gateway` (04): tokens, budget pre-check in money, kill, cache rule, prices; calls the tree.
6. `worker` (05): the loop, the door, `kernel/runs.py`, the tool log; needs gateway, sandbox, tree.
7. `broker` (07): the ledger, `push_branch`, the Gmail connector; needs tree and spaces.
8. `memory` (09): episodes on Redis, `ingest`, `retrieve`, `propose`; needs events and spaces.
9. `surface` (10): sessions, conversations, cards, approvals, the CLI; needs events, tree, spaces.
10. `verifier` (11): checks, the verify sandbox, the blind slice, verdicts; needs worker, sandbox, tree, broker's ledger, surface's cards.
11. `supervisor` (08): the render, the turn with its cap, framing with the estimate, the implicit Scribe; needs everything above.
12. `integration` (99): the entry point, the two objectives end to end, stop, the kill rerun, the cache measurement, the chaos test.

The first plan is `01-events.md`. Building starts there with the project's `/build-m0` skill, which runs the waves under Building below.

**Cascade of 2026-09-20.** The architect's budget and effect class ruling (`docs/reviews/2026-09-20-budget-ruling.md`) made the budget money alone, per objective, with no standing budget and no ceiling on the estimate, and named the effect classes `read`, `propose`, `act`. Seams version 3 carries it and every plan above reads "Seams version 3" in its header, cascaded in the same series of commits or checked and found untouched by the ruling; the Findings, Seam amendments, and Critique sections inside each plan are the record of the earlier rounds and still quote the old shape where they quote it. Building starts at `01-events.md` as before.

## Building

Decided 2026-09-21. The build is orchestrated by the lead with the project's `/build-m0` skill (`.claude/skills/build-m0/`); each component is built by one agent running `/build <slug>` (`.claude/skills/build/`) in its own worktree, on its own database, with every task one commit and every acceptance check run. A read-only validator reruns the checks and a reviewer reads the diff inside the trust boundary before the lead merges to `main`. The project's generic `do-build` skill is set aside for this stage: it builds one feature plan per fork with a PR per plan, and here twelve plans chain through one migration sequence and hand seams to each other.

**Waves.** The build order above is the merge order. A plan whose migration is `000N` starts only after `000N-1` is on `main`, which makes waves 5 to 9 serial and leaves two safe pairs.

| Wave | Components | Why together, or alone |
|---|---|---|
| 1 | `events`, `sandbox` | both depend on the seams alone; disjoint files; sandbox owns no table |
| 2 | `tree` | needs `kernel/events.py` and `schemas/sandbox.py`; migration 0003 |
| 3 | `spaces` | needs `schemas/capability.py`; migration 0004 after 0003 |
| 4 | `gateway`, `memory` | gateway fakes the tree's signatures and needs migration 0004; memory needs events and spaces and owns no table |
| 5 | `worker` | migration 0006; needs gateway, sandbox, tree |
| 6 | `broker` | migration 0007; needs tree, spaces |
| 7 | `surface` | migration 0008; needs events, tree, spaces |
| 8 | `verifier` | migration 0009; needs worker, sandbox, tree, the broker's ledger |
| 9 | `supervisor` | migration 0010; needs everything above |
| 10 | `integration` | needs everything; runs on the `cori` database; gated on the person |

Memory owns no table, so it moves up from eighth in the build order to wave 4 beside the gateway; its two dependencies are on `main` after wave 3 and no migration number depends on it.

**Status column.** `reconciled` is a plan not yet started; `building` has a worktree open; `built` is merged to `main` with the suite green; `blocked` was returned three times or failed a gate, and waits on the person. The lead moves it.

**Resources per builder.** Worktree `.worktrees/build-<slug>` on branch `build/<slug>`; database `cori_<slug>` named by `.env.build` in the worktree (`CORI_PGDATABASE`, `CORI_MIGRATOR_DSN`, `CORI_REDIS_URL`), created at prepare and dropped at merge; Redis db 1; the container runtime, port 8788, and the Keychain shared and kept apart by the wave table. `main` runs against `cori`.

**Live actions the build takes on this machine.** Model calls on the seated models: one Haiku call (gateway), a few Executor runs (worker), nine fixture screens and two verifications on the verifier seat (verifier), and the two objectives, twenty supervisor turns, and the kill rerun (integration); the chaos test uses a scripted model. A branch `cori/<uuid>` pushed to and deleted from the throwaway `yudame/cori-sandbox` (broker, integration). Gmail reads of headers and one body from `psyoptimal.com` mail (spaces, broker, integration). Nothing is sent, merged, or pushed to a client repository. The lead re-confirms with the person before wave 10.

**Record.** `docs/reviews/<date>-build-log.md` is the durable state of the stage: phase 0 checks, one row per component, every finding with its disposition, every seams change made during the build (also written into `00-seams.md` as Version 4), live actions and spend, and the measurements. Design documents are edited once, after wave 10, from that record. The lead pushes `main` and the `build/<slug>` branches to `origin` after each merge and log commit, so the stage survives a crash or a change of Mac (decided 2026-09-26); nothing else is pushed.

## Measurements

Filled by the integration plan's task 6 (the cache measurement on the real loop) when it runs.

## Ownership rules

- An agent writes the one file its slug names, and nothing else.
- Design documents are never edited from a plan. Contradictions go in the plan's Findings and the lead carries them.
- Seam changes are proposed in the plan's Seam amendments and applied only at reconcile.
- A plan is claimed by an early commit with the header filled and the body marked in progress.
