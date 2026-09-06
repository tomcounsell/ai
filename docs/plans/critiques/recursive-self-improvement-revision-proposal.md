---
status: Proposed
type: revision-proposal
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3177
reviewed_commit: af89100b18c29a3b81fa0b46e6507236e69f7262
---

# Proposed revisions to the recursive self-improvement plan

This is a separate proposal for changes to `docs/plans/recursive-self-improvement.md`. The original plan remains untouched. It incorporates Tom's six-agent reconnaissance report and targeted source inspection. It is not a completed critique or an implementation authorization.

## Recommendation

Keep the outer research-loop architecture. Revise the implementation around three explicit layers: research reasoning, a small atomic control journal, and the existing execution infrastructure. The plan currently treats several missing infrastructure primitives as integration work. They are prerequisites with their own contracts and failure tests.

Retire autoexperiment independently and first. Then build the control substrate before permitting autonomous dispatch or paid experiments. Reuse existing polling, artifact storage, bootstrap statistics, and judge contracts instead of recreating them.

## 1. Correct the grounding and sequencing

| Plan area | Proposed correction |
|---|---|
| Autoexperiment | Make retirement an independent first delivery, with no replacement dependency. The installer defaults to `observer`, deleted in #466; the remaining target expects a removed prompt. The runner stores a branch name without selecting a branch, writes during dry-run, and can commit on the current branch based on an unreplicated positive judge delta. Repairing the target alone would expose the unsafe mutation path. Tom's recon reports it never ran on this machine; do not imply observed production optimization. |
| Session evidence | Replace `models/session_log.py` as a substantive evidence source: it is a nine-line re-export shim. Cite `models/agent_session.py`, `models/session_event.py`, and actual transcript/event producers and consumers. Identify coverage by producer, not by existence of a model. |
| TaskTypeProfile | Remove it as a valid rework baseline. `rework_triggered` has no producer; the derived rate is structurally zero, and the recommendation reader has no callers. Treat historical values as unavailable evidence, not low rework. Either separately repair instrumentation or derive new intervention/rework evidence from verified events. Never retroactively relabel the old zeros as measurements. |
| Model layout | Use flat modules such as `models/improvement_case.py`, `models/improvement_experiment.py`, and `models/improvement_charter.py`; no `models/improvement/` sub-package. Group closely related typed payloads inside those modules where appropriate. |
| Configuration | Put runtime defaults in an `ImprovementSettings` block in `config/settings.py`, following existing override conventions. Keep immutable charter versions in Redis records. No `config/improvement.yaml`. Settings provide defaults; the pinned charter is the effective authority for a research action. |

Retirement acceptance: remove installer, launchd template, runner and stale entry-point references; inventory installed matching jobs and disable/remove only verified matching installations; preserve any historical evidence. Confirm ordinary reflection and SDLC services are unaffected. This document only proposes the work; no services have been changed.

## 2. Credit and reuse the existing primitives

### Poll transport

`bridge/poll_registry.py`, `bridge/poll_reconcile.py`, and the poll vote path already implement delivery registration, pending-poll adoption, answer claims, and late-answer routing. Keep them authoritative for transport. Add an investigation reference to the existing descriptor/context contract, rather than a parallel answer-routing registry.

The new layer owns only research semantics: which uncertainty the question addresses, how the answer updates a hypothesis, and which decision it changes. Persist the original answer into research evidence before transport retention expires. Research expiry must not destroy a late answer; it changes the applicability decision, not delivery history.

### Artifact storage

Reuse Popoto `FilesystemStore` through the existing content-store integration (`models/length_safe_content_store.py`). It already provides SHA-256 references, atomic replacement, and archived versions. Do not build another content-addressed store.

Add an experiment artifact adapter for manifests, retention roots, media types, verified reads, and permissions. The store's live paths are key-derived and mutable; use digest-derived immutable artifact identities, verify bytes on every experiment load, and protect retained artifacts from deletion/garbage collection. In the inspected implementation, the archive fallback returns bytes without rechecking their digest, so the adapter must check the expected hash even when the store returns successfully. Existing hashing is not by itself an immutable experiment guarantee.

### Evaluation

Reuse the paired bootstrap interval implementation in `tools/memory_eval/metrics.py`. Its mean-delta interval is domain-neutral; retrieval-specific scoring functions remain in their existing domain. Add endpoint-specific practical thresholds, clustered sampling where tasks share a project, and repeated-selection controls outside that primitive.

Reuse the per-judge contract in `agent/sdlc_review_consensus.py`: `judge_id`, `verdict`, `blockers`, and optional reasoning/confidence. Wrap it with experiment ID, contract digest, evaluator version, trial ID, raw-response reference, and blinded arm ID. Keep SDLC consensus as a review gate; do not confuse agreement among judges with a statistically demonstrated capability gain.

