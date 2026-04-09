---
status: Planning
type: feature
appetite: Medium
owner: valorengels
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/840
last_comment_id:
---

# Bridge Hibernation: Structured Recovery for Telegram Auth and Connectivity Failures

## Problem

When the Telegram bridge goes down, the current 5-level self-healing chain treats all failure modes identically: crash → log → restart → retry. There is no structural distinction between:

1. **Transient connectivity loss** — network blip, Telegram server hiccup, OS sleep. Auto-recoverable in seconds to minutes.
2. **Auth expiry** — the Telethon session token has expired or been invalidated. Requires human intervention (`python scripts/telegram_login.py`). No amount of restarting fixes this.

Today, auth expiry causes the bridge to hit `is_user_authorized() == False` at startup and raise `SystemExit(1)`. The watchdog restarts it. It fails again. This loops indefinitely, generating noise, burning restart budget, and giving the human no clear signal. Meanwhile, the worker keeps running; sessions complete, generate output, and that output goes... nowhere. There is no buffer. Once sessions complete with no delivery path, output is either dropped or written to `FileOutputHandler` with no mechanism to replay it when the bridge recovers.

**Current behavior:** Auth failure → bridge exits → watchdog restarts → bridge exits → loop. No hibernation. No buffer. No useful human notification when Telegram is the only channel.

**Desired outcome:** The bridge detects which failure type has occurred, enters a structured hibernation state for unrecoverable failures, buffers worker output in Redis during downtime, and resumes delivery automatically when connectivity returns. The human gets a clear notification (including via alternative channels if Telegram itself is down).

## Prior Art

No prior issues or PRs found related to bridge hibernation, auth-vs-connectivity distinction, or output buffering during bridge downtime.

## Spike Results

### spike-1: Telethon exception taxonomy for auth vs connectivity
- **Assumption**: "Telethon raises distinct, catchable exception types for auth expiry vs network loss"
- **Method**: code-read
- **Finding**: Confirmed. Auth failures: `AuthKeyError`, `AuthKeyUnregisteredError`, `UnauthorizedError`, `SessionPasswordNeededError`. Network errors: Python's built-in `ConnectionError`, `OSError`, `TimeoutError`, `asyncio.TimeoutError`. Transient Telegram server issues: `FloodWaitError` (already handled), `ServerError`. The `is_user_authorized()` check at startup is the clean gate — False means auth is dead.
- **Confidence**: high
- **Impact on plan**: The auth-detection path is simple: catch the auth-specific Telethon errors OR check `is_user_authorized() == False` at startup. No heuristics needed.

### spike-2: Output buffer — Redis list vs FileOutputHandler
- **Assumption**: "Redis is the right buffer; FileOutputHandler already handles the fallback case"
- **Method**: code-read
- **Finding**: `FileOutputHandler` already writes to `logs/worker/{session_id}.log` when no bridge callbacks are registered. However, replay is manual — there is no mechanism to re-deliver log entries when the bridge recovers. A Redis list (`bridge:output_buffer`) would allow the bridge to `LPUSH` buffered items on session completion and `RPOP` + deliver them on reconnect. Redis TTL provides natural expiry. The `register_callbacks` function in `agent_session_queue.py` is the right insertion point — a `BufferedOutputHandler` can intercept calls when bridge is in hibernation state and push to Redis.
- **Confidence**: high
- **Impact on plan**: Two-phase delivery: (1) `BufferedOutputHandler` wraps Redis LPUSH on send; (2) bridge on reconnect drains the buffer via RPOP + Telegram send.

### spike-3: Alternative notification when Telegram is unavailable
- **Assumption**: "macOS Messages (SMS/iMessage) is accessible programmatically on the machine"
- **Method**: code-read
- **Finding**: `reading-sms-messages` skill confirms macOS Messages app is accessible. `osascript` can send iMessages/SMS. However, this requires a known phone number or contact. The simpler path: the watchdog's Level 5 alert already uses Telegram — when Telegram is down, that alert can't deliver. A plain-text file at `data/bridge-hibernation-notice.txt` plus an optional macOS notification (`osascript -e 'display notification ...'`) covers the human-notification requirement without fragile SMS integration.
- **Confidence**: medium
- **Impact on plan**: Primary alternative notification: macOS system notification via `osascript`. Secondary: prominently logged file in `data/` that `valor-service.sh status` surfaces. SMS integration is a rabbit hole.

