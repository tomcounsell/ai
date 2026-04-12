---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-04-12
tracking: https://github.com/tomcounsell/ai/issues/919
plan: https://github.com/tomcounsell/ai/blob/main/docs/plans/reply-to-routing-gaps.md
last_comment_id:
---

# Reply-to Routing Gaps: Cache Non-Determinism and Completed Session Bypass

## Problem

Two related bugs in the reply-to session routing path cause Telegram reply messages to create new, disconnected agent sessions instead of continuing the correct one. Agents end up working in parallel on the same task with split context — producing confused or contradictory responses.

**Current behavior:**

- **Bug 1**: `_cache_walk_root` in `bridge/context.py` returns `None` on any cache miss and falls back to the Telegram API walk. The API walk can resolve a *different* root message than the cache walk would (e.g., if Valor's intermediate outbound messages aren't in cache yet when the reply arrives). Two replies to the same thread produce different session IDs.

- **Bug 2**: The steering check at `bridge/telegram_bridge.py:1168` only iterates `("running", "active")` statuses. When a reply resolves to a **completed** session, no match is found and the check falls through — creating a fresh `AgentSession` with the same `session_id` but zero prior context.

**Observed evidence:**
```
08:34:07 [routing] Session tg_valor_-5051653062_8890 (continuation=True)
08:34:49 [routing] Session tg_valor_-5051653062_8871 (continuation=True)
```
Two sessions for the same thread, both running concurrently with incomplete context.

```
08:32:04 LIFECYCLE session=tg_valor_-5051653062_8871 transition=pending→pending id=02619848
08:34:49 LIFECYCLE session=tg_valor_-5051653062_8871 transition=pending→pending id=25630d4e
```
Same session_id enqueued twice with different UUIDs — the completed-session guard was skipped.

**Desired outcome:**

- A reply-to message in any Valor thread always resolves to the same session_id, every time
- Replying to a completed session re-enqueues it with the completed session's context summary prepended
- No duplicate `tg_*` sessions in `valor-session list` for the same logical thread

## Freshness Check

