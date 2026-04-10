---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/843
last_comment_id:
---

# Worker Telegram Relay Output Handler

## Problem

When the standalone worker completes a Telegram-originated agent session, the final delivery message (the SEND/EDIT/REACT result from the stop hook) is written to a local log file instead of being delivered to Telegram.

**Current behavior:**
The worker registers `FileOutputHandler` for every project at startup (`worker/__main__.py:152-174`). When `send_to_chat()` in `agent_session_queue.py` calls `send_cb`, it writes to `logs/worker/{session_id}.log`. The log message says `"Delivered to Telegram"` but no Telegram message is sent.

**Desired outcome:**
For Telegram-originated sessions, the worker writes to the Redis outbox (`telegram:outbox:{session_id}`) using the same JSON payload format as `tools/send_telegram.py`. The bridge relay picks it up and delivers to Telegram. `FileOutputHandler` remains as fallback for non-Telegram and dev environments.

## Prior Art

- **PR #737**: Extract standalone worker service from bridge monolith -- created the worker/bridge separation and `FileOutputHandler` as a placeholder. The relay path was deferred.
- **Issue #741**: Worker service: persistent event loop, graceful shutdown, headless nudge loop -- closed, addressed event loop issues but not output delivery.
- **PR #602**: Agent-controlled message delivery: stop-hook review gate -- established the SEND/EDIT/REACT/SILENT delivery choices. Assumed bridge-side callbacks would handle delivery.
- **Issue #750 / PR #751**: Bridge/worker separation -- enforced separation boundary but did not add a Telegram-capable handler to the worker side.

## Data Flow

1. **Stop hook fires** (`agent/hooks/stop.py`): Writes `delivery_action`, `delivery_text`, `delivery_emoji` to `AgentSession` in Redis.
2. **`send_to_chat()`** (`agent_session_queue.py:2725`): Reads delivery fields from `AgentSession`, formats the message, calls `send_cb(chat_id, text, reply_to_msg_id, session)`.
3. **`send_cb`** (currently `FileOutputHandler.send`): Writes to `logs/worker/{session_id}.log`. **This is the broken link.**
4. **Desired**: `TelegramRelayOutputHandler.send()` writes JSON payload to `telegram:outbox:{session_id}` in Redis via `rpush`.
5. **Bridge relay** (`bridge/telegram_relay.py:475`): Already polls `telegram:outbox:*` keys and delivers via Telethon. No changes needed.

## Architectural Impact

- **New dependencies**: None -- Redis is already a dependency; `json`, `time` are stdlib.
- **Interface changes**: None -- `TelegramRelayOutputHandler` implements the existing `OutputHandler` protocol with no changes to the protocol itself.
- **Coupling**: Minimal increase -- the worker now writes to a Redis key that the bridge reads, but this contract already exists (used by `tools/send_telegram.py`).
- **Data ownership**: No change -- the outbox key pattern `telegram:outbox:{session_id}` is already the established contract.
- **Reversibility**: Trivial -- revert to `FileOutputHandler` registration in `worker/__main__.py`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- Redis is already required and connected by the worker at startup.

## Solution

### Key Elements

- **`TelegramRelayOutputHandler`**: New class in `agent/output_handler.py` implementing `OutputHandler` protocol. Writes to the Redis outbox using the same payload format as `tools/send_telegram.py`.
- **Worker registration logic**: Updated in `worker/__main__.py` to use `TelegramRelayOutputHandler` instead of `FileOutputHandler`. `FileOutputHandler` remains available as a fallback.
- **Composite handler**: The worker uses `TelegramRelayOutputHandler` which writes to both Redis outbox (for Telegram delivery) and the file log (for debugging/audit). This avoids losing the file logging capability.

### Flow

**Session completes** -> stop hook writes delivery fields -> `send_to_chat()` calls `send_cb` -> `TelegramRelayOutputHandler.send()` writes JSON to `telegram:outbox:{session_id}` via `rpush` -> bridge relay polls and delivers via Telethon

### Technical Approach

