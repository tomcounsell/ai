---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/497
last_comment_id:
---

# PM Telegram Tool: ChatSession Composes Its Own Messages

## Problem

The PM persona (ChatSession) never writes its own Telegram messages. All output goes through the summarizer — a Haiku-powered compressor that rewrites PM output into bullet-point format. The result sounds like a CI bot, not a project manager.

**Current behavior:**
ChatSession returns text -> bridge captures it in `send_to_chat()` -> `send_response_with_files()` in `bridge/response.py` runs it through `summarize_response()` -> structured bullets are sent to Telegram. The PM persona has communication guidelines but never gets to apply them because the summarizer overrides its voice.

**Desired outcome:**
ChatSession composes and sends its own Telegram messages via a tool, with full control over tone, content, and timing. The summarizer becomes a safety net: if the PM ends a session without self-messaging, the summarizer fires as fallback. PM messages read like a project manager talking to a stakeholder, not a build log.

## Prior Art

- **Issue #274 / PR #275**: Semantic Session Routing — added structured summarizer with context-aware routing. Established the current summarizer architecture.
- **Issue #309**: Observer Agent replacement — replaced auto-continue/summarizer with stage-aware SDLC steerer. Shifted SDLC intelligence from bridge to ChatSession.
- **PR #248**: SDLC summary improvements — removed checkboxes, embedded issue numbers. Tuned summarizer output format.
- **PR #187**: Summarizer overhaul — always summarize, SDLC templates. Established summarizer as the sole message author.
- **PR #242**: Simplify summarizer — removed echo, always summarize.
- **PR #456**: Summarizer evidence hardening — persona gate and evidence requirements.
- **Issue #459**: SDLC Redesign — simplified pipeline, established ChatSession/DevSession split. Current architecture.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #187 | Added SDLC templates to summarizer | Improved format but kept the rewrite-all architecture |
| PR #248 | Removed checkboxes, embedded issue numbers | Cosmetic — still a Haiku rewrite of PM output |
| PR #275 | Structured summarizer with semantic routing | Better routing decisions but PM still doesn't author its own messages |
| PR #242 | Simplified summarizer to always run | Doubled down on the summarizer-as-author pattern |
| PR #456 | Evidence hardening for summarizer | Made summarizer smarter but didn't address the root architecture |

**Root cause pattern:** Every iteration tuned the summarizer's output quality, but none questioned whether the summarizer should be the sole author. The PM persona has communication intelligence that is discarded because its output is always rewritten.

## Data Flow

### Current Flow (summarizer-as-author)
1. **Entry point**: Human sends Telegram message
2. **Bridge**: Routes to ChatSession via `_execute_job()` in `agent/job_queue.py`
3. **ChatSession**: Processes message, orchestrates DevSessions, returns text output
4. **Nudge loop**: `send_to_chat()` classifies output via `classify_nudge_action()` — decides deliver/nudge
5. **Bridge send callback**: `_send()` in `telegram_bridge.py` L1448 calls `send_response_with_files()`
6. **Summarizer**: `bridge/response.py` L396-429 runs `summarize_response()` which rewrites the text
7. **Telegram**: Summarized text sent via `send_markdown()` to Telegram

### Proposed Flow (PM-as-author)
1. **Entry point**: Human sends Telegram message
2. **Bridge**: Routes to ChatSession (unchanged)
3. **ChatSession**: Processes message, composes Telegram messages, calls `send_telegram_message` tool which writes to Redis queue
4. **Bridge message relay**: Async loop in bridge reads Redis queue, sends to Telegram via Telethon, records sent message IDs on AgentSession
5. **Nudge loop**: `send_to_chat()` checks whether PM self-messaged during session. If yes, skip summarizer and only set emoji reaction. If no, fall through to summarizer as safety net.
6. **Telegram**: PM-authored messages already delivered; summarizer only fires as fallback

## Architectural Impact