## 3. Gap A: atomic transitions and non-issue execution ownership

**Decision:** add a narrowly scoped, non-Popoto Redis control journal. Do not pretend Popoto provides compare-and-set, and do not invent issue numbers for research leases.

Namespace keys by project and research case, with explicit schema version. A bounded Lua transition accepts expected revision, execution epoch, action ID, and event payload digest. It atomically verifies ownership, updates the control head, and appends a journal entry. Lease acquisition increments a monotonic epoch; renewal and release require the same owner and epoch. Use Redis time for lease checks.

The control head/journal is authoritative for transitions and dispatch authorization. Flat Popoto records are queryable projections and detailed payload storage, updated through ORM methods. If projection fails after a committed transition, replay repairs it; decisions never rely on a potentially stale projection. Never write raw Redis into Popoto-managed keys.

Keep atomic journal events small: validate and persist immutable payload artifacts first, then reference their digests in the transition. An artifact written without a committed event is an orphan eligible for delayed cleanup, not a partially completed transition.

Execution workers submit results tagged with action ID and epoch. They cannot mutate research state directly. A stale worker may finish an artifact, but cannot have it accepted after takeover. Fencing must also be checked at effect boundaries: dispatch, budget authorization, question delivery, and release. A stale lease check followed by an unguarded effect is not fencing.

This introduces a real consistency boundary, not a general replacement for Popoto. Require a dedicated namespace review, bounded journal indexing, replay tests, and explicit Redis persistence/recovery policy. The research controller pauses when that authority is unavailable.

## 4. Gap B: execution while child spawning is disabled

**Decision:** do not enable or bypass #1633. Remove language promising child-session dispatch.

Research ownership belongs to a durable Job/case, not a parent executor session. Propose a scheduler-owned dispatch adapter that creates ordinary top-level work sessions, with explicit `research_case_id`, `experiment_id`, and `action_id` provenance. It has no parent-child completion semantics and does not let a running agent evade the child gate by omitting a parent argument.

Only the trusted scheduler adapter can submit this work, after validating the charter, journal authorization, budget reservation, and allowed action type. The agent proposes actions; it does not enqueue them directly. Existing worker concurrency limits still apply. Existing issue-keyed SDLC locks remain authoritative for actual implementation work; the research journal separately owns the research action.

This is a deliberate new autonomous dispatch capability and must receive an explicit policy/design review against the rationale of #1633. It is not claimed to exist today. Until accepted, observation and investigation planning can run, but experiment execution remains blocked or operator-dispatched. No environment override is an implementation shortcut.

## 5. Gap C: durable dispatch intents

**Decision:** implement a journal-backed dispatch-intent record before adding automated execution.

Fields: stable action ID, case/epoch, immutable request digest, charter version, reservation ID, intended session identity, state, timestamps, attempt evidence, and result artifact references.

Lifecycle: `prepared → admitted → materialized → running → result_recorded → settled`, with explicit `cancelled` and `reconciliation_required` states.

The queue adapter needs a new idempotent create-or-bind contract accepting an external action key and a preallocated stable session identity. It must validate request-digest equality on retries. The current queue does not supply this contract; a lookup followed by ordinary create is not an acceptable substitute.

Proposed recovery protocol:

1. Atomically admit the intent and reserve capacity in the control namespace.
2. Materialize an inert session through ORM APIs using the persisted identity. It is not worker-eligible until admission/binding is complete.
3. Bind it to the intent and make it eligible through the queue adapter. A worker validates the current dispatch epoch before pickup.
4. Reconcile after crashes by stable identity and action key; retry materialization or activation, never mint another identity for the same action.
5. Record the result before settling the intent. Session completion without the artifact remains unfinished research.

Implementation must identify how inert materialization fits current session states and newest-wins behavior. Add an explicit admission field/gate if needed; do not overload a recoverable pause state that another reflection might reactivate. This API/state change is a prerequisite, not deferred glue.

The design promises idempotent admission and recoverable at-least-once execution. It does not promise exactly-once external effects. External effect adapters require their own idempotency or reconciliation semantics.

## 6. Gap D: reservations rather than post-hoc budgets

**Decision:** add atomic admission accounting; retain `agent/tool_budget.py` as a second-line backstop.

Create a budget reservation keyed by action ID with charter/window, maximum authorized spend, token/runtime/concurrency limits, usage receipts, and state. In the dedicated control namespace, admission atomically checks `settled_spend + outstanding_reservations + requested_maximum <= limit`, including experiment and project caps. Couple intent admission and reservation in the same transaction boundary.

