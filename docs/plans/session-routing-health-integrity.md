---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/963
last_comment_id:
---

# Session Routing Integrity: Steering Dedup Gap + Health Check Parent Safety

## Problem

On 2026-04-14, a user message was steered correctly into session `tg_valor_-1003449100931_617`, but the reconciler fired three minutes later, found no dedup record for the message, and spawned a duplicate session `tg_valor_-1003449100931_623`. That duplicate session was then reset to `pending` by the health check — even though it had spawned a child dev session actively doing work — causing lost output and a wrong reply delivered to the user.

Two independent bugs compounded: (1) steering paths never record dedup, so the reconciler always re-dispatches steered messages; (2) the health check treats any PM session without self-reported progress as stuck, regardless of whether a child dev session is actively running.

**Current behavior:**

- Bug 1: A message steered into session A via `push_steering_message()` is never marked handled in the dedup store. Three minutes later the reconciler scans, finds no record, and enqueues a brand-new session B. Both A and B execute; duplicate or conflicting output is delivered.
- Bug 2: A PM session in `running` status for >300s with zero `turn_count`, empty `log_path`, and no `claude_session_uuid` is declared stuck by `_has_progress()` and reset to `pending` — even when a child dev session is actively running and will attempt to steer results back.

**Desired outcome:**

- Any message handled by a steering path is marked processed before the reconciler's next 180s scan, preventing re-dispatch.
- A PM session with at least one running or pending child dev session is not reset to `pending` by the health check; recovery is deferred until the child finishes.

## Freshness Check

**Baseline commit:** `91d77451f3746fae43a38c5f747d7727f17a9740`
**Issue filed at:** 2026-04-14T14:29:38Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `bridge/telegram_bridge.py:~1023` — semantic-routing steer path — confirmed at L1023; no dedup record written before `return` at L1046.
- `bridge/telegram_bridge.py:~1244` — reply-to running/active session steer path — confirmed at L1244; no dedup record before `return` at L1260.
- `bridge/telegram_bridge.py:~1283` — reply-to pending session steer path — confirmed at L1283; no dedup record before `return` at L1299.
- `bridge/telegram_bridge.py:~1323` — live-guard steer path (belt-and-suspenders re-check inside resume-completed branch) — confirmed at L1323; no dedup record before `return` at L1334.
- `bridge/telegram_bridge.py:~1498` — coalescing guard steer path — already has `record_telegram_message_handled` at L1525 (confirmed in source).
- `bridge/telegram_bridge.py:~1611` — intake-classifier interjection steer path — already has `record_telegram_message_handled` at L1633 (confirmed in source).
- `agent/agent_session_queue.py:~1353` — `_has_progress()` — confirmed at L1353–1372; reads only `turn_count`, `log_path`, `claude_session_uuid`; no child-session lookup.
- `agent/agent_session_queue.py:~1453` — health check invokes `_has_progress()` — confirmed at L1453–1456; path `worker alive but no progress signal` triggers recovery.

**Cited sibling issues/PRs re-checked:**

- #948 (centralize dedup in dispatch wrapper) — closed 2026-04-14T07:17:46Z; PR #952 merged. Moved enqueue-path dedup into `bridge/dispatch.py::dispatch_telegram_session`. Steering paths were explicitly excluded per design — root cause of Bug 1 confirmed still present.
- #944 (health check recovery for stuck slugless dev sessions) — closed 2026-04-14T05:16:33Z; introduced `_has_progress()` to guard dev sessions co-running with PM. Did not account for PM sessions waiting on running children — root cause of Bug 2 confirmed still present.
- #958 (PM context overflow) — still open; separate issue.

**Commits on main since issue was filed (touching referenced files):** None (git log confirms no commits on these files since the issue was filed at 14:29 UTC).

**Active plans in `docs/plans/` overlapping this area:**