- **New dependencies**: Redis pub/sub or list for IPC between ChatSession subprocess and bridge
- **Interface changes**: New `pm_sent_message_ids` field on AgentSession; new Redis key pattern for message queue
- **Coupling**: Slightly increases coupling between ChatSession and bridge (via shared Redis contract), but reduces the summarizer's responsibilities
- **Data ownership**: Message composition shifts from bridge/summarizer to ChatSession. Bridge retains delivery responsibility.
- **Reversibility**: High — remove the tool from ChatSession's environment and the system falls back to summarizer-only behavior automatically

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (validate IPC mechanism choice, review PM persona guidelines)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — this work uses existing Redis infrastructure and Telethon client already available in the bridge process.

## Solution

### Key Elements

- **Redis message queue**: IPC channel between ChatSession subprocess and bridge process. ChatSession writes messages; bridge reads and sends via Telethon.
- **`send_telegram_message` Bash tool**: ChatSession calls a Python script that pushes messages to the Redis queue. Pre-configured with `chat_id` and `reply_to` via environment variables.
- **Bridge message relay**: Async task in bridge that polls the Redis queue and sends messages through the existing Telethon client.
- **AgentSession `pm_sent_message_ids`**: ListField tracking Telegram message IDs sent by the PM during a session. Bridge populates this after each successful send.
- **Summarizer bypass**: Single check point in `send_response_with_files()` — drains any pending Redis queue entries, then checks `pm_sent_message_ids`. If non-empty, skip summarizer and only apply emoji reaction. If summarizer fires as fallback, sets `summarizer_delivered` flag on the session so the relay skips any late-arriving queue entries to prevent duplicate messages.

### Flow

**ChatSession wants to send message** -> calls `python tools/send_telegram.py "message text"` -> script reads `TELEGRAM_CHAT_ID` and `TELEGRAM_REPLY_TO` from env -> pushes `{chat_id, reply_to, text, session_id}` to Redis list `telegram:outbox:{session_id}` -> returns immediately

**Bridge relay loop** -> BLPOP on `telegram:outbox:pending` index list -> gets session_id, reads from `telegram:outbox:{session_id}` -> checks `summarizer_delivered` flag (if set, discard entry to prevent dupes) -> sends via Telethon `send_markdown()` -> records sent Telegram message ID on AgentSession `pm_sent_message_ids` -> deletes processed queue entry

**Session completes** -> `send_response_with_files()` drains pending Redis queue (waits up to 2s), checks AgentSession `pm_sent_message_ids` -> if non-empty, skip summarizer, only set emoji reaction -> if empty, fire summarizer as safety net and set `summarizer_delivered` flag to prevent late relay dupes

### Technical Approach

- **IPC via Redis list**: ChatSession runs as a Claude Code subprocess — it cannot access the bridge's Telethon client directly. Redis lists provide a reliable, ordered queue. The bridge already has a Redis connection.
- **Environment variable injection**: `sdk_client.py` already injects `VALOR_SESSION_ID`, `JOB_ID`, `CHAT_ID` etc. Add `TELEGRAM_CHAT_ID` and `TELEGRAM_REPLY_TO` for ChatSession sessions.
- **Bash-callable tool**: ChatSession has Bash access. A simple Python script in `tools/send_telegram.py` avoids MCP server complexity. The script uses the existing Redis connection pattern from `tools/`.
- **Relay as asyncio task**: The bridge's event loop already runs background tasks (calendar heartbeat, job queue). Add a relay task that processes the outbox queue.
- **Linkification and formatting**: The `send_telegram.py` tool handles PR/Issue reference linkification (e.g., `PR #42` -> `[PR #42](url)`). Extract `_linkify_references()` from `bridge/summarizer.py` to `bridge/formatting.py`, refactored to accept `github_org` and `github_repo` as parameters instead of a session object. The tool reads `GITHUB_ORG` and `GITHUB_REPO` from environment variables (injected alongside `TELEGRAM_CHAT_ID`).
- **Length enforcement**: Telegram's 4096 char limit enforced in the tool before queueing.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/send_telegram.py` Redis connection failure — must log error and return non-zero exit code (ChatSession sees tool failure)
- [ ] Bridge relay Telethon send failure — must log error, NOT delete from queue (retry on next poll)
- [ ] AgentSession save failure for `pm_sent_message_ids` — non-fatal, log warning, still deliver message

