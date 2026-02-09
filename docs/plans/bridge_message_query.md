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
A CLI tool `get-telegram-message-history` that queries real-time messages from Telegram API via the running bridge.

## Appetite

**Time budget:** Small: 1-2 days

**Team size:** Solo

## Prerequisites

No prerequisites — the bridge already has the Telethon client connected.

## Solution

### Key Elements

- **CLI tool**: `get-telegram-message-history <username> [count]`
- **IPC mechanism**: File-based request/response between CLI and bridge
- **User lookup**: Resolve username to Telegram user ID from whitelist config
- **Help output**: `--help` shows available usernames from config

### Flow

```
User runs: get-telegram-message-history Tom 5
    ↓
CLI writes request to data/message_query_request.json
    ↓
Bridge detects request file (polling or file watcher)
    ↓
Bridge calls client.get_messages(user_id, limit=5)
    ↓
Bridge writes results to data/message_query_result.json
    ↓
CLI reads result, formats output, deletes request file
```

### Technical Approach

**1. Whitelist loader utility** (`tools/telegram_users.py`):
```python
def get_whitelisted_users() -> dict[str, int]:
    """Return {username: user_id} from dm_whitelist.json"""

def resolve_username(name: str) -> int | None:
    """Resolve username (case-insensitive) to user ID"""
```

**2. Bridge query handler** (in `bridge/telegram_bridge.py`):
- Check for `data/message_query_request.json` in main loop (every 1s)
- Execute query via `client.get_messages()`
- Write result to `data/message_query_result.json`
- Delete request file

**3. CLI tool** (`scripts/get-telegram-message-history`):
```bash
get-telegram-message-history --help     # Show available users
get-telegram-message-history Tom        # Last 5 messages (default)
get-telegram-message-history Tom 10     # Last 10 messages
```

### Request/Response Format

**Request** (`data/message_query_request.json`):
```json
{
  "user_id": 179144806,
  "username": "Tom",
  "limit": 5,
  "requested_at": "2026-02-09T15:00:00"
}
```

**Response** (`data/message_query_result.json`):
```json
{
  "success": true,
  "user_id": 179144806,
  "username": "Tom",
  "messages": [
    {
      "date": "2026-02-09 14:55",
      "sender": "Tom",
      "text": "Message content here..."
    }
  ],
  "completed_at": "2026-02-09T15:00:01"
}
```

## Rabbit Holes

- **Real-time file watching**: Don't use inotify/fsevents - simple polling is fine
- **HTTP/socket server**: Don't build a server - file IPC is simpler and sufficient
- **Message caching**: Don't try to sync results back to SQLite
- **Media handling**: Don't download or display media - text only

## Risks

### Risk 1: Bridge not running
**Impact:** Query hangs waiting for response
**Mitigation:** CLI timeout (10s default), clear error message

### Risk 2: Race condition on request file
**Impact:** Request could be overwritten
**Mitigation:** Use atomic write, check for existing request

## No-Gos (Out of Scope)

- No HTTP/socket server (file IPC only)
- No SQLite sync of query results
- No media content in results
- No group chat queries (DM only for now)
- No message search (just recent fetch)

## Update System

No update system changes required — this is a bridge-internal feature.

## Agent Integration

No agent integration required initially. This is a CLI tool for human use. Future enhancement could expose to agent via MCP.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/bridge-message-query.md` describing the tool
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstring on query handler function
- [ ] --help text with examples

## Success Criteria

- [ ] `get-telegram-message-history --help` shows available usernames
- [ ] `get-telegram-message-history Tom 5` returns last 5 messages
- [ ] Output shows: date, sender, message text
- [ ] Timeout with clear error if bridge not running
- [ ] Handles unknown username gracefully
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (query-tool)**
  - Name: query-tool-builder
  - Role: Implement CLI tool and bridge handler
  - Agent Type: builder
  - Resume: true

- **Validator (query-tool)**
  - Name: query-tool-validator
  - Role: Verify implementation works correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create user lookup utility
- **Task ID**: build-user-lookup
- **Depends On**: none
- **Assigned To**: query-tool-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/telegram_users.py`
- Implement `get_whitelisted_users()` - returns {name: id} dict
- Implement `resolve_username(name)` - case-insensitive lookup
- Load from `~/Desktop/claude_code/dm_whitelist.json`

### 2. Add bridge query handler
- **Task ID**: build-bridge-handler
- **Depends On**: none
- **Assigned To**: query-tool-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `check_message_query_request()` function to bridge
- Call it in main loop (every 1s)
- Execute `client.get_messages()` when request found
- Write result JSON, delete request file

### 3. Create CLI tool
- **Task ID**: build-cli-tool
- **Depends On**: build-user-lookup
- **Assigned To**: query-tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/get-telegram-message-history`
- Parse args: username, count (default 5), --help
- Write request JSON, poll for result (timeout 10s)
- Format and print output

### 4. Validate implementation
- **Task ID**: validate-implementation
- **Depends On**: build-bridge-handler, build-cli-tool
- **Assigned To**: query-tool-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify --help shows usernames from config
- Check request/response JSON format
- Verify timeout handling
- Check error messages for edge cases

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-implementation
- **Assigned To**: query-tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/bridge-message-query.md`
- Add entry to `docs/features/README.md`

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: query-tool-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Check documentation exists and is indexed

## Validation Commands

- `test -f tools/telegram_users.py` - User lookup module exists
- `test -f scripts/get-telegram-message-history` - CLI tool exists
- `grep -n "check_message_query" bridge/telegram_bridge.py` - Handler exists
- `scripts/get-telegram-message-history --help` - Help works
- `test -f docs/features/bridge-message-query.md` - Feature doc exists
