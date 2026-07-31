---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-31
tracking: https://github.com/tomcounsell/ai/issues/2494
last_comment_id:
---

# Durability: Room / Job / AgentSession / AgentRun

## Problem

On 2026-07-30 a PM session in the `cyndra` project backgrounded a `dev` subagent to build a set of memory fixes. The PM's turn ended 13 seconds later. The subagent kept working for 8 minutes 27 seconds, was SIGKILLed mid-turn, and the session exited 0 with status `completed`. The worktree and branch were deleted one second later. Because the subagent had not committed anything, the unmerged-branch guard did not fire. All of its work is gone.

Nothing detected this. Nothing could have: the session died the way a healthy session dies.

That is not an isolated bug. It is the visible end of a structural property: **session durability in this repo is not designed, it is emergent.** Roughly fourteen independent recovery mechanisms accumulated one per incident, and every one of them keys on `AgentSession.status`. They all answer the same question — *"did a process die badly?"* — and no mechanism anywhere answers *"was the work that was promised actually delivered?"*

**Current behavior:**

Every confirmed loss happens outside the guarded perimeter. From the five-agent audit recorded in #2494:

- Direct messages are permanently lost if the bridge crashes before enqueue. All three recovery scanners open with `if not chat_title: continue` (`bridge/reconciler.py:212`, `bridge/catchup.py:109`, `bridge/agent_catchup.py:243`); a Telegram private chat is a `User` entity with no `title`.
- Inbound email is marked `\Seen` for a whole batch *before* fetch (`bridge/email_bridge.py:1596`), and the search is `UNSEEN`. A crash mid-batch loses every message in it.
- A "stop" message destroys the instructions queued alongside it: `agent/health_check.py:511` drains the entire steering list, finds an abort, and `return`s before the re-push. Unconditional — no crash required.
- `messenger.notify_sdk_started` has **zero** production callers, so `SessionHandle.pid` is always `None` and `AgentSession.harness_pid` is never populated. The Tier-2 subprocess hang probe can never fire.
- `CircuitBreaker.record_failure`/`record_success` have **zero** production callers; `CircuitOpenError` is caught at `agent/agent_session_queue.py:2776` and raised nowhere; there are zero references to `429`, `529`, `overloaded_error`, or `rate_limit_error` in `agent/`, `bridge/`, or `worker/`. An entire pause/hibernate/resume subsystem is wired to an input that does not exist — and `tests/unit/test_d3_silent_failure_logging.py:54` asserts the caller's absence, so CI enforces the deadness.
- `pm_pid` is written at `agent/session_runner/runner.py:660` and cleared by nothing, while `_terminate_detached_harness` SIGTERMs it at startup.

The unifying property is that **a dead detector emits silence, and silence is indistinguishable from health.** Three separate detection layers were found silently dead and none alarmed.

**Desired outcome:**

- A message that arrives can be shown to have been answered, or to be visibly outstanding. Never silently gone.
- "Work was promised and not delivered" is a queryable state.
- Crash-resume, reply-resume, and steering are one code path.
- Four pid-like fields collapse into one execution record with a reuse fence.
- Naming is harness-agnostic, so codex/opencode/pi need no schema change.

## Freshness Check

**Baseline commit:** `ac33726ca5e9e8e4dcfdaea3798dc0b2f8880bbc`
**Issue filed at:** 2026-07-31T05:41:17Z
**Disposition:** Overlap (proceeding; coordination note below)

**File:line references re-verified:** The issue was filed minutes before this plan, and every reference in it was established by five audit agents plus three recon agents against commit `7d441fed1`. The four spikes below independently re-read the same code at `ac33726ca` and corrected several premises — those corrections are recorded in Spike Results and are the authoritative version.

**Cited sibling issues/PRs re-checked:**
- #2420 — OPEN. The motivating incident. Stays independently closeable; this plan dissolves the class, not the instance.
- #2489, #2490 — both OPEN, both confirmed line-by-line by spike-4. #2489 is a **hard prerequisite** for the obligation ledger (see Milestone 3).
- #2421 — OPEN, has its own plan at `docs/plans/promise-gate-short-output-reachability.md`. Adjacent; not absorbed.
- #1925 — CLOSED. Migrated bridge classifiers off Ollama onto Haiku. Directly governs the router decision.
- #2207 — the 7.4M-key Redis flood. Its guard is `AgentSession`-specific and constrains every new model here.

**Commits on main since the issue was filed:**
- `ac33726ca Plan: Pipeline Graph — Single Source of Truth (#2491)` — a plan document only, no code. **Overlap:** #2491 will edit `models/agent_session.py` (the `SDLC_STAGES` constant at `:81`) while this plan deletes pid fields from the same file. Regions are disjoint; both are Large. Coordination, not a blocker — but the two should not build concurrently in the same worktree.

**Active plans in `docs/plans/` overlapping this area:** `pipeline-graph-single-source-of-truth.md` (as above). No others touch session durability.

**Note on blast-radius tooling:** `python -m tools.code_impact_finder` — the tool `docs/sdlc/do-plan.md` declares for Phase 1 blast-radius analysis — **failed and returned nothing**. It aborts index construction with `openai.BadRequestError: Invalid 'input[96]': input cannot be an empty string`, then reports `WARNING: impact finder degraded (empty_index)` and `No results (finder degraded)` while exiting 0. Filed as #2499. The blast radius in this plan therefore comes from the three recon agents and four spikes, which read the code directly — a stronger source, but the tool's silent-degrade-and-exit-0 behavior is itself an instance of the pathology this plan exists to fix.

## Prior Art

The accretion is legible in the merge history. Six separate merged attempts at subprocess liveness and recovery:

- **PR #1557** (#1537): *Liveness recovery confirms subprocess death before requeue.* Added `_confirm_subprocess_dead` with SIGTERM→SIGKILL escalation. Correct as far as it goes; keyed on a pid that the runner path stopped populating.
- **PR #1795** (#1767): *Deterministic U-state worker recovery.* Added the dead-worker sweep.
- **PR #1870** (#1817): *Atomic per-message + pending→running claims.* This is genuinely good and is preserved unchanged by this plan.
- **PR #1875** (#1817): *PTY pid persist, observable bg-task failures, notify liveness (D1-D4).* **This shipped the `notify_sdk_started` path that the audit found is now dead code with zero callers.** The execution path moved from the SDK path to the runner path underneath it.
- **PR #1943** (#1938): *Reap `claude -p` process group before requeue/worktree cleanup.* The teardown reap, which the audit verified as correct and cancellation-proof.
- **PR #2070** (#2069): *Widen never-started grace to ~20min + evidence-based subprocess-hang probe.* The probe it added reads `SessionHandle.pid`, which is always `None`.
- **PR #2155** (#2141): *Update flow drains sessions before worker restart.*