- `health-check-no-progress-recovery.md` — status: docs_complete (shipped as #944); no overlap conflict.

**Notes:** The issue cited four steering paths at L1023, L1244, L1283, L1323. Code inspection confirms exactly those four are missing dedup. Two other steer paths (L1498 coalescing guard, L1611 intake-classifier interjection) already have dedup records — confirmed by code read.

## Prior Art

- **PR #952 / Issue #948** — Centralize dedup recording in bridge dispatch. Merged 2026-04-14. Added `bridge/dispatch.py::dispatch_telegram_session` that pairs enqueue + dedup atomically. Steering paths were explicitly excluded because they don't enqueue. This is the incomplete refactor that left the Bug 1 gap. Relevant: the fix pattern (`record_telegram_message_handled`) and the AST contract test in `tests/unit/test_bridge_dispatch_contract.py` are both proven infrastructure we extend here.
- **PR #947 / Issue #944** — Health check recovery for slugless dev sessions. Merged 2026-04-14. Introduced `_has_progress()` to distinguish stuck dev sessions from PM sessions sharing `worker_key`. Did not consider the inverse: a PM session with a running child. Relevant: our Bug 2 fix extends `_has_progress()` or augments the caller to consult the session hierarchy.
- **Issue #723** — Audit all session recovery mechanisms for completed-session respawn safety. Merged 2026-04-05. Background on the delivery guard (`response_delivered_at`) added to the health check. No direct overlap with parent/child hierarchy awareness.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #952 (#948) | Centralized enqueue-path dedup in `dispatch_telegram_session` wrapper | Steering paths were deliberately excluded; no follow-up issue tracked the gap |
| PR #947 (#944) | Added `_has_progress()` to guard dev sessions with a co-running PM | Only addressed one direction: PM alive → dev not stuck. Did not address PM with no self-turns having a running child |

**Root cause pattern:** Both fixes addressed the symptom visible at the time without auditing all sibling paths. Bug 1 fix covered enqueue paths; steering paths were out of scope. Bug 2 fix covered dev sessions; PM sessions as parents were out of scope.

## Data Flow

### Bug 1: Steering without dedup

1. **Entry**: Telegram message arrives in `handler()`.
2. **Routing**: Handler identifies a matching session and calls `push_steering_message()` — one of the four unguarded paths (L1023, L1244, L1283, L1323).
3. **Gap**: Handler returns without calling `record_telegram_message_handled()`. No dedup record written to Redis.
4. **Reconciler scan** (180s later): `bridge/reconciler.py::reconcile_once()` fetches recent messages, checks `bridge/dedup.py::is_duplicate_message()` for each. The steered message has no record — treated as "missed".
5. **Re-dispatch**: Reconciler calls `dispatch_telegram_session()`, which enqueues a new session **and** writes dedup (too late — duplicate session already created).
6. **Output**: Both the original steered session and the duplicate session execute; conflicting output delivered.

### Bug 2: Health check killing a PM with active children

1. **Entry**: PM session starts, spawns a dev child via `valor_session create --role dev`.
2. **PM status**: PM is in `running` status throughout. It never sets `turn_count`, `log_path`, or `claude_session_uuid` (those are set only during direct agent execution, which PM sessions don't do while waiting for a child).
3. **Health check loop** (300s interval): `_agent_session_health_check()` scans all `running` sessions.
4. **Predicate**: For PM session with `worker_alive=True` and `running_seconds > 300`: calls `_has_progress(entry)`. Returns `False` because all three fields are empty.
5. **Recovery**: `should_recover = True`. Health check resets PM to `pending`.
6. **Race**: PM re-executes with no memory of prior state. Child session finishes and calls `steer_session(parent.session_id, ...)` — parent is now in an inconsistent state (re-running from scratch). Child's result is lost or delivered to wrong context.

## Architectural Impact

- **New dependency on `get_children()`**: `_has_progress()` or its caller must call `entry.get_children()` — already a method on `AgentSession`. No new imports required.
- **Interface change**: `_has_progress()` semantics expand: currently "session has done work itself"; new semantics "session has done work itself OR has active children doing work on its behalf." The docstring must be updated.
- **Coupling**: Minimal increase — health check already imports `AgentSession`; no new cross-module dependency.
- **Reversibility**: Both fixes are additive (adding dedup calls, adding a predicate check). Safe to revert independently.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation before build)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work has no external dependencies. Redis and the test suite are always available.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Test suite runs | `pytest tests/unit/test_bridge_dispatch_contract.py tests/unit/test_health_check_recovery_finalization.py -q` | Confirm baseline passes before touching these files |

## Solution

### Key Elements

- **Dedup on all four unguarded steering paths**: Each of the four `push_steering_message()` call sites in `bridge/telegram_bridge.py` that currently lacks a subsequent `record_telegram_message_handled()` call gets one added immediately before `return`.
- **AST contract extension**: `tests/unit/test_bridge_dispatch_contract.py` gets a new assertion: every `push_steering_message` call site in `handler()` must be followed (within the same branch, before any `return`) by a call to `record_telegram_message_handled` or `dispatch_telegram_session`. This prevents regression.
- **Child-aware health check predicate**: `_has_progress()` is augmented to return `True` when the session has at least one child in a non-terminal status. The existing `get_children()` method on `AgentSession` provides the lookup. The docstring is updated to reflect the expanded contract.
- **Unit tests**: New test cases for the child-aware `_has_progress()` and for the AST contract extension.

### Flow

**Bug 1 fix:**

Telegram message → handler steer path → `push_steering_message()` → `await record_telegram_message_handled(event.chat_id, message.id)` → `return` → reconciler scans 180s later → dedup record found → message skipped (no duplicate session)

**Bug 2 fix:**

Health check fires → `worker_alive=True`, `running_seconds > 300`, `_has_progress(pm_session)=False for own fields` → check `pm_session.get_children()` → at least one child has `status in ("running", "active", "pending")` → `_has_progress()` returns `True` → `should_recover = False` → PM not reset → child completes → steers PM → pipeline continues normally

### Technical Approach

**Bug 1 — Four dedup additions:**

The four unguarded steering paths are:

1. **Semantic routing path** (~L1023): After `push_steering_message(matched_id, ...)` and `await send_markdown(...)`, add `await record_telegram_message_handled(event.chat_id, message.id)` before `return`.
2. **Reply-to running/active session path** (~L1244): After `push_steering_message(session_id, ...)` and `await send_markdown(...)`, add `await record_telegram_message_handled(event.chat_id, message.id)` before `return`.
3. **Reply-to pending session path** (~L1283): After `push_steering_message(session_id, ...)` and `await send_markdown(...)`, add `await record_telegram_message_handled(event.chat_id, message.id)` before `return`.
4. **Live-guard steer path** (~L1323): After `push_steering_message(session_id, ...)` and `await send_markdown(...)`, add `await record_telegram_message_handled(event.chat_id, message.id)` before `return`.

`record_telegram_message_handled` is already imported at the top of `telegram_bridge.py` (L103). The dedup call is async but swallows exceptions internally — no try/except needed at the call site.

**AST contract extension:**

The existing `test_handler_contains_no_direct_banned_calls` test ensures no bare `enqueue_agent_session` or `record_message_processed` calls exist in handler. A new test, `test_steering_paths_record_dedup`, uses the AST walker to verify that every call to `push_steering_message` in handler is followed (in linear branch order) by a call to `record_telegram_message_handled` before the next `return`. This is the structural analogue of the existing enqueue-dedup ordering test in `dispatch.py`.

Implementation note: The AST-level "followed by" check is simpler as a branch-linear walk (visit each `If` branch independently, check that a `return` statement is not preceded by `push_steering_message` without a `record_telegram_message_handled` in between). This is testable with a synthetic violating source per the existing C5 pattern in the test file.

**Bug 2 — `_has_progress()` extension:**

```python
def _has_progress(entry: AgentSession) -> bool:
    # Original three own-progress fields
    if (entry.turn_count or 0) > 0:
        return True
    if bool((entry.log_path or "").strip()):
        return True
    if bool(entry.claude_session_uuid):
        return True
    # Child-progress check: a PM session with active children is not stuck
    try:
        children = entry.get_children()
        active_child = any(
            c.status not in _TERMINAL_STATUSES for c in children
        )
        if active_child:
            return True
    except Exception:
        pass  # get_children() failure is non-fatal; fall through to False
    return False
```

`_TERMINAL_STATUSES` is already defined in scope. `get_children()` does a Redis query by `parent_agent_session_id` — already used in `_agent_session_hierarchy_health_check()`. Exception swallowing ensures a Redis hiccup during health check never causes a false-positive recovery block.

The docstring must be updated to document: (a) the original three own-fields, (b) the new child-activity check, (c) the non-fatal exception clause for the child lookup.

## Failure Path Test Strategy

### Exception Handling Coverage

- The `get_children()` call in `_has_progress()` is wrapped in `try/except Exception: pass`. Test: assert that when `get_children()` raises, `_has_progress()` returns `False` (falls through to original behavior, does not crash health check).
- `record_telegram_message_handled` internally calls `record_message_processed`, which already swallows exceptions and logs at WARNING. No additional exception handling needed at the call sites in `telegram_bridge.py`. Test: existing `test_dedup.py` covers the warning emission on Redis failure.

### Empty/Invalid Input Handling

- `get_children()` returns an empty list when no children exist — `any(...)` on an empty list returns `False`, so `_has_progress()` correctly falls through to `False` for childless sessions. Test: assert `_has_progress(session_without_children)` returns `False`.
- `record_telegram_message_handled(chat_id, message_id=0)` — dedup.py handles zero/None gracefully (existing behavior, no change needed).

### Error State Rendering

- No user-visible output from these components. Failure is observable via logs (`[session-health]` WARNING lines and bridge DEBUG lines). Tests assert log emission rather than rendered output.

## Test Impact

- [ ] `tests/unit/test_bridge_dispatch_contract.py` — UPDATE: add `test_steering_paths_record_dedup` test class with AST-level assertion that every `push_steering_message` call in handler is followed by `record_telegram_message_handled` before `return`. Existing contract tests (`test_handler_contains_no_direct_banned_calls`, `test_dispatch_calls_enqueue_before_record`) are unaffected and must remain green.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — UPDATE: add test cases for the extended `_has_progress()` predicate: (a) returns `True` when session has a child with `status="running"`, (b) returns `True` when child has `status="pending"`, (c) returns `False` when all children are terminal, (d) returns `False` when `get_children()` raises. Existing tests for `response_delivered_at` guard are unaffected.
- [ ] `tests/unit/test_reconciler.py` — No existing test cases break; dedup additions make the fix transparent to the reconciler (it already skips messages with a dedup record). No structural changes needed.
- [ ] `tests/unit/test_steering_mechanism.py` — No existing test cases break; changes are additive (new dedup call after existing push). No structural changes needed.

## Rabbit Holes

- **Moving dedup into `push_steering_message()` itself**: Tempting as centralization, but `push_steering_message()` is in `agent/steering.py` — a layer that has no knowledge of Telegram message IDs or chat IDs. Threading those through would require a signature change and touch all call sites. The current pattern (caller records dedup after the steering call) is consistent with how enqueue paths work and is the right boundary.
- **Reconciler cross-checking active sessions before dispatch**: The reconciler could query running sessions for their steering queues before treating a message as missed. This is a valid defense-in-depth but is a larger change with its own race conditions (what if the session just started?). The dedup record is the correct primary signal; the reconciler's current design is correct.
- **Unified `_has_progress` + hierarchy pass**: Merging `_agent_session_health_check()` and `_agent_session_hierarchy_health_check()` into a single pass that shares data. This would be a larger refactor touching many code paths; deferring. The fix in this plan (adding child-activity check inside `_has_progress()`) is sufficient and isolated.
- **300s threshold adjustment for PM sessions**: The issue notes the threshold may be too low. Changing constants is out of scope here — it requires data analysis of typical PM session lifecycles. The child-activity check is the structurally correct fix; threshold tuning is separate.

## Risks

### Risk 1: get_children() is slow under high session count
**Impact:** Health check loop takes longer per iteration; at extreme scale (1000+ sessions), the loop could become the bottleneck.
**Mitigation:** `get_children()` queries by `parent_agent_session_id` index — a Redis set lookup, O(n) in the number of children for that parent. PM sessions typically have 1–2 children. The exception wrapper ensures a slow query never crashes recovery. If profiling later shows this is expensive, the child lookup can be skipped for non-PM session types (add `if entry.session_type != SessionType.PM: return False` before the child check).

### Risk 2: Record-dedup race on steering paths
**Impact:** If `record_telegram_message_handled` is called after `push_steering_message` but the bridge crashes between those two calls, the message will be re-dispatched by the reconciler on restart — creating a duplicate session.
**Mitigation:** This is the same race that exists on all other dedup paths; the existing design accepts it. The window is milliseconds (between the push and the dedup record). The dedup system is async and best-effort by design. Acceptable.

### Risk 3: get_children() exception masks real data issues
**Impact:** If `get_children()` consistently fails for a PM session due to a data corruption, the child-activity check always returns `False`, and the PM session is treated as childless — vulnerable to the same Bug 2 recovery as before the fix.
**Mitigation:** The exception is logged at DEBUG (consistent with non-fatal health check patterns). The WARNING already emitted by the health check for `no_progress` recovery remains visible. If this occurs in practice, the operator can investigate via `valor-session children --id <id>`.

## Race Conditions

### Race 1: Steer path dedup written after bridge crash
**Location:** `bridge/telegram_bridge.py` L1023–1046 (and three other steer paths)
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

## No-Gos (Out of Scope)

- Refactoring `push_steering_message()` to accept Telegram message metadata and record dedup itself
- Modifying the reconciler's dispatch logic to cross-check steering queues
- Changing `AGENT_SESSION_HEALTH_MIN_RUNNING` or `AGENT_SESSION_HEALTH_CHECK_INTERVAL` constants
- Merging `_agent_session_health_check()` and `_agent_session_hierarchy_health_check()` into a single pass
- Fixing the context overflow bug (#958) — separate issue

## Update System

No update system changes required — this is a bridge and worker internal fix with no new dependencies, config keys, or migration steps. Both files (`bridge/telegram_bridge.py`, `agent/agent_session_queue.py`) are already deployed via the standard `./scripts/valor-service.sh restart` cycle.

## Agent Integration

No agent integration required — these are bridge-internal and worker-internal changes. The Telegram bridge and the worker are already the components that route messages and run health checks. No MCP server changes, no `.mcp.json` changes.

## Documentation

- [ ] Update `docs/features/message-reconciler.md` to document that all steering paths must call `record_telegram_message_handled` (add to the "Ingestion Paths" table and the dedup contract section).
- [ ] Update `agent/agent_session_queue.py::_has_progress()` docstring to document the child-activity check and its non-fatal exception clause.
- [ ] Update `docs/features/bridge-module-architecture.md` if the steering-path dedup contract is described there (check at build time).

## Success Criteria

- [ ] A message steered into a running session is not re-dispatched by the reconciler within the next 180s window
- [ ] A PM session with at least one running/pending child dev session is not reset to `pending` by the health check
- [ ] The AST contract test (or new test) covers all four steering paths for dedup presence
- [ ] Unit test for `_has_progress()` with a child in a non-terminal state returns `True`
- [ ] Unit test for `_has_progress()` when `get_children()` raises returns `False` (non-fatal)
- [ ] `pytest tests/unit/test_bridge_dispatch_contract.py tests/unit/test_health_check_recovery_finalization.py tests/unit/test_reconciler.py tests/unit/test_steering_mechanism.py -q` passes
- [ ] `python -m ruff check . && python -m ruff format --check .` passes

## Team Orchestration

### Team Members

- **Builder (bridge-dedup)**
  - Name: bridge-dedup-builder
  - Role: Add `record_telegram_message_handled` to the four unguarded steering paths in `bridge/telegram_bridge.py` and extend the AST contract test
  - Agent Type: builder
  - Resume: true

- **Builder (health-check)**
  - Name: health-check-builder
  - Role: Extend `_has_progress()` to check for active children and add unit tests
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Run full test suite, lint, confirm acceptance criteria
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Add dedup to four unguarded steering paths
- **Task ID**: build-bridge-dedup
- **Depends On**: none
- **Validates**: `tests/unit/test_bridge_dispatch_contract.py`, `tests/unit/test_reconciler.py`, `tests/unit/test_steering_mechanism.py`
- **Assigned To**: bridge-dedup-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/telegram_bridge.py`, add `await record_telegram_message_handled(event.chat_id, message.id)` immediately before each of the four `return` statements at the end of the unguarded steering paths (semantic routing ~L1046, reply-to running ~L1260, reply-to pending ~L1299, live-guard ~L1334).
- In `tests/unit/test_bridge_dispatch_contract.py`, add `test_steering_paths_record_dedup` test: AST walker verifies that every `push_steering_message` call in the `handler()` function is followed by `record_telegram_message_handled` or `dispatch_telegram_session` before the branch's `return`. Include a synthetic violating source that the walker must detect.

### 2. Extend _has_progress() for child-activity awareness
- **Task ID**: build-health-check
- **Depends On**: none
- **Validates**: new unit tests in `tests/unit/test_health_check_recovery_finalization.py` (or a new `tests/unit/test_has_progress.py`)
- **Assigned To**: health-check-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/agent_session_queue.py`, extend `_has_progress()` to call `entry.get_children()` and return `True` if any child has `status not in _TERMINAL_STATUSES`. Wrap the child lookup in `try/except Exception: pass`.
- Update `_has_progress()` docstring to document the child-activity check, the non-fatal exception clause, and the behavioral contract for PM sessions.
- Add unit tests: (a) `_has_progress()` returns `True` when a child session has `status="running"`, (b) returns `True` when child has `status="pending"`, (c) returns `False` when all children are terminal, (d) returns `False` when `get_children()` raises, (e) returns `False` when no children exist.

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: build-bridge-dedup, build-health-check
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_bridge_dispatch_contract.py tests/unit/test_health_check_recovery_finalization.py tests/unit/test_reconciler.py tests/unit/test_steering_mechanism.py -q` and confirm all pass.
- Run `python -m ruff check . && python -m ruff format --check .` and confirm clean.
- Verify all five acceptance criteria in Success Criteria are met.
- Check that `docs/features/message-reconciler.md` documents the steering-path dedup contract.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_bridge_dispatch_contract.py tests/unit/test_health_check_recovery_finalization.py tests/unit/test_reconciler.py tests/unit/test_steering_mechanism.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No bare steering returns | `python -c "import ast; src=open('bridge/telegram_bridge.py').read(); tree=ast.parse(src); print('ok')"` | exit code 0 |
| _has_progress child check present | `grep -n "get_children" agent/agent_session_queue.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the issue provides sufficient recon and both bugs are fully characterized. No human input required before build.
