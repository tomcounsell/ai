---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-14
baseline_commit: 89f8008766e34867ae299d5a86e02ba127c6bdbb
tracking: https://github.com/tomcounsell/ai/issues/3215
last_comment_id: IC_kwDOEYGa088AAAABTgOFNQ
parent_plan: docs/plans/recursive-self-improvement.md
charter: docs/improvement-charter.md
charter_version: 2
---

# Improvement controller lane 3: control journal, fenced dispatch, and the valor-improve CLI

## Problem

Lanes 1, 2, 2b, and 7 built an observer with a budget: `ImprovementEvidence` rows are collected every fifteen minutes, the charter is pinned by digest, unit 3 is metered, and the dashboard shows what was seen. Nothing decides anything, because there is no place to hold a decision safely. The eight `Improvement*` Popoto records are a projection with no authority behind them: no head revision, no lease, no fencing generation, no reservation, and no dispatch record that survives a crash between "we decided to run this" and "a session exists". A projection with no journal behind it is a set of rows anyone can `save()` and nobody can trust, and lane 4's verdicts, lane 5's first research cycle, and lane 6's releases all inherit that weakness.

**Current behavior:**

- `ImprovementCase.state` (`models/improvement_case.py:116`) is a plain `IndexedField` any caller can overwrite; `revision` (`:119`) is an `IntField` nothing increments. Two ticks that both decide to dispatch a case both succeed.
- No research session has ever been dispatched. The only path that creates a top-level session from a reflection is `agent/reflection_scheduler.py:810`, keyed by a due window; nothing carries `research_case_id`, `experiment_id`, or `action_id`.
- A lane slot does not exist. `ImprovementSettings.max_concurrent_research_sessions` (`config/settings.py:610`) is a declared limit with no reservation behind it. `daily_paid_inference_usd` (`:622`) is the same: `tools/infrastructure_budget.py` meters unit 3, and nothing meters unit 2 (`docs/features/improvement-controller.md:182-185`).
- `valor-improve` is documented as planned (`docs/tools-reference.md:345`) and does not exist. `tools/improvement_resources.py::_probe_vault_write` (`:198`) reports the vault writer `absent`, so lane 7's acquisition tasks and any credential-issuing signup have nowhere to put a secret.
- The `admitted` session status does not exist. A session created for research today would be created `pending` and picked up by the worker immediately, before a liveness check, a reservation, or a journal event says it should run.

**Desired outcome:**

A control journal in its own Redis namespace that is the single authority for what a case is doing; a lease with a fencing generation that every effect boundary re-checks inside the same script call that records the effect; durable dispatch intents that reconcile by action id after a crash; an `admitted` status that lets a session exist without running; a lane-slot reservation released on every termination path; a unit-2 meter that settles paid inference per call or marks it estimated; a sanctioned vault writer; a reconciliation reflection on its own cadence; and the `valor-improve` CLI as the only door through which a research session proposes anything. The four fault-injection tests in the issue's acceptance criteria are the definition of "safely": stale-generation rejection, crash between admission and creation, an unreleased slot on restart, and journal unavailability with a documented break-glass recovery.

## Freshness Check

**Baseline commit:** `89f8008766e34867ae299d5a86e02ba127c6bdbb`
**Issue filed at:** 2026-09-07T04:44:42Z; dependency refreshes 2026-09-08T08:13Z and 2026-09-09T14:47Z
**Disposition:** **Minor drift.** Every claim in the body still holds; three sibling changes since the last refresh shift file:line pointers and add one shipped neighbor (lane 7) whose expectations of this lane are recorded and, in one case, revised.

