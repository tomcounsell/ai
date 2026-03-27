---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-03-27
tracking: https://github.com/tomcounsell/ai/issues/567
last_comment_id:
---

# Reply-To Session Resume: Root Session ID Resolution

## Problem

When a user replies to Valor's response in Telegram, the bridge creates a **new AgentSession** instead of resuming the original conversation. The dashboard shows two rows for what is one conversation thread.

**Current behavior:**

1. User sends message A (msg_id=8111) → bridge creates AgentSession with `session_id=tg_popoto_{chat}_{8111}`
2. Valor responds (msg_id=8113)
3. User replies to Valor's response (msg_id=8114, reply_to=8113) → bridge derives `session_id=tg_popoto_{chat}_{8113}` — a different session_id
4. A second AgentSession is created; dashboard shows two rows

The root cause is `bridge/telegram_bridge.py` ~line 946:
```python
session_id = f"tg_{project_key}_{event.chat_id}_{message.reply_to_msg_id}"
```
This uses `reply_to_msg_id` directly. When the replied-to message is Valor's outbound response (not the original human message), the derived session_id does not match the original session.

**Desired outcome:**

- Reply-to messages always route to the original AgentSession regardless of which message in the thread is replied to
- One AgentSession per conversation thread across its entire lifecycle
- Dashboard shows one row per conversation thread

## Prior Art

- **PR #308** (Fix mid-session steering): Added steering check for `running` and `active` status on reply-to messages. Fixed injection but not session_id derivation — replying to Valor's response still produces a new session_id.
- **Issue #374** (Observer cross-wire on continuation sessions): Fixed session_id mismatch from Claude Code UUID tracking — different root cause, same symptom of duplicate session rows.
- **PR #366** (Route unthreaded messages, #318): Added semantic routing for non-reply messages. Explicitly excluded explicit reply-to chains as out-of-scope. This plan fills that gap.

## Spike Results

### spike-1: Does `send_response_with_files` return the Telegram message_id of the sent message?

- **Assumption**: "We can capture the sent Telegram message_id from the _send callback to enable fast reverse lookup"
- **Method**: code-read
- **Finding**: `send_response_with_files` in `bridge/response.py` returns `bool`, not the sent `Message` object. The `store_message` call in the `_send` callback does not pass `message_id`. Outbound `TelegramMessage` records have `message_id=None`.
- **Confidence**: high
- **Impact on plan**: Cannot use the fast reverse lookup approach without first fixing outbound message_id storage. The reply chain walk via `fetch_reply_chain()` (Telegram API) is the primary resolution path.

### spike-2: Does `fetch_reply_chain()` provide enough info to identify the root human message?

- **Assumption**: "Walking the reply chain backward gives us the oldest human message from which to derive the canonical session_id"
- **Method**: code-read
- **Finding**: `fetch_reply_chain()` in `bridge/context.py` walks up to 20 hops, returns messages with `sender`, `content`, `message_id`, and `date`. For `msg.out=True` it sets `sender_name="Valor"`. The root human message is the oldest entry where `sender != "Valor"`.
- **Confidence**: high
- **Impact on plan**: Fully reusable. Walk the chain, find the oldest non-Valor message, use its `message_id` for session_id derivation.

## Data Flow

**Current (broken) path for reply-to-Valor-response:**

1. **Entry**: User replies to Valor's msg_id=8113 in Telegram
2. **`handle_new_message`** (~line 886): `is_reply_to_valor=True`, `message.reply_to_msg_id=8113`
3. **Session ID derivation** (~line 946): `session_id = f"tg_{project_key}_{chat}_{8113}"` — wrong
4. **Steering check** (~line 1133): looks for AgentSession with `session_id=tg_..._8113` — not found
5. **`enqueue_job()`**: creates new AgentSession with `session_id=tg_..._8113` — duplicate

**Desired path:**

