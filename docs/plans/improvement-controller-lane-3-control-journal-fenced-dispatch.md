---
status: Ready
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
revision_applied: true
revision_applied_at: 2026-09-14T11:47:18Z
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
- OpenRouter now includes `usage.cost` (and `usage.cost_details`) in every response; the `usage: {include: true}` request flag is deprecated and has no effect. Cost cannot be reliably rebuilt from tokens times list price, because one model id routes to different upstreams with cache discounts. Source: [OpenRouter usage accounting](https://openrouter.ai/docs/guides/guides/administration/usage-accounting). Informs `tools/paid_inference_meter.py`: settle from `response.usage.cost` when present (`metering="exact"`), otherwise estimate from a dated price table marked `metering="estimated"`. The `GET /generation?id=` lookup is deliberately left out of this lane: no lane-3 code routes a call through OpenRouter (the one paid call in the repo, `tools/cross_vendor_judge.py:209`, targets OpenAI and rerouting it is a No-Go), so the lookup belongs to whichever lane first does. The parent plan's `extra_body={"usage": {"include": True}}` instruction is dropped.
- Redis 5 and later replicate Lua scripts by effects, so `TIME` is allowed inside a writing `EVAL`; time is frozen for the script's duration, so one `TIME` call is the script's consistent clock. A fencing generation should come from `INCR` inside the same script, never from the clock, and `EVAL` is never read-only (use `EVAL_RO` for pure reads). Source: [Redis scripting with Lua](https://redis.io/docs/latest/develop/programmability/eval-intro/). Informs the lease script (generation from `INCR` on `improve:{p}:{c}:lease:gen`, expiry from `TIME`) and the transition script (one `TIME` read for `updated_at` and the journal `ts`).
- Carried from the parent plan's research: the accept rule at effect boundaries is `generation >= highest_accepted`; release is compare-and-delete. Sources: [Redisson FencedLock](https://redisson.pro/glossary/java-fencedlock.html), [Design a Distributed Lock Service](https://dev.to/gabrielanhaia/design-a-distributed-lock-service-fencing-tokens-and-the-failure-modes-29nl).

## Data Flow

1. **Entry point (proposal)**: a research session (dispatched by this lane, briefed by lane 5) runs `"$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve" propose --case ID --action-type investigate --payload FILE` (the console script lives in the venv only; Decision 14). `tools/improvement.py` validates the case's `priority_area`, non-empty `ranking_rationale`, and `charter_digest == ImprovementCharter.pinned("valor").digest`, refusing with a reason code otherwise. When `AGENT_SESSION_ID` is set in the environment (the worker exports it to every harness subprocess, `agent/session_executor.py:2298`), the CLI loads that row through the ORM and reads `action_id` from `extra_context`; it presents both to the journal as its **intent binding**. With no `AGENT_SESSION_ID` (an operator at a terminal) `propose` is the break-glass path: `--action-id` is optional and no binding is presented.
2. **Artifact first**: the payload is written to `VerifyingArtifactStore` under `POPOTO_IMPROVEMENT_CONTENT_PATH` and hashed; the digest is what the journal will reference.
3. **Lease**: `lease.acquire(f"improve:valor:{case}:lease", ttl=lease_ttl_seconds)` returns a generation or `None` (case busy). The CLI holds it only for the write. **This generation is the CLI's own, minted now**, so it satisfies `>= highest_accepted` unless a newer holder has written in the meantime; the lease generation fences *controllers* (any process that acquires the case lease), never a session that was dispatched minutes ago (Decision 12).
4. **Journal**: `journal.transition(case, expected_revision=head.revision, generation, action_id, event="action_proposed", payload_digest, agent_session_id=<own id or None>)` runs one Lua script: schema check, pause check, `generation >= highest_accepted`, `expected_revision == revision`, and, when `agent_session_id` is given (the session branch of Decision 12; an operator passes `None`), the **intent binding check**: `HGET intent:{action_id} state == "running"` and `HGET intent:{action_id} agent_session_id == ARGV.agent_session_id`, else `{0, "INTENT_STATE", rev}`; then, once the binding compare has passed and before the `RPUSH`, `HSET intent:{action_id} result_digest ARGV.payload_digest updated_ts <now>` (the one writer of `result_digest`, so step 10 can settle the intent); then head advance and `RPUSH`+`LTRIM` on `journal`. Returns `TransitionResult(accepted, reason, revision)`.
5. **Projection**: on `accepted`, `projection.apply(case)` reads the head and `save()`s `ImprovementCase.state` and `revision` through the ORM. The projection is never read for a decision.
6. **Scheduler adapter (function reflection `improvement-controller-tick`, this lane's dispatch half)**: for each case whose head carries an unadmitted `action_proposed` of an allowed type, `intents.admit(...)` runs one Lua script that refuses `INTENT_STATE` while any intent on the case is `reconciliation_required` (checked first, so a wedged case never consumes a slot), then checks the slot count (`HLEN slots + 1 <= max_concurrent_research_sessions`), the revision, and the generation, then writes `intent:{action_id}` in state `admitted` (with `generation` on the intent hash as provenance) and reserves the slot under the action id.
7. **Materialize**: `_push_agent_session(idempotency_key=f"improve:valor:{case}:{action_id}", status="admitted", extra_context_overrides={research_case_id, experiment_id, action_id, idempotency_key}, ...)` returns `(depth, agent_session_id)`; the intent moves `admitted -> materialized` (CAS on `from_state`, Decision 13) with the bound id written to `intent.agent_session_id`. A retry after a crash passes the same key and gets the same id. **No generation rides in the session's `extra_context`**: a generation copied at admit time is stale by design the moment any later acquirer writes (the adapter's own step 8 already does), so the session never presents one.
8. **Activate**: `agent.session_health.any_worker_alive()` must be true; then `transition_status(session, "pending", reason="improvement-controller activate")`, then, strictly after that save, `publish_session_notify(session)` (`agent/agent_session_queue.py:967`, #2439: sync, fail-quiet, notify-only, no local worker spawn, so safe from the reflection subprocess) so the worker's per-key loop wakes within one notify hop instead of waiting for an unrelated enqueue or the 5-minute stalled-`pending` sweep; the seam's own notify fired at create time while the row was still `admitted`, when the loop's `status="pending"` query found nothing. The intent then moves `materialized -> running`. Calling into the module is a read of its public surface, so the "queue seam untouched" anti-criterion still holds. With no live worker the intent stays `materialized` and the reason is journaled.
9. **Worker**: picks up `pending` as today. The session's prompt carries the case brief and the research skill; the session writes results only through `valor-improve propose` (step 1), which acquires its own lease generation (step 3) and presents its intent binding (`action_id` from `extra_context`, its own `AGENT_SESSION_ID`). A session whose intent is no longer `running` under its id (moved to `reconciliation_required`, cancelled, or re-dispatched under a new action id) is refused `INTENT_STATE`; its artifact is stored as `ImprovementEvidence(kind="other", detail="intent_state:<state>")` and the CLI exits 1.
10. **Terminal**: `finalize_session` step 7 calls `intents.on_session_terminal(session, status)`: one Lua script releases the slot held by `action_id` (compare-and-delete on the hash field, returning `released`, `absent`, or `foreign_holder`) and then settles by outcome: `result_digest` set (step 4 wrote it) → `running -> settled`; no `result_digest` but `status == "completed"` → `running -> settled` with `reason="no_proposal"` (a session that honestly found nothing to propose is finished research, not a wedge); `failed`, `killed`, or `abandoned` with no `result_digest` → stamp `session_terminal_at` and leave the intent `running`, the one case the reconcile sweep exists for. The slot is released on every branch.
11. **Recovery (function reflection `improvement-intent-reconcile`, 300s)**: scans `improve:valor:*:intent:*` and branches on state. Intents in `admitted` or `materialized` older than `4 * lease_ttl_seconds`: `stale_sweeps += 1` (the reconcile pass's own counter, distinct from the adapter's `attempts`), and at `stale_sweeps >= max_dispatch_attempts` they are CAS-moved to `reconciliation_required`, their slot released, and any bound `AgentSession` row forced terminal through `finalize_session(status="abandoned", dead_letter_stage="improvement-intent")`. `running` intents whose session row is missing or already in `TERMINAL_STATUSES` past the same age act on the first sweep (slot release, `reconciliation_required`): the session that held the slot is gone, so there is nothing to wait for. Unit-2 reservations whose day window closed without settlement are released and receipted `metering="unknown"`.
12. **Exit from `reconciliation_required`**: the only writer out of that state is `intents.cancel(project_key, case_id, action_id, *, generation, by)`, one EVAL (`from_state == reconciliation_required`, else `INTENT_STATE`; `HSET state cancelled`; idempotent `HDEL _ns:slots action_id`; journal `intent_cancelled`). Its caller is `valor-improve resume --case ID --force`, which lists every `reconciliation_required` intent on the case, cancels each, then runs the head `resumed` transition. The adapter re-admits the case on a later tick under a **new** action id only once no intent is `reconciliation_required` (step 6). Without `--force`, `resume` prints the blocking action ids and exits 1.
13. **Output**: `valor-improve doctor`, `case show`, `budget`, and the dashboard partial read the head, intents, slots, and the unit-2 counter, never the projection.

## Architectural Impact

- **New dependencies**: none third-party. New package `tools/improvement_control/` (journal, lease, intents, scheduler adapter, projection, recovery, export) plus `tools/paid_inference_meter.py`, `tools/vault_write.py`, `tools/improvement.py`, `reflections/improvement_intent_reconcile.py`.
- **Interface changes**: `NON_TERMINAL_STATUSES` and `RECOVERY_OWNERSHIP` gain `admitted` (owner value `reflection`, a new owner vocabulary entry); `ACTIVE_STATUSES` gains `admitted`; `EVIDENCE_KINDS` gains `resource_acquired`; `INVESTIGATION_KINDS` gains `charter_amendment` and `INVESTIGATION_STATES` gains `awaiting_authorization`; `ImprovementSettings` gains `lease_ttl_seconds`, `journal_max_entries`, `max_dispatch_attempts`; `agent/session_health.py` gains the public `any_worker_alive()`; `finalize_session` gains one post-transition side effect (step 7). No queue signature changes; `_push_agent_session` is called with arguments it already accepts.
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
| Create-or-bind seam on main | `grep -q "idempotency_key: str" agent/agent_session_queue.py` | Dispatch materializes through it |
| DeadLetter stage field on main | `grep -q "stage = IndexedField" models/dead_letter.py` | Exhausted intents write `stage="improvement-intent"` |
| Charter v2 vocabulary on main | `grep -q "charter_digest = Field" models/improvement_case.py` | `propose` refuses against it |
| Unit-3 meter on main | `test -f tools/infrastructure_budget.py` | `valor-improve budget` reads it |
| Redis 5 or later (effects replication) | `.venv/bin/python -c "from utils.redis_client import text_redis; v = text_redis().info('server')['redis_version']; assert int(v.split('.')[0]) >= 5, v"` | `TIME` inside a writing `EVAL` |
| Issue 3220 state is readable | `gh issue view 3220 --json state -q .state` | Task 2 branches on OPEN or CLOSED; either passes |

Run all checks via `python scripts/check_prerequisites.py docs/plans/improvement-controller-lane-3-control-journal-fenced-dispatch.md`.

`op` non-interactive auth is deliberately absent from the table: only `tests/integration/test_vault_write_integration.py` needs it, and that test skips with a named reason when `OP_CACHE=false op whoami` fails. No build step depends on it.

## Solution

### Key Elements

- **Control journal** (`tools/improvement_control/journal.py`): the case head and its bounded journal in `improve:{project}:{case}:*`, advanced only by one Lua `transition` that verifies schema, pause, generation, and revision before it writes. Rejections are reason codes.
- **Lease protocol and interim lease** (`tools/improvement_control/lease.py`): `acquire`/`renew`/`release` with a monotonic generation, matching #3220's declared interface, implemented for `improve:*` keys only until #3220 replaces it.
- **Dispatch intents and reservations** (`tools/improvement_control/intents.py`): the durable record between "decided" and "a session exists", the lane-slot hash, and the terminal hook `finalize_session` calls.
- **Scheduler adapter** (`tools/improvement_control/scheduler_adapter.py` behind the `improvement-controller-tick` reflection): admits, materializes through the create-or-bind seam with `status="admitted"`, checks worker liveness, activates.
- **`admitted` session status**: `NON_TERMINAL_STATUSES`, `RECOVERY_OWNERSHIP`, and `ACTIVE_STATUSES` in one change; invisible to the worker, the health check, startup recovery, and the resume drip.
- **Recovery** (`reflections/improvement_intent_reconcile.py`): reads intents, not a status map; CAS to `reconciliation_required`, slot release, forced-terminal row, dead letter on exhaustion; also closes stale unit-2 reservations.
- **Projection and replay** (`tools/improvement_control/projection.py`): ORM `save()` after the journal commits. The head is authoritative; `replay-projection` reconciles the projection to the head, folding the journal tail (which is `LTRIM`med to `journal_max_entries`, Decision 2) as a cross-check and reporting, never failing on, a fold that cannot reach the head's revision because the tail was trimmed.
- **Unit-2 meter** (`tools/paid_inference_meter.py`): reserve-then-check on `improve:{project}:budget:unit2:{day_key}`, settle from `usage.cost`, estimate with a dated table otherwise, receipts as `spend_receipt` evidence with `metering` and `purpose`.
- **Vault writer** (`tools/vault_write.py`): the one sanctioned `op item create` path; never returns, logs, or echoes the value; writes a `resource_acquired` evidence row with the item title and SHA-256 fingerprint; ships the digest section renderer.
- **`valor-improve` CLI** (`tools/improvement.py`) and the **research skill** (`.claude/skills/improve-research/SKILL.md`): the only door for a research session; break-glass for an operator.
- **Export/import** (`tools/improvement_control/export.py`): dump and restore the namespace and artifact manifests against lane 7's destination contract.
- **Dashboard control panel** (`ui/data/improvement.py::get_control_status`, `ui/templates/improvement/control.html`): intents, reservations, unit-2 spend, paused and `reconciliation_required` states, with the three-state empty/unavailable rendering lane 2 established.

### Flow

**Case at `investigating`** → planner (lane 5) or operator writes a brief → **research session** (dispatched by this lane's adapter, status `admitted` → `pending` → running) → `valor-improve propose` → **journal event `action_proposed`** → adapter tick admits (slot reserved, intent `admitted`) → seam creates the next session `admitted` (intent `materialized`) → liveness check passes → session `pending` (intent `running`) → session finishes → `finalize_session` step 7 releases the slot (intent `settled` or incomplete) → **head advanced, projection updated, dashboard shows it**.

**Operator, wedged case**: `valor-improve doctor` → sees one paused head with an outstanding reservation and one `reconciliation_required` intent → `case show --case ID` → reads the journal tail → `resume --case ID` prints the blocking action id and exits 1 → `resume --case ID --force` cancels that intent (journal `intent_cancelled`, slot freed if still held) and advances the head with event `resumed` → the next adapter tick admits the case again under a new action id.

**Operator, namespace outage**: `doctor` prints `namespace unreachable: <error>` and nothing else → fix Redis → `doctor` again → every head reads as it was; nothing was written from the projection.

### Technical Approach

#### Decision 1: the lease without #3220

#3220 owns `models/redis_lease.py` and is open with no branch. The issue says this lane "blocks on the lease specifically". Waiting means lanes 4, 5, and 6 wait too, because every one of them dispatches through this lane. Forking a second general lease is what #3220 forbids. The choice made here:

- `tools/improvement_control/lease.py` defines `LeaseProtocol` (a `typing.Protocol`) with exactly #3220's declared calls: `acquire(key: str, ttl: int) -> int | None`, `renew(key: str, generation: int) -> bool`, `release(key: str, generation: int) -> bool`. The journal, the adapter, and the CLI take a `LeaseProtocol` and default to `default_lease()`, a module-level factory that is the single swap point.
- The same module ships `CaseLease`, the interim implementation: three Lua scripts on `utils.redis_client.text_redis()`, generation from `INCR improve:{p}:{c}:lease:gen` inside the acquire script, expiry from server `TIME`, `renew` and `release` compare the stored generation (compare-and-delete). Keys are restricted to the `improve:` prefix by an assertion in every call, so it can never be pointed at `lease:session:*`.
- **The accept rule lives in the journal, not the lease.** `transition` and every effect script compare `generation >= head.highest_accepted` and record `highest_accepted = generation` on acceptance. That is the rule this lane asserts in its own tests, and it is independent of which lease minted the generation.
- **Hand-off contract, tested**: `tests/unit/test_improvement_control_lease.py` runs the protocol conformance suite (acquire returns increasing generations; a lapsed lease can be re-acquired with a higher generation; `renew` and `release` with a stale generation return False and change nothing; the holder's own second write is accepted) against `CaseLease`, and `test_interim_lease_retired_when_redis_lease_exists` asserts `not (Path(__file__).resolve().parents[2] / "models" / "redis_lease.py").exists()`, anchored to the repo root so it cannot pass trivially from a worktree or a different test cwd. When #3220 lands, its builder deletes `CaseLease`, points `default_lease()` at `models.redis_lease`, deletes the retirement test and the lane-time Verification row that shadows it, and reruns the same conformance suite. The parent's "no second lease in the `improve:*` namespace" rule is honored in the sense that matters: one interface, one swap point, one deletion, no redesign.
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
improve:{project}:{case}:intent:{action_id}  hash    {state, action_type, request_digest, charter_digest, reservation_id, agent_session_id, idempotency_key, attempts, stale_sweeps, generation, created_ts, updated_ts, session_terminal_at, result_digest, reason}
improve:{project}:{case}:lease               hash    {owner, generation, expires_ms}
improve:{project}:{case}:lease:gen           string  monotonic counter
```

`epoch` on the head equals the generation of the last accepted writer; the name is kept because the parent and the feature doc use it. The intent hash carries two counters with one writer each: `attempts` is owned by `record_materialized` (`HINCRBY` on every materialize try, so Race 2 reads `attempts == 2` after one crash-retry) and `stale_sweeps` is owned by `mark_reconciliation_required`'s caller in `recovery.py` (one increment per 300s sweep that finds an `admitted`/`materialized` intent past its age threshold). `result_digest` has exactly one writer, the `transition` script (Data Flow step 4). The client is bound once in `journal.py` as `_control_redis()` (a private alias over `text_redis()`); no module in the package imports `POPOTO_REDIS_DB`, and the package docstring says why (the raw-Redis guard is a text heuristic). Money is stored in integer cents.

#### Decision 3: one script per effect

Each of `transition`, `admit`, `record_materialized`, `record_running`, `release_slot`, `cancel`, `reserve_unit2`, `settle_unit2`, `mark_reconciliation_required` is one `EVAL` that re-checks the generation (`>= highest_accepted`) and the expected revision where a revision is involved, re-checks the intent's `from_state` where an intent is involved (Decision 13), re-checks the intent binding where a session is the writer (Decision 12), then records the effect and appends the journal entry in the same call. Reason codes are a closed vocabulary: `SCHEMA_MISMATCH`, `PAUSED`, `STALE_GENERATION`, `REVISION_MISMATCH`, `SLOT_EXHAUSTED`, `BUDGET_EXHAUSTED`, `INTENT_STATE`, `UNAVAILABLE` from the scripts, plus `INVALID_ARGUMENT` and `NOT_A_RESEARCH_SESSION`, which the Python layer returns before any Redis call. A Redis connection error is caught at the package boundary and surfaced as `TransitionResult(accepted=False, reason="UNAVAILABLE")`; no caller sees an exception and no caller falls back to the projection.

#### Decision 4: the status trio and its owner value

`models/session_lifecycle.py`: `"admitted"` joins `NON_TERMINAL_STATUSES` with the comment "created by the improvement scheduler adapter; inert until the adapter flips it to pending after a liveness check", and `RECOVERY_OWNERSHIP["admitted"] = "reflection"` with the comment naming `improvement-intent-reconcile`. `tests/unit/test_recovery_ownership.py::test_owners_are_known_values` gains `"reflection"`. `ui/data/sdlc.py:32` gains `"admitted"`. All three in one commit. `admitted` is not in `RESUMABLE_STATUSES`, the resume drip's explicit set, or any health-check query, and a test asserts each.

#### Decision 5: provenance in `extra_context`

`research_case_id`, `experiment_id`, `action_id`, and `idempotency_key` are written through `extra_context_overrides` (`agent/agent_session_queue.py:338-339`) and read back with `session.extra_context.get(...)`. **`generation` is not among them**: it stays on the intent hash as provenance of which controller admitted the action, and a session never presents it (Decision 12). No new `AgentSession` field, no migration, no edit to `models/agent_session.py`. `finalize_session` step 7 keys on `action_id` being present. Inside the harness subprocess the CLI finds its own row through `AGENT_SESSION_ID` (`agent/session_executor.py:2298`), the same variable `tools/sdlc_session_ensure.py` already relies on.

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

`on_session_terminal` runs one script: `HDEL` the slot field only if it holds this `action_id` (compare-and-delete), returning a three-way slot result (`released`, `absent`, `foreign_holder`), then settles by outcome: `running -> settled` when `result_digest` is set; `running -> settled` with `reason="no_proposal"` when it is unset and `status == "completed"`; otherwise (`failed`, `killed`, `abandoned`) stamp `session_terminal_at` and leave the intent `running` for the reconcile sweep. It returns `SlotReleaseResult(slot, intent_state, reason)`. Only `foreign_holder` is logged at WARNING (the Race 3 signature: someone else holds the field at this session's end); `absent` is DEBUG, because the reconcile pass releases the slot itself before forcing a row terminal and step 7 then finds the field already gone. The lease's own expiry never releases a slot.

#### Decision 7: unit 2 mirrors unit 3, with `purpose`

`tools/paid_inference_meter.py` copies `tools/infrastructure_budget.py`'s shape: `current_day(now, boundary) -> (day_key, start, end)`, `reserve(project_key, requested_max_usd, *, purpose, case_id=None) -> Reservation | Refusal`, `settle(reservation_id, usd, *, metering)`, `settle_from_response(reservation_id, response)`, `release(reservation_id)`, `status_dict(project_key)`. Admission is `settled + reserved + requested <= daily_paid_inference_usd`, attributed to the day open at reservation. `settle_from_response(reservation_id, response)` has exactly two branches and no network call: `cost = _read_cost(response)` reads `response.usage.cost` then `response["usage"]["cost"]` and returns `None` on `AttributeError`/`KeyError`/`TypeError`; `if cost is not None: settle(reservation_id, cost, metering="exact")`, else `settle(reservation_id, _estimate(model, prompt_tokens, completion_tokens), metering="estimated")` from `PRICE_TABLE` (module constant with `PRICE_TABLE_RETRIEVED_AT`); no usage at all leaves the reservation open for the reconcile pass to receipt as `metering="unknown"`. The module imports no HTTP client (`requests`, `httpx`, `urllib.request`); the OpenRouter `/generation` lookup belongs to the lane that first routes a call through OpenRouter, and a Verification row keeps the URL out of this module. Only `purpose="rsi"` reservations count against the pool; `tools/cross_vendor_judge.py` gets a two-line record-only call (`record_receipt(project_key="valor", purpose="sdlc_review", ...)`) so ordinary review spend is visible and never gated. The receipt is pinned to `project_key="valor"` because the judge runs for any repository and holds no project key of its own, and the receipt is about the paid-inference pool, which is Valor's, not about the repository under review. Unit 3 is read, never written, by this module.

#### Decision 8: the vault writer

`tools/vault_write.py::write_credential(title, value, *, category="API Credential", vault="m-valor", project_key="valor") -> VaultWriteResult`. It writes a JSON item template to a `0600` file under `tempfile.mkdtemp()`, runs `OP_CACHE=false op item create --vault m-valor --template <file>` with `OP_SERVICE_ACCOUNT_TOKEN` inherited from the environment, deletes the file in `finally`, and returns `(item_title, fingerprint="sha256:<hex>", state)`; the value never appears in argv, logs, the result, or the evidence row. It records `ImprovementEvidence.record_once(project_key, "resource_acquired", source_ref=f"vault:{title}", text=title, detail=fingerprint)` and exposes `render_resource_acquired_section(rows) -> str` for lane 5's digest. Any `op` failure returns `state="refused"` with stderr's first line (secret-free by construction; the test greps for the seeded value) and writes nothing.

#### Decision 9: the CLI is thin

`tools/improvement.py` is `argparse` over the package: every subcommand is fewer than forty lines and calls one package function. `--json` on all. `propose` and `propose-amendment` are the only writers a research session uses; `pause`, `resume`, `replay-projection`, `import` are operator writers and print what they are about to do first. `resume --force` is the one path out of `reconciliation_required` (Data Flow step 12): it prints each blocking action id, calls `intents.cancel` on each, then advances the head. `propose-amendment` writes `ImprovementInvestigation(kind="charter_amendment", state="awaiting_authorization")` **and** a journal event `amendment_proposed` carrying the request digest on the case head, because the investigation row expires after 30 days (`models/improvement_investigation.py:99`) and an unanswered amendment must still be findable; it then resolves the `valor` project dict through `reflections.utilities.load_local_projects()` (`:100`) and sends Tom one message through `send_eng_telegram(project, message, logger_prefix=...)` (`reflections/utilities.py:454`, which takes the project dict, not a key). A missing `valor` entry is a refusal printed to stderr with exit 1 after the journal event and the investigation row have landed, so the amendment is on record even when the message cannot go out. `export` writes `{root}/exports/{utc_stamp}/{namespace.json, artifacts.json}` under `POPOTO_IMPROVEMENT_CONTENT_PATH` (lane 7's contract); `import` refuses a non-empty namespace unless `--force`.

#### Decision 10: file ownership across lanes

Owned solely by this lane: `tools/improvement_control/**`, `tools/paid_inference_meter.py`, `tools/vault_write.py`, `tools/improvement.py`, `reflections/improvement_intent_reconcile.py`, `.claude/skills/improve-research/SKILL.md`, `ui/templates/improvement/control.html`, `docs/features/session-recovery-mechanisms.md`, every `tests/**/test_improvement_control_*.py`, `tests/unit/test_paid_inference_meter.py`, `tests/unit/test_vault_write.py`, `tests/unit/test_improvement_cli.py`.

Shared, edited additively and named so the second lane rebases: `models/session_lifecycle.py` (status trio, step 7), `ui/data/sdlc.py:32`, `ui/data/improvement.py` (with lane 5), `models/improvement_evidence.py` (one tuple entry), `models/improvement_investigation.py` (one entry each in `INVESTIGATION_KINDS` and `INVESTIGATION_STATES`; lane 5 reads investigations for the digest), `config/settings.py` (three fields), `agent/session_health.py` (one helper), `scripts/update/reflection_register.py` and `scripts/update/run.py` (one registration each), `pyproject.toml` (one script), `docs/tools-reference.md`, `docs/features/improvement-controller.md` (with lane 4), `docs/plans/critiques/recursive-self-improvement-capability-matrix.md` (with lane 4), `tests/unit/test_improvement_models.py` (with lane 4), `tools/cross_vendor_judge.py` (two lines).

Never touched: `agent/agent_session_queue.py`, `agent/session_executor.py`, `models/agent_session.py`, `scripts/update/migrations.py`, `tools/infrastructure_budget.py`, `tools/improvement_eval/**`.

#### Decision 11: the eligibility cache stays process-local

Lane 2b left promoting `tools/improvement_eligibility.py`'s cache into the control namespace as this lane's call. The adapter calls `is_open_source` once per tick per case inside one process; nothing here needs the answer shared. Not promoted; recorded.

#### Decision 12: two fences, one per kind of writer

The lease generation and the intent binding fence different writers, and the plan's first draft conflated them (the critique's blocker).

- **Controllers** (the adapter tick, the reconcile pass, an operator's `pause`/`resume`, and the CLI's own `propose` write) each acquire the case lease and present the generation it minted for them. The accept rule `generation >= head.highest_accepted` in every effect script refuses a controller that stalled past its lease while a newer holder wrote. A generation is valid for the write it was minted for and nothing longer.
- **Research sessions** are not controllers. A session runs for minutes after admission, and any generation copied into it at admit time is stale the moment the adapter's own activation write (step 8) lands. So the session carries no generation. What binds it to the case is its **intent**: `intent:{action_id}` in state `running` with `agent_session_id` equal to the session's own id. `journal.transition(..., agent_session_id=...)` checks both fields inside the same Lua call as the head advance; any mismatch is `INTENT_STATE`. An intent that the reconcile pass moved to `reconciliation_required`, that an operator cancelled, or that was re-dispatched under a new action id, refuses the old session at the effect boundary, and the artifact it tried to submit is kept as evidence rather than lost.
- The CLI resolves its role from the environment: `AGENT_SESSION_ID` set means "I am a session" and the binding is mandatory; unset means "operator break-glass", the binding is skipped, and the lease generation is the only fence. `propose --action-id` given by an operator is recorded on the journal event but binds nothing.
- Two fault-injection tests replace the single "stale-generation rejection" test: **controller-level** (`test_stale_controller_generation_is_refused`: two `CaseLease` holders on one case, the newer one transitions, the older one's `transition` returns `STALE_GENERATION` and the journal length is unchanged) and **session-level** (`test_stale_session_intent_is_refused`: an intent bound to session S is moved to `reconciliation_required`, then re-dispatched under a new action id bound to S2; `propose` as S returns `INTENT_STATE`, the artifact lands as evidence, and `propose` as S2 is accepted).

#### Decision 13: the intent state machine is six states and one table

```python
INTENT_STATES = ("admitted", "materialized", "running", "settled", "cancelled", "reconciliation_required")
_ALLOWED: dict[str, frozenset[str]] = {
    "admitted": frozenset({"materialized", "reconciliation_required"}),
    "materialized": frozenset({"running", "reconciliation_required"}),
    "running": frozenset({"settled", "reconciliation_required"}),
    "reconciliation_required": frozenset({"cancelled"}),
    "settled": frozenset(),
    "cancelled": frozenset(),
}
```

Every intent CAS script (`admit` writes the initial state; `record_materialized`, `record_running`, `on_session_terminal`'s settle branch, `mark_reconciliation_required`, `cancel` move it) receives `ARGV.from_state` and returns `INTENT_STATE` when `HGET intent state ~= from_state`; the Python wrapper derives `from_state` from `_ALLOWED` so a caller cannot name an illegal pair. `prepared` and `result_recorded` are gone: `admit` writes `admitted` in the one script that also reserves the slot (a separate `prepare` would reopen the two-write window the single-script design exists to close), and `on_session_terminal` moves `running -> settled` directly. `cancelled` now has a writer (`intents.cancel`, Data Flow step 12), a journal event (`intent_cancelled`), and an exit (the case re-admits under a new action id). Terminal states are `settled` and `cancelled`; `reconciliation_required` is a holding state with exactly one exit. `test_improvement_control_intents.py::test_transition_table_is_enforced` parametrizes over `INTENT_STATES x INTENT_STATES` and asserts each pair not in `_ALLOWED` is refused with `INTENT_STATE` and writes nothing.

#### Decision 14: the CLI binary is venv-only, and every caller says so

`valor-improve` is a `pyproject.toml` console script and therefore lands in `.venv/bin` only; the repo's own instructions say `valor-*` tools are "inside the ~/src/ai project venv only, not on system PATH", and the research session runs `claude -p` under the worker with no promise that `.venv/bin` is on its PATH. Three places carry the consequence:

- `.claude/skills/improve-research/SKILL.md` writes every invocation as `"$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve" propose ...`, with the fallback `~/src/ai/.venv/bin/valor-improve` in the same sentence (the pattern `~/.claude/CLAUDE.md` prescribes for `valor-tts`). The Verification row `grep -c "valor-improve propose"` still matches.
- `tests/integration/test_improvement_control_cli.py` resolves the binary as `Path(sys.executable).parent / "valor-improve"` and asserts `binary.exists()` before the end-to-end run, so a missing console script fails as a named assertion rather than a "command not found" in a subprocess.
- `docs/tools-reference.md`'s `valor-improve` section states the venv-only path beside the other `valor-*` entries.

The alternative, prepending `<working_dir>/.venv/bin` to the harness PATH for research sessions in `agent/session_executor.py`, is rejected for this lane because that file is on the never-touched list (Decision 10) and lane 4 is already editing it.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `journal._control_redis()` boundary: `redis.exceptions.ConnectionError` and `TimeoutError` become `reason="UNAVAILABLE"` plus one `logger.warning`; `test_improvement_control_journal.py::test_unavailable_is_a_reason_code_not_an_exception` monkeypatches the alias to raise and asserts the result, the log record, and that no key was written.
- [ ] `finalize_session` step 7 `except Exception` — `tests/unit/test_session_lifecycle.py::test_finalize_survives_slot_release_failure` seeds a session with `action_id` provenance, monkeypatches `on_session_terminal` to raise, and asserts the session still reaches its terminal status and the DEBUG log line fires.
- [ ] `on_session_terminal` swallowing nothing: it returns `SlotReleaseResult(slot, intent_state, reason)` where `slot` is one of `released`, `absent`, `foreign_holder`. Only `foreign_holder` is a WARNING, because a slot held by someone else at a session's end is the Race 3 signature; `absent` is DEBUG, since a reconcile-forced finalize has already released the slot before step 7 runs. `test_reconcile_forced_finalize_logs_no_warning` asserts that path emits no WARNING record.
- [ ] `scheduler_adapter.tick` per-case `except Exception`: each case's failure is logged at WARNING with the case id and the tick continues; `test_tick_isolates_one_bad_case` seeds two cases, breaks one, asserts the other dispatched.
- [ ] `vault_write` `except subprocess.CalledProcessError` and `FileNotFoundError` (no `op` binary): both return `state="refused"` with a secret-free detail; `test_vault_write.py::test_refused_output_contains_no_credential_bytes` seeds a distinctive value and greps the result, the log capture, and the evidence row.
- [ ] `paid_inference_meter._read_cost` `except (AttributeError, KeyError, TypeError)` returns `None`; `settle_from_response` then tries `_estimate`, and a response with no `usage` at all (no tokens either) leaves the reservation open (reconcile receipts it `unknown`) and logs WARNING; tested with a response object lacking `usage`, one with `usage` but no `cost`, and one with `usage.cost`.
- [ ] `reflections/improvement_intent_reconcile.run_*` mirrors `run_improvement_collect`'s shape: per-intent try/except, counts returned in the result dict, never raises to the scheduler.

### Empty/Invalid Input Handling

- [ ] `propose` with an empty or whitespace `--payload` file, a case whose `ranking_rationale` is `None` or `""`, a `priority_area` outside `PRIORITY_AREAS`, or a `charter_digest` that is not the pinned one: each refuses with its reason code and writes nothing (journal length unchanged, artifact store untouched). One parametrized test.
- [ ] `transition` with `expected_revision=None`, a negative generation, an unknown event name, an empty `payload_digest`, or `agent_session_id` given without an `action_id`: `INVALID_ARGUMENT` before any Redis call.
- [ ] `admit` on a case with no head: `INTENT_STATE` (a case must be `investigating` or `experimenting` to admit). `admit` on a case with any `reconciliation_required` intent: `INTENT_STATE`, no slot consumed (`test_admit_refuses_while_reconciliation_required`). `resume` on a case that is not paused: prints "not paused" and exits 0 without a journal write. `resume` on a case with `reconciliation_required` intents and no `--force`: prints the action ids and exits 1 without a journal write. `cancel` on an intent in any state other than `reconciliation_required`: `INTENT_STATE`.
- [ ] `propose` under `AGENT_SESSION_ID` whose row has no `action_id` in `extra_context` (a session that is not a research session): refuses with `NOT_A_RESEARCH_SESSION` before touching the lease or the journal.
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
- [ ] `tests/unit/test_session_lifecycle_consolidation.py::test_thirteen_total_statuses` (`:418-424`) — UPDATE: `assert len(ALL_STATUSES) == 14` becomes `== 15` and the docstring's "5 terminal + 9 non-terminal" becomes "5 terminal + 10 non-terminal", naming `admitted` (#3215) beside `paused_budget` (#1821). `test_non_terminal_statuses` (`:395-407`) gains `"admitted"` in its set literal.
- [ ] `tests/unit/test_ui_sdlc_data.py` — UPDATE: the `ACTIVE_STATUSES` assertion gains `admitted`.
- [ ] `tests/unit/test_session_recovery_drip_budget.py` — UPDATE: add one case asserting an `admitted` session is never dripped to `pending` (same shape as the `paused_budget` case at `:96`).
- [ ] `tests/unit/test_improvement_models.py` — UPDATE: the `EVIDENCE_KINDS` assertion gains `resource_acquired`, which takes the tuple from 7 to 8 values, **exactly `DEFAULT_VOCABULARY_MAXIMUM = 8`** (`:95`; the check at `:174` is `2 <= len <= maximum`, so it passes with no headroom). No `VOCABULARY_MAXIMUMS` entry is added in this lane: eight kinds is the cap the tripwire was set at, and the next kind should have to argue for a ninth. Task 9 leaves a comment on the tuple's closing line saying so, so the lane that trips `test_declared_vocabularies_are_small` next reads the reason and brings a reasoned `VOCABULARY_MAXIMUMS[(ImprovementEvidence, "kind")]` entry with its kind. The `INVESTIGATION_KINDS` and `INVESTIGATION_STATES` pins (`:60-62`) gain `charter_amendment` and `awaiting_authorization` (6 and 5 values, well inside the default). Shared with lane 4; the second lane to land rebases.
- [ ] `tests/unit/test_ui_app.py` and the `ui/data/improvement.py` getter-list pin — UPDATE: the exact getter list gains `get_control_status`; the new partial renders with an empty namespace, a seeded paused case, and an unreachable namespace.
- [ ] `tests/unit/test_reflection_register.py` — UPDATE: `register_improvement_intent_reconcile` is idempotent, pinned to the `valor` owner, and carries `cadence="300s"`.
- [ ] `tests/unit/test_settings.py` — UPDATE: `ImprovementSettings` gains `lease_ttl_seconds=90`, `journal_max_entries=1000`, `max_dispatch_attempts=3`; `IMPROVEMENT__LEASE_TTL_SECONDS` overrides.
- [ ] `tests/unit/test_improvement_resources.py` — UPDATE: no existing test covers `_probe_vault_write` (`tools/improvement_resources.py:198-201`, a file-existence check). Add `test_vault_write_probe_reports_verified_once_the_writer_exists`, which asserts `probe()["vault_write"]["state"] == "verified"` now that `tools/vault_write.py` exists, so the probe's `absent` branch is the one that can no longer be reached on main.
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
**Mitigation:** `test_interim_lease_retired_when_redis_lease_exists` fails the suite the moment `models/redis_lease.py` exists, with the path anchored to the repo root via `Path(__file__).resolve().parents[2]` so the test bites from any cwd; the module docstring names the test, the swap point, and #3220; the plan's Documentation task writes the same instruction into the feature doc's dependency table; a comment on #3220 (Task 12) tells its builder exactly which edits close the hand-off, including deleting the retirement test and the lane-time Verification row that would otherwise become a permanent failure.

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
**Mitigation:** the next tick retries with the same key; `bind` hands back the same preallocated id whether or not the row was created (`agent/enqueue_idempotency.py:59-62`), and `_push_agent_session` creates the row only if it is missing. Test: monkeypatch `AgentSession.async_create` to raise once after `bind`, run the adapter twice, assert one row with the bound id and the intent at `materialized` with `attempts == 2` and `stale_sweeps` unset (`attempts` is the adapter's counter alone; the reconcile pass never touches it).

### Race 3: A lane slot outlives the session that held it
**Location:** `finalize_session` step 7; the `_ns:slots` hash.
**Trigger:** a watchdog, health sweep, or scheduler kill finalizes a research session on a path that never ran the ordinary completion code; or the row is deleted outright.
**Data prerequisite:** `action_id` in `extra_context`.
**State prerequisite:** the slot field for that action id exists and `max_concurrent_research_sessions == 1`, so nothing else can be admitted.
**Mitigation:** step 7 runs on every `finalize_session` path; the reconcile pass releases a slot whose intent has been `running` past the staleness threshold with a terminal or missing session row **on the first sweep that sees it** (no `stale_sweeps` budget applies to that branch: the session is gone, so waiting up to three more sweeps would only keep the single slot held). Test ("unreleased lane slot on restart"): seed a slot and a `running` intent whose `agent_session_id` has no row, run the reconcile pass once, assert the slot is free and the intent is `reconciliation_required`.

### Race 4a: A stalled controller writes under a replaced generation
**Location:** `journal.transition` and every intent script; any controller (adapter tick, reconcile pass, operator `pause`/`resume`, the CLI's `propose` write).
**Trigger:** controller A acquires generation g, stalls past lease expiry, B acquires g+1 and transitions, A wakes and writes with g.
**Data prerequisite:** the head's `highest_accepted`.
**State prerequisite:** `head.highest_accepted == g+1`.
**Mitigation:** the script refuses `g < highest_accepted` with `STALE_GENERATION` and writes nothing. The holder's own second write (`g == highest_accepted`) is accepted. Test (`test_stale_controller_generation_is_refused`, Decision 12): two `CaseLease` holders on one case, both branches.

### Race 4b: A research session submits after its intent was replaced
**Location:** `journal.transition` with `agent_session_id`; `valor-improve propose` under `AGENT_SESSION_ID`.
**Trigger:** session S is dispatched under action id A1; the reconcile pass judges A1 stale (worker outage, row missing) and moves it to `reconciliation_required`; an operator cancels it and the adapter re-dispatches under A2 bound to S2; S comes back and runs `propose`.
**Data prerequisite:** `action_id` in S's `extra_context`; `intent:{A1}.state` and `intent:{A1}.agent_session_id`.
**State prerequisite:** `intent:{A1}.state != "running"` (or its bound id is not S).
**Mitigation:** the session presents no generation (it would be stale on every run); the script compares the intent's state and bound session id inside the same call as the head advance and refuses `INTENT_STATE`. The CLI stores the artifact as `ImprovementEvidence(kind="other", detail="intent_state:<state>")` and exits 1; S2's `propose` under A2 is accepted. Test (`test_stale_session_intent_is_refused`, Decision 12).

### Race 5: Liveness check passes, worker dies before pickup
**Location:** `scheduler_adapter.activate`.
**Trigger:** `any_worker_alive()` is true, the session flips to `pending`, the worker exits before popping it.
**Data prerequisite:** none beyond the pending row.
**State prerequisite:** intent `running`, session `pending`, the activate notify published (Data Flow step 8).
**Mitigation:** the activate path publishes `publish_session_notify(session)` after the flip so a live worker picks the row up within one notify hop; pickup is immediate only because of that publish (the seam's create-time notify fired while the row was still `admitted` and found nothing). If the worker dies after the publish, this is the ordinary queue's problem and the ordinary health check's job ("starts workers for stalled `pending` sessions", `docs/features/session-recovery-mechanisms.md:33`); the intent's staleness threshold is the backstop. No new mechanism beyond the one publish call.

### Race 6: Reconcile pass and a live adapter tick touch one intent
**Location:** `intents.mark_reconciliation_required` and `scheduler_adapter.materialize`.
**Trigger:** the reconcile reflection judges an `admitted` intent stale while a delayed tick is materializing it.
**Data prerequisite:** the intent's `state` and `updated_ts`.
**State prerequisite:** both run under their own lease generation.
**Mitigation:** both are CAS scripts on the intent's `state` with an explicit `from_state` (`admitted` for both, Decision 13); whichever lands second finds the state moved and gets `INTENT_STATE`. A materialize that loses releases nothing (it held nothing new); a reconcile that loses leaves the slot with the live intent. Test: `test_reconcile_and_materialize_race_leaves_one_winner`.

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

- New CLI entry point `valor-improve = "tools.improvement:main"` in `pyproject.toml [project.scripts]`, subcommands `case show`, `case explain`, `propose`, `propose-amendment`, `budget`, `release compare`, `pause`, `resume`, `doctor`, `export`, `import`, `replay-projection`. A research session reaches research state only through `propose`, which acquires the case lease, presents its intent binding (`action_id` from its row's `extra_context`, its own `AGENT_SESSION_ID`), writes one `action_proposed` journal event under journal authorization, and never exposes a raw transition. `--json` on every subcommand for the agent; the human format is the default.
- The console script lives in `.venv/bin` only (Decision 14). The research skill `.claude/skills/improve-research/SKILL.md` (project-only, never synced) writes every invocation as `"$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve" propose ...` (fallback `~/src/ai/.venv/bin/valor-improve`) and instructs the session to read the bounded brief it was given, use `WebSearch`/`WebFetch`, write hypotheses and proposed actions through `valor-improve propose`, never run `valor-session create`, never send a message, and never ask a human anything. The one outbound message class is `valor-improve propose-amendment`, which records the amendment digest on the case journal, writes the `ImprovementInvestigation(kind="charter_amendment", state="awaiting_authorization")` row, and sends Tom one plain Telegram message through `reflections/utilities.py::send_eng_telegram`.
- The bridge imports nothing new; the poll registry, `tools/ask_poll.py`, `bridge/poll_registry.py`, and `bridge/answer_routing.py` are untouched.
- `models/session_lifecycle.py::finalize_session` gains one lazy-imported, exception-isolated call into `tools.improvement_control.intents.on_session_terminal(session, status)`, gated on the session carrying `action_id` provenance, so a research session's lane slot is released on every termination path.
- Integration tests: `tests/integration/test_improvement_control_cli.py` resolves the binary as `Path(sys.executable).parent / "valor-improve"`, asserts it exists, then runs `propose` end to end against a claimed test db (with `AGENT_SESSION_ID` set to a seeded research row) and asserts the journal shows the event; a session on the research path attempting `valor-session create --parent` receives `ChildSessionsDisabledError`'s message; `valor-improve doctor` on a seeded paused case prints the paused head and its outstanding reservation.

## Documentation

- [ ] Update `docs/features/improvement-controller.md`: replace the "lane 3" placeholders in "Control namespace contract", "Dispatch", "Break-glass", and "Dependency on #3183" with the shipped shapes (key layout, reason codes, intent lifecycle, the lease protocol and its #3220 hand-off, unit-2 metering, the vault writer), and correct the unit-3 sentence at `:224-225` to the recorded decision (the counter stays on its key).
- [ ] Update `docs/features/session-recovery-mechanisms.md`: add `admitted` to the status inventory with its owner (`reflection`, the `improvement-intent-reconcile` pass), state that the health check, startup recovery, and the resume drip never touch it, and describe the reconcile pass as mechanism 8 in the same table shape.
- [ ] Update `docs/tools-reference.md`: the `valor-improve` section drops "planned" and lists the shipped subcommands with `--json`.
- [ ] Update `docs/features/adding-reflection-tasks.md` (or its registration section) with the second improvement registration, `improvement-intent-reconcile`.
- [ ] Update `docs/features/redis-models.md`: the `improve:*` namespace exception and its private-alias rule, cross-linked to the feature doc.
- [ ] Create `.claude/skills/improve-research/SKILL.md` (project-only skill text is documentation the agent reads).
- [ ] Add a lane-3 section to `docs/plans/critiques/recursive-self-improvement-capability-matrix.md`.

## Success Criteria

- [ ] `valor-improve` exists as a console script, `tools/improvement.py` is referenced from `pyproject.toml`, and `.claude/skills/improve-research/SKILL.md` references `valor-improve propose`
- [ ] The fault-injection tests pass: stale-generation rejection at an effect boundary split into its two writers, controller-level `test_stale_controller_generation_is_refused` (Race 4a, `STALE_GENERATION`) and session-level `test_stale_session_intent_is_refused` (Race 4b, `INTENT_STATE`); a crash between admission and session creation (Race 2); an unreleased lane slot on restart (Race 3); and journal unavailability with the break-glass recovery exercised end to end (`doctor` reports unreachable, namespace restored, `doctor` reads the pre-outage heads)
- [ ] `reconciliation_required` has exactly one exit: `admit` refuses `INTENT_STATE` while any such intent exists on the case, `resume` without `--force` exits 1 naming the action ids, `resume --force` cancels them (journal `intent_cancelled`) and the next tick admits under a new action id (each asserted)
- [ ] `INTENT_STATES` has six values, every intent script takes `from_state`, and `test_transition_table_is_enforced` refuses every pair outside `_ALLOWED`
- [ ] The session's `extra_context` carries `research_case_id`, `experiment_id`, `action_id`, `idempotency_key` and no `generation` (asserted on the recorded `extra_context_overrides`)
- [ ] `admitted` is in `NON_TERMINAL_STATUSES`, `RECOVERY_OWNERSHIP`, and `ACTIVE_STATUSES` in one commit, `tests/unit/test_recovery_ownership.py` is green, and `admitted` is never selected by the worker, the health check, startup recovery, or the resume drip (each asserted)
- [ ] Two ticks admitting the same case produce exactly one dispatch and one `AgentSession` row (Race 1)
- [ ] Activation publishes `publish_session_notify(session)` exactly once, after the row is `pending`, and never on the no-live-worker path (asserted on the recorded call)
- [ ] The happy path settles: a session that proposes finalizes with its intent `settled` and `result_digest == payload_digest`; a session that completes without proposing settles `no_proposal`; only `failed`/`killed`/`abandoned` leave the intent `running` for the sweep (three tests, Task 4)
- [ ] `attempts` is written only by `record_materialized` and `stale_sweeps` only by the reconcile pass; a `running` intent whose row is terminal or missing is released on the first sweep past the age threshold
- [ ] A reconcile-forced `finalize_session` emits no WARNING (`slot == "absent"` is DEBUG; only `foreign_holder` warns)
- [ ] `valor-improve doctor` on a seeded paused case prints the paused head and its outstanding reservation
- [ ] A session on the research path attempting `valor-session create --parent` receives the existing `ChildSessionsDisabledError` message
- [ ] The seam and the lease are consumed with no change to `agent/agent_session_queue.py`; `LeaseProtocol` matches #3220's three declared calls and the conformance suite passes against `CaseLease`
- [ ] `tools/paid_inference_meter.py` settles a call from `usage.cost` (`metering="exact"`), marks a token-only response `metering="estimated"`, receipts an unsettled reservation `metering="unknown"` on reconcile, never counts `purpose="sdlc_review"` against the pool, and imports no HTTP client
- [ ] `propose-amendment` writes a `charter_amendment` investigation in `awaiting_authorization` (both values in the model's declared tuples) and an `amendment_proposed` journal event carrying the request digest
- [ ] `tools/vault_write.py` writes a `resource_acquired` row with title and fingerprint, emits no credential byte on any path, and `render_resource_acquired_section` renders a seeded row
- [ ] `valor-improve budget` prints all three units with window boundaries, and unit 3's figures match `tools.infrastructure_budget.status_dict`
- [ ] `export` then `import` into an empty namespace round-trips a seeded case byte-for-byte on the head and journal
- [ ] `docs/features/session-recovery-mechanisms.md` documents `admitted` and the reconcile pass; `docs/tools-reference.md` no longer says "planned"
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead orchestrates only. One worktree (`.worktrees/sdlc-3215`, branch `session/sdlc-3215`), a declared file split per builder (Decision 10), commits announced before push, and the mutation review runs in its own checkout.

### Team Members

- **Builder (substrate)**
  - Name: substrate-builder
  - Role: `tools/improvement_control/{__init__,keys,journal,lease,intents,projection}.py`, the status trio, `finalize_session` step 7, `config/settings.py` fields, and their unit tests
  - Agent Type: builder
  - Domain: Redis/Popoto data, async/concurrency
  - Resume: true

- **Builder (dispatch and recovery)**
  - Name: dispatch-builder
  - Role: `scheduler_adapter.py`, `recovery.py`, `reflections/improvement_intent_reconcile.py`, `agent/session_health.py::any_worker_alive`, the two registrations, the four fault-injection tests
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Builder (money and vault)**
  - Name: budget-builder
  - Role: `tools/paid_inference_meter.py`, `tools/vault_write.py`, the `resource_acquired` kind, the judge's record-only call, their tests
  - Agent Type: builder
  - Domain: security/untrusted-input
  - Resume: true

- **Builder (surfaces)**
  - Name: surface-builder
  - Role: `tools/improvement.py`, `pyproject.toml`, `export.py`, the research skill, `ui/data/improvement.py::get_control_status`, the control partial, integration tests
  - Agent Type: builder
  - Resume: true

- **Test engineer (mutation)**
  - Name: fence-mutator
  - Role: mutate each script's generation compare, revision compare, and compare-and-delete one at a time in a private worktree and confirm a named test fails for each; report the matrix
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: lane3-validator
  - Role: run the Verification table, the prerequisite checker, and the Success Criteria; read-only
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: lane3-docs
  - Role: the Documentation section
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Keys, journal, and the transition script
- **Task ID**: build-journal
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_control_journal.py` (create)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/improvement_control/__init__.py` with the package docstring (namespace, private alias rule, reason-code vocabulary, #3220 hand-off).
- `keys.py`: `SCHEMA_VERSION = 1`, builders for every key in Decision 2, and `assert_control_key(key)` raising on any key outside the `improve:` prefix.
- `journal.py`: `_control_redis()` over `utils.redis_client.text_redis()`; `ensure_schema(project_key)`; `read_head(project_key, case_id) -> Head | None`; `journal_tail(project_key, case_id, n)`; `transition(project_key, case_id, *, expected_revision, generation, action_id, event, payload_digest, agent_session_id=None) -> TransitionResult` as one `EVAL` (schema, pause, `generation >= highest_accepted`, `expected_revision == revision`, and when `agent_session_id` is given the intent binding compare `HGET intent:{action_id} state == "running" and HGET intent:{action_id} agent_session_id == ARGV.agent_session_id` else `INTENT_STATE`; then, after the binding compare passes and before the `RPUSH`, `redis.call("HSET", intent_key, "result_digest", ARGV.payload_digest, "updated_ts", now)`, the sole writer of `result_digest`; then head advance, `RPUSH` + `LTRIM` to `journal_max_entries`, one `TIME` read); `pause(project_key, case_id | None, reason, by)` and `resume(...)` as transitions with events `paused`/`resumed`; connection errors → `UNAVAILABLE`.
- Add `lease_ttl_seconds=90`, `journal_max_entries=1000`, `max_dispatch_attempts=3` to `ImprovementSettings` with `Field(description=...)` sentences; update `tests/unit/test_settings.py`.
- Tests: accept and every reason code; bounded journal; unavailability (Failure Path); `INVALID_ARGUMENT` on empty inputs and on `agent_session_id` without `action_id`; the holder's own second write accepted; `test_stale_controller_generation_is_refused` (Race 4a: two `CaseLease` holders, the older one's write is `STALE_GENERATION` and the journal length is unchanged); the intent binding compare refusing a wrong state and a wrong session id, each separately (Race 4b's script half; the end-to-end half is Task 10); an accepted session-bound `transition` leaves `intent.result_digest == payload_digest` and a refused one leaves it unset.

### 2. Lease protocol and interim lease
- **Task ID**: build-lease
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_control_lease.py` (create)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: true
- First run `gh issue view 3220 --json state -q .state`. If `CLOSED` and `models/redis_lease.py` exists, implement `default_lease()` over it and skip `CaseLease`; the conformance suite still applies.
- `lease.py`: `LeaseProtocol` (`acquire(key, ttl) -> int | None`, `renew(key, generation) -> bool`, `release(key, generation) -> bool`), `CaseLease` (three Lua scripts: generation from `INCR` of `{key}:gen`, expiry from `TIME`, renew and release compare the stored generation), `default_lease()` as the single swap point, module docstring naming #3220 and the retirement test.
- Tests: the conformance suite (increasing generations, re-acquire after lapse, stale renew and release return False and change nothing, compare-and-delete leaves a newer holder's lease intact), `assert_control_key` refusing `lease:session:x`, and `test_interim_lease_retired_when_redis_lease_exists` with the path anchored as `Path(__file__).resolve().parents[2] / "models" / "redis_lease.py"`.

### 3. The `admitted` status trio (one commit)
- **Task ID**: build-admitted-status
- **Depends On**: none
- **Validates**: `tests/unit/test_recovery_ownership.py`, `tests/unit/test_session_lifecycle_consolidation.py`, `tests/unit/test_ui_sdlc_data.py`, `tests/unit/test_session_recovery_drip_budget.py`, `tests/unit/test_improvement_control_admitted.py` (create)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: true
- `models/session_lifecycle.py:74` `NON_TERMINAL_STATUSES` gains `"admitted"` with its comment; `:92` `RECOVERY_OWNERSHIP["admitted"] = "reflection"`; `ui/data/sdlc.py:32` gains `"admitted"`. Update the four listed tests (Test Impact) in the same commit.
- `test_improvement_control_admitted.py`: `admitted` not in `RESUMABLE_STATUSES`; the resume drip leaves an `admitted` row untouched; `worker/__main__.py`'s pending query and `_agent_session_health_check`'s queries exclude it (assert by seeding one `admitted` row and running the selection code).
- Commit message: `Add the admitted session status: lifecycle, ownership, dashboard, in one change (Refs #3215)`.

### 4. Intents, slots, and the terminal hook
- **Task ID**: build-intents
- **Depends On**: build-journal, build-lease, build-admitted-status
- **Validates**: `tests/unit/test_improvement_control_intents.py` (create), `tests/unit/test_session_lifecycle.py` (update)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: false
- `intents.py`: `DispatchIntent` dataclass, `INTENT_STATES` (six values) and the module-level `_ALLOWED` transition table exactly as Decision 13; `admit` (one script: `INTENT_STATE` if any intent on the case is `reconciliation_required`, then slot count, revision, generation, intent write in state `admitted` with `generation` as provenance, slot reservation, journal `intent_admitted`), `record_materialized` (`admitted -> materialized`, writes `agent_session_id`, `HINCRBY attempts 1` on every call including a retry that finds the intent already `materialized` under the same bound id; this is the only writer of `attempts`), `record_running` (`materialized -> running`), `cancel(project_key, case_id, action_id, *, generation, by)` (`reconciliation_required -> cancelled`, idempotent slot `HDEL`, journal `intent_cancelled`), `mark_reconciliation_required` (`from_state` in `{admitted, materialized, running}`, slot release, journal event; never touches `attempts`), `on_session_terminal(session, status) -> SlotReleaseResult(slot, intent_state, reason)` exactly as Decision 6 (slot compare-and-delete returning `released`/`absent`/`foreign_holder`; `running -> settled` when `result_digest` is set, `running -> settled` with `reason="no_proposal"` when unset and `status == "completed"`, otherwise `session_terminal_at` stamped and `running` kept; WARNING only on `foreign_holder`), `dead_letter_exhausted(intent)` writing `DeadLetter(stage="improvement-intent", payload_json=..., replayable=False)`. Every CAS script takes `ARGV.from_state`; the Python wrapper derives it from `_ALLOWED`.
- `models/session_lifecycle.py::finalize_session` step 7 exactly as Decision 6.
- Tests: Race 1 (two admits, one intent, one slot); `test_admit_refuses_while_reconciliation_required` (one such intent seeded, `INTENT_STATE`, `HLEN slots` unchanged); slot exhausted at `max_concurrent_research_sessions`; `test_transition_table_is_enforced` (parametrized over `INTENT_STATES x INTENT_STATES`, every pair outside `_ALLOWED` refused with `INTENT_STATE` and no write); `test_cancel_is_the_only_exit_from_reconciliation_required`; Race 6 (`test_reconcile_and_materialize_race_leaves_one_winner`); compare-and-delete on slot release refusing a foreign action id (`slot == "foreign_holder"`, one WARNING record) and reporting `absent` at DEBUG when the field is already gone (`test_reconcile_forced_finalize_logs_no_warning`); the three settle-branch tests: `test_propose_then_finalize_settles_with_result_digest` (session-bound `transition` then `on_session_terminal(status="completed")` → `settled`, `result_digest == payload_digest`, slot released), `test_completed_without_propose_settles_as_no_proposal` (`settled`, `reason == "no_proposal"`, slot released), `test_killed_without_propose_stays_running_for_the_sweep` (`running`, `session_terminal_at` stamped, slot released, and a subsequent `reconcile()` moves it to `reconciliation_required`); `test_finalize_survives_slot_release_failure`; `test_finalize_without_provenance_never_imports_control` (assert `sys.modules` lacks the package after finalizing a plain session in a subprocess); dead letter on exhaustion.

### 5. Projection, replay, export, import
- **Task ID**: build-projection-export
- **Depends On**: build-intents
- **Validates**: `tests/unit/test_improvement_control_projection.py` (create), `tests/unit/test_improvement_control_export.py` (create)
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: true
- `projection.py`: `apply(project_key, case_id)` (ORM `save()` of `state`, `revision`, `updated_at` from the head; never reads the projection first), `replay(project_key, case_id) -> ReplayResult` (the head is authoritative: fold the journal tail, compare the fold to the head, write the head's truth to the projection, and return the diff plus a `fold_reached_head: bool`; a tail trimmed by `LTRIM` to `journal_max_entries` that cannot reach the head's revision is reported as `fold_reached_head=False` with the first folded revision named, never raised, and the projection is still reconciled to the head).
- `export.py`: `export_namespace(project_key, root) -> Path` writing `namespace.json` (schema, heads, journals, intents, slots, unit-2 keys) and `artifacts.json` (digests and paths under `POPOTO_IMPROVEMENT_CONTENT_PATH`); `import_namespace(archive, *, force=False)` refusing a non-empty namespace or a schema mismatch.
- Tests: a direct `ImprovementCase.save()` with a wrong state is corrected by `replay`; a case with more than `journal_max_entries` events (seeded with `journal_max_entries=5`) replays to the head's state with `fold_reached_head=False` and no exception; round trip of a seeded case is byte-equal on head and journal; import refuses non-empty and schema mismatch.

### 6. Scheduler adapter and the controller tick
- **Task ID**: build-dispatch
- **Depends On**: build-intents
- **Validates**: `tests/unit/test_improvement_control_dispatch.py` (create), `tests/unit/test_session_health_worker_liveness.py` (create)
- **Assigned To**: dispatch-builder
- **Agent Type**: builder
- **Parallel**: true
- `agent/session_health.py::any_worker_alive() -> bool`: scan `WORKER_REGISTERED_PID_KEY_PREFIX*`, return True on the first pid whose `_worker_pid_heartbeat_fresh` is True; no other change to the module.
- `scheduler_adapter.py`: `tick(project_key, *, lease=None, push=None, now=None) -> TickResult` iterating open cases (`OPEN_CASE_STATES`), acquiring the case lease, admitting each unadmitted `action_proposed` after the four checks (charter digest pinned, journal authorization, reservation available, action type allowed), materializing through `_push_agent_session(idempotency_key=f"improve:{p}:{c}:{action_id}", status="admitted", session_type="eng", chat_id="0", telegram_message_id=0, sender_name="improvement-controller", extra_context_overrides={...})` via `asyncio.run` from the sync reflection, activating only after `any_worker_alive()`, and releasing the lease in `finally`. Activation is three calls in this order: `transition_status(session, "pending", reason="improvement-controller activate")`, then `publish_session_notify(session)` (imported as `from agent.agent_session_queue import publish_session_notify`; a call into the module, not an edit of it), then `intents.record_running(...)`. The publish runs strictly after the save so the woken loop's `status="pending"` query finds the row. Per-case exception isolation.
- `reflections/improvement_controller_tick.py::run_improvement_controller_tick()` (sync, returns counts), registered as `improvement-controller-tick` with `cadence=f"{controller_tick_seconds}s"` through a new `register_improvement_controller_tick` in `reflection_register.py` and a call in `run.py`. Guarded by `ImprovementSettings.enabled`.
- Tests: Race 2 (crash after bind, retry yields one row, `attempts == 2`, `stale_sweeps` unset); `test_activate_publishes_notify_once_after_the_flip` (monkeypatch `agent.agent_session_queue.publish_session_notify` with a recorder that asserts the row's status is already `pending` when it is called, and assert exactly one call per activation and zero calls on the no-live-worker path); no live worker leaves `materialized`, publishes nothing, and journals the reason; a second tick on an already-`running` intent is a no-op; a case with a `reconciliation_required` intent is skipped with the reason journaled once, not every tick; the research path never passes `parent_agent_session_id` and the recorded `extra_context_overrides` carries exactly `research_case_id`, `experiment_id`, `action_id`, `idempotency_key` and no `generation` (assert the recorded call kwargs).

### 7. Reconcile reflection
- **Task ID**: build-recovery
- **Depends On**: build-dispatch
- **Validates**: `tests/unit/test_improvement_control_recovery.py` (create), `tests/unit/test_reflection_register.py` (update)
- **Assigned To**: dispatch-builder
- **Agent Type**: builder
- **Parallel**: false
- `recovery.py::reconcile(project_key, *, now, lease_ttl) -> ReconcileResult` branches on intent state, with `age = now - updated_ts` and the threshold `4 * lease_ttl_seconds`:
  - `state in {admitted, materialized}` and `age > threshold` → `HINCRBY stale_sweeps 1` (the reconcile pass's own counter; `attempts` belongs to `record_materialized` and is never read or written here); when `stale_sweeps >= max_dispatch_attempts` → `mark_reconciliation_required` + slot release + `finalize_session(row, "abandoned", reason=..., dead_letter_stage="improvement-intent")` when a bound row exists (its step 7 then reports `slot == "absent"` at DEBUG); otherwise leave it for the next sweep.
  - `state == running` and `(row is None or row.status in TERMINAL_STATUSES)` and `age > threshold` → act on this sweep: `mark_reconciliation_required` + slot release. No counter applies; the session that held the slot is gone.
  - `state == running` with a live, non-terminal row → untouched, whatever its age.
  - unit-2 reservations whose day closed unsettled → release + `spend_receipt` with `metering="unknown"`.
- `reflections/improvement_intent_reconcile.py::run_improvement_intent_reconcile()`; `register_improvement_intent_reconcile` (`cadence="300s"`) and its `run.py` call; the register test.
- Tests: Race 3 (unreleased slot on restart, freed after one pass); the crash-between-admission-and-creation case swept only once `stale_sweeps` reaches `max_dispatch_attempts` (three passes, `attempts` unchanged throughout); a `running` intent with a terminal row is acted on by the first pass while a `running` intent with a live row past the threshold is untouched; a fresh `admitted` intent is left alone; a `reconciliation_required` intent is never touched again by the pass (only `cancel` moves it); the reconcile-forced `finalize_session` emits no WARNING (`slot == "absent"`); the unknown-metering receipt.

### 8. Unit-2 meter and the judge's record-only receipt
- **Task ID**: build-meter
- **Depends On**: build-journal
- **Validates**: `tests/unit/test_paid_inference_meter.py` (create), `tests/unit/test_cross_vendor_judge.py` (update if it exists)
- **Assigned To**: budget-builder
- **Agent Type**: builder
- **Parallel**: true
- `tools/paid_inference_meter.py` per Decision 7: `settle_from_response` is `_read_cost` then `_estimate`, two branches, no HTTP client import and no OpenRouter URL anywhere in the module. `PRICE_TABLE` entries carry `usd_per_mtoken_in`, `usd_per_mtoken_out`, and the module-level `PRICE_TABLE_RETRIEVED_AT` date and source URL.
- `tools/cross_vendor_judge.py`: after the usage log at `:263`, `record_receipt(project_key="valor", purpose="sdlc_review", model=..., prompt_tokens=..., completion_tokens=..., metering="estimated")`; the judge holds no project key of its own (it reviews any repository) and the receipt belongs to Valor's pool, so the key is pinned; no other change.
- Tests: exact settlement from `usage.cost` (attribute form and mapping form); estimated from tokens when `cost` is absent; a response with neither leaves the reservation open; `purpose="sdlc_review"` never reduces headroom and the judge's recorded receipt row carries `project_key == "valor"`; two concurrent reservations whose sum exceeds the pool admit exactly one; window attribution across midnight UTC; `INVALID_AMOUNT`.

### 9. Vault writer and the `resource_acquired` kind
- **Task ID**: build-vault
- **Depends On**: none
- **Validates**: `tests/unit/test_vault_write.py` (create), `tests/unit/test_improvement_models.py` (update), `tests/unit/test_improvement_resources.py` (update)
- **Assigned To**: budget-builder
- **Agent Type**: builder
- **Parallel**: true
- `models/improvement_evidence.py`: append `"resource_acquired"` with the ownership comment satisfied, and a comment on the tuple's closing line: `# 8 kinds: exactly DEFAULT_VOCABULARY_MAXIMUM in tests/unit/test_improvement_models.py; the next kind brings a reasoned VOCABULARY_MAXIMUMS[(ImprovementEvidence, "kind")] entry (#3215)`.
- `models/improvement_investigation.py`: append `"charter_amendment",  # a deferred decision that needs Tom's authorization (lane 3, #3215)` to `INVESTIGATION_KINDS` and `"awaiting_authorization",  # sent to Tom, silence is not approval (lane 3, #3215)` to `INVESTIGATION_STATES`; update the pins at `tests/unit/test_improvement_models.py:60-62`. Owned here rather than in Task 10 so the vocabulary lands with the other model edit and Task 10 only consumes it.
- `tests/unit/test_improvement_resources.py::test_vault_write_probe_reports_verified_once_the_writer_exists` (Test Impact).
- `tools/vault_write.py` per Decision 8, with an injectable `runner` like `tools/improvement_resources.py` so unit tests never call `op`; `render_resource_acquired_section(rows) -> str`.
- `tests/integration/test_vault_write_integration.py`: skipped with a named reason unless `OP_CACHE=false op whoami` succeeds; when it runs, creates and then deletes one item titled `test-lane3-<uuid>` in `m-valor`.
- Tests: no credential byte in result, logs, or evidence (seeded distinctive value); refusal on missing `op`, non-zero exit, empty inputs; the digest section renders one seeded row.

### 10. `valor-improve` CLI and the research skill
- **Task ID**: build-cli
- **Depends On**: build-projection-export, build-dispatch, build-meter, build-vault
- **Validates**: `tests/unit/test_improvement_cli.py` (create), `tests/integration/test_improvement_control_cli.py` (create)
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: false
- `tools/improvement.py::main` with the twelve subcommands and `--json`; `pyproject.toml` script; `propose` refusals per Data Flow step 1, resolving its session through `AGENT_SESSION_ID` and presenting the intent binding when set (`NOT_A_RESEARCH_SESSION` when the row carries no `action_id`), operator break-glass when unset; `propose-amendment` writing the `amendment_proposed` journal event with the request digest, the `ImprovementInvestigation(kind="charter_amendment", state="awaiting_authorization")` row (vocabulary from Task 9), then the `valor` project dict resolved through `reflections.utilities.load_local_projects()` and one `send_eng_telegram(project, message, logger_prefix="[improve]")` call (Decision 9; a missing `valor` entry exits 1 after the journal event and row have landed); `resume` refusing with the blocking action ids, `resume --force` cancelling each through `intents.cancel` before the head transition; `budget` composing unit 1 (slots), unit 2 (meter), unit 3 (`infrastructure_budget.status_dict`) with window boundaries; `doctor` per Error State Rendering, listing `reconciliation_required` intents by case.
- `.claude/skills/improve-research/SKILL.md`: the brief contract, the `WebSearch`/`WebFetch` instruction, `"$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve" propose` (fallback `~/src/ai/.venv/bin/valor-improve`) as the only write, the three nevers.
- Integration tests (`tests/integration/test_improvement_control_cli.py`): resolve `Path(sys.executable).parent / "valor-improve"` and assert it exists first; `propose` end to end under a seeded `AGENT_SESSION_ID` shows the journal event; `test_stale_session_intent_is_refused` (Race 4b end to end: the old session's `propose` returns `INTENT_STATE` and its artifact is stored as evidence, the re-dispatched session's `propose` is accepted); `resume --force` on a case with a `reconciliation_required` intent journals `intent_cancelled` then `resumed`; `valor-session create --parent` on the research path receives `ChildSessionsDisabledError`'s message; `doctor` on a seeded paused case; the outage drill (Success Criteria).

### 11. Dashboard control panel
- **Task ID**: build-dashboard
- **Depends On**: build-intents
- **Validates**: `tests/unit/test_ui_app.py` (update), `tests/unit/test_ui_improvement_data.py` (update or create)
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: true
- `ui/data/improvement.py::get_control_status(project_key)` returning intents by state, slots, unit-2 status, paused heads, and `reconciliation_required` intents, with the getter-list pin updated; `ui/templates/improvement/control.html` with the three-state rendering; the inline route in `ui/app.py` beside the goals partial.

### 12. Mutation review of the fences
- **Task ID**: validate-fences
- **Depends On**: build-cli, build-recovery
- **Assigned To**: fence-mutator
- **Agent Type**: test-engineer
- **Parallel**: false
- In a private worktree: for each script (`transition`, `admit`, `record_materialized`, `record_running`, `release_slot`, `cancel`, `reserve_unit2`, `settle_unit2`, `mark_reconciliation_required`, lease `renew`/`release`), flip the generation compare to `>`, then delete the revision compare, then make the compare-and-delete unconditional, then delete the `from_state` compare; and for `transition` alone, two further rows: delete the intent `state == "running"` compare, then delete the intent `agent_session_id` compare. Record which named test fails for each mutation. Any mutation with no failing test is a blocker with the test to add.
- Post a comment on #3220 naming `LeaseProtocol`, `default_lease()`, `test_interim_lease_retired_when_redis_lease_exists`, and the lane-time Verification row "Anti-criterion: no second general lease" as the edits that close the hand-off (delete `CaseLease`, repoint the factory, delete the test and the row in the same PR).

### 13. Documentation
- **Task ID**: document-feature
- **Depends On**: build-cli, build-dashboard, build-recovery
- **Assigned To**: lane3-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Every item in the Documentation section; rebase onto lane 4's feature-doc edits if they have landed.

### 14. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-fences, document-feature
- **Assigned To**: lane3-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table, the prerequisite checker, and every Success Criterion; report pass/fail with evidence.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lane tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_control_journal.py tests/unit/test_improvement_control_lease.py tests/unit/test_improvement_control_admitted.py tests/unit/test_improvement_control_intents.py tests/unit/test_improvement_control_projection.py tests/unit/test_improvement_control_export.py tests/unit/test_improvement_control_dispatch.py tests/unit/test_improvement_control_recovery.py tests/unit/test_paid_inference_meter.py tests/unit/test_vault_write.py tests/unit/test_improvement_cli.py tests/unit/test_recovery_ownership.py tests/unit/test_session_lifecycle_consolidation.py tests/unit/test_ui_sdlc_data.py tests/unit/test_session_recovery_drip_budget.py tests/unit/test_improvement_models.py tests/unit/test_improvement_resources.py tests/unit/test_reflection_register.py tests/unit/test_settings.py tests/unit/test_session_lifecycle.py tests/unit/test_ui_app.py -q` | exit code 0 |
| Integration tests pass | `scripts/pytest-clean.sh tests/integration/test_improvement_control_cli.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/improvement_control tools/improvement.py tools/paid_inference_meter.py tools/vault_write.py reflections/improvement_intent_reconcile.py reflections/improvement_controller_tick.py models/session_lifecycle.py ui/data/improvement.py ui/data/sdlc.py agent/session_health.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/improvement_control tools/improvement.py tools/paid_inference_meter.py tools/vault_write.py reflections/improvement_intent_reconcile.py reflections/improvement_controller_tick.py` | exit code 0 |
| Console script declared | `grep -c 'valor-improve = "tools.improvement:main"' pyproject.toml` | output > 0 |
| Skill references propose | `grep -c "valor-improve propose" .claude/skills/improve-research/SKILL.md` | output > 0 |
| Status trio landed together | `grep -c '"admitted"' models/session_lifecycle.py ui/data/sdlc.py` | output contains models/session_lifecycle.py:2 |
| Dashboard active set has admitted | `grep -c '"admitted"' ui/data/sdlc.py` | output > 0 |
| Lease protocol matches #3220 | `grep -cE "def (acquire|renew|release)\(" tools/improvement_control/lease.py` | output > 2 |
| Accept rule is `>=` in every effect script | `grep -c "highest_accepted" tools/improvement_control/journal.py tools/improvement_control/intents.py` | output contains journal.py |
| Reconcile registered | `grep -c "register_improvement_intent_reconcile" scripts/update/run.py scripts/update/reflection_register.py` | output contains run.py:1 |
| Evidence kind added | `grep -c '"resource_acquired"' models/improvement_evidence.py` | output > 0 |
| Recovery doc names admitted | `grep -c "admitted" docs/features/session-recovery-mechanisms.md` | output > 0 |
| Tools reference no longer says planned | `grep -c "planned, lane 3" docs/tools-reference.md` | match count == 0 |
| Anti-criterion: queue seam untouched | `git diff --stat main -- agent/agent_session_queue.py agent/session_executor.py models/agent_session.py scripts/update/migrations.py tools/infrastructure_budget.py \| grep -c "|"` | match count == 0 |
| Anti-criterion: no second general lease (lane-time only; #3220's builder deletes this row with `CaseLease`) | `gh issue view 3220 --json state -q .state \| grep -q CLOSED \|\| { ls models/redis_lease.py 2>/dev/null \| wc -l; }` | output empty or 0 (the row passes vacuously once #3220 is CLOSED) |
| Intent vocabulary is six states with a transition table | `grep -c "reconciliation_required" tools/improvement_control/intents.py; grep -cE "^(_ALLOWED|INTENT_STATES) *[:=]" tools/improvement_control/intents.py` | second output == 2 |
| Session carries no generation | `grep -c '"generation"' tools/improvement_control/scheduler_adapter.py` | match count == 0 (the generation is written to the intent hash inside `intents.admit`, never into `extra_context_overrides`) |
| Activation wakes the worker | `grep -c "publish_session_notify" tools/improvement_control/scheduler_adapter.py` | output > 1 (one import, one call) |
| `result_digest` has one writer, the transition script | `grep -rl 'HSET.*result_digest' tools/improvement_control/ \| wc -l` | output == 1 (`journal.py`; `intents.py` only `HGET`s it) |
| Two counters, two owners | `grep -c "stale_sweeps" tools/improvement_control/recovery.py; grep -c '"attempts"' tools/improvement_control/recovery.py` | first output > 0, second output == 0 |
| Judge receipt pinned to the pool's project | `grep -c 'record_receipt(project_key="valor"' tools/cross_vendor_judge.py` | output > 0 |
| Retirement test is root-anchored | `grep -c 'parents\[2\]' tests/unit/test_improvement_control_lease.py` | output > 0 |
| Skill invokes the venv binary | `grep -c '\.venv/bin/valor-improve' .claude/skills/improve-research/SKILL.md` | output > 0 |
| Anti-criterion: no OpenRouter lookup in the meter | `grep -c "openrouter.ai/api/v1/generation" tools/paid_inference_meter.py; grep -cE "^(import|from) (requests|httpx|urllib)" tools/paid_inference_meter.py` | both outputs == 0 |
| Investigation vocabulary carries the amendment values | `grep -c '"charter_amendment"\|"awaiting_authorization"' models/improvement_investigation.py` | output == 2 |
| Anti-criterion: no Popoto client in the control package | `grep -rc "POPOTO_REDIS_DB\|from popoto.redis_db" tools/improvement_control/ tools/paid_inference_meter.py` | match count == 0 |
| Anti-criterion: no child-gate bypass | `grep -rc "VALOR_ALLOW_CHILD_SESSIONS\|parent_agent_session_id=" tools/improvement_control/ tools/improvement.py reflections/improvement_controller_tick.py reflections/improvement_intent_reconcile.py` | match count == 0 |
| Anti-criterion: only the vault writer touches op | `grep -rlE "\bop (item|whoami|read|signin)" tools/improvement_control/ tools/improvement.py tools/paid_inference_meter.py reflections/improvement_intent_reconcile.py reflections/improvement_controller_tick.py \| wc -l` | match count == 0 |
| Anti-criterion: no direct case save outside the projection | `grep -rc "ImprovementCase.*\.save()\|case\.save()" tools/improvement_control/journal.py tools/improvement_control/intents.py tools/improvement_control/scheduler_adapter.py tools/improvement_control/recovery.py tools/improvement.py` | match count == 0 |
| Anti-criterion: deprecated OpenRouter flag absent | `grep -rc '"include": True' tools/paid_inference_meter.py` | match count == 0 |
| Anti-criterion: no `.env` write on the improvement path | `grep -rlE "\.env['\"]" tools/improvement_control/ tools/improvement.py tools/vault_write.py tools/paid_inference_meter.py \| wc -l` | match count == 0 |
| Anti-criterion: no routine question path | `grep -rc "AskUserQuestion\|ask_poll\|poll_registry" tools/improvement_control/ tools/improvement.py reflections/improvement_controller_tick.py` | match count == 0 |

## Critique Results

Critique round 3, 2026-09-14 (FULL depth; mode: sequential lenses, Agent tool unavailable in the stage-runner context, so no finding below was independently corroborated). This was the concern re-critique of round 2: every one of the eight round-2 `Addressed By` cells was re-verified against the revised text and the source at the baseline and is genuinely embedded, not merely cited: activation is `transition_status` then `publish_session_notify` (sync, fail-quiet, notify-only at `agent/agent_session_queue.py:967`) then `record_running` (Data Flow step 8, Race 5, Task 6 test, Success Criterion, Verification row); `transition`'s Lua `HSET`s `result_digest` after the binding compare and `on_session_terminal` settles on `result_digest` or `completed` (`no_proposal`) with only `failed`/`killed`/`abandoned` left `running` (Data Flow steps 4 and 10, Decisions 2 and 6, Task 1 assertion, three Task 4 tests, Success Criterion, Verification row); `attempts` belongs to `record_materialized` and `stale_sweeps` to `recovery.py` with the state-branched `reconcile()` and the Race 3 first-sweep rule (Decision 2, Data Flow step 11, Races 2 and 3, Tasks 4 and 7, Success Criterion, Verification row); the judge receipt is pinned to `project_key="valor"` and `propose-amendment` loads the `valor` dict via `load_local_projects()` (`reflections/utilities.py:100`) before `send_eng_telegram(project, ...)` (`:454`); the slot script returns `released`/`absent`/`foreign_holder` with WARNING only on `foreign_holder`; `EVIDENCE_KINDS` lands on `DEFAULT_VOCABULARY_MAXIMUM = 8` (7 values at `models/improvement_evidence.py:73-81`, cap at `tests/unit/test_improvement_models.py:95`) with the Task 9 comment; Data Flow step 4 reads `agent_session_id=<own id or None>`; replay is head-authoritative with `fold_reached_head`. The rows below are new.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Activation is three non-atomic calls (`transition_status` -> `publish_session_notify` -> `intents.record_running`), and the plan never says what a later tick does with an intent still `materialized` whose row is no longer `admitted`. A reflection-subprocess crash between the flip and `record_running` leaves the session `pending`/`running` with the intent `materialized`; the next tick's activate re-runs `transition_status(session, "pending")`, which `models/session_lifecycle.py::transition_status` permits from `running` (only terminal sources are rejected; the CAS compares against the freshly loaded status), so a session the worker is executing is knocked back to `pending` and can be picked up a second time, while the session's own `propose` is refused `INTENT_STATE` because the binding demands `state == "running"`. Race 2 covers the crash before materialize and Race 5 the worker dying after publish; the window between activate's writes is uncovered. | pending | In `scheduler_adapter.activate`: `fresh = get_authoritative_session(session.session_id)`; `if fresh.status == "admitted": transition_status(fresh, "pending", reason=...); publish_session_notify(fresh)`; `elif fresh.status in TERMINAL_STATUSES: return` (leave the intent for `reconcile()`); then `intents.record_running(...)` with `from_state="materialized"` in every non-terminal branch. Test: `test_activate_retry_never_demotes_a_running_session` (intent `materialized`, row seeded `running`; the second tick leaves the row `running`, publishes nothing, moves the intent to `running`). Same gotcha in Task 7: `finalize_session` raises `StatusConflictError` on an already-terminal row (`models/session_lifecycle.py:555-565`, `reject_from_terminal=True`), so the exhausted-intent branch guards `if row is not None and row.status not in TERMINAL_STATUSES` before calling it; otherwise every sweep past a terminal row logs an error instead of a no-op. |
| CONCERN | Risk & Robustness | `admit` must refuse "while any intent on the case is `reconciliation_required`" inside one Lua script, and `resume --force`, `doctor`, and `get_control_status` all enumerate a case's intents, but the Decision 2 key layout has no per-case index of intent ids. The only way a script can find "every intent on the case" from those keys is `redis.call("KEYS", "improve:valor:{case}:intent:*")` inside the EVAL, which blocks the server on the whole keyspace; a builder who reaches for it makes every admit O(keyspace). | pending | Add `improve:{project}:{case}:intents` (set of action ids) to Decision 2, written by `admit` in the same script that creates the intent. In `admit`'s Lua, before the slot check: `for _, aid in ipairs(redis.call("SMEMBERS", intents_key)) do if redis.call("HGET", intent_key_for(aid), "state") == "reconciliation_required" then return {0, "INTENT_STATE", rev} end end`, then `SADD intents_key action_id` beside the `HSET` that writes the intent; pass `intents_key` in `KEYS`. `cancel`, `resume --force`, `doctor`, and `get_control_status` read the set; the reconcile pass keeps its Python `scan_iter`. `export_namespace` dumps the set and `import_namespace` restores it, or the round-trip Success Criterion passes while `admit` refuses nothing after a restore. One Task 4 test: a case with a `settled` and a `reconciliation_required` intent refuses admit; the same case after `cancel` admits. |
| NIT | Scope & Value | `case explain` is one of the twelve subcommands Task 10 counts and Agent Integration lists, and it appears nowhere else: no Decision, Data Flow step, Error State Rendering bullet, Success Criterion, or test says what it prints or which package function it calls, while every other subcommand has at least one of those. | pending | Give `case explain` one sentence in Decision 9 (what it renders beyond `case show`, from which reader) and one assertion in `test_improvement_cli.py`, or drop it and make the count eleven. |
| NIT | History & Consistency | The Verification row "Status trio landed together" expects `models/session_lifecycle.py:2`, pinning the count of quoted `"admitted"` occurrences in that file to exactly two, but Decision 4 asks for a comment on each of the two entries, and any comment or docstring that spells the status in quotes makes the count three and fails a correct build. | pending | Make the expectation `>= 2` for `models/session_lifecycle.py` (or grep the two set literals separately), matching how the other rows use `> 0`. |

---

## Open Questions

Charter §9 applies to this plan too: each item below is a decision already made with its default, written so the critique can overturn it with evidence rather than so a human has to answer it. Silence keeps the default.

1. **The lease hedge (Decision 1).** Default: `LeaseProtocol` plus the deletable `CaseLease`, retired by a test the moment `models/redis_lease.py` exists. The alternative, blocking this lane and therefore lanes 4 through 6 on #3220 (open, no branch), was rejected because the accept rule the lane depends on lives in the journal scripts either way. If the critique judges the interim implementation a fork in the sense #3220 forbids, the fallback is to build #3220's `models/redis_lease.py` first as its own lane and re-plan this one against it.
2. **Unit 3 stays on its key (No-Gos, #3274).** Default: no migration; `valor-improve budget` reads unit 3 through `infrastructure_budget.status_dict`. Lane 7's plan expected the move; the feature doc sentence is corrected here. Overturn if a namespace-wide `export` that omits unit 3's counter is judged incomplete; the export already includes the unit-3 ledger rows through the ORM, and the counter is rebuilt from them by `infrastructure_budget` on the next admission.
3. **The cross-vendor judge is receipted, not gated (Decision 7).** Default: `purpose="sdlc_review"` receipts are informational. Overturn only if charter §8 is read to put ordinary review spend inside the $10/day RSI pool, which would let RSI exhaustion refuse client review.
4. **`RECOVERY_OWNERSHIP["admitted"] = "reflection"` (Decision 4).** Default: a new owner value, because none of `worker`, `bridge-watchdog`, `none`, `human` is true. Overturn to `"worker"` if a reviewer prefers no vocabulary growth; the constant is informational either way.
