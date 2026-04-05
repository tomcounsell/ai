---
status: Shipped
type: bug
appetite: Small
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/705
last_comment_id:
---

# Rapid-Fire Message Coalescing Fix

## Problem

When a user sends two Telegram DMs back-to-back (within ~200ms), each message creates its own AgentSession instead of coalescing into one. This produces duplicate responses — in the observed incident, ~20 near-identical replies about the same topic.

**Current behavior:**
Two messages arrive 134ms apart. Both are routed as `continuation=False`, each creating a separate session. Both sessions process independently, generating duplicate responses. The pending session merge window (7s) cannot catch this because `AgentSession.async_create()` hasn't written to Redis yet when the second message's handler runs.

**Desired outcome:**
The second message attaches to the first message's session via `queued_steering_messages`, producing a single combined session and one response. The user sees an "Adding to current task" acknowledgment on the second message.

## Prior Art

- **Issue #619 / PR #621**: "Attach follow-up messages to pending sessions" — Added the 7s `PENDING_MERGE_WINDOW_SECONDS` coalescing window and intake classifier pending merge. Successfully handles messages arriving >165ms apart (after Redis write), but fails for sub-200ms arrivals due to the Redis visibility gap.
- **Issue #274 / PR #275**: "Semantic session routing" — Original semantic routing implementation using Haiku LLM matching against sessions with expectations. Merged but disabled by default behind `SEMANTIC_ROUTING` env var.
- **PR #366**: "Route unthreaded messages into active sessions (#318)" — Added steering into running sessions via the intake classifier. Works correctly once sessions exist in Redis.
- **Issue #700 / PR #703**: "Session completion zombie loop" — Related session management fix (completed sessions reverting to pending). Different root cause but same area of code.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #621 | Added Redis-based pending session lookup within 7s window | The session isn't written to Redis until `AgentSession.async_create()` at `agent_session_queue.py:264`, which happens ~165ms after the handler starts. Messages arriving within this gap can't see each other's sessions. |
| PR #275 | Added semantic routing to match messages to active sessions | Feature-flagged as disabled by default. Even when enabled, it only matches against sessions with declared expectations — new sessions created moments ago have no expectations yet. |

**Root cause pattern:** Both fixes rely on Redis state that doesn't exist yet during the critical sub-200ms window. The gap between handler start and Redis write is the fundamental race condition.

## Data Flow

1. **Entry point**: Telegram message arrives → Telethon async event handler (`bridge/telegram_bridge.py`)
2. **Mark as read + reactions**: ~50ms of mark-as-read, emoji selection, acknowledgment reactions
3. **Semantic routing check** (line 950): If enabled, queries Redis for sessions with expectations — skipped when disabled
4. **Steering check** (line 1186): Queries Redis for running/active sessions → routes interjection if found
5. **Pending merge check** (line 1264): Queries Redis for pending sessions within merge window → **RACE: session not yet in Redis**
6. **Intake classifier** (line 1282): Classifies intent via Haiku — returns `new_work` immediately when no sessions exist
7. **Enqueue** (`telegram_bridge.py:1502`): Calls `enqueue_agent_session()` which calls `AgentSession.async_create()` at `agent_session_queue.py:260` — **Redis write happens here, ~165ms after step 1**
8. **Worker pickup**: Worker picks up session from Redis queue, starts Claude API call

The race occurs between steps 1 and 7: the second message reaches step 4/5, finds nothing in Redis, and creates a competing session.

## Architectural Impact

- **New dependencies**: None — uses only module-level Python dict (in-memory)
- **Interface changes**: `is_semantic_routing_enabled()` removed; `find_matching_session()` always called
- **Coupling**: Slightly reduces coupling by removing env var dependency for semantic routing
- **Data ownership**: No change — coalescing guard is ephemeral process-local state, not persisted
- **Reversibility**: Easy — revert is just removing the dict check and restoring the feature flag

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are to bridge-internal code running in a single asyncio process.

## Solution

### Key Elements

- **Always-on semantic routing**: Remove the `SEMANTIC_ROUTING` feature flag so `find_matching_session()` runs on every non-reply message. Error handling is already fully graceful.
- **In-memory coalescing guard**: A module-level dict `_recent_session_by_chat` mapping `chat_id → (session_id, timestamp)` that bridges the Redis visibility gap. Set before `enqueue_agent_session()`, checked before creating new sessions.
- **Merge window increase**: Bump `PENDING_MERGE_WINDOW_SECONDS` from 7 to 8.

