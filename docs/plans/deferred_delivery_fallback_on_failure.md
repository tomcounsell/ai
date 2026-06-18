---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-19
tracking: https://github.com/tomcounsell/ai/issues/1730
last_comment_id: 4745184399
revision_applied: true
---

# Deferred Delivery Fallback on Session Failure

## Problem

When a session's user-facing delivery is deferred by the empty-promise self-draft
mechanism (the drafter flags `needs_self_draft=True`, the outbox write is skipped,
and a steering message tagged `sender="drafter-fallback"` is injected asking the
agent to rewrite), and the session is *then* killed by the health checker due to a
`tool_timeout`, **the user receives no message at all**. The deferred-delivery
state lives only as a local `steering_deferred` boolean in `output_handler.send()`
— it is never persisted on the `AgentSession` record. When the health checker
finalizes the session as `failed`, the pending self-draft is silently dropped.

A second, compounding bug makes this far more likely to fire: after a session
delivers a message and saves a `complete` snapshot, the worker's asyncio future is
never resolved, so the session stays `running` in Redis indefinitely. The health
checker eventually recovers it (`no_progress`) and **re-runs an already-completed
session**, which hits the empty-promise gate a second time, defers delivery again,
then wedges a tool and gets killed as `failed` — landing exactly in the dropped-
delivery hole above.

**Current behavior** (reproduced from the production timeline in the issue):
1. Agent produces empty-promise output → delivery deferred, self-draft steering injected
2. Session completes its SDK turn → saves `complete` snapshot → one message delivered ✓
3. Worker future never resolves → session stays `running` for 32 min (`log_path=None`)
4. Health checker recovers session (attempt 1, `no_progress`) → re-runs it
5. Re-run produces another empty promise → delivery deferred again
6. Bash tool wedge (>300 s) → health checker kills session as `failed` (attempt 2, `tool_timeout`)
7. **User receives no message from the final run. 0 deliveries.**

**Desired outcome:**
- When a session is finalized as `failed` (or `killed`) with a pending
  `drafter-fallback` steering message, a fallback delivery is attempted before
  the session closes — reusing #1711's `_deliver_tool_timeout_degraded_notice`
  delivery pattern but routing through the deferred-self-draft path.
- The worker-future leak is fixed so a session that finishes its SDK work
  is finalized to `completed` within ~10 s — no 32-min ghost `running` state and
  no spurious recovery re-run. `completed` is a **terminal** status, so the
  transition MUST go through `models/session_lifecycle.py::finalize_session()`,
  **not** `transition_status()` (which raises `ValueError` on terminal targets —
  see Blocker fix in Technical Approach).
- The `steering:attempts:{session_id}` Redis counter is cleaned up on every
  terminal transition, not left to a 1-hour TTL.

## Freshness Check

**Baseline commit:** `66c718a60eb2db3b90e8dd7c7e352f6f7c8288cb`
**Issue filed at:** 2026-06-18T08:14:37Z
**Disposition:** Minor drift

Two cited dependencies merged *after* the issue was filed and both touch the exact
finalization path this plan modifies, so a full re-verification was mandatory:
- **#1711** (commit `03b667b3`) — added `_deliver_tool_timeout_degraded_notice` and
  advisory tool_timeout steering injection in `session_health.py`. This is the
  delivery pattern the issue's Solution Sketch says to reuse. **Confirmed landed.**
- **#1724** (commit `2efb58ce`) — recover stalled never_started / mid-run-wedge
  sessions. Adjacent recovery work; does not change the deferred-delivery gap.

**File:line references re-verified:**
- `agent/output_handler.py:365` — `steering_deferred = False` local boolean — **still holds** (line 365 exactly).
- `agent/output_handler.py:719` — `_inject_self_draft_steering()` — **still holds**; injection pushes steering with `sender="drafter-fallback"` (~line 794).
- `agent/output_handler.py:809-834` — `_apply_narration_fallback()` — **confirmed**; returns `NARRATION_FALLBACK_MESSAGE` when the first 500 chars are pure narration, else the original text.
- `agent/steering.py:179` — `SELF_DRAFT_MAX_ATTEMPTS = 2` — **still holds** (issue cited 184; constant is at 179, helper at 184 — minor drift).
- `agent/steering.py:189-213` — `bump_self_draft_attempts()` / TTL-only cleanup — **still holds** (issue cited 207).
- `agent/steering.py:216-234` — `reset_self_draft_attempts()` (Redis `DELETE`) — **confirmed**; this is the existing cleanup helper to reuse for AC4.
- `agent/steering.py:80-109` — `pop_all_steering_messages(session_id) -> list[dict]` — **confirmed**; each dict carries `sender` (so `"drafter-fallback"` is detectable).
- `agent/session_health.py:1257-1336` — `_deliver_tool_timeout_degraded_notice` — **confirmed** (issue cited an approximate 2190; the real symbol lives here). Idempotent via Redis SETNX `tool_timeout:degraded_sent:{session_id}`, resolves transport from `extra_context["transport"]`, sends via `_resolve_callbacks()` callback (FileOutputHandler fallback).
- `agent/session_health.py:1633-1634, 1657-1658` — the two `failed`-finalization branches that call `_deliver_tool_timeout_degraded_notice(entry, tool_name)` — **confirmed**; neither checks for `drafter-fallback` steering state. This is the gap.
- `agent/agent_session_queue.py:1408-1410` — `_execute_agent_session()` returns → `finalized_by_execute=True` — **confirmed** (issue cited 1236 for the task creation, which is at ~1221).
- `agent/agent_session_queue.py:1499` — `if not session_completed and not finalized_by_execute:` — the "running after complete" guard that only fires on the crash/cancel path — **confirmed** (issue cited 1537; the guard is at 1499, 1537 is the nudge-overwrite sub-block).

