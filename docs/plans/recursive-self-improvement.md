---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-05
baseline_commit: 85524b94092d7062e0ae5b231a7ef57385a711f3
tracking: https://github.com/tomcounsell/ai/issues/3177
last_comment_id: 5560558494
---

# Recursive self-improvement controller

## Problem

Valor improves only when a human notices a weakness and files work. The system records what it did and keeps no durable evidence of whether the work served its purpose, which corrections were architectural rescues rather than preference tweaks, or which past experiments were tried and rejected. The one prior attempt at autonomous self-optimization, `scripts/autoexperiment.py`, is unsafe and dead.

Tom supplied three success criteria for the system as it matures:

1. Less architectural rescue from Tom. The dominant failure is locally plausible work that misses the end-to-end user journey.
2. Greater competence with clients and nontechnical stakeholders, including acquiring meeting and voice capabilities when useful.
3. A plateau in bug creation.

These operationalize as a vector, never a single gameable reward:

| Objective | Primary measures | Required context and guardrails |
|---|---|---|
| Architectural independence | Rescue incidence and severity per comparable task; observed Tom time | Attempted workload, difficulty, abandoned work, whether needed clarification was suppressed |
| Stakeholder competence | Verified communication-task completion; corrected misunderstandings; accurate and fulfilled commitments | Capability coverage, factual accuracy, stakeholder feedback, latency, authorized scope |
| Sustainable quality | Unique defect arrival per exposure; recurrence; severity-weighted unresolved debt | Raw issue count, detection coverage, duplicate and label changes, throughput, observation window |

Classification of a correction (architect intervention, ordinary preference, new scope, expected domain clarification) is uncertain evidence until corroborated. Time estimates are never invented from message counts. A declining bug count with declining detection is not a win. Raw measures publish beside normalized ones.

**Current behavior:**

- `Job` records the goal and when it was discharged, never why or on what evidence. Success and abandonment are indistinguishable from rest-by-age (`models/job.py:491-522`).
- Human corrections are detected by a flat regex list (`reflections/utilities.py:31-40`) whose result is a transient dict. Nothing classifies a correction.
- `TaskTypeProfile.rework_rate` is structurally zero: `rework_triggered` has zero production writers, `failure_stage_distribution` has zero writers, `get_delegation_recommendation` has zero callers.
- No evaluator compares a full candidate agent run against an incumbent. Every judge scores one artifact with candidate identity visible. No holdout split, blinding, or judge calibration exists.
- Ideas Tom sends as links land as `Memory` rows with `source="human"` (`.claude/hooks/hook_utils/memory_bridge.py:801-836`) and nothing reads them as research input. No record links a claim to the source and date it came from, so an assumption about a provider, a price, or a technique is indistinguishable from a guess.
- `scripts/autoexperiment.py` assigns `self.branch` at line 308 and never reads it, so it commits to whatever branch is checked out. Its `--dry-run` still overwrites tracked source and spends API money. Its installer defaults to a target whose module was deleted in #466, so a default install schedules a nightly job that raises `KeyError` forever.

**Desired outcome:**

A persistent controller, served by bounded sessions and existing reflections, that can notice a consequential weakness, decide what it needs to learn, acquire that information on its own (mining Tom-sourced memories for inspiration and researching current practice online), build and evaluate a candidate in isolation, and explain with durable evidence why the next version deserves to exist. Claim levels:

1. **Loop operational:** one complete autonomous investigation-to-measurement cycle.
2. **System improvement demonstrated:** held-out and production gains over incumbent.
3. **Recursive improvement demonstrated:** a changed research process produces greater validated gains per comparable total budget on fresh opportunities.

Repeated edits alone satisfy none of the latter two. This is system-level recursive improvement with externally supplied models; model-weight training and claims of unbounded acceleration are out of scope. This document proposes implementation; it does not authorize deployment, meeting attendance, new external communications, or changes to human-owned approval signals.

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
- #3095 (`/ask-me` poll deferrals): open; no longer relevant, because this plan asks no questions (see Gap F).
- #1312 / PR #2196 (bridge reacts when no worker is alive): shipped and closed. **No overlap.** Re-verified 2026-09-06: the liveness substrate it established is the `worker:registered_pid:{hostname}:{pid}` key family, prefix constant at `agent/session_health.py:216`, written with TTL by `register_worker_pid` at `:5268`, read by a `scan_iter` over the prefix at `:6463`, with per-PID freshness at `_worker_pid_heartbeat_fresh` (`:5393`). The scheduler adapter reuses that read before activating an `admitted` session (Gap B), so a research session never rots in a queue no worker is draining.
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
- **Impact on plan**: Gap C uses a new `admitted` status. Task list names the vocabulary edit and the dashboard tuple.

### spike-2: queue create-or-bind contract
- **Assumption**: "`_push_agent_session` can be given a stable identity and an external action key so a crash-retry binds to the existing row."
- **Method**: code-read
- **Finding**: Holds, with one nuance. `_push_agent_session` (`agent/agent_session_queue.py:204-231`) accepts caller-supplied `session_id`, `correlation_id`, `scheduled_at`, `parent_agent_session_id`, `telegram_message_key`; the row's primary key is a Popoto `AutoKeyField` minted inside `async_create` (`:369-397`), so a retry always mints a second row, and that row wins newest-wins (`models/agent_session.py:1209-1229`), orphaning the first. `correlation_id` is log-only (`models/agent_session.py:358`; read at `agent_session_queue.py:1756, 3015`), never filtered. The `count() > 0` check at `:333-336` gates a telemetry log line, not creation. Popoto `get_or_create` (`~/src/popoto/src/popoto/models/base.py:1760-1836`) is get-then-create with a post-hoc retry, not atomic, and `session_id` is not unique. No `SET NX`-style model create exists. The repo's external-key precedent is the raw `SET NX EX` run-claim at `models/session_lifecycle.py:856`.
- **Confidence**: high
- **Impact on plan**: Gap C adds `idempotency_key` and optional preallocated `agent_session_id` to `_push_agent_session`, guarded by an NX key in the control namespace, placed after the stale-terminal reconcile at `:361` and before `async_create` at `:369`.

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
9. **Decision and release**: inconclusive returns to investigation with a new registered action; reject closes the case with evidence; qualify produces an `ImprovementRelease` record for human review. Automatic promotion stays disabled.
10. **Output**: dashboard partials under `/_partials/improvement/` render coverage and intervention burden in this build; cases, hypotheses, spend, and release lineage arrive with the lanes that first write them. No branch of this flow sends a question to a human. Unresolved decisions become provisional assumptions carrying their evidence, revisited when new evidence arrives.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #411 autoexperiment | Overnight loop: extract prompt, LLM proposes edit, re-score, keep if higher | No branch isolation (branch name assigned, never checked out); single ungrounded judge with strict-inequality accept, so judge noise alone yields roughly half spurious "improvements"; exceptions dropped from the mean silently; dry-run still mutates source and spends money; default target deleted in #466 and never repaired |
| `scripts/sdlc_reflection.py` | Scrapes `- lesson:` lines from PR bodies into `docs/sdlc/{stage}.md` | Extraction with zero behavioral validation; a lesson's effect on later behavior is never measured |
| `TaskTypeProfile` aggregates | Per-task-type maturity and rework rates | Instrumentation was never wired: `rework_triggered` has no writer, so the rate is a constant zero that reads as "no rework" |