**File:line references re-verified (by symbol on the baseline):**
- `agent/agent_session_queue.py:233` (`idempotency_key`) — drifted to `:246`; `status: str = "pending"` at `:247`; `-> tuple[int, str]` at `:250`; the bind and lost-race return at `:428-441`. Claim holds.
- `models/dead_letter.py:33` (`DeadLetter`) — holds; `stage = IndexedField(default="telegram_send")` at `:60`; `finalize_session(dead_letter_stage=...)` at `models/session_lifecycle.py:296` writes one through `_record_terminal_dead_letter` (`:235`).
- `models/session_lifecycle.py` `NON_TERMINAL_STATUSES` `:74`, `RECOVERY_OWNERSHIP` `:92` (informational header `:90`), `finalize_session` `:286`, issue-lease compare-and-delete release inside finalize at `:722-748` — hold. The raw-Redis exemption comment moved `:942` → `:1000`; the Lua CAS `_R.eval` is at `:1473`, the compare-and-delete release at `:1628`.
- `ui/data/sdlc.py:32` (`ACTIVE_STATUSES`) and `is_active` reading it at `:488` — hold.
- `tests/unit/test_recovery_ownership.py:16` (set equality) — holds; `:33` pins `known_owners = {"worker", "bridge-watchdog", "none", "human"}`, which is why `admitted`'s owner value is a Test Impact item.
- `agent/session_health.py` `WORKER_REGISTERED_PID_KEY_PREFIX` `:216` → `:217`; `register_worker_pid` `:5269` → `:5319`; `_worker_pid_heartbeat_fresh` `:5393` → `:5443`; the `scan_iter` `:6463` → `:6513`. Claims hold; no public liveness helper exists.
- `scripts/update/reflection_register.py::register_improvement_collect` at `:625` with `cadence="900s"` — holds; `register_reflection` at `:484`.
- `models/improvement_case.py` `priority_area` `:118`, `charter_digest` `:126`, `ranking_rationale` `:127` — hold (lane 2b landed as PR #3275, `aff4d7e2e`).
- `tools/improvement_eligibility.py::is_open_source` `:83` with the process-local `_CACHE` `:52` and `_clear_cache` `:55` — holds.

**Cited sibling issues/PRs re-checked:**
- #3183 — closed 2026-09-07T16:24Z; PR #3229 merged (`a9822d719`). Seam and dead letter consumed as-is.
- #3220 — **open, no branch, no PR.** Declares the lease interface this plan's protocol matches. See Technical Approach, decision 1.
- #3255 — closed 2026-09-09T19:47Z; PR #3275 merged. Charter v2 vocabulary and settings are on `main`.
- #3274 (lane 7) — closed; PR #3299 merged 2026-09-14 (`37dc10b33`). **Not cited by the issue and not in either refresh.** It added `tools/infrastructure_budget.py` (unit-3 meter on `improvement:budget:unit3:{window_key}`), `models/improvement_infrastructure_ledger.py`, `tools/improvement_operating_report.py`, the `spend_receipt` and `resource_probe` evidence kinds with an ownership note reserving `resource_acquired` for this lane (`models/improvement_evidence.py:59`), and the export destination contract in `docs/infra/improvement-cloud-execution.md:233-254`. Its plan expected this lane to migrate the unit-3 counter into the control namespace; this plan declines (No-Gos) and says why.
- #3216 (lane 4) — open, building on `session/sdlc-3216`; its diff touches `docs/features/improvement-controller.md`, `docs/features/README.md`, `scripts/update/migrations.py`, `models/improvement_evaluation.py`, `agent/session_executor.py`, `tests/unit/test_improvement_models.py`. Shared with this lane: the feature doc and `test_improvement_models.py` (both additive; the second lane to land rebases).
- #3217 (lane 5) and #3218 (lane 6) — open, branches at main's tip with no commits. Lane 5's body names the dashboard renderings of cases, hypotheses, and rejected experiments as its own, so `ui/data/improvement.py` is shared with it.
- #2731 — closed; its finding (ownership leases cannot detect intra-run collisions) is why the admit script compares `expected_revision`, not just the lease.

**Commits on main since issue was filed touching referenced files:** `aff4d7e2e` (lane 2b: settings, case fields, eligibility) — partially addresses, consumed; `37dc10b33` (lane 7) — adjacent, consumed; `705e8df7d` and `16c8ce696` (executor finalize guard) — irrelevant to this lane's `finalize_session` hook, which is a post-transition side effect; `06ccc8d50` (shadow-append recovery) — irrelevant.

**Active plans in `docs/plans/` overlapping this area:** `recursive-self-improvement.md` (parent, `status: Planning`, its lane-3 section is this plan's source); `improvement-controller-lane-4-frozen-evaluation-inputs.md` (lane 4, in build; overlap limited to the two shared files above). `sdlc-control-plane-asserted-facts.md` does not overlap.

## Prior Art

- **#3183 / PR #3229**: ETL-grade pipeline hardening. Shipped the create-or-bind seam (`agent/enqueue_idempotency.py`, `_push_agent_session(idempotency_key=, status=)`), the generalized `DeadLetter`, and the queue lock policy. Consumed whole; nothing here re-implements it.
- **#3220**: session execution lease, split out of #3183 because it ships behind a shadow-release flag over two releases. Open. Its declared interface is the contract this plan's `LeaseProtocol` matches.
- **#2731**: stage liveness gate. Proved an ownership lease cannot detect an intra-run collision; the journal's `expected_revision` compare and action-id fencing are designed around it.
- **PR #2706**: "Reach lease helpers through the module, closing the #2469 freeze class". Established that lease helpers are called through their module (monkeypatchable), not bound at import; the adapter follows it.
- **#1312 / PR #2196**: bridge reacts when no worker is alive. The `worker:registered_pid:*` convention is the liveness signal the scheduler adapter reads before activating an intent.
- **#1633**: merged PM/Dev roles and kept the child session gate. Research sessions are top-level with no parent, so the gate is never engaged.
- **Lane 7 (#3274 / PR #3299)**: `tools/infrastructure_budget.py` is the shape unit 2 mirrors: `current_window`, reserve-then-check in one Lua `EVAL` on a plain key, paired idempotent release, `spend_receipt` rows for settlement, window key and boundaries disclosed on every decision.
- **`models/session_lifecycle.py:1000-1628`**: the issue-lock Lua CAS (`touch_issue_lock`, `release_issue_lock`) is the in-repo precedent for compare-and-delete on a non-Popoto key.

## Research

**Queries used:**
- OpenRouter usage accounting `usage.include` response cost field per request
- Redis Lua script TIME command allowed effects replication fencing token lease EVAL

**Key findings:**
- OpenRouter now includes `usage.cost` (and `usage.cost_details`) in every response; the `usage: {include: true}` request flag is deprecated and has no effect. Cost cannot be reliably rebuilt from tokens times list price, because one model id routes to different upstreams with cache discounts. Source: [OpenRouter usage accounting](https://openrouter.ai/docs/guides/guides/administration/usage-accounting). Informs `tools/paid_inference_meter.py`: settle from `response.usage.cost` when present, fall back to `GET /generation?id=` by generation id, and only then estimate from a dated price table marked `metering="estimated"`. The parent plan's `extra_body={"usage": {"include": True}}` instruction is dropped.
- Redis 5 and later replicate Lua scripts by effects, so `TIME` is allowed inside a writing `EVAL`; time is frozen for the script's duration, so one `TIME` call is the script's consistent clock. A fencing generation should come from `INCR` inside the same script, never from the clock, and `EVAL` is never read-only (use `EVAL_RO` for pure reads). Source: [Redis scripting with Lua](https://redis.io/docs/latest/develop/programmability/eval-intro/). Informs the lease script (generation from `INCR` on `improve:{p}:{c}:lease:gen`, expiry from `TIME`) and the transition script (one `TIME` read for `updated_at` and the journal `ts`).
- Carried from the parent plan's research: the accept rule at effect boundaries is `generation >= highest_accepted`; release is compare-and-delete. Sources: [Redisson FencedLock](https://redisson.pro/glossary/java-fencedlock.html), [Design a Distributed Lock Service](https://dev.to/gabrielanhaia/design-a-distributed-lock-service-fencing-tokens-and-the-failure-modes-29nl).

## Data Flow

1. **Entry point (proposal)**: a research session (dispatched by this lane, briefed by lane 5) runs `valor-improve propose --case ID --action-type investigate --payload FILE`. `tools/improvement.py` validates the case's `priority_area`, non-empty `ranking_rationale`, and `charter_digest == ImprovementCharter.pinned("valor").digest`, refusing with a reason code otherwise.
2. **Artifact first**: the payload is written to `VerifyingArtifactStore` under `POPOTO_IMPROVEMENT_CONTENT_PATH` and hashed; the digest is what the journal will reference.
3. **Lease**: `lease.acquire(f"improve:valor:{case}:lease", ttl=lease_ttl_seconds)` returns a generation or `None` (case busy). The CLI holds it only for the write.
4. **Journal**: `journal.transition(case, expected_revision=head.revision, generation, action_id, event="action_proposed", payload_digest)` runs one Lua script: schema check, pause check, `generation >= highest_accepted`, `expected_revision == revision`, then head advance and `RPUSH`+`LTRIM` on `journal`. Returns `TransitionResult(accepted, reason, revision)`.
5. **Projection**: on `accepted`, `projection.apply(case)` reads the head and `save()`s `ImprovementCase.state` and `revision` through the ORM. The projection is never read for a decision.
6. **Scheduler adapter (function reflection `improvement-controller-tick`, this lane's dispatch half)**: for each case whose head carries an unadmitted `action_proposed` of an allowed type, `intents.admit(...)` runs one Lua script that checks the slot count (`HLEN slots + 1 <= max_concurrent_research_sessions`), the revision, and the generation, then writes `intent:{action_id}` in state `admitted` and reserves the slot under the action id.
7. **Materialize**: `_push_agent_session(idempotency_key=f"improve:valor:{case}:{action_id}", status="admitted", extra_context_overrides={research_case_id, experiment_id, action_id, generation}, ...)` returns `(depth, agent_session_id)`; the intent moves to `materialized` with the bound id. A retry after a crash passes the same key and gets the same id.
8. **Activate**: `agent.session_health.any_worker_alive()` must be true; then `transition_status(session, "pending")` and the intent moves to `running`. With no live worker the intent stays `materialized` and the reason is journaled.
9. **Worker**: picks up `pending` as today. The session's prompt carries the case brief and the research skill; the session writes results only through `valor-improve propose` (step 1), which now carries `--action-id` and the session's generation from `extra_context`; a stale generation's artifact is stored as evidence and its transition refused.
10. **Terminal**: `finalize_session` step 7 calls `intents.on_session_terminal(session, status)`: one Lua script releases the slot held by `action_id` (compare-and-delete on the hash field) and, if a result artifact was recorded, moves the intent to `settled`; otherwise it stamps `session_terminal_at` and leaves the intent `running` as incomplete research.
11. **Recovery (function reflection `improvement-intent-reconcile`, 300s)**: scans `improve:valor:*:intent:*`; intents in `admitted` or `materialized` older than `4 * lease_ttl_seconds` are CAS-moved to `reconciliation_required`, their slot released, and any bound `AgentSession` row forced terminal through `finalize_session(status="abandoned", dead_letter_stage="improvement-intent")` when `attempts >= max_dispatch_attempts`. Unit-2 reservations whose day window closed without settlement are released and receipted `metering="unknown"`.
12. **Output**: `valor-improve doctor`, `case show`, `budget`, and the dashboard partial read the head, intents, slots, and the unit-2 counter, never the projection.

## Architectural Impact

- **New dependencies**: none third-party. New package `tools/improvement_control/` (journal, lease, intents, scheduler adapter, projection, recovery, export) plus `tools/paid_inference_meter.py`, `tools/vault_write.py`, `tools/improvement.py`, `reflections/improvement_intent_reconcile.py`.
- **Interface changes**: `NON_TERMINAL_STATUSES` and `RECOVERY_OWNERSHIP` gain `admitted` (owner value `reflection`, a new owner vocabulary entry); `ACTIVE_STATUSES` gains `admitted`; `EVIDENCE_KINDS` gains `resource_acquired`; `ImprovementSettings` gains `lease_ttl_seconds`, `journal_max_entries`, `max_dispatch_attempts`; `agent/session_health.py` gains the public `any_worker_alive()`; `finalize_session` gains one post-transition side effect (step 7). No queue signature changes; `_push_agent_session` is called with arguments it already accepts.
- **Coupling**: `models/session_lifecycle.py` gains a lazy import of `tools.improvement_control.intents` inside a `try/except` block, the same shape as its `session_archive` and `supervised_run` imports. The control package imports `utils.redis_client.text_redis` and `models.*` records; nothing in `agent/` imports the control package except the liveness helper's caller.
- **Data ownership**: the `improve:{project}:{case}:*` namespace is owned by `tools/improvement_control/` and is the authority for case state; `ImprovementCase.state`/`revision` become a projection written only by `projection.apply` and `replay-projection`. Unit 3 stays owned by `tools/infrastructure_budget.py` on its own key. Credentials stay owned by the vault; `tools/vault_write.py` is the only improvement-path module that invokes `op`.
- **Reversibility**: the namespace is additive and separate; deleting the package and the `admitted` status returns the system to lane 2b's observer with no data migration. The interim lease is designed to be deleted (No-Gos, #3220).

## Appetite

**Size:** Large

**Team:** lead orchestrator, four builders in one worktree with a declared file split, one test engineer, one validator, one documentarian.

**Interactions:**
- PM check-ins: 1-2 (the lease decision and the unit-3 counter decision are recorded here for critique; no human question is expected during build)
- Review rounds: 2+ (the mutation-review round on the four fault-injection tests is the one that matters)

Sequencing inside the lane: substrate first (journal, lease, intents, status trio), then dispatch and recovery, then meter and vault writer, then CLI and skill, then dashboard and docs. The CLI is last because every subcommand is a thin reader or writer over the substrate.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Create-or-bind seam on `main` | `grep -c "idempotency_key: str | None = None" agent/agent_session_queue.py` | Dispatch materializes through it; output > 0 |
| `DeadLetter.stage` on `main` | `grep -c 'stage = IndexedField' models/dead_letter.py` | Exhausted intents write `stage="improvement-intent"`; output > 0 |
| Charter v2 vocabulary on `main` | `grep -c "charter_digest = Field" models/improvement_case.py` | `propose` refuses against it; output > 0 |
| Unit-3 meter on `main` | `test -f tools/infrastructure_budget.py` | `valor-improve budget` reads it; exit code 0 |
| Redis 5+ (effects replication) | `redis-cli INFO server \| grep -E '^redis_version:[5-9]\|^redis_version:[1-9][0-9]'` | `TIME` inside a writing `EVAL`; exit code 0 |
| `#3220` still open at build start | `gh issue view 3220 --json state -q .state` | If `CLOSED`, delete the interim lease before building anything else (Task 2); output contains OPEN or CLOSED, either is fine, the builder branches on it |
| `op` non-interactive auth (vault writer integration test only) | `OP_CACHE=false op whoami` | `test_vault_write_integration` skips with a named reason when this fails; no build step depends on it |

Run all checks via `python scripts/check_prerequisites.py docs/plans/improvement-controller-lane-3-control-journal-fenced-dispatch.md`.

## Solution

### Key Elements

- **Control journal** (`tools/improvement_control/journal.py`): the case head and its bounded journal in `improve:{project}:{case}:*`, advanced only by one Lua `transition` that verifies schema, pause, generation, and revision before it writes. Rejections are reason codes.
- **Lease protocol and interim lease** (`tools/improvement_control/lease.py`): `acquire`/`renew`/`release` with a monotonic generation, matching #3220's declared interface, implemented for `improve:*` keys only until #3220 replaces it.
- **Dispatch intents and reservations** (`tools/improvement_control/intents.py`): the durable record between "decided" and "a session exists", the lane-slot hash, and the terminal hook `finalize_session` calls.
- **Scheduler adapter** (`tools/improvement_control/scheduler_adapter.py` behind the `improvement-controller-tick` reflection): admits, materializes through the create-or-bind seam with `status="admitted"`, checks worker liveness, activates.
- **`admitted` session status**: `NON_TERMINAL_STATUSES`, `RECOVERY_OWNERSHIP`, and `ACTIVE_STATUSES` in one change; invisible to the worker, the health check, startup recovery, and the resume drip.
- **Recovery** (`reflections/improvement_intent_reconcile.py`): reads intents, not a status map; CAS to `reconciliation_required`, slot release, forced-terminal row, dead letter on exhaustion; also closes stale unit-2 reservations.
- **Projection and replay** (`tools/improvement_control/projection.py`): ORM `save()` after the journal commits; `replay-projection` rebuilds any case from its journal.
- **Unit-2 meter** (`tools/paid_inference_meter.py`): reserve-then-check on `improve:{project}:budget:unit2:{day_key}`, settle from `usage.cost`, estimate with a dated table otherwise, receipts as `spend_receipt` evidence with `metering` and `purpose`.
- **Vault writer** (`tools/vault_write.py`): the one sanctioned `op item create` path; never returns, logs, or echoes the value; writes a `resource_acquired` evidence row with the item title and SHA-256 fingerprint; ships the digest section renderer.
- **`valor-improve` CLI** (`tools/improvement.py`) and the **research skill** (`.claude/skills/improve-research/SKILL.md`): the only door for a research session; break-glass for an operator.
- **Export/import** (`tools/improvement_control/export.py`): dump and restore the namespace and artifact manifests against lane 7's destination contract.
- **Dashboard control panel** (`ui/data/improvement.py::get_control_status`, `ui/templates/improvement/control.html`): intents, reservations, unit-2 spend, paused and `reconciliation_required` states, with the three-state empty/unavailable rendering lane 2 established.

### Flow

**Case at `investigating`** → planner (lane 5) or operator writes a brief → **research session** (dispatched by this lane's adapter, status `admitted` → `pending` → running) → `valor-improve propose` → **journal event `action_proposed`** → adapter tick admits (slot reserved, intent `admitted`) → seam creates the next session `admitted` (intent `materialized`) → liveness check passes → session `pending` (intent `running`) → session finishes → `finalize_session` step 7 releases the slot (intent `settled` or incomplete) → **head advanced, projection updated, dashboard shows it**.

**Operator, wedged case**: `valor-improve doctor` → sees one paused head with an outstanding reservation → `case show --case ID` → reads the journal tail → `resume --case ID` (refused while intents are `reconciliation_required`, unless `--force`) → head advanced with event `resumed`.

**Operator, namespace outage**: `doctor` prints `namespace unreachable: <error>` and nothing else → fix Redis → `doctor` again → every head reads as it was; nothing was written from the projection.

### Technical Approach

#### Decision 1: the lease without #3220

#3220 owns `models/redis_lease.py` and is open with no branch. The issue says this lane "blocks on the lease specifically". Waiting means lanes 4, 5, and 6 wait too, because every one of them dispatches through this lane. Forking a second general lease is what #3220 forbids. The choice made here:

- `tools/improvement_control/lease.py` defines `LeaseProtocol` (a `typing.Protocol`) with exactly #3220's declared calls: `acquire(key: str, ttl: int) -> int | None`, `renew(key: str, generation: int) -> bool`, `release(key: str, generation: int) -> bool`. The journal, the adapter, and the CLI take a `LeaseProtocol` and default to `default_lease()`, a module-level factory that is the single swap point.
- The same module ships `CaseLease`, the interim implementation: three Lua scripts on `utils.redis_client.text_redis()`, generation from `INCR improve:{p}:{c}:lease:gen` inside the acquire script, expiry from server `TIME`, `renew` and `release` compare the stored generation (compare-and-delete). Keys are restricted to the `improve:` prefix by an assertion in every call, so it can never be pointed at `lease:session:*`.
- **The accept rule lives in the journal, not the lease.** `transition` and every effect script compare `generation >= head.highest_accepted` and record `highest_accepted = generation` on acceptance. That is the rule this lane asserts in its own tests, and it is independent of which lease minted the generation.
- **Hand-off contract, tested**: `tests/unit/test_improvement_control_lease.py` runs the protocol conformance suite (acquire returns increasing generations; a lapsed lease can be re-acquired with a higher generation; `renew` and `release` with a stale generation return False and change nothing; the holder's own second write is accepted) against `CaseLease`, and `test_interim_lease_retired_when_redis_lease_exists` asserts `not Path("models/redis_lease.py").exists()`. When #3220 lands, its builder deletes `CaseLease`, points `default_lease()` at `models.redis_lease`, and reruns the same conformance suite. The parent's "no second lease in the `improve:*` namespace" rule is honored in the sense that matters: one interface, one swap point, one deletion, no redesign.
- **Consequence stated plainly**: until #3220 lands, session rows created by this lane have no execution lease of their own. The worker's existing PID fence and health check own the session; the case lease owns the case. A stale *worker* writing to a session row is #3220's problem and is not made worse here; a stale *controller* or *research session* writing to a case is this lane's problem and is fenced.

#### Decision 2: key layout and schema version

```
improve:{project}:_ns:schema                 string  "1"                          (written once; every script checks it)
improve:{project}:_ns:pause                  hash    {reason, by, ts}             (namespace-wide pause)
improve:{project}:_ns:slots                  hash    {action_id -> admitted_ts}   (unit 1 reservations)
improve:{project}:budget:unit2:{day_key}     hash    {reserved_cents, settled_cents, expires_ts}
improve:{project}:budget:unit2:res:{res_id}  hash    {cents, purpose, case_id, day_key, state}
improve:{project}:{case}:head                hash    {schema, revision, state, epoch, highest_accepted, owner, updated_at, paused, pause_reason}
improve:{project}:{case}:journal             list    JSON {revision, action_id, event, payload_digest, generation, ts}
improve:{project}:{case}:intent:{action_id}  hash    {state, action_type, request_digest, charter_digest, reservation_id, agent_session_id, idempotency_key, attempts, generation, created_ts, updated_ts, session_terminal_at, result_digest, reason}
improve:{project}:{case}:lease               hash    {owner, generation, expires_ms}
improve:{project}:{case}:lease:gen           string  monotonic counter
```

`epoch` on the head equals the generation of the last accepted writer; the name is kept because the parent and the feature doc use it. The client is bound once in `journal.py` as `_control_redis()` (a private alias over `text_redis()`); no module in the package imports `POPOTO_REDIS_DB`, and the package docstring says why (the raw-Redis guard is a text heuristic). Money is stored in integer cents.

#### Decision 3: one script per effect

Each of `transition`, `admit`, `release_slot`, `reserve_unit2`, `settle_unit2`, `mark_reconciliation_required` is one `EVAL` that re-checks the generation (`>= highest_accepted`) and the expected revision where a revision is involved, then records the effect and appends the journal entry in the same call. Reason codes are a closed vocabulary: `SCHEMA_MISMATCH`, `PAUSED`, `STALE_GENERATION`, `REVISION_MISMATCH`, `SLOT_EXHAUSTED`, `BUDGET_EXHAUSTED`, `INTENT_STATE`, `UNAVAILABLE`. A Redis connection error is caught at the package boundary and surfaced as `TransitionResult(accepted=False, reason="UNAVAILABLE")`; no caller sees an exception and no caller falls back to the projection.

#### Decision 4: the status trio and its owner value

`models/session_lifecycle.py`: `"admitted"` joins `NON_TERMINAL_STATUSES` with the comment "created by the improvement scheduler adapter; inert until the adapter flips it to pending after a liveness check", and `RECOVERY_OWNERSHIP["admitted"] = "reflection"` with the comment naming `improvement-intent-reconcile`. `tests/unit/test_recovery_ownership.py::test_owners_are_known_values` gains `"reflection"`. `ui/data/sdlc.py:32` gains `"admitted"`. All three in one commit. `admitted` is not in `RESUMABLE_STATUSES`, the resume drip's explicit set, or any health-check query, and a test asserts each.

#### Decision 5: provenance in `extra_context`

`research_case_id`, `experiment_id`, `action_id`, `generation`, and `idempotency_key` are written through `extra_context_overrides` and read back with `session.extra_context.get(...)`. No new `AgentSession` field, no migration, no edit to `models/agent_session.py`. `finalize_session` step 7 keys on `action_id` being present.

#### Decision 6: slot release inside `finalize_session`

After step 6 (issue-lease release), step 7:

```python
# 7. Improvement lane-slot release (#3215). Gated on action-id provenance so
# every other session pays one dict lookup. Best-effort and exception-isolated.
try:
    _ec = getattr(session, "extra_context", None) or {}
    if _ec.get("action_id"):
        from tools.improvement_control.intents import on_session_terminal
        on_session_terminal(session, status)
except Exception as e:
    logger.debug("[lifecycle] improvement slot release failed (non-fatal): %s", e)
```

`on_session_terminal` runs one script: `HDEL` the slot field only if it holds this `action_id` (compare-and-delete), then `settled` if `result_digest` is set, else stamp `session_terminal_at`. The lease's own expiry never releases a slot.

#### Decision 7: unit 2 mirrors unit 3, with `purpose`

`tools/paid_inference_meter.py` copies `tools/infrastructure_budget.py`'s shape: `current_day(now, boundary) -> (day_key, start, end)`, `reserve(project_key, requested_max_usd, *, purpose, case_id=None) -> Reservation | Refusal`, `settle(reservation_id, usd, *, metering)`, `settle_from_response(reservation_id, response)`, `release(reservation_id)`, `status_dict(project_key)`. Admission is `settled + reserved + requested <= daily_paid_inference_usd`, attributed to the day open at reservation. `settle_from_response` reads `usage.cost` (attribute or mapping), then tries `GET https://openrouter.ai/api/v1/generation?id=` with the response id when the client's `base_url` is OpenRouter, then estimates from `PRICE_TABLE` (module constant with `PRICE_TABLE_RETRIEVED_AT`) and marks `metering="estimated"`; no usage at all leaves the reservation open for the reconcile pass to receipt as `metering="unknown"`. Only `purpose="rsi"` reservations count against the pool; `tools/cross_vendor_judge.py` gets a two-line record-only call (`record_receipt(purpose="sdlc_review", ...)`) so ordinary review spend is visible and never gated. Unit 3 is read, never written, by this module.

#### Decision 8: the vault writer

`tools/vault_write.py::write_credential(title, value, *, category="API Credential", vault="m-valor", project_key="valor") -> VaultWriteResult`. It writes a JSON item template to a `0600` file under `tempfile.mkdtemp()`, runs `OP_CACHE=false op item create --vault m-valor --template <file>` with `OP_SERVICE_ACCOUNT_TOKEN` inherited from the environment, deletes the file in `finally`, and returns `(item_title, fingerprint="sha256:<hex>", state)`; the value never appears in argv, logs, the result, or the evidence row. It records `ImprovementEvidence.record_once(project_key, "resource_acquired", source_ref=f"vault:{title}", text=title, detail=fingerprint)` and exposes `render_resource_acquired_section(rows) -> str` for lane 5's digest. Any `op` failure returns `state="refused"` with stderr's first line (secret-free by construction; the test greps for the seeded value) and writes nothing.

#### Decision 9: the CLI is thin

`tools/improvement.py` is `argparse` over the package: every subcommand is fewer than forty lines and calls one package function. `--json` on all. `propose` and `propose-amendment` are the only writers a research session uses; `pause`, `resume`, `replay-projection`, `import` are operator writers and print what they are about to do first. `export` writes `{root}/exports/{utc_stamp}/{namespace.json, artifacts.json}` under `POPOTO_IMPROVEMENT_CONTENT_PATH` (lane 7's contract); `import` refuses a non-empty namespace unless `--force`.

#### Decision 10: file ownership across lanes

Owned solely by this lane: `tools/improvement_control/**`, `tools/paid_inference_meter.py`, `tools/vault_write.py`, `tools/improvement.py`, `reflections/improvement_intent_reconcile.py`, `.claude/skills/improve-research/SKILL.md`, `ui/templates/improvement/control.html`, `docs/features/session-recovery-mechanisms.md`, every `tests/**/test_improvement_control_*.py`, `tests/unit/test_paid_inference_meter.py`, `tests/unit/test_vault_write.py`, `tests/unit/test_improvement_cli.py`.

Shared, edited additively and named so the second lane rebases: `models/session_lifecycle.py` (status trio, step 7), `ui/data/sdlc.py:32`, `ui/data/improvement.py` (with lane 5), `models/improvement_evidence.py` (one tuple entry), `config/settings.py` (three fields), `agent/session_health.py` (one helper), `scripts/update/reflection_register.py` and `scripts/update/run.py` (one registration each), `pyproject.toml` (one script), `docs/tools-reference.md`, `docs/features/improvement-controller.md` (with lane 4), `docs/plans/critiques/recursive-self-improvement-capability-matrix.md` (with lane 4), `tests/unit/test_improvement_models.py` (with lane 4), `tools/cross_vendor_judge.py` (two lines).

Never touched: `agent/agent_session_queue.py`, `agent/session_executor.py`, `models/agent_session.py`, `scripts/update/migrations.py`, `tools/infrastructure_budget.py`, `tools/improvement_eval/**`.

#### Decision 11: the eligibility cache stays process-local

Lane 2b left promoting `tools/improvement_eligibility.py`'s cache into the control namespace as this lane's call. The adapter calls `is_open_source` once per tick per case inside one process; nothing here needs the answer shared. Not promoted; recorded.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `journal._control_redis()` boundary: `redis.exceptions.ConnectionError` and `TimeoutError` become `reason="UNAVAILABLE"` plus one `logger.warning`; `test_improvement_control_journal.py::test_unavailable_is_a_reason_code_not_an_exception` monkeypatches the alias to raise and asserts the result, the log record, and that no key was written.
- [ ] `finalize_session` step 7 `except Exception` — `tests/unit/test_session_lifecycle.py::test_finalize_survives_slot_release_failure` seeds a session with `action_id` provenance, monkeypatches `on_session_terminal` to raise, and asserts the session still reaches its terminal status and the DEBUG log line fires.
- [ ] `on_session_terminal` swallowing nothing: it returns `SlotReleaseResult(released: bool, reason)`; a `False` with `reason="NOT_HOLDER"` is a WARNING, because a slot held by someone else at a session's end is the Race 3 signature.
- [ ] `scheduler_adapter.tick` per-case `except Exception`: each case's failure is logged at WARNING with the case id and the tick continues; `test_tick_isolates_one_bad_case` seeds two cases, breaks one, asserts the other dispatched.
- [ ] `vault_write` `except subprocess.CalledProcessError` and `FileNotFoundError` (no `op` binary): both return `state="refused"` with a secret-free detail; `test_vault_write.py::test_refused_output_contains_no_credential_bytes` seeds a distinctive value and greps the result, the log capture, and the evidence row.
- [ ] `paid_inference_meter.settle_from_response` `except (AttributeError, KeyError, TypeError)` on a malformed response: leaves the reservation open (reconcile receipts it `unknown`), logs WARNING; tested with a response object lacking `usage`.
- [ ] `reflections/improvement_intent_reconcile.run_*` mirrors `run_improvement_collect`'s shape: per-intent try/except, counts returned in the result dict, never raises to the scheduler.

### Empty/Invalid Input Handling

- [ ] `propose` with an empty or whitespace `--payload` file, a case whose `ranking_rationale` is `None` or `""`, a `priority_area` outside `PRIORITY_AREAS`, or a `charter_digest` that is not the pinned one: each refuses with its reason code and writes nothing (journal length unchanged, artifact store untouched). One parametrized test.
- [ ] `transition` with `expected_revision=None`, a negative generation, an unknown event name, or an empty `payload_digest`: `INVALID_ARGUMENT` before any Redis call.
- [ ] `admit` on a case with no head: `INTENT_STATE` (a case must be `investigating` or `experimenting` to admit). `resume` on a case that is not paused: prints "not paused" and exits 0 without a journal write.
- [ ] `export` on an empty namespace writes an archive with zero cases and says so; `import` of an archive whose `schema` differs from `SCHEMA_VERSION` refuses.
- [ ] `reserve(requested_max_usd=0)`, negative, NaN, or infinite: `Refusal(reason="INVALID_AMOUNT")`, mirroring `infrastructure_budget._valid_rate`.
- [ ] `write_credential(title="", value="")` or whitespace: `state="refused"`, reason `EMPTY`, no `op` call (asserted with a recording runner).
- [ ] The research session's brief is not processed by this lane; `propose` reads a file path the session names and never interprets agent output, so there is no empty-output loop to guard.

### Error State Rendering

- [ ] `doctor` renders three distinguishable states: reachable-and-clean ("no paused heads, no stale intents, no outstanding reservations"), reachable-with-findings (tables), and `namespace unreachable: <error>` with exit code 2. A test covers each.
- [ ] The dashboard control partial renders "unavailable" when `get_control_status` raises, "nothing yet, written by lane 3 when a case is admitted" on an empty namespace, and content otherwise; `tests/unit/test_ui_app.py` covers all three, following the goals partial's precedent.
- [ ] `propose` refusals print the reason code and the one-line human explanation to stderr and exit 1; `--json` carries `{"accepted": false, "reason": ...}`.
- [ ] `budget` prints `metering="unknown"` receipts in their own block with the window they were charged to, so an unknown never reads as zero.

## Test Impact

- [ ] `tests/unit/test_recovery_ownership.py::test_owners_are_known_values` — UPDATE: `known_owners` gains `"reflection"`, the owner value for `admitted` (the `improvement-intent-reconcile` reflection). `test_keys_match_non_terminal_statuses` stays as written and is the guard that forces the two edits to land together.
- [ ] `tests/unit/test_session_lifecycle_consolidation.py` (non-terminal enumeration at `:407-421`) — UPDATE: the enumerated set gains `admitted` and the count becomes ten.
- [ ] `tests/unit/test_ui_sdlc_data.py` — UPDATE: the `ACTIVE_STATUSES` assertion gains `admitted`.
- [ ] `tests/unit/test_session_recovery_drip_budget.py` — UPDATE: add one case asserting an `admitted` session is never dripped to `pending` (same shape as the `paused_budget` case at `:96`).
- [ ] `tests/unit/test_improvement_models.py` — UPDATE: the `EVIDENCE_KINDS` assertion gains `resource_acquired`.
- [ ] `tests/unit/test_ui_app.py` and the `ui/data/improvement.py` getter-list pin — UPDATE: the exact getter list gains `get_control_status`; the new partial renders with an empty namespace, a seeded paused case, and an unreachable namespace.
- [ ] `tests/unit/test_reflection_register.py` — UPDATE: `register_improvement_intent_reconcile` is idempotent, pinned to the `valor` owner, and carries `cadence="300s"`.
- [ ] `tests/unit/test_settings.py` — UPDATE: `ImprovementSettings` gains `lease_ttl_seconds=90`, `journal_max_entries=1000`, `max_dispatch_attempts=3`; `IMPROVEMENT__LEASE_TTL_SECONDS` overrides.
- [ ] `tests/unit/test_improvement_resources.py` — UPDATE: the vault-write probe row now reads `verified` because `tools/vault_write.py` exists; the test that asserted `absent` flips.
- [ ] `tests/unit/test_validate_no_raw_redis_delete.py::test_model_list_is_complete` — no change expected; this lane adds no `popoto.Model` subclass. Listed so the builder runs it after adding `tools/improvement_control/`.
- [ ] `tests/unit/test_infrastructure_budget.py` — no change; unit 3's key and scripts stay where lane 7 put them (see No-Gos).

## Rabbit Holes

- Building #3220 here. The session lease with write fencing in `transition_status` and the relay is a two-release shadow rollout on the production path. This lane fences the *case*, not the session.
- A general-purpose lease module under `utils/` that both this lane and #3220 could share "later". That is the fork #3220 forbids; the protocol plus a deletable interim implementation is the whole hedge.
- Moving unit 3's counter into the namespace (No-Gos). A rename that touches a live counter for consistency's sake.
- A Popoto model for intents or reservations "so the dashboard can query them". The namespace is the authority precisely because Popoto has no compare-and-set; the dashboard reads the namespace through `get_control_status`.
- A generic event-sourcing framework. One head, one list, one transition script, one replay function.
- Rerouting the cross-vendor judge to OpenRouter to get `usage.cost`. A provider change for a review judge is not metering work.
- Answering "how much did the subscription cost" in dollars. Unit 1 is concurrency; `total_cost_usd` is recorded on the intent as provenance and never gated on.
- Wiring the research session's prompt assembly, brief, or ranking snapshot. Lane 5 owns the brief; this lane's adapter takes a `brief_ref` digest and passes it through.
- A `worker:registered_pid` refactor. `any_worker_alive()` wraps the existing scan and freshness check; the orphan reaper's own loop is untouched.

## Risks

### Risk 1: The interim lease outlives #3220
**Impact:** two lease implementations drift, and a builder on #3220 leaves `CaseLease` in place because deleting it looks like scope creep.
**Mitigation:** `test_interim_lease_retired_when_redis_lease_exists` fails the suite the moment `models/redis_lease.py` exists; the module docstring names the test, the swap point, and #3220; the plan's Documentation task writes the same instruction into the feature doc's dependency table; a comment on #3220 (Task 12) tells its builder exactly which three edits close the hand-off.

### Risk 2: A fence check followed by an unguarded effect
**Impact:** a stale controller passes `transition` and then performs the enqueue or the slot write outside the script, and two sessions run for one action.
**Mitigation:** every effect is one script that both checks and records (Decision 3); the seam call is idempotent under the action id so even a double materialize yields one row; the mutation-review round is instructed to mutate each script's generation compare and revision compare separately and confirm a test fails for each.

### Risk 3: `finalize_session` step 7 regresses every session's termination
**Impact:** an import error or a Redis hiccup inside the new block breaks terminal transitions for ordinary sessions.
**Mitigation:** the block is gated on `extra_context.get("action_id")` before any import, so ordinary sessions execute one dict lookup; the import is lazy and inside the `try`; `test_finalize_survives_slot_release_failure` asserts the terminal status lands when the hook raises; `test_finalize_without_provenance_never_imports_control` asserts the package is not imported for a plain session.

### Risk 4: The raw-Redis guard blocks the builder's own verification
**Impact:** a builder typing `python -c "...improve:...delete..."` in Bash trips `validate_no_raw_redis_delete.py` and works around it with something worse.
**Mitigation:** the package binds `_control_redis()` privately, no file in it names `POPOTO_REDIS_DB` or `popoto`, every compare-and-delete is exercised only from pytest files, and the builder brief says so up front.

### Risk 5: Unit 2 admits what it cannot meter, or gates what it must not
**Impact:** either a paid call runs with no receipt (charter §8: unknown is not zero) or the SDLC judge is refused because RSI exhausted the day.
**Mitigation:** a reservation with no settlement is receipted `metering="unknown"` by the reconcile pass and pauses further `purpose="rsi"` admission until an operator runs `budget --acknowledge-unknown`; `purpose="sdlc_review"` receipts are record-only and never counted against the pool or refused.

### Risk 6: The status trio lands in two commits
**Impact:** a builder adds `admitted` to `NON_TERMINAL_STATUSES`, the suite fails on `test_keys_match_non_terminal_statuses`, and the fix commit lands the owner separately; or `ui/data/sdlc.py` is forgotten and `admitted` reads inactive on the dashboard.
**Mitigation:** Task 3 is the three edits and their tests as one commit, explicitly; the Verification row greps all three files for `admitted`.

### Risk 7: A research session finds another door
**Impact:** the session calls `valor-session create` or writes `ImprovementCase.save()` directly, bypassing the journal.
**Mitigation:** the skill text forbids it; the projection is overwritten by the next `apply` from the head, so a direct save is lost rather than authoritative; `replay-projection` proves it; the "no child-gate bypass" and "no direct case save outside projection" Verification rows grep for both.

## Race Conditions

### Race 1: Two ticks admit the same case
**Location:** `intents.admit` script; `agent/reflection_scheduler.py::is_reflection_running` (`:577`), which reads a status field, not a lease.
**Trigger:** a slow tick overlaps its successor; both read the head at revision N and decide to admit.
**Data prerequisite:** the head's `revision` and `highest_accepted`.
**State prerequisite:** the second admit must observe the first's revision advance.
**Mitigation:** `admit` compares `expected_revision` inside the script; the loser gets `REVISION_MISMATCH` and writes nothing. The slot reservation is in the same script, so a loser never holds a slot. Test: two `admit` calls with the same expected revision, exactly one `admitted` intent and one slot field.

### Race 2: Crash between admission and session creation
**Location:** `scheduler_adapter.materialize` around the `_push_agent_session` call.
**Trigger:** the process dies after the intent is `admitted` (or after the seam's `bind` won the key) and before the intent records the bound id.
**Data prerequisite:** the intent's `idempotency_key` is written at admit time, before the seam is called.
**State prerequisite:** intent `admitted`; the row may or may not exist; the idempotency key may or may not be bound.
**Mitigation:** the next tick retries with the same key; `bind` hands back the same preallocated id whether or not the row was created (`agent/enqueue_idempotency.py:59-62`), and `_push_agent_session` creates the row only if it is missing. Test: monkeypatch `AgentSession.async_create` to raise once after `bind`, run the adapter twice, assert one row with the bound id and the intent at `materialized` with `attempts == 2`.

### Race 3: A lane slot outlives the session that held it
**Location:** `finalize_session` step 7; the `_ns:slots` hash.
**Trigger:** a watchdog, health sweep, or scheduler kill finalizes a research session on a path that never ran the ordinary completion code; or the row is deleted outright.
**Data prerequisite:** `action_id` in `extra_context`.
**State prerequisite:** the slot field for that action id exists and `max_concurrent_research_sessions == 1`, so nothing else can be admitted.
**Mitigation:** step 7 runs on every `finalize_session` path; the reconcile pass releases a slot whose intent has been `running` past the staleness threshold with a terminal or missing session row. Test ("unreleased lane slot on restart"): seed a slot and a `running` intent whose `agent_session_id` has no row, run the reconcile pass, assert the slot is free and the intent is `reconciliation_required`.

### Race 4: Result submitted under a replaced generation
**Location:** `journal.transition`; `valor-improve propose --action-id`.
**Trigger:** controller A stalls past lease expiry, B acquires generation g+1 and transitions, A's research session submits with g.
**Data prerequisite:** the session's `generation` in `extra_context`.
**State prerequisite:** `head.highest_accepted == g+1`.
**Mitigation:** the script refuses `g < highest_accepted` with `STALE_GENERATION`; the CLI stores the artifact as `ImprovementEvidence(kind="other", detail="stale_generation")` and exits 1. The holder's own second write (`g == highest_accepted`) is accepted. Test ("stale-generation rejection at an effect boundary"): both branches.

### Race 5: Liveness check passes, worker dies before pickup
**Location:** `scheduler_adapter.activate`.
**Trigger:** `any_worker_alive()` is true, the session flips to `pending`, the worker exits before popping it.
**Data prerequisite:** none beyond the pending row.
**State prerequisite:** intent `running`, session `pending`.
**Mitigation:** this is the ordinary queue's problem and the ordinary health check's job ("starts workers for stalled `pending` sessions", `docs/features/session-recovery-mechanisms.md:33`); the intent's staleness threshold is the backstop. No new mechanism.

### Race 6: Reconcile pass and a live adapter tick touch one intent
**Location:** `intents.mark_reconciliation_required` and `scheduler_adapter.materialize`.
**Trigger:** the reconcile reflection judges an `admitted` intent stale while a delayed tick is materializing it.
**Data prerequisite:** the intent's `state` and `updated_ts`.
**State prerequisite:** both run under their own lease generation.
**Mitigation:** both are CAS scripts on the intent's `state`; whichever lands second gets `INTENT_STATE` and stops. A materialize that loses releases nothing (it held nothing new); a reconcile that loses leaves the slot with the live intent.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3220] The production session execution lease (`models/redis_lease.py`, `lease:session:{agent_session_id}`, write fencing in `transition_status` and the Telegram relay, the shadow-release flag). This lane consumes a lease through `tools/improvement_control/lease.py::LeaseProtocol`, whose three calls match #3220's declared interface, and ships an interim implementation confined to `improve:*` keys. `tests/unit/test_improvement_control_lease.py::test_interim_lease_retired_when_redis_lease_exists` fails the moment `models/redis_lease.py` exists, so #3220 lands by deleting the interim module and pointing the protocol at its own. This lane opens no change to `agent/agent_session_queue.py`, `agent/session_executor.py`, or `transition_status`.
- [SEPARATE-SLUG #3217] The `improvement-assumption-digest` reflection, the planner tick that opens cases, and the dashboard renderings of cases, hypotheses, and rejected experiments. This lane ships the `resource_acquired` evidence row and `tools/vault_write.py::render_resource_acquired_section(rows)`, the pure renderer the digest calls.
- [SEPARATE-SLUG #3216] The `serves_charter` judge and every file under `tools/improvement_eval/`. This lane's meter exposes `reserve`/`settle` for lane 4's paid evaluators to call; it does not edit lane 4's files.
- [SEPARATE-SLUG #3218] Release records, `valor-improve release compare`'s comparison logic, and the candidate-surface denylist. The CLI ships the `release compare` subcommand as a reader of `ImprovementRelease` rows that prints "no release records yet, written by lane 6" until lane 6 writes them.
- [SEPARATE-SLUG #3274] Moving the unit-3 window counter (`improvement:budget:unit3:{window_key}`) and its Lua into the `improve:*` namespace. Lane 7 shipped it on its own key with paired Popoto ledger rows; moving a live counter mid-window buys a naming consistency and risks a double-count. The counter stays where it is, `valor-improve budget` reads it through `tools.infrastructure_budget.status_dict`, and the sentence in `docs/features/improvement-controller.md` that promised the migration is corrected in this lane's docs task.
- [DESTRUCTIVE] Lifting or bypassing the child session gate. The research path never passes `parent_agent_session_id` and never sets `VALOR_ALLOW_CHILD_SESSIONS`; the "no child-gate bypass" Verification row asserts it.
- [DESTRUCTIVE] Rerouting `tools/cross_vendor_judge.py` to OpenRouter (`base_url`). That is a provider change for a review-quality judge, not a metering change. The judge gains a record-only meter call (receipt with `purpose="sdlc_review"`, `metering="estimated"`) and keeps its provider.
- [EXTERNAL] Provisioning the `valor-local` service account's write access to `m-valor`. `tools/vault_write.py` fails closed with a `VaultWriteRefused` result when `op item create` is refused, and the probe row reports it.

## Update System

- `scripts/update/run.py` gains a `register_improvement_intent_reconcile` step calling the new `reflection_register.py::register_improvement_intent_reconcile` (`cadence="300s"`, callable `reflections.improvement_intent_reconcile.run_improvement_intent_reconcile`), idempotent like `register_improvement_collect` (`scripts/update/reflection_register.py:625`), machine-pinned by the existing `_this_machine_owns_valor` guard. The `RegisterResult` field joins the run.py result dataclass beside the collect step's.
- `pyproject.toml [project.scripts]` gains `valor-improve = "tools.improvement:main"`. `/update` already reinstalls the project (`uv sync`), so the console script materializes on the next update with no further step.
- No new third-party dependencies. `openai` (already pinned) is the only client the meter touches, and only through the response object a caller hands it.
- No `.env` change. `OP_SERVICE_ACCOUNT_TOKEN` is already declared with `# @passthrough op`; `tools/vault_write.py` reads it from the process environment exactly as `tools/improvement_resources.py` does.
- No Popoto schema change and no migration. `admitted` is a status value, `resource_acquired` is a constant, session provenance rides `AgentSession.extra_context`, and every new record lives in the non-Popoto `improve:*` namespace. `scripts/update/migrations.py` is untouched, which also keeps this lane out of lane 4's edit to that file.

## Agent Integration

- New CLI entry point `valor-improve = "tools.improvement:main"` in `pyproject.toml [project.scripts]`, subcommands `case show`, `case explain`, `propose`, `propose-amendment`, `budget`, `release compare`, `pause`, `resume`, `doctor`, `export`, `import`, `replay-projection`. A research session reaches research state only through `propose`, which acquires the case lease, writes one `action_proposed` journal event under journal authorization, and never exposes a raw transition. `--json` on every subcommand for the agent; the human format is the default.
- The research skill `.claude/skills/improve-research/SKILL.md` (project-only, never synced) instructs the session to read the bounded brief it was given, use `WebSearch`/`WebFetch`, write hypotheses and proposed actions through `valor-improve propose`, never run `valor-session create`, never send a message, and never ask a human anything. The one outbound message class is `valor-improve propose-amendment`, which sends Tom one plain Telegram message through `reflections/utilities.py::send_eng_telegram` and leaves the investigation `awaiting_authorization`.
- The bridge imports nothing new; the poll registry, `tools/ask_poll.py`, `bridge/poll_registry.py`, and `bridge/answer_routing.py` are untouched.
- `models/session_lifecycle.py::finalize_session` gains one lazy-imported, exception-isolated call into `tools.improvement_control.intents.on_session_terminal(session, status)`, gated on the session carrying `action_id` provenance, so a research session's lane slot is released on every termination path.
- Integration tests: `tests/integration/test_improvement_control_cli.py` runs `valor-improve propose` end to end against a claimed test db and asserts the journal shows the event; a session on the research path attempting `valor-session create --parent` receives `ChildSessionsDisabledError`'s message; `valor-improve doctor` on a seeded paused case prints the paused head and its outstanding reservation.

## Documentation

- [ ] Update `docs/features/improvement-controller.md`: replace the "lane 3" placeholders in "Control namespace contract", "Dispatch", "Break-glass", and "Dependency on #3183" with the shipped shapes (key layout, reason codes, intent lifecycle, the lease protocol and its #3220 hand-off, unit-2 metering, the vault writer), and correct the unit-3 sentence at `:224-225` to the recorded decision (the counter stays on its key).
- [ ] Update `docs/features/session-recovery-mechanisms.md`: add `admitted` to the status inventory with its owner (`reflection`, the `improvement-intent-reconcile` pass), state that the health check, startup recovery, and the resume drip never touch it, and describe the reconcile pass as mechanism 8 in the same table shape.
- [ ] Update `docs/tools-reference.md`: the `valor-improve` section drops "planned" and lists the shipped subcommands with `--json`.
- [ ] Update `docs/features/adding-reflection-tasks.md` (or its registration section) with the second improvement registration, `improvement-intent-reconcile`.
- [ ] Update `docs/features/redis-models.md`: the `improve:*` namespace exception and its private-alias rule, cross-linked to the feature doc.
- [ ] Create `.claude/skills/improve-research/SKILL.md` (project-only skill text is documentation the agent reads).
- [ ] Add a lane-3 section to `docs/plans/critiques/recursive-self-improvement-capability-matrix.md`.

## Success Criteria

_Filled in the next revision of this document._

## Team Orchestration

_Filled in the next revision of this document._

## Step by Step Tasks

_Filled in the next revision of this document._

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Placeholder until the tasks are written | `true` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

_Filled in the next revision of this document._