1. **Entry**: User replies to Valor's msg_id=8113 in Telegram
2. **`handle_new_message`** (~line 886): `is_reply_to_valor=True`, `message.reply_to_msg_id=8113`
3. **Root resolution**: Walk reply chain from msg_id=8113. Find root human message msg_id=8111.
4. **Session ID derivation**: `session_id = f"tg_{project_key}_{chat}_{8111}"` — correct
5. **Steering check**: looks for AgentSession with `session_id=tg_..._8111` — found (or dormant)
6. **Route**: If running/active → steer. If dormant/completed → resume with same session_id.

**Prerequisite fix — outbound message_id storage:**

1. `bridge/response.py` `send_response_with_files` modified to return `Message | None`
2. `_send` callback in `bridge/telegram_bridge.py` captures returned message_id
3. `store_message` called with `message_id=sent_msg_id`
4. Future: fast reverse lookup via `TelegramMessage.query.filter(message_id=X)` becomes possible

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #308 | Added steering for `running` + `active` status on reply-to messages | Only handles replies directly to the session-root message. If user replies to Valor's response, `reply_to_msg_id` differs from session root — no steering match |
| PR #366 | Added semantic routing for non-reply messages | Explicitly scoped out explicit reply-to chains; designed for a different case |

**Root cause pattern:** Both fixes assumed `reply_to_msg_id` is always the session root. That holds for the first reply but breaks for any subsequent reply to Valor's responses. The fix belongs at the session_id derivation layer.

## Architectural Impact

- **New dependencies**: None — uses existing `fetch_reply_chain()` in `bridge/context.py`
- **Interface changes**: `send_response_with_files` return type changes from `bool` to `Message | None`
- **Coupling**: Slight increase — session_id derivation now calls `bridge/context.py`. Already imported elsewhere in bridge.
- **Data ownership**: `TelegramMessage.message_id` for outbound messages becomes populated (previously None). No schema change — field already exists.
- **Reversibility**: Fully reversible — derivation change is localized to ~5 lines at line ~946.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation after critique)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | TelegramMessage storage |
| Bridge importable | `python -c "import bridge.telegram_bridge"` | Validate no import errors |

Run all checks: `python scripts/check_prerequisites.py docs/plans/reply_to_session_resume.md`

## Solution

### Key Elements

- **Root resolver**: New async helper `resolve_root_session_id(client, chat_id, reply_to_msg_id, project_key)` in `bridge/context.py` that walks the reply chain and returns the canonical session_id from the oldest human message.
- **Session ID derivation fix**: Replace the line at ~line 946 in `telegram_bridge.py` with a call to the root resolver.
- **Outbound message_id storage**: Modify `send_response_with_files` to return the Telegram Message id; update `_send` callback to pass it to `store_message`.
- **Graceful fallback**: If chain walk fails, fall back to `reply_to_msg_id` directly. Never block message delivery.

### Flow

User replies to Valor's message → `handle_new_message` detects `is_reply_to_valor=True` → call `resolve_root_session_id(client, chat_id, reply_to_msg_id)` → walk chain up to 20 hops → find oldest human message → derive `session_id` from its `message_id` → existing steering check → steer or resume as before

### Technical Approach

1. **`bridge/context.py`** — Add `resolve_root_session_id` async function:
   - Calls `fetch_reply_chain(client, chat_id, reply_to_msg_id)`
   - Finds oldest message where `sender != "Valor"`
   - Returns `f"tg_{project_key}_{chat_id}_{root_msg_id}"`
   - On any exception: returns `f"tg_{project_key}_{chat_id}_{reply_to_msg_id}"` (fallback)

2. **`bridge/telegram_bridge.py`** (~line 944) — Replace one-liner:
   ```python
   # Before:
   session_id = f"tg_{project_key}_{event.chat_id}_{message.reply_to_msg_id}"
   # After:
   from bridge.context import resolve_root_session_id
   session_id = await resolve_root_session_id(
       client, event.chat_id, message.reply_to_msg_id, project_key
   )
   ```

3. **`bridge/response.py`** — Modify `send_response_with_files` to return `Message | None` instead of `bool`. Update all 4 call sites.

