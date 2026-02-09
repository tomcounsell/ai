---
status: Planning
type: feature
appetite: Small: 1-2 days
owner: Valor
created: 2026-02-09
tracking: https://github.com/tomcounsell/ai/issues/67
---

# Bridge Message Query Tool

## Problem

When the bridge is running, it exclusively holds the Telegram session. This means:

- Cannot query recent messages via Telethon from outside the bridge
- The SQLite history database may lag behind real-time messages
- No way to fetch the *actual* latest messages from Telegram API while bridge is running

**Current behavior:**
To see recent messages, must either:
1. Query SQLite database (may be stale)
2. Stop the bridge, run a query script, restart (disruptive)

**Desired outcome:**
Query real-time messages from Telegram API while the bridge is running, via an internal tool that leverages the bridge's active Telethon client.

## Appetite

**Time budget:** Small: 1-2 days

**Team size:** Solo

## Prerequisites

No prerequisites — the bridge already has the Telethon client connected.

## Solution

### Key Elements

- **Query function**: Internal async function `query_messages(chat_id, limit)` using `client.get_messages()`
- **Bridge command**: Handle `/messages` or similar command in Telegram to trigger query
- **Formatted response**: Return messages in a readable format

### Flow

**User sends `/messages 5`** → Bridge intercepts → Calls `client.get_messages(chat_id, limit=5)` → Formats results → Replies with message list

Alternative flow for CLI/tool access:
**Tool request** → Bridge exposes via internal API → Query executes → Returns JSON

### Technical Approach

1. Add `async def query_recent_messages(client, chat_id, limit)` to bridge
2. Register `/messages` command handler (similar to `/update`)
3. Format output: timestamp, sender, preview of content
4. Optional: expose via Unix socket or HTTP for external tool access (future)

## Rabbit Holes

- **External API exposure**: Don't build HTTP server or Unix socket now - just the Telegram command
- **Message caching**: Don't try to sync with SQLite database on query - keep it simple
- **Rich formatting**: Don't render media, reactions, or reply chains - just text content

## Risks

### Risk 1: Rate limiting
**Impact:** Telegram might rate limit frequent queries
**Mitigation:** Add simple cooldown (e.g., 30s between queries)

### Risk 2: Large message content
**Impact:** Long messages could exceed Telegram's message length limit
**Mitigation:** Truncate content preview to ~100 chars per message

## No-Gos (Out of Scope)

- No HTTP/Unix socket API (future enhancement)
- No SQLite sync on query
- No media content display
- No message search (just recent fetch)
- No cross-chat queries in single command

## Update System

No update system changes required — this is a bridge-internal feature.

## Agent Integration

No agent integration required — this is a direct Telegram command, not exposed to the Claude agent. The agent already has access to the SQLite history via `tools/telegram_history/` for historical queries.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/bridge-message-query.md` describing the command
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstring on `query_recent_messages` function
- [ ] Comment explaining command format

## Success Criteria

- [ ] `/messages` or `/messages 5` command works in any chat
- [ ] Returns last N messages (default 5, max 20)
- [ ] Shows: timestamp, sender name, content preview
- [ ] Handles errors gracefully (invalid chat, rate limit)
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (bridge-query)**
  - Name: bridge-query-builder
  - Role: Implement the query function and command handler
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-query)**
  - Name: bridge-query-validator
  - Role: Verify implementation works correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement query function
- **Task ID**: build-query-function
- **Depends On**: none
- **Assigned To**: bridge-query-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `async def query_recent_messages(client, chat_id, limit=5)` to `bridge/telegram_bridge.py`
- Use `client.get_messages(chat_id, limit=limit)`
- Format results as list of dicts with timestamp, sender, content preview
- Handle errors (invalid chat, etc.)

### 2. Add command handler
- **Task ID**: build-command-handler
- **Depends On**: build-query-function
- **Assigned To**: bridge-query-builder
- **Agent Type**: builder
- **Parallel**: false
- Add handler for `/messages` command (similar to existing `/update` handler)
- Parse optional limit argument from command text
- Call query function and format response
- Reply with formatted message list

### 3. Validate implementation
- **Task ID**: validate-implementation
- **Depends On**: build-command-handler
- **Assigned To**: bridge-query-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `/messages` command is handled in code
- Check error handling for edge cases
- Verify output formatting is readable

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-implementation
- **Assigned To**: bridge-query-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/bridge-message-query.md`
- Add entry to `docs/features/README.md`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: bridge-query-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Check documentation exists and is indexed

## Validation Commands

- `grep -n "def query_recent_messages" bridge/telegram_bridge.py` - Function exists
- `grep -n "/messages" bridge/telegram_bridge.py` - Command handler exists
- `test -f docs/features/bridge-message-query.md` - Feature doc exists
- `grep -q "message-query" docs/features/README.md` - Indexed in README

---

## Open Questions

1. **Command name**: `/messages` or `/recent` or `/fetch`? (Suggesting `/messages` as most intuitive)
2. **Default chat**: Should it default to current chat, or require explicit chat specification for cross-chat queries?
3. **Output format**: Plain text list, or structured with separators/headers?