- `TelegramRelayOutputHandler.__init__` accepts an optional `redis_url` (defaults to `REDIS_URL` env var or `redis://localhost:6379/0`) and an optional `file_handler` for dual-write.
- `send()` builds the same JSON payload as `tools/send_telegram.py:145-151`: `{"chat_id", "reply_to", "text", "session_id", "timestamp"}`. Uses `rpush` to `telegram:outbox:{session_id}` and sets TTL of 3600s.
- `react()` builds a reaction payload: `{"type": "reaction", "chat_id", "reply_to", "emoji", "session_id", "timestamp"}`. Uses `rpush` to the same outbox key.
- The `session_id` is extracted from `session.session_id` (the `AgentSession` object passed as the `session` parameter), falling back to `chat_id`.
- Connection uses `redis.Redis.from_url()` matching the pattern in `tools/send_telegram.py:40-45`.
- If Redis write fails, log the error but do not crash the session -- output delivery is best-effort. The file handler fallback ensures output is never lost.
- Worker registration in `worker/__main__.py` creates `TelegramRelayOutputHandler(file_handler=FileOutputHandler())` and registers it for all projects.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `TelegramRelayOutputHandler.send()` catches Redis connection errors and logs them via `logger.error` -- test asserts the error is logged and no exception propagates
- [x] `TelegramRelayOutputHandler.react()` same pattern -- Redis failure logged, no crash

### Empty/Invalid Input Handling
- [x] `send()` with empty text returns immediately (no Redis write, matching `FileOutputHandler` behavior)
- [x] `send()` with `None` session falls back to `chat_id` for session_id extraction

### Error State Rendering
- [x] Not applicable -- this is a backend delivery handler with no user-visible error rendering

## Test Impact

- [x] `tests/unit/test_output_handler.py::TestOutputHandlerProtocol` -- UPDATE: add test that `TelegramRelayOutputHandler` satisfies `OutputHandler` protocol
- [x] `tests/unit/test_output_handler.py` -- ADD: new `TestTelegramRelayOutputHandler` class with tests for send/react payloads, Redis write verification, error handling, empty text handling

No existing tests need to be deleted or replaced. The `FileOutputHandler` tests remain unchanged since that class is unmodified.

## Rabbit Holes

- **Modifying the bridge relay** -- the relay already reads `telegram:outbox:*` correctly; do not touch it.
- **Refactoring `send_to_chat()`** -- the closure in `agent_session_queue.py` is complex but works; the fix is only in the handler, not the caller.
- **Adding retry logic to Redis writes** -- the relay already has TTL-based cleanup; a single `rpush` failure is acceptable as best-effort. Do not add retry/backoff complexity.
- **Changing the outbox payload format** -- match `tools/send_telegram.py` exactly. Do not invent a new format.

## Risks

### Risk 1: Redis connection unavailable at delivery time
**Impact:** Final message not delivered to Telegram (same as current broken state, but now logged clearly).
**Mitigation:** Log the error clearly. The `FileOutputHandler` fallback still writes to disk so the output is recoverable. The worker already validates Redis at startup.

### Risk 2: Payload format mismatch with relay expectations
**Impact:** Relay silently drops malformed messages.
**Mitigation:** Use the exact same payload structure as `tools/send_telegram.py:145-151`. Unit test verifies payload keys and types.

## Race Conditions

No race conditions identified -- `rpush` is atomic in Redis, and the relay reads from the list independently. The outbox key is scoped per session_id, so there is no cross-session contention.

## No-Gos (Out of Scope)

- Do not modify `bridge/telegram_relay.py` -- it already works
- Do not modify the stop hook delivery gate (`agent/hooks/stop.py`)
- Do not add Telegram-specific logic to `agent_session_queue.py`
- Do not remove `FileOutputHandler` -- it remains for dev/non-Telegram use

## Update System

No update system changes required -- this is a worker-internal change. The `TelegramRelayOutputHandler` class uses Redis which is already a dependency on all machines. After merge, machines running the worker will pick up the change on next `git pull` and worker restart.

## Agent Integration

No agent integration required -- this is a worker-internal output routing change. No new MCP servers, no `.mcp.json` changes, no bridge imports needed. The agent's tools (`valor-telegram send`) already work independently via the same Redis outbox.