4. **`bridge/telegram_bridge.py`** (`_send` callback) — Capture returned message_id, pass to `store_message(message_id=sent_id, ...)`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `resolve_root_session_id` catches all exceptions and returns fallback session_id — test with mock client that raises `ConnectionError`
- [ ] `fetch_reply_chain` already catches per-hop exceptions — verify fallback behavior when mid-chain fetch fails
- [ ] Existing `except (ConnectionError, OSError)` and `except Exception` blocks in the steering check handle failures — no regression expected

### Empty/Invalid Input Handling
- [ ] Empty reply chain (Telegram returns no messages) → falls back to `reply_to_msg_id` directly
- [ ] Chain consisting entirely of Valor messages (bot responding to itself) → uses last available human message_id, or falls back to `reply_to_msg_id`
- [ ] `reply_to_msg_id=None` guard: `resolve_root_session_id` should not be called with None (guarded at call site by `if is_reply_to_valor and message.reply_to_msg_id`)

### Error State Rendering
- [ ] If root resolution degrades to fallback, user still receives a response — graceful degradation test
- [ ] No user-visible errors introduced — resolution happens before steering

## Test Impact

- [ ] `tests/integration/test_steering.py` — UPDATE: Add `test_reply_to_valor_response_resolves_root_session` test case verifying reply-to-Valor msg_id resolves to original human-message-based session_id
- [ ] `tests/unit/test_model_relationships.py` — No change expected (tests `agent_session_id` back-reference, unaffected by this fix)
- [ ] Call sites of `send_response_with_files` that check return type — UPDATE: assert `Message | None` instead of `bool`

## Rabbit Holes

- **Semantic routing integration**: Do not expand this to fix semantic routing edge cases — that is #318's domain.
- **Re-indexing historical outbound TelegramMessages**: Not worth the effort — existing records without `message_id` are harmless.
- **Centralizing session_id generation**: Refactoring session_id generation globally is a separate chore.
- **Adding TelegramMessage DB reverse lookup as primary path**: Only useful once outbound records have `message_id`. Can be a follow-up; the API walk is authoritative for now.

## Risks

### Risk 1: Telegram API rate limits during chain walk
**Impact:** Multiple `client.get_messages()` calls per incoming message could hit rate limits under load.
**Mitigation:** `fetch_reply_chain` is bounded to 20 hops. In practice chains are 1-3 deep. Fallback ensures degraded-not-broken behavior.

### Risk 2: Outbound message_id capture breaks response delivery
**Impact:** If `send_response_with_files` return type change causes call sites to treat `None` as failure, responses could be silently dropped.
**Mitigation:** Audit all 4 call sites before changing signature. `return False` maps cleanly to `return None`. Test each call site.

### Risk 3: In-flight sessions orphaned at deploy time
**Impact:** Ongoing conversations started before the fix have `reply_to_msg_id`-based session_ids. Next reply after deploy uses root-based id — mismatch.
**Mitigation:** `enqueue_job()` supersede logic handles this (old session marked superseded). One-time disruption for in-flight sessions; acceptable.

## Race Conditions

### Race 1: Two rapid replies arrive before first is processed
**Location:** `bridge/telegram_bridge.py` ~line 944 (session_id derivation) and ~line 1133 (steering check)
**Trigger:** Two reply messages arrive before either is processed; both derive the same root session_id
**Data prerequisite:** Root session_id derived before steering check runs
**State prerequisite:** AgentSession exists before steering can match it
**Mitigation:** `enqueue_job()` same-session_id concurrency handling (supersede logic lines 318-336). No shared mutable state in the chain walk.

### Race 2: Outbound message stored before message_id is captured
**Location:** `bridge/telegram_bridge.py` `_send` callback
**Trigger:** `store_message` called before Telegram confirms delivery
**Data prerequisite:** Sent message_id must come from Telegram's API response
**State prerequisite:** `store_message` must be called after `await send_response_with_files(...)`
**Mitigation:** Already sequential — `store_message` is inside the `if sent:` block after `await`. No new race introduced.

## No-Gos (Out of Scope)