**Cited sibling issues/PRs re-checked:**
- #1680 — CLOSED 2026-06-13; introduced the pass-through drafter + self-draft pattern. Landscape intact.
- #1219 — CLOSED; self-draft mechanism was the resolution. Intact.
- #867 — CLOSED; nudge/finalize race. Same race family as Bug A.
- #875 — CLOSED; session_lifecycle CAS authority — root-cause fix for the race family. Bug A's worker-future leak is a surviving instance not covered by #875.
- #1711 / #1724 — merged since filing (see above).

**Commits on main since issue was filed (touching referenced files):**
- `03b667b3` (#1711) — **partially addresses**: added the degraded-notice delivery primitive this plan reuses, but did NOT add the `drafter-fallback` check. The gap survives.
- `2efb58ce` (#1724) — **irrelevant** to the deferred-delivery gap (different recovery class).

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/per_tool_timeout_tier_counters.md` (#1270, status `docs_complete`) — adjacent: it changes *progress detection* (per-tier liveness counters), not the *finalization-delivery* path. No conflict; this plan should not touch the progress-detection logic.

**Notes:** The bug is still present on current main — the two `failed`-finalization
branches finalize without any check for a pending self-draft. Reproduction by code
read is conclusive (the production timeline in the issue is the live repro);
re-deriving the 32-min ghost in a live worker is infeasible and unnecessary.

## Prior Art

- **#1680 / PR #1685** (CLOSED): Repositioned the message drafter from LLM rewriter to
  pass-through validation filter. **Introduced** the current empty-promise / self-draft
  pattern that this bug exposes. Relevant: the `needs_self_draft=True` → steering →
  outbox-skip flow originates here.
- **#1219** (CLOSED): Audit to prevent false promises across all delivery paths. The
  self-draft mechanism was the resolution. Relevant: the deferred-delivery design is
  intentional; this plan adds a *failure fallback* to it, it does not unwind it.
- **#1711 / PR #1738** (MERGED): MCP-hang graceful degradation — added
  `_deliver_tool_timeout_degraded_notice` + advisory steering injection. **Directly
  reused** by this plan as the delivery primitive for Bug B.
- **#867** (CLOSED): Race between nudge re-enqueue and `finalize_session()`. Same race
  family as Bug A's worker-future leak.
- **#875** (CLOSED): Promoted `models/session_lifecycle.py` to status authority with CAS —
  root-cause fix for the #867 race family. Bug A is a *surviving instance*: the worker
  returns normally (`finalized_by_execute=True`) without the internal completion
  transition firing, so the CAS authority is simply never invoked on this path.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1738 (#1711) | Added `_deliver_tool_timeout_degraded_notice` to the two `failed`-finalization branches | Delivers a *canned* degraded notice keyed on `tool_name`, but never checks whether a self-draft was deferred. A session with a pending `drafter-fallback` steering message still gets the generic notice (or nothing, since the notice is gated on `reason_kind == "tool_timeout"` only) and the user's *actual answer* — which the agent had narrated but deferred — is never delivered. |
| PR #875 | CAS status authority in `models/session_lifecycle.py` | Authoritative *when invoked*, but on the happy-path return from `_execute_agent_session()` the completion transition is expected to have already happened inside the executor. When the executor leaves the session `running` (deferred-delivery / unconsumed-steering path), `finalized_by_execute=True` suppresses the worker finally-block's completion guard — so CAS is never called and the session ghosts. |

**Root cause pattern:** deferred-delivery state and completion state are both
*implicit* — encoded in local variables and control flow rather than persisted/
asserted. The fix makes both states explicit at the terminal transition: check for
a pending `drafter-fallback` steering message before declaring failure, and ensure
the worker future resolves to a terminal status whenever the executor stops doing
SDK work.

## Data Flow

1. **Entry point**: Agent produces output → `TelegramRelayOutputHandler.send()` (`agent/output_handler.py`).
2. **Drafter gate**: `draft_message()` returns `needs_self_draft=True` for an empty promise → `steering_deferred = self._inject_self_draft_steering(session)` pushes a steering message tagged `sender="drafter-fallback"` onto the session's Redis steering queue and bumps `steering:attempts:{session_id}`.
3. **Outbox skip + persist defer state**: `if steering_deferred:` (`agent/output_handler.py:429-436`) → outbox write skipped, file dual-write only, `return`. **At this point** the handler persists `extra_context["deferred_self_draft_pending"] = True` and `extra_context["deferred_self_draft_text"] = text` on the `AgentSession` and saves, *before* the early `return`. The agent is still expected to consume the steering message on its next SDK turn and resend.
4. **Steering queue is drained at turn start, not finalization**: the agent's next SDK turn drains the steering queue (`pop_all_steering_messages` at pickup). **By the time the health checker finalizes, the queue is already empty** — the issue's Recon confirms this. So the detection signal **cannot** be the steering queue; it must be the persisted `extra_context["deferred_self_draft_pending"]` flag from step 3.
5. **Health-checker finalization**: if the session is killed (`tool_timeout`/`no_progress`) before delivery lands, `_apply_recovery_transition()` (`agent/session_health.py`) finalizes it as `failed` (non-local) or `abandoned` (local `no_progress`) — currently with no awareness of the persisted self-draft flag.
6. **Output (today)**: nothing, or the generic `tool_timeout` degraded notice. The deferred answer is lost.
7. **Output (desired)**: before each terminal `finalize_session(...)` in `_apply_recovery_transition()`, **read `entry.extra_context.get("deferred_self_draft_pending")`** (NOT the steering queue). On a truthy flag, deliver a fallback (apply the narration gate to `extra_context["deferred_self_draft_text"]` if recoverable, else an explicit "couldn't finish responding" notice) through the same callback path `_deliver_tool_timeout_degraded_notice` uses. This must fire on **all** terminal recovery branches — the two `failed` branches AND the local `abandoned` (`no_progress`) branch.

For Bug A: **entry** `_execute_agent_session()` finishes SDK work → saves `complete`
snapshot → **gap**: returns normally without a terminal CAS transition →
`finalized_by_execute=True` → worker finally-block completion guard skipped →
session stays `running` → **output**: 32-min ghost + spurious recovery.

## Architectural Impact

- **New dependencies**: none. Reuses `_deliver_tool_timeout_degraded_notice`'s callback
  resolution, `reset_self_draft_attempts`, and the narration fallback — all already in-tree.
- **Interface changes**: none to public signatures. A new private helper
  (e.g. `_deliver_deferred_self_draft_fallback(entry)`) in `agent/session_health.py`,
  parallel to the existing degraded-notice helper.
- **Coupling**: `agent/session_health.py` already imports from `agent.steering` and
  resolves output callbacks; this adds one read of `entry.extra_context` (a field it
  already reads for transport resolution). No new cross-module coupling beyond what
  #1711 established. **Note:** detection does NOT read the steering queue — that queue
  is empty by finalization time (see Data Flow step 4).
- **Data ownership**: the deferred-delivery signal is **persisted** (mandatory, per
  critique) into `AgentSession.extra_context` (existing nullable `DictField`) at defer
  time in `agent/output_handler.py` — keys `deferred_self_draft_pending` (bool) and
  `deferred_self_draft_text` (str). **No new top-level field, no migration** (honors the
  issue's scope constraint). The Redis steering queue still carries the agent-facing
  *instruction*; it is no longer the cross-process *detection* signal.
- **Reversibility**: high. Each of the three changes is an additive, independently
  revertable guard.

## Appetite

**Size:** Medium

**Team:** Solo dev, async-specialist (Bug A diagnosis), code reviewer

**Interactions:**
- PM check-ins: 1 (confirm fallback-message wording; persist-vs-drain is resolved by critique — persist is mandatory)
- Review rounds: 1-2 (the worker-future-leak fix touches the lifecycle hot path — needs careful review against the #867/#875 race family)

## Prerequisites

No external prerequisites — this work runs entirely against in-tree code and the
local Redis/worker. Reproduction relies on existing test fixtures.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | Steering-queue + counter operations |

Run all checks: `python scripts/check_prerequisites.py docs/plans/deferred_delivery_fallback_on_failure.md`

## Solution

### Key Elements

- **Persist defer state (Bug B, part 1)**: At defer time in `agent/output_handler.py`
  (the `if steering_deferred:` block, lines 429-436), persist
  `extra_context["deferred_self_draft_pending"] = True` and
  `["deferred_self_draft_text"] = text` on the `AgentSession` and save **before** the
  early `return`. This is the cross-process detection signal — the steering queue is
  empty by finalization time and cannot be used (critique blocker).
- **Deferred-self-draft fallback (Bug B, part 2)**: A new private async helper in
  `agent/session_health.py` that, before a session is finalized as `failed`/`abandoned`,
  reads `entry.extra_context["deferred_self_draft_pending"]`, and on a truthy flag
  delivers a recovery message through the same callback path the degraded notice uses.
  Wired into **all three** terminal recovery branches (two `failed` + the local
  `abandoned`/`no_progress` branch).
- **Worker-future-leak fix (Bug A)**: Ensure `_execute_agent_session()` resolves the
  session to the terminal status `completed` via
  `models/session_lifecycle.py::finalize_session(session, "completed", ...)` (NOT
  `transition_status` — it raises `ValueError` on terminal targets) whenever it stops
  doing SDK work, so the worker future resolves promptly and no spurious recovery fires.
- **Terminal-state counter cleanup (AC4)**: Call the existing
  `reset_self_draft_attempts(session_id)` whenever a session reaches a terminal
  status (`failed`, `killed`, `completed`), replacing reliance on the 1-hour TTL.

### Flow

Empty-promise output → self-draft steering injected (delivery deferred) →
**defer state persisted to `extra_context`** → [session killed by tool_timeout or
no_progress] → finalization path reads `extra_context["deferred_self_draft_pending"]`
→ on truthy flag, delivers fallback (narration-gated `deferred_self_draft_text`, or
explicit "couldn't finish" notice) → finalize as `failed`/`abandoned` → counter reset.

Happy path (Bug A): SDK work finishes → executor saves `complete` snapshot →
**`finalize_session(session, "completed", ...)`** → worker future resolves ≤10 s →
no ghost → no recovery.

### Technical Approach

- **Bug B, part 1 — persist defer state (detection signal).** The steering queue is
  the WRONG detection signal: the agent's next SDK turn drains it at pickup
  (`pop_all_steering_messages`), so by finalization time the queue is empty (the issue
  Recon confirms this). Draining it at finalization would detect nothing and the
  fallback would never fire. Instead, in `agent/output_handler.py`, inside the
  `if steering_deferred:` block (lines 429-436), **before** the early `return`,
  persist on the `AgentSession`:
  `session.extra_context = {**(session.extra_context or {}), "deferred_self_draft_pending": True, "deferred_self_draft_text": text}`
  then `session.save()`. Best-effort, wrapped so a persistence error never blocks the
  file dual-write / return. This is the cross-process, cross-turn signal the
  finalization path reads.
- **Bug B, part 2 — fallback delivery.** Add `_deliver_deferred_self_draft_fallback(entry)`
  in `agent/session_health.py`, modeled on `_deliver_tool_timeout_degraded_notice`
  (distinct SETNX idempotency lock `self_draft_fallback_sent:{sid}`; same
  `_resolve_callbacks()` + `FileOutputHandler` fallback; swallow-and-log). It reads
  `entry.extra_context.get("deferred_self_draft_pending")` — **NOT** the steering
  queue. On a truthy flag, it recovers `entry.extra_context.get("deferred_self_draft_text")`,
  applies `_apply_narration_fallback()` to it (substituting `NARRATION_FALLBACK_MESSAGE`
  / an explicit "I couldn't finish responding to that" notice when the recovered text
  is absent/whitespace or pure narration), and delivers. **Precedence over the canned
  degraded notice**: in the two `failed` branches (lines 1633-1634, 1657-1658) attempt
  the deferred-self-draft fallback *first*; only fall back to the generic degraded
  notice if `deferred_self_draft_pending` was not set. Both helpers are independently
  idempotent so a double-call cannot double-send. This is **not** gated on
  `reason_kind == "tool_timeout"` — a `no_progress` finalization with a pending
  self-draft must also deliver (the production timeline shows the `no_progress`
  recovery re-run is where the second deferral happens).
- **Bug B, part 3 — wire the `no_progress`/`abandoned` branch (blocker 3).** The
  `failed` branches are not the only terminal path. The local `no_progress` recovery
  finalizes the session as **`abandoned`** at `agent/session_health.py:1614-1625`
  (the `if is_local:` branch), which today has **no** fallback-delivery call. Add the
  `_deliver_deferred_self_draft_fallback(entry)` call to that `abandoned` branch too,
  so a deferred self-draft killed via the `no_progress`→`abandoned` path is not
  silently dropped. All three terminal branches (two `failed` + one `abandoned`) get
  the fallback; the requeue (`else`) branch does NOT (the session will run again).
- **Recovered text.** Persist+recover is **mandatory** (Open Question 1 → option (b),
  resolved by critique). The original deferred `text` is persisted in
  `extra_context["deferred_self_draft_text"]` at defer time (part 1) and recovered +
  narration-gated at delivery (part 2). When the key is absent/None/whitespace, the
  helper delivers the explicit "couldn't finish responding" notice, never an empty
  message. The canned-notice-only alternative is **removed as the primary path** — it
  remains only the degenerate case when no text was persisted.
- **Bug A — worker-future leak.** Audit `_execute_agent_session()`'s exit path. The
  symptom is: executor saves a `complete` snapshot and the output handler returns
  (deferred path `return`s early at `output_handler.py:436`), but the lifecycle is
  not finalized to `completed`, and `_execute_agent_session()` returns normally so
  `finalized_by_execute=True` suppresses the worker finally-block completion guard.
  The fix is to ensure the executor finalizes the session to the terminal status
  `completed` **via `models/session_lifecycle.py::finalize_session(session, "completed", ...)`**
  before returning whenever it has stopped doing SDK work — including the
  steering-deferred early-return and the unconsumed-steering re-enqueue path the issue
  flags. **`completed` is terminal**, so `transition_status()` is the WRONG API — it
  raises `ValueError` on terminal targets and a literal build would crash the worker
  (critique blocker 1). `finalize_session()` is the #875 CAS authority for terminal
  states; routing through it (no raw status writes) preserves the CAS guard and avoids
  re-opening the #867 race.
- **AC4 — counter cleanup.** Wire `reset_self_draft_attempts(session_id)` into the
  terminal-transition path. Cleanest seat: the lifecycle reaper hook that already
  fires on terminal transitions (the same place `finalize_session` telemetry reaps),
  so all three terminal statuses (`failed`, `killed`, `completed`) are covered by a
  single call site rather than sprinkling deletes across branches. Best-effort,
  swallow errors.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_deliver_deferred_self_draft_fallback` must never raise (mirrors
  `_deliver_tool_timeout_degraded_notice`'s swallow-and-log contract). Add a test
  that injects a callback raising an exception and asserts the finalization still
  completes and a `logger.warning` is emitted.
- [ ] The AC4 counter-reset call is best-effort `except Exception` — add a test that
  a Redis failure during reset does not block finalization (asserts the terminal
  transition still lands).

### Empty/Invalid Input Handling
- [ ] `extra_context["deferred_self_draft_pending"]` absent/falsy → fallback helper is
  a no-op, finalization proceeds, no spurious delivery; falls through to the generic
  degraded notice (tool_timeout) or nothing.
- [ ] `deferred_self_draft_pending` truthy but `deferred_self_draft_text`
  absent/None/whitespace → deliver the explicit "couldn't finish" notice, never an
  empty message.
- [ ] `extra_context` is None entirely → helper reads it defensively (`or {}`), no-op,
  no crash.

### Error State Rendering
- [ ] Assert the user-visible fallback message is actually delivered (outbox/file
  callback invoked with non-empty text) on the tool_timeout-kills-deferred-session path.
- [ ] Assert the generic degraded notice and the self-draft fallback never both send
  for the same session (idempotency locks hold).

## Test Impact

- [ ] `tests/unit/test_mcp_hang_graceful_degradation.py` — UPDATE: this is the existing
  module that houses #1711's `_deliver_tool_timeout_degraded_notice` tests
  (`tests/unit/test_session_health.py` does NOT exist — verified). Add cases for the new
  `_deliver_deferred_self_draft_fallback` helper, the new precedence (self-draft
  fallback before generic notice), and assert the generic notice is suppressed when a
  self-draft fallback fired. Add a case asserting the fallback fires on the local
  `abandoned`/`no_progress` branch, not just the `failed` branches.
- [ ] `tests/unit/test_mcp_hang_graceful_degradation.py` (degraded-notice idempotency
  cases) — UPDATE: confirm the two helpers' SETNX locks (`tool_timeout:degraded_sent:{sid}`
  vs. `self_draft_fallback_sent:{sid}`) are distinct so neither blocks the other.
- [ ] `tests/unit/test_output_handler.py` — UPDATE: add a case asserting that when
  `steering_deferred=True`, `extra_context["deferred_self_draft_pending"]` and
  `["deferred_self_draft_text"]` are persisted on the session before the early return.
- [ ] Worker-loop / lifecycle tests around `finalized_by_execute` and the finally-block
  completion guard (`tests/integration/test_agent_session_queue*.py` or equivalent) —
  UPDATE: add a regression asserting a session that finishes SDK work is finalized to
  `completed` (no lingering `running`) — the Bug A regression test. Assert the
  transition goes through `finalize_session`, not `transition_status`.
- [ ] Steering counter tests in `tests/unit/test_steering.py` — UPDATE: assert
  `steering:attempts:{session_id}` is deleted on terminal transition.

No existing tests are deleted or replaced — all changes are additive guards plus new
assertions on existing behavior.

## Rabbit Holes

- **Re-architecting deferred delivery to a persisted first-class field.** The issue
  explicitly forbids a new mandatory `AgentSession` field / migration. Stay in
  `extra_context` (nullable DictField) if persistence is needed.
- **Fixing the `StatusConflictError` on continuation re-enqueue** (logged at 07:53:49
  in the production timeline). The recon explicitly **dropped** this — it is a symptom
  of the underlying race, not the user-visible bug. Do not chase it here.
- **Touching the progress-detection / per-tier-counter logic** owned by the adjacent
  `per_tool_timeout_tier_counters.md` plan (#1270). This plan changes finalization
  delivery, not liveness detection.
- **Generalizing the fallback to all transports / all steering senders.** Scope to
  `sender == "drafter-fallback"` and the existing transport-resolution path. Broader
  routing is a separate concern.
- **Re-litigating #1724 / #1711 recovery decisions.** Build on them; do not reopen.

## Risks

### Risk 1: Double delivery (degraded notice + self-draft fallback)
**Impact:** User receives two messages for one failure.
**Mitigation:** Distinct SETNX idempotency locks per helper, and explicit precedence
(self-draft fallback first; generic notice only when no `drafter-fallback` was
pending). Test asserts mutual exclusivity.

### Risk 2: Bug A fix re-opens the #867/#875 nudge/finalize race
**Impact:** A nudge-enqueued session gets its `pending` status stomped back to
`completed`, or a CAS conflict crashes the worker.
**Mitigation:** Route the executor's completion transition exclusively through the
#875 CAS authority (`finalize_session(session, "completed", ...)` — the terminal-state
authority; `transition_status` would raise on the terminal target), never a raw status
write. Add a regression test that a nudge enqueued during execution is not overwritten.
Async-specialist reviews this change.

### Risk 3: Detection signal missed because the steering queue is empty at finalization
**Impact:** If detection read the steering queue, the fallback would NEVER fire — the
agent's next SDK turn drains the queue at pickup, so it is empty by finalization time
(the issue Recon confirms this). The original (pre-revision) plan had this exact bug.
**Mitigation:** Detection reads the **persisted** `extra_context["deferred_self_draft_pending"]`
flag (written at defer time in `output_handler.py`), not the steering queue. The flag
survives the drain and survives across processes. No queue read at finalization.

## Race Conditions

### Race 1: Persisted-flag read vs. agent's in-flight delivery
**Location:** `agent/session_health.py` terminal branches (two `failed` ~1633-1658
plus the local `abandoned` ~1614-1625) reading `extra_context` vs. the agent's next
turn that may consume the steering message and successfully deliver, then clear/leave
the flag.
**Trigger:** Health checker reads the persisted flag at the same moment a
(believed-dead) subprocess completes a successful re-delivery.
**Data prerequisite:** The subprocess must be confirmed dead/cancelled before the
fallback fires. **State prerequisite:** the fallback runs only in the terminal branches,
which are reached after cancel+SIGTERM+SIGKILL (or attempt-cap exhaustion).
**Mitigation:** Call the fallback only inside the terminal branches — never on the
requeue (`else`) branch where the session will run again. The `self_draft_fallback_sent:{sid}`
idempotency lock guards against a double-send. (Optional hardening: clear the
`deferred_self_draft_pending` flag on a successful agent self-draft in `output_handler`
so a terminal that arrives *after* a real delivery does not re-send — but the SETNX lock
already makes the fallback at-most-once per session.)

### Race 2: Concurrent degraded-notice and self-draft-fallback callers
**Location:** the two `failed` branches (1633-1634, 1657-1658).
**Trigger:** Two recovery passes finalize the same session near-simultaneously.
**Data prerequisite:** Both read the steering queue. **State prerequisite:** both
attempt delivery.
**Mitigation:** Each helper's SETNX lock (`tool_timeout:degraded_sent:{sid}` and
`self_draft_fallback_sent:{sid}`) ensures first-caller-wins per message type.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #1270]` Per-tier timeout liveness counters / progress-detection
  changes — owned by `docs/plans/per_tool_timeout_tier_counters.md`. This plan does
  not modify `_has_progress` or the freshness windows.
- `[ORDERED]` Fixing the `StatusConflictError` on continuation re-enqueue — the recon
  dropped it as a symptom, not a root cause; it must wait until the underlying race
  family is revisited under a dedicated issue, not bundled into a user-facing hotfix.

## Update System

No update system changes required — this is a bridge/worker-internal behavior fix.
No new dependencies, config files, or migration steps. The fix ships with the next
`/update` pull and `valor-service.sh restart` like any other worker/health-checker
code change.

## Agent Integration

No agent integration required — this is a worker/health-checker-internal change. The
agent's user-facing output continues to flow through the existing output handler and
the same registered send callbacks; no new CLI entry point and no bridge import
changes. The only agent-observable effect is that a previously-dropped message now
gets delivered. Integration coverage is the end-to-end test that drives a deferred
self-draft to a `tool_timeout` finalization and asserts a delivery lands.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` (or the deferred-delivery /
  self-draft doc, whichever owns the empty-promise flow) to describe the new
  finalization-time fallback delivery and the worker-future-leak fix.
- [ ] Cross-reference from the `_deliver_tool_timeout_degraded_notice` documentation
  (the #1711 MCP-hang graceful-degradation doc) noting the new precedence:
  self-draft fallback takes priority over the generic degraded notice.

### External Documentation Site
- [ ] Not applicable — this repo has no separate docs site for worker internals.

### Inline Documentation
- [ ] Docstring on `_deliver_deferred_self_draft_fallback` documenting idempotency,
  the terminal-branch-only precondition, and the swallow-and-log contract.
- [ ] Comment at the finalization call sites explaining the precedence over the
  generic degraded notice and why the fallback is safe only in terminal branches.

## Success Criteria

- [ ] When a session is finalized as `failed` **or `abandoned` (no_progress)** with a
  persisted `extra_context["deferred_self_draft_pending"]` flag, a fallback message is
  delivered to the user before the session closes (narration fallback of the recovered
  `deferred_self_draft_text`, or an explicit "couldn't finish responding" notice). [AC1]
- [ ] When a session's executor completes its SDK run and saves a `complete`
  snapshot, the executor finalizes it to `completed` via `finalize_session()` (not
  `transition_status`) and the worker future resolves within 10 s — no 32-min ghost
  `running` state, no spurious `no_progress` recovery. [AC2]
- [ ] Unit/integration test covers: empty-promise → self-draft steering injected →
  session task cancelled (tool_timeout) → fallback message delivered. [AC3]
- [ ] `steering:attempts:{session_id}` Redis key is deleted on terminal transition
  (`failed`, `killed`, `completed`). [AC4]
- [ ] Generic degraded notice and self-draft fallback never both deliver for one
  session (idempotency verified by test).
- [ ] No new mandatory `AgentSession` field / no migration (any persistence uses the
  existing nullable `extra_context` DictField).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (delivery-fallback)**
  - Name: `fallback-builder`
  - Role: Implement `_deliver_deferred_self_draft_fallback` + finalization precedence (Bug B) and AC4 counter cleanup.
  - Agent Type: builder
  - Resume: true

- **Builder (worker-future-leak)**
  - Name: `leak-builder`
  - Role: Diagnose and fix the worker-future leak so completed sessions transition to `completed` via CAS (Bug A).
  - Agent Type: async-specialist
  - Resume: true

- **Validator (delivery)**
  - Name: `delivery-validator`
  - Role: Verify AC1/AC3/AC4 + idempotency / no-double-send.
  - Agent Type: validator
  - Resume: true

- **Validator (lifecycle)**
  - Name: `lifecycle-validator`
  - Role: Verify AC2 + no #867/#875 race regression.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `docs-writer`
  - Role: Update session-lifecycle / graceful-degradation docs.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard tiers — `builder`, `validator`, `async-specialist`, `documentarian`.)

## Step by Step Tasks

**Commit sequencing (per issue guidance):** Bug B (delivery fallback) lands **first**,
as its own commit. Bug A (worker-future leak) lands as a **separate, second commit**.
The two bugs are independent; bundling them was a critique concern. The builder for
Bug A must commit separately even though the work is parallelizable.

### 1. Persist defer state + delivery fallback + precedence (Bug B) — FIRST COMMIT
- **Task ID**: build-delivery-fallback
- **Depends On**: none
- **Validates**: `tests/unit/test_mcp_hang_graceful_degradation.py` (add cases), `tests/unit/test_output_handler.py` (persistence case)
- **Assigned To**: fallback-builder
- **Agent Type**: builder
- **Parallel**: true
- **Persist defer state.** In `agent/output_handler.py`, inside the `if steering_deferred:` block (lines 429-436), **before** the early `return`, set `session.extra_context["deferred_self_draft_pending"] = True` and `["deferred_self_draft_text"] = text` (merging into the existing dict, `extra_context or {}`) and `session.save()`. Best-effort, never blocks the file dual-write / return.
- Add `_deliver_deferred_self_draft_fallback(entry)` in `agent/session_health.py`, modeled on `_deliver_tool_timeout_degraded_notice` (distinct SETNX lock `self_draft_fallback_sent:{sid}`, `_resolve_callbacks` + FileOutputHandler fallback, swallow-and-log).
- **Detect via `entry.extra_context.get("deferred_self_draft_pending")` — NOT the steering queue** (the queue is drained at turn start and empty by finalization; using it would never fire the fallback). Recover `extra_context.get("deferred_self_draft_text")` and apply `_apply_narration_fallback()`; deliver the explicit "couldn't finish" notice when the text is absent/whitespace.
- Wire the fallback into the **two `failed` branches** (1633-1634, 1657-1658) **before** the generic degraded notice, with precedence (generic notice only if `deferred_self_draft_pending` was not set). Do **not** gate on `reason_kind == "tool_timeout"`.
- **Wire the fallback into the local `abandoned`/`no_progress` branch too** (`if is_local:`, `agent/session_health.py:1614-1625`) — this is the `no_progress` terminal path and was previously unwired (blocker 3). All three terminal branches deliver; the requeue (`else`) branch does NOT.
- Commit this as the first, standalone commit (Bug B only).

### 2. Terminal-state counter cleanup (AC4) — part of FIRST COMMIT
- **Task ID**: build-counter-cleanup
- **Depends On**: none
- **Validates**: `tests/unit/test_steering.py` (add deletion assertion)
- **Assigned To**: fallback-builder
- **Agent Type**: builder
- **Parallel**: true
- Call `reset_self_draft_attempts(session_id)` from the single terminal-transition reaper seat covering `failed`/`killed`/`completed`. Best-effort, swallow errors. May ride in the Bug B commit (same builder, same delivery concern).

### 3. Worker-future-leak fix (Bug A) — SECOND, SEPARATE COMMIT
- **Task ID**: build-worker-leak
- **Depends On**: none (parallelizable), but lands as a distinct commit after Bug B
- **Validates**: `tests/integration/test_agent_session_queue*.py` (add Bug A regression)
- **Assigned To**: leak-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Trace `_execute_agent_session()` exit paths (steering-deferred early return, unconsumed-steering re-enqueue) and ensure the session is finalized to `completed` before normal return when SDK work is done.
- **Use `models/session_lifecycle.py::finalize_session(session, "completed", ...)`** — NOT `transition_status()`, which raises `ValueError` on the terminal `completed` target and would crash the worker on a literal build (blocker 1). `finalize_session` is the #875 CAS authority for terminal states; never a raw status write. Preserve the #867 nudge-overwrite guard.
- Commit Bug A as its own separate commit, after the Bug B commit.

### 4. Validate delivery (AC1/AC3/AC4)
- **Task ID**: validate-delivery
- **Depends On**: build-delivery-fallback, build-counter-cleanup
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the deferred→tool_timeout→fallback test; assert delivery + no double-send + counter deleted.

### 5. Validate lifecycle (AC2 + race safety)
- **Task ID**: validate-lifecycle
- **Depends On**: build-worker-leak
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Assert a completed session transitions to `completed` ≤10 s; assert no nudge-overwrite regression.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-delivery, validate-lifecycle
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-lifecycle.md` and cross-reference the #1711 graceful-degradation doc with the new precedence.

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full suite; verify every Success Criterion incl. docs; generate report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Fallback wired | `grep -n "_deliver_deferred_self_draft_fallback" agent/session_health.py` | output > 1 |
| Counter cleanup wired | `grep -rn "reset_self_draft_attempts" agent/ | grep -v "agent/steering.py"` | output contains a terminal-transition call site |
| No new mandatory field | `git diff main -- models/agent_session.py | grep -E '^\+.*= (Field|IndexedField|KeyField)\(' | grep -v 'null=True'` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | War-room (unanimous) | Bug A used `transition_status` to reach `completed`; raises `ValueError` on terminal targets — a literal build crashes the worker. | Solution / Technical Approach / Task 3 / Risk 2 / AC2 | Use `models/session_lifecycle.py::finalize_session(session, "completed", ...)`. |
| BLOCKER | War-room | Bug B detected `drafter-fallback` by draining the steering queue at finalization, but the agent already drained it at turn start (queue empty). Fallback would never fire. | Data Flow / Solution / Technical Approach (part 1) / Task 1 / Risk 3 | Persist `deferred_self_draft_pending` + `deferred_self_draft_text` in `extra_context` at defer time in `output_handler.py`; read the persisted flag at finalization. Persist+recover is now MANDATORY. |
| BLOCKER | War-room | `no_progress` routes to an unwired `abandoned` branch; only the two `failed` branches were wired. | Solution / Technical Approach (part 3) / Task 1 / AC1 | Wire `_deliver_deferred_self_draft_fallback` into the `if is_local:` `abandoned` branch (`session_health.py:1614-1625`) too. |
| CONCERN | War-room | Mis-pathed file: `agent/session_lifecycle.py` does not exist. | Prior Art / Why Previous Fixes Failed / Technical Approach | Corrected to `models/session_lifecycle.py` throughout. |
| CONCERN | War-room | Test file `tests/unit/test_session_health.py` does not exist; real tests live in `test_mcp_hang_graceful_degradation.py`. | Test Impact / Failure Path Test Strategy / Tasks | Repointed to `tests/unit/test_mcp_hang_graceful_degradation.py`, `test_output_handler.py`, `test_steering.py`. |
| CONCERN | War-room | Bug A bundled with Bug B against the issue's "separate commit, Bug B first" guidance. | Step by Step Tasks (sequencing note) | Bug B first commit; Bug A as a separate second commit. |

---

## Resolved Decisions (formerly Open Questions)

Both questions were resolved by the FULL war-room critique (NEEDS REVISION pass); no
remaining human input is required before build.

1. **Recovered text vs. canned notice → RESOLVED: persist + recover (option b),
   MANDATORY.** The steering queue is empty by finalization (drained at turn start), so
   a queue-based detection signal would never fire — persistence is the *only* viable
   detection path, not merely a content-recovery nicety. The original deferred `text`
   is persisted into `AgentSession.extra_context["deferred_self_draft_text"]` (alongside
   `["deferred_self_draft_pending"] = True`) at defer time in `output_handler.py`, and
   recovered + narration-gated at delivery. The canned-notice-only alternative is
   removed as the primary path; the explicit "couldn't finish" notice remains only the
   degenerate case when no text was persisted. Migration-free (nullable `extra_context`).
2. **`no_progress` terminal finalization → RESOLVED: covered, wiring fixed.** The
   fallback fires on the `no_progress` path too, not just `tool_timeout`. The production
   timeline shows the second deferral happens on the `no_progress` recovery re-run. The
   `no_progress` local path finalizes as **`abandoned`** (`agent/session_health.py:1614-1625`),
   which was previously unwired — the fallback delivery is now wired into that branch as
   well as the two `failed` branches.
