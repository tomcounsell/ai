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

_Filled in the next revision of this document._

## Architectural Impact

_Filled in the next revision of this document._

## Appetite

**Size:** Large

## Prerequisites

_Filled in the next revision of this document._

## Solution

### Key Elements

_Filled in the next revision of this document._

### Flow

_Filled in the next revision of this document._

### Technical Approach

_Filled in the next revision of this document._

## Failure Path Test Strategy

### Exception Handling Coverage

_Filled in the next revision of this document._

### Empty/Invalid Input Handling

_Filled in the next revision of this document._

### Error State Rendering

_Filled in the next revision of this document._

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

_Filled in the next revision of this document._

## Risks

_Filled in the next revision of this document._

## Race Conditions

_Filled in the next revision of this document._

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
