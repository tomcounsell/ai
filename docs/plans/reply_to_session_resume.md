---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-03-27
tracking: https://github.com/tomcounsell/ai/issues/567
last_comment_id:
---

# Reply-To Should Resume Original AgentSession

## Problem

When a user replies to a Valor response in Telegram, the bridge creates a second AgentSession instead of resuming the original one. This happens because the `session_id` is derived from `reply_to_msg_id` — and when the user replies to Valor's response (not the original human message), the derived session_id points to a different (non-existent) session.

**Current behavior:**

1. User sends message A (msg_id=8111) -> bridge creates AgentSession `tg_popoto_{chat}_8111`
2. Valor responds (msg_id=8113)
3. User replies to Valor's response (msg_id=8114, reply_to=8113) -> bridge derives `tg_popoto_{chat}_8113` — a different session_id
4. Dashboard shows two separate sessions; steering check fails because no session with id `_8113` exists

**Desired outcome:**

Reply-to any message in a conversation thread (including Valor's responses) resolves to the **original** AgentSession. One session per conversation thread, always.

## Prior Art

- **Issue #318**: Route unthreaded messages into active sessions — Added semantic routing and intake classifier for messages that don't use Telegram's reply feature. Separate concern (unthreaded), but established the steering pattern.
- **Issue #374**: Observer cross-wire on continuation sessions — Fixed session_id mismatch caused by Claude Code session UUID tracking. Different root cause but same symptom (wrong session_id).
- **PR #378**: Fix Observer SDLC pipeline — Fixed classification race and cross-repo gh resolution. Tangentially related (session lifecycle).
- **Issue #23**: Adopt steering concepts from pi-mono — Original steering architecture. Established the fast-path pattern for reply-to messages.

## Data Flow

Current (broken) flow for reply-to-Valor:

1. **Entry point**: User sends reply-to message in Telegram (reply_to_msg_id = Valor's response)
2. **telegram_bridge.py L944-946**: `is_reply_to_valor=True`, derives `session_id = tg_{project}_{chat}_{reply_to_msg_id}` using Valor's message ID directly
3. **telegram_bridge.py L1130-1146**: Steering check queries `AgentSession.query.filter(session_id=session_id, status="running")` — finds nothing because no session has Valor's msg_id as its key
4. **telegram_bridge.py L1197+**: Falls through to intake classifier or enqueue, creating a NEW AgentSession with the wrong session_id

Fixed flow:

1. **Entry point**: Same user reply-to message
2. **New: resolve_root_session_id()**: Look up `reply_to_msg_id` in TelegramMessage records. If it's an outbound message (direction="out"), read its `session_id` field to find the original session. If not found, walk the reply chain via `fetch_reply_chain()` as fallback.
3. **telegram_bridge.py L944-946**: Use resolved root session_id instead of raw `reply_to_msg_id`
4. **Steering check**: Finds the correct running/active session, injects steering message
5. **Or enqueue**: If session is completed/dormant, re-queues with the correct session_id

## Architectural Impact

- **New dependencies**: None — uses existing TelegramMessage model and `fetch_reply_chain()`
- **Interface changes**: `store_message()` call for outbound messages needs `message_id` and `session_id` parameters added (currently missing)
- **Coupling**: Slightly increases coupling between session_id derivation and TelegramMessage storage, but this is correct — outbound messages are part of the session lifecycle
- **Data ownership**: No change — TelegramMessage already owns message<->session mapping
- **Reversibility**: Fully reversible — the new resolution function is additive and falls back to current behavior if lookup fails

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment)
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses existing models and infrastructure.

## Solution

### Key Elements

- **Root session resolver**: New function that maps any message_id (including Valor's outbound messages) back to the original session_id that started the conversation
- **Outbound message enrichment**: Ensure `store_message()` for outbound messages includes `message_id` and `session_id` so the reverse lookup works
- **Graceful fallback**: If TelegramMessage lookup fails (missing records, Redis down), fall back to current behavior (derive from raw reply_to_msg_id)

### Flow

**User replies to Valor** → resolve_root_session_id() → TelegramMessage lookup (fast, DB-only) → found? use original session_id : fallback to reply chain walk → **correct session_id** → steering or enqueue

### Technical Approach

1. **Add `resolve_root_session_id()` to `bridge/context.py`**: Given a `reply_to_msg_id` and `chat_id`, look up the TelegramMessage record. If direction="out", return its `session_id`. If direction="in", derive session_id from that message's `message_id` (it was a human message that started a session). If not found, optionally walk the reply chain via `fetch_reply_chain()` to find the root human message.

2. **Enrich outbound `store_message()` call** (telegram_bridge.py L1620-1626): Pass `message_id=sent.id` (requires `send_response_with_files` to return the sent message object or ID) and `session_id` from the job context. This is the prerequisite — without it, reverse lookup has no data.

3. **Update `send_response_with_files` return type**: Change from `bool` to `int | None` returning the sent message's Telegram ID, or keep returning `bool` and capture the sent message ID separately. The `_send` callback already has access to `sent` — just need to extract `.id` from the first sent message.

4. **Wire into session_id derivation** (telegram_bridge.py L944-946): Call `resolve_root_session_id()` instead of directly using `message.reply_to_msg_id`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `resolve_root_session_id()` function must catch all exceptions and fall back to current behavior (return `reply_to_msg_id` unchanged). Test that Redis errors, missing records, and corrupt data all degrade gracefully.
- [ ] The outbound `store_message()` at L1620 already has `except Exception: pass` — this is acceptable since message storage is best-effort, but the test should verify that failures don't block message delivery.

### Empty/Invalid Input Handling
- [ ] Test `resolve_root_session_id()` with: message_id that doesn't exist in TelegramMessage, message_id with no session_id set, message_id=None
- [ ] Test with TelegramMessage records that have empty/null `session_id` field (old records before enrichment)

### Error State Rendering
- [ ] No user-visible error states — this is internal routing logic. Verify that fallback behavior produces the same user experience as current behavior (just with a potentially wrong session_id, which is the status quo).

## Test Impact

- [ ] `tests/integration/test_steering.py` — UPDATE: Tests that create sessions with hardcoded session_ids will still work, but new tests should be added for the reply-to-Valor flow
- [ ] `tests/e2e/test_session_lifecycle.py::test_chat_session_created_with_correct_fields` — UPDATE: May need to account for the new resolve step in session_id derivation
- [ ] `tests/e2e/test_session_continuity.py::test_multiple_sessions_same_chat` — UPDATE: Verify this still creates separate sessions for genuinely separate conversations

No existing tests are expected to break because the change is additive — `resolve_root_session_id()` is a new function inserted before the existing session_id derivation. The fallback produces the exact same session_id as the current code.

## Rabbit Holes

- **Walking the full Telegram reply chain for every reply-to message**: The `fetch_reply_chain()` function makes Telegram API calls (one per hop). This should only be used as a fallback when TelegramMessage lookup fails. The primary path must use DB-only lookups.
- **Backfilling session_id on historical outbound TelegramMessage records**: Tempting to migrate old records, but unnecessary — the fallback handles old messages, and new ones will have the data. A migration script would be a separate chore.
- **Recursive chain resolution**: If a reply chain spans multiple sessions (user starts conversation A, Valor responds, user starts new topic B as reply-to Valor), we should NOT trace back to session A. Only resolve within the same logical conversation. The single-hop TelegramMessage lookup handles this correctly — the outbound message's `session_id` IS the right answer.

## Risks

### Risk 1: Outbound messages missing `message_id` in `store_message()`
**Impact:** Without the Telegram message ID on outbound records, the reverse lookup has no data to match against, making the feature non-functional for all conversations.
**Mitigation:** The `_send` callback at L1606 receives `sent` from `send_response_with_files`. Modify `send_response_with_files` to return the sent message ID alongside the success boolean. This is a small, low-risk change.

### Risk 2: `send_response_with_files` sends multiple messages (text + files)
**Impact:** When a response includes files, multiple Telegram messages are sent. The user might reply to a file message rather than the text message, and only one message_id gets stored.
**Mitigation:** Store the message_id of the FIRST sent message (which is the file or first chunk). Alternatively, store all sent message IDs. For v1, storing the last text message ID is sufficient since users typically reply to text, not files.

### Risk 3: TelegramMessage records have stale or missing session_id
**Impact:** Outbound messages stored before this fix won't have `session_id` populated. Reverse lookups on old messages will fail.
**Mitigation:** Graceful fallback — if TelegramMessage exists but has no session_id, fall through to `fetch_reply_chain()` or return raw `reply_to_msg_id`. New messages will work correctly going forward.

## Race Conditions

### Race 1: Outbound message stored before session_id is available
**Location:** `bridge/telegram_bridge.py` L1606-1626 (the `_send` callback)
**Trigger:** The `_send` callback fires to deliver a response while the session is still being set up or the session_id isn't passed through to the callback scope.
**Data prerequisite:** The `session_id` must be available in the `_send` callback closure.
**State prerequisite:** The AgentSession must exist before its outbound messages reference it.
**Mitigation:** The `_send` callback is created inside a per-project loop that has access to the session context. Pass `session_id` through the `session` parameter that `_send` already accepts. The session_id is deterministic (derived at message intake time), so it's always available before any outbound messages are sent.

## No-Gos (Out of Scope)

- **Backfilling historical outbound TelegramMessage records** — Handle gracefully via fallback, don't migrate
- **Multi-session thread merging** — If a user intentionally starts a new topic via reply-to, that's a new session; we don't merge sessions
- **Semantic routing changes** — The unthreaded routing (#318) is a separate system; this fix only addresses explicit reply-to chains
- **Changing the session_id format** — The format `tg_{project}_{chat}_{msg_id}` stays the same; we're just resolving the correct msg_id

## Update System

No update system changes required — this is a bridge-internal change. The bridge auto-restarts on deploy via the existing update skill.

## Agent Integration

No agent integration required — this is a bridge-internal change that affects session routing logic only. No new MCP servers or tool changes needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-isolation.md` to document the root session resolution behavior
- [ ] Add inline documentation on `resolve_root_session_id()` explaining the lookup chain and fallback strategy

### Inline Documentation
- [ ] Code comments on the resolve function explaining the TelegramMessage lookup -> reply chain walk -> raw fallback chain
- [ ] Updated docstrings for modified `store_message()` call explaining why `message_id` and `session_id` are now included

## Success Criteria

- [ ] Reply-to any message in a Valor conversation thread (including Valor's own responses) resumes the original AgentSession
- [ ] Dashboard shows exactly one row per conversation thread
- [ ] If the original session is running/active, the reply is injected via steering (no new job created)
- [ ] If the original session is completed/dormant, it is resumed with the same session_id
- [ ] No new AgentSession is created when replying within an existing thread
- [ ] Existing steering tests continue to pass
- [ ] New test: reply-to-Valor-response resolves to root session_id
- [ ] Graceful fallback: if TelegramMessage lookup fails, behavior matches current code
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (bridge-routing)**
  - Name: routing-builder
  - Role: Implement resolve_root_session_id(), enrich outbound store_message(), wire into session_id derivation
  - Agent Type: builder
  - Resume: true

- **Builder (response-enrichment)**
  - Name: response-builder
  - Role: Modify send_response_with_files to return sent message ID, update _send callback
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Write unit and integration tests for root session resolution and outbound message enrichment
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: End-to-end verification of reply-to routing
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Enrich outbound message storage
- **Task ID**: build-outbound-enrichment
- **Depends On**: none
- **Validates**: tests/unit/test_outbound_message_enrichment.py (create)
- **Assigned To**: response-builder
- **Agent Type**: builder
- **Parallel**: true
- Modify `send_response_with_files()` in `bridge/response.py` to return `int | None` (sent message Telegram ID) instead of `bool`
- Update the `_send` callback in `bridge/telegram_bridge.py` L1606 to capture the returned message ID
- Pass `message_id` and `session_id` to the `store_message()` call at L1620-1626
- Ensure `session_id` is available in the `_send` closure (thread it through from the job context)

### 2. Implement root session resolver
- **Task ID**: build-root-resolver
- **Depends On**: build-outbound-enrichment
- **Validates**: tests/unit/test_root_session_resolver.py (create), tests/integration/test_reply_to_session_resume.py (create)
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `resolve_root_session_id()` in `bridge/context.py`
- Logic: given (reply_to_msg_id, chat_id, project_key), look up TelegramMessage by (chat_id, message_id=reply_to_msg_id). If found and direction="out", return its session_id. If found and direction="in", derive session_id from that message's message_id. If not found, return None (caller falls back).
- Wire into `bridge/telegram_bridge.py` L944-946: when `is_reply_to_valor and message.reply_to_msg_id`, call resolver first, fall back to current derivation if resolver returns None

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-root-resolver
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit test: `resolve_root_session_id()` with outbound message, inbound message, missing message, no session_id
- Unit test: outbound `store_message()` now includes message_id and session_id
- Integration test: full flow — create session, send outbound with enriched storage, simulate reply-to-Valor, verify same session_id resolved
- Verify all existing steering tests still pass

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: routing-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` with root session resolution behavior
- Add inline code comments and docstrings

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify success criteria
- Check that reply-to-Valor correctly resolves to original session

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Resolver function exists | `grep -r "def resolve_root_session_id" bridge/context.py` | exit code 0 |
| Outbound store has message_id | `grep "message_id=" bridge/telegram_bridge.py \| grep "store_message"` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions — the issue has thorough recon and the solution path is clear. The technical approach uses existing infrastructure (TelegramMessage model, fetch_reply_chain fallback) and the main prerequisite (outbound message enrichment) is straightforward.