**Root cause pattern:** each attempt measured a proxy the system could inflate or that nothing populated, and none isolated the candidate from production state. This plan makes evidence, isolation, and fencing the substrate rather than features layered on later.

## Architectural Impact

- **New dependencies**: none external. Redis Lua scripting is already in use (`models/session_lifecycle.py:1357`, `agent/supervised_run.py:306`).
- **Interface changes**: `NON_TERMINAL_STATUSES` gains `admitted`; `config/settings.py` gains `ImprovementSettings`; `ContentField` consumers gain a second store instance; `ui/app.py` gains one HTMX partial route family; `_harness_env` gains `VALOR_PROJECT_KEY` for research sessions; `stop.py` and the prompt-ingest path gain an env-gated kill switch. **This plan changes no queue signature.** `_push_agent_session` is #3183's to change; #3177 consumes whatever contract #3183 lands (Gap C).
- **Coupling**: the controller depends on Job, AgentSession, the reflection scheduler, the poll registry, and the pipeline ledger through their public APIs. Nothing existing depends on the controller. The control journal introduces one new consistency boundary in a dedicated Redis namespace, authoritative for research transitions only.
- **Data ownership**: `ImprovementCase` owns research state; Job owns responsibility; AgentSession owns execution; PipelineLedger owns implementation stages; the control journal owns transition authority; flat records are projections. None substitutes for another.
- **Reversibility**: every piece is additive and behind `ImprovementSettings.enabled` defaulting to false. Removing the controller deletes flat modules, the namespace, the settings block, the partial route, and the reflection registration; production paths are untouched.

## Appetite

**Size:** Large

**Team:** Solo dev per lane, plan critic, code reviewer, one human decision point per charter field.

**Interactions:**
- PM check-ins: 0 for research. The charter's human-owned fields (contact permissions, spend limits, releasable surfaces, acceptable regressions) are set by the values recorded in this plan and amended only when Tom chooses to amend them. The controller never generates a question for a human; the daily question ceiling to Tom is zero (Gap F).
- Review rounds: 2+ per lane

Delivery is staged as six lanes (see Step by Step Tasks). Lanes 1 and 2 (retire autoexperiment; evidence and reuse contracts) are the build this plan dispatches; lanes 3 through 6 get child issues filed from lane 2's capability matrix, each referencing #3177. The original calendar estimate is withdrawn: queue admission, financial reservations, and memory isolation are newly explicit engineering work, and the re-estimate happens after lane 2.

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

Three execution authorities remain as in the original design. Research authority reads scoped evidence, authors hypotheses, requests investigations, and proposes experiments within the charter. Candidate authority modifies declared candidate surfaces in isolation and cannot read holdout answers, alter release policy, touch production state, or contact stakeholders. Evaluation and release authority runs frozen contracts and writes access-controlled verdicts under a separate process identity. A worktree is not a security boundary; until evaluator secrets and production credentials are separated from candidate execution, automated promotion stays disabled.

### Flow

Real work → observer adapters write evidence → planner tick proposes an action → control journal admits it (fence, reservation, intent) → scheduler adapter materializes an `admitted` session and activates it → session runs isolated and submits a tagged result → evaluation runner scores paired trials blind → decision (inconclusive / reject / qualify) → dashboard and, for qualify, a release record for human review → evidence feeds the next tick.

### Technical Approach

#### Grounding corrections (from #3177 recon and the accepted revision proposal)

