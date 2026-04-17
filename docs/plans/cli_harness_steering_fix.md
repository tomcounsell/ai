---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-17
tracking: https://github.com/tomcounsell/ai/issues/1018
last_comment_id:
---

# CLI-Harness Steering Fix: Silent Drop Elimination

## Problem

A PM session calling `scripts/steer_child.py` against a running CLI-harness Dev session gets a success exit code and a confirmation message, but the steering message is silently dropped — the Dev session never sees it.

**Current behavior:**
`steer_child.py` → `agent.steering.push_steering_message()` → Redis list `steering:{session_id}`. The only mid-execution consumer of that list (`_handle_steering()` in `agent/health_check.py`) requires an active SDK client. CLI-harness Dev sessions never register an SDK client, so `_handle_steering()` re-pushes the message every tool call until session end, at which point `agent_session_queue.py` drops the accumulated messages with a WARNING log. The PM reports success; the Dev session received nothing.

**Desired outcome:**
`steer_child.py` against a running CLI-harness child delivers the message at the next turn boundary via `AgentSession.queued_steering_messages`, or exits non-zero with a clear error. Silent drop is eliminated.

## Freshness Check

**Baseline commit:** `177db8cf3dd8ec89a326c9ed391b2e1b4ed0fbe5`
**Issue filed at:** 2026-04-17T02:59:54Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/steering.py:37` — `push_steering_message()` writes to Redis list `steering:{session_id}` — still holds
- `agent/health_check.py:409` — `_handle_steering()` requires active SDK client — still holds; confirmed `get_active_client()` returns `None` for CLI sessions, triggering silent re-push
- `agent/hooks/post_tool_use.py:66` — `watchdog_hook` called on PostToolUse; calls `_handle_steering()` internally — still holds (registration path confirmed)
- `agent/agent_session_queue.py:4325` — WARNING log for dropped messages — still holds (issue cited 4318; drifted to 4325)
- `agent/agent_session_queue.py:4067` — turn-boundary `pop_steering_messages()` consumer — still holds; confirmed harness-agnostic
- `models/agent_session.py:1430` — `AgentSession.push_steering_message()` model method — still holds
- `docs/features/pm-dev-session-architecture.md:386` — inaccurate claim "watchdog hook picks up on next tool call" without SDK caveat — still holds; not yet fixed

**Cited sibling issues/PRs re-checked:**
- #912 — CLI harness migration — merged 2026-04-13; confirmed all Dev sessions now run CLI harness by default, making this bug universal
- PR #1016 (`session/pm-orch-audit-hotfixes`) — open, touches `docs/features/pm-dev-session-architecture.md`; must coordinate to avoid conflict on the same doc

**Commits on main since issue was filed (touching referenced files):** None

**Active plans in `docs/plans/` overlapping this area:**
- `cli_harness_full_migration.md` — status: Shipped; no conflict
- No other active plans touching `scripts/steer_child.py` or `agent/steering.py`

**Notes:** Line 4318 cited in issue is now 4325 (minor drift). PR #1016 is open and touches `pm-dev-session-architecture.md` — builder should check PR #1016 status before editing that doc, or coordinate edits.

## Prior Art

- **#912** (CLI harness migration, merged 2026-04-13): Migrated all Dev sessions to `claude -p` subprocess. This migration is what made the SDK-harness-only steering path universally broken — previously, only some sessions used CLI harness.
- **#780** (AgentSession harness abstraction, merged 2026-04-11): Original abstraction that introduced the CLI harness path. Steering was not updated to account for the new harness type.
- **#749** (externalized queued_steering_messages): Introduced `AgentSession.queued_steering_messages` as the turn-boundary inbox. This is the correct delivery mechanism for CLI-harness sessions — it already works; `steer_child.py` simply doesn't write to it.
- **#496** (original parent-child steering script): Created `scripts/steer_child.py`. Wrote to Redis steering list, which was correct for SDK-harness sessions at the time.

No prior fix was applied to this specific silent-drop bug. The sequence of migrations (#780 → #912) progressively broke the SDK-harness assumption without updating `steer_child.py`.

## Research

No relevant external findings — this is a purely internal change involving no external libraries, APIs, or ecosystem patterns. The fix involves calling an existing internal method (`steer_session()`) from `steer_child.py`.

## Data Flow

**Current (broken) flow for CLI-harness Dev session:**

1. **PM calls `steer_child.py`** — validates parent-child relationship, calls `agent.steering.push_steering_message(session_id, text, sender="pm")`
2. **`push_steering_message()` writes to Redis list** — `steering:{session_id}` (RPUSH)
3. **PostToolUse hook fires** on Dev session's next tool call → `watchdog_hook()` → `_handle_steering(session_id)`
4. **`_handle_steering()` pops the message**, calls `get_active_client(session_id)` → returns `None` for CLI-harness
5. **`_repush_messages()` called** — message goes back onto Redis list; logged as warning
6. **Steps 3-5 repeat** on every subsequent tool call until session end
7. **Session completes** → `pop_all_steering_messages()` drains leftover messages → WARNING log: "N unconsumed steering message(s) dropped"
8. **PM sees success exit code** — no indication steering failed

**Fixed flow for CLI-harness Dev session:**

1. **PM calls `steer_child.py`** — validates parent-child relationship
2. **`steer_child.py` calls `steer_session(session_id, text)`** (from `agent.agent_session_queue`) — writes to `AgentSession.queued_steering_messages` model field
3. **Turn-boundary check in `_execute_agent_session()`** — pops `queued_steering_messages` before starting next turn, uses first message as turn input
4. **Dev session receives steering message** as user input at next turn boundary

**Fix for `_handle_steering()` no-client branch:**

When `get_active_client()` returns `None`, instead of re-pushing to the Redis list (which will never be consumed), fall back to writing to `AgentSession.queued_steering_messages`. This catches the case where a message arrives via the Redis list path (e.g., from the bridge's Telegram reply-thread path) for a CLI-harness session.

## Architectural Impact

- **No new dependencies**: Both `steer_session()` and `AgentSession.push_steering_message()` already exist
- **Interface unchanged**: `steer_child.py` CLI flags remain identical; behavior improves silently
- **Coupling reduced**: `steer_child.py` no longer relies on the watchdog hook's SDK-client assumption
- **Data ownership clarified**: `steer_child.py` (PM→child path) writes to the model-field inbox (turn-boundary), not the Redis-list inbox (SDK interrupt). This mirrors how `valor-session steer` already works.
- **Reversibility**: Trivially reversible — swap `steer_session()` call back to `push_steering_message()`

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`steer_child.py` dual-write**: Call `steer_session()` (writes to `AgentSession.queued_steering_messages`) instead of `push_steering_message()` (writes to Redis list). This gives turn-boundary delivery to CLI-harness sessions, which is the correct and reliable path.
- **`_handle_steering()` fallback**: When `get_active_client()` returns `None` and the session is CLI-harness, write to `AgentSession.queued_steering_messages` instead of re-pushing to the Redis list. This prevents the infinite re-push loop for messages that arrive via the bridge's Telegram-reply path.
- **`steer_child.py` harness detection**: Read `child.session_harness` (or `DEV_SESSION_HARNESS` equivalent) to emit a clear log message indicating which delivery path was used. No silent ambiguity.
- **`docs/features/pm-dev-session-architecture.md:386`**: Update the claim to accurately describe turn-boundary delivery for CLI-harness sessions.
- **Steering doc scopes**: Add a header note to each of the three steering docs clarifying which mechanism it describes, preventing future confusion. Do NOT merge the docs — they describe genuinely distinct subsystems.
- **Test**: Add one integration test for `steer_child.py` → CLI-harness delivery path, using a real `AgentSession` (no mock of `get_active_client` or `push_steering_message`).

### Flow

PM calls `steer_child.py` → validates session → calls `steer_session()` → writes to `AgentSession.queued_steering_messages` → worker pops at next turn boundary → Dev session receives as turn input

### Technical Approach

- In `scripts/steer_child.py::_steer_child()`: Replace the call to `push_steering_message()` with `steer_session(session_id=session_id, message=message)` from `agent.agent_session_queue`. This is a one-line change in the import and one-line change in the call. The abort path is a special case: `steer_session()` does not support `is_abort`; for abort messages, continue calling `push_steering_message()` with `is_abort=True` (abort signals are handled differently — they trigger immediate SIGTERM-equivalent via the watchdog hook, not turn-boundary injection).
- In `agent/health_check.py::_handle_steering()`: In the `else` branch (no active client), attempt to write messages to `AgentSession.queued_steering_messages` via the model. If the session can't be found in the model, fall back to the current re-push behavior with a louder warning.
- In `docs/features/pm-dev-session-architecture.md`: Update line 386 to say "The child's `queued_steering_messages` inbox is written by `steer_child.py`. The worker injects it at the next turn boundary. For SDK-harness sessions only: the watchdog hook also pops from the Redis steering queue mid-turn."
- In each steering doc (`mid-session-steering.md`, `session-steering.md`, `steering-queue.md`): Add a one-line scope declaration at the top of the file. Do not merge the files.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_handle_steering()` `else` branch — new model write can fail (session not found, Redis down). Must log warning and fall back to re-push, never raise. Existing `except Exception as e` block covers this; add assertion in test that re-push fallback triggers when model write fails.
- [ ] `steer_child.py` — `steer_session()` returns a dict with `success: bool`. If `success=False`, `steer_child.py` must exit non-zero with the error message. Add test for this path.