### Empty/Invalid Input Handling
- [ ] Empty message text to `send_telegram.py` — reject with clear error message
- [ ] Missing `TELEGRAM_CHAT_ID` env var — reject with error explaining the tool is only available in ChatSession context
- [ ] Malformed Redis queue entry — skip and log, don't crash relay loop

### Error State Rendering
- [ ] If `send_telegram.py` fails, ChatSession sees a tool error and can fall back to returning text (which triggers summarizer)
- [ ] If bridge relay fails to send all queued messages before session ends, summarizer fires as fallback

## Test Impact

- [ ] `tests/unit/test_summarizer.py::TestSummarizeResponse` — UPDATE: add test cases for summarizer bypass when `pm_sent_message_ids` is non-empty
- [ ] `tests/unit/test_nudge_loop.py` — UPDATE: add test cases for the new "pm already messaged" path in `classify_nudge_action()` or the delivery logic
- [ ] `tests/e2e/test_nudge_loop.py` — UPDATE: verify end-to-end flow with PM self-messaging
- [ ] `tests/unit/test_sdk_client.py` — UPDATE: verify `TELEGRAM_CHAT_ID` and `TELEGRAM_REPLY_TO` env var injection for chat sessions

## Rabbit Holes

- **Building a full MCP server for Telegram**: Overkill. ChatSession has Bash access; a Python script in `tools/` is simpler and more maintainable. MCP server would need registration, health checks, and lifecycle management.
- **Real-time streaming of PM messages**: The relay can use simple polling (100ms interval) rather than pub/sub. The latency difference is negligible for Telegram delivery.
- **Removing the summarizer entirely**: Keep it as safety net. Complete removal creates a risk of silent sessions where the PM crashes without sending a message.
- **Multiple message formats (rich media, buttons, etc.)**: Text messages with markdown are sufficient for v1. File attachments and interactive elements are a separate concern.
- **Bi-directional IPC**: The PM tool only needs to send. Reading replies is already handled by the bridge's incoming message handler.

## Risks

### Risk 1: Redis queue message loss
**Impact:** PM sends a message but it never reaches Telegram. User sees no response.
**Mitigation:** Use Redis RPUSH/BLPOP for reliable queue semantics. Bridge relay uses LPOP with re-push on send failure (at-least-once delivery). Summarizer safety net catches cases where all queued messages fail.

### Risk 2: Race between PM self-message and session completion
**Impact:** PM queues a message but the session completes before the bridge relay processes it. Summarizer fires because `pm_sent_message_ids` is empty, leading to duplicate messages.
**Mitigation:** `send_response_with_files()` drains the queue (up to 2s) before checking. If summarizer fires as fallback, `summarizer_delivered` flag is set on AgentSession so the relay discards any late-arriving entries for that session. This eliminates the duplicate message scenario.

### Risk 3: ChatSession uses tool incorrectly or not at all
**Impact:** PM returns raw text without calling the tool, triggering the summarizer.
**Mitigation:** This is actually the desired fallback behavior. The PM persona instructions encourage tool use but the summarizer catches the case gracefully. Over time, prompt iteration improves tool usage.

## Race Conditions