## Data Flow

### Startup auth check (failure → hibernation)

1. **Entry point**: Bridge starts (launchd or manual)
2. **`bridge/telegram_bridge.py` `run_bridge()`**: Attempts `client.connect()` + `is_user_authorized()`
3. **Auth failure detected**: `is_user_authorized() == False` OR `AuthKeyError`/`AuthKeyUnregisteredError` raised
4. **`bridge/hibernation.py` `enter_hibernation(reason="auth_expired")`**: Writes hibernation state to `data/bridge-hibernation.json`, triggers notification
5. **Notification**: `osascript` desktop notification + log prominent warning; watchdog reads hibernation file and suppresses restart loop
6. **Worker continues**: No change to `agent_session_queue.py` — worker keeps processing sessions normally
7. **`BufferedOutputHandler`**: Sessions deliver output via buffer (Redis list) instead of Telegram

### Session output during hibernation

1. **Worker session completes**: `send_to_chat()` is called in `agent_session_queue.py`
2. **`BufferedOutputHandler.send()`**: Checks Redis key `bridge:hibernation:state`; if hibernating, `LPUSH bridge:output_buffer` with serialized `{chat_id, text, reply_to_msg_id, session_id, timestamp}`
3. **Buffer TTL**: Redis key expires after 24 hours if not drained (no stale messages accumulate indefinitely)
4. **Output**: Message sits in Redis list until bridge recovers

### Bridge reconnect → buffer drain

1. **Bridge reconnects**: Auth succeeds, `_bridge_was_connected = True`
2. **`drain_output_buffer(client)`**: Called immediately after reconnect in `run_bridge()`
3. **Redis RPOP loop**: Dequeue messages oldest-first, send via `send_response_with_files()`
4. **Hibernation state cleared**: `data/bridge-hibernation.json` deleted; Redis key `bridge:hibernation:state` removed
5. **Normal operation resumes**: `register_callbacks()` re-registers Telegram send callbacks

### Transient connectivity loss (NOT hibernation)

1. **`run_until_disconnected()` raises**: `ConnectionError`, `OSError`, or similar
2. **Existing retry loop**: Already handles with exponential backoff (2s–256s, 8 attempts)
3. **Reconnect succeeds**: Normal operation — no hibernation entered
4. **Reconnect exhausted**: Only then enter hibernation with `reason="connection_exhausted"`

## Architectural Impact

- **New module**: `bridge/hibernation.py` — state management, notification, buffer drain
- **Modified**: `bridge/telegram_bridge.py` — catches auth errors, calls hibernation module, drains buffer on reconnect
- **Modified**: `agent/output_handler.py` — new `BufferedOutputHandler` class that writes to Redis list
- **Modified**: `agent/agent_session_queue.py` — uses `BufferedOutputHandler` when no bridge callbacks (currently falls back to `FileOutputHandler`)
- **Modified**: `monitoring/bridge_watchdog.py` — reads hibernation state file and suppresses restart loop for auth-expired hibernations
- **New data**: `data/bridge-hibernation.json` — file-based hibernation state (persists across restarts)
- **New Redis keys**: `bridge:output_buffer` (list), `bridge:hibernation:state` (string/hash)
- **Coupling**: Adds bridge → Redis dependency for buffering. Redis is already a hard dependency for sessions, so this adds no new external requirement.
- **Reversibility**: Entirely additive. Removing the feature reverts to current FileOutputHandler fallback behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on buffer size and notification approach)
- Review rounds: 1 (code review of hibernation state machine and buffer drain)

## Prerequisites

