---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/726
last_comment_id:
---

# Fix valor-telegram send — route through Redis relay

## Problem

`valor-telegram send` is broken in two independent ways, making the CLI unusable for sending messages.

**Current behavior:**
1. **Session lock conflict** -- `cmd_send()` creates a Telethon client using the same SQLite session file (`data/valor_bridge`) as the bridge. When the bridge is running (nearly always), the session file is locked: `sqlite3.OperationalError: database is locked`.
2. **No forum/topic support** -- The CLI calls `client.send_message(entity, text)` with no `reply_to` parameter. Telegram groups with topics enabled reject plain sends with `CHAT_SEND_PLAIN_FORBIDDEN`.

Additional missing capabilities vs the bridge relay path: no markdown formatting, no message length validation, no linkification, no dead-letter persistence on failure.

**Desired outcome:**
`valor-telegram send --chat "Any Chat" "any message"` works reliably regardless of whether the bridge is running, with markdown formatting, forum support, and long message truncation handled transparently.

## Prior Art

- **Issue #641** (closed): Unified the PM agent's send interface (`tools/send_telegram.py`) to support files via Redis queue. Fixed the agent-facing tool but did not touch `valor-telegram send`.
- **PR #642** (merged): Added `--file` support to the PM send tool. Confirmed the Redis queue + relay pattern works for file attachments.
- **PR #527** (merged): PM Telegram tool -- ChatSession composes its own messages via Redis outbox queue.
- **Issue #698** (closed): Fixed relay re-queuing undeliverable messages infinitely -- added retry limit and dead-lettering.

## Data Flow

### Current (broken) flow:
1. **Entry point**: `valor-telegram send --chat "X" "message"` -> `cmd_send()`
2. **Resolution**: `resolve_chat()` maps chat name to numeric ID
3. **Telethon client**: `_telethon_client()` opens `data/valor_bridge` SQLite session (CONFLICT with bridge)
4. **Send**: `client.send_message(entity, text)` -- no reply_to, no markdown, no length check
5. **Output**: Success or `sqlite3.OperationalError`

### Proposed flow:
1. **Entry point**: `valor-telegram send --chat "X" "message"` -> `cmd_send()`
2. **Resolution**: `resolve_chat()` maps chat name to numeric ID (unchanged)
3. **Linkification**: Apply `linkify_references()` to message text
4. **Length check**: Truncate at 4096 chars at sentence boundary
5. **Queue**: Push to `telegram:outbox:cli-{timestamp}` Redis list (same format as `send_telegram.py`)
6. **Relay**: Bridge relay (`bridge/telegram_relay.py`) picks up and sends with markdown, reply_to, retry
7. **Output**: "Message queued" confirmation

## Architectural Impact

- **New dependencies**: None -- Redis and relay already exist
- **Interface changes**: New `--reply-to` CLI flag on `valor-telegram send`; `--async` flag for fire-and-forget
- **Coupling**: Increases coupling to Redis/relay infrastructure, but this is the proven send path
- **Data ownership**: Send responsibility moves from CLI-direct to relay (matches agent send path)
- **Reversibility**: Easy -- revert `cmd_send()` to direct Telethon if needed

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- Redis and relay infrastructure already exist and are stable.

## Solution

### Key Elements

- **Queue-based send**: `cmd_send()` pushes to Redis outbox instead of creating a Telethon client
- **CLI argument mapping**: Map `--chat`, `--reply-to`, `--file` to the relay payload format
- **Delivery confirmation**: Brief poll for relay delivery (default), or fire-and-forget with `--async`

### Flow

**CLI invocation** -> resolve chat name -> linkify + truncate -> push Redis outbox -> relay picks up -> Telethon send -> confirmation

### Technical Approach

1. **Rewrite `cmd_send()`** to build a relay-compatible payload and push to Redis, mirroring `send_telegram.py`'s `send_message()` but sourcing chat_id/reply_to from CLI args instead of env vars
2. **Add `--reply-to` flag** to the send subparser for forum group support
3. **Use synthetic session_id** (`cli-{unix_timestamp}`) for the queue key since CLI sends have no session context
4. **Apply `_linkify_text()`** and length truncation before queueing (same as `send_telegram.py`)
5. **Remove direct Telethon usage** from `cmd_send()` entirely -- no fallback to direct send. If Redis/relay is unavailable, error clearly.
6. **Keep `_telethon_client()` and `_fetch_from_telegram_api()`** for the `read` command's API fallback (read operations don't conflict because they only hold the SQLite lock briefly)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Test Redis connection failure in `cmd_send()` -- should print clear error and exit 1
- [ ] Test resolve_chat returning None -- should print error with "use chats to list"

### Empty/Invalid Input Handling
- [ ] Test empty message with no file -- should error
- [ ] Test non-existent file path -- should error before queueing
- [ ] Test message over 4096 chars -- should truncate at sentence boundary

### Error State Rendering
- [ ] All error paths print to stderr and return non-zero exit code
- [ ] "Message queued" confirmation goes to stdout on success

## Test Impact

No existing tests affected -- `cmd_send()` has zero test coverage today. All tests for this fix are new.

## Rabbit Holes