- Full migration of existing `TelegramMessage` records to add `message_id` for historical outbound messages
- Semantic routing changes (handled by #318)
- Multi-hop reply chain caching
- Changing session_id format or semantics globally
- Adding DB reverse-lookup as primary path (follow-up once outbound message_ids are populated)

## Update System

No update system changes required — this is a bridge-internal change. No new config or dependencies to propagate. Standard bridge restart after deploy.

## Agent Integration

No agent integration required — this is a bridge-internal change in the Telegram message ingress layer. No MCP server changes, no `.mcp.json` changes.

## Documentation

- [ ] Update or create `docs/features/session-management.md` to describe reply-chain root resolution behavior
- [ ] Add entry to `docs/features/README.md` index table if `session-management.md` is new
- [ ] Add docstring to `resolve_root_session_id` and the updated session_id derivation block in `handle_new_message`

## Success Criteria

- [ ] Reply to any message in a Valor thread (including Valor's responses) resumes the original AgentSession
- [ ] Dashboard shows exactly one row per conversation thread
- [ ] If original session is running/active, reply is injected via steering (no new job created)
- [ ] If original session is completed/dormant, it is resumed with the same session_id
- [ ] Outbound `TelegramMessage` records have `message_id` populated after the fix
- [ ] Existing steering tests continue to pass
- [ ] New test `test_reply_to_valor_response_resolves_root_session` passes
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-id-fix)**
  - Name: session-id-builder
  - Role: Implement root resolver, fix session_id derivation, fix outbound message_id storage
  - Agent Type: builder
  - Resume: true

- **Test Engineer (steering-tests)**
  - Name: steering-test-engineer
  - Role: Add new steering tests for reply-to-Valor-response scenario
  - Agent Type: test-engineer
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Run full test suite, verify new tests pass, confirm no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: feature-documentarian
  - Role: Update session management docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Prerequisite: Fix outbound message_id storage
- **Task ID**: build-outbound-msg-id
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py` (no regression)
- **Assigned To**: session-id-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `send_response_with_files` in `bridge/response.py` to return `Message | None` instead of `bool`
- Update `_send` callback in `bridge/telegram_bridge.py` to capture message_id from returned object
- Pass `message_id=sent_msg_id` to `store_message` call in `_send`
- Update all call sites of `send_response_with_files` to handle the new return type

### 2. Core fix: Root session_id resolver
- **Task ID**: build-root-resolver
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py` (new test), `tests/unit/` (no regression)
- **Assigned To**: session-id-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `resolve_root_session_id(client, chat_id, reply_to_msg_id, project_key)` async function to `bridge/context.py`
- Replace session_id derivation at line ~946 of `bridge/telegram_bridge.py` with a call to `resolve_root_session_id`
- Verify fallback: exception during chain walk returns `f"tg_{project_key}_{chat_id}_{reply_to_msg_id}"`

### 3. Tests: Reply-to-Valor scenario
- **Task ID**: build-steering-tests
- **Depends On**: build-root-resolver
- **Validates**: `tests/integration/test_steering.py`
- **Assigned To**: steering-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_reply_to_valor_response_resolves_root_session` to `tests/integration/test_steering.py`
- Add `test_resolve_root_session_id_fallback_on_error` — verify fallback on API failure
- Add `test_resolve_root_session_id_all_valor_chain` — edge case: chain with no human messages

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-root-resolver, build-steering-tests
- **Assigned To**: feature-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update or create `docs/features/session-management.md` describing reply-chain root resolution
- Add entry to `docs/features/README.md`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-outbound-msg-id, build-root-resolver, build-steering-tests, document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`
- Verify all success criteria met
- Confirm `ruff check` and `ruff format --check` pass

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Root resolver exists | `python -c "from bridge.context import resolve_root_session_id"` | exit code 0 |
| New steering test exists | `grep -r "test_reply_to_valor_response_resolves_root_session" tests/` | exit code 0 |
| Outbound msg_id stored | `grep -n "message_id=sent" bridge/telegram_bridge.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. Should `resolve_root_session_id` attempt a `TelegramMessage` DB lookup first (using `direction="in"` records for the replied-to message_id) as a fast path before the Telegram API walk? The DB lookup avoids API calls but requires that inbound messages are reliably stored with their `message_id` (they appear to be). This is a performance/reliability tradeoff — API walk is authoritative, DB is faster. Recommend: DB-first with API fallback if record not found.