No new prerequisites — Redis and Telethon are already required dependencies.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "import redis; r=redis.Redis(); r.ping()"` | Output buffer storage |
| Telethon installed | `python -c "import telethon.errors; print('ok')"` | Auth error taxonomy |

## Solution

### Key Elements

- **`bridge/hibernation.py`**: Hibernation state manager. Writes/reads `data/bridge-hibernation.json`. Sends macOS desktop notification via `osascript`. Exposes `enter_hibernation(reason)`, `exit_hibernation()`, `is_hibernating()`, `drain_output_buffer(client)`.
- **`BufferedOutputHandler`**: New `OutputHandler` implementation in `agent/output_handler.py`. On `send()`, checks Redis `bridge:hibernation:state`; if hibernating, LPUSH to `bridge:output_buffer` with 24h TTL. Falls through to `FileOutputHandler` if Redis unavailable.
- **Auth detection in bridge startup**: Explicit catch of `AuthKeyError`, `AuthKeyUnregisteredError`, `UnauthorizedError` in the connection retry loop. `is_user_authorized() == False` also triggers hibernation. Transient errors (network, `FloodWaitError`) continue with existing retry logic.
- **Watchdog hibernation awareness**: Before executing Level 1 (restart), check `data/bridge-hibernation.json`. If `reason == "auth_expired"`, skip restart and alert human instead. Prevents the watchdog from restarting a bridge that will immediately fail again.
- **Buffer drain on reconnect**: Immediately after successful `client.connect()` + `is_user_authorized()`, call `drain_output_buffer(client)` before registering event handlers. Drains oldest-first, with per-message error handling so one bad message doesn't block the rest.

### Flow

**Auth expiry detected** → `enter_hibernation("auth_expired")` → write `data/bridge-hibernation.json` + send desktop notification → bridge exits cleanly (no more restart loop) → **worker keeps running** → sessions complete → `BufferedOutputHandler` → messages queued in Redis → human runs `telegram_login.py` → **bridge restarts** → auth succeeds → `drain_output_buffer()` → buffered messages delivered → **normal operation**

**Transient network loss** → existing retry loop → exponential backoff → reconnect succeeds → **no hibernation** (fast path unchanged)

**Transient retry exhausted** → `enter_hibernation("connection_exhausted")` → watchdog notified → human inspects → resolves network → bridge restarts → drain buffer → **normal operation**

### Technical Approach

- Hibernation state is file-based (`data/bridge-hibernation.json`) for cross-restart persistence. Redis key is the secondary signal for `BufferedOutputHandler` to check during a session.
- `BufferedOutputHandler` is injected at the `register_callbacks()` fallback point in `agent_session_queue.py`. When the bridge exits for hibernation, it deregisters its callbacks (or they become stale); the fallback handler is used.
- Buffer items are JSON-serialized dicts: `{chat_id, text, reply_to_msg_id, session_id, timestamp_iso}`. Max 1000 items enforced by Redis list trim (LTRIM) to prevent unbounded growth during extended outages.
- The `drain_output_buffer()` function uses a loop with `RPOP` (not `LRANGE`) so it's resumable if interrupted. Each successful send removes the item from the list.
- macOS notification is best-effort — bridge continues to hibernation state even if `osascript` fails.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/hibernation.py` `enter_hibernation()` — if `osascript` fails (non-macOS, missing tool), log warning and continue. Test: mock `subprocess.run` to raise, assert hibernation still completes.
- [ ] `BufferedOutputHandler.send()` — if Redis unavailable, fall through to `FileOutputHandler`. Test: mock Redis to raise `ConnectionError`, assert file fallback triggered.
- [ ] `drain_output_buffer()` — if a buffered message fails to send (Telegram error), log error and continue to next message. Test: mock `send_response_with_files` to raise on first call, assert second message still sent.

### Empty/Invalid Input Handling
- [ ] `drain_output_buffer()` with empty buffer (no-op, should exit cleanly without error)
- [ ] `BufferedOutputHandler.send()` with empty `text` (should not push to Redis, matching `FileOutputHandler` behavior)
- [ ] Malformed JSON in `bridge:output_buffer` list (should log error and skip item, not crash)

### Error State Rendering
- [ ] Hibernation notice file at `data/bridge-hibernation.json` is human-readable JSON with `reason`, `timestamp`, and `instructions` fields
- [ ] `valor-service.sh status` output should surface hibernation state (or it stays purely in the file — to be decided during implementation)

## Test Impact