**Baseline commit:** `7961ec25`
**Issue filed at:** 2026-04-12T08:53:16Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/context.py:406` — `resolve_root_session_id` function — still at L406, claims hold exactly
- `bridge/context.py:482` — `_cache_walk_root` function — still at L482, cache-miss returns `None` confirmed
- `bridge/telegram_bridge.py:1168` — steering check iterates only `("running", "active")` — confirmed; `"completed"` not checked
- `bridge/telegram_bridge.py:1015` — intake terminal guard skips the `is_reply_to_valor` branch — confirmed; this skip is what enables Bug 2

**Cited sibling issues/PRs re-checked:**
- #567 — Original reply-to fix — closed; resolved via PRs #573 and #574 (merged 2026-03-27)
- #705 — Rapid-fire message coalescing — still open; no overlap with this fix
- #318 — Semantic session routing — still open; no overlap

**Commits on main since issue was filed (touching referenced files):** None

**Active plans in `docs/plans/` overlapping this area:** None

**Notes:** `models/agent_session.py:187` confirms `context_summary = Field(null=True)` exists and is available for Bug 2 re-enqueue context injection.

## Prior Art

- **PR #573** — "Fix reply-to session resume: resolve root session_id via chain walk" — introduced `_cache_walk_root` and `resolve_root_session_id` (closed 2026-03-27). This is the code this plan is fixing — the original implementation had a correctness gap in how cache misses interact with concurrent outbound message caching.
- **PR #574** — "Fix: resolve root session_id when user replies to Valor's response" — companion PR promoting `TelegramMessage.message_id` to `KeyField` for O(1) lookups. Also part of the fix being patched here.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #573 + #574 | Introduced cache-first → API fallback → direct fallback resolution strategy | Cache and API fallbacks can resolve *different* roots when Valor's outbound messages are written to cache asynchronously (timing gap). The fix assumed cache and API walks would produce the same result but didn't persist the resolved root to make future lookups authoritative. Additionally, the steering check was updated for running/pending but not for the completed-session re-enqueue path. |

**Root cause pattern:** Both bugs share the same failure mode — the fix was applied at the lookup/walk layer but missed the persistence layer (Bug 1) and the re-enqueue path (Bug 2). Lookups that find nothing fall through to path-of-least-resistance behavior (create new session) rather than authoritative recovery behavior.

## Data Flow

**Bug 1 path — session ID resolution:**

1. **Entry**: Telegram message arrives with `reply_to_msg_id` set
2. **`bridge/telegram_bridge.py:921`**: Calls `resolve_root_session_id(client, chat_id, reply_to_msg_id, project_key)`
3. **`bridge/context.py:440`**: Calls `_cache_walk_root(chat_id, reply_to_msg_id)` — walks TelegramMessage Popoto records
4. **Cache miss**: If any intermediate message's `TelegramMessage` record isn't in cache (e.g., Valor's outbound message hasn't been stored yet), returns `None`
5. **API fallback**: `fetch_reply_chain()` walks via Telegram API — finds different root (e.g., skips Valor's intermediate if the API chain includes messages that the cache walk would stop at)
6. **Result**: Non-deterministic `session_id` — two replies to the same thread can produce different IDs

**Bug 2 path — completed session bypass:**

1. **Entry**: Reply to a message whose session is `completed`
2. **`bridge/telegram_bridge.py:1159`**: `is_reply_to_valor=True`, enters steering check
3. **`bridge/telegram_bridge.py:1168`**: Checks `("running", "active")` — finds nothing
4. **`bridge/telegram_bridge.py:1203`**: Checks `"pending"` — finds nothing
5. **Falls through to L1015**: Intake terminal guard is **skipped** because `is_reply_to_valor=True`
6. **`enqueue_agent_session()`**: Creates a brand-new `AgentSession` with the same `session_id` but no context from the completed session

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment before build)
- Review rounds: 1 (code review after build)

## Prerequisites

No prerequisites — both fixes use existing infrastructure (AgentSession model, Popoto, `push_steering_message`).

## Solution

### Key Elements

- **Bug 1 — Authoritative root cache**: When the API fallback resolves a root, persist the result to a Redis key (`session_root:{chat_id}:{msg_id}`) so subsequent lookups for any message in the same chain return the same root deterministically. This is Option B from the issue's Solution Sketch. Warm path is unaffected — the key is checked before `_cache_walk_root` and returned immediately if found.

- **Bug 2 — Completed session re-enqueue with context**: After the `"pending"` check at L1203, add a `"completed"` status check. If found, re-enqueue the session (new UUID, same `session_id`) with the completed session's `context_summary` prepended to the message text, signaling the agent what was previously done. If `context_summary` is None, prepend a generic "This continues a completed session" note.

### Flow

**Bug 1:**
Reply arrives → check `session_root:{chat_id}:{msg_id}` Redis key → hit: return cached root (deterministic) → miss: `_cache_walk_root` → miss: API walk → **persist result to `session_root:` key** → return root

**Bug 2:**
Reply to completed session → steering check running/active (miss) → pending (miss) → **completed (hit)** → re-enqueue with `context_summary` prepended → return (don't fall through)

### Technical Approach

**Bug 1 implementation:**

1. Add a new async helper `_get_cached_root(chat_id, msg_id) -> int | None` that reads from Redis key `session_root:{chat_id}:{msg_id}` (TTL: 7 days — chains are stable)
2. Add `_set_cached_root(chat_id, msg_id, root_id)` that writes the resolved root for *every message in the chain* (not just the queried one) to maximize future hit rate
3. In `resolve_root_session_id`, check `_get_cached_root` first (before `_cache_walk_root`). On any successful resolution (cache walk or API walk), call `_set_cached_root` before returning
4. Use the existing Redis connection from `bridge/context.py` (already imports redis client)

**Bug 2 implementation:**

1. After the `pending_sessions` block at L1235 (the `return` for pending steering), add an `elif completed_sessions` block:
   ```python
   completed_sessions = AgentSession.query.filter(session_id=session_id, status="completed")
   if completed_sessions:
       completed = completed_sessions[0]
       summary = getattr(completed, "context_summary", None) or "This continues a previously completed session."
       augmented_text = f"[Prior session context: {summary}]\n\n{clean_text}"
       # Enqueue fresh session with prior context prepended
       enqueue_agent_session(..., message=augmented_text, ...)
       await send_markdown(client, event.chat_id, "Resuming from prior session.", reply_to=message.id)
       logger.info(f"[{project_name}] Resumed completed session {session_id} with prior context")
       return
   ```
2. The enqueue uses the same `session_id` (same thread continuity) but a new `AgentSession` UUID

**No latency on warm path**: Both fixes add O(1) Redis lookups that short-circuit before the existing cache walk — warm path gets *faster*, not slower.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_get_cached_root` and `_set_cached_root` must fail silently (Redis unavailable → log debug, proceed to cache walk). The existing `except Exception` in `resolve_root_session_id` covers this — verify new helpers are called inside the try block
- [ ] Completed session check: if `AgentSession.query.filter()` raises, fall through to existing enqueue (same behavior as running/pending checks)