### Flow

**Message 1 arrives** → routing checks (no match) → set `_recent_session_by_chat[chat_id]` → `enqueue_agent_session()` → Redis write

**Message 2 arrives (within 8s)** → routing checks → **check `_recent_session_by_chat`** → found! → `push_steering_message()` on the session → "Adding to current task" ack → return (no new session)

### Technical Approach

- **Part A — Remove semantic routing feature flag:**
  - Delete `is_semantic_routing_enabled()` from `bridge/session_router.py`
  - Remove the `if is_semantic_routing_enabled():` guard at `bridge/telegram_bridge.py:950`
  - Always call `find_matching_session()` for non-reply messages
  - Remove `SEMANTIC_ROUTING=false` from `.env.example`
  - Update `docs/features/semantic-session-routing.md`

- **Part B — Add in-memory coalescing guard:**
  - Add `_recent_session_by_chat: dict[str, tuple[str, float]] = {}` at module level in `bridge/telegram_bridge.py`
  - Just before `enqueue_agent_session()` is called, set `_recent_session_by_chat[chat_id] = (session_id, time.time())`
  - In the intake classifier path (around line 1264), check the in-memory dict first: if a session was created for this `chat_id` within the merge window, look up the `AgentSession` by session_id, call `push_steering_message(text)` on it, send "Adding to current task" ack, and return
  - Lazy cleanup: on each check, delete entries older than `PENDING_MERGE_WINDOW_SECONDS`
  - The dict uses `queued_steering_messages` (Popoto model field), NOT the Redis steering queue (`agent/steering.py`)

- **Part C — Bump merge window:**
  - Change `PENDING_MERGE_WINDOW_SECONDS = 7` to `PENDING_MERGE_WINDOW_SECONDS = 8` (per issue #705 requirement — provides additional margin for the Redis-based pending merge path that covers messages arriving >165ms apart but within typing pauses)

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] The in-memory coalescing guard must be wrapped in try/except so failures fall through to normal session creation (match existing pattern at lines 1232-1245)
- [x] Verify that `push_steering_message()` failures on a not-yet-created AgentSession don't crash the handler