Also: **#1721** (CLOSED) — *"Granite sessions can't resume where they stopped: persist resume handles."* The resume-handle persistence it established is preserved and moves to `AgentRun`.

**`docs/plans/session-recovery-observation-audit.md`** (2026-07-15) lists 13 remediations, none executed. Item **#11** is *"Consolidate liveness into a persisted execution lease — make runner/worker ownership authoritative and demote progress timestamps to diagnostics."* Item **#3** is *"Fence recovery on owner generation and kill acknowledgement."* Milestone 1 of this plan is those two items.

### Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1557 | Confirm subprocess death before requeue | Reads a recorded pid. Correct logic, but the field it depends on is populated only on a code path that no longer runs. |
| PR #1875 | Persist harness pid, add `notify_sdk_started` liveness callback | The callback's only invoker (`BossMessenger.notify_sdk_started`) lost its caller when the SDK path was deleted. Nothing alarmed, because a detector going dark produces silence. |
| PR #2070 | Evidence-based subprocess-hang probe | Built on `SessionHandle.pid`, which #1875's dead path was supposed to fill. Permanently returns `("unknown", None)`. |
| PR #1795 | Dead-worker sweep | Correct for its case, but runs *before* the recovery step, so a worker+child dying together yields `killed` rather than a resume. Whether a session resumes with full context depends on whether `claude_pid` happened to be stamped — a race, not a policy. |

**Root cause pattern:** every fix added a new healer with a new status filter, and none consolidated. Each was written the morning after a process died badly, so each asks "did a process die badly?". Nobody wrote the healer for "the process exited 0 and lied," so that path has no owner, no telemetry, and no test.

## Research

**Queries used:**
- `PID reuse safe process identity fencing pid start time /proc stat btime`

**Key findings:**

