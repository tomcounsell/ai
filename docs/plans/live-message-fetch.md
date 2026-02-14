---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-02-14
tracking: https://github.com/tomcounsell/ai/issues/98
---

# Live Message Fetch for All Chat Types

## Problem

The `valor-telegram read` command and the `get-telegram-messages` skill only read from SQLite cache — they cannot fetch live messages from Telegram. The old live-fetch mechanism (`scripts/get-telegram-message-history`) only works for whitelisted DM users, not group chats. There should be no difference between fetching messages from DMs and groups.

**Current behavior:**
- `valor-telegram read` reads only from SQLite cache (populated passively by the bridge as messages arrive)
- `scripts/get-telegram-message-history` fetches live from Telegram API but only supports DM users via `dm_whitelist.json`
- The bridge's `check_message_query_request()` IPC handler only accepts `user_id` — no group chat support
- If the bridge wasn't running when messages arrived, they're missing from cache entirely
- Agent gets told "The skill is for DMs, not groups" — a false limitation

**Desired outcome:**
- `valor-telegram read --chat "Dev: Valor"` fetches live messages from Telegram (groups work)
- `valor-telegram read --chat "Tom"` fetches live messages from Telegram (DMs work identically)
- No distinction between DMs and groups in the tool interface
- SQLite cache is updated transparently when live messages are fetched
- Old DM-only `scripts/get-telegram-message-history` is removed

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (confirm API-first approach and session reuse)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Telethon session exists | `test -f /Users/valorengels/src/ai/data/valor_bridge.session` | API access |
| API credentials in .env | `grep -q TELEGRAM_API_ID .env` | Telegram authentication |

## Solution

### Key Elements

- **Live fetch in `valor-telegram read`**: Use Telethon directly (like `send` already does) to fetch messages from any chat — DM or group
- **Unified chat resolution**: Same `resolve_chat()` already handles both groups (SQLite) and DMs (whitelist) — use this for live fetch too
- **Cache-through**: Store fetched messages in SQLite so subsequent reads are instant
- **Remove legacy tool**: Delete `scripts/get-telegram-message-history` and the bridge's `check_message_query_request()` IPC handler

### Flow

```
valor-telegram read --chat "Dev: Valor" --limit 10
    ↓
resolve_chat("Dev: Valor") → chat_id
    ↓
Connect Telethon → client.get_messages(chat_id, limit=10)
    ↓
Store messages in SQLite cache
    ↓
Format and display
```

Fallback: if Telethon connection fails (no session, no network), fall back to SQLite cache with a warning.

### Technical Approach

- Reuse the same Telethon session pattern from `cmd_send()` — it already connects, authenticates, and disconnects cleanly
- `client.get_messages()` works identically for DM user IDs and group chat IDs — Telethon handles both
- After fetching, upsert messages into the SQLite `messages` table to keep the cache warm
- Add `--cached` flag to force cache-only reads (skip API) for offline/fast queries
- Remove `scripts/get-telegram-message-history` and the bridge's `check_message_query_request()` function — they're fully superseded

## Rabbit Holes

- **Pagination for large histories**: Don't build infinite scroll — just respect `--limit` and return that many messages
- **Conflict resolution between cache and API**: Don't try to merge — API results are authoritative, upsert by message_id
- **Keeping Telethon session alive**: Don't build a persistent connection pool — connect/disconnect per invocation like `send` does

## Risks

### Risk 1: Telethon session conflicts with running bridge
**Impact:** Both the CLI and bridge use the same session file, which could cause auth conflicts
**Mitigation:** Telethon supports concurrent read-only access to the same session. The `send` command already does this successfully. If issues arise, add a short retry with backoff.

### Risk 2: API rate limits on frequent reads
**Impact:** Rapid polling could trigger Telegram's flood protection
**Mitigation:** Default to cache-first for repeated reads within a short window. The `--cached` flag provides an explicit escape hatch.

## No-Gos (Out of Scope)

- Real-time streaming / live-tailing of messages
- Fetching media content (just text and metadata)
- Modifying or deleting messages
- Building a background sync daemon

## Update System

No update system changes required — this modifies existing CLI behavior only.

## Agent Integration

No new agent integration needed — the existing `telegram` skill at `.claude/skills/telegram/SKILL.md` already documents `valor-telegram read`. The skill instructions just need a minor note that reads are live by default. The `--cached` flag should be mentioned.

## Documentation

- [ ] Update `docs/features/telegram-messaging.md` to document live fetch behavior and `--cached` flag
- [ ] Update `.claude/skills/telegram/SKILL.md` to mention live vs cached reads

## Success Criteria

- [ ] `valor-telegram read --chat "Dev: Valor" --limit 5` fetches live messages from the group
- [ ] `valor-telegram read --chat "Tom" --limit 5` fetches live messages from the DM
- [ ] Both commands use the exact same code path — no DM vs group branching
- [ ] Fetched messages are stored in SQLite cache
- [ ] `valor-telegram read --chat "Dev: Valor" --cached` reads from cache only
- [ ] `scripts/get-telegram-message-history` is deleted
- [ ] Bridge's `check_message_query_request()` IPC handler is removed
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (live-fetch)**
  - Name: fetch-builder
  - Role: Add Telethon live fetch to valor-telegram read, remove legacy code
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: fetch-validator
  - Role: Verify live fetch works for both DMs and groups
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add live fetch to valor-telegram read
- **Task ID**: build-live-fetch
- **Depends On**: none
- **Assigned To**: fetch-builder
- **Agent Type**: builder
- **Parallel**: false
- Refactor `cmd_read()` in `tools/valor_telegram.py` to use Telethon `client.get_messages()` as the primary data source
- Add `--cached` flag to skip API and use SQLite only
- Upsert fetched messages into SQLite cache after each live fetch
- Fall back to cache with warning if Telethon connection fails

### 2. Remove legacy DM-only code
- **Task ID**: remove-legacy
- **Depends On**: build-live-fetch
- **Assigned To**: fetch-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `scripts/get-telegram-message-history`
- Remove `check_message_query_request()` from `bridge/telegram_bridge.py`
- Remove calls to `check_message_query_request()` from the bridge's main loop
- Clean up any references to the old IPC mechanism

### 3. Update documentation
- **Task ID**: update-docs
- **Depends On**: remove-legacy
- **Assigned To**: fetch-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/telegram-messaging.md` with live fetch details
- Update `.claude/skills/telegram/SKILL.md` with `--cached` flag

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: update-docs
- **Assigned To**: fetch-validator
- **Agent Type**: validator
- **Parallel**: false
- Test `valor-telegram read --chat "Dev: Valor" --limit 5` returns live messages
- Test `valor-telegram read --chat "Tom" --limit 5` returns live messages
- Test `valor-telegram read --chat "Dev: Valor" --cached` works from cache
- Verify `scripts/get-telegram-message-history` no longer exists
- Verify bridge still starts cleanly without the removed IPC handler
- Run `pytest tests/` to check no regressions

## Validation Commands

- `valor-telegram read --chat "Dev: Valor" --limit 3` - Live group fetch works
- `valor-telegram read --chat "Tom" --limit 3` - Live DM fetch works
- `valor-telegram read --chat "Dev: Valor" --cached --limit 3` - Cache-only mode works
- `test ! -f scripts/get-telegram-message-history` - Legacy script removed
- `pytest tests/` - No test regressions