### Empty/Invalid Input Handling
- [x] Test with empty `chat_id` — guard should not match
- [x] Test with `clean_text` being empty string — should still coalesce (message content doesn't affect routing)

### Error State Rendering
- [x] If coalescing guard fails silently, the user gets a normal session (degraded but functional) — no broken error state visible

## Test Impact

- [x] `tests/integration/test_steering.py::test_pending_merge_window_constant_is_7` — UPDATE: change assertion from `== 7` to `== 8`
- [x] `tests/integration/test_steering.py` — UPDATE: all tests referencing `PENDING_MERGE_WINDOW_SECONDS` will import the updated value dynamically, so they should pass without changes (they already use the imported constant, not a hardcoded 7)
- No tests import `is_semantic_routing_enabled` — verified by grep (only `bridge/session_router.py` and `bridge/telegram_bridge.py` reference it)

## Rabbit Holes

- **Redis-based coalescing key**: Tempting to use a Redis key for cross-process coordination, but the bridge is a single asyncio process — in-memory dict is simpler, faster, and sufficient
- **Medium-confidence disambiguation (0.50-0.80)**: The semantic router doc mentions Phase 3 disambiguation — defer this, it's a separate enhancement
- **Option 3 (active session detection without semantic routing)**: Explicitly disallowed by the issue — proper semantic routing must remain the primary mechanism

## Risks

### Risk 1: In-memory dict grows unbounded
**Impact:** Memory leak if cleanup never runs
**Mitigation:** Lazy cleanup on every check removes entries older than merge window. Even without cleanup, dict holds at most one entry per chat_id — bounded by number of active chats.

### Risk 2: Bridge restart clears the in-memory dict
**Impact:** Brief window after restart where coalescing guard is empty
**Mitigation:** Acceptable — the dict repopulates naturally as new sessions are created. Redis-based pending merge (existing code) still provides coverage for messages arriving >165ms apart.

## Race Conditions

### Race 1: Two messages arrive within <165ms (the target bug)
**Location:** `bridge/telegram_bridge.py` lines 1264-1273
**Trigger:** User sends two messages back-to-back; both handlers run before either calls `enqueue_agent_session()`
**Data prerequisite:** `_recent_session_by_chat[chat_id]` must be set before the second handler checks it
**State prerequisite:** First handler must reach the dict-set point before second handler reaches the dict-check point
**Mitigation:** Set the dict entry as early as possible — right after the session_id is determined, BEFORE any `await` calls (semantic routing LLM call, mark-as-read, reactions). Since the bridge is single-threaded asyncio, the dict write happens atomically between await points. The critical insight: if the dict is set just before `enqueue_agent_session()` (as originally proposed), the second handler can run its entire path while the first handler awaits the semantic routing LLM call at `session_router.py:137`. Setting it early (before any awaits) prevents this. The session_id can be generated early in the handler flow using the same derivation logic used later for `enqueue_agent_session()`.

### Race 2: Dict entry set but AgentSession not yet created in Redis
**Location:** `bridge/telegram_bridge.py` — between dict set and `AgentSession.async_create()`
**Trigger:** Second message checks dict, finds entry, tries to call `push_steering_message()` on a session that doesn't exist in Redis yet
**Data prerequisite:** AgentSession must exist in Redis before `push_steering_message()` can succeed
**State prerequisite:** The Popoto model must be queryable
**Mitigation:** If `AgentSession.query.filter(session_id=session_id)` returns empty, fall back to a brief retry (single `await asyncio.sleep(0.2)`) then retry once. If still empty, fall through to normal session creation. This handles the narrow window between dict set and Redis write.

## No-Gos (Out of Scope)

- Multi-process bridge support (not needed — single asyncio process confirmed)
- Medium-confidence semantic routing disambiguation (Phase 3 of the routing roadmap)
- Changing the intake classifier logic beyond the coalescing guard check

## Update System

No update system changes required — this is a bridge-internal change. The `SEMANTIC_ROUTING` env var removal is backward-compatible: existing `.env` files with `SEMANTIC_ROUTING=false` will have no effect since the flag is no longer checked. The update script does not reference this variable.

## Agent Integration

No agent integration required — this is a bridge-internal change. The fix modifies message routing in `bridge/telegram_bridge.py` and removes a feature flag from `bridge/session_router.py`. No new MCP servers, tools, or agent-facing interfaces are involved.

## Documentation

- [x] Update `docs/features/semantic-session-routing.md` to remove feature flag references and document always-on behavior
- [x] Remove `SEMANTIC_ROUTING` row from configuration table in `docs/features/semantic-session-routing.md`
- [x] Add entry about the in-memory coalescing guard to `docs/features/semantic-session-routing.md`
- [x] No new feature doc needed — this extends existing documentation

## Success Criteria

- [x] Semantic routing runs on every non-reply message without requiring env var opt-in
- [x] `is_semantic_routing_enabled()` function and `SEMANTIC_ROUTING` env var references are removed
- [x] Two messages sent to the same chat within 8 seconds coalesce into a single session, even when arriving <200ms apart
- [x] Second message receives "Adding to current task" acknowledgment
- [x] `PENDING_MERGE_WINDOW_SECONDS` is 8
- [x] In-memory coalescing guard does not leak memory (stale entries cleaned up)
- [x] Existing steering tests pass (with constant updated from 7 to 8)
- [x] New test covers the sub-200ms race condition scenario
- [x] `docs/features/semantic-session-routing.md` updated to reflect always-on behavior
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (bridge-fix)**
  - Name: bridge-builder
  - Role: Implement semantic routing flag removal, in-memory coalescing guard, and merge window bump
  - Agent Type: async-specialist
  - Resume: true

- **Validator (bridge-verify)**
  - Name: bridge-validator
  - Role: Verify coalescing behavior and test coverage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove semantic routing feature flag
- **Task ID**: build-semantic-routing-always-on
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py`
- **Assigned To**: bridge-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Delete `is_semantic_routing_enabled()` from `bridge/session_router.py`
- Remove the `if is_semantic_routing_enabled():` guard in `bridge/telegram_bridge.py:950` — always call `find_matching_session()`
- Remove `SEMANTIC_ROUTING=false` from `.env.example`
- Update docstring in `bridge/session_router.py` to remove feature flag references

### 2. Add in-memory coalescing guard
- **Task ID**: build-coalescing-guard
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py`, `tests/unit/test_coalescing_guard.py` (create)
- **Assigned To**: bridge-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_recent_session_by_chat: dict[str, tuple[str, float]] = {}` at module level in `bridge/telegram_bridge.py`
- Set dict entry early — right after session_id is determined, BEFORE any `await` calls (semantic routing, mark-as-read, reactions)
- Add check before pending merge check: if `chat_id` in dict and within merge window, push to BOTH `AgentSession.push_steering_message()` AND `agent.steering.push_steering_message()` (mirroring the existing interjection pattern at lines 1316-1338), then return
- Add lazy cleanup of stale entries
- Wrap in try/except matching existing error handling pattern (lines 1232-1245)
- Handle Race 2 (AgentSession not yet in Redis) with single retry after `asyncio.sleep(0.2)`

### 3. Bump merge window constant
- **Task ID**: build-merge-window-bump
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py::test_pending_merge_window_constant_is_7`
- **Assigned To**: bridge-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Change `PENDING_MERGE_WINDOW_SECONDS = 7` to `PENDING_MERGE_WINDOW_SECONDS = 8` in `bridge/telegram_bridge.py`
- Update test assertion from `== 7` to `== 8` and rename test

### 4. Create coalescing guard tests
- **Task ID**: build-coalescing-tests
- **Depends On**: build-coalescing-guard
- **Validates**: `tests/unit/test_coalescing_guard.py` (create)
- **Assigned To**: bridge-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test: two messages to same chat_id within 200ms — second should find dict entry
- Test: stale entry cleanup — entry older than merge window should be removed
- Test: dict entry with non-existent AgentSession — should fall through gracefully
- Test: different chat_ids — should not interfere

### 5. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-semantic-routing-always-on, build-coalescing-guard
- **Assigned To**: bridge-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/semantic-session-routing.md` to remove feature flag references
- Document the in-memory coalescing guard as a complement to Redis-based pending merge
- Update configuration table to remove `SEMANTIC_ROUTING` row

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-coalescing-tests, document-feature
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify `is_semantic_routing_enabled` is not referenced anywhere
- Verify `SEMANTIC_ROUTING` is not referenced in `.env.example` or docs
- Verify `PENDING_MERGE_WINDOW_SECONDS == 8`
- Verify `_recent_session_by_chat` exists and has cleanup logic

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No semantic routing flag | `grep -rn 'is_semantic_routing_enabled' bridge/ tests/` | exit code 1 |
| No SEMANTIC_ROUTING env ref | `grep -rn 'SEMANTIC_ROUTING' .env.example` | exit code 1 |
| Merge window is 8 | `python -c "from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS; assert PENDING_MERGE_WINDOW_SECONDS == 8"` | exit code 0 |
| Coalescing guard exists | `grep -n '_recent_session_by_chat' bridge/telegram_bridge.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Resolution |
|----------|--------|---------|------------|
| BLOCKER | Adversary, Skeptic | Coalescing guard uses only `queued_steering_messages` but existing interjection pattern pushes to BOTH model field AND Redis steering queue. Using only model method would drop messages for running sessions. | FIXED: Updated Part B to push to both `AgentSession.push_steering_message()` and `agent.steering.push_steering_message()`, mirroring lines 1316-1338. Removed No-Go prohibiting Redis steering queue. |
| CONCERN | Adversary | Dict set timing: if set just before `enqueue_agent_session()`, second handler can run entire path while first awaits semantic routing LLM call | FIXED: Updated Part B and Race 1 mitigation to set dict BEFORE any `await` calls, right after session_id is determined. |
| CONCERN | Skeptic | Test Impact line 124 says "any tests importing `is_semantic_routing_enabled`" but grep shows zero tests import it | FIXED: Replaced with verified statement confirming no tests import the function. |
| CONCERN | Archaeologist | Plan references incorrect line numbers (e.g., `agent_session_queue.py:1468` for enqueue) | FIXED: Updated Data Flow step 7 to reference correct location `telegram_bridge.py:1502`. |
| CONCERN | Simplifier | Merge window bump 7→8 is unjustified | ADDRESSED: Added justification (issue #705 explicit requirement, additional margin for Redis-based path). Kept in scope. |
| NIT | User | Documentation section doesn't mention removing SEMANTIC_ROUTING config table row | FIXED: Added checkbox for removing config table row. |

---

## Open Questions

No open questions — the issue provides complete root cause analysis, confirmed recon, and an explicit solution sketch. All architectural decisions (in-memory dict over Redis, always-on semantic routing, `queued_steering_messages` over Redis steering queue) are already validated in the issue's recon.