- **`(pid, start_time)` is a detection and correlation identifier, not a fence.** There is always a TOCTOU window between reading the start time and acting on the pid. Sources: [LWN — Rethinking race-free process signaling](https://lwn.net/Articles/784997/), [OCSF common-process-id specification](https://github.com/ocsf/common-process-id/blob/main/specification.md).
- **Linux's race-free answer is `pidfd`** (`pidfd_open`, `CLONE_PIDFD`, `pidfd_send_signal` returning `ESRCH` once the process is gone). **macOS has no pidfd**, and this system runs on darwin. So `psutil.Process.create_time()` is the ceiling available here.
- **For processes we spawn ourselves, a stronger option exists**: retaining the child handle / not reaping until done keeps the kernel from recycling the pid. The runner already spawns with `start_new_session=True` and tracks the pgid.

**How this informs the approach:** the plan claims `(pid, create_time)` as **best-effort detection**, documented as such, not as a safety guarantee. It is an unambiguous improvement over the status quo — which is a bare pid with no fence at all, and a `pm_pid` that nothing ever clears — but the plan must not overstate it. For runs the runner spawned, the child handle is the primary mechanism and the fence is the backstop.

## Spike Results

Four spikes ran in parallel. Three of them corrected premises that were in the issue.

### spike-1: What is a Room for a chatless session?
- **Assumption**: "Some sessions have no chat, so Room may need to be optional."
- **Method**: code-read
- **Finding**: **Every session belongs to a Room; chatless sessions get a synthetic per-project `system` Room.** The decisive fact is that **steering is keyed by `session_id` alone** — `_queue_key()` (`agent/steering.py:32`) takes no chat or project argument, and `steer_session` (`agent/session_executor.py:684-727`) performs no addressability check. An operator can steer `sdlc-local-42` today. The inbox is universal and chat-independent, so an optional Room would place operator steering and abort outside the model for exactly the sessions that are hardest to observe. Stronger still: **the codebase has already chosen this three inconsistent times** — literal `chat_id="0"` (`agent/reflection_scheduler.py:618`), `chat_id = session_id` (`models/agent_session.py:1656`), and default-DM inheritance (`tools/agent_session_scheduler.py:83`). And `worker_key` already falls back to `chat_id or project_key` (`models/agent_session.py:647`). Room does not introduce a synthetic addressee; it unifies three of them.
- **Confidence**: high
- **Impact on plan**: unblocks Milestone 2. Shape is `Room = (project_key, addressee)` with addressee ∈ `telegram:{chat_id}` | `email:{address}` | `system`. Fifteen id families were enumerated; four of them are not `AgentSession` ids at all.
- **Defects surfaced**: #2495 (steering a ledger session is accepted and never delivered), #2497 (reflection sessions push to `telegram:outbox` with `chat_id="0"`).

### spike-2: Does an authorship timestamp exist?
- **Assumption**: "The at-rest check must anchor on authorship, and authorship may not be recorded anywhere."
- **Method**: code-read
- **Finding**: **It exists.** `runner_user_routed` / `runner_complete_routed` (`agent/session_runner/adapter.py:499`, `:534`) and `turn_history` (`agent/session_runner/runner.py:1629`) all stamp `ts=_now_iso()` at emit time in the worker process, persisted to `AgentSession.session_events`. The reference incident **is** detectable today: authored 14:32:49, activity 14:41:16, delivered 14:42:49. All three delivery-side records (`chat_message_log` via `bridge/telegram_relay.py:705`, `store_message` at `:918`, `recent_sent_drafts` at `models/agent_session.py:1867`) give 14:42:49 and read clean. The trap is confirmed.
- **Confidence**: high
- **Impact on plan**: the primary health check is buildable now. Minimal hardening is one `last_authored_at` scalar written at two existing save sites (`adapter.py:491`, `:526`), avoiding a 200-entry list scan with dual format parsing on every evaluation. Three real constraints: `session_events` is trimmed to 200 entries (`adapter.py:237`), timestamps are heterogeneous (ISO string vs float epoch), and the activity side carries a 5s liveness-writer cooldown.
- **Correction to the issue**: **reactions have no durable record anywhere.** `_send_queued_reaction` (`bridge/telegram_relay.py:117-179`) returns a bool and writes nothing. "A reaction discharges the communication obligation" is not implementable today; it is new work, and `tools/react_with_emoji.py:88` holds no `AgentSession` handle.
- **Defect surfaced**: #2496 (relay-sent PM messages recorded as `direction="in"`).

### spike-3: Which Redis namespaces move to Room?
- **Assumption**: "18 session-keyed namespaces need a per-key ruling; two are TTL-less lists of undelivered messages needing a careful live migration."
- **Method**: code-read + read-only Redis SCAN
- **Finding**: **three premises were wrong.** (1) There are **14** real namespaces, not 18 — `post_session_extraction:` is an asyncio *task name*, and `extraction-`, `scheduled-`, `tui-` are Popoto field *values*. (2) `telegram:outbox` and `email:outbox` are **not** TTL-less; every writer sets `expire(key, 3600)` (`tools/send_message.py:135`, `tools/react_with_emoji.py:92`, `agent/tool_budget.py:342`, and five more). Only `steering:` is genuinely TTL-less. (3) **Live count is zero** — 0 steering keys, 0 outbox keys, verified by read-only Lua SCAN over 34,986 keys in db0. Nine of the fourteen stay session-scoped for one structural reason: they are all SET-NX-with-TTL flags meaning *"did this execution already emit signal X"*, and Room-scoping any of them breaks dedup between concurrent sessions in the same chat.
- **Confidence**: high
- **Impact on plan**: **no data migration is required.** The outbox relay is pattern-driven (`OUTBOX_KEY_PATTERN = "telegram:outbox:*"`, scanned at `bridge/telegram_relay.py:801`) and resolves the session from payload fields rather than the key suffix — so room-scoped keys under the same prefix are drained by the existing relay **unmodified**, and in-flight session keys self-expire in an hour. Steering gets a drain-only dual-read instead of a data move. This removes an entire task class from Milestone 2.
- **Hazard avoided**: `pop_all_steering_messages` is a destructive LPOP drain with a documented single-consumer invariant (`agent/steering.py:88-90`). A migration script doing LRANGE+RPUSH alongside a live worker produces **duplicate steers**, and if `_reenqueue_leftover_steering` (`agent/session_executor.py:802`) fires during the window, three copies.

### spike-4: Does the obligation ledger become a nag machine?
- **Assumption**: "Promoting the promise gate to a durable ledger is safe with a discharge rule."
- **Method**: code-read + read-only ORM sampling + direct execution of the classifier against candidate phrases
- **Finding**: **yes, on the design as stated — and no, if the trigger is narrowed.** Running `_evaluate_promise_heuristic` against the proposed trigger phrases: `"I'm working on it"`, `"I'll take a look"`, `"let me check"`, and `"on it"` **all currently ALLOW**. The proposed trigger is strictly broader than anything deployed, so ledger volume cannot be extrapolated from the gate's current block rate. Worse: **every durable pending-flag in this subsystem currently lacks a release path.** `deferred_self_draft_pending` has exactly one write site (`agent/output_handler.py:683`), zero writes of `False`, and zero deletes (#2489). `AgentSession.expectations` is written only when non-`None` (`agent/output_handler.py:1139`) and the drafter returns `None` rather than `""` when empty, so once set it is never cleared. Two for two.
- **Real data**: of 57 live sessions, **0 carry a non-empty `expectations`** — the existing partial ledger has no rows — and **2 currently carry the #2489 leak**. The audited promise-gate paths produced 39 `forward_deferral` blocks in 88 days ≈ 0.44/day.
- **Confidence**: high
- **Impact on plan**: the ledger ships **only after #2489 is fixed**, with the narrow trigger (`forward_deferral` without a scheduled-delivery reference — *not* `behavioral_change`, which produced 1 real block in 90 days against 39 forward-deferrals and is the false-positive generator), auto-discharge on any subsequent substantive outbound, at-rest-only evaluation, a cap of one open obligation per Job, 24h silent expiry, and first-iteration alarms routed to the operator surface rather than human chat. Estimated well under 1 alarm/day.

## Data Flow

**Today (inbound):**

1. Telethon event → `bridge/telegram_bridge.py` handler
2. `record_last_event` → `is_duplicate_message` → cursor guard → `store_message`
3. **👀 reaction + `mark_read` (`:1685`, `:1691`) — the user-visible ack**
4. ~860 lines of awaits: media processing, Haiku intake classifier (`:2168`), semantic session routing (`bridge/session_router.py:145`), revival prompt (git subprocess), reply-chain fetch
5. `dispatch_telegram_session` → `claim_message` (SETNX) → `enqueue_agent_session` → `AgentSession.async_create(status="pending")`
6. `record_message_processed` (durable dedup) → `record_last_processed` (cursor)

Steps 3→5 are the loss window. For group chats the reconciler recovers within ~3 minutes; **for DMs nothing recovers, ever.**

**Target (inbound):**

1. Telethon event → handler
2. Resolve **Room** from config (`bridge/routing.py` `find_project_for_{chat,dm,email}` — a dict lookup, no network)
3. **👀 reaction** — "the bridge saw this". Deliberately stays pre-durable; it is a different and useful fact from "durably assigned".
4. **Append to Room inbox — DURABLE.** One Redis write. The loss window is now this single operation.
5. Route: reply-to → permanent `message_id → job_id` index, no model call. Otherwise → `bridge/job_router.py` (Haiku, fail-open to NEW).
6. Bind message to Job; emit 🤔 (new Job) or the existing steer reaction.

**Outbound (unchanged except two stamps):**

runner emits → `adapter._on_user`/`_on_complete` (**authorship stamped here**) → `output_handler` drafter/redundancy/read-the-room → `rpush telegram:outbox:{...}` → `telegram_relay.process_outbox` LPOP → Telethon send → **delivery stamped here, after success**.

## Architectural Impact

- **New dependencies**: none. No new packages; `psutil` is already a dependency and provides `create_time()` cross-platform.
- **Interface changes**: `AgentSession` loses `claude_pid`, `pm_pid`, `harness_pid`; `SessionHandle.pid` is deleted. `agent/steering.py` gains a Room-scoped key with a legacy dual-read leg. Three new Popoto models.
- **Coupling**: net decrease. Four pid fields and three liveness derivations collapse. But note the honest counter-argument from recon: of five modules nominated to consolidate under Room, only `bridge/read_the_room.py` genuinely does (35 `chat_id` refs, 0 `session_id`). `bridge/redundancy_filter.py` is per-session *by explicit design* (`:133`) and moving it would change suppression semantics; `message_drafter`, `message_quality`, and `promise_gate` are stateless. The modules that do consolidate are `bridge/dedup.py` and `bridge/context.py`, already chat-keyed.
- **Data ownership**: the inbox moves from session to Room. This is the single most consequential change — it makes a message addressed to a dead object impossible by construction, which is the root of the orphaned-steering class.
- **Reversibility**: Milestone 1 is highly reversible (additive model + field deletions with a scripted migration). Milestone 2 is moderately reversible (dual-read leg can be left in place). Milestone 3 is additive.
- **Supersedes**: `docs/infra/harness-cross-compat.md` plans Codex support as **four more nullable `AgentSession` fields** (dev harness selection, thread id, version, turn count). That approach scales as 4 fields × N harnesses. `AgentRun` makes it one row per run with a `harness` column. The two designs must not both land; this plan's Milestone 1 should be sequenced before any Codex field work.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (one per milestone boundary — each milestone is independently shippable and independently abortable)
- Review rounds: 2+ (schema review before Milestone 1 lands, since KeyField sets get exactly one free shot; full review per milestone)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable with AOF on | `redis-cli INFO persistence \| grep -q 'aof_enabled:1'` | Durable writes for new models |
| `psutil` importable | `python -c "import psutil; psutil.Process().create_time()"` | PID-reuse fence |
| Popoto ≥ 1.8.0 | `python -c "import popoto; print(popoto.__version__)"` | `_create_lazy_model` default-fill behavior this plan relies on |
| #2489 fixed and merged | `gh issue view 2489 --json state -q .state` | Hard blocker for Milestone 3 only |

## Solution

### Key Elements

- **`Room`**: the environment a conversation happens in. `(project_key, addressee)` where addressee is `telegram:{chat_id}` | `email:{address}` | `system`. Resolved from `projects.json`, which stays the source of truth — Room caches the resolved binding, it does not replace the config. Owns addressing, participants, and the inbox. Immortal.
- **`Job`**: a responsibility to complete something end to end. Carries a required, PM-authored, append-only-versioned `goal`. Never hard-closed; goes to rest by age; always resumable by a new steering message regardless of age.
- **`AgentSession`**: unchanged in role — an agent's context, owning the resume handle and the existing `parent_agent_session_id` hierarchy. Becomes thinner by *losing* execution fields, not by being wrapped.
- **`AgentRun`**: one execution. Harness enum, nullable pid, pid-reuse fence, cwd, low-cardinality lifecycle status. Parent link is a plain id `KeyField`. An in-process Task subagent is representable with `pid=None` and an `agent_id`.

### Flow

Message arrives → 👀 (*bridge saw it*) → durable Room-inbox append → route → 🤔 or steer reaction (*durably assigned*) → PM works → PM speaks → at rest, health check compares last **authored** communication against last activity.

A message stuck at 👀 with no follow-on reaction is itself the alarm — visible in the chat without a dashboard, and queryable as "Room inbox entry with no Job binding older than N".

### Technical Approach

**Schema (all constraints verified by recon — do not re-litigate):**

- Parent links are `KeyField(null=True)` holding an id string. Popoto's `Relationship` requires the actual class object, so **self-reference is impossible**; there are zero `Relationship` uses in `models/`, and `AgentSession` already uses the id-string pattern.
- Nothing is queryable unless it is a `KeyField`, `IndexedField`, or `SortedField`. Plain `Field`/`DatetimeField`/`IntField` raise `QueryException` on `.filter()`. So **`ended_at IS NULL` is not queryable** — liveness is a low-cardinality `IndexedField` status, mirroring the existing terminal/non-terminal vocabulary. `__isnull` has zero usages repo-wide.
- `IndexedField` creates one Redis Set **per distinct value**. Never index a pid, uuid, or timestamp. (`claude_pid` is currently exactly this anti-pattern, so deleting it is a net win.)
- Every `KeyField` becomes part of the primary Redis key, sorted alphabetically. Adding one to an existing model forces a full key-rewrite migration — **the new models get one free shot at their KeyField set**, which is why schema review gates Milestone 1.
- New models need no data migration; nullable field additions are default-filled by `_create_lazy_model`. `_heal_descriptor_pollution` does **not** exist (removed as dead under Popoto 1.8.0, #2083) — do not cite it.

**Fence:** `(pid, create_time)` via `psutil`, documented as **best-effort detection**, not a guarantee — there is an irreducible TOCTOU window and macOS has no `pidfd`. For runs the runner spawned, the retained child handle is primary and the fence is the backstop.

**Router:** new `bridge/job_router.py`, a sibling of `bridge/session_router.py` rather than an edit in place — that file is load-bearing on the hot bridge path and merging would give one function two return types and two failure meanings. Copy its structure: zero-candidate short-circuit (`:83-88`), top-5 recency cap (`:90-95`), numbered-choice prompt (`:102-135`), **post-hoc `valid_ids` membership check** (`:151-159`), 0.80 threshold, total fail-open (`:175-178`). Runs on Haiku via `run_typed` like every other classifier migrated by #1925. Granite is deferred to #2498 pending a sanctioned transport and measured latency.

**Inbox migration:** none. Outbox room keys ship under the existing `telegram:outbox:` / `email:outbox:` prefix and the pattern-scanning relay drains them unmodified; session-scoped stragglers self-expire on their existing 1h TTL. Steering gets a drain-only dual-read shipped *before* writers flip.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/steering.py` re-push failures (`agent/session_runner/runner.py:624-629` currently logs and drops) — test must assert an observable signal, not silent loss
- [ ] `agent/session_pickup.py:235-240` catches a save exception and logs "non-fatal" after a destructive LPOP — test must assert the messages are recoverable or the failure is loud
- [ ] `bridge/job_router.py` fail-open path — test asserts a model error yields NEW Job, never a wrong binding
- [ ] Fence read failure (`psutil.NoSuchProcess`, `AccessDenied`) — test asserts "treat as dead" and that this is logged

### Empty/Invalid Input Handling
- [ ] Router with zero candidate Jobs → short-circuits without a model call
- [ ] Router returns an id not in the candidate set → treated as NEW
- [ ] `Job.goal` empty or whitespace-only → rejected at creation
- [ ] `AgentRun` with `pid=None` (in-process subagent) → all fence code paths handle it without raising

### Error State Rendering
- [ ] A message that fails to bind to a Job leaves the 👀-orphan state visible and queryable
- [ ] The at-rest health check's output reaches the operator surface, and a failure inside the check is logged rather than swallowed

## Test Impact

- [ ] `tests/unit/test_d3_silent_failure_logging.py:50-55` — **DELETE**: it asserts `"circuit.record_failure" not in source`, i.e. CI currently enforces that the circuit breaker stays unfed. Feeding the circuit is out of scope for this plan, but this assertion must be re-scoped or removed so it does not block the follow-up; at minimum add a comment naming the dead subsystem.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` — UPDATE: reaper moves from `claude_pid` to `AgentRun` lookup
- [ ] `tests/unit/test_session_health_orphan_reap.py` — UPDATE: same
- [ ] `tests/unit/test_session_health_phantom_guard.py` — UPDATE: phantom guard must cover the new models
- [ ] `tests/unit/test_messenger_callbacks.py:33,55,70` — **DELETE**: tests `notify_sdk_started`, which this plan deletes as dead code
- [ ] `tests/unit/test_agentsession_pending_index_leak.py` — UPDATE: index assertions extend to new models
- [ ] `tests/integration/test_steering.py` (69 steering call sites) — UPDATE: dual-read leg; assert legacy-key drain still works during the transition
- [ ] `tests/integration/test_crash_auto_resume.py` — UPDATE: resume path now keys on `AgentRun`
- [ ] `tests/integration/test_harness_resume.py` — UPDATE: resume handle moves to `AgentRun`
- [ ] `tests/unit/test_session_pickup_phantom_filter.py` — UPDATE
- [ ] `tests/unit/test_recovery_ownership.py` — UPDATE: `RECOVERY_OWNERSHIP` extends past `NON_TERMINAL_STATUSES`
- [ ] `tests/unit/test_promise_gate_session_events.py` — UPDATE (Milestone 3 only)
- [ ] `tests/unit/test_session_modal_liveness_render.py` — UPDATE: dashboard reads `AgentRun` for liveness

## Rabbit Holes

- **Feeding the Anthropic circuit breaker.** The audit found the whole pause/hibernate/resume subsystem is wired to a `record_failure` with no callers, and no 429/529 handling exists anywhere. Fixing it is genuinely valuable and genuinely separate — it is an error-handling problem, not a durability-model problem. Resist folding it in; it will double the review surface. Filed separately.
- **Rewriting `session_health.py`.** It is 5,705 lines with 61 issue references and 61 commits in 90 days. The temptation to "clean it up while we're in there" is enormous and would swallow the whole appetite. This plan changes only the pid-lookup sites and the index-drift generalization.
- **Making `Room` absorb the comms layer.** Recon measured this: only 1 of 5 nominated modules genuinely consolidates. Moving stateless formatters under Room is re-parenting with no gain, and moving `redundancy_filter` changes suppression semantics. Move `read_the_room` only; leave the rest.
- **Perfect PID fencing.** macOS has no `pidfd`. Chasing an airtight fence leads to kqueue `EVFILT_PROC` plumbing for marginal gain over `(pid, create_time)` plus a retained child handle. Document the residual window and move on.
- **A `Turn` model.** A third concept (the queue item) is genuinely hiding in `AgentSession` — it is why two `pending` rows can exist for one conversation. It is real, and it is not this plan.

## Risks

### Risk 1: KeyField sets are a one-shot decision
**Impact:** Every `KeyField` is part of the primary Redis key, sorted alphabetically. Getting the set wrong on `Room`/`Job`/`AgentRun` means a full key-rewrite migration on a model that by then holds live data.
**Mitigation:** A dedicated schema review gate before any model lands (Task 1). Write the access paths down first and confirm each is served by a `KeyField`/`IndexedField`/`SortedField`, since plain fields are not queryable at all.

### Risk 2: A new model re-triggers the #2207 phantom index flood
**Impact:** The identity-less-hash guard in `repair_indexes()` is `AgentSession`-specific. Registering a new model in `models.__all__` opts it into the generic `rebuild_indexes()` sweep, which has no such guard — the exact mechanism behind the 7.4M-key Redis flood of 2026-07-22.
**Mitigation:** Each new model ships with its own guarded repair path plus a `_GUARDED_ELSEWHERE` entry in `scripts/popoto_index_cleanup.py`, or a written proof it cannot produce an identity-less hash. Enforced as a Verification row.

### Risk 3: Drift detection silently narrows
**Impact:** `agent/index_drift.py` hardcodes `AgentSession`. Splitting into four models drops drift detection for three of them. The 2026-07-14 incident — corruption masquerading as emptiness, `query.all()` returning 0 while 11 hashes existed — is precisely why that detection exists.
**Mitigation:** Generalize `index_drift.py` in the same change as the first new model, not afterwards. Verification row asserts coverage.

### Risk 4: The obligation ledger inherits #2489's bug at scale
**Impact:** Two of two durable pending-flags in this subsystem have no release path today, and 2 of 57 live sessions currently carry the #2489 leak. A ledger is the same state shape at 10-100× the volume.
**Mitigation:** #2489 is a hard prerequisite for Milestone 3. Narrow trigger, discharge at the delivery site where the write site already lives, cap of 1 per Job, 24h silent expiry, operator-surface-only alarms in the first iteration.

### Risk 5: Concurrent build with #2491 in the same file
**Impact:** Both plans edit `models/agent_session.py`. Disjoint regions, but a shared checkout invites conflict.
**Mitigation:** Sequence, do not parallelize. Each uses its own worktree (`.worktrees/{slug}/`). Coordinate at the milestone boundary.

## Race Conditions

### Race 1: Steering dual-read against a live single-consumer drain
**Location:** `agent/steering.py:103-118`, `agent/session_executor.py:802-847`
**Trigger:** New-code consumer drains legacy + room keys while an old-code worker LPOPs the legacy key for the same session.
**Data prerequisite:** The dual-read consumer must be deployed *before* any writer flips to the room key.
**State prerequisite:** `pop_all_steering_messages` has a documented single-consumer invariant (`agent/steering.py:88-90`).
**Mitigation:** Two-phase deploy — ship the dual-read consumer first with writers unchanged (no restart ordering constraint, old workers keep working), then flip writers once all workers are on new code. No LRANGE+RPUSH copy script; that is what produces duplicate steers.

### Race 2: AgentRun written after the process is already dead
**Location:** `agent/session_runner/runner.py:660-670`
**Trigger:** Runner spawns the harness, and the process exits before the `AgentRun` row is persisted.
**Data prerequisite:** The row must exist before any recovery path can observe the run.
**State prerequisite:** Same capture-at-init contract that `claude_session_uuid` already honors (audit verified this as correct).
**Mitigation:** Create the `AgentRun` row at spawn on the same code path that already persists the resume handle at `system/init`, before any turn work. A row with a dead pid is recoverable; a missing row is not.

### Race 3: PID recycled between fence read and signal
**Location:** every `os.kill` / SIGTERM site, notably `agent/session_health.py:91-114`
**Trigger:** The recorded pid is recycled between reading `create_time` and sending the signal.
**Data prerequisite:** `(pid, create_time)` recorded at spawn.
**State prerequisite:** None available that closes the window — macOS has no `pidfd`.
**Mitigation:** Re-read `create_time` immediately before signalling and compare; document the residual sub-millisecond window explicitly. For runner-spawned children, prefer the retained handle. This is strictly better than today, where `pm_pid` is signalled with no fence at all.

### Race 4: Two Jobs minted for one message
**Location:** `bridge/job_router.py` (new), between inbox append and Job bind
**Trigger:** A crash after the durable append but before the Job binding; a recovery sweep then re-routes.
**Data prerequisite:** The inbox entry carries a stable message identity.
**State prerequisite:** Binding must be idempotent on `(room, message_id)`.
**Mitigation:** Bind via SETNX on the permanent `message_id → job_id` index. A re-route finds the existing binding and no-ops. This is the same at-least-once bias the existing `claim_message` uses, and matches the established preference for duplicate delivery over loss.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2498] Running the Job router on granite. Requires an Ollama provider branch in `agent/llm/wrapper.py` and measured latency; #2494 ships on Haiku.
- [SEPARATE-SLUG #2495] Fixing `steer_session` accepting writes to ledger sessions that no consumer drains.
- [SEPARATE-SLUG #2496] Fixing relay-sent PM messages recorded as `direction="in"`.
- [SEPARATE-SLUG #2497] Fixing reflection sessions pushing to `telegram:outbox` with `chat_id="0"`.
- [SEPARATE-SLUG #2489] The `deferred_self_draft_pending` release path. **Hard prerequisite for Milestone 3**, not merely deferred.
- [SEPARATE-SLUG #2490] Promise-gate drafter-path coverage and audit trail.
- [SEPARATE-SLUG #2420] The specific fire-and-forget instance stays independently closeable.
- [DESTRUCTIVE] Deleting orphaned `claude_pid`/`pm_pid`/`harness_pid` hash entries from **live, non-terminal** rows. The migration rewrites terminal rows only; live rows age out via `Meta.ttl`. Rewriting a running session's hash risks clobbering concurrent writes.
- [ORDERED] Landing any Codex-harness `AgentSession` fields from `docs/infra/harness-cross-compat.md`. Must wait until `AgentRun` ships, or the two designs collide.
- Feeding the Anthropic circuit breaker and adding 429/529 handling — an error-handling problem, not a durability-model one. Will be filed after this plan is approved so the reference is concrete rather than aspirational.

## Update System

`/update` changes are required:

- **Popoto migration registration.** Per `docs/sdlc/do-plan.md`, any Popoto model change adds an idempotent function to `scripts/update/migrations.py` registered in the `MIGRATIONS` dict, recorded once in `data/migrations_completed.json`. This plan needs one: strip the three pid fields from terminal rows. It must call the standalone script via subprocess rather than inlining the logic.
- **No new dependencies or config files** to propagate. `psutil` is already installed.
- **No new secrets.**
- **Ordering constraint:** the steering dual-read consumer must reach every machine before any machine flips its writers. `/update` already restarts bridge, watchdog, and worker together, so a single `/update` per machine is sufficient — but Milestone 2 must not ship the writer flip and the dual-read in the same release.

## Agent Integration

- **No new CLI entry point.** `valor-session` already exposes steer/resume; its behavior changes but its surface does not.
- **`valor-session status` and `resume` must read `AgentRun`** rather than `claude_pid`. `tools/valor_session.py:826-832` currently checks only that `claude_session_uuid` is non-`None`; it should validate against the `AgentRun` row (cwd exists, fence matches) and populate the existing-but-never-set `ResumeResult.warning` field (`:718-724`) when it degrades to a cold start.
- **The bridge calls the new router directly** — `bridge/job_router.py` is imported by the intake handler, same as `bridge/session_router.py` is today.
- **PM Job creation** is exposed as a tool with the Room scope enforced **at the tool layer**, not in the system prompt. Prompt-level constraints drift here: teammate permissions already live in three drifting sources and once falsely blocked `/do-issue`.
- **Integration test** must verify a PM can create a Job in its own Room and is refused for another Room.

## Documentation

- [ ] Rewrite `docs/features/session-recovery-mechanisms.md` to describe the new perimeter. It currently lists 10 mechanisms under a heading that says "(7)" and omits eight others — the rewrite replaces it rather than appending.
- [ ] Create `docs/features/durability-model.md` — the single place that answers "a session is durable because X", which does not exist today. This is the document whose absence the audit identified as the root problem.
- [ ] Update `docs/features/session-lifecycle.md` for the four-model split.
- [ ] Update `docs/features/session-steering.md` for the Room-owned inbox and the dual-read transition.
- [ ] Update `docs/features/harness-adapter.md` — the resume handle now lives on `AgentRun`.
- [ ] Update `docs/infra/harness-cross-compat.md` — the four-Codex-fields approach is superseded by `AgentRun.harness`.
- [ ] Add all new docs to the `docs/features/README.md` index table.
- [ ] Inline: document the fence's residual TOCTOU window at the fence implementation, with the LWN reference. A future reader must not mistake it for a guarantee.

## Success Criteria

- [ ] `AgentRun` exists with harness, nullable pid, `pid_start_time` fence, cwd, resume handle, and a low-cardinality `IndexedField` lifecycle status; parent link is a `KeyField` id string
- [ ] `claude_pid`, `pm_pid`, `harness_pid`, and `SessionHandle.pid` are deleted from the codebase
- [ ] Every `os.kill`/SIGTERM site compares the fence; a test that fakes PID reuse proves a recycled pid reads as dead
- [ ] An in-process Task subagent is representable as an `AgentRun` and visible to recovery; a test reproduces the #2420 shape and shows it detected
- [ ] `Room` resolves from `projects.json` and covers DMs and groups identically — the `if not chat_title: continue` filter is **removed**, not fixed
- [ ] The inbox is Room-owned; a steering message cannot be addressed to a terminated session
- [ ] `Job` exists with a required, PM-authored, versioned `goal`; Jobs are never hard-closed and any steer resumes one regardless of age
- [ ] Reply-to routes via a permanent `message_id → job_id` index with no TTL, including for outbound messages
- [ ] Ordering is 👀 → durable append → route → bind + reaction; a test kills the process between append and route and shows recovery
- [ ] The at-rest check flags "activity after last **authored** communication", with a regression test asserting the reference timestamps flag on authorship and do **not** flag on delivery
- [ ] Delivery timestamps are written after send success
- [ ] `agent/index_drift.py` covers all new models
- [ ] Each new model has a guarded repair path or a documented proof it cannot write an identity-less hash
- [ ] No new `IndexedField` holds an unbounded-cardinality value
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

- **Schema Reviewer**
  - Name: `schema-reviewer`
  - Role: Gate the KeyField/IndexedField sets before any model lands — one free shot
  - Agent Type: code-reviewer
  - Resume: true

- **Builder (AgentRun)**
  - Name: `agentrun-builder`
  - Role: `AgentRun` model, write sites, fence enforcement, pid-field deletion
  - Agent Type: builder
  - Resume: true

- **Builder (index safety)**
  - Name: `index-builder`
  - Role: Guarded repair paths, `index_drift` generalization, migration script
  - Agent Type: builder
  - Resume: true

- **Builder (health check)**
  - Name: `health-builder`
  - Role: `last_authored_at`, at-rest check, delivery timestamp fix
  - Agent Type: builder
  - Resume: true

- **Builder (Room)**
  - Name: `room-builder`
  - Role: `Room` model, config resolution, scanner filter removal, inbox dual-read
  - Agent Type: builder
  - Resume: true

- **Builder (Job)**
  - Name: `job-builder`
  - Role: `Job` model, `job_router`, permanent reply index, obligation ledger
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `durability-validator`
  - Role: Verify each milestone against its criteria
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: `durability-tester`
  - Role: Regression tests, especially the authorship-vs-delivery pair and the #2420 reproduction
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `durability-docs`
  - Role: The documentation tasks above
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Schema review gate
- **Task ID**: review-schema
- **Depends On**: none
- **Assigned To**: schema-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Write down every access path for `Room`, `Job`, `AgentRun` before any field is declared
- Confirm each path is served by a `KeyField`, `IndexedField`, or `SortedField` — plain fields are not queryable
- Confirm no `IndexedField` holds an unbounded-cardinality value
- Confirm the KeyField set for each model; this is a one-shot decision
- **Blocks all model work.** Output is an approved field list.

### 2. AgentRun model + write sites
- **Task ID**: build-agentrun
- **Depends On**: review-schema
- **Validates**: tests/unit/test_agent_run.py (create), tests/integration/test_harness_resume.py
- **Informed By**: spike-2 (authorship exists in session_events), Research (fence is best-effort)
- **Assigned To**: agentrun-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Declare `AgentRun` per the approved field list; parent is a `KeyField` id string, never a `Relationship`
- Create the row at spawn on the same path that persists the resume handle at `system/init` (Race 2)
- Record `(pid, create_time)` at spawn; `pid=None` for in-process subagents, with `agent_id` instead
- Terminalize from exactly one writer, mirroring `_apply_recovery_transition`'s discipline

### 3. Fence enforcement + pid-field deletion
- **Task ID**: build-fence
- **Depends On**: build-agentrun
- **Validates**: tests/unit/test_pid_fence.py (create), tests/unit/test_session_health_orphan_reap.py
- **Assigned To**: agentrun-builder
- **Agent Type**: builder
- **Parallel**: false
- Compare the fence at every `os.kill`/SIGTERM site, notably `agent/session_health.py:91-114`
- Delete `claude_pid`, `pm_pid`, `harness_pid`, `SessionHandle.pid` and the dead `notify_sdk_started` path
- Delete `tests/unit/test_messenger_callbacks.py` cases for the removed callback
- Document the residual TOCTOU window inline with the LWN reference

### 4. Index safety + migration
- **Task ID**: build-index-safety
- **Depends On**: build-agentrun
- **Validates**: tests/unit/test_index_drift_coverage.py (create), tests/unit/test_agentsession_pending_index_leak.py
- **Assigned To**: index-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Give `AgentRun` a guarded repair path; add a `_GUARDED_ELSEWHERE` entry in `scripts/popoto_index_cleanup.py`
- Generalize `agent/index_drift.py` past its hardcoded `AgentSession`
- Write the pid-field strip migration modelled on `scripts/migrate_strip_pty_fields.py`: dry-run default, `--apply`, terminal rows only, ORM-only writes, idempotent
- Register it in `scripts/update/migrations.py`'s `MIGRATIONS` dict

### 5. Authorship health check
- **Task ID**: build-health-check
- **Depends On**: build-agentrun
- **Validates**: tests/unit/test_at_rest_owed_communication.py (create)
- **Informed By**: spike-2 (authorship at adapter.py:499/:534; reactions have NO durable record)
- **Assigned To**: health-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `last_authored_at`, written at the two existing save sites `adapter.py:491` and `:526`
- Implement the at-rest check: no live `AgentRun` AND last activity > last authored comms
- Activity anchor is `max(last_stdout_at, last_tool_use_at, last_turn_at)` — `ui/data/sdlc.py:1042` already computes this
- Account for the 5s liveness cooldown and the 200-entry `session_events` trim
- Move the delivery timestamp write to after send success

### 6. Validate Milestone 1
- **Task ID**: validate-m1
- **Depends On**: build-fence, build-index-safety, build-health-check
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false
- **Milestone 1 is independently shippable — stop here for PM review before Milestone 2.**

### 7. Room model + resolution
- **Task ID**: build-room
- **Depends On**: validate-m1
- **Validates**: tests/unit/test_room_resolution.py (create), tests/integration/test_dm_recovery.py (create)
- **Informed By**: spike-1 (system Room for chatless sessions; high confidence)
- **Assigned To**: room-builder
- **Agent Type**: builder
- **Parallel**: false
- `Room = (project_key, addressee)`; addressee ∈ `telegram:{chat_id}` | `email:{address}` | `system`
- Resolve from `projects.json` via the existing `find_project_for_{chat,dm,email}`; config stays source of truth
- **Remove** the `if not chat_title: continue` filter from all three scanners — they iterate Rooms, not Telegram dialogs
- Give `Room` a guarded repair path and extend `index_drift` coverage

### 8. Room-owned inbox (dual-read, phase 1)
- **Task ID**: build-inbox-dualread
- **Depends On**: build-room
- **Validates**: tests/integration/test_steering.py
- **Informed By**: spike-3 (no migration needed; dual-read before writer flip)
- **Assigned To**: room-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- `pop_all_steering_messages` drains the legacy session key first, then the room key
- **Writers unchanged in this task** (Race 1) — the writer flip is a separate release
- Outbox room keys go under the existing `telegram:outbox:` prefix so the pattern-scanning relay drains them unmodified
- Fix `agent/health_check.py:511` — re-push the abort's siblings

### 9. Validate Milestone 2
- **Task ID**: validate-m2
- **Depends On**: build-inbox-dualread
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false
- **Milestone 2 is independently shippable — stop here for PM review before Milestone 3.**

### 10. Job model + router
- **Task ID**: build-job
- **Depends On**: validate-m2
- **Validates**: tests/unit/test_job_router.py (create), tests/integration/test_job_routing.py (create)
- **Informed By**: spike-4 (narrow trigger; #2489 is a hard prerequisite)
- **Assigned To**: job-builder
- **Agent Type**: builder
- **Parallel**: false
- `Job` with a required, PM-authored, append-only-versioned `goal`; rest by age, never hard-closed, always resumable
- `bridge/job_router.py` as a sibling of `session_router.py`, on Haiku via `run_typed`
- Post-hoc `valid_ids` membership check; anything outside the candidate set becomes NEW
- Permanent `message_id → job_id` index, no TTL, bound via SETNX (Race 4), written for outbound messages too
- PM Job creation enforced to the session's own Room **at the tool layer**

### 11. Obligation ledger
- **Task ID**: build-obligations
- **Depends On**: build-job
- **Validates**: tests/unit/test_obligation_ledger.py (create)
- **Informed By**: spike-4 (0.44 forward-deferrals/day; expectations has 0 rows; 2 live #2489 leaks)
- **Assigned To**: job-builder
- **Agent Type**: builder
- **Parallel**: false
- **Gated on #2489 being merged.** Verify before starting.
- Trigger on `forward_deferral` without a scheduled-delivery reference only — **not** `behavioral_change`
- Auto-discharge at the delivery site on any subsequent substantive outbound; also on `schedule_id` fire
- Cap 1 open obligation per Job; 24h silent expiry with an audit row; at-rest evaluation only
- Route alarms to the operator surface, not human chat, until the false-positive rate is measured
- Add a durable record for reactions (new work — `react_with_emoji.py` holds no session handle today)

### 12. Documentation
- **Task ID**: document-feature
- **Depends On**: build-obligations
- **Assigned To**: durability-docs
- **Agent Type**: documentarian
- **Parallel**: false
- All tasks in the Documentation section above

### 13. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| pid fields deleted | `grep -rn "claude_pid\|pm_pid\|harness_pid" --include=*.py agent/ models/ bridge/ tools/ ui/ \| wc -l` | output contains 0 |
| Dead callback deleted | `grep -rn "notify_sdk_started" --include=*.py . \| grep -v tests/ \| wc -l` | output contains 0 |
| DM filter removed | `grep -rn 'if not chat_title' --include=*.py bridge/ \| wc -l` | output contains 0 |
| No Relationship self-ref | `grep -rn "Relationship(" --include=*.py models/ \| wc -l` | output contains 0 |
| index_drift generalized | `grep -c "AgentRun\|Room\|Job" agent/index_drift.py` | output > 0 |
| Guarded repair registered | `grep -c "_GUARDED_ELSEWHERE" scripts/popoto_index_cleanup.py` | output > 0 |
| Migration registered | `grep -c "strip_pid_fields" scripts/update/migrations.py` | output > 0 |
| Authorship anchor test exists | `grep -rn "authored" tests/unit/test_at_rest_owed_communication.py` | exit code 0 |
| Anti-criterion: no live-row rewrite | `grep -n "status.*running" scripts/migrate_strip_pid_fields.py` | match count == 0 |
| Anti-criterion: no unbounded index | `grep -nE "IndexedField" models/agent_run.py \| grep -iE "pid\|uuid\|_at\b"` | match count == 0 |
| Anti-criterion: router not merged into session_router | `grep -c "Job" bridge/session_router.py` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Milestone 3 gating.** #2489 is a hard prerequisite for the obligation ledger. Should this plan absorb the #2489 fix (it is small — one release path on a flag with one write site), or stay blocked on it landing separately?
2. **`behavioral_change` trigger class.** Spike-4 recommends excluding it entirely from obligation writes: 1 real block in 90 days against 39 forward-deferrals, with a 30-50% estimated false-positive rate. Confirm exclusion, or keep it and accept the noise?
3. **Reaction durability.** Making a reaction discharge an obligation requires building a durable reaction record that does not exist today. Is that in scope for Milestone 3, or does the first iteration require an actual message to discharge?
4. **Sequencing against #2491.** Both plans edit `models/agent_session.py`. Which lands first?
5. **`AgentSession` naming.** With `Room` owning the conversation and `AgentRun` owning execution, `AgentSession` means "an agent's context / resume handle". Is that name still right, or should it become something like `AgentContext` while we are already migrating readers?