- [ ] `tests/unit/test_output_handler.py` — UPDATE: add `TestBufferedOutputHandler` test class covering Redis push, fallback to file on Redis failure, and empty text no-op
- [ ] `tests/unit/test_messenger.py` — UPDATE: verify `BufferedOutputHandler` satisfies `OutputHandler` protocol
- [ ] `tests/integration/test_reply_delivery.py` — REVIEW: may need a test for buffer drain flow (bridge reconnect delivers buffered messages)

No existing tests will break — this is additive. `FileOutputHandler` behavior is unchanged.

## Rabbit Holes

- **SMS via macOS Messages for alternative notification**: `osascript` iMessage sending requires knowing the recipient's phone number/contact, is fragile on non-macOS machines, and adds a new outbound dependency. macOS desktop notification achieves the same result for a single-machine setup.
- **Persistent queue with delivery guarantees**: Exactly-once delivery with acknowledgments (à la proper message queues). 24h TTL Redis list with best-effort drain is sufficient for the use case — we don't need Kafka.
- **Re-auth automation**: Having the bridge trigger `telegram_login.py` unattended. This requires a human in the loop (the script prompts for a code sent to the phone). Automation is impossible without human interaction or pre-authorizing bots.
- **Cross-machine buffer sharing**: If multiple machines run the bridge, Redis buffers would intermix. Out of scope — each machine has its own bridge process and its own Redis namespace is sufficient.
- **Infinite retry with human-escalation timer**: Progressively escalating retry with SMS/email after N minutes. The hibernation state + desktop notification is the right level of complexity for a single-operator setup.

## Risks

### Risk 1: Buffered messages delivered out of order or duplicated
**Impact:** Confusing conversation history in Telegram — the human sees a reply to a message they sent hours ago, out of context.
**Mitigation:** Buffer messages include `timestamp_iso` so the drain function can optionally prepend a delivery notice: `"[Delivered after bridge outage — originally at {timestamp}]"`. Deduplication is not required since `drain_output_buffer()` only runs once at reconnect and clears items atomically.

### Risk 2: Watchdog restart loop despite hibernation state
**Impact:** Bridge keeps restarting despite hibernation file, generating noise.
**Mitigation:** Watchdog reads `data/bridge-hibernation.json` before Level 1 action. If `reason == "auth_expired"`, skip restart and increment a `suppressed_restart_count` in the file instead. After 3 suppressed restarts, send an escalated alert.

### Risk 3: `drain_output_buffer()` blocks startup
**Impact:** Large buffer (hundreds of messages) causes a noticeable delay before the bridge accepts new messages.
**Mitigation:** Drain is async. Run concurrently with event handler registration (`asyncio.create_task(drain_output_buffer(client))`). New messages are handled immediately; draining happens in the background.

## Race Conditions

### Race 1: Session sends output while bridge is mid-reconnect
**Location:** `agent/agent_session_queue.py` `send_to_chat()`, `bridge/hibernation.py` `exit_hibernation()`
**Trigger:** Worker session completes and calls send_cb at the exact moment the bridge clears hibernation state but before re-registering Telegram callbacks
**Data prerequisite:** `bridge:hibernation:state` must be cleared before `register_callbacks()` runs, so new sends don't go to buffer
**State prerequisite:** Telegram client must be fully connected and authorized before hibernation state is cleared
**Mitigation:** Clear hibernation state only after `register_callbacks()` completes in `run_bridge()`. Brief window where a message might double-buffer is acceptable — drain is idempotent.

### Race 2: Multiple bridge restarts between auth failure and hibernation write
**Location:** `bridge/telegram_bridge.py` startup, `data/bridge-hibernation.json`
**Trigger:** Watchdog restarts bridge before `enter_hibernation()` finishes writing the state file
**Data prerequisite:** Hibernation file must be written before bridge exits
**State prerequisite:** Write must be atomic (temp file + `os.replace`) to avoid corrupt JSON on read
**Mitigation:** Use atomic write pattern (already established for `data/flood-backoff` and `data/last_connected`). If file is corrupt/missing on watchdog check, treat as non-hibernating (safe default).

## No-Gos (Out of Scope)