### Race 1: Queue drain vs. completion check
**Location:** `bridge/response.py` `send_response_with_files()` and bridge relay task
**Trigger:** Session completes, bypass check runs before relay has processed the queue
**Data prerequisite:** All entries in `telegram:outbox:{session_id}` must be processed before bypass decision
**State prerequisite:** AgentSession `pm_sent_message_ids` must reflect all sent messages
**Mitigation:** `send_response_with_files()` actively drains the queue (poll 100ms intervals, up to 2s). After timeout, if queue still non-empty, fall through to summarizer and set `summarizer_delivered` flag. Relay checks this flag before sending, discarding entries for sessions already handled by the summarizer — preventing duplicate messages.

### Race 2: Concurrent relay processing
**Location:** Bridge relay task
**Trigger:** Multiple bridge instances processing the same outbox key
**Data prerequisite:** Each message should be sent exactly once
**State prerequisite:** N/A (single bridge instance per deployment)
**Mitigation:** Single bridge process. LPOP is atomic in Redis — even if multiple consumers existed, each message is popped exactly once.

## No-Gos (Out of Scope)

- DevSession Telegram access — only ChatSession gets the tool
- Rich media messages (photos, documents, buttons) — text + markdown only for v1
- Message editing/deletion — PM can only send new messages
- Read receipts or delivery confirmation back to ChatSession — fire-and-forget from PM's perspective
- Summarizer removal — it stays as fallback
- PM persona rewrite — only add tool usage guidance, no personality overhaul

## Update System

No update system changes required — this feature is purely internal to the bridge and agent SDK. No new dependencies, config files, or migration steps needed for remote deployments.

## Agent Integration

- **New tool**: `tools/send_telegram.py` — a Bash-callable Python script that pushes messages to the Redis outbox queue. ChatSession calls it via Bash tool.
- **No MCP server needed**: The tool is a standalone script, not an MCP server. ChatSession invokes it as `python tools/send_telegram.py "message text"`.
- **Bridge modification**: `bridge/telegram_bridge.py` needs a new async relay task that processes the Redis outbox queue. This runs alongside the existing job queue consumer.
- **Environment variable injection**: `agent/sdk_client.py` needs to inject `TELEGRAM_CHAT_ID` and `TELEGRAM_REPLY_TO` for chat-type sessions.
- **Integration test**: Verify that calling `tools/send_telegram.py` with valid env vars queues a message in Redis, and the bridge relay picks it up.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/pm-telegram-tool.md` describing the PM self-messaging architecture, IPC mechanism, and fallback behavior
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Code comments on Redis queue contract (key pattern, message format, TTL)
- [ ] Docstrings for `tools/send_telegram.py` and bridge relay function
- [ ] Update `bridge/response.py` summarizer bypass with inline explanation

## Success Criteria

- [ ] ChatSession has a `send_telegram_message` tool (via Bash + `tools/send_telegram.py`) pre-configured with chat_id and reply_to via environment variables
- [ ] ChatSession composes and sends its own Telegram messages using the tool
- [ ] Bridge relay task processes Redis outbox queue and sends messages via Telethon
- [ ] Bridge skips summarizer when PM has already sent a message via tool during the session
- [ ] Summarizer retained as fallback: fires when PM ends session without self-messaging
- [ ] PM persona updated with stakeholder communication guidelines (no stage names, no play-by-play)
- [ ] Tool handles formatting: markdown, PR/Issue linkification, 4096 char limit
- [ ] Existing test coverage for summarizer/nudge loop updated for new bypass path
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (telegram-tool)**
  - Name: telegram-tool-builder
  - Role: Implement `tools/send_telegram.py`, env var injection in `sdk_client.py`, PM persona updates
  - Agent Type: builder
  - Resume: true

- **Builder (bridge-relay)**
  - Name: bridge-relay-builder
  - Role: Implement bridge relay task, summarizer bypass in `bridge/response.py`, AgentSession field addition
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end flow: tool -> Redis -> relay -> Telegram, plus summarizer fallback
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add AgentSession field and Redis queue contract
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session.py (create)
- **Assigned To**: bridge-relay-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `pm_sent_message_ids` ListField to AgentSession in `models/agent_session.py`
- Add `summarizer_delivered` BooleanField (default False) to AgentSession — set when summarizer fires as fallback, checked by relay to prevent duplicate messages
- Define Redis key pattern: `telegram:outbox:{session_id}` for per-session message queue, `telegram:outbox:pending` as index list of session_ids with pending messages
- JSON message format: `{chat_id, reply_to, text, session_id, timestamp}`
- Add helper methods on AgentSession: `record_pm_message(msg_id)`, `has_pm_messages() -> bool`, `mark_summarizer_delivered()`

### 2. Build `tools/send_telegram.py`
- **Task ID**: build-tool
- **Depends On**: build-model
- **Validates**: tests/unit/test_send_telegram.py (create)
- **Assigned To**: telegram-tool-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/send_telegram.py` that reads `TELEGRAM_CHAT_ID`, `TELEGRAM_REPLY_TO`, `VALOR_SESSION_ID`, `GITHUB_ORG`, `GITHUB_REPO` from env
- Accepts message text as CLI argument
- Pushes to Redis list `telegram:outbox:{session_id}` AND appends session_id to `telegram:outbox:pending` index list
- Extract `_linkify_references()` from `bridge/summarizer.py` to `bridge/formatting.py`, refactored to accept `github_org` and `github_repo` as parameters (not a session object)
- Update `bridge/summarizer.py` to call the extracted function from `bridge/formatting.py`
- Apply linkification and 4096 char truncation before queueing
- Return exit code 0 on success, non-zero on failure with stderr message