- **Delivery confirmation polling**: Tempting to poll Redis until the relay processes the message and confirm delivery. This adds complexity (how long to wait? what if relay is slow?) for marginal value. Start with fire-and-forget queue semantics; add optional `--wait` later if needed.
- **Separate Telethon session for CLI**: Creating a second session file (e.g., `data/valor_cli`) would avoid the lock conflict but duplicates auth management and doesn't give us markdown/forum/retry for free. The relay path is strictly better.
- **Fallback to direct Telethon when bridge is down**: Adds complexity and a code path that rarely runs (bridge is nearly always up). Error clearly instead.

## Risks

### Risk 1: Bridge/relay not running
**Impact:** CLI sends queue to Redis but are never delivered. Messages expire after 1 hour TTL.
**Mitigation:** Print warning "Note: delivery requires the bridge relay to be running" after queueing. User can check with `valor-service.sh status`.

### Risk 2: Chat name resolution for forum groups
**Impact:** User may not know the reply_to message ID required for forum sends.
**Mitigation:** Make `--reply-to` optional. Without it, the relay will attempt to send anyway -- it will fail for forum groups but succeed for normal chats. Error message from Telegram is clear enough.

## Race Conditions

No race conditions identified -- the CLI pushes a single atomic RPUSH to Redis and exits. The relay processes queues sequentially with LPOP.

## No-Gos (Out of Scope)

- Delivery confirmation/polling (future enhancement)
- Fallback to direct Telethon when relay is down
- Changes to the `read` or `chats` subcommands
- Changes to `tools/send_telegram.py` (agent-facing tool)
- Changes to `bridge/telegram_relay.py` (relay is already correct)

## Update System

No update system changes required -- this modifies only `tools/valor_telegram.py` which is already installed as a console script via pyproject.toml. `uv sync` on update handles it.

## Agent Integration

No agent integration required -- `valor-telegram` is a human-facing CLI tool. The agent uses `tools/send_telegram.py` for sending, which already works correctly via the Redis queue path.

## Documentation

- [ ] Update `CLAUDE.md` to note `valor-telegram send` supports `--reply-to` for forum groups
- [ ] Add inline code comments on the queue payload format in `cmd_send()`

## Success Criteria

- [ ] `valor-telegram send --chat "Agent Builders Chat" "test message"` works while bridge is running
- [ ] `valor-telegram send --chat "Forum Group" --reply-to 123 "message"` works for forum groups
- [ ] Messages sent via CLI have markdown formatting applied (via relay)
- [ ] Messages over 4096 chars are truncated before queueing
- [ ] `--file` attachments work through the queue path
- [ ] No `sqlite3.OperationalError` when bridge is running
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (send-fix)**
  - Name: send-fixer
  - Role: Rewrite cmd_send() to use Redis queue
  - Agent Type: builder
  - Resume: true

- **Validator (send-fix)**
  - Name: send-validator
  - Role: Verify queue payloads match relay expectations
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rewrite cmd_send() to queue via Redis
- **Task ID**: build-send-queue
- **Depends On**: none
- **Validates**: tests/unit/test_valor_telegram_send.py (create)
- **Assigned To**: send-fixer
- **Agent Type**: builder
- **Parallel**: true
- Remove direct Telethon client creation from `cmd_send()`
- Add `--reply-to` argument to send subparser
- Build relay-compatible payload: `{chat_id, reply_to, text, file_paths, session_id, timestamp}`
- Use synthetic session_id: `f"cli-{int(time.time())}"`
- Apply `_linkify_text()` (copy pattern from `send_telegram.py`) before queueing
- Truncate text to 4096 chars at sentence boundary before queueing
- Push to `telegram:outbox:{session_id}` via Redis RPUSH with 1-hour TTL
- Handle file validation (exists check) before queueing
- Print "Message queued ({N} chars)" on success, error to stderr on failure

### 2. Write tests for cmd_send()
- **Task ID**: build-tests
- **Depends On**: build-send-queue
- **Validates**: tests/unit/test_valor_telegram_send.py
- **Assigned To**: send-fixer
- **Agent Type**: builder
- **Parallel**: false
- Test successful queue push (mock Redis)
- Test chat name resolution failure
- Test empty message / no file error
- Test file not found error
- Test message truncation at 4096 chars
- Test reply_to included in payload when --reply-to provided
- Test file_paths included in payload when --file provided

### 3. Validate queue payload compatibility
- **Task ID**: validate-payload
- **Depends On**: build-send-queue
- **Assigned To**: send-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify payload keys match relay expectations in `_send_queued_message()`
- Verify file_paths format matches relay's normalization logic
- Verify session_id format doesn't collide with bridge session IDs
- Run all tests

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-payload
- **Assigned To**: send-fixer
- **Agent Type**: documentarian
- **Parallel**: false
- Update CLAUDE.md with --reply-to flag documentation
- Add inline comments in cmd_send()

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: send-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_valor_telegram_send.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/valor_telegram.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/valor_telegram.py` | exit code 0 |
| No direct Telethon in send | `grep -c 'telethon_client\|TelegramClient' tools/valor_telegram.py` | output contains 1 |
| Queue key uses cli prefix | `grep -c 'cli-' tools/valor_telegram.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the solution path is clear and all assumptions were validated during recon.