- Anthropic API failures and worker hibernation — handled by #839
- Re-auth automation without human input
- Multi-machine buffer coordination
- SMS/email notification (macOS desktop notification is sufficient)
- Replay of messages received by Telegram while bridge was down (message catchup already exists in `bridge/catchup.py`)
- Buffer persistence beyond 24 hours

## Update System

No update system changes required — this feature adds new files and modifies existing bridge/agent modules. The `/update` skill's existing pattern (git pull + restart) handles propagation. No new environment variables, config files, or external services are required. The Redis keys created by this feature are ephemeral and require no migration.

## Agent Integration

No agent integration required — this is a bridge-internal resilience change. The agent (Claude) is unaware of hibernation state; it simply completes sessions and delivers output through the normal OutputHandler protocol. The `BufferedOutputHandler` is invisible to the agent — it satisfies the same `OutputHandler` protocol. No MCP server changes, no `.mcp.json` changes.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` — add "Bridge Hibernation" section covering auth-vs-connectivity distinction, hibernation state file, buffer mechanics, and drain-on-reconnect
- [ ] Add entry to `docs/features/README.md` index table if a standalone `bridge-hibernation.md` is warranted (decide during implementation — may be a section in self-healing doc)
- [ ] Code comments in `bridge/hibernation.py` explaining state machine transitions
- [ ] Update `CLAUDE.md` Quick Commands table: add `cat data/bridge-hibernation.json` as a status check command

## Success Criteria

- [ ] Bridge detects auth expiry specifically (not generic crash) and enters hibernation state — verified by unit test mocking `is_user_authorized()` returning False
- [ ] Bridge does NOT enter hibernation for transient network errors — existing retry loop handles them
- [ ] Worker continues processing sessions during bridge hibernation — sessions complete and output is buffered
- [ ] Output buffer (Redis list) accumulates messages during hibernation — verified by integration test
- [ ] Buffer is drained and delivered to Telegram on reconnect — verified by integration test
- [ ] macOS desktop notification is sent on auth-expiry hibernation — verified by unit test (mocked `osascript`)
- [ ] Watchdog does not restart-loop on auth-expired hibernation — reads state file and suppresses
- [ ] Buffered messages are not lost if Redis restarts before drain (Redis AOF/RDB persistence — out of scope to configure, but buffer is best-effort and this is acceptable)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `data/bridge-hibernation.json` is human-readable with `reason`, `timestamp`, and `instructions`

## Team Orchestration

### Team Members

- **Builder (hibernation-core)**
  - Name: hibernation-builder
  - Role: Implement `bridge/hibernation.py`, auth detection in bridge startup, watchdog hibernation awareness
  - Agent Type: builder
  - Resume: true

- **Builder (buffer)**
  - Name: buffer-builder
  - Role: Implement `BufferedOutputHandler`, Redis push/drain logic, integration with `agent_session_queue.py` fallback
  - Agent Type: async-specialist
  - Resume: true

- **Validator (core)**
  - Name: core-validator
  - Role: Verify hibernation state machine, watchdog suppression, notification
  - Agent Type: validator
  - Resume: true

- **Validator (buffer)**
  - Name: buffer-validator
  - Role: Verify buffer push/drain, Redis key management, TTL, order preservation
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update bridge-self-healing.md, CLAUDE.md, README
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build hibernation core
- **Task ID**: build-hibernation-core
- **Depends On**: none
- **Validates**: `tests/unit/test_hibernation.py` (create)
- **Informed By**: spike-1 (auth error taxonomy), spike-3 (notification approach)
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/hibernation.py` with `enter_hibernation(reason)`, `exit_hibernation()`, `is_hibernating()`, `get_hibernation_state()` functions
- Write `data/bridge-hibernation.json` atomically (temp file + `os.replace`) with `reason`, `timestamp`, `instructions` fields
- Send macOS desktop notification via `osascript` (best-effort, silently skip if unavailable)
- Add explicit catch of `AuthKeyError`, `AuthKeyUnregisteredError`, `UnauthorizedError` in `bridge/telegram_bridge.py` startup — call `enter_hibernation("auth_expired")` then exit cleanly
- Add `is_user_authorized() == False` path → `enter_hibernation("auth_expired")` (already at startup, just route to hibernation instead of raw `SystemExit(1)`)
- Add connection-exhausted path → `enter_hibernation("connection_exhausted")` after max retries