### Empty/Invalid Input Handling
- [ ] `steer_session()` already rejects empty messages (returns `success: False`). `steer_child.py` already validates non-empty. No change needed; existing behavior preserved.

### Error State Rendering
- [ ] If `steer_session()` returns `success: False`, `steer_child.py` must print the error to stderr and exit 1 — not print the success message. Verify this in the test.

## Test Impact

- [ ] `tests/integration/test_steering.py::TestWatchdogSteering::test_watchdog_handles_missing_client` — UPDATE: current test asserts message is re-pushed to Redis list; after fix, test must assert message is written to `AgentSession.queued_steering_messages` when model lookup succeeds, OR re-pushed when model lookup fails (session not in DB). Split into two test cases.
- [ ] `tests/integration/test_steering.py::TestWatchdogSteering::test_watchdog_repushes_on_injection_failure` — UPDATE: re-push is now fallback, not primary path for missing-client case. Rename and narrow scope to "model write fails → re-push to Redis".

## Rabbit Holes

- **Merging the three steering doc files**: They document distinct subsystems (Telegram bridge steering, turn-boundary inbox, mid-session SDK interrupts). Merging would create a blob that's harder to navigate. Clarify scope with a header note instead.
- **Refactoring `agent/agent_session_queue.py`**: Explicitly out of scope (5031 LOC monster). Touch only `steer_child.py`, `agent/health_check.py`, and docs.
- **Adding `is_abort` support to `steer_session()`**: Abort semantics differ (SIGTERM-equivalent, immediate effect). This is a separate concern. For this fix, abort messages continue to use the Redis list path, which the watchdog hook handles correctly even for CLI-harness (it returns an `additionalContext` injection, not an SDK interrupt).
- **End-to-end test with a real running `claude -p` subprocess**: Too slow and flaky for CI. The unit-level integration test (real `AgentSession`, no subprocess) is sufficient to verify the delivery path.

