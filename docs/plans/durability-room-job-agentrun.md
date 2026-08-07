---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-31
tracking: https://github.com/tomcounsell/ai/issues/2494
last_comment_id: 5142251045
revision_applied: true
revision_applied_at: 2026-07-31T08:56:37Z
---

# Durability: Room / Job / AgentSession

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
- "Work was promised and not delivered" is a queryable state — as PM-authored promises on the Job, not as classifier output.
- Crash-resume, reply-resume, and steering are one code path.
- Four pid-like fields collapse into one fenced execution record **on `AgentSession`**.
- Naming is harness-agnostic, so codex/opencode/pi need no schema change beyond the `harness` enum.

**Model shape (owner decision, 2026-07-31):** `AgentSession` and the execution record are the same thing — there is no separate `AgentRun` model. The only new models are the two higher-order objects, **`Room`** and **`Job`**. The pid consolidation and `(pid, create_time)` fence land as fields on `AgentSession`; a crash-resume overwrites the fence fields at each spawn, exactly as today's pid fields already behave. `AgentSession` keeps its name.

## Freshness Check

**Baseline commit:** `ac33726ca5e9e8e4dcfdaea3798dc0b2f8880bbc`
**Issue filed at:** 2026-07-31T05:41:17Z
**Disposition:** Overlap (proceeding; coordination note below)
**Revision pass:** 2026-07-31 — critique findings and six owner decisions folded in; #2489 fixed on main at `ac3a87d51` during the revision.

**File:line references re-verified:** The issue was filed minutes before this plan, and every reference in it was established by five audit agents plus three recon agents against commit `7d441fed1`. The four spikes below independently re-read the same code at `ac33726ca` and corrected several premises — those corrections are recorded in Spike Results and are the authoritative version.

**Cited sibling issues/PRs re-checked:**
- #2420 — OPEN. The motivating incident. Stays independently closeable; this plan dissolves the class, not the instance.
- #2489 — **CLOSED (fixed 2026-07-31, commit `ac3a87d51`)**: the clean-draft send path at `agent/output_handler.py:658-679` now clears `deferred_self_draft_pending`. No longer a prerequisite for anything in this plan.
- #2490 — OPEN, confirmed line-by-line by spike-4. Stays separate.
- #2421 — OPEN, has its own plan at `docs/plans/promise-gate-short-output-reachability.md`. Adjacent; not absorbed.
- #1925 — CLOSED. Migrated bridge classifiers off Ollama onto Haiku. **Deliberately partially reversed by this plan (owner decision, 2026-07-31)**: the two hot-path classifiers (intake, Job router) return to the local granite model via PydanticAI — viable now because the durable inbox removes latency from the durability path, and classification is exactly what granite is sanctioned for. #2498 is absorbed by this plan.
- #2207 — the 7.4M-key Redis flood. Its guard is `AgentSession`-specific and constrains every new model here.

**Commits on main since the issue was filed:**
- `ac33726ca Plan: Pipeline Graph — Single Source of Truth (#2491)` — a plan document only, no code. **Overlap:** #2491 will edit `models/agent_session.py` (the `SDLC_STAGES` constant at `:81`) while this plan edits pid fields in the same file. Regions are disjoint; both are Large. Sequence, do not parallelize; **no ordering preference (owner decision)** — whichever pipeline is ready first builds first, each in its own worktree.

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

Also: **#1721** (CLOSED) — *"Granite sessions can't resume where they stopped: persist resume handles."* The resume-handle persistence it established is preserved unchanged on `AgentSession`.

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
- **Correction to the issue**: reactions had no durable record anywhere. `_send_queued_reaction` (`bridge/telegram_relay.py:117-179`) returns a bool and writes nothing. **Resolution (owner, 2026-07-31): no new subsystem — a sent reaction is recorded as a reply-to message in the existing message log with escaped, parseable content (e.g. `<reaction>:thumbs-up:</reaction>`).**
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
- **Finding**: **yes, on the design as stated.** Running `_evaluate_promise_heuristic` against the proposed trigger phrases: `"I'm working on it"`, `"I'll take a look"`, `"let me check"`, and `"on it"` **all currently ALLOW**. Any mechanical trigger is strictly broader than anything deployed, so ledger volume cannot be extrapolated from the gate's current block rate. Worse: **every durable pending-flag in this subsystem lacked a release path at audit time.** `deferred_self_draft_pending` had one write site and no release (since fixed — #2489, `ac3a87d51`). `AgentSession.expectations` is written only when non-`None` (`agent/output_handler.py:1139`) and the drafter returns `None` rather than `""` when empty, so once set it is never cleared.
- **Real data**: of 57 live sessions, **0 carry a non-empty `expectations`** — the existing partial ledger has no rows — and 2 carried the #2489 leak at audit time. The audited promise-gate paths produced 39 `forward_deferral` blocks in 88 days ≈ 0.44/day.
- **Confidence**: high
- **Impact on plan (superseded by owner decision, 2026-07-31)**: the spike's evidence killed the mechanical-trigger design entirely. **No trigger class writes obligations.** The promise-gate classifier becomes *advisory to the PM* — "sounds like you're promising X, and we don't make false promises; revise or override" — and the PM, as the intelligent actor, either rewrites the message or deliberately stands by the promise. Promises are PM-authored and PM-discharged, stored as appended/removed entries on the Job's append-only-versioned `goal`. No separate obligation model, no trigger taxonomy, no auto-discharge machinery.

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
5. Route: reply-to → permanent `message_id → job_id` index, no model call. Otherwise → `bridge/job_router.py` (granite via PydanticAI, JSON decision, fail-open to NEW). **Job routing replaces session-level semantic routing — `bridge/session_router.py`'s call site is deleted in the same milestone.**
6. Bind message to Job; emit 🤔 (new Job) or the existing steer reaction.

**Outbound (unchanged except two stamps):**

runner emits → `adapter._on_user`/`_on_complete` (**authorship stamped here**) → `output_handler` drafter/redundancy/read-the-room → `rpush telegram:outbox:{...}` → `telegram_relay.process_outbox` LPOP → Telethon send → **delivery stamped here, after success**.

## Architectural Impact

- **New dependencies**: none. No new packages; `psutil` is already a dependency and provides `create_time()` cross-platform.
- **Interface changes**: `AgentSession` loses `claude_pid`, `pm_pid`, `harness_pid`, and `expectations`; it gains one fenced execution record — `harness` enum, nullable execution pid, `pid_create_time` fence, execution cwd (final names set by the schema review gate). `SessionHandle.pid` is deleted. `agent/steering.py` gains a Room-scoped key with a legacy dual-read leg. **Two** new Popoto models: `Room` and `Job`.
- **Coupling**: net decrease. Four pid fields and three liveness derivations collapse into one fenced record. But note the honest counter-argument from recon: of five modules nominated to consolidate under Room, only `bridge/read_the_room.py` genuinely does (35 `chat_id` refs, 0 `session_id`). `bridge/redundancy_filter.py` is per-session *by explicit design* (`:133`) and moving it would change suppression semantics; `message_drafter`, `message_quality`, and `promise_gate` are stateless. The modules that do consolidate are `bridge/dedup.py` and `bridge/context.py`, already chat-keyed.
- **Data ownership**: the inbox moves from session to Room. This is the single most consequential change — it makes a message addressed to a dead object impossible by construction, which is the root of the orphaned-steering class.
- **Reversibility**: Milestone 1 is highly reversible (field additions + field deletions with a scripted migration). Milestone 2 is moderately reversible (dual-read leg can be left in place). Milestone 3 is additive except the session-router retirement, which is a deliberate cutover.
- **Execution-record semantics**: one `AgentSession` = one resumable body of agent work. A crash-resume spawns a new process for the same session and **appends** a new fence record to the session's spawn history; the newest entry is the live fence the recovery paths read. Full spawn history is preserved for the session's lifetime (bounded by `Meta.ttl`) — a died-resumed-died-again timeline stays reconstructable. Likewise the 200-entry `session_events` trim (`adapter.py:237`) is **removed**: events are the forensic record and are bounded by the same session TTL, not by an arbitrary count.
- **Harness cross-compat**: `docs/infra/harness-cross-compat.md` planned Codex support as four nullable `AgentSession` fields. With the execution record living on `AgentSession`, that direction is compatible: this plan lands the `harness` enum; the remaining cross-compat fields ride on `AgentSession` later with no collision. The previous AgentRun-vs-fields conflict is dissolved; no ordering constraint remains.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (one per milestone boundary — each milestone is independently shippable and independently abortable)
- Review rounds: 2+ (schema review before any new model lands, since KeyField sets get exactly one free shot; full review per milestone)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable with AOF on | `redis-cli INFO persistence \| grep -q 'aof_enabled:1'` | Durable writes for new models |
| `psutil` importable | `python -c "import psutil; psutil.Process().create_time()"` | PID-reuse fence |
| Popoto ≥ 1.8.0 | `python -c "import popoto; print(popoto.__version__)"` | `_create_lazy_model` default-fill behavior this plan relies on |
| granite reachable via Ollama | `curl -s localhost:11434/api/tags \| grep -qi granite` | Local model for the intake classifier and Job router |

## Solution

### Key Elements