### Empty/Invalid Input Handling
- [ ] `context_summary` may be `None` on completed sessions — default to generic "continuing completed session" string, never pass None to the message builder
- [ ] `_set_cached_root` called with empty chain → no-op, don't write anything to Redis

### Error State Rendering
- [ ] If completed session re-enqueue fails, user sees no ack — log at ERROR and let it fall through to a clean new enqueue (acceptable degradation)

## Test Impact

- [ ] `tests/integration/test_steering.py::TestBridgeSteeringLogic::test_steering_no_match_for_completed` — UPDATE: this test currently asserts that completed sessions return `None` from the running/active check (correct). It must be extended (or a sibling test added) to verify that the completed branch *re-enqueues* with context rather than falling through entirely.
- [ ] `tests/integration/test_steering.py::TestResolveRootSessionId::test_resolve_root_session_id_api_fallback_on_cache_miss` — UPDATE: add assertion that after API fallback resolves, the root is persisted to the `session_root:` Redis key (i.e., `_get_cached_root` returns the same value on second call without API hit).
- [ ] `tests/integration/test_steering.py::TestResolveRootSessionId::test_resolve_root_session_id_cache_hit` — UPDATE: add a sub-case where `_get_cached_root` is warm (Redis key pre-populated) and verify neither `_cache_walk_root` nor `fetch_reply_chain` are called.

## Documentation

- [ ] Update `docs/features/session-management.md` — add section on deterministic root caching (`session_root:` Redis key scheme, TTL, write-on-resolve pattern)
- [ ] Update `docs/features/session-management.md` — add section on completed-session resume behavior (context injection, user-visible ack message)
- [ ] Update `docs/features/README.md` index if the session-management entry needs a new description

## Update System

No update system changes required — this fix is internal to bridge and context modules. No new config keys, no schema migrations, no new dependencies.

## Agent Integration

No agent integration changes required — the fix is in bridge routing logic, which runs in the bridge process. The agent receives steering messages via the existing `push_steering_message` path, which is unchanged.

## Rabbit Holes

- **Option A (retry on cache miss)**: Tempting — add a short sleep/retry before falling back to the API. Problem: adds latency to every cold path. The authoritative cache approach (Option B) solves the root cause without latency.
- **Option C (ensure outbound TelegramMessage write-before-return)**: Fixing the write ordering in `response.py` is a valid long-term fix but requires tracing async write completion through the bridge callback chain. Out of scope — the authoritative cache is simpler and sufficient.
- **Storing full chain in Redis**: Writing every message ID in the chain to `session_root:{chat_id}:{msg_id}` is valuable but could be over-engineering. Start with writing the queried `start_msg_id` only; full chain write can be a follow-up.
- **`context_summary` richness**: Tempting to summarize the completed session with a new LLM call at resume time. Scope creep — use what's already in `context_summary` field, which the session populates during finalization.

