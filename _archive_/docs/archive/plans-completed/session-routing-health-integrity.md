---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/963
last_comment_id:
revision_applied: true
---

# Session Routing Integrity: Steering Dedup Gap + Health Check Parent Safety

## Problem

On 2026-04-14, a user message was steered correctly into session `tg_valor_-1003449100931_617`, but the reconciler fired three minutes later, found no dedup record for the message, and spawned a duplicate session `tg_valor_-1003449100931_623`. That duplicate session was then reset to `pending` by the health check — even though it had spawned a child dev session actively doing work — causing lost output and a wrong reply delivered to the user.

Two independent bugs compounded and were identified and fixed in commit `78b275b3` (2026-04-14):

**Bug 1:** A message steered into session A via `push_steering_message()` was never marked handled in the dedup store. Three minutes later the reconciler scanned, found no record, and enqueued a brand-new session B. Both A and B executed; duplicate or conflicting output was delivered.

**Bug 2:** A PM session in `running` status for >300s with zero `turn_count`, empty `log_path`, and no `claude_session_uuid` was declared stuck by `_has_progress()` and reset to `pending` — even when a child dev session was actively running and would attempt to steer results back.

**Resolution:** Both bugs were fixed in a single deploy (commit `78b275b3`):

- All four unguarded steering paths in `bridge/telegram_bridge.py` now call `record_telegram_message_handled()` before returning, preventing the reconciler from re-dispatching steered messages.
- `_has_progress()` in `agent/agent_session_queue.py` was extended to consult `get_children()` — a PM session with at least one non-terminal child is no longer treated as stuck.

## Freshness Check

**Baseline commit:** `91d77451f3746fae43a38c5f747d7727f17a9740`
**Issue filed at:** 2026-04-14T14:29:38Z
**Disposition:** Minor drift

*Note: Freshness check was run at issue-filing time (2026-04-14T14:29:38Z). Fix commit `78b275b3` was merged subsequently, resolving both bugs. The file:line references below reflect the state at plan-writing time and were accurate at that point; the fix was applied at those exact locations.*

**File:line references verified at plan time:**

- `bridge/telegram_bridge.py:~1023` — semantic-routing steer path — confirmed at L1023; no dedup record written before `return` at L1046. Fixed in `78b275b3` (dedup now at L1046).
- `bridge/telegram_bridge.py:~1244` — reply-to running/active session steer path — confirmed at L1244; no dedup record before `return` at L1260. Fixed in `78b275b3` (dedup now at L1261).
- `bridge/telegram_bridge.py:~1283` — reply-to pending session steer path — confirmed at L1283; no dedup record before `return` at L1299. Fixed in `78b275b3` (dedup now at L1301).
- `bridge/telegram_bridge.py:~1323` — live-guard steer path — confirmed at L1323; no dedup record before `return` at L1334. Fixed in `78b275b3` (dedup now at L1337).
- `bridge/telegram_bridge.py:~1498` — coalescing guard steer path — already had `record_telegram_message_handled` at L1525 (confirmed in source).
- `bridge/telegram_bridge.py:~1611` — intake-classifier interjection steer path — already had `record_telegram_message_handled` at L1633 (confirmed in source).
- `agent/agent_session_queue.py:~1353` — `_has_progress()` — confirmed at L1353–1372 at plan time; read only `turn_count`, `log_path`, `claude_session_uuid`; no child-session lookup. Fixed in `78b275b3` (child lookup added at L1386–1388).
- `agent/agent_session_queue.py:~1453` — health check invokes `_has_progress()` — confirmed at L1453–1456; path `worker alive but no progress signal` triggers recovery. No change to call site; predicate now returns correctly.

**Cited sibling issues/PRs re-checked:**

- #948 (centralize dedup in dispatch wrapper) — closed 2026-04-14T07:17:46Z; PR #952 merged. Moved enqueue-path dedup into `bridge/dispatch.py::dispatch_telegram_session`. Steering paths were explicitly excluded per design — this was the root cause of Bug 1.
- #944 (health check recovery for stuck slugless dev sessions) — closed 2026-04-14T05:16:33Z; introduced `_has_progress()` to guard dev sessions co-running with PM. Did not account for PM sessions waiting on running children — this was the root cause of Bug 2.
- #958 (PM context overflow) — still open; separate issue.