### 3. Inject environment variables for ChatSession
- **Task ID**: build-env-injection
- **Depends On**: none
- **Validates**: tests/unit/test_sdk_client.py (update)
- **Assigned To**: telegram-tool-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sdk_client.py` `_build_options()`, inject `TELEGRAM_CHAT_ID`, `TELEGRAM_REPLY_TO`, `GITHUB_ORG`, and `GITHUB_REPO` for chat-type sessions
- Source `TELEGRAM_CHAT_ID` and `TELEGRAM_REPLY_TO` from the job's `chat_id` and `message_id` fields
- Source `GITHUB_ORG` and `GITHUB_REPO` from `get_project_config()` using the session's `project_key`

### 4. Build bridge relay task
- **Task ID**: build-relay
- **Depends On**: build-model
- **Validates**: tests/unit/test_bridge_relay.py (create)
- **Assigned To**: bridge-relay-builder
- **Agent Type**: builder
- **Parallel**: true
- Create async relay function in `bridge/telegram_relay.py` that BLPOP on `telegram:outbox:pending` index list (O(1) instead of wildcard scan)
- For each session_id popped: read from `telegram:outbox:{session_id}`, check `summarizer_delivered` flag on AgentSession (if set, discard to prevent duplicate messages)
- Send via Telethon `send_markdown()`, record message ID on AgentSession, delete queue entry
- Handle send failures: log error, re-push to queue tail, backoff
- Track `last_relay_processed_at` timestamp; bridge watchdog alerts if relay hasn't processed in >60s while queue entries exist
- Start relay task in bridge's main event loop alongside job queue consumer

### 5. Implement summarizer bypass
- **Task ID**: build-bypass
- **Depends On**: build-model, build-relay
- **Validates**: tests/unit/test_summarizer.py (update), tests/unit/test_nudge_loop.py (update)
- **Assigned To**: bridge-relay-builder
- **Agent Type**: builder
- **Parallel**: false
- **Single check point** in `bridge/response.py` `send_response_with_files()`: before summarizer call, drain any pending Redis queue entries for `telegram:outbox:{session_id}` (poll 100ms intervals, up to 2s timeout)
- Re-read AgentSession after drain, check `has_pm_messages()`
- If PM self-messaged: skip summarizer, skip text send, only return True (emoji reaction handled separately)
- If PM did NOT self-message: fire summarizer as fallback, then set `summarizer_delivered` flag on AgentSession so the relay discards any late-arriving queue entries (prevents duplicate messages)
- No bypass logic in `agent/job_queue.py` `send_to_chat()` — all bypass decisions consolidated in `send_response_with_files()`

### 6. Update PM persona with communication guidelines
- **Task ID**: build-persona
- **Depends On**: build-tool
- **Validates**: manual review
- **Assigned To**: telegram-tool-builder
- **Agent Type**: builder
- **Parallel**: true
- Update ChatSession system prompt via `load_pm_system_prompt()` or the ChatSession prompt block in `agent/sdk_client.py` `_create_options()` with tool usage instructions
- Add guidelines: use `send_telegram_message` for stakeholder communication, never expose SDLC stage names, write in business terms
- Add instruction: if you don't call the tool, your return value will be summarized and sent automatically (fallback behavior)

### 7. Update existing tests
- **Task ID**: build-tests
- **Depends On**: build-bypass, build-tool
- **Validates**: tests/unit/test_summarizer.py, tests/unit/test_nudge_loop.py, tests/unit/test_sdk_client.py
- **Assigned To**: integration-validator
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `tests/unit/test_summarizer.py` with bypass path tests
- Update `tests/unit/test_nudge_loop.py` with PM self-message detection tests
- Update `tests/unit/test_sdk_client.py` with env var injection tests
- Create `tests/unit/test_send_telegram.py` for the tool
- Create `tests/unit/test_bridge_relay.py` for the relay task

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-persona
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify summarizer bypass works when `pm_sent_message_ids` is non-empty
- Verify summarizer fallback works when `pm_sent_message_ids` is empty
- Verify `tools/send_telegram.py` queues correctly with proper env vars
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Tool exists | `test -f tools/send_telegram.py` | exit code 0 |
| Relay exists | `test -f bridge/telegram_relay.py` | exit code 0 |
| Model field exists | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'pm_sent_message_ids')"` | exit code 0 |
| Env injection | `grep -q TELEGRAM_CHAT_ID agent/sdk_client.py` | exit code 0 |