## Success Criteria

- [ ] Repeated replies to different messages in the same Valor thread always resolve to the same `session_id`
- [ ] After API fallback resolves a root, subsequent calls hit Redis cache (no second API walk)
- [ ] Replying to a completed session re-enqueues with prior `context_summary` in message text
- [ ] No duplicate `tg_*` sessions appear in `valor-session list` for the same thread
- [ ] `test_resolve_root_session_id_api_fallback_on_cache_miss` updated to assert root caching
- [ ] New test: `test_reply_to_completed_session_reenqueues_with_context` passes
- [ ] `test_steering_no_match_for_completed` updated to cover new completed-session handling

## Team Members

- **Builder (bridge-routing)**
  - Name: bridge-builder
  - Role: Implement Bug 1 root caching in `bridge/context.py` and Bug 2 completed-session check in `bridge/telegram_bridge.py`
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-routing)**
  - Name: bridge-validator
  - Role: Verify both fixes, run steering tests, confirm no regression on warm-path performance
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: bridge-documentarian
  - Role: Update `docs/features/session-management.md` with root caching and completed-session resume sections
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement Bug 1 — Authoritative root cache
- **Task ID**: build-root-cache
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py::TestResolveRootSessionId`
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_get_cached_root(chat_id, msg_id) -> int | None` helper in `bridge/context.py` using Redis key `session_root:{chat_id}:{msg_id}` with 7-day TTL
- Add `_set_cached_root(chat_id, msg_id, root_id)` helper that writes the resolved root; call it from `resolve_root_session_id` on every successful resolution (both cache walk and API walk paths)
- Check `_get_cached_root` at the top of `resolve_root_session_id` before `_cache_walk_root`; return immediately if hit
- Both helpers must be inside the existing `try/except` block — fail silently on Redis errors

### 2. Implement Bug 2 — Completed session re-enqueue with context
- **Task ID**: build-completed-resume
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py::TestBridgeSteeringLogic`
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/telegram_bridge.py`, after the `pending_sessions` block (around L1235), add a completed-session check: `AgentSession.query.filter(session_id=session_id, status="completed")`
- If found, prepend `context_summary` (or generic fallback string) to `clean_text` and call `enqueue_agent_session` with the augmented message
- Send ack: "Resuming from prior session." via `send_markdown`
- Log at INFO and return — do not fall through to the main enqueue path

### 3. Update tests for both fixes
- **Task ID**: build-tests
- **Depends On**: build-root-cache, build-completed-resume
- **Validates**: `tests/integration/test_steering.py`
- **Assigned To**: bridge-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `test_resolve_root_session_id_api_fallback_on_cache_miss` to assert root is persisted to Redis after API fallback
- Add `test_resolve_root_session_id_uses_cached_root` — warm Redis key → no cache walk, no API call
- Update `test_steering_no_match_for_completed` or add `test_reply_to_completed_session_reenqueues_with_context` — verify completed session produces augmented re-enqueue

### 4. Validate fixes
- **Task ID**: validate-bridge
- **Depends On**: build-tests
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_steering.py -v`
- Run `python -m ruff check bridge/context.py bridge/telegram_bridge.py`
- Verify all success criteria are met

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-bridge
- **Assigned To**: bridge-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-management.md` with root caching and completed-session resume sections
- Update `docs/features/README.md` index entry if needed

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Confirm all verification checks pass
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/integration/test_steering.py -v` | exit code 0 |
| Full suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/context.py bridge/telegram_bridge.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/context.py bridge/telegram_bridge.py` | exit code 0 |
| Root cache test | `pytest tests/integration/test_steering.py -k "cached_root"` | exit code 0 |
| Completed resume test | `pytest tests/integration/test_steering.py -k "completed"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

## Open Questions

None — both bugs have clear root causes, confirmed file:line references, and unambiguous solution approaches. Ready to build.