- **`Room`**: the environment a conversation happens in. `(project_key, addressee)` where addressee is `telegram:{chat_id}` | `email:{address}` | `system`. Resolved from `projects.json`, which stays the source of truth — Room caches the resolved binding, it does not replace the config. Owns addressing, participants, and the inbox. Immortal.
- **`Job`**: a responsibility to complete something end to end. Carries a required, append-only-versioned `goal`. **The router is not smart enough to write a goal** (it may run on a small local model): at mint it stamps only a mechanical placeholder — `handle user message '<first 20 chars of message>…'` — so `goal` is never null and the synchronous bind-or-mint path never blocks. **Authoring the real goal (v1) is the PM's first step on the Job**, mandated in the PM's context priming / system prompt, not left to discretion. The PM's promises live on the goal as appended/removed entries (see Milestone 3). Never hard-closed; goes to rest by age; always resumable by a new steering message regardless of age. **This revival-after-apparent-completion is new user-visible behavior**, documented as such.
- **`AgentSession`**: an agent's context — resume handle, `parent_agent_session_id` hierarchy, **and the execution record**: `harness` enum, nullable execution pid, `pid_create_time` fence, execution cwd. These replace `claude_pid`, `pm_pid`, `harness_pid`, and `SessionHandle.pid`. Each spawn **appends** a fence record to the session's spawn history (newest entry = live fence); history persists for the session's TTL. There is no separate run model.

### Flow

Message arrives → 👀 (*bridge saw it*) → durable Room-inbox append → route → 🤔 or steer reaction (*durably assigned*) → PM works → PM speaks → at rest, health check compares last **authored** communication against last activity.

A message stuck at 👀 with no follow-on reaction is itself the alarm — visible in the chat without a dashboard, and queryable as "Room inbox entry with no Job binding older than N".

### Technical Approach

**Schema (all constraints verified by recon — do not re-litigate):**