## Documentation

- [x] Update `docs/features/bridge-worker-architecture.md` to document the `TelegramRelayOutputHandler` and the worker's output delivery path
- [x] Add inline docstrings to `TelegramRelayOutputHandler` class and methods

## Success Criteria

- [x] Worker delivers final session output to Telegram for Telegram-originated sessions
- [x] `FileOutputHandler` still works as fallback for non-Telegram / dev environments
- [x] The misleading `"Delivered to Telegram"` log message is removed or replaced with accurate logging
- [x] Stop hook delivery choices (SEND/EDIT/REACT/SILENT) are honored end-to-end
- [x] Unit tests verify payload format matches `tools/send_telegram.py` contract
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (relay-handler)**
  - Name: relay-builder
  - Role: Implement TelegramRelayOutputHandler and update worker registration
  - Agent Type: builder
  - Resume: true

- **Validator (relay-handler)**
  - Name: relay-validator
  - Role: Verify handler protocol compliance, payload format, and error handling
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement TelegramRelayOutputHandler
- **Task ID**: build-relay-handler
- **Depends On**: none
- **Validates**: tests/unit/test_output_handler.py (update + add)
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `TelegramRelayOutputHandler` class to `agent/output_handler.py` implementing `OutputHandler` protocol
- `__init__` accepts optional `redis_url` (str) and optional `file_handler` (FileOutputHandler) for dual-write
- `send()` builds JSON payload matching `tools/send_telegram.py:145-151` format: `{"chat_id", "reply_to", "text", "session_id", "timestamp"}`
- `send()` uses `rpush` to `telegram:outbox:{session_id}` with 3600s TTL
- `send()` delegates to `file_handler.send()` if file_handler provided (dual-write for audit)
- `send()` with empty text returns immediately (no-op)
- `react()` builds reaction payload: `{"type": "reaction", "chat_id", "reply_to", "emoji", "session_id", "timestamp"}`
- `react()` uses `rpush` to same outbox key pattern
- All Redis errors caught and logged, never propagated

### 2. Update worker registration
- **Task ID**: build-worker-registration
- **Depends On**: build-relay-handler
- **Validates**: worker starts without error
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: false
- In `worker/__main__.py`, import `TelegramRelayOutputHandler`
- Create `TelegramRelayOutputHandler(file_handler=FileOutputHandler())` as the handler
- Register it for all projects (same loop, just different handler instance)
- Update log message from `"Registered FileOutputHandler"` to `"Registered TelegramRelayOutputHandler"`

### 3. Add unit tests
- **Task ID**: build-tests
- **Depends On**: build-relay-handler
- **Validates**: tests/unit/test_output_handler.py
- **Assigned To**: relay-builder
- **Agent Type**: builder
- **Parallel**: true
- Add protocol compliance test: `TelegramRelayOutputHandler` satisfies `OutputHandler`
- Test `send()` writes correct JSON payload to Redis (use fakeredis or mock)
- Test `send()` with empty text is a no-op
- Test `send()` extracts session_id from session object, falls back to chat_id
- Test `react()` writes reaction payload with `type: "reaction"` field
- Test Redis failure is caught and logged (no exception propagation)
- Test dual-write: when file_handler provided, both Redis and file get the output

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-worker-registration, build-tests
- **Assigned To**: relay-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` with output delivery path diagram
- Ensure inline docstrings are complete

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: relay-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_output_handler.py -v` -- all pass
- Run `pytest tests/ -x -q` -- full suite passes
- Verify `TelegramRelayOutputHandler` import works in worker context
- Verify payload format matches `tools/send_telegram.py` contract

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_output_handler.py -v` | exit code 0 |
| Full suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/output_handler.py worker/__main__.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/output_handler.py worker/__main__.py` | exit code 0 |
| Handler importable | `python -c "from agent.output_handler import TelegramRelayOutputHandler"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None -- the issue is well-scoped with clear acceptance criteria and the solution follows established patterns (same Redis outbox format as `tools/send_telegram.py`).
