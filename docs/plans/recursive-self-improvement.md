---
status: Planning
revision_applied: true
revision_applied_at: 2026-09-07T03:10:03Z
charter: docs/improvement-charter.md
charter_version: 2
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-05
baseline_commit: 85524b94092d7062e0ae5b231a7ef57385a711f3
tracking: https://github.com/tomcounsell/ai/issues/3177
last_comment_id: 5560574590
---

# Recursive self-improvement controller

## Problem

Valor improves only when a human notices a weakness and files work. The system records what it did and keeps no durable evidence of whether the work served its purpose, which corrections were architectural rescues rather than preference tweaks, or which past experiments were tried and rejected. The one prior attempt at autonomous self-optimization, `scripts/autoexperiment.py`, is unsafe and dead.

**The north star is [the RSI charter](../improvement-charter.md), version 2, dated 2026-09-07.** It governs compounding capacity to learn, execute, and acquire abilities. The plan remains in Planning until the reconciliation below is carried through its technical design, tasks, and verification. Where the plan and charter disagree, the charter governs; older critique verdicts do not establish readiness against version 2.

### Charter version 2 reconciliation (carried through on 2026-09-07; each bullet names where)

- Replace fixed independence-first weighting, the 60/25/15 allocation, and the mandatory journey-preservation first experiment with evidence-based ranking that initially favors inference capacity, token efficiency, skills, specialized personas, and cloud execution.
- Add the first-month expectation of mostly cloud-sandbox, round-the-clock RSI operation, including infrastructure acquisition, unattended execution, storage, recovery, and measurable progress. A single-machine/one-lane first increment must retain explicit follow-through to that outcome.
- Implement separate metering and admission for $10/day paid inference and $50/week infrastructure, including recurring charges and credit expiry. Subscription capacity is separate; one lane is not a permanent charter cap.
- Permit any provider for open-source work; preserve Claude/Codex subscription execution for regular client work and prevent client-context leakage into provider experiments.
- Replace blanket no-question wording and its anti-criterion with no routine RSI research questions plus an explicit, usable charter-amendment permission path. Ordinary client/project discernment remains separate.
- Reflect authority to acquire accounts and resources through Valor's available browser, Workspace identities, card, vault, and Cloudflare access; verify access rather than asserting it exists. No autonomous revenue activity.
- Permit evidence-backed core SDLC merges after representative comparison, no material quality regression, normal review, and applicable repository gates, without an extra Tom approval. Distinguish merge authority from deployment mechanics; retain technical isolation protections.
- Make evaluation and research methods revisable in service of the charter. Carry the creative-skill acquisition scenario through evidence intake, vetting, library integration, comparative evaluation, and later reuse.
- Treat Telegram links and videos as inspiration by default; explicit `do-issue` direction denotes requested work. Remove fulfilled-promises measures and apply the strict no-promises rule.
- Where each landed: ranking in Problem, the planner bullet, and Gap G; first-month expectation in Appetite, lane 7, and Success Criteria; budgets in Gap D and task 2; providers in Risk 8 and `tools/improvement_eligibility.py` (task 3); the amendment path in Gap F, Agent Integration, and the no-routine-question Verification row; resource authority in the resource-acquisition bullet, `tools/improvement_resources.py::probe`, and the `[EXTERNAL]` No-Go; merge authority in the Release bullet and the first `[ORDERED]` No-Go; revisable evaluation in Promotion criteria and lane 6; the §5 skill-acquisition scenario in the investigation kinds and lane 5; inspiration intake in the memory-inspiration bullet and task 3; no-promises in the downstream-signs table and lane 5. Issue #3177's stale question-delivery and journey-preservation acceptance criteria are corrected on the issue itself. Readiness still waits on a fresh critique round.


**What the loop optimizes (charter §1, §3, §11).** The north star is compounding capacity: continuously improve Valor's abilities and his ability to discover, acquire, integrate, and validate new abilities, so each useful improvement makes the next easier and faster. Opportunities are ranked by expected contribution to that north star, weighing opportunity cost, quality, resource cost, uncertainty, and the capacity they unlock. The charter names five early priorities (cheap inference integrated into eligible sessions, token efficiency, the skill library, narrow subagent personas, cloud execution capacity) as strong starting hypotheses that evidence may reorder. There is no fixed allocation and no mandatory first experiment. RSI is not subordinate to the cadence of client work.

Downstream signs of usefulness are tracked beside capacity, never as the reward:

| Downstream sign | Primary measures | Required context and guardrails |
|---|---|---|
| Fewer architectural rescues | Rescue incidence and severity per comparable task; observed Tom time | Attempted workload, difficulty, abandoned work, whether a needed clarification was suppressed |
| Better stakeholder judgment | Verified communication-task completion; corrected misunderstandings; proportionate contact cadence; zero unqualified promises in outbound messages (charter §10) | Capability coverage, factual accuracy, stakeholder feedback, latency, relationship context |
| Stronger delegation | Delegated-task success without rework; context handoff completeness; delegation depth and breadth against cost | Task type, brief content, model and tool budget per delegate, whether the parent could have done it cheaper alone |
| Sustainable quality | Unique defect arrival per exposure; recurrence; severity-weighted unresolved debt | Raw issue count, detection coverage, duplicate and label changes, throughput, observation window |

Capacity itself is measured directly: abilities acquired with comparative evidence and later reuse (charter §5), the share of RSI sessions running unattended in cloud sandboxes and what sustains them at what cost (charter §2), and validated gain per unit of authorized resource (charter §6, claim level 3).

Classification of a correction (architect intervention, ordinary preference, new scope, expected domain clarification) is uncertain evidence until corroborated. Time estimates are never invented from message counts. A declining bug count with declining detection is not a win. Raw measures publish beside normalized ones.

**Current behavior:**

- `Job` records the goal and when it was discharged, never why or on what evidence. Success and abandonment are indistinguishable from rest-by-age (`models/job.py::sweep_to_rest`, `:521`).
- Human corrections are detected by a flat regex list (`reflections/utilities.py::CORRECTION_PATTERNS`, `:37-46`) whose result is a transient dict. Nothing classifies a correction.
- `TaskTypeProfile.rework_rate` is structurally zero: `rework_triggered` has zero production writers, `failure_stage_distribution` has zero writers, `get_delegation_recommendation` has zero callers.
- No evaluator compares a full candidate agent run against an incumbent. Every judge scores one artifact with candidate identity visible. No holdout split, blinding, or judge calibration exists.
- Ideas Tom sends as links land as `Memory` rows with `source="human"` (`.claude/hooks/hook_utils/memory_bridge.py:801-836`) and nothing reads them as research input. No record links a claim to the source and date it came from, so an assumption about a provider, a price, or a technique is indistinguishable from a guess.
- `scripts/autoexperiment.py` assigns `self.branch` at line 308 and never reads it, so it commits to whatever branch is checked out. Its `--dry-run` still overwrites tracked source and spends API money. Its installer defaults to a target whose module was deleted in #466, so a default install schedules a nightly job that raises `KeyError` forever.

**Desired outcome:**

A persistent controller, served by bounded sessions and existing reflections, that can notice a consequential weakness, decide what it needs to learn, acquire that information on its own (mining Tom-sourced memories for inspiration and researching current practice online), build and evaluate a candidate in isolation, and explain with durable evidence why the next version deserves to exist. Claim levels:

1. **Loop operational:** an autonomous discovery-to-evaluation cycle completed.
2. **Ability improved:** comparative evidence demonstrates a useful gain, with subsequent-use evidence reported separately when available.
3. **Recursive improvement demonstrated:** a change to how Valor learns or executes produces greater validated gains on fresh opportunities at comparable resources, or sustains greater useful capacity within the authorized budgets.

Repeated edits alone satisfy none of the latter two (charter §6). This is system-level recursive improvement with externally supplied models; model-weight training and claims of unbounded acceleration are out of scope. This document proposes implementation; it does not authorize changes to deployment mechanics, RSI-initiated outreach, revenue activity, or human-owned approval signals. Client contact within assigned responsibilities is Valor's discernment under charter §10 and is a capability target of the loop, never an action the loop takes.

## Freshness Check

**Baseline commit:** `85524b94092d7062e0ae5b231a7ef57385a711f3`
**Issue filed at:** 2026-09-05T13:14:50Z
**Disposition:** Unchanged

**File:line references re-verified:** every reference in the issue's Recon Summary was produced against `ffda9fc86` on 2026-09-05. `git log --since=<issue createdAt>` over all cited files (`models/job.py`, `models/task_type_profile.py`, `models/session_event.py`, `reflections/utilities.py`, `reflections/expectation_reconciler.py`, `bridge/poll_registry.py`, `bridge/answer_routing.py`, `agent/reflection_scheduler.py`, `agent/agent_session_queue.py`, `models/child_session_gate.py`, `models/session_lifecycle.py`, `agent/tool_budget.py`, `tools/memory_eval/`, `agent/sdlc_review_consensus.py`, `models/length_safe_content_store.py`, `scripts/autoexperiment.py`, `ui/app.py`, `config/settings.py`) returns no commits. All references still hold.

**Cited sibling issues/PRs re-checked:**
- #410 / PR #411 (autoexperiment): closed and merged 2025; superseded by this plan.
- #466 (observer deletion): closed; the deletion that orphaned autoexperiment's default target stands.
- #1506 (doc references deleted target): closed; `docs/features/autoexperiment.md` still presents the observer target as live.
- #818 (SDLC harness benchmarking): closed; no benchmarking code landed.
- #1633 (child session gate): closed with the gate deliberately retained; rationale recorded in `models/child_session_gate.py`.
- #2731 (stage liveness gate): closed re-scoped; intra-run collision remains a documented residual.
- #3095 (`/ask-me` poll deferrals): open; not a dependency, because the controller sends no routine research questions and its one message class, the amendment request, needs no poll binding (see Gap F).
- #1312 / PR #2196 (bridge reacts when no worker is alive): shipped and closed. **No overlap.** Re-verified 2026-09-06: the liveness substrate it established is the `worker:registered_pid:{hostname}:{pid}` key family, prefix constant at `agent/session_health.py:216`, written with TTL by `register_worker_pid` at `:5269`, read by a `scan_iter` over the prefix at `:6463`, with per-PID freshness at `_worker_pid_heartbeat_fresh` (`:5393`). The scheduler adapter reuses that read before activating an `admitted` session (Gap B), so a research session never rots in a queue no worker is draining.
- #3183 (ETL-grade pipeline hardening): **open, filed 2026-09-06, overlap resolved by ownership.** It owns the shared substrate this plan previously proposed to build: the idempotent create-or-bind seam on `_push_agent_session`, the renewed Lua execution lease with a fencing generation, and the one dead-letter record. #3177 lane 3 consumes those primitives and changes no queue signature. See No-Gos and Gaps A and C.

**Commits on main since issue was filed (touching referenced files):** none as of `85524b9`. Re-checked on 2026-09-06: the citations this revision touches were re-derived directly against the working tree rather than trusted from the prior pass. Two had drifted and are corrected below (see the Citation corrections table under Technical Approach).

**Active plans in `docs/plans/` overlapping this area:** `remove-popoto-1-8-0-naive-datetime-scar-tissue` touches Popoto datetime handling across models and will land before the new records are written; new models follow whatever `_ts` convention it settles. `sibling-reflections-hardcode-eng-valor` touches reflection routing and does not overlap the controller's registration path. `etl-pipeline-hardening` (#3183) overlaps deliberately and is resolved by the ownership split above. None blocks lanes 1 and 2; #3183 blocks lane 3.

**Notes:** the revision proposal at `docs/plans/critiques/recursive-self-improvement-revision-proposal.md` was reviewed at `af89100b` and its factual claims re-verified (popoto `FilesystemStore.load` archive fallback returns bytes unverified at `stores/filesystem.py:159-163`; Lua compare-and-set precedent at `models/session_lifecycle.py:1357`; child gate enforced at `agent/agent_session_queue.py:259-265`; reflection scheduler enqueues top-level sessions with no parent at `agent/reflection_scheduler.py:754`).

## Prior Art

- **#410 / PR #411**: Autoexperiment, autonomous prompt optimization. Shipped a hypothesize-edit-evaluate loop with no branch isolation, a single noisy judge, and strict-inequality acceptance. Never installed or run on this machine. Retired by this plan.
- **#818**: SDLC harness benchmarking. Closed with no code; the only "benchmark" hits are `tests/performance/test_benchmarks.py` and token-cost tests.
- **PR #2135**: hybrid retrieval eval. Source of the paired per-query deltas and seeded bootstrap 95% CIs in `tools/memory_eval/metrics.py`. Reused here.
- **PR #2210**: memory telemetry baseline export. Source of the `--force`-guarded `docs/baselines/` artifact pattern in `tools/memory_eval/snapshot.py`. Reused for holdout manifests.
- **#1633**: merged PM/Dev roles and retained the child session gate. Its rationale (redundant with in-session subagents; no per-parent fanout cap) is the constraint Gap B answers.
- **#2731**: stage liveness gate. Proved ownership leases cannot detect intra-run collisions. The control journal's action-ID fencing is designed around that finding.
- **#3183**: ETL-grade pipeline hardening. Owns the create-or-bind seam on `_push_agent_session`, the renewed execution lease with a fencing generation, and the generalized dead-letter record. Filed as the production-path complement to this plan's research namespace, explicitly so the two do not each change the queue signature. Lane 3 consumes it.
- **#1312 / PR #2196**: bridge reacts when no worker is alive. Shipped; no overlap. Its `worker:registered_pid:*` liveness convention is the pre-activation check the scheduler adapter reuses.
- **`docs/archive/plans-completed/do-build-ai-evaluator.md`**: single-arm plan-acceptance judge (`scripts/evaluate_build.py`). Advisory only; its PASS/PARTIAL/FAIL shape is not reused.

## Research

**Queries used:**
- Redis fencing token lease epoch Lua script compare-and-set stale worker write prevention
- sequential testing alpha spending repeated candidate selection LLM evaluation paired comparison noninferiority