**Commits on main since issue was filed (touching referenced files):** Fix shipped in `78b275b3` (2026-04-14).

**Active plans in `docs/plans/` overlapping this area:**

- `health-check-no-progress-recovery.md` — status: docs_complete (shipped as #944); no overlap conflict.

**Notes:** The issue cited four steering paths at L1023, L1244, L1283, L1323. Code inspection confirmed exactly those four were missing dedup. Two other steer paths (L1498 coalescing guard, L1611 intake-classifier interjection) already had dedup records. The `push_steering_message` call scope was audited: all calls at L1023, L1245, L1285, L1326 are within `handler()` scope; L1177 is an import; L1502/L1508/L1615/L1631/L1667 are in paths already covered. The AST contract test correctly targets all affected paths.

## Prior Art

- **PR #952 / Issue #948** — Centralize dedup recording in bridge dispatch. Merged 2026-04-14. Added `bridge/dispatch.py::dispatch_telegram_session` that pairs enqueue + dedup atomically. Steering paths were explicitly excluded because they don't enqueue. This was the incomplete refactor that left the Bug 1 gap. The fix pattern (`record_telegram_message_handled`) and the AST contract test in `tests/unit/test_bridge_dispatch_contract.py` were proven infrastructure extended by this fix.
- **PR #947 / Issue #944** — Health check recovery for slugless dev sessions. Merged 2026-04-14. Introduced `_has_progress()` to distinguish stuck dev sessions from PM sessions sharing `worker_key`. Did not consider the inverse: a PM session with a running child. The Bug 2 fix extends `_has_progress()` via `get_children()`.
- **Issue #723** — Audit all session recovery mechanisms for completed-session respawn safety. Merged 2026-04-05. Background on the delivery guard (`response_delivered_at`) added to the health check. No direct overlap with parent/child hierarchy awareness.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #952 (#948) | Centralized enqueue-path dedup in `dispatch_telegram_session` wrapper | Steering paths were deliberately excluded; no follow-up issue tracked the gap |
| PR #947 (#944) | Added `_has_progress()` to guard dev sessions with a co-running PM | Only addressed one direction: PM alive → dev not stuck. Did not address PM with no self-turns having a running child |

**Root cause pattern:** Both fixes addressed the symptom visible at the time without auditing all sibling paths. Bug 1 fix covered enqueue paths; steering paths were out of scope. Bug 2 fix covered dev sessions; PM sessions as parents were out of scope.

## Data Flow

### Bug 1: Steering without dedup (pre-fix)

1. **Entry**: Telegram message arrived in `handler()`.
2. **Routing**: Handler identified a matching session and called `push_steering_message()` — one of the four unguarded paths.
3. **Gap**: Handler returned without calling `record_telegram_message_handled()`. No dedup record written to Redis.
4. **Reconciler scan** (180s later): `bridge/reconciler.py::reconcile_once()` fetched recent messages, checked `bridge/dedup.py::is_duplicate_message()` for each. The steered message had no record — treated as "missed".
5. **Re-dispatch**: Reconciler called `dispatch_telegram_session()`, which enqueued a new session and wrote dedup (too late — duplicate session already created).
6. **Output**: Both the original steered session and the duplicate session executed; conflicting output delivered.

### Bug 2: Health check killing a PM with active children (pre-fix)

1. **Entry**: PM session started, spawned a dev child via `valor_session create --role dev`.
2. **PM status**: PM was in `running` status throughout. It never set `turn_count`, `log_path`, or `claude_session_uuid` (those are set only during direct agent execution, which PM sessions don't do while waiting for a child).
3. **Health check loop** (300s interval): `_agent_session_health_check()` scanned all `running` sessions.
4. **Predicate**: For PM session with `worker_alive=True` and `running_seconds > 300`: called `_has_progress(entry)`. Returned `False` because all three fields were empty.
5. **Recovery**: `should_recover = True`. Health check reset PM to `pending`.
6. **Race**: PM re-executed with no memory of prior state. Child session finished and called `steer_session(parent.session_id, ...)` — parent was now in an inconsistent state (re-running from scratch). Child's result was lost or delivered to wrong context.

## Architectural Impact

- **New dependency on `get_children()`**: `_has_progress()` now calls `entry.get_children()` — already a method on `AgentSession`. No new imports required.
- **Interface change**: `_has_progress()` semantics expanded: was "session has done work itself"; now "session has done work itself OR has active children doing work on its behalf." Docstring updated to reflect expanded contract.
- **Coupling**: Minimal increase — health check already imported `AgentSession`; no new cross-module dependency.
- **Reversibility**: Both fixes are additive (adding dedup calls, adding a predicate check). Safe to revert independently.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation before build)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work had no external dependencies. Redis and the test suite were always available.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Test suite runs | `pytest tests/unit/test_bridge_dispatch_contract.py tests/unit/test_health_check_recovery_finalization.py -q` | Confirm baseline passed before touching these files |

## Solution

Fix shipped in commit `78b275b3` (2026-04-14). Both bugs addressed in a single deploy.

### What Was Done

**Bug 1 — Dedup added to four unguarded steering paths:**

The four previously unguarded steering paths in `bridge/telegram_bridge.py` were updated to call `await record_telegram_message_handled(event.chat_id, message.id)` immediately before `return`:

1. **Semantic routing path** (L1046): After `push_steering_message(matched_id, ...)` and `await send_markdown(...)`, dedup now recorded before `return`.
2. **Reply-to running/active session path** (L1261): After `push_steering_message(session_id, ...)` and `await send_markdown(...)`, dedup now recorded before `return`.
3. **Reply-to pending session path** (L1301): After `push_steering_message(session_id, ...)` and `await send_markdown(...)`, dedup now recorded before `return`.
4. **Live-guard steer path** (L1337): After `push_steering_message(session_id, ...)` and `await send_markdown(...)`, dedup now recorded before `return`.

`record_telegram_message_handled` was already imported at the top of `telegram_bridge.py` (L103). The dedup call is async and swallows exceptions internally — no try/except needed at the call site.

**AST contract extension** (`tests/unit/test_bridge_dispatch_contract.py`): A new `test_steering_paths_record_dedup` test was added. The AST walker verifies that every call to `push_steering_message` in `handler()` is followed (in linear branch order) by a call to `record_telegram_message_handled` before the next `return`. The walker handles nested branches correctly — a `return` inside a nested `if` checks whether any ancestor scope has a pending `push_steering_message`. A synthetic violating source (nested-branch case) is included to verify the walker catches nested violations. Scope audit confirmed all `push_steering_message` calls in `telegram_bridge.py` are within `handler()` scope or in paths already covered by dedup.

**Bug 2 — `_has_progress()` extended for child-activity awareness:**

`_has_progress()` in `agent/agent_session_queue.py` was extended to call `entry.get_children()` and return `True` if any child has `status not in _TERMINAL_STATUSES`. The actual shipped implementation:

```python
# Child-progress check: a PM session with active children is not stuck
# get_children() queries via Popoto parent_agent_session_id index (not string session_id)
# and already returns [] on failure with a WARNING log — no outer try/except needed
children = entry.get_children()
if any(c.status not in _TERMINAL_STATUSES for c in children):
    return True
return False
```

The child-activity check applies to all session types by design — any session that has spawned children and is waiting for them should not be treated as stuck. If profiling later shows this is expensive at scale (threshold: health check loop taking >5s for a 1000-session deployment), a session-type guard can be added before the child check.

`_TERMINAL_STATUSES` is already defined in scope. `get_children()` does a Redis query by `parent_agent_session_id` — already used in `_agent_session_hierarchy_health_check()`.

The docstring was updated to document: (a) the original three own-fields, (b) the new child-activity check, (c) the non-fatal exception behavior via `get_children()`'s internal handler.

### Flow After Fix

**Bug 1:**

Telegram message → handler steer path → `push_steering_message()` → `await record_telegram_message_handled(event.chat_id, message.id)` → `return` → reconciler scans 180s later → dedup record found → message skipped (no duplicate session)

**Bug 2:**

Health check fires → `worker_alive=True`, `running_seconds > 300`, own-progress fields empty → `get_children()` returns child with `status="running"` → `any(c.status not in _TERMINAL_STATUSES ...)` = True → `_has_progress()` returns `True` → `should_recover = False` → PM not reset → child completes → steers PM → pipeline continues normally

## Failure Path Test Strategy

### Exception Handling Coverage

- `get_children()` already handles exceptions internally and returns `[]` on failure (models/agent_session.py:1487-1493). No outer try/except needed in `_has_progress()`. The `any(...)` on an empty list correctly returns `False`. *Note: This means a Redis failure during the child lookup causes `_has_progress()` to return `False` for the child-activity check — the session is treated as childless. This is the accepted fail direction (consistent with the existing system's best-effort design). A spike in the `no_progress` counter for PM session types in `curl localhost:8500/dashboard.json` is the observable signal if this occurs in practice.*
- `record_telegram_message_handled` internally calls `record_message_processed`, which already swallows exceptions and logs at WARNING. No additional exception handling needed at the call sites in `telegram_bridge.py`. Existing `test_dedup.py` covers the warning emission on Redis failure.

### Empty/Invalid Input Handling

- `get_children()` returns an empty list when no children exist — `any(...)` on an empty list returns `False`, so `_has_progress()` correctly falls through to `False` for childless sessions.
- `record_telegram_message_handled(chat_id, message_id=0)` — dedup.py handles zero/None gracefully (existing behavior, no change needed).

### Error State Rendering

- No user-visible output from these components. Failure is observable via logs (`[session-health]` WARNING lines and bridge DEBUG lines). Tests assert log emission rather than rendered output.

## Test Impact

- [x] `tests/unit/test_bridge_dispatch_contract.py` — UPDATED: added `test_steering_paths_record_dedup` test class with AST-level assertion that every `push_steering_message` call in handler is followed by `record_telegram_message_handled` before `return`. Existing contract tests (`test_handler_contains_no_direct_banned_calls`, `test_dispatch_calls_enqueue_before_record`) are unaffected and remain green.
- [x] `tests/unit/test_health_check_recovery_finalization.py` — UPDATED: added test cases for the extended `_has_progress()` predicate: (a) returns `True` when session has a child with `status="running"`, (b) returns `True` when child has `status="pending"`, (c) returns `False` when all children are terminal, (d) returns `False` when no children exist.
- [x] `tests/unit/test_reconciler.py` — No existing test cases broken; dedup additions are transparent to the reconciler (it already skips messages with a dedup record).
- [x] `tests/unit/test_steering_mechanism.py` — No existing test cases broken; changes were additive.

## Rabbit Holes

- **Moving dedup into `push_steering_message()` itself**: Tempting as centralization, but `push_steering_message()` is in `agent/steering.py` — a layer that has no knowledge of Telegram message IDs or chat IDs. Threading those through would require a signature change and touch all call sites. The current pattern (caller records dedup after the steering call) is consistent with how enqueue paths work and is the right boundary.
- **Reconciler cross-checking active sessions before dispatch**: The reconciler could query running sessions for their steering queues before treating a message as missed. This is a valid defense-in-depth but is a larger change with its own race conditions (what if the session just started?). The dedup record is the correct primary signal; the reconciler's current design is correct.
- **Unified `_has_progress` + hierarchy pass**: Merging `_agent_session_health_check()` and `_agent_session_hierarchy_health_check()` into a single pass that shares data. This would be a larger refactor touching many code paths; deferred. The fix (adding child-activity check inside `_has_progress()`) is sufficient and isolated.
- **300s threshold adjustment for PM sessions**: The issue notes the threshold may be too low. Changing constants is out of scope here — it requires data analysis of typical PM session lifecycles. The child-activity check is the structurally correct fix; threshold tuning is separate.

## Risks

### Risk 1: get_children() is slow under high session count
**Impact:** Health check loop takes longer per iteration; at extreme scale (1000+ sessions), the loop could become the bottleneck.
**Mitigation:** `get_children()` queries by `parent_agent_session_id` index — a Redis set lookup, O(n) in the number of children for that parent. PM sessions typically have 1–2 children. If profiling shows this is expensive (threshold: health check loop >5s for 1000-session deployment), the child lookup can be skipped for non-PM session types (add `if entry.session_type != SessionType.PM: return False` before the child check). `SessionType` is already imported in `agent_session_queue.py`.

### Risk 2: Record-dedup race on steering paths
**Impact:** If `record_telegram_message_handled` is called after `push_steering_message` but the bridge crashes between those two calls, the message will be re-dispatched by the reconciler on restart — creating a duplicate session.
**Mitigation:** This is the same race that exists on all other dedup paths; the existing design accepts it. The window is milliseconds (between the push and the dedup record). The dedup system is async and best-effort by design. Acceptable. Both fixes deploy together in a single bridge/worker restart; each can ship alone without risk from the other.

### Risk 3: get_children() exception masks real data issues
**Impact:** If `get_children()` consistently fails for a PM session due to data corruption, the child-activity check always returns `False`, and the PM session is treated as childless — vulnerable to the same Bug 2 recovery as before the fix.
**Mitigation:** The exception is logged at DEBUG (consistent with non-fatal health check patterns). The WARNING already emitted by the health check for `no_progress` recovery remains visible. **Production signal:** A spike in the `no_progress` counter for PM session types in `curl localhost:8500/dashboard.json` (the `session-health:recoveries:no_progress` Redis counter, already instrumented) indicates this pattern. If it occurs in practice, investigate via `valor-session children --id <id>`. The accepted fail direction (treat as childless on error) is consistent with the existing system's best-effort approach.

## Race Conditions

### Race 1: Steer path dedup written after bridge crash
**Location:** `bridge/telegram_bridge.py` L1046 (and three other steer paths)
**Trigger:** Bridge crashes after `push_steering_message()` succeeds but before `record_telegram_message_handled()` completes.
**Data prerequisite:** Dedup record must be written before reconciler's next 180s scan.
**State prerequisite:** Bridge must complete both the push and the record for the message to be fully handled.
**Mitigation:** Accepted race; same window as all other dedup paths. The reconciler produces a duplicate session in the rare crash case — this is the existing system behavior for any crash-between-steps scenario.

### Race 2: Health check fires while child session is in `pending` (not yet `running`)
**Location:** `agent/agent_session_queue.py:_has_progress()`
**Trigger:** Dev child has been enqueued (status=`pending`) but not yet started. Health check fires; `get_children()` returns the child with status `pending`. `_has_progress()` correctly returns `True` (pending is not terminal).
**Data prerequisite:** Child `status` field must be set to `pending` before health check fires (it is, immediately on creation).
**State prerequisite:** `_TERMINAL_STATUSES` must not include `pending`.
**Mitigation:** Confirmed: `pending` is not in `_TERMINAL_STATUSES`. The child-activity check uses `status not in _TERMINAL_STATUSES`, which covers `running`, `active`, `pending`, `dormant`, `waiting_for_children`, and `paused` — all non-terminal states.

### Race 3: PM session finalized as `completed` by PM executor between health check load and child lookup
**Location:** `agent/agent_session_queue.py:_agent_session_health_check()`
**Trigger:** Health check loads the running sessions list; PM transitions to `completed` between the load and the `_has_progress()` call; health check tries to recover an already-completed session.
**Data prerequisite:** Health check must re-read session status before applying recovery.
**State prerequisite:** `transition_status()` has CAS re-read that would detect `completed` and prevent reset to `pending`.
**Mitigation:** `transition_status()` already performs a CAS re-read. If the PM completed between the health check's initial load and the recovery call, the CAS fails gracefully with a logged conflict. No new mitigation needed.

### Race 4: parent_agent_session_id assignment timing
**Location:** `agent/agent_session_queue.py:_push_agent_session()`
**Trigger:** A concern was raised about whether `parent_agent_session_id` is set before the health check could query children.
**Resolution:** `parent_agent_session_id` is set synchronously in `_push_agent_session()` at `agent_session_queue.py:2122` before the call returns. `get_children()` always sees the child immediately after creation — no timing window exists.

## No-Gos (Out of Scope)

- Refactoring `push_steering_message()` to accept Telegram message metadata and record dedup itself
- Modifying the reconciler's dispatch logic to cross-check steering queues
- Changing `AGENT_SESSION_HEALTH_MIN_RUNNING` or `AGENT_SESSION_HEALTH_CHECK_INTERVAL` constants
- Merging `_agent_session_health_check()` and `_agent_session_hierarchy_health_check()` into a single pass
- Fixing the context overflow bug (#958) — separate issue

## Update System

No update system changes required — this is a bridge and worker internal fix with no new dependencies, config keys, or migration steps. Both files (`bridge/telegram_bridge.py`, `agent/agent_session_queue.py`) are deployed via the standard `./scripts/valor-service.sh restart` cycle.

## Agent Integration

No agent integration required — these are bridge-internal and worker-internal changes. The Telegram bridge and the worker are already the components that route messages and run health checks. No MCP server changes, no `.mcp.json` changes.

## Documentation

- [x] `docs/features/message-reconciler.md` updated to document that all steering paths must call `record_telegram_message_handled` (dedup contract section).
- [x] `agent/agent_session_queue.py::_has_progress()` docstring updated to document the child-activity check and its non-fatal exception behavior.
- [ ] `docs/features/bridge-module-architecture.md` — verify at build time whether the steering-path dedup contract is described there and update if so.

## Success Criteria

- [x] A message steered into a running session is not re-dispatched by the reconciler within the next 180s window
- [x] A PM session with at least one running/pending child dev session is not reset to `pending` by the health check
- [x] The AST contract test covers all four steering paths for dedup presence (including nested-branch detection)
- [x] Unit test for `_has_progress()` with a child in a non-terminal state returns `True`
- [x] Unit test for `_has_progress()` when no children exist returns `False`
- [x] `pytest tests/unit/test_bridge_dispatch_contract.py tests/unit/test_health_check_recovery_finalization.py tests/unit/test_reconciler.py tests/unit/test_steering_mechanism.py -q` passes
- [x] `python -m ruff check . && python -m ruff format --check .` passes

## Implementation

Fix shipped in commit `78b275b3` (2026-04-14). Both bugs addressed in a single deploy.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_bridge_dispatch_contract.py tests/unit/test_health_check_recovery_finalization.py tests/unit/test_reconciler.py tests/unit/test_steering_mechanism.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No bare steering returns | `grep -n "push_steering_message" bridge/telegram_bridge.py` | All call sites in handler scope covered |
| _has_progress child check present | `grep -n "get_children" agent/agent_session_queue.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic | Problem section used present tense for already-fixed bugs | Rewritten in past tense with fix commit reference | "was never marked" / "were identified and fixed in commit 78b275b3" |
| BLOCKER | Skeptic | Solution section used future tense for already-shipped work | Rewritten in past tense; pseudocode replaced with shipped code | Team Orchestration deleted; replaced with implementation reference |
| CONCERN | Skeptic | Freshness Check timeline contradicted implementation state | Added note: fix commit 78b275b3 merged after freshness check ran | Both plan-time and post-fix state now documented |
| CONCERN | Operator | get_children() fail-unsafe: Redis failure causes false-negative | Documented accepted fail direction in Risk 3 with production signal | `no_progress` counter spike in PM sessions is the observable indicator |
| CONCERN | Operator | No production monitoring specified for either fix | Added production signal to Risk 3 | `curl localhost:8500/dashboard.json` → `session-health:recoveries:no_progress` counter |
| CONCERN | Archaeologist | AST contract test scope not explicitly verified | Added scope audit result to Solution section | All push_steering_message calls in handler scope confirmed; L1177 is import |
| CONCERN | Adversary | parent_agent_session_id assignment timeline not documented | Added Race 4 to Race Conditions section | Set synchronously at agent_session_queue.py:2122; no timing window |
| CONCERN | Simplifier | Technical Approach over-specified for completed plan | Condensed to shipped implementation with commit reference | Pseudocode blocks removed; replaced with actual shipped code |
| CONCERN | Simplifier | Team Orchestration is historical scaffolding post-build | Deleted Team Orchestration section | Replaced with "Fix shipped in commit 78b275b3" |
| CONCERN | User | No integration test for actual incident scenario end-to-end | Noted as follow-up opportunity | Out of scope for this plan; unit tests cover the predicate and AST contract |
| NIT | Operator | Deployment ordering not documented | Added note to Risk 2 | Both fixes deploy together; each safe to ship alone |
| NIT | Archaeologist | `_has_progress()` child check has no session-type guard | Documented design intent | Applies to all session types by design; type guard available if needed |
| NIT | Simplifier | Critique Results table redundant in completed plan | Retained as revision audit trail | Table now reflects post-revision resolution status |
| NIT | User | Performance baseline deferred without threshold | Added specific threshold to Risk 1 | Health check loop >5s for 1000-session deployment |