### 2. Build output buffer
- **Task ID**: build-buffer
- **Depends On**: none
- **Validates**: `tests/unit/test_output_handler.py` (update), `tests/integration/test_buffer_drain.py` (create)
- **Informed By**: spike-2 (Redis list approach)
- **Assigned To**: buffer-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `BufferedOutputHandler` class to `agent/output_handler.py` — on `send()`, check Redis `bridge:hibernation:state`; if set, LPUSH serialized message to `bridge:output_buffer` with 24h TTL; trim list to 1000 items
- Fall through to `FileOutputHandler` if Redis unavailable (fail-safe)
- Update `agent_session_queue.py` fallback path (around line 2612) to use `BufferedOutputHandler` instead of `FileOutputHandler` directly
- Add `drain_output_buffer(client)` to `bridge/hibernation.py` — async RPOP loop, send via `send_response_with_files()`, skip and log on per-message error
- Call `drain_output_buffer(client)` as `asyncio.create_task()` immediately after successful auth in `run_bridge()`, before normal event handling begins
- Call `exit_hibernation()` after drain task is scheduled

### 3. Build watchdog hibernation awareness
- **Task ID**: build-watchdog
- **Depends On**: build-hibernation-core
- **Validates**: `tests/unit/test_watchdog_hibernation.py` (create)
- **Informed By**: spike-1, spike-3
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: false
- In `monitoring/bridge_watchdog.py` Level 1 logic, read `data/bridge-hibernation.json` before deciding to restart
- If `reason == "auth_expired"`: skip restart, increment `suppressed_restart_count` in file, log prominently
- After 3 suppressed restarts, send escalated Telegram alert (Level 5 path) — or if Telegram down, rely on desktop notification already sent
- If `reason == "connection_exhausted"`: allow restart (connection issues may self-resolve)

### 4. Validate hibernation core
- **Task ID**: validate-hibernation
- **Depends On**: build-hibernation-core, build-watchdog
- **Assigned To**: core-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_hibernation.py tests/unit/test_watchdog_hibernation.py -v`
- Verify `data/bridge-hibernation.json` is valid JSON with required fields
- Verify watchdog suppresses restart on `auth_expired` reason
- Verify bridge exits cleanly (not with exception traceback) on auth failure

### 5. Validate buffer
- **Task ID**: validate-buffer
- **Depends On**: build-buffer
- **Assigned To**: buffer-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_output_handler.py tests/integration/test_buffer_drain.py -v`
- Verify `BufferedOutputHandler` satisfies `OutputHandler` protocol
- Verify Redis TTL is set on `bridge:output_buffer`
- Verify drain delivers messages in FIFO order
- Verify empty buffer drain is a no-op

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hibernation, validate-buffer
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Add "Bridge Hibernation" section to `docs/features/bridge-self-healing.md` covering auth detection, state machine, buffer mechanics, drain-on-reconnect, watchdog suppression
- Add `cat data/bridge-hibernation.json` to CLAUDE.md Quick Commands table
- Update `docs/features/README.md` index if bridge-hibernation warrants its own entry

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: core-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ tests/integration/ -q`
- Run lint: `python -m ruff check .`
- Run format check: `python -m ruff format --check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Hibernation module exists | `python -c "from bridge.hibernation import enter_hibernation, exit_hibernation, drain_output_buffer; print('ok')"` | output contains ok |
| BufferedOutputHandler exists | `python -c "from agent.output_handler import BufferedOutputHandler; print('ok')"` | output contains ok |
| Auth errors importable | `python -c "from telethon.errors import AuthKeyError, AuthKeyUnregisteredError, UnauthorizedError; print('ok')"` | output contains ok |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

1. **Buffer drain timing**: Should drain happen as a background task (new messages handled immediately) or synchronously (no new messages until drain completes)? Background task is proposed — does this risk delivering buffered messages interleaved with new ones?
2. **`valor-service.sh status` hibernation awareness**: Should the status command surface hibernation state explicitly, or is `cat data/bridge-hibernation.json` sufficient for the operator?