| Plan area | Correction |
|---|---|
| Autoexperiment | Retire first, standalone, with no replacement dependency. Never imply observed production optimization; it never ran here. |
| Session evidence | `models/session_log.py` is a nine-line shim. Evidence sources are `models/agent_session.py`, `models/session_event.py`, and the actual transcript and event producers; coverage is identified by producer, not by model existence. |
| Job expectations | Both existing directions are obligations the system owes (requester→us, PM→lane). No agent→human expectation exists; the observer records human interventions as evidence rather than as expectations. |
| TaskTypeProfile | Excluded as a rework baseline. Historical zeros are unavailable evidence, never low rework. Lane 2 either wires `rework_triggered` from a verified event or deletes the aggregate and its dead reader; the dashboard asserts historical zeros never render as measured success. |
| Model layout | Flat modules `models/improvement_charter.py`, `models/improvement_evidence.py`, `models/improvement_model_revision.py`, `models/improvement_case.py`, `models/improvement_investigation.py`, `models/improvement_experiment.py`, `models/improvement_evaluation.py`, `models/improvement_release.py`, each exported from `models/__init__.py` with the schema-gate docstring. No `models/improvement/` package. |
| Configuration | `ImprovementSettings(BaseModel)` in `config/settings.py` beside `FeatureSettings` and `HybridEvalSettings`, env-overridable with the `__` nested delimiter. Immutable charter versions are `ImprovementCharter` records in Redis. No `config/improvement.yaml`. |
| UI | Routes are inline in `ui/app.py` (no `ui/routers/`); data layer under `ui/data/improvement.py`; templates under `ui/templates/improvement/`; sync `def` handlers; read-only. `docs/features/web-ui.md` is corrected in the same lane. |
| Scheduling | Controller ticks are function reflections registered through `scripts/update/reflection_register.py` from `scripts/update/run.py`, pinned to the `valor` project's owning machine. No agent-type reflection entry, so no vault hand-edit. |
| Shared substrate | The create-or-bind seam, the renewed fencing lease, and the dead-letter record belong to #3183. This plan states the contract it needs and consumes the result. |
| Human questions | None. The controller resolves uncertainty from Tom-sourced memories and online research, and records what it still does not know as a provisional assumption with its evidence. |

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
| Worker liveness before activation | `worker:registered_pid:*` (prefix constant `agent/session_health.py:216`, written by `register_worker_pid` at `:5268`, scanned at `:6463`, freshness at `_worker_pid_heartbeat_fresh` `:5393`) | the scheduler adapter requires at least one fresh key before flipping `admitted` to `pending` |
| Bootstrap confidence intervals | `tools/memory_eval/metrics.py` | import; add per-endpoint thresholds, clustered resampling by project, repeated-selection correction outside it |
| Judge record shape | `agent/sdlc_review_consensus.py` (`judge_id`, `verdict`, `blockers`, confidence) | wrap with experiment ID, contract digest, evaluator version, trial ID, raw-response reference, blinded arm ID |
| Large artifacts | Popoto `ContentField(store=...)` with a dedicated `FilesystemStore` subclass under a retention root | verifying subclass re-hashes on every load including the archive path |
| Digest strings | `tools/sdlc_verdict.py:135-224` `"sha256:<hex>"` normalized form | none |
| Scheduler ticks and timeouts | `agent/reflection_scheduler.py` (`asyncio.wait_for`, startup concurrency cap) | none |
| Top-level session creation | `_push_agent_session` / `valor_session.create_session` | none from this plan. #3183 owns the idempotent create-or-bind seam; #3177 states the contract it needs and calls it (Gap C) |
| Execution fence | `agent/pid_fence.py::fence_is_live` | none |
| Renewed lease with fencing generation | #3183's lease module, itself modeled on the `models/session_lifecycle.py:1357` Lua CAS | none from this plan. The control journal imports it wherever it needs a lease (Gap A) |
| External-LLM spend | `tools/cross_vendor_judge.py` over the OpenRouter endpoint in `config/models.py:56`, per-call usage in the response envelope | per-call settlement against the daily dollar reservation (Gap D) |
| Correction detection | `reflections/utilities.py` `CORRECTION_PATTERNS`, `reflections/session_intelligence.py:60-95` | promoted to a persisted, typed detector emitting `SessionEvent` kinds `intervention` and `correction` with a classification field |
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
- **Worker liveness before activation (#1312 / PR #2196, no overlap).** Before flipping an `admitted` session to `pending`, the adapter requires at least one fresh `worker:registered_pid:*` key: prefix constant `WORKER_REGISTERED_PID_KEY_PREFIX` at `agent/session_health.py:216`, written with TTL by `register_worker_pid` at `:5268`, read by the `scan_iter` pattern at `:6463`, with per-PID freshness available from `_worker_pid_heartbeat_fresh` at `:5393`. With no live worker the adapter leaves the session `admitted` and records the reason, so a research session never rots in a queue nothing is draining. Reuse is by convention, not by a public helper: no `is_a_worker_alive()` exists today, so lane 3 extracts one from the existing scan rather than duplicating it inline.
- Fanout bound: `ImprovementSettings.max_concurrent_research_sessions` (default **1**, one SDLC lane of concurrency) enforced as a reservation in the control namespace, on top of the worker's global `MAX_CONCURRENT_SESSIONS`. See Gap D for why the Claude unit is a lane rather than a dollar figure.
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
- **Inert materialization**: the session is created with `status="admitted"`, a new value added to `NON_TERMINAL_STATUSES` (`models/session_lifecycle.py:72`). It is also added to the two dashboard sites, which are different things and must be named as such: the module constant `ACTIVE_STATUSES` at `ui/data/sdlc.py:1256`, and the inline literal returned by the `is_active` property at `ui/data/sdlc.py:469` (defined at `:468`). Lane 2 makes the property read the constant so there is one place to edit; a builder greping for `ACTIVE_STATUSES` would otherwise find one site and leave `admitted` reading as inactive on the other path. `RECOVERY_OWNERSHIP` (`:90`) gets an entry for documentation value only; it is informational (`:88`) and establishes no recovery, which is why the reconciliation reflection in Gap A exists. The worker never selects `admitted`. The adapter flips it to `pending` after binding and after the liveness check in Gap B, and the worker validates the current dispatch generation at pickup through the provenance fields.
- **Recovery**: on restart, reconcile by action ID: retry materialization or activation, never mint another identity. The `improvement-intent-reconcile` reflection in Gap A covers intents the restart pass misses. Session completion without the required result artifact leaves the intent at `running` and the case at `experimenting`, surfaced as incomplete research.
- **Dead letters**: an intent that exhausts its attempts is written to **#3183's one dead-letter record**, with `stage="improvement-intent"`. This plan defines no second dead-letter sink.
- The design promises idempotent admission and recoverable at-least-once execution. External effect adapters carry their own idempotency.

#### Gap D: two reservation units, one of which is not money

Claude work runs on the Claude subscription. Dollars are the wrong unit for it, and the killed-turn undercount spike-5 found is an accounting artifact of a meter nobody is billed by. The budget therefore has **two units**, reserved and settled separately.

**Unit 1: the subscription lane slot.** The scarce Claude resource is concurrency, not money.

- `ImprovementSettings.max_concurrent_research_sessions = 1`: one SDLC lane of concurrency, the same shape as any other lane on this machine. Admission checks `outstanding_slots + 1 <= max_concurrent_research_sessions` atomically in the Lua call that admits the intent.
- A slot is held by the intent's action ID and released on session termination. `AgentSession.total_cost_usd` (written by `accumulate_session_tokens`, `agent/sdk_client.py:191`) is recorded on the intent as **informational provenance** and is never a gate. Nothing settles a Claude turn "at the authorized maximum" and nothing pauses on a Claude dollar figure, because no Claude dollar figure is being spent.
- **Release fires on every termination path.** The slot release and the intent's settlement transition live inside `models/session_lifecycle.py::finalize_session` (`:233`) as a post-transition side effect, gated on the session carrying reservation and action-ID provenance. `finalize_session` is the common floor reached from the Stop hook, `agent/session_health.py`, `worker/__main__.py`, and `tools/agent_session_scheduler.py`, so a watchdog-finalized or crash-recovered session releases its slot exactly like an ordinary completion. Wiring settlement to the ordinary completion path alone would strand a slot forever and, at a bound of 1, would wedge all research. The `improvement-intent-reconcile` reflection (Gap A) is the backstop for a slot whose session row vanished entirely.

**Unit 2: daily external-LLM dollars.** Non-Claude LLM calls (judges, cheap bulk evaluators) run through the existing OpenRouter path: `tools/cross_vendor_judge.py` over the endpoint in `config/models.py:56`.

- `ImprovementSettings.daily_external_llm_usd = 10.00`, a rolling daily reservation in the control namespace keyed by UTC date. Admission checks `settled_today + outstanding + requested_max <= daily_external_llm_usd`.
- **Per-call settlement.** OpenRouter returns usage in the response envelope, so each call settles against its own reported usage immediately after it returns. There is no killed-turn hole here: a call that never returns reserved and never settled, and its outstanding amount is released by the reconciliation reflection. Duplicate receipts are idempotent.
- Exhausting the daily dollars pauses external evaluation only; lane-slot work continues. Unknown external spend pauses external evaluation until reconciled.
- Controller and evaluator hold separate dollar reservations out of the same daily pool, so a runaway controller cannot consume the budget needed to evaluate or revert it.

**Cheaper token sources are a research target, not an assertion.** `config/models.py` already carries a free-tier entry (`OPENROUTER_GEMMA4_FREE`, `google/gemma-4-e2b:free`, `:139`) and a low-cost experiment model (`OPENROUTER_KIMI_K2_5`, `:133`), so the cheap-evaluator path exists. Tom named **"Meta Muse 1.3"** as a candidate nearly-free token source. This plan asserts nothing about it: no pricing, no availability, no capability, no API shape. It is recorded as the **first target of a resource-acquisition research action** (see the planner's capability-expansion allocation), which verifies current provider documentation by `WebSearch`/`WebFetch`, records what it finds as an `ImprovementInvestigation` row with source URLs and retrieval date, and only then proposes an adapter.

**What tooling cannot enforce.** `agent/tool_budget.py` remains a second-line backstop; it reads `total_cost_usd` inside a PreToolUse evaluator (`:123`), so within a turn it sees only prior turns' settled spend and cannot fire on the first paid call of the current turn. It is never the enforcement mechanism. Hosted harnesses expose no hard per-call ceiling, so the charter distinguishes a conservative admission estimate from an enforceable ceiling and states the bounded-overshoot policy for external calls. No claim of a strict dollar bound is made where tooling cannot enforce one.

**Human attention is not a budget line, because it is not spent.** The daily question ceiling to Tom is zero (Gap F). Observed intervention burden is still measured and published beside rescue incidence, as an outcome of the system's behavior rather than a resource it draws down.

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
- **Planner**: one bounded tick chooses investigate, experiment, defer, retire, or escalate with rationale and the evidence that could change it. Ordinal ranking on impact, exposure, uncertainty, cost, and reversibility until calibration supports numbers. Portfolio allocation 60/25/15 (recurring failures / capability expansion / research-process improvement), provisional and recorded in the charter. Hypotheses name a mechanism and a falsifier; a novelty check retrieves prior investigations and rejected experiments.
- **Resource-acquisition research** is an explicit action type inside the capability-expansion allocation, not an accident of it. The mandate is to seek out and integrate anything that makes the loop faster or cheaper: nearly-free or free token sources, evaluation tooling, statistics libraries, harness features, measurement instruments. Each such action is an `ImprovementInvestigation` of kind `resource_acquisition` recording what was searched, what the provider's current documentation says, the retrieval date, and what an adapter would cost. Two constraints bound it. A source that needs **no credential** may be integrated autonomously behind `ImprovementSettings`, defaulting off. A source that needs a **new credential** stops at a prepared adapter plus a written request naming the vault item; the key itself is placed in `~/Desktop/Valor/.env` by Tom (see No-Gos, `[EXTERNAL]`). The system proposes and prepares; it never acquires a secret.
- **Investigations without questions (Gap F)**: `ImprovementInvestigation` survives, stripped of every human-delivery state. Its lifecycle is `draft → deduplicated → policy_checked → running → recorded → interpreted → applied`, with terminal `cancelled`, `superseded`, and `failed`. Kinds are `probe` (run something and observe), `trace_analysis` (read existing execution evidence), `memory_retrieval` (mine Tom-sourced memories), `web_research`, and `resource_acquisition`. Every row stores triggering evidence, the uncertainty it addresses, the decision affected, prior answers checked, expected information value, and its sources with retrieval dates. Interpretation creates scoped claims that keep a pointer to the raw source text.
  - **Memory inspiration intake.** Tom sends ideas as links to Valor. They are already saved as `Memory` rows with `source="human"` at `.claude/hooks/hook_utils/memory_bridge.py:801-836` (`importance=PROVISIONAL_INGEST_IMPORTANCE`, `reference` holding the URL or pointer, per `models/memory.py:158-162`). An observer adapter reads them through `tools/memory_search/__init__.py::search` (`:57`) filtered to `project_key="valor", source="human"`, dedupes by memory id, and writes `ImprovementEvidence` rows of kind `inspiration`. The planner treats those as hypothesis seeds. Tom is not asked to explain them; a link he sent with no commentary is still evidence of what he thinks is worth looking at.
  - **Online research.** `WebSearch` and `WebFetch` on current provider documentation and current practice, recorded with query, source URLs, and retrieval date. A claim about the outside world with no URL and no date is not a claim and cannot enter the system model.
  - **No questions.** The daily question ceiling to Tom is **zero**. There is no preauthorized question class, no attention queue, no poll descriptor change, no `investigation_id` on `tools/ask_poll.py`, and consequently no late-answer race to handle. An uncertainty the system cannot resolve on its own becomes a **provisional assumption**: a recorded claim with its evidence, its confidence, and the observation that would overturn it. Provisional assumptions are listed on the dashboard so Tom can correct one if he wants to, on his own initiative, which is ordinary correction evidence and not a question. The model still bootstraps from the three objectives, the journey-comprehension diagnosis, and the elicitation requirement Tom already supplied.

#### Experiments, evaluation, release, recursion

- **Frozen contract** before candidate construction: hypothesis, mechanism, eligible changes, falsification criteria, primary outcome, minimum worthwhile effect, sample rationale, dev/validation/holdout assignment by task, project, and time grouping, trial count, stopping rule (finite batch or a named alpha-spending function), error treatment, cost ceiling, observation window, evaluator and rubric version, baseline identity. The contract digest is part of every verdict; an altered digest invalidates the verdict.
- **Candidate manifest**: code SHA, skill and prompt content hashes, model IDs and parameters, dependencies, memory snapshot digest, configuration, tool interfaces, environment fixtures, provider revision where available, and the paused-writer list.
- **Paired trials** with randomized ordering and isolated writable state. Candidate failures count as failures; evaluator infrastructure failures invalidate and retry the pair under a predeclared cap. Judges are blinded to arm identity, calibrated against retained human and outcome references, and monitored for disagreement. Holdout inputs, labels, and reference judgments live outside candidate-accessible storage. Trace content is untrusted data.
- **Promotion criteria**: practical gain past the per-endpoint margin, uncertainty evidence with repeated-selection correction (Holm at minimum), no prohibited regression. Small samples yield inconclusive. Rotate holdouts with an untouched final set.
- **Release**: immutable bundle digest activated for newly assigned Jobs; active Jobs stay pinned. Rollout `staged → limited → expanded → accepted` or `reverted`, with stable Job-level exposure. Rollback repoints the default bundle. Human-owned `upvote` labels stay human-owned. Automatic promotion for named reversible surfaces is a later charter amendment, after incident and recovery drills.
- **Recursive boundary**: once the ordinary loop works, planner, context assembly, observation sampling, hypothesis generation, and experiment selection become candidate surfaces, compared on fresh matched opportunities with equal total budgets including evaluation and human time, with the worker model frozen. Evaluator upgrades calibrate separately. Objectives, authority, and budgets remain externally governed.

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
- [ ] Historical `TaskTypeProfile` zeros never render as measured success (anti-criterion in Verification)

## Test Impact

- [ ] `tests/unit/test_autoexperiment.py` — DELETE: tests the removed script; every test imports `scripts.autoexperiment`
- [ ] `tests/unit/test_valor_service_bootstrap.py` — UPDATE if it enumerates the prefix-drift service list; assert `autoexperiment` is absent
- [ ] `tests/unit/test_task_type_profile.py`, `tests/unit/test_session_tags.py`, `tests/integration/test_session_finalize.py` — UPDATE: whichever branch lane 2 takes (wire `rework_triggered` or delete the aggregate), these assert the new truth rather than the constant-zero rate
- [ ] `tests/unit/test_session_lifecycle_consolidation.py`, `tests/unit/test_recovery_ownership.py`, `tests/unit/test_session_health_orphan_process_reap.py`, `tests/unit/test_agent_session_scheduler_kill.py` — UPDATE: `NON_TERMINAL_STATUSES` gains `admitted`; ownership map and enumeration checks include it
- [ ] `tests/unit/test_agent_session_queue.py`, `tests/unit/test_agent_session_queue_async.py` — UPDATE: new `idempotency_key` and `agent_session_id` kwargs; add the lost-race binding case
- [ ] `tests/unit/test_agentsession_index_guard_generalized.py` — UPDATE: extend the runtime `IndexedField` enumeration to the eight new models
- [ ] `tests/unit/test_poll_registry.py` — UPDATE: descriptor carries optional `investigation_id`; existing assertions on descriptor shape are extended
- [ ] `tests/unit/test_settings.py` — UPDATE: `ImprovementSettings` defaults and `IMPROVEMENT__*` overrides
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
**Mitigation:** unresolved uncertainty is recorded as an explicit **provisional assumption** with its evidence, its confidence, and the observation that would overturn it, never as a settled claim; provisional assumptions are rendered on the dashboard where Tom can correct one on his own initiative; the three objectives, the journey-comprehension diagnosis, and the charter's human-owned fields stay externally governed and are never inferred; a correction Tom volunteers is captured as ordinary evidence and supersedes the assumption it contradicts. Observed intervention burden is published beside rescue incidence so the trade is visible.

### Risk 7: Registration clobbered by `/update`
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

- [ORDERED] Automatic promotion of any release. Gated on evaluator secrets and production credentials being separated from candidate execution and on a human-amended charter naming reversible surfaces; both are events outside this plan.
- [ORDERED] Real stakeholder communication (client outreach, meeting attendance, voice calls). Each requires its own charter scope approved by Tom; offline simulations and capability probes are in scope, real deployment is not.
- [EXTERNAL] Shared durable artifact storage for fleet-wide execution. Requires a storage decision and credentials on machines the agent does not administer; local retention root plus export/import is the in-scope substitute.
- [EXTERNAL] Any hand edit of `~/Desktop/Valor/reflections.yaml`. Registration goes through `reflection_register.py`; if an agent-type entry is ever needed, that is a vault edit only Tom performs.
- [EXTERNAL] Placing a new credential for any acquired resource. Resource-acquisition research may find a token source worth adopting, but the system stops at a prepared adapter and a written request naming the vault item. The key is written to `~/Desktop/Valor/.env` by Tom, per the repo's secrets rule. A keyless source may be integrated autonomously behind `ImprovementSettings`, defaulting off. A Verification row asserts no controller module writes to `.env` or shells out to `op`.
- [SEPARATE-SLUG #3183] The idempotent create-or-bind seam on `_push_agent_session`. Owned by #3183 for the production path; #3177 lane 3 consumes it and changes no queue signature. If #3183 has not landed when lane 3 starts, lane 3 blocks.
- [SEPARATE-SLUG #3183] The renewed execution lease with a fencing generation. Owned by #3183; the control journal imports it rather than writing a second lease.
- [SEPARATE-SLUG #3183] The dead-letter record. Owned by #3183 as one generalized model with a `stage` field; exhausted improvement intents write to it with `stage="improvement-intent"`. This plan defines no second sink.
- [DESTRUCTIVE] Deleting `data/experiments/*/eval_*.jsonl`. They are retained as marked-legacy evidence; the "Legacy corpora retained" Verification row asserts all three paths still exist and fails closed if any is missing.
- [DESTRUCTIVE] Lifting or bypassing the child session gate. The research path never passes a parent and never sets `VALOR_ALLOW_CHILD_SESSIONS`. The "no child-gate bypass" Verification row greps trees that exist today (`agent/`, `tools/`, `models/`, `reflections/`) with the one sanctioned definition site excluded, so it bites during lanes 1 and 2 rather than passing on a missing directory.

## Update System

- `scripts/update/run.py` gains a `register_improvement_collector` step calling `reflection_register.py::register_reflection` with the collection tick's callable, cadence, and `project_key="valor"`, idempotent like `register_crash_recovery`.
- `/update` unloads a launchd job only when its label matches `<prefix>.autoexperiment` exactly, on machines where one is installed; a mismatch is logged, never acted on.
- No new dependencies. `ImprovementSettings` defaults to disabled, so no `.env` key is required; the `IMPROVEMENT__ENABLED` override is documented in `.env.example` with `# @optional`.
- Popoto schema: the eight new models are additive; a no-op migration entry in `scripts/update/migrations.py` records their registration so `run_pending_migrations()` has a durable marker for the schema version.
- Fleet execution stays single-machine (the `valor` owner) until shared storage exists.

## Agent Integration

- New CLI entry point `valor-improve = "tools.improvement:main"` in `pyproject.toml [project.scripts]` with subcommands `case show`, `case explain`, `propose`, `release compare`, `pause`, `resume`, `export`, `import`, `replay-projection`. Research sessions reach research state only through this CLI, which enforces journal authorization and never exposes a raw transition.
- The research skill (`.claude/skills/improve-research/SKILL.md`, project-only) instructs the planning session to read a bounded brief, write hypotheses and proposed actions through `valor-improve propose`, and never enqueue sessions or send messages directly.
- The bridge imports nothing new. The poll descriptor gains an optional `investigation_id` that `tools/ask_poll.py` forwards when present in the environment.
- Integration tests: a research session in a test worktree runs `valor-improve propose` end to end and the journal shows the event; a session attempting `valor-session create --parent` on the research path receives the existing gate error.

## Documentation

- [ ] Create `docs/features/improvement-controller.md`: three layers, authorities, records, control namespace contract, dispatch adapter, reservations, memory isolation, claim levels
- [ ] Create `docs/features/improvement-evaluation.md`: contract fields, manifest, judge envelope, statistics, holdout policy
- [ ] Add both rows to `docs/features/README.md`; delete the Autoexperiment row at line 25
- [ ] Delete `docs/features/autoexperiment.md`; correct `docs/research/claude-code-feature-swot.md:413` and `docs/features/nightly-regression-tests.md:371`
- [ ] Update `docs/features/adding-reflection-tasks.md` to name the vault source and `reflection_register.py` as the tracked registration path
- [ ] Update `docs/features/web-ui.md` to remove the stale `ui/routers/` description and document the inline-route plus `ui/data/` pattern
- [ ] Update `docs/features/redis-models.md` with the control-namespace exception and the schema-gate docstring requirement for the eight models
- [ ] Update `docs/features/session-recovery-mechanisms.md` for the `admitted` status and its ownership entry
- [ ] Create `data/experiments/README.md` marking the retained corpora as legacy evidence
- [ ] Update `docs/tools-reference.md` with `valor-improve`

## Success Criteria

- [ ] Lane 1: autoexperiment entry point, installer, plist, tests, and feature doc are gone; `valor-service.sh` no longer enumerates it; retained corpora still exist and are marked legacy
- [ ] Lane 2: the eight flat models exist, are exported, pass the index guard, and carry schema-gate docstrings; `ImprovementSettings` exists; `rework_rate` is either written from a verified event or deleted with its reader; the poll descriptor carries `investigation_id`; the verifying store subclass rejects a corrupted archive; a capability matrix marks each planned component implemented, deployed, measured, or unknown
- [ ] Lane 3: fault-injection tests pass for stale-epoch rejection, crash between admission and creation, unresolved charge on restart, and journal unavailability
- [ ] Lane 4: two arms on private Redis processes produce byte-identical corpus reads; baseline retrieval parity holds on the frozen corpus; a corrupted artifact invalidates evaluation rather than scoring
- [ ] Lane 5: one autonomous hypothesis about journey preservation is tested under a frozen contract with paired blinded evaluation and an accepted-or-rejected verdict with complete lineage; the verdict changes the next selection
- [ ] Dashboard never presents experiment count or merged-patch count as improvement and never renders historical `TaskTypeProfile` zeros as measured success
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `tools/improvement.py` is referenced from `pyproject.toml` and the research skill references `valor-improve propose`

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
  - Role: lane 2, correction detector persistence, `rework_rate` decision, poll descriptor `investigation_id`, verifying store subclass, capability matrix
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
- **Validates**: `tests/unit/test_improvement_models.py` (create), `tests/unit/test_agentsession_index_guard_generalized.py`, `tests/unit/test_settings.py`
- **Informed By**: recon (flat layout, schema-gate docstring, `project_key` partition, bounded recency sort); spike-5 (per-field store)
- **Assigned To**: records-builder
- **Agent Type**: builder
- **Parallel**: true
- Domain: Redis/Popoto data
- Create the eight flat modules with `AutoKeyField` id, `KeyField project_key`, `SortedField created_at partition_by="project_key"`, low-cardinality `IndexedField` state fields only, `Meta.ttl` decisions per record (evidence and investigations follow `ReflectionRun`; case, evaluation, and release are immortal like `Job`)
- Export from `models/__init__.py` after the rebuild interlock
- Add `ImprovementSettings` to `config/settings.py` with `enabled=False`, `max_concurrent_research_sessions=2`, `daily_question_ceiling`, `portfolio_allocation`, `controller_tick_seconds`
- Add the no-op migration marker to `scripts/update/migrations.py`
- Extend the index guard test to enumerate the new models

### 3. Evidence and reuse seams
- **Task ID**: build-seams
- **Depends On**: build-records
- **Validates**: `tests/unit/test_poll_registry.py`, `tests/unit/test_length_safe_content_store.py`, `tests/unit/test_task_type_profile.py`, `tests/unit/test_session_tags.py`, `tests/unit/test_improvement_evidence.py` (create)
- **Informed By**: recon (correction detector transient; `_shipped_evidence` discarded; `rework_triggered` unwritten); spike-5 (archive path unverified)
- **Assigned To**: seams-builder
- **Agent Type**: builder
- **Parallel**: false
- Promote `CORRECTION_PATTERNS` into a persisted detector emitting `SessionEvent` kinds `intervention` and `correction` with a `classification` field (`architectural`, `preference`, `scope`, `clarification`, `unknown`)
- Persist `expectation_reconciler`'s shipped-work evidence and owner liveness as `ImprovementEvidence` rows with cursor and coverage
- Decide and implement the `rework_rate` branch: wire `rework_triggered` from the new `correction` event with classification `architectural`, or delete the aggregate, `failure_stage_distribution`, and `get_delegation_recommendation`
- Add optional `investigation_id` to the poll descriptor and forward it from `tools/ask_poll.py`
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
- Render coverage, active cases, hypotheses, rejected experiments, intervention burden, spend, release lineage; render `paused_budget`, `inconclusive`, and `reconciliation_required` with reason text
- Add `admitted` to `ACTIVE_STATUSES` at `ui/data/sdlc.py:469,1255`

### 5. Validate lanes 1 and 2
- **Task ID**: validate-lanes-1-2
- **Depends On**: build-retire, build-records, build-seams, build-ui
- **Assigned To**: lane-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row; mutation-check each new guard (index guard, verifying store, detector persistence)
- Confirm no `models/improvement/` directory and no `config/improvement.yaml`
- Confirm retained corpora exist

### 6. File child issues for lanes 3 through 6
- **Task ID**: file-children
- **Depends On**: validate-lanes-1-2
- **Assigned To**: seams-builder
- **Agent Type**: builder
- **Parallel**: false
- File four issues via `/do-issue`, each `Refs #3177`, carrying the relevant Technical Approach subsection and the capability matrix rows it depends on:
  - Lane 3, control substrate: control namespace and Lua transition, leases and fencing, dispatch intents with `admitted` status and the `_push_agent_session` create-or-bind seam, reservations and settlement, scheduler adapter, `valor-improve` CLI, export/import, fault-injection suite
  - Lane 4, frozen evaluation inputs: memory corpus export, per-arm private Redis helper for tests, `VALOR_PROJECT_KEY` in `_harness_env`, writer kill switch, isolated retrieval adapter with parity check, judge envelope, `tools/improvement_eval/` statistics with Holm correction and named stopping rules
  - Lane 5, first complete research cycle: observer adapters, system model revisions, planner tick, investigation lifecycle over the poll transport, the journey-preservation experiment under a frozen contract, qualified-result report
  - Lane 6, production promotion and meta-experiments: release records, exposure assignment, rollback, observation windows, incident drills, recursive candidate surfaces with budget-matched comparison

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
- Re-run Verification rows; confirm the four child issues exist and reference #3177; confirm Documentation items landed

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Autoexperiment script gone | `test -e scripts/autoexperiment.py; echo $?` | output contains 1 |
| Installer and plist gone | `ls scripts/install_autoexperiment.sh com.valor.autoexperiment.plist 2>&1 \| grep -c "No such file"` | output > 1 |
| Service enumeration clean | `grep -c autoexperiment scripts/valor-service.sh` | match count == 0 |
| Docs no longer cite it as live | `grep -rc "autoexperiment" docs/features/README.md docs/features/nightly-regression-tests.md docs/research/claude-code-feature-swot.md` | match count == 0 |
| Legacy corpora retained | `ls data/experiments/summarizer/eval_samples.jsonl data/experiments/observer/eval_corpus.jsonl data/experiments/README.md \| wc -l` | output > 2 |
| No models sub-package | `test -d models/improvement; echo $?` | output contains 1 |
| No YAML config | `test -e config/improvement.yaml; echo $?` | output contains 1 |
| Eight flat models exported | `.venv/bin/python -c "import models as m; names=[n for n in m.__all__ if n.startswith('Improvement')]; print(len(names))"` | output > 7 |
| Settings block present | `grep -c "class ImprovementSettings" config/settings.py` | output > 0 |
| Index guard covers new models | `scripts/pytest-clean.sh tests/unit/test_agentsession_index_guard_generalized.py tests/unit/test_improvement_models.py -q` | exit code 0 |
| Verifying store rejects corrupted archive | `scripts/pytest-clean.sh tests/unit/test_length_safe_content_store.py -q -k verifying` | exit code 0 |
| Poll descriptor carries investigation_id | `grep -c "investigation_id" bridge/poll_registry.py tools/ask_poll.py` | output > 1 |
| Correction detector persists events | `scripts/pytest-clean.sh tests/unit/test_improvement_evidence.py -q` | exit code 0 |
| rework_rate no longer constant zero | `grep -rn "rework_triggered" agent/ reflections/ models/ \| grep -v "task_type_profile.py\|session_tags.py" \| wc -l` | output > 0 |
| Dashboard partials render | `scripts/pytest-clean.sh tests/unit/test_ui_app.py -q -k improvement` | exit code 0 |
| Anti-criterion: no child-gate bypass on research path | `grep -rc "VALOR_ALLOW_CHILD_SESSIONS" agent/improvement_*.py tools/improvement.py 2>/dev/null \|\| echo 0` | match count == 0 |
| Anti-criterion: no raw Redis on Popoto keys | `grep -rcE "POPOTO_REDIS_DB\.(hset\|hdel\|sadd\|srem\|zadd\|zrem\|delete)\(" models/improvement_*.py` | match count == 0 |
| Anti-criterion: historical zeros never render as success | `grep -c "rework_rate" ui/templates/improvement/*.html \|\| echo 0` | match count == 0 |
| Format clean | `.venv/bin/python -m ruff format --check .` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check .` | exit code 0 |

## Critique Results

War room, FULL depth, independent roster (3 critics) plus automated structural checks. Verdict: NEEDS REVISION.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Gap C treats adding `admitted` to `RECOVERY_OWNERSHIP` as establishing recovery, but that dict's own header at `models/session_lifecycle.py:88` says it "is an informational constant; it is not used for runtime routing". No real recovery pass watches `admitted` or `materialized`, and Gap A's namespace-unreachable pause names no break-glass path, so a crash mid-admission or an outage outliving the restart-triggered reconcile strands the intent and its reservation with nothing watching it. | pending | Add a scheduled reflection tick (same `scripts/update/reflection_register.py` seam as the controller ticks) that scans `improve:{project}:*:intent:*` for `state in {admitted, materialized}` past a lease-derived staleness threshold, CAS-transitions the journal entry to `reconciliation_required`, and calls the existing `finalize_session()` (`models/session_lifecycle.py:233`) to force the orphaned row terminal. Add a `valor-improve` subcommand that manually clears a paused journal head. |
| BLOCKER | History & Consistency | The Verification row "rework_rate no longer constant zero" is vacuously true on main today. Its grep already returns 1 before any lane-2 work, matching `models/agent_session.py:221` (`rework_triggered = Field(null=True)`), a pre-existing dead field the exclusion filter never strips. The row cannot distinguish wired from unwired under either branch of Open Question 1, so the anti-criterion has no working detector behind it. | pending | Grep for a write site, not a substring: `grep -rn "\.rework_triggered\s*=" agent/ reflections/` for the wire branch, and add a separate delete-branch row asserting the aggregate is gone (`python -c "import models; assert not hasattr(models, 'TaskTypeProfile')"`). Exclude `models/agent_session.py` explicitly from any substring grep. Note `session_tags.py` lives at `tools/session_tags.py`, outside the three searched roots, so that exclusion term matches nothing. |
| CONCERN | Risk & Robustness | Gap D says "Settlement reads `AgentSession.total_cost_usd` after completion" without naming which termination path fires it. `finalize_session()` is reached from the Stop hook, `agent/session_health.py`, `worker/__main__.py`, and `tools/agent_session_scheduler.py`. If settlement is wired only to the ordinary completion path, a watchdog- or crash-recovery-finalized session never settles and its reservation stays outstanding forever, quietly eating the `settled + outstanding + requested_max <= limit` budget Gap D exists to protect. | pending | Put the settlement transition inside `models/session_lifecycle.py::finalize_session` (line 233) as a post-transition side effect gated on the session carrying reservation/action-ID provenance, so it fires uniformly regardless of which caller terminates the session. |
| CONCERN | History & Consistency, structural | Gap A's claim "It is not Popoto-managed, so the raw-Redis guard does not apply" overstates a namespace exemption that does not exist. `find_violation()` in `.claude/hooks/validators/validate_no_raw_redis_delete.py` fires on a Bash command whose text contains both a `_POPOTO_CONTEXT` substring (including the bare literals `POPOTO_REDIS_DB`, `popoto`, `Job`, `AgentSession`) and a block pattern such as `\br\.delete\(`. It has no awareness of the target key. Gap A's own cited precedent obtains its handle as `from popoto.redis_db import POPOTO_REDIS_DB as _R` (`models/session_lifecycle.py:853`) and Gap A requires compare-and-delete on release, so a manual debug one-liner on a plain `improve:*` key will be blocked. | pending | Bind the control journal's client under a private alias (for example `_journal_redis`) rather than importing `POPOTO_REDIS_DB` into the same file, so ad hoc verification commands do not match `_POPOTO_CONTEXT`. Restate the exemption as "the guard is a Bash-command text heuristic with no namespace awareness", and verify the delete/release path through a pytest file rather than an inline `python -c`. The Lua CAS precedent itself is fair: `models/session_lifecycle.py:1357` is a real `_R.eval` on a non-Popoto key, and the comment at `:884` states that exemption explicitly. |
| CONCERN | History & Consistency | `scripts/sdlc_reflection.py` is named in "Why Previous Fixes Failed" with the root cause "extraction with zero behavioral validation", which is exactly the failure mode this plan's evaluation loop exists to fix. The string appears once in the whole 662-line plan, in that table row, and never in Prior Art, the Reuse map, the task list, or No-Gos. The script stays in production scraping lessons unvalidated, in parallel with the system meant to replace that pattern. | pending | Minimal fix is a No-Gos row in the plan's own named-rationale style: "[DEFERRED] `scripts/sdlc_reflection.py`'s lesson extraction. Its unvalidated-lesson failure mode is diagnosed but not fixed by lanes 1-2; folding it into the observer and evaluation loop is lane-5 scope once the evidence substrate exists." Alternatively add a lane-5 task routing its extraction through an `ImprovementEvidence` observer adapter. |
| CONCERN | structural | Gap C says the queue seam, on a lost NX race, "returns the existing row via `AgentSession.query.get(id=...)`". `_push_agent_session` has exactly one return statement, `return await AgentSession.query.async_count(chat_id=chat_id, status="pending")` at `agent/agent_session_queue.py:460`, and its declared return type is `int` (queue depth). A function returning queue depth cannot return a row, so the create-or-bind contract is unimplementable as written. Separately, `async_create` at `:369` hardcodes `status="pending"`, but the seam list names only `idempotency_key` and `agent_session_id` as new parameters, never a status parameter, while Gap C requires `status="admitted"`. | pending | Either change the seam's contract to return `tuple[int, str]` (queue depth plus the bound `agent_session_id`) and update all callers, or have the adapter read the bound id back from the NX key it just lost rather than from the return value. Add `status: str = "pending"` to the `_push_agent_session` signature and thread it into the `async_create(...)` call at `agent/agent_session_queue.py:369`, which currently passes the literal. |
| CONCERN | structural | Spike-1 and Gap C cite the dashboard tuple as `ACTIVE_STATUSES` at `ui/data/sdlc.py:469,1255`, but `ACTIVE_STATUSES` is defined only once, at `:1255`. Line 469 is an unrelated inline literal inside the `is_active` property: `return self.status in ("pending", "running", "active", "waiting_for_children")`. A builder who greps for `ACTIVE_STATUSES` finds one site and silently leaves the second unpatched, so `admitted` sessions read as inactive on that code path. | pending | Name the two sites by what they are: the module constant `ACTIVE_STATUSES` at `ui/data/sdlc.py:1255`, and the inline tuple in the `is_active` property at `ui/data/sdlc.py:469`. Better, have the property read the constant so there is one place to edit. |
| CONCERN | structural | Success Criteria include rows for lanes 3, 4, and 5, plus a final row requiring `grep` to confirm `tools/improvement.py` is referenced from `pyproject.toml` and that the research skill references `valor-improve propose`. None of those have a task in this plan's Step by Step Tasks, which cover lanes 1 and 2 only; the CLI and the skill are lane-3 scope per task 6. A `/do-build` reading Success Criteria as its definition of done cannot satisfy them and will either fail or invent lane-3 work. | pending | Split the Success Criteria section into "This build (lanes 1-2)" and "Carried to child issues", and move the lane 3/4/5 rows plus the `tools/improvement.py` and `valor-improve propose` grep row under the latter, so the build's exit condition matches its task list. |
| CONCERN | structural | The `[DESTRUCTIVE]` No-Go "Lifting or bypassing the child session gate" points at the Verification row `grep -rc "VALOR_ALLOW_CHILD_SESSIONS" agent/improvement_*.py tools/improvement.py 2>/dev/null \|\| echo 0`. After lanes 1 and 2, neither `agent/improvement_*.py` nor `tools/improvement.py` exists, so grep errors, stderr is discarded, and the fallback prints 0. The anti-criterion passes without inspecting a single line and never bites. | pending | Scope the grep to the tree that exists in this build and fail closed on an empty target set, for example `grep -rn "VALOR_ALLOW_CHILD_SESSIONS" agent/ tools/ models/ \| grep -v "models/child_session_gate.py" \| wc -l` expecting 0, so a new occurrence anywhere in the controller surface is caught and a missing directory cannot masquerade as a pass. |
| CONCERN | Scope & Value | Task 4 renders coverage, active cases, hypotheses, rejected experiments, intervention burden, spend, release lineage, plus the `paused_budget`, `inconclusive`, and `reconciliation_required` states. Only `ImprovementEvidence` gets a writer in this build. `ImprovementCase`, `ImprovementInvestigation`, `ImprovementExperiment`, `ImprovementEvaluation`, and `ImprovementRelease` have no writer until lanes 3, 5, or 6, which are child issues with no committed timeline, so six of nine render targets show permanent empty state. | pending | Trim task 4 to the `ui/data/improvement.py` coverage and intervention-burden partial, the one view backed by data this build writes, and move the rest into the child issue for the lane that first writes it. This fails no Verification row: the dashboard row only requires `scripts/pytest-clean.sh tests/unit/test_ui_app.py -q -k improvement` to pass, and the Lane 2 success row never requires populated content. |
| CONCERN | Scope & Value | Lane 2's Success Criteria are entirely structural (models exist and export, index guard passes, docstrings present, settings exist, poll field added, store subclass rejects a corrupted archive). Nothing ties to Tom's three stated objectives or even to the correction-evidence capture that is this lane's actual user-visible value, so a reviewer checking the row off cannot tell whether the detector is running against real sessions. | pending | Add one checkable criterion to the Lane 2 row: a real correction or intervention from ordinary use appears as a persisted `ImprovementEvidence` row and is visible in the trimmed dashboard partial. Gate at `ImprovementEvidence.query.filter(project_key="valor").count() >= 0` plus a render smoke test at merge time, and graduate to `count() > 0` after a week of normal use, since no correction may have occurred yet on merge day. |
| NIT | structural | Spike-4 and Gap E cite `hook_utils/memory_bridge.py` five times. No such path exists; the real file is `.claude/hooks/hook_utils/memory_bridge.py`. | pending | n/a |
| NIT | structural | Three Verification rows use `grep -c` against multiple file arguments or a glob ("Docs no longer cite it as live", "no raw Redis on Popoto keys", "historical zeros never render as success"). With more than one file, `grep -c` prints one `file:count` line per file, so the "match count == 0" expectation is not a scalar an automated checker can compare. | pending | n/a |
| NIT | Scope & Value | Only `ImprovementEvidence` gets a writer in this build; the other seven models are exported schema with no instances. Batching all eight is defensible, since it avoids fragmenting the index-guard update and migration marker across five later PRs, but the plan never says so, leaving a reviewer to reverse-engineer which models are live. | pending | n/a |

---

## Open Questions

1. **`rework_rate` branch.** Wire `rework_triggered` from the new architectural-correction event, or delete the aggregate and its dead reader? Default if unanswered: delete, and derive rework from `ImprovementEvidence` directly. Reversible either way.
2. **Daily question ceiling.** The charter needs an initial number for research questions to Tom. Default if unanswered: 3 per day, batched at one delivery time.
3. **Experiment spend ceiling for lane 5.** The first journey-preservation experiment needs a maximum authorized spend for the controller and a separate one for the evaluator. Default if unanswered: $25 controller, $25 evaluator, per experiment.