## Risks

### Risk 1: PR #1016 conflict on `pm-dev-session-architecture.md`
**Impact:** Merge conflict if #1016 merges before or during this work; both touch the same doc.
**Mitigation:** Check #1016 status at build time. If still open, coordinate edit with the hotfix branch or wait for #1016 to merge first.

### Risk 2: Abort path regression
**Impact:** Abort messages (`--abort` flag) use `push_steering_message(is_abort=True)`. If the builder accidentally changes the abort path to use `steer_session()` (which doesn't support `is_abort`), abort steering breaks silently.
**Mitigation:** Plan explicitly separates the two paths. Test must include an abort-path test asserting that abort messages still reach the Redis list with `is_abort=True`.

### Risk 3: Turn-boundary delay vs. intra-turn injection
**Impact:** A PM steering message will be delivered at the Dev session's next turn boundary, not mid-tool-call. For time-sensitive corrections, the Dev session might take one more turn before seeing the message.
**Mitigation:** This is acceptable. The alternative (true intra-turn injection) requires an SDK client, which CLI-harness doesn't have. Turn-boundary delivery is already the mechanism for `valor-session steer`. Document the latency in the doc update.

## Race Conditions

### Race 1: `steer_child.py` validation window
**Location:** `scripts/steer_child.py:_steer_child()`
**Trigger:** PM validates `child.status == "running"`, then writes to `queued_steering_messages`; child completes in the window between validation and write.
**Data prerequisite:** Child session must be running when the write occurs.
**State prerequisite:** `queued_steering_messages` must be consumed before the session's final cleanup.
**Mitigation:** `steer_session()` already checks `_TERMINAL_STATUSES` before writing and returns `success: False` if terminal. If the session completes between `steer_child.py`'s validation check and `steer_session()`'s write, `steer_session()` returns an error and `steer_child.py` exits non-zero. The message is not lost; the PM learns the session already finished.

## No-Gos (Out of Scope)

- Refactor of `agent/agent_session_queue.py` — separate issue
- Naming drift (`session_type` vs `role` vs `session_mode`) — separate issue
- Investigation of `agent/hooks/subagent_stop.py` orphan status — separate investigation
- Adding `is_abort` support to `steer_session()` — separate concern
- Merging the three steering doc files into one
- End-to-end test with a real running `claude -p` subprocess

## Update System

No update system changes required — this feature is purely internal. No new dependencies, no config changes, no migration steps. The fix is contained to `scripts/steer_child.py`, `agent/health_check.py`, and doc files.

## Agent Integration

No agent integration required — `steer_child.py` is called by the PM session as a bash script, which already works. The fix changes which internal Python function is called, not how the PM invokes the script. No MCP server changes, no `.mcp.json` changes.

## Documentation

- [x] Update `docs/features/pm-dev-session-architecture.md` line 386 to accurately describe CLI-harness turn-boundary delivery and SDK-harness mid-turn delivery as distinct mechanisms
- [x] Add scope declaration header to `docs/features/mid-session-steering.md` (Telegram bridge reply-thread path)
- [x] Add scope declaration header to `docs/features/session-steering.md` (turn-boundary inbox via `queued_steering_messages`)
- [x] Add scope declaration header to `docs/features/steering-queue.md` (Redis list, bridge coalescing, SDK mid-turn injection)
- [x] Update inline docstring for `_handle_steering()` to describe the fallback-to-model-field behavior

## Success Criteria

- [ ] `scripts/steer_child.py --session-id <cli-harness-child> --message "..."` results in the message appearing in `AgentSession.queued_steering_messages` of the target session
- [ ] `scripts/steer_child.py` against a terminal-status session exits non-zero with a clear error message
- [ ] `_handle_steering()` no-client branch writes to `AgentSession.queued_steering_messages` instead of re-pushing to Redis list (when session found in model)
- [ ] `docs/features/pm-dev-session-architecture.md:386` accurately describes the turn-boundary delivery mechanism for CLI-harness
- [ ] All three steering doc files have scope declarations at the top
- [ ] At least one integration test in `tests/integration/test_steering.py` exercises `steer_child.py` with a real `AgentSession` (no mock of `get_active_client` or `push_steering_message`)
- [ ] Abort path test: `steer_child.py --abort` still writes to Redis list with `is_abort=True`
- [ ] `python -m ruff format .` clean
- [ ] `python -m ruff check .` clean
- [ ] Tests pass: `pytest tests/integration/test_steering.py -x -q`

## Team Orchestration

### Team Members

- **Builder (steering-fix)**
  - Name: steering-builder
  - Role: Implement all code changes in `steer_child.py` and `agent/health_check.py`, update docs
  - Agent Type: builder
  - Resume: true

- **Validator (steering-fix)**
  - Name: steering-validator
  - Role: Run tests, verify success criteria, confirm no regressions
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

#### 1. Fix `steer_child.py` to use turn-boundary delivery
- **Task ID**: build-steer-child
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py`
- **Informed By**: Freshness Check (line 4325 drift), Risk 2 (abort path must stay on Redis list)
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/steer_child.py::_steer_child()`: import `steer_session` from `agent.agent_session_queue`; replace the `push_steering_message()` call with `steer_session(session_id=child.session_id, message=message)` for non-abort messages. Check the returned dict — if `success=False`, print error to stderr and return 1.
- For abort messages (`abort=True`): keep the existing `push_steering_message(..., is_abort=True)` call — abort is Redis-list path only.
- Update the success print: include which delivery path was used ("Steered {session_id} via turn-boundary inbox: {preview}")

#### 2. Fix `_handle_steering()` no-client fallback
- **Task ID**: build-health-check
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py::TestWatchdogSteering`
- **Informed By**: Data Flow section (fix for `_handle_steering()` branch)
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/health_check.py::_handle_steering()`, in the `else` branch (no active client): attempt to look up the `AgentSession` by `session_id` and call `agent_session.push_steering_message(msg["text"])` for each non-abort message. Wrap in try/except — if model lookup fails, fall back to `_repush_messages()` with a WARNING log.
- Update the logger.warning message to be more actionable: "No active client for {session_id} (CLI harness?); writing to turn-boundary inbox instead"

#### 3. Update affected tests
- **Task ID**: build-tests
- **Depends On**: build-steer-child, build-health-check
- **Validates**: `tests/integration/test_steering.py`
- **Assigned To**: steering-builder
- **Agent Type**: test-writer
- **Parallel**: false
- Update `test_watchdog_handles_missing_client`: assert message lands in `AgentSession.queued_steering_messages` (model field), not Redis re-push, when session is found in DB.
- Add new test: missing-client AND session not in DB → message is re-pushed to Redis list (existing fallback).
- Update `test_watchdog_repushes_on_injection_failure`: rename to `test_watchdog_fallback_to_repush_when_model_write_fails`; mock model write to raise, assert Redis re-push.
- Add new integration test `test_steer_child_cli_harness_delivery`: create a real `AgentSession` (dev, running, CLI harness), call `_steer_child()`, assert `session.queued_steering_messages` contains the message.
- Add abort-path test: call `_steer_child(..., abort=True)`, assert Redis list contains message with `is_abort=True`, `AgentSession.queued_steering_messages` is empty.

#### 4. Update documentation
- **Task ID**: document-steering
- **Depends On**: build-steer-child, build-health-check
- **Assigned To**: steering-builder
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/pm-dev-session-architecture.md` line 386 area: describe turn-boundary delivery for CLI-harness, SDK mid-turn delivery for SDK-harness.
- Add one-line scope declaration at top of `docs/features/mid-session-steering.md`: "**Scope:** Telegram bridge reply-thread steering via Redis list (`steering:{session_id}`). For PM→child steering, see `session-steering.md`."
- Add one-line scope declaration at top of `docs/features/session-steering.md`: "**Scope:** Turn-boundary inbox (`AgentSession.queued_steering_messages`) consumed by the worker executor. Used by `valor-session steer` and `scripts/steer_child.py`."
- Add one-line scope declaration at top of `docs/features/steering-queue.md`: "**Scope:** Redis list steering queue design and bridge coalescing. SDK-harness mid-turn injection (legacy/secondary path). For PM→child steering, see `session-steering.md`."
- Update `_handle_steering()` docstring to describe CLI-harness fallback behavior.

#### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-steering
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_steering.py -x -q`
- Run `python -m ruff check .`
- Run `python -m ruff format --check .`
- Verify all success criteria in the Success Criteria section are met
- Confirm PR #1016 coordination: if open, check for edit conflicts on `pm-dev-session-architecture.md`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Steering tests pass | `pytest tests/integration/test_steering.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `steer_child.py` imports `steer_session` | `grep -n "steer_session" scripts/steer_child.py` | output contains steer_session |
| `_handle_steering` fallback references model | `grep -n "queued_steering_messages\|push_steering_message" agent/health_check.py` | output contains queued_steering_messages |
| Doc scope headers present | `grep -c "Scope:" docs/features/mid-session-steering.md docs/features/session-steering.md docs/features/steering-queue.md` | output contains 3 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the architectural question (turn-boundary vs. intra-turn delivery) is resolved: turn-boundary via `queued_steering_messages` is the correct and implementable path for CLI-harness sessions. Intra-turn injection requires an SDK client that CLI-harness sessions never have.