## Critique Results

| Severity | Critic | Concern | Resolution |
|----------|--------|---------|------------|
| BLOCKER | Skeptic, Operator | Bypass check split across two files (`response.py` and `job_queue.py`) with no coordination | Consolidated to single check point in `send_response_with_files()` |
| BLOCKER | Skeptic, Adversary | `_linkify_references()` needs session object; tool script has no session | Refactored to accept `github_org`/`github_repo` params; injected as env vars |
| CONCERN | Adversary, Operator | 2s drain timeout may cause duplicate messages (summarizer + late relay) | Added `summarizer_delivered` flag on AgentSession; relay checks before sending |
| CONCERN | Operator, Simplifier | `telegram:outbox:*` wildcard scan is O(N) | Replaced with `telegram:outbox:pending` index list, relay uses BLPOP (O(1)) |
| CONCERN | Archaeologist | Task 1 references non-existent `test_agent_session.py` | Changed to `(create)` — new file |
| CONCERN | Skeptic | Task 6 references wrong line numbers in `sdk_client.py` | Replaced with function name references |
| CONCERN | Operator | No health monitoring for relay task | Added `last_relay_processed_at` tracking; watchdog alerts if stale |
| NIT | structural | `validate-all` has no specific validation command | Acceptable — validator agent runs Verification table commands |
| NIT | structural | Some file paths unqualified | Minor; builders resolve from context |

---

## Open Questions

None — all resolved.

## Resolved Questions

1. **Queue TTL**: No TTL. The relay drains pending entries on restart after a crash. TTL would risk expiring messages that should still be delivered. Orphaned keys from malformed session IDs are rare enough to handle via periodic cleanup, not TTL.
2. **Message ordering guarantee**: No strict ordering required. FIFO is best-effort but retries can reorder — acceptable for PM communication.
3. **Rate limiting**: No rate limits. Rely on PM persona guidance for message frequency.