- Parent links are `KeyField(null=True)` holding an id string. Popoto's `Relationship` requires the actual class object, so **self-reference is impossible**; there are zero `Relationship` uses in `models/`, and `AgentSession` already uses the id-string pattern.
- Nothing is queryable unless it is a `KeyField`, `IndexedField`, or `SortedField`. Plain `Field`/`DatetimeField`/`IntField` raise `QueryException` on `.filter()`. So **`ended_at IS NULL` is not queryable** — liveness is a low-cardinality `IndexedField` status, mirroring the existing terminal/non-terminal vocabulary. `__isnull` has zero usages repo-wide.
- `IndexedField` creates one Redis Set **per distinct value**. Never index a pid, uuid, or timestamp. (`claude_pid` is currently exactly this anti-pattern, so deleting it is a net win.)
- Every `KeyField` becomes part of the primary Redis key, sorted alphabetically. Adding one to an existing model forces a full key-rewrite migration — **`Room` and `Job` get one free shot at their KeyField sets**, which is why schema review gates all model work. The `AgentSession` additions are plain nullable fields (no KeyField changes), default-filled by `_create_lazy_model` — no data migration for the additions; one scripted migration strips the deleted pid fields from terminal rows.
- `_heal_descriptor_pollution` does **not** exist (removed as dead under Popoto 1.8.0, #2083) — do not cite it.

**Fence:** `(pid, create_time)` via `psutil`, stored on `AgentSession`, documented as **best-effort detection**, not a guarantee — there is an irreducible TOCTOU window and macOS has no `pidfd`. For runs the runner spawned, the retained child handle is primary and the fence is the backstop.

**Router (owner decision, 2026-07-31): both hot-path classifiers — the intake classifier (`bridge/telegram_bridge.py:2168`) and the new bind-or-mint Job router — run on the local granite model via PydanticAI.** Granite is good at classification and tool calling; each call is deliberately very simple: a small prompt, a strict Pydantic output model, JSON decision out (e.g. `{"decision": "bind" | "new", "job_id": str | null, "confidence": float}`). PydanticAI keeps the call site a few lines and model-agnostic (Ollama provider for granite), per the two-transport rule — harness for session work, PydanticAI for all non-harness LLM calls. This works because the durable Room-inbox append happens **before** routing: the message is already safe, so router latency is a UX concern, not a durability concern.

Structure of `bridge/job_router.py` is modeled on `bridge/session_router.py`: zero-candidate short-circuit (`:83-88`), top-5 recency cap (`:90-95`), **post-hoc `valid_ids` membership check** (`:151-159`), total fail-open to NEW (`:175-178`) — worst case is an extra Job, never a lost or wrong-bound message. **Job routing replaces session-level semantic routing**: in the same milestone, the intake handler's `session_router` call site is deleted, `bridge/session_router.py` is removed, and the write-only `AgentSession.expectations` field and its write path (`agent/output_handler.py:1139-1143`) are deleted (spike-4: zero non-empty rows across all live sessions). One classifier, one routing authority — per NO LEGACY CODE TOLERANCE. This absorbs #2498 (router-on-granite): `spike-router-proto` supplies the measured latency and accuracy that issue asked for. **The router never authors `goal`** — on a NEW verdict it stamps the mechanical placeholder (`handle user message '<first 20 chars>…'`) and the PM's priming makes goal-authoring the first step of its first turn on the Job. This keeps the router swappable onto a small local model with no quality dependency.

**Promises (Milestone 3, reframed by owner decision 2026-07-31):** the promise-gate classifier becomes **advisory**: when an outbound message reads like a deferral, the gate returns a suggestion to the PM — *"sounds like you're promising X; we don't make false promises — revise or override"* — instead of mechanically writing an obligation. The PM either rewrites the message or stands by it and **appends the promise to `Job.goal`** (a new goal version). Discharge is likewise PM-authored: the PM removes the promise entry (another goal version) when delivered. The at-rest health check surfaces Jobs at rest with an open promise entry to the **operator surface** (not human chat) as the backstop. No trigger-class taxonomy, no auto-discharge machinery, no separate obligation model, no cap/expiry bookkeeping.

The outbound classification pass doubles as the goal-reset nudge: when it evaluates an outgoing message and the bound Job's `goal` is still the mint placeholder, its advisory response additionally reminds the PM to author the goal. This gives the goal mandate two enforcement points — the priming (first turn) and the outbound advisory (every send until authored) — both suggestions to the intelligent actor, never mechanical writes.

**Reactions:** a sent reaction is recorded through the existing message log as a reply-to message with escaped, parseable content — `<reaction>:thumbs-up:</reaction>` — at the same site that records sent messages (`bridge/telegram_relay.py` success path). No new model, no new subsystem.

**Inbox migration:** none. Outbox room keys ship under the existing `telegram:outbox:` / `email:outbox:` prefix and the pattern-scanning relay drains them unmodified; session-scoped stragglers self-expire on their existing 1h TTL. Steering gets a drain-only dual-read shipped *before* writers flip.

**At-rest check invocation (critique finding, resolved):** the check is invoked from the existing periodic health sweep in `agent/session_health.py` — the same cadence that runs the orphan reap today. A Verification row asserts the caller exists, and a test asserts the sweep actually invokes it, so the check cannot ship as correct-logic-with-a-dead-caller.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/steering.py` re-push failures (`agent/session_runner/runner.py:624-629` currently logs and drops) — test must assert an observable signal, not silent loss
- [ ] `agent/session_pickup.py:235-240` catches a save exception and logs "non-fatal" after a destructive LPOP — test must assert the messages are recoverable or the failure is loud
- [ ] `bridge/job_router.py` fail-open path — test asserts a model error yields NEW Job, never a wrong binding
- [ ] Fence read failure (`psutil.NoSuchProcess`, `AccessDenied`) — test asserts "treat as dead" and that this is logged

### Empty/Invalid Input Handling
- [ ] Router with zero candidate Jobs → short-circuits without a model call
- [ ] Router returns an id not in the candidate set → treated as NEW
- [ ] `Job.goal` at mint → always the mechanical placeholder (`handle user message '<first 20 chars>…'`); never null, never model-authored by the router
- [ ] `AgentSession` with execution pid `None` (in-process subagent) → all fence code paths handle it without raising

### Error State Rendering
- [ ] A message that fails to bind to a Job leaves the 👀-orphan state visible and queryable
- [ ] The at-rest health check's output reaches the operator surface, and a failure inside the check is logged rather than swallowed

## Test Impact

- [ ] `tests/unit/test_d3_silent_failure_logging.py:50-55` — **DELETE**: it asserts `"circuit.record_failure" not in source`, i.e. CI currently enforces that the circuit breaker stays unfed. Feeding the circuit is out of scope for this plan, but this assertion must be re-scoped or removed so it does not block the follow-up; at minimum add a comment naming the dead subsystem.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` — UPDATE: reaper moves from `claude_pid` to the fenced execution fields on `AgentSession`
- [ ] `tests/unit/test_session_health_orphan_reap.py` — UPDATE: same
- [ ] `tests/unit/test_session_health_phantom_guard.py` — UPDATE: phantom guard must cover `Room` and `Job`
- [ ] `tests/unit/test_messenger_callbacks.py:33,55,70` — **DELETE**: tests `notify_sdk_started`, which this plan deletes as dead code
- [ ] `tests/unit/test_agentsession_pending_index_leak.py` — UPDATE: index assertions extend to `Room` and `Job`
- [ ] `tests/integration/test_steering.py` (69 steering call sites) — UPDATE: dual-read leg; assert legacy-key drain still works during the transition
- [ ] `tests/integration/test_crash_auto_resume.py` — UPDATE: resume path reads the fenced execution fields
- [ ] `tests/integration/test_harness_resume.py` — UPDATE: resume validation checks the fence and cwd
- [ ] `tests/unit/test_session_pickup_phantom_filter.py` — UPDATE
- [ ] `tests/unit/test_recovery_ownership.py` — UPDATE: `RECOVERY_OWNERSHIP` extends past `NON_TERMINAL_STATUSES`
- [ ] `tests/unit/test_promise_gate_session_events.py` — UPDATE (Milestone 3): gate becomes advisory; assert suggestion surfaces to the PM instead of a mechanical write
- [ ] `tests/unit/test_session_modal_liveness_render.py` — UPDATE: dashboard reads the fenced execution fields for liveness
- [ ] Session-router tests (`grep -rl "session_router" tests/`) — **DELETE/REPLACE** in Milestone 3: routing authority moves to `bridge/job_router.py`

## Rabbit Holes

- **Feeding the Anthropic circuit breaker.** The audit found the whole pause/hibernate/resume subsystem is wired to a `record_failure` with no callers, and no 429/529 handling exists anywhere. Fixing it is genuinely valuable and genuinely separate — it is an error-handling problem, not a durability-model problem. Resist folding it in; it will double the review surface. Filed separately.
- **Rewriting `session_health.py`.** It is 5,705 lines with 61 issue references and 61 commits in 90 days. The temptation to "clean it up while we're in there" is enormous and would swallow the whole appetite. This plan changes only the pid-lookup sites, the at-rest check wiring, and the index-drift generalization.
- **Making `Room` absorb the comms layer.** Recon measured this: only 1 of 5 nominated modules genuinely consolidates. Moving stateless formatters under Room is re-parenting with no gain, and moving `redundancy_filter` changes suppression semantics. Move `read_the_room` only; leave the rest.
- **Perfect PID fencing.** macOS has no `pidfd`. Chasing an airtight fence leads to kqueue `EVFILT_PROC` plumbing for marginal gain over `(pid, create_time)` plus a retained child handle. Document the residual window and move on.
- **A `Turn` model.** A third concept (the queue item) is genuinely hiding in `AgentSession` — it is why two `pending` rows can exist for one conversation. It is real, and it is not this plan.

## Risks

### Risk 1: KeyField sets are a one-shot decision
**Impact:** Every `KeyField` is part of the primary Redis key, sorted alphabetically. Getting the set wrong on `Room`/`Job` means a full key-rewrite migration on a model that by then holds live data.
**Mitigation:** A dedicated schema review gate before any model lands (Task 1). Write the access paths down first and confirm each is served by a `KeyField`/`IndexedField`/`SortedField`, since plain fields are not queryable at all.

### Risk 2: A new model re-triggers the #2207 phantom index flood
**Impact:** The identity-less-hash guard in `repair_indexes()` is `AgentSession`-specific. Registering a new model in `models.__all__` opts it into the generic `rebuild_indexes()` sweep, which has no such guard — the exact mechanism behind the 7.4M-key Redis flood of 2026-07-22.
**Mitigation:** **Both** new models (`Room`, Task 10; `Job`, Task 13) ship with their own guarded repair path plus a `_GUARDED_ELSEWHERE` entry in `scripts/popoto_index_cleanup.py`, or a written proof they cannot produce an identity-less hash. The Verification row asserts the literal presence of **both** model names — a one-model omission fails the check.

### Risk 3: Drift detection silently narrows
**Impact:** `agent/index_drift.py` hardcodes `AgentSession`. Adding models without extending it drops drift detection for them. The 2026-07-14 incident — corruption masquerading as emptiness, `query.all()` returning 0 while 11 hashes existed — is precisely why that detection exists.
**Mitigation:** Generalize `index_drift.py` in Milestone 1; each new model extends coverage in its own task. Verification row asserts coverage of both new models.

### Risk 4: The advisory promise flow silently regresses to a nag machine
**Impact:** Spike-4 showed every mechanical trigger design either under-fires (current gate) or over-fires (proposed phrases). If the advisory suggestion is ignored by the PM prompt-side, promises never get authored and the feature is dead code; if a future change re-mechanizes the write, volume explodes.
**Mitigation:** Promises are PM-authored only — the gate returns a suggestion, never writes. The at-rest backstop surfaces open promises to the operator surface only, until measured. A test asserts the gate's advisory path performs zero writes.

### Risk 5: Concurrent build with #2491 in the same file
**Impact:** Both plans edit `models/agent_session.py`. Disjoint regions, but a shared checkout invites conflict.
**Mitigation:** Sequence, do not parallelize; no ordering preference (owner decision) — whichever pipeline is ready first. Each uses its own worktree (`.worktrees/{slug}/`). Coordinate at the milestone boundary.

### Risk 6: Granite quality or availability on the hot path
**Impact:** Both hot-path classifiers move to the local granite model. A weak prompt over-mints Jobs; an unreachable Ollama daemon takes both classifiers down at once.
**Mitigation:** `spike-router-proto` measures accuracy and latency before any commitment; both call sites fail open (intake → default classification, router → NEW Job) so the failure direction is always an extra Job or a conservative default, never a lost or wrong-bound message. The message is durable in the Room inbox before either classifier runs.

## Race Conditions

### Race 1: Steering dual-read against a live single-consumer drain
**Location:** `agent/steering.py:103-118`, `agent/session_executor.py:802-847`
**Trigger:** New-code consumer drains legacy + room keys while an old-code worker LPOPs the legacy key for the same session.
**Data prerequisite:** The dual-read consumer must be deployed *before* any writer flips to the room key.
**State prerequisite:** `pop_all_steering_messages` has a documented single-consumer invariant (`agent/steering.py:88-90`).
**Mitigation:** Two-phase deploy — ship the dual-read consumer first with writers unchanged (no restart ordering constraint, old workers keep working), then flip writers once all workers are on new code. No LRANGE+RPUSH copy script; that is what produces duplicate steers.

### Race 2: Fence fields stamped after the process is already dead
**Location:** `agent/session_runner/runner.py:660-670`
**Trigger:** Runner spawns the harness, and the process exits before the fence fields are persisted to `AgentSession`.
**Data prerequisite:** The fence must be recorded before any recovery path can observe the spawn.
**State prerequisite:** Same capture-at-init contract that `claude_session_uuid` already honors (audit verified this as correct).
**Mitigation:** Stamp `(pid, create_time, cwd, harness)` at spawn on the same code path that already persists the resume handle at `system/init`, before any turn work. Stale fence fields pointing at a dead pid are recoverable; missing fence fields are not.

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

- [SEPARATE-SLUG #2495] Fixing `steer_session` accepting writes to ledger sessions that no consumer drains.
- [SEPARATE-SLUG #2496] Fixing relay-sent PM messages recorded as `direction="in"`.
- [SEPARATE-SLUG #2497] Fixing reflection sessions pushing to `telegram:outbox` with `chat_id="0"`.
- [SEPARATE-SLUG #2490] Promise-gate drafter-path coverage and audit trail.
- [SEPARATE-SLUG #2420] The specific fire-and-forget instance stays independently closeable.
- [DESTRUCTIVE] Deleting orphaned `claude_pid`/`pm_pid`/`harness_pid` hash entries from **live, non-terminal** rows. The migration rewrites terminal rows only; live rows age out via `Meta.ttl`. Rewriting a running session's hash risks clobbering concurrent writes.
- Feeding the Anthropic circuit breaker and adding 429/529 handling — an error-handling problem, not a durability-model one. Will be filed after this plan is approved so the reference is concrete rather than aspirational.

## Update System

`/update` changes are required:

- **Popoto migration registration.** Per `docs/sdlc/do-plan.md`, any Popoto model change adds an idempotent function to `scripts/update/migrations.py` registered in the `MIGRATIONS` dict, recorded once in `data/migrations_completed.json`. This plan needs one: strip the three pid fields (and `expectations`) from terminal rows. It must call the standalone script via subprocess rather than inlining the logic.
- **No new dependencies or config files** to propagate. `psutil` is already installed.
- **No new secrets.**
- **Ordering constraint:** the steering dual-read consumer must reach every machine before any machine flips its writers. `/update` already restarts bridge, watchdog, and worker together, so a single `/update` per machine is sufficient — but Milestone 2 must not ship the writer flip and the dual-read in the same release.

## Agent Integration

- **No new CLI entry point.** `valor-session` already exposes steer/resume; its behavior changes but its surface does not.
- **`valor-session status` and `resume` must read the fenced execution fields** rather than `claude_pid`. `tools/valor_session.py:826-832` currently checks only that `claude_session_uuid` is non-`None`; it should validate the fence (cwd exists, `(pid, create_time)` matches or pid is dead) and populate the existing-but-never-set `ResumeResult.warning` field (`:718-724`) when it degrades to a cold start.
- **The bridge calls the new router directly** — `bridge/job_router.py` is imported by the intake handler, replacing the `bridge/session_router.py` import.
- **PM Job creation and promise add/remove** are exposed as tools with the Room scope enforced **at the tool layer**, not in the system prompt. Prompt-level constraints drift here: teammate permissions already live in three drifting sources and once falsely blocked `/do-issue`.
- **Integration test** must verify a PM can create a Job in its own Room and is refused for another Room.

## Documentation

- [ ] Rewrite `docs/features/session-recovery-mechanisms.md` to describe the new perimeter. It currently lists 10 mechanisms under a heading that says "(7)" and omits eight others — the rewrite replaces it rather than appending.
- [ ] Create `docs/features/durability-model.md` — the single place that answers "a session is durable because X", which does not exist today. This is the document whose absence the audit identified as the root problem. Must include the Room/Job/AgentSession three-model shape and the fence semantics.
- [ ] Update `docs/features/session-lifecycle.md` for the Room/Job split and the execution record on `AgentSession`.
- [ ] Update `docs/features/session-steering.md` for the Room-owned inbox and the dual-read transition.
- [ ] Update `docs/features/harness-adapter.md` — the execution record (harness enum, fence) lives on `AgentSession`.
- [ ] Update `docs/infra/harness-cross-compat.md` — the AgentRun collision is dissolved; the `harness` enum lands via this plan and the remaining fields ride on `AgentSession`.
- [ ] Document the user-visible behavior change: **a Job can resume long after it looked done** — any topically-matching message can revive an aged-out Job. This is new behavior, not just an internal resume-handle change.
- [ ] Add all new docs to the `docs/features/README.md` index table.
- [ ] Inline: document the fence's residual TOCTOU window at the fence implementation, with the LWN reference. A future reader must not mistake it for a guarantee.

## Success Criteria

- [ ] `AgentSession` carries the fenced execution record: `harness` enum, nullable execution pid, `pid_create_time` fence, execution cwd — stamped at spawn, appended to spawn history (newest = live fence), history preserved for the session TTL
- [ ] The 200-entry `session_events` trim is removed; events persist for the session's lifetime
- [ ] `claude_pid`, `pm_pid`, `harness_pid`, `SessionHandle.pid`, and `AgentSession.expectations` are deleted from the codebase
- [ ] Every `os.kill`/SIGTERM site compares the fence; a test that fakes PID reuse proves a recycled pid reads as dead
- [ ] An in-process Task subagent is representable (pid `None` + `agent_id`) and visible to recovery; a test reproduces the #2420 shape and shows it detected
- [ ] `Room` resolves from `projects.json` and covers DMs and groups identically — the `if not chat_title: continue` filter is **removed**, not fixed
- [ ] The inbox is Room-owned; a steering message cannot be addressed to a terminated session
- [ ] `Job` exists with a required, append-only-versioned `goal` — mechanical placeholder at mint, PM-authored v1 as the PM's mandated first step (enforced in the PM priming); Jobs are never hard-closed and any steer resumes one regardless of age
- [ ] Reply-to routes via a permanent `message_id → job_id` index with no TTL, including for outbound messages
- [ ] `bridge/session_router.py` and its intake call site are deleted; `bridge/job_router.py` is the single routing authority
- [ ] Both hot-path classifiers (intake, Job router) run on granite via PydanticAI with strict JSON output models; a test asserts each fails open when Ollama is unreachable
- [ ] Ordering is 👀 → durable append → route → bind + reaction; a test kills the process between append and route and shows recovery
- [ ] The at-rest check flags "activity after last **authored** communication", is invoked from the `agent/session_health.py` periodic sweep (test asserts the invocation), and a regression test asserts the reference timestamps flag on authorship and do **not** flag on delivery. **Spike-1 measured a 0 false-positive rate across 95 live sessions; the reference incident fires with a 507s gap; grace is `AT_REST_OWED_GRACE_SECONDS` (provisional 30s, env-overridable).**
- [ ] Delivery timestamps are written after send success
- [ ] The promise gate is advisory: it returns a revise-or-override suggestion to the PM and performs zero writes; PM-authored promises append/remove as `Job.goal` versions; the at-rest backstop surfaces open promises to the operator surface
- [ ] A sent reaction is recorded as a reply-to message with escaped content (`<reaction>:…:</reaction>`) in the existing message log
- [ ] `agent/index_drift.py` covers `Room` and `Job`
- [ ] `Room` and `Job` each have a guarded repair path or a documented proof they cannot write an identity-less hash — verified per model name, not by aggregate count
- [ ] No new `IndexedField` holds an unbounded-cardinality value
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

- **Prototyper**
  - Name: `durability-prototyper`
  - Role: Tasks 1–4 — real working prototypes in disposable branches; sole deliverable is plan-file updates
  - Agent Type: builder
  - Resume: true

- **Schema Reviewer**
  - Name: `schema-reviewer`
  - Role: Gate the KeyField/IndexedField sets for `Room` and `Job`, and the `AgentSession` field additions, before any model work lands — one free shot
  - Agent Type: code-reviewer
  - Resume: true

- **Builder (execution record)**
  - Name: `session-builder`
  - Role: `AgentSession` fence fields, spawn-site stamping, fence enforcement, pid-field + `expectations` + `notify_sdk_started` deletion
  - Agent Type: builder
  - Resume: true

- **Builder (index safety)**
  - Name: `index-builder`
  - Role: `index_drift` generalization, pid-strip migration script, guarded-repair scaffolding
  - Agent Type: builder
  - Resume: true

- **Builder (health check)**
  - Name: `health-builder`
  - Role: `last_authored_at`, at-rest check + its `session_health` sweep wiring, delivery timestamp fix
  - Agent Type: builder
  - Resume: true

- **Builder (Room)**
  - Name: `room-builder`
  - Role: `Room` model, config resolution, scanner filter removal, inbox dual-read
  - Agent Type: builder
  - Resume: true

- **Builder (Job)**
  - Name: `job-builder`
  - Role: `Job` model, `job_router`, session-router retirement, permanent reply index, advisory promise flow
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

**Prototype spikes (Tasks 1–4) are prerequisites for all build work.** Each builds a real working system in a **disposable branch** (own worktree, never merged, deleted afterward). **The only allowed end result is an update to this plan file** — measured numbers, corrected assumptions, and firmed-up field/prompt choices land in the relevant sections; the prototype code itself is thrown away. Redis writes during spikes use `test-`/`dbg-` prefixed keys via the ORM only, deleted afterward. **If a spike's findings contradict this plan** — false-positive rate too high, granite accuracy or latency unusable, a schema access path unservable, parity failures in the intake replay — **STOP and surface to the owner before the Task 5 schema gate.** That is the spikes' entire purpose; do not quietly adapt the plan around a contradiction.

### 1. Prototype: at-rest check against production data
- **Task ID**: spike-atrest-proto
- **Depends On**: none
- **Assigned To**: durability-prototyper
- **Agent Type**: builder (worktree isolation, disposable branch)
- **Parallel**: true
- Build the real at-rest authorship-vs-activity check (read-only against production Redis) and run it two ways: replay the 2026-07-30 reference incident timestamps and assert it **fires**; run across all live sessions and record what it would flag right now
- Measure the false-positive rate; probe the two suspects (5s liveness-writer cooldown, 200-entry `session_events` trim) and the heterogeneous-timestamp parsing
- **Plan update**: measured false-positive rate, the final activity-anchor field choice, and any threshold constants (named, env-overridable) into Task 8 (build-health-check) and Success Criteria

### 2. Prototype: Room/Job schema in a scratch namespace
- **Task ID**: spike-schema-proto
- **Depends On**: none
- **Assigned To**: durability-prototyper
- **Agent Type**: builder (worktree isolation, disposable branch)
- **Parallel**: true
- Declare real `Room` and `Job` Popoto models under `test-` keys and exercise **every access path** the plan implies (inbox append/drain, bind-or-mint lookup, at-rest-with-open-promise query, reply-index lookup, drift/repair sweep)
- Confirm each queried path is served by a `KeyField`/`IndexedField`/`SortedField`; confirm goal versioning append/read round-trips; delete all scratch keys via the ORM afterward
- **Plan update**: the concrete proposed field lists (with KeyField sets) written into Task 5 (review-schema) as the reviewer's starting baseline — the one-shot KeyField decision then gets a reviewed dry run instead of a cold first draft

### 3. Prototype: Job router replay on real messages
- **Task ID**: spike-router-proto
- **Depends On**: none
- **Assigned To**: durability-prototyper
- **Agent Type**: builder (worktree isolation, disposable branch)
- **Parallel**: true
- Build the real bind-or-mint router (granite via PydanticAI/Ollama, strict JSON output model, placeholder-goal mint) and replay a sample of real inbound message history through it against reconstructed candidate-Job sets
- Score binding accuracy by hand: wrong-bind rate vs over-mint rate; probe confidence-threshold sensitivity; **measure median and p95 latency** of the granite call
- Run the same harness over the intake classifier's prompt on granite to validate the migration in Task 13
- **Plan update**: measured accuracy + latency numbers, the tuned threshold (named, env-overridable), and the final prompt/output-model shapes into Task 13 (build-job); if wrong-binds appear, the fail-open bias gets re-examined in Risks

### 4. Prototype: durable-inbox intake reordering
- **Task ID**: spike-inbox-proto
- **Depends On**: none
- **Assigned To**: durability-prototyper
- **Agent Type**: builder (worktree isolation, disposable branch)
- **Parallel**: true
- Build the reordered intake (👀 → durable Room-inbox append → route → bind) as a real working path in a disposable branch, alongside the untouched production handler
- Kill-test the loss window: SIGKILL between append and route, and between route and bind; assert recovery finds and processes the inbox entry
- Shadow-parity: replay a day of real inbound traffic through both paths; assert identical dispatch decisions
- **Plan update**: a concrete cutover checklist for Task 11, measured parity results, and any reordering hazards found

### Schema Gate — Open Decisions from Spikes

The four spikes ran clean (no STOP; every finding supports the plan). They surfaced **three decisions the owner/schema reviewer must rule on explicitly at the Task 5 gate** — recorded here so they are decided deliberately, not defaulted into by the first builder:

1. **Reply-index shape (from spike-2).** The permanent `message_id → job_id` index (no TTL) can be either **(a)** a standalone non-Popoto `reply:{message_id}` string key — honors the owner's "only two new models" directive and stays outside the ORM/drift/index-cleanup machinery — or **(b)** a third KeyField-indexed model. Spike-2 leans (a) (fewer models, no new drift-coverage surface), but it means the reply index is not walked by `index_drift`/`rebuild_indexes`; the reviewer must confirm that trade is acceptable or mandate (b).
2. **👀-before-append residual (from spike-4).** The 👀 reaction currently precedes the durable append ("bridge saw this", deliberately pre-durable), leaving a sub-millisecond residual window where 👀 is shown but the message is not yet durable. Either **(a)** keep 👀 before the append and document the ~0.8ms residual as an accepted, non-durability-implying ack, or **(b)** move 👀 to *after* the append and redefine its meaning as "received and durable". Spike-4 measured the residual at ~0.8ms; the decision is about what 👀 should *promise*, not about loss (append recovers regardless).
3. **Over-mint rate ~35% (from spike-3).** The router's 35% over-mint at the tuned 0.70 threshold is the safe failure direction (extra Job, never wrong-bound/lost), so it is *acceptable to ship* — but it is **the one production number to watch**, and the lever to reduce it is **prompt tuning, not the confidence threshold** (raising the threshold trades over-mint for latency/NEW-bias without improving binding). Flag for post-ship monitoring, not a pre-ship blocker.

### Schema Gate Ruling (APPROVED 2026-08-02)

The Task 5 schema review gate ran with code-reviewer rigor against the codebase at `d00d33101`. Every access path for `Room`, `Job`, and the new `AgentSession` execution fields was written down and confirmed servable. **The gate CLEARS — no unservable access path, no IndexedField forced to hold unbounded cardinality, no KeyField set that cannot serve its queries.** One correction to Task 6 scope is required (below); it is a servable refinement, not a blocker.

**Approved field list:**

- **`AgentSession` execution fields — all plain (non-indexed):** `harness` (plain `Field`, enum string — read off a fetched session, never `.filter()`ed), execution pid (plain nullable `IntField`), `pid_create_time` (plain nullable field, the fence), execution cwd (plain nullable field), spawn history (`ListField`, append-only, newest entry = live fence). None is an `IndexedField`. This *removes* the last pid-valued index in the model (`claude_pid`), a net cardinality win.
- **`Room` KeyField set = {`project_key`, `addressee`} — RATIFIED.** Every Room resolution is a direct composite-primary-key lookup; the inbox is a Redis list under the Room's primary key (drained by the pattern-scanning relay, not a `.filter()`); the "👀-orphan inbox entry older than N with no Job binding" diagnostic is a bounded class-set scan (`Room.query.all()` × per-Room inbox), acceptable because it runs on the periodic health cadence, not the hot path. No `IndexedField` on `Room`.
- **`Job` KeyField set = {`id`, `room_id`} — RATIFIED.** `id` (`AutoKeyField`) for identity; `room_id` (`KeyField`, composite `{project_key}|{addressee}`) makes a single `SortedField(partition_by=room_id)` on recency serve the top-N bind-or-mint candidate lookup without an unbounded index (the `partition_by` pattern is proven live by `AgentSession.created_at = SortedField(partition_by="project_key")`); a low-cardinality `status` `IndexedField` (`active`/`at-rest`) serves the at-rest-with-open-promise query; `goal` is an append-only-versioned plain field (append/read round-trips confirmed empirically by spike-2). Reply-index lookups always resolve `room_id` from the inbound message's chat/Room context, so a `message_id → job_id` hit reconstructs the full composite Job key for the fetch — no id-alone lookup is required.
- **No `IndexedField` holds an unbounded-cardinality value — CONFIRMED.** `Room`: none. `Job`: only the low-cardinality `status`. `AgentSession`: the new fields are all plain, and the deletion of `claude_pid` removes the one pid-valued index.

**REQUIRED CORRECTION to Task 6 (servable, added to scope):** Deleting `claude_pid` also removes the `AgentSession.find_by_claude_pid` reverse pid→session index, which has three live callers in the orphan reaper — `_reap_orphan_session_processes` (`agent/session_health.py:5511`, `:5518`) and `_oneshot_owner_is_live` (`:5328`) — that ask "which live session owns this OS pid?". The plan text does not name this replacement. **Ruling:** the execution pid stays a *plain* field (do NOT re-introduce a pid index — that is the anti-pattern this plan exists to delete, and the append-only spawn history would make it unbounded). Instead, reimplement these reverse lookups as a **forward scan over the `status`-indexed non-terminal set**: iterate `AgentSession.query.filter(status=s)` for `s in models.session_lifecycle.NON_TERMINAL_STATUSES` (each a low-cardinality indexed lookup, dozens of live rows), build an in-process `{live_pid: session}` map from each row's live fence record, and resolve ownership by membership. Delete `find_by_claude_pid` and migrate its callers to this helper. This is bounded, honors "never index a pid," and preserves the ownership gate that protects long live PM turns.

**Ruling on the three open decisions:**

1. **Reply-index shape — (a) standalone non-Popoto `reply:{message_id}` string key, no TTL, bound via `SET NX`.** RATIFIED. Precedent exists in-repo (`bridge/context.py:529` root-id mapping and `bridge/dedup.py::claim_message`, both raw string-key `SET NX`). The trade (index not walked by `index_drift`/`rebuild_indexes`) is **accepted**: a plain string KV has no hash, no class set, and no secondary index, so the #2207 identity-less-hash flood mechanism — which is specific to a Popoto model's index sweep — structurally cannot occur for it. Honors the owner's "only two new models" directive.
2. **👀-before-append residual — (a) keep 👀 before the append; document the ~0.8ms residual as an accepted, non-durability-implying ack.** DEFAULT RULING — **FLAGGED FOR OWNER OVERRIDE.** This is a product-semantics call about what 👀 promises, not a data-loss question (the append recovers the message either way). Default (a) preserves the current "the bridge saw this" meaning and the spike-measured behavior. The owner may prefer (b) — move 👀 after the append and redefine it as "received and durable" — if 👀 should promise durability. No code depends on the choice beyond one line's ordering; recorded here so the owner rules deliberately.
3. **Over-mint ~35% — ship-and-monitor.** RATIFIED as a non-blocker. It is the safe failure direction (extra Job, never wrong-bound/lost); the reduction lever is prompt tuning, not the confidence threshold. Tracked as the one post-ship production number to watch; not gated pre-ship.

### Schema Gate Amendment 1 (APPROVED 2026-08-07) — `Job` carries two low-cardinality IndexedFields

Raised by #2634 item 2 (bound `at_rest_with_open_promises`). **The gate holds; the amendment is granted.**

`Job` now declares **two** IndexedFields: `status` (`active` / `at-rest`) and `has_open_promises` (`type=bool`, two-valued, derived). The invariant this gate actually enforces is unchanged and restated here as the governing rule: **no IndexedField may hold an unbounded-cardinality value — never index a pid, uuid, or timestamp.** A two-valued derived boolean honors that rule; the original "`status` is the ONLY IndexedField" phrasing described the then-current field list, not the safety property, and is superseded by this amendment wherever it appears.

**Why an index was required at all.** `at_rest_with_open_promises` hydrated every at-rest Job and `json.loads`-ed its `goal` on every health tick (300s, per worker process) to surface a set that is almost always empty. Jobs are immortal (no `Meta.ttl`) and at-rest is the steady state every Job eventually reaches, so the scan cost rises monotonically with lifetime Job count with no ceiling. Measured against a synthetic 5,000-Job at-rest population carrying 20 open promises: the full scan costs **0.26s** and grows linearly, while an index-bounded lookup costs **0.006s** and is constant in the at-rest population. The gap widens without bound.

**Alternatives considered and rejected:**

- **`SortedField(partition_by="status")`** — disqualified empirically, not on taste. popoto's `on_save` only ZREMs from the old partition when `_saved_field_values` shows the partition field changed, and a lazily-loaded instance (what `query.filter()` returns) populates `_saved_field_values` with KeyFields only. On the exact `sweep_to_rest` code shape the member orphans in the old partition and never appears in the new one, and neither `.delete()` nor `rebuild_indexes()` can clean it.
- **Unpartitioned `at_rest_since` SortedField** — rejected on semantics, which is the more decisive objection than the schema one. It bounds by *how recently a Job went to rest*, but the backstop exists to surface promises nobody discharged; the oldest at-rest Jobs are precisely the most neglected ones. A time window would silently drop the exact rows the query is for. It also cannot be null and populates only on save, so it would need a full-population backfill before it returned anything truthful.
- **Refining the `status` enum to add `at-rest-owed`** — achieves an identical bound with no new field, no amendment to this gate, and a backfill proportional to the flagged set rather than the whole population. Rejected anyway: it encodes a boolean into a lifecycle enum, so `status` would stop meaning one thing, and every present and future reader of `filter(status="at-rest")` would silently miss owed Jobs. That is a wrong-answer failure mode with no compile-time or test-time signal. A separate two-valued field composes cleanly and keeps `goal` JSON as the single source of truth, with the index a derived projection that any read that matters re-verifies via `open_promises()`.

**Timing note, load-bearing.** The one real cost of the chosen design is that pre-existing rows land in *neither* index set until a backfill runs, so the backstop under-reports silently in the interim. That backfill is cheapest now: the Job model shipped 2026-08-07 and the measured live population at amendment time is **zero**. Any design here needs a write-backfill eventually; approving and building it while the population is empty converts the risk into a no-op. It will never be cheaper than it is today.

**Unchanged by this amendment:** the `Job` KeyField set `{id, room_id}`, the `last_active_at` `SortedField(partition_by="room_id")`, `goal` as an append-only-versioned plain field, and the `Room` and `AgentSession` rulings above.

### 5. Schema review gate
- **Task ID**: review-schema
- **Depends On**: spike-atrest-proto, spike-schema-proto, spike-router-proto, spike-inbox-proto
- **Assigned To**: schema-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Write down every access path for `Room`, `Job`, and the new `AgentSession` execution fields before any field is declared
- Confirm each queried path is served by a `KeyField`, `IndexedField`, or `SortedField` — plain fields are not queryable
- Confirm no `IndexedField` holds an unbounded-cardinality value (the fence pair and cwd are plain fields)
- Confirm the KeyField set for `Room` and `Job`; this is a one-shot decision
- **Blocks all model work.** Output is an approved field list.
- **Spike-2 proposed baseline (reviewed dry run — not yet ratified; the KeyField sets below are the one-shot decision the reviewer confirms or corrects):**
  - **`Room` KeyField set = {`project_key`, `addressee`}.** The primary key is the natural `(project_key, addressee)` identity from spike-1 — every Room resolution is a direct key lookup, no index needed. Fields: `project_key` (KeyField), `addressee` (KeyField, `telegram:{chat_id}`|`email:{address}`|`system`), inbox storage (a list keyed by the Room's primary key — drained by the pattern-scanning relay), plus plain metadata fields.
  - **`Job` KeyField set = {`id`, `room_id`}** where `room_id` is the composite `{project_key}\|{addressee}` string. Making `room_id` a KeyField lets a **single `SortedField(partition_by=room_id)` on recency** serve the bind-or-mint candidate lookup (top-N recent Jobs in a Room) without an unbounded index. Fields: `id` (KeyField), `room_id` (KeyField, composite), a status `IndexedField` (low-cardinality: active/at-rest — serves the at-rest-with-open-promise query), a recency `SortedField(partition_by=room_id)`, and `goal` as an append-only-versioned plain field (versions round-trip confirmed).
  - **All 6 access paths served** by a KeyField/IndexedField/SortedField (inbox append/drain, bind-or-mint candidate lookup, at-rest-with-open-promise query, reply-index lookup, drift/repair sweep, goal-version read). Goal versioning append/read round-trips cleanly. Scratch-key cleanup verified clean (zero `test-` keys left under the spike prefix). **Two shape questions the spike surfaced for the reviewer are recorded under "Schema Gate — Open Decisions from Spikes" below.**

### 6. AgentSession execution record + fence + pid deletion
- **Task ID**: build-exec-record
- **Depends On**: review-schema
- **Validates**: tests/unit/test_pid_fence.py (create), tests/unit/test_session_health_orphan_reap.py, tests/integration/test_harness_resume.py
- **Informed By**: spike-2 (authorship exists in session_events), Research (fence is best-effort)
- **Assigned To**: session-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Add the execution fields to `AgentSession` per the approved field list: `harness` enum, nullable execution pid, `pid_create_time`, execution cwd, spawn history
- Stamp `(pid, create_time, cwd, harness)` at spawn on the same path that persists the resume handle at `system/init` (Race 2); pid `None` + `agent_id` for in-process subagents; each spawn **appends** to spawn history, newest entry is the live fence
- **Remove the 200-entry `session_events` trim** (`adapter.py:237`) — events are the forensic record, bounded by session TTL, not by count
- Compare the fence at every `os.kill`/SIGTERM site, notably `agent/session_health.py:91-114`; retained child handle stays primary for runner-spawned processes
- Delete `claude_pid`, `pm_pid`, `harness_pid`, `SessionHandle.pid`, the dead `notify_sdk_started` path, and the write-only `AgentSession.expectations` field with its write path (`agent/output_handler.py:1139-1143`)
- **Reverse pid→session lookup (schema-gate ruling):** delete `AgentSession.find_by_claude_pid` and migrate its three orphan-reaper callers — `_reap_orphan_session_processes` (`agent/session_health.py:5511`, `:5518`) and `_oneshot_owner_is_live` (`:5328`) — to a forward scan over the `status`-indexed non-terminal set (`AgentSession.query.filter(status=s)` for `s in NON_TERMINAL_STATUSES`), building an in-process `{live_pid: session}` map from each row's live fence record. Do NOT index the execution pid; the pid stays a plain field.
- Delete `tests/unit/test_messenger_callbacks.py` cases for the removed callback
- Document the residual TOCTOU window inline with the LWN reference

### 7. Index safety + migration
- **Task ID**: build-index-safety
- **Depends On**: build-exec-record
- **Validates**: tests/unit/test_index_drift_coverage.py (create), tests/unit/test_agentsession_pending_index_leak.py
- **Assigned To**: index-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Generalize `agent/index_drift.py` past its hardcoded `AgentSession`, ready for `Room` and `Job` to register
- Write the field-strip migration modelled on `scripts/migrate_strip_pty_fields.py`: dry-run default, `--apply`, terminal rows only, ORM-only writes, idempotent — strips the three pid fields and `expectations`
- Register it in `scripts/update/migrations.py`'s `MIGRATIONS` dict

### 8. Authorship health check
- **Task ID**: build-health-check
- **Depends On**: build-exec-record
- **Validates**: tests/unit/test_at_rest_owed_communication.py (create)
- **Informed By**: spike-2 (authorship at adapter.py:499/:534)
- **Assigned To**: health-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `last_authored_at`, written at the two existing save sites `adapter.py:491` and `:526`
- Implement the at-rest check: no live fenced execution AND last activity > last authored comms
- **Wire the check into the existing periodic sweep in `agent/session_health.py`** — the same cadence as the orphan reap — with a test asserting the sweep invokes it (no correct-logic-dead-caller)
- Activity anchor is `max(last_stdout_at, last_tool_use_at, last_turn_at)` — `ui/data/sdlc.py:1042` already computes this
- Account for the 5s liveness cooldown (the 200-entry `session_events` trim is removed by Task 6; the prototype's measurements against the still-trimmed production data set the baseline)
- Move the delivery timestamp write to after send success
- **Spike-1 results (measured against production, read-only):** false-positive rate **0** — of 95 live sessions, 8 had both anchors present and all 8 were correctly left unflagged. The 2026-07-30 reference-incident replay **fires** with a **507s** activity-after-authorship gap. Final activity anchor confirmed as `max(last_stdout_at, last_tool_use_at, last_turn_at)`. Grace window is a named env-overridable constant — provisional default **30s** (`AT_REST_OWED_GRACE_SECONDS`, tunable), comfortably below the 507s incident gap and above the 5s liveness-writer cooldown so the cooldown never trips a false positive. The **200-entry `session_events` trim is the one false-positive hazard** (an evicted authorship record makes a delivered session look owed); it is mitigated structurally by the Task 6 trim removal **and** by the `last_authored_at` scalar (which survives independent of the events list), so the check must read `last_authored_at` first and fall back to the events scan only when the scalar is absent.

### 9. Validate Milestone 1
- **Task ID**: validate-m1
- **Depends On**: build-index-safety, build-health-check
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false
- **Milestone 1 is independently shippable — stop here for PM review before Milestone 2.**

### 10. Room model + resolution
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
- **Initialize scanner cursors for newly-covered DM Rooms at "now", never at history-zero** — the first deploy must not replay old DM history as "recovered" messages
- **Re-enable catchup** (`data/catchup-disabled`, off since 2026-07-22 pending #2204) as part of validating DM recovery end-to-end — the DM-recovery success criterion is untestable while catchup is off; coordinate the re-enable with #2204's scoping
- Give `Room` a guarded repair path with a `_GUARDED_ELSEWHERE` entry naming `Room`, and register it in `index_drift` coverage

### 11. Room-owned inbox (dual-read, phase 1)
- **Task ID**: build-inbox-dualread
- **Depends On**: build-room
- **Validates**: tests/integration/test_steering.py
- **Informed By**: spike-3 (no migration needed; dual-read before writer flip)
- **Assigned To**: room-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- **Cutover is gated on the spike-inbox-proto checklist**: ship shadow mode first (durable append written alongside the untouched existing flow), verify dispatch parity in production, then make the append authoritative in a separate release — never a one-shot cutover of the hot intake path
- `pop_all_steering_messages` drains the legacy session key first, then the room key
- **Writers unchanged in this task** (Race 1) — the writer flip is a separate release
- Outbox room keys go under the existing `telegram:outbox:` prefix so the pattern-scanning relay drains them unmodified
- Fix `agent/health_check.py:511` — re-push the abort's siblings
- **Spike-4 results (reordered intake built alongside the untouched production handler; kill-tested and shadow-replayed):** the loss window collapses to a **single ~0.8ms RPUSH** (the durable Room-inbox append). Both kill points — SIGKILL between append→route and between route→bind — **recover with zero loss**: recovery finds the inbox entry and processes it. **Idempotency confirmed** — a crash-after-append + re-route no-ops via SETNX on the permanent `message_id → job_id` index (no duplicate Job). **Shadow-parity 67/67 = 100%** identical dispatch decisions across a replayed day of real inbound traffic; no divergences. All `dbg-`/`test-` scratch keys cleaned via the ORM (before/after verified).
- **Spike-4 cutover checklist (3-phase, never a one-shot cutover of the hot intake path):**
  1. **Shadow.** Ship the durable Room-inbox append written *alongside* the untouched existing dispatch flow (append is not yet authoritative). Verify dispatch parity in production against the live handler.
  2. **Authoritative append.** In a *separate release*, make the durable append the source of truth for dispatch (route/bind read from the inbox entry). The 👀→append→route→bind ordering goes live here.
  3. **Retire the legacy path** once the authoritative append is proven in production.
  - **The steering dual-read (Race 1) is an INDEPENDENT deploy** and must not ride in the same release as the intake reorder: ship the dual-read consumer first (writers unchanged, old workers keep working), flip writers only after every worker is on new code. Sequencing the two together couples two hot-path cutovers in one release — do not.

### 12. Validate Milestone 2
- **Task ID**: validate-m2
- **Depends On**: build-inbox-dualread
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false
- **Milestone 2 is independently shippable — stop here for PM review before Milestone 3.**

**M2 SHIPPED 2026-08-07 via PR #2622 (tracking #2626).** Implementation notes against the tasks above:
- Task 10's cursor-init bullet is implemented as the **DM coverage epoch**: `bridge:dm_coverage_epoch:{chat_id}` (plain key, SET NX at now, no TTL). First covered pass skips; all later lookbacks — including catchup's 24h `lookback_override` and the unbounded per-chat cursor — clamp to the epoch (`max(cutoff, epoch)`, clamp-not-min). All three scanners use the fail-safe `get_or_init` variant, so a Redis blip clamps to ~zero instead of falling open.
- Task 11 phase 1 (shadow append via `bridge/room_inbox.py` + steering dual-read in all five consumers and the status peek) shipped as two independent legs, writers untouched.
- **The steering writer flip shipped separately (#2642), with a stated selective boundary.** Conversation-level originating writes target the Room key; aborts and session-scoped diagnostics stay on the legacy key by rule; a requeue writes to the leg it read from, read off a transient `_leg` stamp; the Room leg is age-bounded at drain time by `TimeoutSettings.steering_room_max_age_s`. See [`docs/features/session-steering.md`](../features/session-steering.md).
- **Remaining, each its own release:** phase 2 (authoritative append), phase 3 (legacy-path retirement). Phase-2 gate: N days of error-free `[room-inbox]` shadow appends plus spot-checked parity of inbox entries vs dispatched sessions.
- Task 12: catchup re-enable is **operator-gated**, not shipped: run `rm data/catchup-disabled` on the bridge machine after `/update` propagates ≥ PR #2622, coordinated with #2204's scoping; watch the one-time `DM coverage epoch initialized` lines, `catchup.re_enqueue age_s` for replay spikes, and the #2611 doctor stale-flag WARN clearing.
- Test-impact rows "extend phantom-guard/pending-index-leak to Room" are satisfied by `test_room_resolution.py::TestGuardedRepair`/`TestDriftCoverage` (Room has zero IndexedFields, so the AgentSession shim mechanics don't apply). Job's extension stays with M3.
- **Inbox entry-shape constraint for the phase-2 authoritative drainer** (from #2497/PR #2627): the system-Room null sink appends outbound entries `{direction: "outbound", session_id, text, file_paths, ts}` while the Telegram intake shadow-writes inbound entries with **no** `direction` key (`{chat_id, message_id, sender_id, sender_name, text, ts}`). The drainer must treat a missing `direction` as inbound, or it will dispatch reflection output as a user message.

### 13. Job model + router + session-router retirement
- **Task ID**: build-job
- **Depends On**: validate-m2
- **Validates**: tests/unit/test_job_router.py (create), tests/integration/test_job_routing.py (create)
- **Assigned To**: job-builder
- **Agent Type**: builder
- **Parallel**: false
- `Job` with a required, append-only-versioned `goal`: mechanical placeholder at mint (`handle user message '<first 20 chars>…'` — the router never model-authors it), PM-authored v1 as the PM's mandated first step; rest by age, never hard-closed, always resumable
- Add the goal-authoring mandate to the PM context priming (`prime-pm-role`): first step on any Job whose goal is still the placeholder is to author the real goal
- `bridge/job_router.py` on **granite via PydanticAI** (Ollama provider), modeled on the session-router structure; very simple prompt, strict Pydantic JSON output; post-hoc `valid_ids` membership check; anything outside the candidate set becomes NEW
- **Migrate the intake classifier (`bridge/telegram_bridge.py:2168`) to granite via PydanticAI** in the same pattern — both hot-path classifiers on the local model, both JSON-out, both a few lines of call-site code
- **Retire the old routing authority in the same task**: delete the `session_router` invocation from the intake handler, delete `bridge/session_router.py`, and DELETE/REPLACE its tests
- Give `Job` a guarded repair path with a `_GUARDED_ELSEWHERE` entry naming `Job`, and register it in `index_drift` coverage
- Permanent `message_id → job_id` index, no TTL, bound via SETNX (Race 4), written for outbound messages too
- PM Job creation enforced to the session's own Room **at the tool layer**
- **Spike-3 results (granite via PydanticAI/Ollama, replayed on real messages):** **wrong-bind rate 0/35 at every confidence threshold** — the safe direction; **over-mint rate 35% at the tuned threshold** (an extra Job, never a lost or wrong-bound message). Router latency **median ~1.1s / p95 ~1.4s** against the live granite daemon (durable Room-inbox append precedes routing, so this is a UX cost, not a durability cost). Intake-classifier-on-granite validation: **84.2% raw agreement vs the current Haiku classifier, ~95% behavioral agreement** once the dormant-guard path is accounted for. **Tuned threshold = `JOB_ROUTER_CONFIDENCE_THRESHOLD` (provisional 0.70, env-overridable)** — below it the verdict falls open to NEW. Final output model is `JobRouteDecision` (`decision: Literal["bind","new"]`, `job_id: str | None`, `confidence: float`) with the post-hoc `valid_ids` membership check; prompt is the very-simple candidate-list-plus-message shape modeled on `session_router.py`. **Over-mint at ~35% is the one production number to watch (see Open Decisions #3) — the lever is prompt tuning, not the threshold.**

### 14. Advisory promise flow + reaction record
- **Task ID**: build-promises
- **Depends On**: build-job
- **Validates**: tests/unit/test_promise_advisory.py (create), tests/unit/test_promise_gate_session_events.py
- **Informed By**: spike-4 (mechanical triggers rejected; owner decision 2026-07-31)
- **Assigned To**: job-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewire the promise gate to **advisory**: on a deferral-shaped outbound, return a revise-or-override suggestion to the PM; the gate performs **zero writes** (test asserts this)
- Piggyback the goal-reset nudge on the same outbound pass: if the bound Job's `goal` is still the mint placeholder, the advisory response also reminds the PM to author the goal (test: placeholder goal + outbound → nudge present; authored goal → no nudge)
- PM tools to append a promise to `Job.goal` (new version) and remove it on delivery (new version); Room-scope enforced at the tool layer
- At-rest backstop: the Task-8 health check additionally surfaces Jobs at rest with an open promise entry, routed to the operator surface only
- Record sent reactions as reply-to messages with escaped content (`<reaction>:thumbs-up:</reaction>`) at the relay's send-success site — no new model
- Operator metric: count advisory suggestions issued vs promises authored — not to prevent a PM from ignoring the advisory (it can't be prevented), but so an ignored advisory is visible instead of silent

### 15. Documentation
- **Task ID**: document-feature
- **Depends On**: build-promises
- **Assigned To**: durability-docs
- **Agent Type**: documentarian
- **Parallel**: false
- All tasks in the Documentation section above

### 16. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false

**M3 SHIPPED 2026-08-07 via PR #2631 (tracking #2632).** Implementation notes against Tasks 13-15:
- Job routing runs **in shadow** at the intake seam (backgrounded `shadow_route_job`, dispatch untouched) — the 🤔/steer-reaction leg and the authoritative 👀→append→route→bind ordering land with the phase-2 cutover release, per the M2 cutover checklist above. Before that cutover, `Job.recent_for_room` needs a bounded ZREVRANGE, tracked in #2636.
- The acknowledgment intent class was retired with the granite two-class taxonomy migration; Jobs replace session-level acknowledgment semantics. `bridge/session_router.py`, its intake call site, the #730 terminal guard, and the expectations-keyed branch are deleted; `docs/features/semantic-session-routing.md` deleted with it.
- The promise override is `promise_recorded_override` in the drafter chokepoint (`_evaluate_drafter_promise`; #2621's `_gate_empty_promise` wrapper was absorbed), **Job-scoped by design** — any open promise on the bound Job clears the gate until discharge. The terminal flush keeps #2423's substitute-fallback unchanged (no live PM to record a promise at flush time).
- Rest-by-age: `Job.sweep_to_rest()` at `JOB_AT_REST_AGE_SECONDS` (provisional 72h), invoked in the health sweep ahead of `at_rest_with_open_promises`; `revive()` re-stamps recency on bind so no thrash.
- Verification row "Advisory gate writes nothing" is satisfied by `tests/unit/test_promise_advisory.py` (poisons eval/evalsha/register_script + pipeline writes); `session_events` CAS mechanics unchanged.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| pid fields deleted | `grep -rn "claude_pid\|pm_pid\|harness_pid" --include=*.py agent/ models/ bridge/ tools/ ui/ \| wc -l` | output contains 0 |
| expectations deleted | `grep -rn "\.expectations" --include=*.py agent/ models/ bridge/ \| wc -l` | output contains 0 |
| Dead callback deleted | `grep -rn "notify_sdk_started" --include=*.py . \| grep -v tests/ \| wc -l` | output contains 0 |
| DM filter removed | `grep -rn 'if not chat_title' --include=*.py bridge/ \| wc -l` | output contains 0 |
| Session router retired | `test -f bridge/session_router.py; echo $?` | output contains 1 |
| No Relationship self-ref | `grep -rn "Relationship(" --include=*.py models/ \| wc -l` | output contains 0 |
| index_drift covers Room | `grep -c "Room" agent/index_drift.py` | output > 0 |
| index_drift covers Job | `grep -c "Job" agent/index_drift.py` | output > 0 |
| Guard registered: Room | `grep "_GUARDED_ELSEWHERE" -A5 scripts/popoto_index_cleanup.py \| grep -c "Room"` | output > 0 |
| Guard registered: Job | `grep "_GUARDED_ELSEWHERE" -A5 scripts/popoto_index_cleanup.py \| grep -c "Job"` | output > 0 |
| Migration registered | `grep -c "strip_pid_fields" scripts/update/migrations.py` | output > 0 |
| At-rest check has a live caller | `grep -rn "at_rest" agent/session_health.py \| wc -l` | output > 0 |
| Authorship anchor test exists | `grep -rn "authored" tests/unit/test_at_rest_owed_communication.py` | exit code 0 |
| Advisory gate writes nothing | `grep -rn "zero writes\|assert.*not.*save" tests/unit/test_promise_advisory.py` | exit code 0 |
| Anti-criterion: no live-row rewrite | `grep -n "status.*running" scripts/migrate_strip_pid_fields.py` | match count == 0 |
| Anti-criterion: no unbounded index | `grep -nE "IndexedField" models/room.py models/job.py \| grep -iE "pid\|uuid\|_at\b"` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Adversary) | Task 10 (build-job) has no guarded-repair-path or `index_drift` bullet for `Job`; the Verification row `grep -c "_GUARDED_ELSEWHERE"` → `> 0` is satisfied by AgentRun alone, so `Job` could ship unguarded into the generic `rebuild_indexes()` sweep — the exact #2207 flood mechanism Risk 2 exists to prevent. | Rev 2026-07-31: Tasks 10 & 13 each carry a guarded-repair bullet; Verification asserts `Room` and `Job` literally, per model. | Add a guarded-repair-path bullet to Task 10 and change the Verification row to assert `_GUARDED_ELSEWHERE` contains the literal names `"Room"`, `"Job"`, and `"AgentRun"` (or three separate rows), so a Job-only omission fails instead of passing. |
| CONCERN | Risk & Robustness (Skeptic) | `Job.goal` is defined as "required, PM-authored," but the Flow/Task 10 router bind-or-mints a NEW Job at inbound time, before any PM turn has run — the plan never states what `goal` holds at mint time, so creation must either violate "PM-authored" or block the router's synchronous path. | Rev 2026-07-31 (owner): router stamps only a mechanical placeholder at mint — it is not smart enough to author a goal; PM authors v1 as its mandated first step via priming (Key Elements, Task 13). Never null, router never blocks. | Either (a) make `goal` a nullable Field filled at the PM's first turn, with router-created Jobs starting `goal=None` and excluded from resumable-by-steering semantics until authored, or (b) have `job_router.py` seed `goal` from the triggering message text and restate the contract as "PM-authored, router-seeded pending first turn." |
| CONCERN | Risk & Robustness (Operator) | Task 5 implements the at-rest authorship-vs-activity check but no task or criterion names what invokes it on a recurring cadence — the same "correct logic, dead caller" shape as `notify_sdk_started`, which this plan's Problem section cites as the root failure. | Rev 2026-07-31: Task 8 wires the check into the `agent/session_health.py` periodic sweep with a test asserting the invocation; Verification row added. | Name the scheduling mechanism (existing periodic sweep in `agent/session_health.py`, a cron entry, or the dashboard poll) and add a Verification row of the form `grep -rn "<check-function-name>" agent/session_health.py scripts/ \| wc -l` → `> 0`, plus a test asserting the invocation happens. |
| CONCERN | Scope & Value | `bridge/job_router.py` copies `session_router.py`'s classifier pattern but the plan never retires `session_router.py` or the `AgentSession.expectations` write path — after Milestone 3 two near-identical Haiku "does this belong to an existing conversation" classifiers run on overlapping traffic with no stated resolution rule, conflicting with NO LEGACY CODE TOLERANCE. | Rev 2026-07-31: Task 13 deletes the intake call site and `bridge/session_router.py`; Task 6 deletes `expectations` + its write path; Verification rows assert both. | Task 10 should add a companion step: delete the `session_router` invocation in the intake handler and the `expectations` writes at `agent/output_handler.py:1139`, with a removed-callsite Verification check — the existing anti-criterion (`grep -c "Job" bridge/session_router.py` == 0) only proves textual separation, not retirement. If both must coexist, state the coexistence rule and which wins. |
| CONCERN | Scope & Value | Milestone 3 (obligation ledger, durable reactions, discharge rules) solves a related-but-distinct problem from the motivating SIGKILL incident, which Milestones 1+2 fully address; the Open Questions interrogate how to gate it but never whether it belongs in this plan. | Owner affirmed 2026-07-31: M3 stays, reframed — advisory gate, PM-authored promises on `Job.goal`, no obligation model (Technical Approach, Task 14). | Call out in Problem/Appetite that Milestone 3 is a distinct capability needing an affirmative PM scope call, or split it to its own plan; if kept, gate Task 11 behind a PM check-in that reconfirms scope (beyond the #2489 prerequisite), since spike-4's data shows the ledger's failure mode compounds an existing unfixed leak. |
| CONCERN | History & Consistency | Open Question 3 frames reaction durability as unresolved, but Task 11 already commits to it unconditionally ("Add a durable record for reactions") — the task list bakes in an answer the plan still presents as open. | Owner resolved 2026-07-31: reactions recorded as reply-to messages with escaped content in the existing log (Task 14); Open Questions section removed. | Either resolve Q3 in the plan body (state the decision, delete the question) or make Task 11's reaction-durability bullet conditional: "skip this bullet if Open Question 3 is unresolved at the validate-m2 review gate," so `job-builder` doesn't silently build still-debatable scope. |
| CONCERN | History & Consistency | Open Question 2 asks whether to exclude the `behavioral_change` trigger class, but Task 11 already hardcodes the exclusion ("**not** `behavioral_change`") — the task step silently presumes the answer to a question listed as open. | Owner resolved 2026-07-31: trigger classes removed entirely; the gate is advisory and writes nothing (Task 14); Open Questions section removed. | Collapse Q2 into a stated decision in Technical Approach citing spike-4's 1-block-in-90-days evidence, or at minimum add "Resolved: excluded, see Task 11" next to Q2 so a reviewer doesn't read it as undecided when the code will already have decided it. |
| NIT | Scope & Value | "Never hard-closed; always resumable regardless of age" is a user-facing behavior change (a seemingly-terminal conversation can be revived much later by a topically-similar message) but is presented only as an implementation property. | Rev 2026-07-31: named as user-visible behavior in Key Elements and the Documentation checklist. | Add one line to Documentation or Success Criteria naming it as new user-visible behavior, not just an internal resume-handle change. |