**Key findings:**
- A lease alone does not protect the resource. Only the resource being written can reject a stale writer, so the fence check must be atomic inside Redis (Lua), and the accept rule is `token >= highest_accepted`, never strictly greater, or a holder's own second write is refused. Release is compare-and-delete, never unconditional. Sources: [Redisson FencedLock](https://redisson.pro/glossary/java-fencedlock.html), [Design a Distributed Lock Service](https://dev.to/gabrielanhaia/design-a-distributed-lock-service-fencing-tokens-and-the-failure-modes-29nl). Informs Gap A: the control journal's transition script checks epoch and revision server-side and every effect boundary re-checks.
- Multiplicity changes results: of five significant pairs at uncorrected α=0.05 on a shared eval, only three survive Holm or Bonferroni. Paired LLM evaluation should be framed as a level-α, power-(1−β) test with a per-pair resolution diagnostic. Sources: [Statistical Methods for Multiple LM Comparison](https://arxiv.org/html/2608.22659), [Resolution Diagnostics for Paired LLM Evaluation](https://arxiv.org/html/2605.30315v1). Informs the evaluation design: repeated-selection correction is mandatory and "inconclusive" is the default verdict for underpowered pairs.
- Group sequential testing with Lan-DeMets alpha spending is the production choice at Spotify; early termination biases effect estimates upward, more so the earlier the stop. Sources: [Spotify sequential testing comparison](https://engineering.atspotify.com/2023/03/choosing-sequential-testing-framework-comparisons-and-discussions), [PSU STAT 509 alpha spending](https://online.stat.psu.edu/stat509/lesson/9/9.6). Informs the contract: register finite batches or a named spending function before the first trial, and report early-stopped effects as biased.
- No source addresses noninferiority margins for paired LLM comparisons combined with best-of-many selection. That combination is a design decision this plan makes explicitly (per-endpoint margins in the charter, holdout rotation, untouched final set) rather than a borrowed recipe.

## Spike Results

### spike-1: inert session admission state
- **Assumption**: "A new AgentSession status the worker ignores can be added without a migration and without any recovery pass reactivating it."
- **Method**: code-read
- **Finding**: New status value wins over an admission field. Index sets are created lazily per value, so no migration or index rebuild. The one required edit is `models/session_lifecycle.py:72` (`NON_TERMINAL_STATUSES`), because `transition_status` raises `ValueError` on an unknown status at `:694-695`; also record the state in `RECOVERY_OWNERSHIP` at `:90`. Worker pickup is `status="pending"` only (`worker/__main__.py:903`, warm-up `:613`); stuck and orphan passes query `running` and `waiting_for_children` only; `session_recovery_drip` drips `paused` and `paused_circuit` only. Sites that iterate `NON_TERMINAL_STATUSES` and merely observe: `agent/session_health.py:848`, `bridge/email_bridge.py:1162`, `models/agent_session.py:1387`, `reflections/expectation_reconciler.py:309`, `reflections/sdlc_upvote_lanes.py:269`, `reflections/sdlc_progress.py:850`, `tools/agent_session_scheduler.py:1224`. Dashboard `ACTIVE_STATUSES` at `ui/data/sdlc.py:469,1255` is a hardcoded tuple and needs the new value. A plain boolean field is worse: unindexed, so the worker would hash-read every pending row, and untyped bools round-trip as the truthy string `"False"` (`docs/features/redis-models.md:113-135`).
- **Confidence**: high
- **Impact on plan**: Gap C uses a new `admitted` status. **Lane 3 owns introducing it** (task 6's lane-3 child issue), because lane 3 is the first thing that creates a session in that state; lanes 1 and 2 add no status. Lane 2 does collapse `ui/data/sdlc.py`'s two active-status definitions into one (task 4), so lane 3's addition is a one-line edit in a single place instead of two sites a builder could half-find.

### spike-2: queue create-or-bind contract
- **Assumption**: "`_push_agent_session` can be given a stable identity and an external action key so a crash-retry binds to the existing row."
- **Method**: code-read
- **Finding**: Holds, with one nuance. `_push_agent_session` (`agent/agent_session_queue.py:204-231`) accepts caller-supplied `session_id`, `correlation_id`, `scheduled_at`, `parent_agent_session_id`, `telegram_message_key`; the row's primary key is a Popoto `AutoKeyField` minted inside `async_create` (`:370`, its `status="pending"` literal at `:372`), so a retry always mints a second row, and that row wins newest-wins (`models/agent_session.py:1209-1229`), orphaning the first. `correlation_id` is log-only (`models/agent_session.py:358`; read at `agent_session_queue.py:1756, 3015`), never filtered. The `count() > 0` check at `:333-336` gates a telemetry log line, not creation. Popoto `get_or_create` (`~/src/popoto/src/popoto/models/base.py:1760-1836`) is get-then-create with a post-hoc retry, not atomic, and `session_id` is not unique. No `SET NX`-style model create exists. The repo's external-key precedent is the raw `SET NX EX` run-claim at `models/session_lifecycle.py:856`.
- **Confidence**: high
- **Impact on plan**: Gap C adds `idempotency_key` and optional preallocated `agent_session_id` to `_push_agent_session`, guarded by an NX key in the control namespace, placed after the stale-terminal reconcile at `:361` and before `async_create` at `:370`.

### spike-3: reflection registration fleet-wide
- **Assumption**: "A new controller tick runs on the fleet by adding a Python module plus a registry entry in the repo."
- **Method**: code-read
- **Finding**: Half true. `config/reflections.yaml` is a gitignored per-machine copy; the source of truth is `~/Desktop/Valor/reflections.yaml` in the iCloud vault. The tracked registration path is `scripts/update/reflection_register.py::register_reflection` (line 475) called from `scripts/update/run.py` (precedent: `register_crash_recovery` near line 1158); it appends idempotently to the vault file on the machine that owns the `valor` project, and other machines receive it via iCloud plus the `/update` Step 1.66 copy. It emits `execution_type: function` entries only; an agent-type (`command:`) reflection has no tracked helper and must be hand-added to the vault. Machine pinning is `project_key` plus `projects.<key>.machine`, enforced at update time by `tools/reflection_machine_filter.py` (unknown `project_key` fails open and runs everywhere). `/update` regenerates the YAML on every run. `docs/features/adding-reflection-tasks.md` says "register in `config/reflections.yaml`" and omits the vault, so a literal reading produces a registration clobbered on the next update.
- **Confidence**: high
- **Impact on plan**: the collection tick registers through `reflection_register.py` in `run.py`; the bounded LLM planning session is dispatched by the collection tick through the scheduler adapter rather than as an agent-type reflection, which removes the vault hand-edit. Documentation task corrects `adding-reflection-tasks.md`.

### spike-4: per-arm Redis isolation path
- **Assumption**: "A candidate arm subprocess can be pointed at an isolated Redis via `REDIS_URL` and nothing on the memory hook path falls back to production."
- **Method**: code-read
- **Finding**: `REDIS_URL` at the subprocess is necessary and not sufficient. Env plumbing works: `_harness_env` (`agent/session_executor.py:2194`) is an additive overlay that never strips `REDIS_URL`; it flows through `agent/session_runner/runner.py:520` and `role_driver.py:204` to `harness/claude.py:459-462`, where `proc_env.update(env)` lets a `_harness_env` value override the worker's. Popoto binds once at import; hooks and MCP servers are fresh processes inheriting the arm env. Two leaks remain. First, partition: no `"valor"` is hardcoded on the hook path, but `config/project_key_resolver.py:89` resolves by `projects.json` working-directory prefix, so an arm in an `ai` worktree resolves to the production key, and `.claude/hooks/hook_utils/memory_bridge.py:551-552,683-684` falls back to `DEFAULT_PROJECT_KEY` (`"default"`) on `None`. `_harness_env` has no `VALOR_PROJECT_KEY` entry. Second, writers: post-session extraction (`.claude/hooks/stop.py:134` → detached `Popen(env=dict(os.environ))` at `stop_detach_worker.py:246`), prompt ingest (`.claude/hooks/hook_utils/memory_bridge.py:803-833`), retrieval-side decay counter bumps (`agent/memory_retrieval.py:116`), and post-merge extraction (`.claude/hooks/hook_utils/memory_bridge.py:997`) all inherit the arm env and would mutate a fixed corpus; the decay/prune reflection (`reflections/memory/memory_decay_prune.py` via `reflections/memory_management.py:14`) runs in the reflection process with a hardcoded `"valor"` fallback at `reflections/redis_access.py:32` and is not isolated by the arm env at all. The flush guard (`tools/redis_flush_guard.py:81`) reads only the `db` number, never host or port, so `redis://localhost:6390/0` is treated as production; `tests/db_claim.py:342` sidesteps this with a non-zero db.
- **Confidence**: high on env plumbing and flush guard; medium on the decay reflection's cadence.
- **Impact on plan**: Gap E sets both `REDIS_URL` and `VALOR_PROJECT_KEY` in the arm's `_harness_env`, uses a non-zero db on the private instance, adds an env-gated kill switch before `try_reserve_detach_slot()` in `stop.py` and the prompt-ingest path, pins the decay counter on the read path for trials, and pauses the decay/prune reflection for the trial window. Startup asserts the resolved project key equals the arm's assigned key.

### spike-5: budget and content-store seams
- **Assumption A**: "Per-session actual spend is available after completion with enough provenance to settle a reservation."
- **Method**: code-read
- **Finding**: Mostly holds, with one real gap. `total_cost_usd` has one writer, `accumulate_session_tokens` at `agent/sdk_client.py:191` (sum at `:286`), fed from the `claude -p` stream-json `result` event via `agent/session_runner/harness/claude.py:782` and `:1243`. A turn killed before its `result` event (timeout, teardown SIGKILL at `claude.py:1308-1325`) records zero of that turn's spend. `agent/tool_budget.py:123` reads the same field inside a PreToolUse evaluator, so within a turn it sees only prior turns' settled spend and cannot fire on the first paid call of the current turn.
- **Assumption B**: "Popoto `ContentField` accepts a per-field store instance."
- **Finding**: Holds. `ContentField(store=...)` (`~/src/popoto/src/popoto/fields/content_field.py:81`, property at `:94`); a separate `FilesystemStore(base_path=...)` instance affects nothing else. Local precedent: `models/knowledge_document.py:50`, `models/document_chunk.py:42`. `FilesystemStore.delete()` (`stores/filesystem.py:170-187`) unlinks the live path only; `.versions/` copies survive and remain loadable. The `garbage_collect()` its docstring names does not exist.
- **Confidence**: high on both
- **Impact on plan**: revised 2026-09-06. Claude work runs on the subscription, so `total_cost_usd` is informational for it and the killed-turn undercount costs nothing that matters; the enforced Claude unit is one SDLC lane of concurrency instead. Dollar settlement applies only to external (OpenRouter) calls, which return per-call usage in the response envelope and so have no killed-turn hole. `agent/tool_budget.py` stays a backstop. Experiment artifacts use a dedicated verifying store instance under their own retention root; a pin mechanism is deferred until a GC exists. See Gap D.

## Data Flow

1. **Entry point**: real work happens. A Job's goal version is appended, an AgentSession finalizes, a human sends a correction, an issue changes label, a nightly test fails, a poll is answered.
2. **Observer adapters** (function reflections on the scheduler): each owns a durable cursor, reads its source through the ORM or GitHub, deduplicates by stable source ID, and writes `ImprovementEvidence` rows with coverage and lag metadata. Partial data is marked partial. One adapter reads Tom-sourced memories (`Memory` rows with `source="human"`) as inspiration, turning links and remarks into evidence and hypothesis seeds without asking him anything.
3. **System model**: a bounded planning session reads fresh evidence and proposes `ImprovementModelRevision` deltas (claims, relations, confidence, competing explanations). The observer never turns an inference into a user requirement.
4. **Planner tick**: reviews evidence, stalled investigations, coverage, and the capability frontier; chooses investigate, experiment, defer, retire, or escalate for an `ImprovementCase`; records rationale and the evidence that could change it. Every choice is a proposed action, not an effect.
5. **Control journal**: the proposed action becomes a transition request carrying expected revision, epoch, and action ID. A Lua script verifies ownership, advances the head, and appends the journal entry. Flat Popoto records are projections repaired by replay.
6. **Scheduler adapter**: for actions with effects (dispatch a session, deliver a question, reserve budget), the adapter validates charter, journal authorization, reservation, and action type, then materializes an `admitted` top-level AgentSession bound to the action ID and flips it to `pending`.
7. **Investigation or experiment session**: runs in its own worktree and venv; for experiments, in per-arm isolated Redis with a frozen memory corpus; submits results tagged with action ID and epoch. A stale epoch's result is recorded as evidence and rejected as a transition.
8. **Evaluation**: the independent runner executes paired incumbent and candidate trials against a frozen contract, applies blinded judges wrapped in the consensus judge envelope, computes paired bootstrap intervals with repeated-selection correction, and writes an `ImprovementEvaluation` verdict artifact by digest.
9. **Decision and release**: inconclusive returns to investigation with a new registered action; reject closes the case with evidence; qualify produces an `ImprovementRelease` and, for a core-workflow change, a PR through the ordinary SDLC pipeline carrying the verdict and contract digest (charter §6); nothing merges by any other path.
10. **Output**: dashboard partials under `/_partials/improvement/` render coverage and intervention burden in this build; cases, hypotheses, spend, and release lineage arrive with the lanes that first write them. No branch of this flow sends a routine research question to a human. Unresolved factual claims become provisional assumptions carrying their evidence, revisited when new evidence arrives; a decision that needs authority the charter has not granted is deferred and surfaced as a charter amendment request (Gap F), never quietly narrowed.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #411 autoexperiment | Overnight loop: extract prompt, LLM proposes edit, re-score, keep if higher | No branch isolation (branch name assigned, never checked out); single ungrounded judge with strict-inequality accept, so judge noise alone yields roughly half spurious "improvements"; exceptions dropped from the mean silently; dry-run still mutates source and spends money; default target deleted in #466 and never repaired |
| `scripts/sdlc_reflection.py` | Scrapes `- lesson:` lines from PR bodies into `docs/sdlc/{stage}.md` | Extraction with zero behavioral validation; a lesson's effect on later behavior is never measured |
| `TaskTypeProfile` aggregates | Per-task-type maturity and rework rates | Instrumentation was never wired: `rework_triggered` has no writer, so the rate is a constant zero that reads as "no rework" |

**Root cause pattern:** each attempt measured a proxy the system could inflate or that nothing populated, and none isolated the candidate from production state. This plan makes evidence, isolation, and fencing the substrate rather than features layered on later.

## Architectural Impact

- **New dependencies**: none external. Redis Lua scripting is already in use (`models/session_lifecycle.py:1357`, `agent/supervised_run.py:306`).
- **Interface changes**: `config/settings.py` gains `ImprovementSettings`; `ContentField` consumers gain a second store instance; `ui/app.py` gains one HTMX partial route family; `_harness_env` gains `VALOR_PROJECT_KEY` for research sessions; `stop.py` and the prompt-ingest path gain an env-gated kill switch. **This plan changes no queue signature.** `_push_agent_session` is #3183's to change; #3177 consumes whatever contract #3183 lands (Gap C). **This build also changes no session-status vocabulary.** `NON_TERMINAL_STATUSES` gaining `admitted` (`models/session_lifecycle.py:72`) is lane 3's edit, filed as a child issue by task 6, because lane 3 is the first thing that creates a session in that state. It is a two-file change, not one line: `tests/unit/test_recovery_ownership.py:16` asserts `set(RECOVERY_OWNERSHIP.keys()) == NON_TERMINAL_STATUSES` exactly, so the status and its `RECOVERY_OWNERSHIP` entry at `:90` must land together, and eleven further modules and tests read `NON_TERMINAL_STATUSES` (`models/session_lifecycle.py`, `models/agent_session.py`, `agent/session_health.py`, `agent/worktree_manager.py`, `tools/agent_session_scheduler.py`, `reflections/expectation_reconciler.py`, `reflections/sdlc_upvote_lanes.py`, `reflections/sdlc_progress.py`, `bridge/email_bridge.py`, plus `tests/unit/test_session_lifecycle_consolidation.py`, `tests/unit/test_session_health_orphan_process_reap.py`, `tests/unit/test_agent_session_scheduler_kill.py`, `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`, `tests/integration/test_orphan_reap_forward_scan.py`, measured 2026-09-07).
- **Coupling**: the controller depends on Job, AgentSession, the reflection scheduler, the poll registry, and the pipeline ledger through their public APIs. Nothing existing depends on the controller. The control journal introduces one new consistency boundary in a dedicated Redis namespace, authoritative for research transitions only.
- **Data ownership**: `ImprovementCase` owns research state; Job owns responsibility; AgentSession owns execution; PipelineLedger owns implementation stages; the control journal owns transition authority; flat records are projections. None substitutes for another.
- **Reversibility**: every piece is additive and behind `ImprovementSettings.enabled` defaulting to false. Removing the controller deletes flat modules, the namespace, the settings block, the partial route, and the reflection registration; production paths are untouched.

## Appetite

**Size:** Large

**Team:** Solo dev per lane, plan critic, code reviewer. The charter (v2) is written; Valor may propose amendments and Tom authorizes them.

**Interactions:**
- PM check-ins: 0 for routine research. The one message class the controller may send is an evidence-backed charter amendment request (Gap F); work continues within existing authority while it waits, and silence is not approval.
- Review rounds: 2+ per lane

Delivery is staged as seven lanes (see Step by Step Tasks). Lanes 1 and 2 (retire autoexperiment; evidence and reuse contracts) are the build this plan dispatches; lanes 3 through 7 get child issues filed from lane 2's capability matrix, each referencing #3177. Lane 7 (cloud execution capacity) exists because charter §2 expects RSI to run mostly in cloud sandboxes, around the clock, within a month of operation; the single-machine, one-lane first increment in lanes 1 through 3 is a bootstrap, and every progress report on #3177 states how far the operating model has moved toward §2 and what still prevents it. The original calendar estimate is withdrawn: reservations and memory isolation are newly explicit engineering work, and the re-estimate happens after lane 2. Lane 3 additionally depends on #3183, in two different shapes: it needs #3183's **current build** for the create-or-bind seam and the dead-letter record, and it needs the fencing **lease**, which per #3183's own §Relationship table lives in #3183's lane 6 and is itself a child issue of #3183. So lane 3 blocks on #3183 for the seam and the record, and on that grandchild issue for the lease. Nothing in lanes 1 and 2 depends on either.

**Why all eight models land in one lane when only one gets a writer.** Lane 2 ships `ImprovementCharter`, `ImprovementEvidence`, `ImprovementModelRevision`, `ImprovementCase`, `ImprovementInvestigation`, `ImprovementExperiment`, `ImprovementEvaluation`, and `ImprovementRelease`. Only `ImprovementEvidence` gets a production writer in this build; the other seven are exported schema with no instances until lanes 3, 5, or 6. That is deliberate, and it is the cheaper trade: the index-guard extension (`tests/unit/test_agentsession_index_guard_generalized.py`) and the migration marker in `scripts/update/migrations.py` are each one edit here instead of five edits spread across five later PRs, and the schema-gate docstrings get reviewed as one coherent set. The cost is that a reviewer looking at lane 2 alone sees seven empty tables. This paragraph is the answer to that, so nobody has to reverse-engineer which models are live: **`ImprovementEvidence` is live; the rest are declared.**

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable through Popoto | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; assert r.ping()"` | Control journal, records, projections |
| `gh` authenticated | `gh auth status` | Issue lifecycle adapter, child-issue filing |
| Interpreter on pin | `.venv/bin/python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); assert sys.version.startswith(pin), (pin, sys.version)"` | Worktree venvs for candidate lanes inherit the pinned interpreter |
| `OPENROUTER_API_KEY` present | `.venv/bin/python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENROUTER_API_KEY')"` | Cross-vendor judge in evaluation |

Run via `python scripts/check_prerequisites.py docs/plans/recursive-self-improvement.md`.

## Solution

### Key Elements

Three explicit layers:

- **Research reasoning**: bounded LLM sessions that read evidence, revise the system model, generate hypotheses with mechanism and falsifier, and propose actions. Natural-language research instructions live in one narrowly scoped skill; authority lives in code.
- **Control journal**: a small non-Popoto Redis namespace, keyed by project and research case, whose Lua transition script is the sole authority for research-state transitions, leases, dispatch intents, and budget reservations. Flat Popoto records project it for queries and hold detailed payloads.
- **Existing execution infrastructure**: Jobs, AgentSessions, the reflection scheduler, worktree and venv isolation, the SDLC pipeline, the poll registry, the content store, and `memory_eval` statistics. Reused through public APIs; extended at named seams.

Three execution authorities remain as in the original design. Research authority reads scoped evidence, authors hypotheses, requests investigations, and proposes experiments within the charter. Candidate authority modifies declared candidate surfaces in isolation and cannot read holdout answers, alter release policy, touch production state, or contact stakeholders. Evaluation and release authority runs frozen contracts and writes access-controlled verdicts under a separate process identity. A worktree is not a security boundary: candidate execution never holds evaluator secrets or production credentials, and that isolation is a technical protection that stands regardless of who approves a merge (charter §6).

### Flow

Real work → observer adapters write evidence → planner tick proposes an action → control journal admits it (fence, reservation, intent) → scheduler adapter materializes an `admitted` session and activates it → session runs isolated and submits a tagged result → evaluation runner scores paired trials blind → decision (inconclusive / reject / qualify) → dashboard and, for qualify, a release record for human review → evidence feeds the next tick.

### Technical Approach

#### Grounding corrections (from #3177 recon and the accepted revision proposal)

| Plan area | Correction |
|---|---|
| Autoexperiment | Retire first, standalone, with no replacement dependency. Never imply observed production optimization; it never ran here. |
| Session evidence | `models/session_log.py` is a nine-line shim. Evidence sources are `models/agent_session.py`, `models/session_event.py`, and the actual transcript and event producers; coverage is identified by producer, not by model existence. |
| Job expectations | Both existing directions are obligations the system owes (requester→us, PM→lane). No agent→human expectation exists; the observer records human interventions as evidence rather than as expectations. |
| TaskTypeProfile | Excluded as a rework baseline. Historical zeros are unavailable evidence, never low rework. **Settled: lane 2 deletes `models/task_type_profile.py` whole**, plus its live writer `update_task_type_profile` (`:133`) and the `finalize_session` block that calls it (`models/session_lifecycle.py:577-587`), plus its keyspace via a registered migration. Rework is derived from `ImprovementEvidence` rows with `classification="architectural"`, which have a real writer. |
| Model layout | Flat modules `models/improvement_charter.py`, `models/improvement_evidence.py`, `models/improvement_model_revision.py`, `models/improvement_case.py`, `models/improvement_investigation.py`, `models/improvement_experiment.py`, `models/improvement_evaluation.py`, `models/improvement_release.py`, each exported from `models/__init__.py` with the schema-gate docstring. No `models/improvement/` package. |
| Configuration | `ImprovementSettings(BaseModel)` in `config/settings.py` beside `FeatureSettings` and `HybridEvalSettings`, env-overridable with the `__` nested delimiter. Immutable charter versions are `ImprovementCharter` records in Redis. No `config/improvement.yaml`. |
| UI | Routes are inline in `ui/app.py` (no `ui/routers/`); data layer under `ui/data/improvement.py`; templates under `ui/templates/improvement/`; sync `def` handlers; read-only. `docs/features/web-ui.md` is corrected in the same lane. |
| Scheduling | Controller ticks are function reflections registered through `scripts/update/reflection_register.py` from `scripts/update/run.py`, pinned to the `valor` project's owning machine. No agent-type reflection entry, so no vault hand-edit. |
| Shared substrate | The create-or-bind seam, the renewed fencing lease, and the dead-letter record belong to #3183. This plan states the contract it needs and consumes the result. |
| Human questions | No routine research questions (charter §9). The controller resolves uncertainty from memory, logs, work history, pending issues, saved links, and online research, and records what it still does not know as a provisional assumption that cannot redefine an outcome, erase a requirement, grant authority, or raise a budget. One question class exists: an evidence-backed charter amendment request, which Tom authorizes or declines. New assumptions are pushed to Tom on Telegram every three days as a status report that says silence validates nothing (§11). |
| North star | The charter is a tracked file, `docs/improvement-charter.md` (v2), seeded into `ImprovementCharter` by digest in lane 2. Every case, assumption, and release carries the digest it served; a case with no ranking rationale against charter §3 is refused at `valor-improve propose`; the charter is never a candidate surface (Gap G, charter §12). |
| Capability and secrets | Valor may acquire and integrate resources for RSI, create service accounts, accept ordinary terms, and use his browser, personal and work Google Workspace accounts, virtual debit card, funded Cloudflare account and CLI, and vault within two budgets: $10/day paid inference and $50/week infrastructure (charter §8). Availability of each resource is verified by probe before anything relies on it. Newly issued credentials go to the vault through `tools/vault_write.py`. No revenue activity. |
| Providers | Open-source work may run on any provider within the inference budget; regular client work and its private context stay on the Claude and Codex subscriptions (charter §7). Eligibility is derived from the project's GitHub visibility and fails closed to "client". No Codex harness exists in `agent/session_runner/harness/` today; that is recorded as unknown in the capability matrix, not assumed. |
| Merge authority | An evidence-backed core-workflow change merges through the ordinary pipeline after representative comparison, no material quality regression, normal review, and repository gates, with no extra approval from Tom (charter §6). Deployment mechanics are unchanged. |

#### Citation corrections (re-derived 2026-09-06)

Every file:line this revision touches was located by symbol against the working tree, not carried forward. Two were wrong.

| Prior citation | Corrected | How it was verified |
|---|---|---|
| `ui/data/sdlc.py:469` described as `ACTIVE_STATUSES` | `ACTIVE_STATUSES` is the module constant at `ui/data/sdlc.py:1256`. `:469` is a separate inline literal, the return of the `is_active` property defined at `:468`: `return self.status in ("pending", "running", "active", "waiting_for_children")`. A third `is_active` at `:244` compares a stage status and is unrelated. | `grep -n "ACTIVE_STATUSES\|def is_active" ui/data/sdlc.py` |
| `hook_utils/memory_bridge.py` (5 sites in spike-4 and Gap E) | `.claude/hooks/hook_utils/memory_bridge.py` | `ls .claude/hooks/hook_utils/memory_bridge.py` |
| `models/session_lifecycle.py:90` for `RECOVERY_OWNERSHIP` | Correct: the dict opens at `:90` and its header at `:88` states "This is an informational constant; it is not used for runtime routing." That header is why Gap A now names a real recovery pass instead. | `sed -n '85,95p' models/session_lifecycle.py` |
| Non-Popoto raw-Redis exemption comment | `models/session_lifecycle.py:885`: "The lock key is NOT Popoto-managed, so raw Redis GET/SET/EXPIRE/EVAL here is fine and already the established pattern." The Lua CAS precedent is `_R.eval` at `:1357`; the `POPOTO_REDIS_DB as _R` import in that file is at `:853`, `:1282`, and `:1501`. | `grep -n "NOT Popoto-managed\|_R.eval\|POPOTO_REDIS_DB as _R" models/session_lifecycle.py` |
| `NON_TERMINAL_STATUSES` at `models/session_lifecycle.py:72`; `finalize_session` at `:233` | Both correct. | `grep -n "NON_TERMINAL_STATUSES = \|^def finalize_session" models/session_lifecycle.py` |
| `models/agent_session.py:221` | Correct and load-bearing: `rework_triggered = Field(null=True)` is a dead field with no writer, which is exactly why the old Verification row passed vacuously. `tools/session_tags.py:156,181,189` reads it; `models/task_type_profile.py:62,94,196` aggregates it. | `grep -n "rework_triggered" models/agent_session.py models/task_type_profile.py tools/session_tags.py` |

#### Reuse map

| Need | Reuse | Extension at the seam |
|---|---|---|
| Intended outcome and goal version | `Job.append_goal_version`, append-only goal JSON | none |
| Implementation lineage | `agent/pipeline_ledger.py` | none |
| Inspiration from Tom without asking him | `Memory` rows written with `source="human"` by `.claude/hooks/hook_utils/memory_bridge.py:801-836`; read through `tools/memory_search/__init__.py::search` (`:57`) | an observer adapter filters `project_key="valor", source="human"`, dedupes by memory id, and writes `ImprovementEvidence` rows of kind `inspiration` carrying the original text, its `reference` (URL or pointer), and its timestamp |
| Online research on current practice | `WebSearch` and `WebFetch` inside the bounded research session | each search or fetch becomes an `ImprovementInvestigation` row of kind `web_research` with the query, source URLs, retrieval date, and the claims extracted; a claim with no URL and date is not a claim |
| Worker liveness before activation | `worker:registered_pid:*` (prefix constant `agent/session_health.py:216`, written by `register_worker_pid` at `:5269`, scanned at `:6463`, freshness at `_worker_pid_heartbeat_fresh` `:5393`) | the scheduler adapter requires at least one fresh key before flipping `admitted` to `pending` |
| Bootstrap confidence intervals | `tools/memory_eval/metrics.py` | import; add per-endpoint thresholds, clustered resampling by project, repeated-selection correction outside it |
| Judge record shape | `agent/sdlc_review_consensus.py` (`judge_id`, `verdict`, `blockers`, confidence) | wrap with experiment ID, contract digest, evaluator version, trial ID, raw-response reference, blinded arm ID |
| Large artifacts | Popoto `ContentField(store=...)` with a dedicated `FilesystemStore` subclass under a retention root | verifying subclass re-hashes on every load including the archive path |
| Digest strings | `tools/sdlc_verdict.py:135-224` `"sha256:<hex>"` normalized form | none |
| Scheduler ticks and timeouts | `agent/reflection_scheduler.py` (`asyncio.wait_for`, startup concurrency cap) | none |
| Top-level session creation | `_push_agent_session` / `valor_session.create_session` | none from this plan. #3183 owns the idempotent create-or-bind seam; #3177 states the contract it needs and calls it (Gap C) |
| Execution fence | `agent/pid_fence.py::fence_is_live` | none |
| Renewed lease with fencing generation | #3183's lease module (delivered by **#3183's lane 6, a child issue of #3183**), itself modeled on the `models/session_lifecycle.py:1357` Lua CAS | none from this plan. The control journal imports it wherever it needs a lease (Gap A) |
| External-LLM spend | `tools/cross_vendor_judge.py` over the OpenRouter endpoint in `config/models.py:56`, per-call usage in the response envelope | per-call settlement against the daily dollar reservation (Gap D) |
| North-star text | `docs/improvement-charter.md`, human-owned, git-versioned | `models/improvement_charter.py::load_from_file` hashes the file with the `tools/sdlc_verdict.py` `"sha256:<hex>"` form and creates one immutable `ImprovementCharter` row per new digest; the controller tick calls it first and pins the current digest on every action |
| Secret placement | 1Password `valor-local` service account, `op` non-interactive (`CLAUDE.md` Secrets; `docs/plans/migrate-secrets-to-1password-op-cli.md`) | one sanctioned module, `tools/vault_write.py`, wrapping `op item create` against `m-valor`; it never returns, logs, or echoes the value, and it is the only improvement-path module allowed to touch `op` |
| Correction detection | `reflections/utilities.py::CORRECTION_PATTERNS` (`:37-46`), consumed transiently by `_analyze_sessions_from_redis` (`reflections/session_intelligence.py:34`, pattern loop at `:71`; imported at `:22`) | promoted to a persisted, typed detector emitting `SessionEvent` kinds `intervention` and `correction` with a classification field |
| Existing evidence computation | `reflections/expectation_reconciler.py::_shipped_evidence` and owner liveness | persisted instead of discarded |

#### Gap A: control journal, leases, and fencing

A dedicated Redis namespace `improve:{project_key}:{case_id}:*` with explicit schema version, holding only what is research-specific: the case head, its revision, action IDs, and reservations. Anywhere it needs a lease it calls **#3183's lease module**; this plan writes no second lease implementation.

**Raw-Redis guard, stated honestly.** The guard at `.claude/hooks/validators/validate_no_raw_redis_delete.py` is a Bash-command *text* heuristic with no namespace or key awareness: `find_violation()` fires when one command string contains both a `_POPOTO_CONTEXT` substring (the bare literals `POPOTO_REDIS_DB`, `popoto`, `Job`, `AgentSession` among them) and a block pattern such as `\br\.delete\(`. It is not scoped to Popoto keys, so it can and will misfire on a plain `improve:*` key. Two consequences, both binding:

- The control journal binds its client under a private alias (`_journal_redis`) obtained in its own module, never `from popoto.redis_db import POPOTO_REDIS_DB as _R` in a file where an operator would type a debug one-liner. This keeps ad hoc verification off the `_POPOTO_CONTEXT` tripwire.
- Compare-and-delete on release is exercised through a pytest file, never an inline `python -c` in a Bash command.

The exemption for raw Redis on a non-Popoto key is real and already stated in this repo at `models/session_lifecycle.py:885` ("The lock key is NOT Popoto-managed, so raw Redis GET/SET/EXPIRE/EVAL here is fine and already the established pattern"), with the working Lua CAS precedent at `:1357`. A namespace review stays a critique gate.

- **Head and journal**: `head` holds `{revision, state, epoch, owner, updated_at}`; `journal` is a bounded list of `{revision, action_id, event, payload_digest, ts}`. One Lua script `transition(expected_revision, epoch, action_id, event, payload_digest)` verifies ownership and epoch, checks `expected_revision == head.revision`, advances the head, and appends. Rejections return a reason code, never raise.
- **Leases**: acquisition, renewal, and release are #3183's, including the monotonic fencing generation and Redis-server-time (`TIME`) expiry. The rule this plan depends on and will assert in its own tests: the accept rule at effect boundaries is `generation >= highest_accepted`, because a strictly-greater rule refuses the holder's own second write.
- **Fencing at effects**: dispatch, budget authorization, and release each re-check the generation inside the same script call that records the effect. A lease check followed by an unguarded effect is not fencing. Result submissions from workers carry action ID and generation; a stale generation's artifact is stored as evidence and its transition is refused.
- **Projection**: flat Popoto records are updated through ORM `save()` after the journal commit; a replay tool rebuilds any projection from the journal. Decisions read the head, never the projection.
- **Artifacts first**: payloads are written to the content store and hashed before the transition references their digest. An artifact with no committed event is an orphan eligible for delayed cleanup.

**Recovery for `admitted` and `materialized` (critique blocker 1).** `RECOVERY_OWNERSHIP` at `models/session_lifecycle.py:90` is explicitly informational: its header at `:88` says "This is an informational constant; it is not used for runtime routing." Adding `admitted` to it establishes nothing. The real recovery pass is a **reconciliation reflection**, `improvement-intent-reconcile`, registered through the same `scripts/update/reflection_register.py::register_reflection` seam as the controller ticks:

- It scans `improve:{project}:*:intent:*` for `state in {admitted, materialized}` older than a lease-derived staleness threshold. It reads **intents**, not a status map, so it does not depend on `RECOVERY_OWNERSHIP` for anything.
- For each stale intent it CAS-transitions the journal entry to `reconciliation_required`, releases the reservation, and calls the existing `finalize_session()` (`models/session_lifecycle.py:233`) to force the orphaned row terminal. It never mints a second identity.
- It runs on its own cadence, so an outage that outlives the restart-triggered pass is still swept.

**Availability and break-glass.** If the namespace is unreachable the controller pauses and reports; it never falls back to projection state. A pause is not self-clearing, so it needs a hand on it:

- `valor-improve pause [--case ID] [--reason TEXT]` sets the pause explicitly; `valor-improve resume [--case ID]` clears a paused head after re-reading it, and refuses to resume a case whose intents are still `reconciliation_required` unless `--force` is given.
- `valor-improve doctor` prints every paused head, every stale intent, and every outstanding reservation, so the operator sees the blast radius before resuming.
- The manual procedure (how to tell a namespace outage from a wedged case, what to check, what to run, and what evidence to keep) is documented in `docs/features/improvement-controller.md` under "Break-glass", and is a Documentation-section task.

#### Gap B: dispatch while child spawning stays gated

The gate in `agent/agent_session_queue.py:259-265` fires only when `parent_agent_session_id` is set. The reflection scheduler already creates top-level sessions with no parent. So top-level scheduler-owned dispatch exists today; what is new is the agent-proposes, scheduler-admits handshake and a per-controller fanout bound.

- Research ownership belongs to the `ImprovementCase` and its Job, never to a parent executor session. Sessions carry `research_case_id`, `experiment_id`, and `action_id` as provenance fields.
- Only the scheduler adapter (a function reflection) submits research sessions, after validating charter, journal authorization, reservation, and allowed action type. A planning session proposes an action by writing a journal event; it cannot enqueue.
- **Worker liveness before activation (#1312 / PR #2196, no overlap).** Before flipping an `admitted` session to `pending`, the adapter requires at least one fresh `worker:registered_pid:*` key: prefix constant `WORKER_REGISTERED_PID_KEY_PREFIX` at `agent/session_health.py:216`, written with TTL by `register_worker_pid` at `:5269`, read by the `scan_iter` pattern at `:6463`, with per-PID freshness available from `_worker_pid_heartbeat_fresh` at `:5393`. With no live worker the adapter leaves the session `admitted` and records the reason, so a research session never rots in a queue nothing is draining. Reuse is by convention, not by a public helper: no `is_a_worker_alive()` exists today, so lane 3 extracts one from the existing scan rather than duplicating it inline.
- Fanout bound: `ImprovementSettings.max_concurrent_research_sessions` (default **1**, an operating choice per charter §8, env-overridable, never a charter cap) enforced as a reservation in the control namespace, on top of the worker's global `MAX_CONCURRENT_SESSIONS`. See Gap D for why the Claude unit is a lane rather than a dollar figure.
- Honest framing: any session can already call `valor-session create` without `--parent`. This adapter does not close that hole; it declines to widen it and gives research sessions a provenance that audits can filter on. The gate and #1633 stay untouched; `VALOR_ALLOW_CHILD_SESSIONS` is never set by the controller.
- Until the adapter passes critique, observation and investigation planning run; experiment execution is operator-dispatched.

#### Gap C: durable dispatch intents

- **Record**: `improve:{project}:{case}:intent:{action_id}` with request digest, charter version, reservation ID, preallocated `agent_session_id`, state, attempts, and result artifact references.
- **Lifecycle**: `prepared → admitted → materialized → running → result_recorded → settled`, plus `cancelled` and `reconciliation_required`.
- **Queue seam: consumed, not built.** #3183 owns the idempotent create-or-bind seam on `_push_agent_session`. This plan changes no queue signature. The consumer contract #3177 lane 3 requires from #3183, stated as a requirement rather than an implementation:
  - **In**: an idempotency key. Two calls with the same key produce one row.
  - **Out**: the bound `agent_session_id`, reachable by the caller. The prior draft said the seam "returns the existing row", which is unimplementable: `_push_agent_session` has one return statement, `return await AgentSession.query.async_count(chat_id=chat_id, status="pending")` at `agent/agent_session_queue.py:460`, typed `int` (queue depth). How the id is handed back (a widened return type, an out-parameter, or a read-back keyed by the idempotency key) is #3183's design choice; #3177 needs only that it is reachable and stable across retries.
  - **At creation**: `status="admitted"`. `async_create` at `agent/agent_session_queue.py:370` currently passes the literal `status="pending"`, so the seam must accept a caller-supplied status. #3177 does not add that parameter itself.
  - **On replay**: request-digest equality is validated and a mismatched replay is refused.
- **Dependency.** If #3183 has not landed when lane 3 starts, **lane 3 blocks on it.** Lane 3 does not fork a private copy of the seam, and it does not open a competing PR against `agent/agent_session_queue.py`. Lanes 1, 2, 4, and 5's non-dispatch work are unaffected.
- **Inert materialization** (all of this is **lane 3's** work, not lanes 1-2): the session is created with `status="admitted"`, a new value added to `NON_TERMINAL_STATUSES` (`models/session_lifecycle.py:72`) together with its `RECOVERY_OWNERSHIP` entry at `:90` in the same change, because `tests/unit/test_recovery_ownership.py:16` asserts the two key sets are exactly equal. That `RECOVERY_OWNERSHIP` entry carries documentation value only; the constant is informational (`:88`) and establishes no recovery, which is why the reconciliation reflection in Gap A exists. `admitted` also joins the dashboard's active-status definition. Lane 2 (task 4) collapses that definition to one place first — the module constant `ACTIVE_STATUSES` at `ui/data/sdlc.py:1256` and the inline literal returned by the `is_active` property at `ui/data/sdlc.py:469` (defined at `:468`) are different things that disagree today — so lane 3 edits one site rather than two, and a builder greping for `ACTIVE_STATUSES` cannot find one and leave `admitted` reading as inactive on the other path. The worker never selects `admitted`. The adapter flips it to `pending` after binding and after the liveness check in Gap B, and the worker validates the current dispatch generation at pickup through the provenance fields.
- **Recovery**: on restart, reconcile by action ID: retry materialization or activation, never mint another identity. The `improvement-intent-reconcile` reflection in Gap A covers intents the restart pass misses. Session completion without the required result artifact leaves the intent at `running` and the case at `experimenting`, surfaced as incomplete research.
- **Dead letters**: an intent that exhausts its attempts is written to **#3183's one dead-letter record**, with `stage="improvement-intent"`. This plan defines no second dead-letter sink.
- The design promises idempotent admission and recoverable at-least-once execution. External effect adapters carry their own idempotency.

#### Gap D: three reservation units, one of which is not money

Claude and Codex subscription work is metered in concurrency, and the killed-turn undercount spike-5 found is an accounting artifact of a meter nobody is billed by. Charter §8 then authorizes two dollar categories that must never be pooled or moved between. The budget therefore has **three units**, reserved and settled separately, with window boundaries disclosed on every report.

**Unit 1: the subscription lane slot.** The scarce subscription resource is concurrency, not money.

- `ImprovementSettings.max_concurrent_research_sessions = 1`: one SDLC lane of concurrency, the same shape as any other lane on this machine. Charter §8 calls this an operating choice, so it is env-overridable and lane 7 revisits it when sessions run in sandboxes. Admission checks `outstanding_slots + 1 <= max_concurrent_research_sessions` atomically in the Lua call that admits the intent.
- A slot is held by the intent's action ID and released on session termination. `AgentSession.total_cost_usd` (written by `accumulate_session_tokens`, `agent/sdk_client.py:191`) is recorded on the intent as **informational provenance** and is never a gate for subscription work.
- **Release fires on every termination path.** The slot release and the intent's settlement transition live inside `models/session_lifecycle.py::finalize_session` (`:233`) as a post-transition side effect, gated on the session carrying reservation and action-ID provenance. `finalize_session` is the common floor reached from the Stop hook, `agent/session_health.py`, `worker/__main__.py`, and `tools/agent_session_scheduler.py`, so a watchdog-finalized or crash-recovered session releases its slot exactly like an ordinary completion. The `improvement-intent-reconcile` reflection (Gap A) is the backstop for a slot whose session row vanished entirely.

**Unit 2: daily paid inference, $10.** Every paid token call that is not subscription-covered: OpenRouter judges and bulk evaluators today (`tools/cross_vendor_judge.py` over `config/models.py:56`), and any provider lane 5 integrates for eligible open-source work.

- `ImprovementSettings.daily_paid_inference_usd = 10.00`, a reservation in the control namespace keyed by UTC date (`budget_day_boundary="UTC"`, disclosed on every report). Admission checks `settled_today + outstanding + requested_max <= daily_paid_inference_usd`. Controller, workers, and evaluators share the total and hold separate reservations from it, so a runaway controller cannot consume what evaluation needs.
- **Per-call settlement.** Providers that return usage in the response envelope settle immediately; a call that never returns stays reserved until the reconciliation reflection releases it. Duplicate receipts are idempotent. A provider with no usage envelope is metered at its published list price per token from a dated `ImprovementInvestigation` row, and marked `metering="estimated"`.
- Exhausting the day pauses paid inference only; lane-slot work continues. **Unknown or missing metering is not zero cost** (charter §8): a call with no settlement and no estimate pauses paid inference until reconciled.

**Unit 3: weekly infrastructure, $50.** Sandboxes, storage, Cloudflare services, and any other RSI infrastructure, tracked together.

- `ImprovementSettings.weekly_infrastructure_usd = 50.00`, keyed by ISO week starting Monday 00:00 UTC (`budget_week_start="monday"`, disclosed). Admission for a recurring resource reserves its charge for the remainder of the current window and forecasts the next; a resource whose charge cannot be forecast is refused.
- **Credits and promotions.** Free tiers, credits, and promotions are pursued autonomously and each is recorded with its expiry and the paid rate that follows it, so a free promotion never silently becomes paid usage. A credit that expires inside the window converts to a forecast charge on the day it expires.
- Metering comes from provider billing APIs where they exist (Cloudflare's billing endpoints, sandbox provider usage) and otherwise from receipts recorded as `ImprovementEvidence` rows of kind `spend_receipt`. Exhausting the week pauses new acquisition; running resources are not torn down by the budget alone, because tearing down a sandbox mid-trial destroys evidence. Lane 7 owns the teardown policy.
- No transfer between units 2 and 3. Charter §8 forbids moving spend between categories to evade a limit, and the Lua admission script has no code path that reads one unit's headroom for the other.

`valor-improve budget` (lane 3) prints all three units with window boundaries, settled, outstanding, forecast, credit expiries, and every `metering="unknown"` receipt.

**Cheaper token sources are a research target, not an assertion.** `config/models.py` already carries a free-tier entry (`OPENROUTER_GEMMA4_FREE`, `google/gemma-4-e2b:free`, `:139`) and a low-cost experiment model (`OPENROUTER_KIMI_K2_5`, `:133`), so the cheap-evaluator path exists. Tom named **"Meta Muse 1.3"** as a candidate nearly-free token source. This plan asserts nothing about it: no pricing, no availability, no capability, no API shape. Charter §3's first priority (discover inexpensive inference, evaluate task suitability, integrate into eligible sessions) is expected to reach it early; the action verifies current provider documentation by `WebSearch`/`WebFetch`, records what it finds as an `ImprovementInvestigation` row with source URLs and retrieval date, evaluates suitability on representative tasks (low price is not evidence of suitability, charter §7), and only then proposes an adapter.

**What tooling cannot enforce.** `agent/tool_budget.py` remains a second-line backstop; it reads `total_cost_usd` inside a PreToolUse evaluator (`:123`), so within a turn it sees only prior turns' settled spend and cannot fire on the first paid call of the current turn. It is never the enforcement mechanism. Hosted harnesses expose no hard per-call ceiling, so the charter distinguishes a conservative admission estimate from an enforceable ceiling and states the bounded-overshoot policy for external calls. No claim of a strict dollar bound is made where tooling cannot enforce one.

**Human attention is not a budget line, because it is not spent.** There are no routine research questions to Tom (Gap F). Observed intervention burden is still measured and published beside rescue incidence, as an outcome of the system's behavior rather than a resource it draws down.

#### Gap E: immutable memory inputs and paired-arm isolation

- The first experiment uses a fixed evidence corpus exported through ORM reads into the experiment content store: complete memory fields consumed by retrieval, embedding bytes, embedding model and dimension, retrieval parameters, reference maps, and the effective retrieval clock. The manifest and bytes are hashed. Neither arm reads live production memory.
- An export of a changing partition is not a point-in-time snapshot. The default is to define the experiment corpus as the exact exported immutable dataset with a disclosed collection interval and no point-in-time claim. Experiments that need temporal coherence first implement a partition write barrier or a versioned export protocol.
- Each arm runs as a subprocess whose `_harness_env` sets both `REDIS_URL` (a private Redis process on a private port, non-zero db so the flush guard's db-0 rule stays honest) and `VALOR_PROJECT_KEY` (the arm's own key, so `project_key_resolver` does not prefix-match the worktree to production). Startup asserts the resolved key equals the assigned key and refuses otherwise.
- Writers disabled for fixed-corpus trials, each with its trigger site (spike-4): post-session extraction at `.claude/hooks/stop.py:134` and the prompt-ingest path in `.claude/hooks/hook_utils/memory_bridge.py:803-833` gain an env-gated kill switch checked before `try_reserve_detach_slot()`; the retrieval-side decay counter bump at `agent/memory_retrieval.py:116` is pinned for trials; post-merge extraction at `.claude/hooks/hook_utils/memory_bridge.py:997` is covered by the same switch; the decay/prune reflection (`reflections/memory/memory_decay_prune.py`, reflection process, `"valor"` fallback at `reflections/redis_access.py:32`) is paused for the trial window through the scheduler's `enabled` flag, and the pause is recorded in the experiment manifest. The restriction is validated through the real hook and retrieval path.
- Test infrastructure: the pytest DB-claim fixture exports one `REDIS_URL` process-wide and cannot host two arms. Paired-arm tests launch their own Redis processes on private ports through a sanctioned helper in `tests/` extending the `redis_test_url()` pattern, each on a non-zero db.
- The isolated adapter must reproduce baseline retrieval on the frozen corpus before any scoring; a mismatch blocks scoring until explained or registered as a factor.
- Later learning-policy experiments clone the same seed into separate mutable stores and replay the same task sequence with per-arm embeddings, caches, extraction sidecars, outcomes, and decay clock.

#### Observer, system model, planner, investigations

- **Observer**: adapters for Job goal and expectation changes, session events, delivered artifacts, human corrections, issue lifecycle, nightly test failures, production incidents, and authorized stakeholder interactions. Each owns a durable cursor and reports coverage, lag, errors, and source version. Evidence is written asynchronously, deduplicated by stable source ID, and never converts missing data into success. Apparently successful tasks are randomly sampled for independent checking. Outcome packets link intended outcome and goal version, inputs at the time, artifacts and traces, interventions with classification, effort with provenance, and exact release, model, memory state, and execution records.
- **System model**: `ImprovementModelRevision` holds claims and relations with evidence references, confidence, scope, effective dates, and supersession. Two views: task intent (actor, outcome, trigger, journey steps, decisions, handoffs, recovery, completion evidence) attached to the Job goal as a referenced artifact; system capability (capability → task families → failure modes → suspected causes → interventions → measured outcomes, including missing capabilities). Context assembly delivers a bounded brief; details are fetched on demand.
- **Planner**: one bounded tick chooses investigate, experiment, defer, retire, or escalate with rationale and the evidence that could change it. Ranking is by expected contribution to the north star (charter §3): opportunity cost, quality, resource cost, uncertainty, and the capacity an opportunity unlocks for later ones, ordinal until calibration supports numbers, recorded per case as `ranking_rationale` beside the charter digest. The initial ordering favors the five §3 priorities and is revised as evidence arrives; there is no fixed allocation and no `portfolio_allocation` setting. Hypotheses name a mechanism and a falsifier; a novelty check retrieves prior investigations and rejected experiments.
- **Resource-acquisition research** is an explicit action type inside the capability-expansion allocation, not an accident of it. The mandate is to seek out and integrate anything that makes the loop faster or cheaper: nearly-free or free token sources, evaluation tooling, statistics libraries, harness features, measurement instruments. Each such action is an `ImprovementInvestigation` of kind `resource_acquisition` recording what was searched, what the provider's current documentation says, the retrieval date, and what an adapter would cost. The line is charter §8. Valor may acquire and integrate resources for RSI, create service accounts, accept ordinary terms, and use his browser (BYOB), personal and work Google Workspace accounts, virtual debit card, funded Cloudflare account and connected CLI, and the vault, within units 2 and 3 of Gap D. **Verify before relying** (charter §8): `tools/improvement_resources.py::probe()` (task 3) checks each named resource by vault item title and credential fingerprint, never value, and by a harmless authenticated read where one exists (`op whoami`, Cloudflare account read), and the capability matrix records each as verified or unknown; today `wrangler` is not installed and the vault inventory has not been listed from this session, so every one of them starts as unknown. A source that needs **no credential** is integrated behind `ImprovementSettings`, defaulting off. A source whose signup **issues a credential** is integrated the same way, and the credential is written to the `m-valor` vault by `tools/vault_write.py` (Reuse map), never echoed, with a notice to Tom that the item exists. Revenue generation, cold outreach, and anything that exposes Valor's identity to spam classification are outside the mandate (§8). Reserved to Tom: authorizing charter amendments, Valor's identity and public profiles, and human-owned approval signals (§12).
- **Investigations and the amendment path (Gap F)**: `ImprovementInvestigation` has one human-delivery kind and otherwise none. Its lifecycle is `draft → deduplicated → policy_checked → running → recorded → interpreted → applied`, with terminal `cancelled`, `superseded`, and `failed`, plus `awaiting_authorization` for the amendment kind only. Kinds are `probe` (run something and observe), `trace_analysis` (read existing execution evidence), `memory_retrieval` (mine memory, including Tom-sourced links), `inspiration_intake` (extract the substance of a forwarded link or video), `web_research`, `resource_acquisition`, `skill_acquisition` (charter §5: find or develop, vet, integrate, evaluate against prior capability, observe reuse), and `charter_amendment`. Every row stores triggering evidence, the uncertainty it addresses, the decision affected, prior answers checked, expected information value, and its sources with retrieval dates. Interpretation creates scoped claims that keep a pointer to the raw source text.
  - **Memory inspiration intake.** Tom sends ideas as links to Valor. They are already saved as `Memory` rows with `source="human"` at `.claude/hooks/hook_utils/memory_bridge.py:801-836` (`importance=PROVISIONAL_INGEST_IMPORTANCE`, `reference` holding the URL or pointer, per `models/memory.py:158-162`). An observer adapter reads them through `tools/memory_search/__init__.py::search` (`:57`) filtered to `project_key="valor", source="human"`, dedupes by memory id, and writes `ImprovementEvidence` rows of kind `inspiration`. The planner treats those as hypothesis seeds. Tom is not asked to explain them; a link he sent with no commentary is still evidence of what he thinks is worth looking at. **Intake extracts substance** (charter §4): a YouTube link goes through `valor-youtube-transcribe`, a page through `WebFetch` or `valor-ingest`, and the extracted text, source reference, and retrieval date are stored on an `inspiration_intake` investigation; a source that cannot be fetched is recorded as inaccessible, never as reviewed. Links default to inspiration. When Tom explicitly directs `do-issue`, that is requested work and takes the ordinary SDLC path, distinct from an inspiration item. A forwarded link is never permission to change the charter.
  - **Online research.** `WebSearch` and `WebFetch` on current provider documentation and current practice, recorded with query, source URLs, and retrieval date. A claim about the outside world with no URL and no date is not a claim and cannot enter the system model.
  - **No routine research questions; one permitted question class.** Research ideas, technical uncertainties, and ordinary implementation choices are resolved from evidence and investigation (charter §9). An unresolved factual claim becomes a **provisional assumption**: a recorded claim with its evidence, confidence, consequences, the charter passage it interprets, and the observation that would overturn it. An assumption cannot redefine the intended outcome, erase a requirement, grant authority, or increase a budget; `valor-improve propose` refuses an assumption whose declared effect is any of those. A decision that depends on ungranted authority is **deferred**, not narrowed, and becomes a `charter_amendment` investigation: `valor-improve propose-amendment` records the proposed text change, its expected value, and its evidence, sends Tom one plain Telegram message through the existing outbound path that names the change and asks permission, and leaves the row `awaiting_authorization`. Independent work within existing authority continues. Silence is not approval. The answer is the charter file's next committed digest (authorized) or a reply that declines; no poll descriptor, no `investigation_id` on `tools/ask_poll.py`, and no late-answer race, because nothing is bound to a message id.

#### Gap G: the charter as north star

With the question ceiling at zero, the written charter is the only place the controller can find an answer to "what would Tom want." Before this gap, the vision lived in a conversation the plan referred to and in a metrics table; `ImprovementCharter` was a declared model with unspecified content, and this plan is archived on merge. Gap G makes the charter durable, first in context, cited by construction, measured, and unwritable by the controller.

- **Source of truth is a file.** `docs/improvement-charter.md`, frontmatter `owner: Tom Counsell`, `version`, `effective`. Tom edits and commits it; nothing else writes it. Its digest is computed with the `tools/sdlc_verdict.py` normalized `"sha256:<hex>"` form.
- **Seeded by digest (lane 2).** `models/improvement_charter.py::load_from_file(path)` reads the file, computes the digest, and creates one immutable `ImprovementCharter` row per new digest carrying `version`, `effective`, `digest`, and the full text in a `ContentField`. The controller tick calls it first on every run; a new digest becomes the pinned version for actions admitted after it, and actions already admitted complete under the digest they carry (charter §12). No projection, no edit path, no `save()` on an existing row.
- **First in every brief (lane 5).** The bounded planning session's brief opens with the pinned charter verbatim. The research skill instructs: every hypothesis, case, and provisional assumption states its expected contribution to the north star (charter §3), names the charter passage it relies on, and resolves uncertainty per §9: evidence and investigation first, then a guarded provisional assumption, then deferral plus an amendment request when authority is missing.
- **Cited by construction (lane 3).** `ImprovementCase` carries `priority_area` as a low-cardinality `IndexedField` (`inference`, `token_efficiency`, `skills`, `personas`, `cloud_execution`, `research_process`, `evaluators`, `memory`, `orchestration`, `infrastructure`, `other`; `other` exists precisely so the ranking is never a fixed allocation), a free-text `ranking_rationale`, and `charter_digest`; `ImprovementInvestigation` and `ImprovementRelease` carry `charter_digest`. `valor-improve propose` refuses a case whose `ranking_rationale` is empty, whose `priority_area` is unset, or whose digest is not the pinned one, with a reason code. This is code enforcement; the skill text is a courtesy.
- **Provisional assumptions carry their interpretation.** Each records the objective, the charter passage interpreted, evidence, confidence, and the overturning observation (charter §7). A reflection `improvement-assumption-digest`, registered on the same `reflection_register.py` seam with `cadence="259200s"` (three days), sends new assumptions since the last digest to Tom's Telegram as a status report via the existing outbound path, grouped by priority area, and states in its own text that silence validates nothing (charter §11). It asks nothing: no poll, no `AskUserQuestion`, no reply-to expectation. A reply from Tom arrives as an ordinary correction and the detector records it as evidence superseding the assumption it contradicts. The dashboard renders the §11 record: current RSI goals and their charter relation, opportunity ranking, acquired abilities, evaluations, rejected approaches, unresolved assumptions, and resource use by budget unit. A dashboard does not transfer drift detection to Tom; the `serves_charter` judge and the amendment path are the controller's own drift checks.
- **Measured (lane 4).** The evaluation judge roster gains a blinded `serves_charter` judge that scores a candidate's output against the pinned charter text, calibrated against retained human corrections classified `architectural`. Its verdict is one input to the consensus envelope, never a gate on its own.
- **Never a candidate surface (lane 6).** The candidate manifest's eligible-changes list is validated against a denylist that includes `docs/improvement-charter.md` and `models/improvement_charter.py`; a manifest naming either is refused before construction. The recursive boundary states it already; this makes it a check.
- **Level 0 on the claim ladder.** Before "loop operational" can be claimed, the pinned digest equals the file's digest and every open case cites it. A Verification row asserts the file exists and the loader round-trips its digest.

#### Experiments, evaluation, release, recursion

- **Frozen contract** before candidate construction: hypothesis, mechanism, eligible changes, falsification criteria, primary outcome, minimum worthwhile effect, sample rationale, dev/validation/holdout assignment by task, project, and time grouping, trial count, stopping rule (finite batch or a named alpha-spending function), error treatment, cost ceiling, observation window, evaluator and rubric version, baseline identity. The contract digest is part of every verdict; an altered digest invalidates the verdict.
- **Candidate manifest**: code SHA, skill and prompt content hashes, model IDs and parameters, dependencies, memory snapshot digest, configuration, tool interfaces, environment fixtures, provider revision where available, and the paused-writer list.
- **Paired trials** with randomized ordering and isolated writable state. Candidate failures count as failures; evaluator infrastructure failures invalidate and retry the pair under a predeclared cap. Judges are blinded to arm identity, calibrated against retained human and outcome references, and monitored for disagreement. Holdout inputs, labels, and reference judgments live outside candidate-accessible storage. Trace content is untrusted data.
- **Promotion criteria**: practical gain past the per-endpoint margin, uncertainty evidence with repeated-selection correction (Holm at minimum), no prohibited regression. Small samples yield inconclusive. Rotate holdouts with an untouched final set. **Evaluation methods are revisable in service of the charter** (§6): a replacement evaluator, benchmark, or selection method ships as its own release, calibrated against retained anchors, with a written argument that it better assesses progress toward the charter; easier tests, narrower tasks, hidden failures, or changed definitions never establish a gain, and the `serves_charter` judge scores the argument too.
- **Release**: immutable bundle digest activated for newly assigned Jobs; active Jobs stay pinned. Rollout `staged → limited → expanded → accepted` or `reverted`, with stable Job-level exposure. Rollback repoints the default bundle. Human-owned `upvote` labels stay human-owned. **Merge authority (charter §6):** a qualified release that changes a core SDLC workflow is promoted by opening a PR through the ordinary pipeline with its evaluation verdict, contract digest, and comparison attached; it merges under the pipeline's normal review, tests, and repository gates with no additional approval from Tom. Infrastructure changes for RSI itself (settings, adapters, sandboxes, personas) need no comparison, only review and gates. Merge authority is distinct from deployment mechanics, which are unchanged, and from candidate isolation (Gap E), which holds regardless of who approves the merge.
- **Recursive boundary**: once the ordinary loop works, planner, context assembly, observation sampling, hypothesis generation, and experiment selection become candidate surfaces, compared on fresh matched opportunities with equal total budgets including evaluation and human time, with the worker model frozen. Evaluator upgrades calibrate separately. Authority and budgets remain externally governed and the charter is never a candidate surface (§12); evaluators and research selection are candidate surfaces under §6's anti-gaming rule.

#### Backup and restore

No backup exists for `data/`. Lane 3 adds `valor-improve export` and `import`, which dump the control namespace, journal, and artifact manifests to a dated archive under the retention root and restore them into an empty namespace, with a test that round-trips a seeded case. Fleet-wide execution requires shared durable storage and is a separate charter decision.

#### Autoexperiment retirement

Delete `scripts/autoexperiment.py`, `scripts/install_autoexperiment.sh`, `com.valor.autoexperiment.plist`, and `tests/unit/test_autoexperiment.py`; drop `autoexperiment` from the prefix-drift regex at `scripts/valor-service.sh:39-40`; delete `docs/features/autoexperiment.md` and its row at `docs/features/README.md:25`; correct `docs/research/claude-code-feature-swot.md:413` and the nonexistent `autoexperiment_last_run.json` citation at `docs/features/nightly-regression-tests.md:371`. Keep `data/experiments/{observer,summarizer}/eval_*.jsonl` and mark them legacy in a `data/experiments/README.md`. On each fleet machine, `/update` unloads a launchd job only when its label matches `<prefix>.autoexperiment` exactly.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Observer adapters: every `except Exception` records the failure on the adapter's coverage row (source, error, cursor position) and a test asserts the coverage row, not just a log line
- [ ] Control journal client: Redis unavailability raises a typed `JournalUnavailable`; a test asserts the controller tick returns `paused` and writes no projection
- [ ] Scheduler adapter: `ChildSessionsDisabledError` can never be raised on the research path (no parent is ever passed); a test asserts the adapter's call carries no `parent_agent_session_id`
- [ ] Evaluation runner: judge exceptions mark the trial `infra_failed`, never `candidate_failed`; a test asserts the pair is retried up to the cap and then invalidates the pair
- [ ] Poll answer persistence: registry TTL expiry after the answer was copied into the investigation leaves the investigation `answered`; a test asserts no re-ask

### Empty/Invalid Input Handling
- [ ] Transition script with `expected_revision=None`, empty `action_id`, or an unknown event returns a reason code and leaves the head unchanged
- [ ] Reservation admission with `requested_max=0` or negative is refused
- [ ] Evidence adapter receiving an empty page advances no cursor and records `partial=false, count=0`
- [ ] Planner receiving zero fresh evidence returns `defer` with rationale rather than fabricating a case
- [ ] Judge returning empty output yields `infra_failed` for that trial

### Error State Rendering
- [ ] Dashboard renders a case in `paused_budget`, `inconclusive`, and `reconciliation_required` with the reason text; test covers each partial
- [ ] Dashboard renders "coverage unknown" when an adapter has no heartbeat, never a green tile
- [ ] No dashboard surface reads a rework figure that has no writer. `TaskTypeProfile` is deleted whole, so the historical-zeros hazard is removed at the source rather than guarded against; the rework anti-criterion in Verification is the detector, and the `ui/`-only variant of that grep was dropped because its roots are already inside it and it could never fail independently

## Test Impact

- [ ] `tests/unit/test_autoexperiment.py` — DELETE: tests the removed script; every test imports `scripts.autoexperiment`
- [ ] `tests/unit/test_valor_service_bootstrap.py` — UPDATE if it enumerates the prefix-drift service list; assert `autoexperiment` is absent
- [ ] `tests/unit/test_task_type_profile.py` — DELETE: `models/task_type_profile.py` is removed whole (Open Question 1, disposition (a)), and all 54 references in this file import from it
- [ ] `tests/unit/test_session_tags.py` — REPLACE: `tools/session_tags.py:181` loses its `rework_triggered` read, so the file is rewritten against the reduced tag set
- [ ] `tests/unit/test_session_lifecycle_consolidation.py`, `tests/unit/test_recovery_ownership.py`, `tests/unit/test_session_health_orphan_process_reap.py`, `tests/unit/test_agent_session_scheduler_kill.py`, `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`, `tests/integration/test_orphan_reap_forward_scan.py` — **no change from this build.** `NON_TERMINAL_STATUSES` gaining `admitted` is lane 3's edit (see Architectural Impact); these six move to the lane-3 child issue's Test Impact
- [ ] `tests/unit/test_session_lifecycle.py`, `tests/integration/test_session_finalize.py` — UPDATE: the `# 5.5. Update TaskTypeProfile` block at `models/session_lifecycle.py:577-587` is deleted, so every case asserting a profile write after `finalize_session` goes with it (23 and 29 references respectively). Both call sites swallow at DEBUG, so these two files are the only detector of a half-done deletion
- [ ] `tests/unit/test_agent_session_queue.py`, `tests/unit/test_agent_session_queue_async.py` — no change from this plan. The queue seam is #3183's; its tests belong to that issue
- [ ] `tests/unit/test_agentsession_index_guard_generalized.py` — UPDATE: extend the runtime `IndexedField` enumeration to the eight new models
- [ ] `tests/unit/test_poll_registry.py` — no change. The poll descriptor is untouched; this plan sends no questions
- [ ] `tests/unit/test_ui_sdlc_data.py` — UPDATE: the `is_active` property at `ui/data/sdlc.py:468` now reads the `ACTIVE_STATUSES` constant at `:1256` instead of its own inline tuple; assert the two agree
- [ ] `tests/unit/test_improvement_charter.py` — CREATE: `load_from_file` is idempotent per digest, a changed byte yields a second immutable row, a file without `owner: Tom Counsell` is refused
- [ ] `tests/unit/test_settings.py` — UPDATE: `ImprovementSettings` defaults (`max_concurrent_research_sessions=1`, `daily_paid_inference_usd=10.00`, `weekly_infrastructure_usd=50.00`, `budget_day_boundary="UTC"`, `budget_week_start="monday"`, `daily_question_ceiling` and `portfolio_allocation` absent) and `IMPROVEMENT__*` overrides
- [ ] `tests/unit/test_improvement_eligibility.py` — CREATE: public repository is eligible; private, missing `github` field, and `gh` failure all return `False`
- [ ] `tests/unit/test_improvement_resources.py` — CREATE: the probe reports `unknown` when `op` cannot authenticate, never raises, and its output contains no credential bytes (assert against a seeded fake)
- [ ] `tests/unit/test_ui_app.py`, `tests/unit/test_ui_reflections_data.py` — UPDATE: new partial routes render with an empty namespace and with a seeded case
- [ ] `tests/unit/test_length_safe_content_store.py` — UPDATE: add the verifying subclass's archive-path digest check
- [ ] `tests/unit/test_reflection_register.py` — UPDATE: controller tick registration is idempotent and pinned to the `valor` owner

## Rabbit Holes

- Repairing autoexperiment's target instead of deleting it. Repair exposes the unsafe mutation path; delete.
- Building a graph database for the system model. Relational JSON claims with evidence references are enough until a query proves otherwise.
- Adding a correlation ID to steering. Steering is correlation-free by design (the writer never looks a session up); the poll registry is the correlation table.
- A second content-addressed store. `ContentField(store=...)` with a verifying subclass is the whole change.
- Expected-value scoring with fabricated precision. Ordinal ranking until calibration exists.
- Proving point-in-time consistency of a memory export. Declare the corpus as the exported dataset with its interval; build a write barrier only when an experiment needs one.
- Making `tool_budget.py` the enforcement mechanism. It cannot see the current turn's spend.
- Lifting the child session gate. The research path never passes a parent.
- Enumerating meeting and voice vendors before the loop runs. Frontier review is a planner action gated by the charter, not a phase-0 deliverable.
- Building the cloud sandbox before lane 2's substrate exists. Lane 7 is a child issue with its own spikes; lanes 1 and 2 give it records, budgets, and a resource probe to build on.
- Treating a backup ISP or bill payment as an early opportunity. Charter §3 says feasibility is not priority.

## Risks

### Risk 1: Weak causal attribution
**Impact:** the controller promotes changes that did nothing, and the dashboard reports improvement that is noise.
**Mitigation:** paired trials with randomized order, per-endpoint margins, Holm correction, inconclusive as the default for underpowered pairs, holdout rotation with an untouched final set, production observation windows stratified by task type.

### Risk 2: Missing outcome evidence read as success
**Impact:** quiet failures vanish and the agenda biases toward easily reported bugs.
**Mitigation:** explicit `partial` and `unknown` markers, coverage heartbeats per adapter, random sampling of apparently successful tasks, and a promotion freeze whenever observation or evaluation stops.

### Risk 3: Shared-environment contamination between arms
**Impact:** the candidate reads production memory or trial writes leak across arms, confounding the result.
**Mitigation:** per-arm Redis processes on private ports with `VALOR_PROJECT_KEY` set, the writer kill switch, the paused decay reflection recorded in the manifest, startup refusal on any out-of-namespace resolution, baseline retrieval parity check before scoring.

### Risk 4: Stale executor writes after takeover
**Impact:** two controller ticks dispatch one case, or a replaced worker's result overwrites the new owner's state.
**Mitigation:** monotonic epoch per lease, Lua transition with expected revision, fence re-check inside every effect-recording script call, action-ID tagged results with stale-epoch rejection.

### Risk 5: Unbounded spend, unknown spend, or a stranded lane slot
**Impact:** a runaway experiment consumes the external-LLM budget needed to evaluate or revert it; or, at a concurrency bound of 1, a single unreleased lane slot wedges all research indefinitely.
**Mitigation:** atomic admission accounting for both units; separate controller and evaluator dollar reservations out of the same daily pool; per-call settlement from OpenRouter usage; the lane-slot release wired inside `finalize_session` (`models/session_lifecycle.py:233`) so every termination path frees it; the `improvement-intent-reconcile` reflection as the backstop for a slot whose session row vanished; unknown external spend pauses external evaluation.

### Risk 6: The system substitutes its own guesses for Tom's judgment
**Impact:** with a question ceiling of zero, an uncertainty that genuinely needed Tom's answer becomes a confident wrong assumption, and the system optimizes toward the wrong objective while reporting progress.
**Mitigation:** the written charter (`docs/improvement-charter.md`, Gap G) is the first input to every planning brief and states the rule for exactly this case (§9: evidence and investigation first, then a guarded provisional assumption, then deferral plus an amendment request when authority is missing); unresolved factual claims are recorded as explicit **provisional assumptions** with evidence, confidence, consequences, the charter passage interpreted, and the overturning observation, and an assumption can never redefine an outcome, erase a requirement, grant authority, or raise a budget; new assumptions reach Tom every three days as a Telegram status report that states silence validates nothing, and stay on the dashboard where he can correct one on his own initiative; the north star, authority, and budgets are externally governed and never inferred; a correction Tom volunteers is captured as ordinary evidence and supersedes the assumption it contradicts. Observed intervention burden is published beside rescue incidence so the trade is visible.

### Risk 7: A free promotion silently becomes paid usage, or unknown metering reads as zero
**Impact:** infrastructure spend exceeds $50/week without any admission ever refusing, because a credit expired or a provider's usage was never read.
**Mitigation:** Gap D unit 3 reserves a recurring charge for the rest of the window at admission, records every credit with its expiry and the paid rate behind it, converts an expiring credit to a forecast charge on its expiry day, treats a missing meter as a pause rather than zero, and `valor-improve budget` lists every `metering="unknown"` receipt.

### Risk 8: Client context reaches a non-subscription provider during an open-source experiment
**Impact:** charter §7 is breached: private client work or its context is routed to a provider Tom has not authorized for it.
**Mitigation:** provider routing is gated by `tools/improvement_eligibility.py::is_open_source(project_key)`, which derives eligibility from the project's GitHub visibility through the `github` field in `projects.json`, caches with a TTL, and fails closed to "client" on any error or unknown; a routed session asserts at startup that its `VALOR_PROJECT_KEY` resolves eligible and refuses otherwise; a test feeds a private repository and asserts refusal.

### Risk 9: Registration clobbered by `/update`
**Impact:** the controller silently stops ticking after the next fleet update.
**Mitigation:** registration through `reflection_register.py` in `run.py`, never a hand edit of `config/reflections.yaml`; a test asserts the entry survives a simulated sync.

## Race Conditions

### Race 1: Two ticks admit the same case
**Location:** control namespace `head` and `intent:*`; `agent/reflection_scheduler.py` skip-if-running at `:541`
**Trigger:** the scheduler's `is_reflection_running` reads a status field, not a lease, so a slow tick and its successor overlap.
**Data prerequisite:** the case head's `revision` and `epoch`.
**State prerequisite:** the second tick must observe the first tick's transition.
**Mitigation:** the Lua transition compares `expected_revision`; the loser gets a reason code and no effect. Admission and reservation live in the same script call.

### Race 2: Crash between intent admission and session creation
**Location:** the create-or-bind seam, owned by #3183; the call site is this plan's scheduler adapter.
**Trigger:** the process dies after the intent is admitted and before the seam returns a bound `agent_session_id`.
**Data prerequisite:** the intent holds the idempotency key; the seam's binding survives the crash.
**State prerequisite:** intent is `admitted`, the row may or may not exist.
**Mitigation:** reconciliation retries the seam with the **same idempotency key**, which by #3183's contract yields the one row rather than a second; request-digest equality is checked on retry; the `improvement-intent-reconcile` reflection sweeps an intent that stays `admitted` past the staleness threshold, transitions it to `reconciliation_required`, and releases its lane slot. This plan does not implement the seam's crash safety; it depends on it, and lane 3 blocks until #3183 provides it.

### Race 3: A lane slot outlives the session that held it
**Location:** `models/session_lifecycle.py::finalize_session` (`:233`) and the reservation record in the control namespace.
**Trigger:** a watchdog, the health sweep, or the scheduler kills a research session on a path that does not run the ordinary completion code.
**Data prerequisite:** the session carries reservation and action-ID provenance.
**State prerequisite:** the reservation is outstanding and `max_concurrent_research_sessions` is 1, so nothing else can be admitted.
**Mitigation:** the release is a post-transition side effect **inside** `finalize_session`, the common floor for every one of those callers, so it fires uniformly. If the row is gone entirely, the reconciliation reflection releases the slot from the intent side. A slot is never released by lease expiry alone, because an expired lease does not prove the work stopped.

### Race 4: Result submitted under a replaced fencing generation
**Location:** result submission script; `agent/pid_fence.py`
**Trigger:** executor A stalls past lease expiry, B takes over, A finishes.
**Data prerequisite:** A's result carries `(action_id, epoch_A)`.
**State prerequisite:** head epoch is `epoch_B > epoch_A`.
**Mitigation:** the script accepts `epoch >= highest_accepted` for transitions and records lower epochs as evidence artifacts only.

### Race 5: Fleet update changes a running arm
**Location:** worktree venv pin, global skill hardlinks
**Trigger:** `/update` lands while a paired trial runs.
**Data prerequisite:** the candidate manifest pins interpreter, skill content hashes, and configuration.
**State prerequisite:** trials run in worktrees with their own venvs and a copied `settings.local.json`.
**Mitigation:** the manifest is re-hashed at trial end; a mismatch invalidates the pair rather than scoring it.

### Race 6: Decay reflection fires during a trial
**Location:** `reflections/memory/memory_decay_prune.py` via the reflection worker
**Trigger:** the reflection's cadence lands inside a trial window.
**Data prerequisite:** the arm's corpus digest recorded at trial start.
**State prerequisite:** the reflection is paused for the window and the pause is in the manifest.
**Mitigation:** the arm re-hashes its corpus at trial end; a changed digest invalidates the pair; the reflection process targets production Redis, so the arm's private instance is untouched even if the pause fails, and the digest check is the proof.

## No-Gos (Out of Scope)

- [ORDERED] Promotion by any path other than the SDLC pipeline. Charter §6 grants merge authority for evidence-backed core-workflow changes through the ordinary pipeline; nothing merges by another route, and candidate execution never holds evaluator secrets or production credentials (Gap E).
- [ORDERED] RSI-initiated outreach unrelated to assigned client responsibilities, cold sales, or any revenue activity (charter §8, §10). Client calls and messages within assigned responsibilities are Valor's discernment under §10 and a capability target of the loop, never an action the loop itself takes.
- [SEPARATE-LANE 7] Cloud execution and shared durable storage. In scope under charter §2 (mostly cloud-sandbox, round-the-clock RSI within a month) and the $50/week infrastructure unit, delivered by lane 7 as a child issue filed by task 6. Lanes 1 and 2 keep the local retention root plus export/import so lane 7 has something to migrate.
- [EXTERNAL] Any hand edit of `~/Desktop/Valor/reflections.yaml`. Registration goes through `reflection_register.py`; if an agent-type entry is ever needed, that is a vault edit only Tom performs.
- [EXTERNAL] Provisioning the resources charter §8 names (Workspace identities, virtual debit card, Cloudflare account and CLI token, service-account write access to `m-valor`). Tom reports they are or should be in the vault; `tools/improvement_resources.py::probe` (task 3) verifies each by item title and fingerprint, never value, and the capability matrix records verified or unknown. `tools/vault_write.py` fails closed when write access is absent. No controller module ever writes `.env`, and no improvement-path module other than `tools/vault_write.py` invokes `op`. A Verification row asserts both.
- [ORDERED] Autonomously amending `docs/improvement-charter.md`, Valor's identity and persona files, and public profiles. Valor may propose a charter amendment through Gap F's path; only Tom authorizes (§12). The charter is on the candidate-surface denylist (Gap G).
- [SEPARATE-SLUG #3183] The idempotent create-or-bind seam on `_push_agent_session`. Owned by #3183 for the production path; #3177 lane 3 consumes it and changes no queue signature. If #3183 has not landed when lane 3 starts, lane 3 blocks.
- [SEPARATE-SLUG #3183] The renewed execution lease with a fencing generation. Owned by **#3183's lane 6, which is a child issue of #3183 rather than part of its current build** (per #3183's §Relationship table); the control journal imports it rather than writing a second lease, and lane 3 blocks on that grandchild for the lease specifically.
- [SEPARATE-SLUG #3183] The dead-letter record. Owned by #3183 as one generalized model with a `stage` field; exhausted improvement intents write to it with `stage="improvement-intent"`. This plan defines no second sink.
- [DESTRUCTIVE] Deleting `data/experiments/*/eval_*.jsonl`. They are retained as marked-legacy evidence; the "Legacy corpora retained" Verification row asserts all three paths still exist and fails closed if any is missing.
- [DESTRUCTIVE] Lifting or bypassing the child session gate. The research path never passes a parent and never sets `VALOR_ALLOW_CHILD_SESSIONS`. The "no child-gate bypass" Verification row greps trees that exist today (`agent/`, `tools/`, `models/`, `reflections/`) with the one sanctioned definition site excluded, so it bites during lanes 1 and 2 rather than passing on a missing directory.

## Update System

- `scripts/update/run.py` gains a `register_improvement_collect` step calling `reflection_register.py::register_improvement_collect`, idempotent like `register_crash_recovery` (`scripts/update/run.py:1164`). **Task 3 owns this edit**, including the `reflections/improvement_collect.py` callable, the wrapper and its two module constants in `reflection_register.py`, the `RegisterResult` field on the run.py result dataclass, and the `tests/unit/test_reflection_register.py` cases; see task 3 for the exact `cadence`/`cron` guard and the vault-file target. Machine pinning is inherited from `register_reflection`'s existing `_this_machine_owns_valor` guard, not re-implemented. Lane 3 adds a second registration on the same seam for `improvement-intent-reconcile` (Gap A's recovery pass).
- `/update` unloads a launchd job only when its label matches `<prefix>.autoexperiment` exactly, on machines where one is installed; a mismatch is logged, never acted on.
- No new dependencies. `ImprovementSettings` defaults to disabled, so no `.env` key is required at runtime. The `IMPROVEMENT__ENABLED` override **is** declared in `.env.example` with `# @optional`, and **task 2 owns that edit** together with the `Field(description=...)` sentence in `config/settings.py` that clears leg 1 of `tests/unit/test_env_declaration_readers.py`. `@optional` covers the vault-presence check only; the reader test is a separate, non-negotiable obligation.
- Popoto schema, two dispositions, both decided:
  - **Additive (the eight new models):** a no-op migration entry in `scripts/update/migrations.py` records their registration so `run_pending_migrations()` has a durable marker for the schema version. Registered in the `MIGRATIONS` dict at `:1241`; task 2 owns it.
  - **Subtractive:** `TaskTypeProfile` is deleted whole, so its keyspace gets a real strip migration — `"retire_task_type_profile"`, registered in the same dict, owned by task 3, detailed there. `AgentSession.rework_triggered` (`models/agent_session.py:221`) gets **no** migration and needs none: it is a plain `Field(null=True)`, not an `IndexedField`/`SortedField`/`KeyField`, so no index set is orphaned, and this repo's contract for exactly this case is already written at `models/agent_session.py:879-883` — there is deliberately "no pop-list for fields deleted by past schema changes (#2873, #1927, #1924)" because Popoto's `Model.__init__` does `self.__dict__.update(kwargs)`, so an unknown key "lands harmlessly in the instance dict and never raises; encoding iterates `_meta.fields` only, so it is never persisted and disappears on the next save." That is the decision, recorded rather than left silent.
- Fleet execution stays single-machine (the `valor` owner) until shared storage exists.

## Agent Integration

- New CLI entry point `valor-improve = "tools.improvement:main"` in `pyproject.toml [project.scripts]` with subcommands `case show`, `case explain`, `propose`, `propose-amendment`, `budget`, `resources`, `release compare`, `pause`, `resume`, `doctor`, `export`, `import`, `replay-projection`. Research sessions reach research state only through this CLI, which enforces journal authorization and never exposes a raw transition. `pause`, `resume`, and `doctor` are the break-glass path for Gap A's namespace-unreachable state.
- The research skill (`.claude/skills/improve-research/SKILL.md`, project-only) instructs the planning session to read a bounded brief, use `WebSearch` and `WebFetch` for external research, write hypotheses and proposed actions through `valor-improve propose`, never enqueue sessions, and send a human exactly one kind of message: a charter amendment request through `valor-improve propose-amendment` (Gap F).
- **The bridge imports nothing new and the poll registry is untouched.** No `investigation_id` on the poll descriptor, no change to `tools/ask_poll.py`, no change to `bridge/poll_registry.py` or `bridge/answer_routing.py`. The amendment request and the three-day digest go out as plain messages through the existing outbound path; the answer to an amendment is the charter file's next digest, so nothing needs binding to a message id.
- Integration tests: a research session in a test worktree runs `valor-improve propose` end to end and the journal shows the event; a session attempting `valor-session create --parent` on the research path receives the existing gate error; `valor-improve doctor` on a seeded paused case prints the paused head and its outstanding reservation.

## Documentation

- [ ] Link `docs/improvement-charter.md` from `docs/README.md` and from the top of `docs/features/improvement-controller.md` as the north star; the feature doc describes the digest pinning, the `propose` refusal, the assumption digest, and the candidate-surface denylist (Gap G)
- [ ] Create `docs/features/improvement-controller.md`: three layers, authorities, records, control namespace contract, dispatch adapter, the two reservation units, memory isolation, claim levels, the #3183 dependency, and a **Break-glass** section giving the manual procedure for a paused or wedged case (`valor-improve doctor`, `pause`, `resume`, how to distinguish a namespace outage from a wedged case, what evidence to keep)
- [ ] Create `docs/features/improvement-evaluation.md`: contract fields, manifest, judge envelope, statistics, holdout policy
- [ ] Add both rows to `docs/features/README.md`; delete the Autoexperiment row at line 25
- [ ] Delete `docs/features/autoexperiment.md`; correct `docs/research/claude-code-feature-swot.md:413` and `docs/features/nightly-regression-tests.md:371`
- [ ] Update `docs/features/adding-reflection-tasks.md` to name the vault source and `reflection_register.py` as the tracked registration path
- [ ] Update `docs/features/web-ui.md` to remove the stale `ui/routers/` description and document the inline-route plus `ui/data/` pattern
- [ ] Update `docs/features/redis-models.md` with the control-namespace exception and the schema-gate docstring requirement for the eight models
- [ ] Delete `docs/features/trm-task-type-profile.md` and its README row at `docs/features/README.md:263` (the model it documents is removed whole)
- [ ] `docs/features/session-recovery-mechanisms.md` is **not** touched by this build. The `admitted` status and its `RECOVERY_OWNERSHIP` entry belong to the lane-3 child issue, which carries this doc task
- [ ] Create `data/experiments/README.md` marking the retained corpora as legacy evidence
- [ ] Update `docs/tools-reference.md` with `valor-improve`

## Success Criteria

### This build (lanes 1 and 2)

Every row here is satisfiable by the tasks in Step by Step Tasks. Nothing here depends on a child issue.

- [ ] Lane 1: autoexperiment entry point, installer, plist, tests, and feature doc are gone; `valor-service.sh` no longer enumerates it; retained corpora still exist and are marked legacy
- [ ] Lane 2: the eight flat models exist, are exported, pass the index guard, and carry schema-gate docstrings; `ImprovementSettings` exists with `max_concurrent_research_sessions=1` and `daily_paid_inference_usd=10.00`, and `IMPROVEMENT__ENABLED` clears `tests/unit/test_env_declaration_readers.py`; `models/task_type_profile.py` is gone whole along with the `finalize_session` block at `models/session_lifecycle.py:577-587`, its keyspace is retired by the registered `retire_task_type_profile` migration, and rework is derived from `ImprovementEvidence`; the verifying store subclass rejects a corrupted archive; a capability matrix marks each planned component implemented, deployed, measured, or unknown; `docs/improvement-charter.md` (v2) is seeded into `ImprovementCharter` by digest and the goals partial renders the pinned version, its §3 priorities, and the §11 record headings (Gap G, level 0); `tools/improvement_eligibility.py` fails closed on a private or unknown repository; `tools/improvement_resources.py::probe` reports each §8 resource as verified or unknown
- [ ] Lane 2, user-visible value: the correction detector runs against real sessions **because something ticks it**. The `improvement-evidence-collect` reflection is registered through `reflection_register.register_improvement_collect`, called from `scripts/update/run.py`, and `reflections/improvement_collect.py::run_improvement_collect` runs the observer adapters. At merge, `ImprovementEvidence.query.filter(project_key="valor").count() >= 0` holds and the trimmed dashboard partial renders (a render smoke test), which is the honest gate on merge day because no correction may have occurred yet. It graduates to `count() > 0` after one week of normal use, checked by hand and recorded on the tracking issue
- [ ] `ui/data/sdlc.py` carries exactly one active-status definition: the `is_active` property at `:468` reads the `ACTIVE_STATUSES` constant at `:1256` instead of its own inline tuple, so no future status can be live on one path and inactive on the other. This build adds no status to it; `admitted` arrives with lane 3
- [ ] The dashboard never presents experiment count or merged-patch count as improvement, and no template references `rework_rate` (which no longer exists)
- [ ] Five child issues exist for lanes 3 through 7, each referencing #3177, the lane-3 issue records its dependency on #3183, and the lane-7 issue carries the charter §2 first-month expectation
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

### Carried to child issues (lanes 3 through 6)

These are the exit conditions of the child issues filed by task 6, restated here so the delivery order stays committed. They are **not** this build's definition of done.

- Lane 3: the `valor-improve` CLI exists, `tools/improvement.py` is referenced from `pyproject.toml`, and the research skill references `valor-improve propose`; fault-injection tests pass for stale-generation rejection, crash between admission and creation, an unreleased lane slot on restart, and journal unavailability with a break-glass recovery; #3183's seam and lease are consumed without a competing change to `agent/agent_session_queue.py`
- Lane 4: two arms on private Redis processes produce byte-identical corpus reads; baseline retrieval parity holds on the frozen corpus; a corrupted artifact invalidates evaluation rather than scoring
- Lane 5: the top-ranked opportunity by charter §3 (expected, not mandated: cheap inference integrated into eligible open-source sessions behind the eligibility guard) is tested under a frozen contract with paired blinded evaluation and an accepted-or-rejected verdict with complete lineage; the verdict changes the next selection; one `skill_acquisition` cycle per charter §5 runs end to end from an observed gap through vetting, library integration, comparative evaluation, and a reuse observation; the inspiration-intake, web-research, and amendment-request kinds are each exercised
- Lane 6: release records, exposure assignment, rollback, observation windows, and incident drills exist, and the recursive comparison runs on budget-matched fresh opportunities
- Lane 7: at least one RSI session runs unattended in a cloud sandbox with its evidence, budget settlement, and recovery recorded; the first-month progress report on #3177 states which sessions run where, what sustains them, what they cost against the $50/week unit, and what still prevents mostly-cloud operation (charter §2)
- Gap G across lanes: lane 3 refuses an unranked case at `propose` and delivers an amendment request; lane 4's `serves_charter` judge is calibrated and blinded; lane 5's brief opens with the charter and the three-day assumption digest reaches Telegram; lane 6's manifest denylist refuses the charter as a candidate surface and a core-workflow release merges through the pipeline with no extra approval

## Team Orchestration

### Team Members

- **Builder (retirement)**
  - Name: retire-builder
  - Role: lane 1, autoexperiment removal and doc corrections
  - Agent Type: builder
  - Resume: true

- **Builder (records and settings)**
  - Name: records-builder
  - Role: lane 2, eight flat models, `ImprovementSettings`, index-guard extension, migration marker
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (evidence and reuse seams)**
  - Name: seams-builder
  - Role: lane 2, correction detector persistence, deleting the `rework_rate` aggregate and its readers, the memory-inspiration observer adapter, verifying store subclass, capability matrix
  - Agent Type: builder
  - Resume: true

- **Builder (dashboard)**
  - Name: ui-builder
  - Role: lane 2, `ui/data/improvement.py`, templates, partial routes, `ACTIVE_STATUSES` update
  - Agent Type: builder
  - Resume: true

- **Validator (lanes 1 and 2)**
  - Name: lane-validator
  - Role: run Verification rows, mutation-check each new guard, confirm no `models/improvement/` package and no YAML config
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: improvement-docs
  - Role: Documentation section tasks
  - Agent Type: documentarian
  - Resume: true

Lanes 3 through 6 are staffed by their child plans.

## Step by Step Tasks

Lanes 1 and 2 are this plan's first `/do-build`. Lanes 3 through 6 are child issues filed by lane 2's final task, each referencing #3177, and are listed here so the delivery order is committed.

### 1. Retire autoexperiment
- **Task ID**: build-retire
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_service_bootstrap.py`, Verification rows 1 through 5
- **Informed By**: recon (all four defect claims confirmed; never installed here)
- **Assigned To**: retire-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `scripts/autoexperiment.py`, `scripts/install_autoexperiment.sh`, `com.valor.autoexperiment.plist`, `tests/unit/test_autoexperiment.py`, `docs/features/autoexperiment.md`
- Remove `autoexperiment` from both alternations at `scripts/valor-service.sh:39-40`; remove the README row; correct the two stale citations
- Add `data/experiments/README.md` marking the corpora legacy
- Add the exact-label launchd unload step to `/update`

### 2. Records and settings
- **Task ID**: build-records
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_models.py` (create), `tests/unit/test_agentsession_index_guard_generalized.py`, `tests/unit/test_settings.py`, `tests/unit/test_env_declaration_readers.py`
- **Informed By**: recon (flat layout, schema-gate docstring, `project_key` partition, bounded recency sort); spike-5 (per-field store)
- **Assigned To**: records-builder
- **Agent Type**: builder
- **Parallel**: true
- Domain: Redis/Popoto data
- Create the eight flat modules with `AutoKeyField` id, `KeyField project_key`, `SortedField created_at partition_by="project_key"`, low-cardinality `IndexedField` state fields only, `Meta.ttl` decisions per record (evidence and investigations follow `ReflectionRun`; case, evaluation, and release are immortal like `Job`)
- Export from `models/__init__.py` after the rebuild interlock
- Add `ImprovementSettings` to `config/settings.py` with `enabled=False`, `max_concurrent_research_sessions=1` (an operating choice, charter §8), `daily_paid_inference_usd=10.00`, `weekly_infrastructure_usd=50.00`, `budget_day_boundary="UTC"`, `budget_week_start="monday"`, `controller_tick_seconds`. There is no `daily_question_ceiling` field and no `portfolio_allocation` field: routine questions do not exist, and ranking is evidence-based rather than allocated
- **This task also owns the `.env.example` declaration for `IMPROVEMENT__ENABLED`** (it was previously ownerless). Two obligations, not one:
  - `# @optional` in the declaration's comment block exempts the key from the **vault-presence** check only (`docs/features/env-completeness-validation.md`). It does **not** exempt it from `tests/unit/test_env_declaration_readers.py`, which asserts every declaration clears leg 1 (a literal occurrence in a tracked non-markdown file, `git grep -F` with `:!*.md`, `:!.env.example`, and the test file itself excluded) or leg 2 (a `@passthrough <binary>` sigil). `@passthrough` is wrong here — nothing external reads this key.
  - Clear leg 1 the way every existing nested key does: write the literal string `IMPROVEMENT__ENABLED` into the pydantic `Field(description=...)` for `ImprovementSettings.enabled` in `config/settings.py`. That is exactly how `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS` clears it — `git grep -l -F` for that key returns `config/settings.py` alone, matching the description string at `config/settings.py:708` ("... Env: FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS."), because the nested-delimiter form never appears as an identifier (the field is `improvement: ImprovementSettings`). Follow the same `Env: <KEY>.` sentence shape. Then add the declaration to `.env.example` with a `# @optional` line, matching the `FEATURES__MAX_CRITIQUE_CYCLES` block at `.env.example:334-339`
- **Seed the charter (Gap G).** `models/improvement_charter.py` gains `load_from_file(path=Path("docs/improvement-charter.md"))`: read the file, parse `version` and `effective` from frontmatter, compute the `"sha256:<hex>"` digest over the file bytes with the `tools/sdlc_verdict.py` normalizer, and `create` one immutable row per unseen digest with the full text in a `ContentField`. Rows are never updated. `ImprovementCase`, `ImprovementInvestigation`, and `ImprovementRelease` carry `charter_digest`; `ImprovementCase.priority_area` is an `IndexedField` over the Gap G vocabulary and `ranking_rationale` is free text. Create `tests/unit/test_improvement_charter.py`: loading twice creates one row; a changed byte creates a second row with a different digest and leaves the first untouched; a file missing `owner: Tom Counsell` is refused
- Extend `tests/unit/test_settings.py` for the `ImprovementSettings` defaults and the `IMPROVEMENT__*` nested overrides
- Add the no-op migration marker to `scripts/update/migrations.py`
- Extend the index guard test to enumerate the new models

### 3. Evidence and reuse seams
- **Task ID**: build-seams
- **Depends On**: build-records
- **Validates**: `tests/unit/test_length_safe_content_store.py`, `tests/unit/test_session_tags.py`, `tests/unit/test_session_lifecycle.py`, `tests/integration/test_session_finalize.py`, `tests/unit/test_reflection_register.py`, `tests/unit/test_improvement_evidence.py` (create). `tests/unit/test_task_type_profile.py` is DELETED by this task, not validated by it
- **Informed By**: recon (correction detector transient; `_shipped_evidence` discarded; `rework_triggered` unwritten); spike-5 (archive path unverified); critique blocker 2 (the old anti-criterion matched a dead field)
- **Assigned To**: seams-builder
- **Agent Type**: builder
- **Parallel**: false
- Promote `CORRECTION_PATTERNS` (`reflections/utilities.py:37-46`) into a persisted detector. **The durable row is `ImprovementEvidence`, one per detected correction**, carrying `project_key`, the source `session_id` for dedup, the matched text, and `classification` as a low-cardinality `IndexedField` (`architectural`, `preference`, `scope`, `clarification`, `unknown`). That is the row the lane-2 criterion counts and the dashboard partial reads. A `SessionEvent` append is **optional in-session provenance only**: `SessionEvent` (`models/session_event.py:30`) is a pydantic `BaseModel` "Stored as a dict in `AgentSession.session_events` `ListField`" (`models/agent_session.py:224`), its field is `event_type` (`models/session_event.py:37-39`, arbitrary strings accepted for backward compat) and **not** `kind`, and embedded dicts inside a `ListField` are not queryable, so nothing may gate on them
- Persist `expectation_reconciler`'s shipped-work evidence and owner liveness as `ImprovementEvidence` rows with cursor and coverage
- **Retire `TaskTypeProfile` whole (Open Question 1 is decided, disposition (a)).** Every line number below was re-derived by symbol against the working tree on 2026-09-07, not carried forward; the round-2 numbers (`:62`, `:94`, `:196`) were docstring and inner-loop lines and are withdrawn.
  - Delete `models/task_type_profile.py` entirely. Deleting only the three fields is not available: `delegation_recommendation` is an `IndexedField` (`:77`) whose sole reader is `get_delegation_recommendation` (`:217`), so removing the reader while keeping the model strands a live indexed field, and `_derive_recommendation` (`:84`) exists only to feed it. What remains after the four symbols go is `session_count` and `avg_turns`, which have no reader anywhere. The whole-module delete is the smaller change, not the larger one.
  - **The live writer, named.** `update_task_type_profile` (`models/task_type_profile.py:133`) assigns `profile.rework_rate` at `:203` and `profile.delegation_recommendation = _derive_recommendation(new_rate, new_count)` at `:204`, reading `rework_triggered` off the session at `:196`. It is called from `finalize_session` on every completed session at `models/session_lifecycle.py:585`, inside the `if not skip_auto_tag and status == "completed"` block that opens at `:579` under the `# 5.5. Update TaskTypeProfile` comment at `:577`. Delete that whole block, `:577-587` inclusive.
  - **Both call sites swallow at DEBUG**, so a half-done deletion degrades silently and never raises in production: `except Exception as e: logger.debug(...)` at `models/task_type_profile.py:213` and at `models/session_lifecycle.py:586-587`. pytest is therefore the only detector — `tests/unit/test_session_lifecycle.py` (23 references) and `tests/integration/test_session_finalize.py` (29 references) both exercise the path, and both are on the Test Impact list.
  - Reword the stale comment at `models/agent_session.py:83` ("TRM task type vocabulary — used for TaskTypeProfile keying and delegation decisions") to name only the surviving consumer, `tools/session_tags.py`. The `task_type` field itself stays; only its `TaskTypeProfile` justification goes.
  - Remove the `rework_triggered` branch from `tools/session_tags.py` (`:156` docstring, `:181` read, `:189` priority comment); remove the writerless `rework_triggered = Field(null=True)` at `models/agent_session.py:221`, which is the reason the old Verification row passed vacuously.
  - Delete `docs/features/trm-task-type-profile.md` and its README row at `docs/features/README.md:263`.
  - **Blast radius is closed.** `/usr/bin/grep -rn "TaskTypeProfile\|get_delegation_recommendation\|task_type_profile\|delegation_recommendation"` over `models/ agent/ tools/ ui/ reflections/ bridge/ worker/ scripts/ tests/ config/ docs/features/` (measured 2026-09-07, `__pycache__` excluded) returns matches in exactly seven files: `models/task_type_profile.py` (33), `models/session_lifecycle.py` (4), `models/agent_session.py` (1, the comment above), `tests/unit/test_task_type_profile.py` (54), `tests/integration/test_session_finalize.py` (29), `tests/unit/test_session_lifecycle.py` (23), `docs/features/trm-task-type-profile.md` (15). `get_delegation_recommendation` has zero production callers — only tests. There is no eighth consumer to find.
  - Rework is derived instead from `ImprovementEvidence` rows carrying `classification="architectural"`, which have a real writer (the correction detector below)
- **Register the collection tick so the detector actually runs (this task owns the edit).** Without this, no observer adapter ever fires and the lane-2 user-visible criterion is unreachable by construction. Three edits, following the `crash-recovery` precedent end to end:
  1. Create `reflections/improvement_collect.py` exporting `run_improvement_collect()`, the tick that runs this task's three observer adapters (correction detector, memory-inspiration adapter, `expectation_reconciler` persistence) and returns the standard reflection result dict.
  2. In `scripts/update/reflection_register.py`, add module constants `IMPROVEMENT_COLLECT_NAME = "improvement-evidence-collect"` and `IMPROVEMENT_COLLECT_CALLABLE = "reflections.improvement_collect.run_improvement_collect"` beside `CRASH_RECOVERY_NAME` / `CRASH_RECOVERY_CALLABLE` (`:74-75`), plus a thin `register_improvement_collect(project_dir)` wrapper modeled on `register_crash_recovery` (`:559-573`). It calls `register_reflection` (`:475`) with **`cadence="900s"` and no `cron`** — the guard at `:501` is `if bool(cadence) == bool(cron): raise ValueError`, so exactly one must be supplied — `priority="normal"`, and `description="Collect improvement evidence from completed sessions and Tom-sourced memories (#3177)"`. `register_reflection` writes the **vault** file `~/Desktop/Valor/reflections.yaml` (`_vault_reflections_path()`), never `config/reflections.yaml`, emits `execution_type: function` entries only, and already returns `RegisterResult(True, "skipped", ...)` when `_this_machine_owns_valor(project_dir)` is false — that guard is what pins the tick to the `valor` owner, so no extra pinning code is needed. Idempotent: a no-op once the entry exists.
  3. In `scripts/update/run.py`, add `improvement_collect_register_result: reflection_register.RegisterResult | None = None` to the result dataclass beside `sdlc_upvote_pickup_register_result` (`:167`), and a call site after the `register_sdlc_upvote_pickup` block (`:1227`) with the same log / `_append_warning` shape as the `crash-recovery` block at `:1163-1174`. It must run **before** Step 1.66's vault→config copy, the same ordering rationale recorded at Step 1.655, so the appended entry propagates into this machine's `config/reflections.yaml` on the same cycle.
  - Extend `tests/unit/test_reflection_register.py` with an idempotence case for the new wrapper and a case asserting the entry survives a simulated vault→config sync (Risk 7's mitigation, which had no owner before this bullet)
- **Retire the `TaskTypeProfile` keyspace through a registered migration (concern 6, part 1).** Whole-model deletion leaves orphaned `TaskTypeProfile:*` hashes plus its `$Index:TaskTypeProfile:delegation_recommendation` index set, which no surviving code can reach through the ORM once the class is gone. Follow the three existing strip precedents exactly (`_migrate_strip_pty_session_fields`, `_migrate_schema_diet_fields`, `_migrate_strip_pid_fields`), all of which shell out to a standalone script via subprocess (`scripts/update/migrations.py:207`, `:253`, `:275`): add `scripts/migrate_retire_task_type_profile.py` declaring a minimal module-level `class TaskTypeProfile(Model)` stub — Popoto keys by class name with no override available (`popoto/models/base.py:167-185`), so the stub resolves to the same keyspace — carrying only `id = AutoKeyField()`, `project_key = KeyField()`, `task_type = KeyField()`, `delegation_recommendation = IndexedField(default="structured")`. It enumerates with the bare `TaskTypeProfile.query.filter()` form already used inside a migration at `scripts/update/migrations.py:1073` and calls `instance.delete()` on each row, so the raw-Redis guard is satisfied and index members are removed by the ORM. Register it in the `MIGRATIONS` dict at `scripts/update/migrations.py:1241` under `"retire_task_type_profile"` — a defined-but-unregistered function never runs. Idempotent: a second run enumerates zero rows. **Residue bound if the migration is ever skipped:** `TaskTypeProfile.Meta.ttl = 7776000` (90 days), so every hash self-expires within 90 days of its last write and no reader survives to see it in the meantime
- Add the memory-inspiration observer adapter. **The read seam is enumeration, not search.** `tools/memory_search/__init__.py::search` (`:57`) takes `query, project_key, limit, category, tag, min_act_rate, assess_quality, min_rrf_score` — it has no `source` parameter, early-returns empty on a blank query, and is a relevance-ranked top-N, so it cannot enumerate a partition and an adapter built on it would silently under-read (the Risk 2 failure this plan exists to prevent). Instead, promote the existing private `_fetch_all_records(project_key) -> list` at `tools/memory_search/__init__.py:22` (already shared by `inspect(stats=True)` at `:324` and `status()` at `:449`) to a public helper beside it, and have the adapter call that. `project_key` is the only indexed partition (`KeyField`, `models/memory.py:155`); `source` is a plain `StringField` (`:159-161`), **not** a `KeyField` or `IndexedField`, so `Memory.query.filter(source="human")` is not a queryable dimension and the `source == SOURCE_HUMAN` match happens in Python after the fetch. Dedupe by `memory_id`, write `ImprovementEvidence` rows of kind `inspiration` preserving the original text, its `reference` (`models/memory.py:162`), and its timestamp, and for each row with a URL reference open an `inspiration_intake` investigation that fetches substance (`valor-youtube-transcribe` for YouTube, `WebFetch` or `valor-ingest` otherwise) and records inaccessible sources honestly (charter §4). No poll registry change, no `investigation_id`, no question path
- Add `tools/improvement_eligibility.py::is_open_source(project_key) -> bool`: resolve the project's `github` field from `projects.json`, read visibility with `gh repo view --json visibility`, cache with a TTL in the control namespace, and return `False` on any error, missing field, or unknown value (charter §7 fails closed to "client"). Create `tests/unit/test_improvement_eligibility.py` covering public, private, missing field, and `gh` failure
- Add `tools/improvement_resources.py::probe() -> dict`: for each resource charter §8 names (personal and work Workspace identities, virtual debit card, Cloudflare account and CLI token, `m-valor` write access), report `verified`, `absent`, or `unknown` from vault item title presence, a SHA-256 fingerprint of the credential, and a harmless authenticated read where one exists; never print a value or prefix. Its output is a section of the capability matrix
- Add `VerifyingArtifactStore(FilesystemStore)` under a retention root that re-hashes on every load including the archive fallback; wire it into `ImprovementEvaluation` and `ImprovementExperiment` content fields
- Produce `docs/plans/critiques/recursive-self-improvement-capability-matrix.md` marking each planned component implemented, deployed, measured, or unknown

### 4. Dashboard views
- **Task ID**: build-ui
- **Depends On**: build-records
- **Validates**: `tests/unit/test_ui_app.py`, `tests/unit/test_ui_reflections_data.py`
- **Informed By**: recon (inline routes in `ui/app.py`, `ui/data/` layer, sync handlers)
- **Assigned To**: ui-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `ui/data/improvement.py` read-only queries, `ui/templates/improvement/` partials, `@app.get("/_partials/improvement/...")` routes, and an index card
- **Render only what this build writes**: the coverage partial and the intervention-burden partial, both backed by `ImprovementEvidence`, the provisional-assumptions list grouped by priority area, and the **goals and accomplishments** partial (charter §7): goals are the pinned charter's version and digest, its §3 priorities, and open cases with ranking rationale; the §11 headings (acquired abilities, evaluations, rejected approaches, unresolved assumptions, resource use by budget unit) render with honest empty states until the lane that writes each one lands. Cases, hypotheses, rejected experiments, spend, release lineage, and the `paused_budget` / `inconclusive` / `reconciliation_required` state renderings move to the child issue for the lane that first writes each one (lane 3 for intents and reservations, lane 5 for cases and hypotheses, lane 6 for releases). Shipping six permanently empty tiles is not a dashboard
- **Collapse the two active-status definitions into one. This build adds no new status.** They are two different things and were once cited as one: the module constant `ACTIVE_STATUSES = ("running", "pending", "in_progress", "active", "waiting_for_children")` at `ui/data/sdlc.py:1256` (read at `:1308` and `:1339`), and the inline literal `("pending", "running", "active", "waiting_for_children")` returned by `PipelineProgress.is_active` at `ui/data/sdlc.py:469` (property defined at `:468`). Both belong to `PipelineProgress` and they disagree today: the constant carries `"in_progress"` and the inline tuple does not. Collapse them onto the constant and keep the union, so the property gains `"in_progress"` as a deliberate, tested change rather than a silent one. Assert in `tests/unit/test_ui_sdlc_data.py` that exactly one definition exists and that the property and the constant agree membership for membership. A third `is_active` at `:244` belongs to `StageState`, compares `"in_progress"` on a stage rather than a session status, and is left alone. Adding `admitted` to the collapsed definition is lane 3's one-line follow-on, cheap precisely because this task leaves a single definition to edit

### 5. Validate lanes 1 and 2
- **Task ID**: validate-lanes-1-2
- **Depends On**: build-retire, build-records, build-seams, build-ui
- **Assigned To**: lane-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row; mutation-check each new guard (index guard, verifying store, detector persistence, the collection-tick registration row, the `.env.example` reader row, and the `retire_task_type_profile` registration row) — a green row that reaches no code is the failure mode this task exists to catch
- Confirm no `models/improvement/` directory and no `config/improvement.yaml`
- Confirm retained corpora exist
- Confirm this build introduced no session status: `git diff main -- models/session_lifecycle.py` shows the `# 5.5. Update TaskTypeProfile` block removed and `NON_TERMINAL_STATUSES` unchanged

### 6. File child issues for lanes 3 through 6
- **Task ID**: file-children
- **Depends On**: validate-lanes-1-2
- **Assigned To**: seams-builder
- **Agent Type**: builder
- **Parallel**: false
- File five issues via `/do-issue`, each `Refs #3177`, carrying the relevant Technical Approach subsection, the matching rows from "Carried to child issues" in Success Criteria, the render targets moved out of task 4, and the capability matrix rows each depends on:
  - **Lane 3, control substrate.** Control namespace and Lua transition, dispatch intents with `admitted` status — **this issue owns introducing that status**, which means `models/session_lifecycle.py:72` (`NON_TERMINAL_STATUSES`) and its matching `RECOVERY_OWNERSHIP` entry at `:90` landing in one change (`tests/unit/test_recovery_ownership.py:16` asserts the two key sets are exactly equal), the six test files listed in Test Impact, the `docs/features/session-recovery-mechanisms.md` update, and adding `admitted` to the single `ACTIVE_STATUSES` definition task 4 leaves behind — the `improvement-intent-reconcile` recovery reflection registered on the same `reflection_register` seam task 3 establishes, `valor-improve pause`/`resume`/`doctor` break-glass with its documented manual procedure, the two-unit reservation model with the lane-slot release wired inside `finalize_session`, the scheduler adapter with its `worker:registered_pid:*` liveness check, the `valor-improve` CLI, export/import, and the fault-injection suite. **This issue records a hard dependency on #3183 in two shapes**: on #3183's current build for the create-or-bind seam and the dead-letter record, and on **#3183's lane 6, itself a child issue of #3183** (per #3183's §Relationship table), for the renewed fencing lease. It states the consumer contract from Gap C and blocks on each of the two until that one lands; it does not redesign either. It opens no PR against `agent/agent_session_queue.py`. It also carries the dashboard renderings for intents, reservations, spend, and `paused_budget` / `reconciliation_required`, moved out of task 4. **Gap G and Gap D in this lane:** `valor-improve propose` refuses a case whose `ranking_rationale` is empty, whose `priority_area` is unset, or whose `charter_digest` is not the pinned one, with a reason code; `valor-improve propose-amendment` delivers a charter amendment request and leaves the row `awaiting_authorization`; the three budget units with per-call inference settlement, recurring-charge reservation, credit expiry tracking, and `valor-improve budget`; `tools/vault_write.py` lands here as the one sanctioned `op item create` wrapper, failing closed until the service account has write access to `m-valor`
  - **Lane 4, frozen evaluation inputs.** Memory corpus export, per-arm private Redis helper for tests, `VALOR_PROJECT_KEY` in `_harness_env`, writer kill switch, isolated retrieval adapter with parity check, judge envelope, `tools/improvement_eval/` statistics with Holm correction and named stopping rules, and the blinded `serves_charter` judge calibrated against retained `architectural` corrections (Gap G)
  - **Lane 5, first complete research cycle.** Observer adapters, system model revisions, planner tick, the investigation lifecycle (probe, trace analysis, memory retrieval, inspiration intake, web research, resource acquisition, skill acquisition, charter amendment), the first resource-acquisition action verifying current provider documentation for **Meta Muse 1.3** and any other nearly-free token source and evaluating task suitability on representative tasks, the top-ranked experiment under a frozen contract (charter §3 expects cheap-inference integration into eligible open-source sessions to rank first; journey preservation remains eligible and is no longer mandatory), one complete `skill_acquisition` cycle per charter §5, and the qualified-result report. It also carries: routing `scripts/sdlc_reflection.py`'s lesson extraction through an `ImprovementEvidence` observer adapter so a scraped lesson gains the behavioral validation it has never had. That script is named in "Why Previous Fixes Failed" as exactly the pattern this loop exists to replace, and it stays in production until this lane folds it in. Plus the dashboard renderings for cases and hypotheses, moved out of task 4. **Gap G in this lane:** the planning brief opens with the pinned charter verbatim and the research skill enforces the §9 resolution rule; the `improvement-assumption-digest` reflection (`cadence="259200s"`) sends new provisional assumptions to Tom's Telegram as a status message that asks nothing; any change the loop needs to authority or budgets goes out as an amendment request through `valor-improve propose-amendment`, never as a self-applied edit; the unqualified-promise detector (an LLM judge over outbound client messages, charter §10) lands here as an `ImprovementEvidence` adapter
  - **Lane 6, production promotion and meta-experiments.** Release records, exposure assignment, rollback, observation windows, incident drills, recursive candidate surfaces with budget-matched comparison, and the release-lineage dashboard rendering moved out of task 4. **Gap G and charter §6 in this lane:** the candidate-manifest denylist refuses `docs/improvement-charter.md` and `models/improvement_charter.py` before construction; a qualified core-workflow release opens a PR through the ordinary pipeline with verdict and contract digest attached and merges under normal gates with no extra approval; evaluator and selection-method replacements ship as their own release type with anchor calibration; the accomplishments column of the goals partial is populated from qualified releases
  - **Lane 7, cloud execution capacity (charter §2).** Spikes first, each a code-read or a probe with a recorded finding: which sandbox providers fit the $50/week unit; how a worker, Redis, and a `claude -p` subprocess run remotely with subscription authentication; where evidence, the control namespace, and artifacts persist (this lane supersedes the local retention root and migrates export/import); how a sandbox recovers from a crash unattended; how the fleet-update path treats a sandbox. Then: acquisition through the §8 resources (Cloudflare account and CLI, card) with unit-3 metering and teardown policy, at least one unattended RSI session in a sandbox, and the first-month progress report on #3177 written per §2 (which sessions run where, what sustains them, cost, what still prevents mostly-cloud operation). Blocks on lane 3 for the control namespace and budgets; does not block lane 5, which runs its first cycle on this machine

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-lanes-1-2
- **Assigned To**: improvement-docs
- **Agent Type**: documentarian
- **Parallel**: true
- Execute every item in the Documentation section

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: file-children, document-feature
- **Assigned To**: lane-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run Verification rows, including the child-issue count row; confirm the five child issues exist and reference #3177, that the lane-3 issue records both shapes of its #3183 dependency, and that it owns the `admitted` status introduction; confirm Documentation items landed

## Verification

Every anti-criterion row below was measured against the working tree on 2026-09-06 so none of them can pass vacuously. Where a row asserts an absence, the baseline count is recorded beside it. Multi-file `grep -c` is avoided throughout: per-file `path:N` lines are not a scalar, so each counting row pipes through `wc -l` and uses the `match count == 0` form, which the checker at `agent/verification_parser.py:352` rejects on empty stdout and so fails closed when a target directory is missing.

| Check | Command | Expected |
|-------|---------|----------|
| Autoexperiment script gone | `test -e scripts/autoexperiment.py; echo $?` | output contains 1 |
| Installer and plist gone | `ls scripts/install_autoexperiment.sh com.valor.autoexperiment.plist 2>&1 \| grep -c "No such file"` | output > 1 |
| Service enumeration clean | `grep -c autoexperiment scripts/valor-service.sh` | match count == 0 |
| Docs no longer cite it as live | `grep -rn "autoexperiment" docs/features/README.md docs/features/nightly-regression-tests.md docs/research/claude-code-feature-swot.md \| wc -l` | match count == 0 |
| Legacy corpora retained | `ls data/experiments/summarizer/eval_samples.jsonl data/experiments/observer/eval_corpus.jsonl data/experiments/README.md 2>/dev/null \| wc -l` | output > 2 |
| No models sub-package | `test -d models/improvement; echo $?` | output contains 1 |
| No YAML config | `test -e config/improvement.yaml; echo $?` | output contains 1 |
| Eight flat models exported | `.venv/bin/python -c "import models as m; names=[n for n in m.__all__ if n.startswith('Improvement')]; print(len(names))"` | output > 7 |
| Settings block present | `grep -c "class ImprovementSettings" config/settings.py` | output > 0 |
| Concurrency bound is one lane | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; print(S().max_concurrent_research_sessions)"` | output contains 1 |
| Daily paid-inference dollars set | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; print(int(S().daily_paid_inference_usd))"` | output contains 10 |
| Weekly infrastructure dollars set | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; print(int(S().weekly_infrastructure_usd))"` | output contains 50 |
| Budget windows disclosed | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; print(S().budget_day_boundary, S().budget_week_start)"` | output contains UTC monday |
| Eligibility guard fails closed | `scripts/pytest-clean.sh tests/unit/test_improvement_eligibility.py -q` | exit code 0 |
| Resource probe never leaks | `scripts/pytest-clean.sh tests/unit/test_improvement_resources.py -q` | exit code 0 |
| Index guard covers new models | `scripts/pytest-clean.sh tests/unit/test_agentsession_index_guard_generalized.py tests/unit/test_improvement_models.py -q` | exit code 0 |
| Verifying store rejects corrupted archive | `scripts/pytest-clean.sh tests/unit/test_length_safe_content_store.py -q -k verifying` | exit code 0 |
| Correction detector persists events | `scripts/pytest-clean.sh tests/unit/test_improvement_evidence.py -q` | exit code 0 |
| Memory-inspiration adapter reads Tom-sourced memories | `scripts/pytest-clean.sh tests/unit/test_improvement_evidence.py -q -k inspiration` | exit code 0 |
| Dashboard partials render | `scripts/pytest-clean.sh tests/unit/test_ui_app.py -q -k improvement` | exit code 0 |
| One active-status definition in `ui/data/sdlc.py`, not two | `scripts/pytest-clean.sh tests/unit/test_ui_sdlc_data.py -q -k active_statuses` | exit code 0 |
| Collection tick registered (blocker 2; the wrapper exists and `run.py` calls it) | `.venv/bin/python -c "from scripts.update import reflection_register as r; import inspect, scripts.update.run as u; print(hasattr(r,'register_improvement_collect') and 'register_improvement_collect' in inspect.getsource(u))"` | output contains True |
| Collection-tick registration is idempotent and vault-targeted | `scripts/pytest-clean.sh tests/unit/test_reflection_register.py -q -k improvement` | exit code 0 |
| `.env.example` declaration has a reader (concern 7) | `scripts/pytest-clean.sh tests/unit/test_env_declaration_readers.py -q` | exit code 0 |
| `TaskTypeProfile` module and its finalize hook are gone | `test -e models/task_type_profile.py; echo $?` | output contains 1 |
| `TaskTypeProfile` keyspace retirement is registered, not merely defined | `grep -c "retire_task_type_profile" scripts/update/migrations.py` | output > 1 |
| Five child issues exist and reference #3177 (nit 3) | `gh issue list --state all --search "\"Refs #3177\" in:body" --json number --jq length` | output > 4 |
| Anti-criterion: the rework aggregate and its dead field are gone (re-measured 2026-09-07 with `/usr/bin/grep`, reproducing the round-2 baseline exactly: **27** matches across exactly `models/agent_session.py`, `models/task_type_profile.py`, `tools/session_tags.py`, including the writerless field at `models/agent_session.py:221` that made the old row pass vacuously. Deleting `models/task_type_profile.py` whole retires 26 of the 27 at a stroke; the 27th is `models/agent_session.py:221`) | `grep -rnE "rework_rate\|rework_triggered\|failure_stage_distribution\|get_delegation_recommendation" models/ agent/ reflections/ tools/ ui/ \| grep -v "__pycache__" \| wc -l` | match count == 0 |
| Anti-criterion: no child-gate bypass on the research surface (measured 2026-09-06: **0** with the one definition site and `__pycache__` excluded, 3 without. The `__pycache__` filter is load-bearing: a stale `.pyc` matches and `/usr/bin/grep` reports it while a `.gitignore`-honoring grep does not) | `grep -rn "VALOR_ALLOW_CHILD_SESSIONS" agent/ tools/ models/ reflections/ \| grep -v "models/child_session_gate.py" \| grep -v "__pycache__" \| wc -l` | match count == 0 |
| Anti-criterion: control modules never bind the Popoto client (Gap A private alias; paired with the "Eight flat models exported" row above so a missing target cannot masquerade as a pass) | `grep -rn "POPOTO_REDIS_DB" models/ ui/ \| grep improvement \| grep -v "__pycache__" \| wc -l` | match count == 0 |
| Anti-criterion: no routine research-question path to a human (measured 2026-09-06: **0**; charter §9 permits exactly one message class, the amendment request, which lives in lane 3's `tools/improvement_amendment.py` and is excluded) | `grep -rnE "investigation_id\|daily_question_ceiling\|ask_poll\|AskUserQuestion" bridge/ tools/ config/ models/ ui/ reflections/ \| grep -i improvement \| grep -v "tools/improvement_amendment.py" \| grep -v "__pycache__" \| wc -l` | match count == 0 |
| Charter pinned is version 2 | `grep -c "^version: 2" docs/improvement-charter.md` | output contains 1 |
| Anti-criterion: no controller module handles a credential outside the one sanctioned writer (`[EXTERNAL]` No-Go, charter §8; measured 2026-09-06: **0**) | `grep -rnE "OP_SERVICE_ACCOUNT_TOKEN\|op read\|op run\|op item\|Desktop/Valor/.env" models/ ui/ tools/ reflections/ \| grep improvement \| grep -v "tools/vault_write.py" \| grep -v "__pycache__" \| wc -l` | match count == 0 |
| Charter file exists and is human-owned | `grep -c "^owner: Tom Counsell" docs/improvement-charter.md` | output contains 1 |
| Charter seeds by digest and round-trips (Gap G, level 0) | `scripts/pytest-clean.sh tests/unit/test_improvement_charter.py -q` | exit code 0 |
| Anti-criterion: no improvement module writes the charter file (measured 2026-09-07: **0**) | `grep -rn "improvement-charter.md" models/ tools/ reflections/ ui/ \| grep -v "__pycache__" \| grep -vE "read_text\|open\(.*'r'\|load_from_file" \| wc -l` | match count == 0 |
| Format clean | `.venv/bin/python -m ruff format --check .` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check .` | exit code 0 |

## Critique Results

**Revisions of 2026-09-07 post-date round 3.** Gap G (charter as north star) and then the charter v2 reconciliation (compounding-capacity framing, three budget units, the amendment path, provider eligibility, merge authority, lane 7 cloud execution, Risks 7 and 8, and seven new Verification rows) were applied after the round-3 verdict below and have not been critiqued. `/do-plan-critique` runs again before `/do-build`.

Round 3 — FULL roster (Risk & Robustness, Scope & Value, History & Consistency), sequential lenses (Agent tool unavailable: not in tool list), plus automated structural checks. **READY TO BUILD (no concerns)**: 0 blockers, 0 concerns, 7 nits.

Round 2's three blockers, four concerns and three nits were each re-verified against the working tree rather than accepted from the Addressed By column, and every line number the revision cites was re-derived by locating its symbol. All ten hold as resolved. The `TaskTypeProfile` blast radius reproduces at exactly seven files with the claimed per-file counts (33 / 4 / 1 / 54 / 29 / 23 / 15) and `models/__init__.py` carries no reference, so the whole-module delete strands no import; a widened `rework_triggered|rework_rate|failure_stage_distribution` sweep across eleven roots finds no writer outside the deleted module. The registration path's every cited site exists (`reflection_register.py:74-75`, `:475`, `:500-503`, `:559`; `run.py:167`, `:1163-1174`, `:1227`, Step 1.66 at `:1285`) and the Verification row's command shape was executed with the `crash-recovery` analogue substituted and returned `True`. No lane-1/lane-2 task touches `models/session_lifecycle.py:72` or `:90`. Structurally: 30 Verification rows parse through `agent/verification_parser.py::parse_verification_table` with 0 malformed and 0 skipped; all four Prerequisites pass; task numbering 1-8 is contiguous and its `Depends On` graph acyclic; every referenced path exists. The `wc -l` + `match count == 0` composition was executed against `evaluate_expectation` and behaves as the plan assumes, empty-stdout gate included. All five anti-criterion baselines reproduce (rework 27, child-gate 0, popoto-in-improvement 0, question path 0, credential 0), and the two non-zero-today rows bite (`valor-service.sh` 2, docs 3).

**#3183 cross-reference re-confirmed.** `docs/plans/etl-pipeline-hardening.md` was read read-only. Its §Relationship to #3177 table assigns the create-or-bind seam to its own lane 5b, the fencing lease to its lane 6 (explicitly a child issue of #3183), and the generalized dead-letter record to its lane 2, and states "Nothing here changes #3177's scope." Both of this plan's claims hold: #3177 consumes those primitives without redesigning them, and nothing in the lane-1/lane-2 build depends on a seam shape #3183 has not settled.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| NIT | Risk & Robustness | Verification row "`TaskTypeProfile` keyspace retirement is registered, not merely defined" runs `grep -c "retire_task_type_profile" scripts/update/migrations.py` expecting `output > 1`, which a defined-but-unregistered migration also satisfies (wrapper name plus script-filename string already give two). The row does not prove the property its name claims. | pending | Task 5 already mandates a mutation check on this exact row, which would catch it. A stronger form: `.venv/bin/python -c "from scripts.update.migrations import MIGRATIONS; print('retire_task_type_profile' in MIGRATIONS)"` with `output contains True`. |
| NIT | Risk & Robustness | Verification preamble cites `agent/verification_parser.py:352` for the empty-stdout rejection. `:352` is the closing paren of a `MalformedRow(reason="Check, Command, and Expected must all be non-empty.")`; the empty-stdout gate is documented at `:374-377` and implemented at `:408-414`. | pending | n/a |
| NIT | Scope & Value | Task 3's registration bullet cites "the guard at `:501`". The `if bool(cadence) == bool(cron):` is at `scripts/update/reflection_register.py:500`; `:501` is the `raise ValueError`. | pending | n/a |
| NIT | Scope & Value | Tasks 6 and 7 carry no `Validates:` field, unlike tasks 1-4. Coverage exists elsewhere (the child-issue count Verification row; task 8's documentation confirmation), so the gap is presentational. | pending | n/a |
| NIT | History & Consistency | Architectural Impact says "eleven further modules and tests read `NON_TERMINAL_STATUSES`" and then lists fourteen paths. The measured reader set across `models/ agent/ tools/ reflections/ bridge/ worker/ ui/ tests/` is fifteen files, the fifteenth being `tests/unit/test_recovery_ownership.py`. | pending | n/a |
| NIT | History & Consistency | Task 3 cites the three subprocess strip precedents as `scripts/update/migrations.py:207`, `:253`, `:275`. The first two land inside `_migrate_strip_pty_session_fields` and `_migrate_strip_pid_fields` as claimed, but `:275` is inside `_migrate_strip_pty_session_fields_v2`; `_migrate_schema_diet_fields` shells out at `:227`. | pending | n/a |
| NIT | History & Consistency | Spike-2's "Impact on plan" still reads as #3177 adding `idempotency_key` and a preallocated `agent_session_id` to `_push_agent_session`, contradicting Gap C, the Reuse map, Architectural Impact and the `[SEPARATE-SLUG #3183]` No-Go, which all state in bold that this plan changes no queue signature. Both cited line numbers are correct (`:361` is `if deleted_count:`, `:370` is `await AgentSession.async_create(`); only the ownership attribution is stale, and it sits in deferred lane-3 scope. | pending | Reword to "Gap C states the create-or-bind contract lane 3 consumes from #3183 at that seam" and keep `:361`/`:370` as the location evidence. |

---

## Decisions Recorded (formerly Open Questions)

All open questions are answered. Tom's decisions of 2026-09-06 are recorded as comment 5560558494 on #3177; the charter decisions of 2026-09-07 are recorded in `docs/improvement-charter.md` and in item 4 below.

1. **`rework_rate` branch: delete.** The aggregate, `failure_stage_distribution`, `get_delegation_recommendation`, their readers in `tools/session_tags.py`, and the writerless `rework_triggered` field at `models/agent_session.py:221` all go. Rework is derived from `ImprovementEvidence` rows carrying `classification="architectural"`, which have a real writer. Enforced by the rework anti-criterion in Verification, which returns 27 today and must return 0.
2. **No routine research questions; amendment requests allowed (charter v2 §9, superseding the 2026-09-06 "zero" wording).** The controller resolves research and implementation uncertainty from evidence and investigation and records what it cannot resolve as a guarded provisional assumption. It may send Tom one kind of message: an evidence-backed charter amendment request through `valor-improve propose-amendment`, continuing independent work while it waits; silence is not approval. There is no `daily_question_ceiling` setting and no poll-registry change (Gap F). Enforced by the no-routine-question anti-criterion.
3. **Budget: three units (charter v2 §8).** Subscription work is one SDLC lane of concurrency (`max_concurrent_research_sessions=1`, an operating choice, never a charter cap). Paid inference is **$10 per UTC day** (`daily_paid_inference_usd=10.00`), settled per call, shared by controller, workers, and evaluators through separate reservations. Infrastructure is **$50 per ISO week from Monday 00:00 UTC** (`weekly_infrastructure_usd=50.00`) covering sandboxes, storage, Cloudflare, and other RSI infrastructure, with recurring charges reserved for the window, credit expiries tracked, unknown metering treated as a pause, and no transfer between units (Gap D).
4. **Charter v2 (Tom, 2026-09-07, `docs/improvement-charter.md`), superseding the v1 interview record that follows.** v2 makes compounding capacity the north star, sets a first-month expectation of mostly cloud-sandbox round-the-clock RSI, replaces independence-first weighting and the fixed allocation with evidence-based ranking that initially favors cheap inference, token efficiency, skills, personas, and cloud execution, permits any provider for open-source work while client work stays on the Claude and Codex subscriptions, grants merge authority for evidence-backed core-workflow changes through the ordinary pipeline, allows client contact within assigned responsibilities under a strict no-promises rule, splits spending into $10/day inference and $50/week infrastructure, and keeps charter amendment with Tom while letting Valor propose. This plan's Gap D, Gap F, Gap G, No-Gos, lanes 5 through 7, and Verification rows were reconciled to it in this revision. **The v1 record, kept for lineage:** Valor is the CEO a retired founder hired: as independent as possible, discerning about when to bring something to the board, ambitious in support of assigned work, never wasteful, and worthy of the role through subagent hiring and training, sound delegation, and trustworthiness. Purpose: posture adapts per client project; humans are reserved for the highest-leverage asks because the 2027 target runs on parallel subagents where stopping for a human is the costliest decision. Consequences carried into this plan: a **fourth measured objective, delegation proficiency**; **independence wins, quality is the floor** when objectives conflict; the zero-question rule applies to the self-improvement loop while client work uses Valor's discernment; Valor may **open accounts, accept terms, spend within the external budget, and write a credential he obtained to the `m-valor` vault himself** through `tools/vault_write.py` (Tom's one remaining action is granting the service account write access); identity, persona, public profiles, the charter, approval signals, real stakeholder contact, and automatic promotion stay Tom's; new provisional assumptions are **pushed to Telegram every three days** as a status message and the dashboard keeps a running list of goals and accomplishments. Enforced by Gap G's seed-by-digest loader, the `propose` refusal, the candidate-surface denylist, and the three new Verification rows.