Before each paid operation, reserve its bounded maximum or debit a preallocated action envelope. After receipt, settle actual use and release unused capacity. Duplicate receipts are idempotent. An expired executor lease does not free an unresolved financial reservation: a request may still be running or already billed. Unknown charges remain reserved until reconciled or conservatively settled at the authorized maximum.

Hosted harnesses may not expose a hard per-call spending limit. For those, distinguish a conservative admission estimate from an enforceable ceiling. Do not claim a strict dollar bound where tooling cannot enforce one. Restrict hard-budget experiments to supported adapters, or make their bounded overshoot policy explicit in the charter. Runtime termination alone cannot retract a submitted provider request.

Keep evaluator/recovery capacity separately reserved. Human attention is an additional quota and observed burden, not a fabricated dollar exchange rate. Record actual usage as evidence, and preserve existing tool-call denials even after reservations are introduced.

## 7. Gap E: immutable memory inputs and paired-arm isolation

**Decision:** build a snapshot/export and isolated retrieval adapter. Changing `project_key` alone is not a snapshot or an isolation boundary.

For the first experiment, use a fixed evidence corpus exported through ORM reads into the existing content store. Record complete memory fields consumed by retrieval, embedding bytes, embedding model/dimension, retrieval parameters, reference resolution maps, and the effective retrieval clock. Hash the canonical manifest and bytes. Neither arm reads live production memory.

An export of a changing partition is not automatically a point-in-time snapshot. Initial default: define the experiment corpus as the exact exported immutable dataset, disclose its collection interval, and make no production point-in-time claim. Both arms consume identical bytes. Experiments that require a coherent temporal snapshot must first implement a partition write barrier honored by every writer, or a versioned export protocol; retries over ORM reads alone do not establish consistency.

Run each arm with a separate retrieval backend/root and credentials that cannot access production Redis. Disable extraction, confidence updates, consolidation, and background memory maintenance for fixed-corpus trials. Freeze time-dependent ranking inputs. Validate these restrictions through the actual hook/retrieval path, not just the test harness's configuration.

For later learning-policy experiments, clone the same seed into separate mutable stores and replay the same task sequence. Each arm gets its own embeddings, caches, extraction sidecars, outcomes, and decay clock. Capture snapshots at episode boundaries. Search for and remove hard-coded production partition fallbacks from the experiment path; refuse startup if an arm resolves any state outside its assigned namespace.

The isolated adapter must first reproduce baseline retrieval on the frozen corpus. Otherwise the experiment changes both context policy and retrieval implementation, confounding the result. Such a mismatch blocks scoring until explained or registered as an experimental factor.

## 8. Revised delivery order and acceptance gates

1. **Retire autoexperiment independently.** No replacement dependency; no production-gain claim to preserve.
2. **Correct evidence and reuse contracts.** Producer coverage audit, false-baseline exclusion, poll bindings, artifact adapter, settings/charter placement, flat schemas.
3. **Build the control substrate.** Journal/fences, dispatch intents, queue admission contract, reservations. Policy review of scheduler-owned top-level execution. Observation work can proceed independently; autonomous effects cannot.
4. **Build frozen evaluation inputs.** Memory export and isolation, judge envelope, reusable statistics, replay parity.
5. **Run one complete research cycle.** Discovery, information acquisition, candidate construction, paired evaluation, and qualified result. Rejection is a valid result.
6. **Add production promotion and then meta-experiments.** Keep the original plan's evidence and release constraints.

Do not preserve the original calendar estimate unchanged. Queue admission, financial reservations, and memory isolation are newly explicit engineering work. Re-estimate after their contracts and spikes; the sequencing above is the commitment this proposal can support.

Required fault-injection additions:

- Pause executor A past lease expiry; admit B; resume A at every effect boundary. A cannot authorize new work or overwrite B's state.
- Crash between journal commit, ORM projection, session materialization, binding, and queue activation. Recovery yields one stable action/session binding and no unadmitted pickup.
- Restart with an unresolved provider charge. Capacity is not silently refunded; duplicate receipts cannot double-charge accounting.
- Change production memory during export and trials. Fixed-corpus arms remain byte-identical; a claimed point-in-time export is rejected without a valid consistency mechanism.
- Exercise existing late-answer and orphan-poll adoption paths with investigation references. No second routing mechanism is introduced.
- Corrupt an archived artifact and delete an unpinned file. Digest verification/retention failure invalidates evaluation rather than returning a score.
- Assert historical TaskTypeProfile zeros never appear as measured success in the baseline dashboard.

## Disposition

The conceptual architecture survives. The revised proposal should be more candid about what exists and more specific about what must be built. The central change is replacing aspirational phrases—“atomic transition,” “spawn,” “reserve,” and “freeze”—with enforceable protocols and explicit prerequisite gates.
