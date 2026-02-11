---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-02-11
tracking: https://github.com/tomcounsell/ai/issues/71
---

# Telegram Message Tools Consolidation

## Problem

We have fragmented Telegram capabilities with confusing distinctions:

| Current State | Problem |
|---------------|---------|
| Two skills for reading messages | Agents must understand SQLite vs API internals |
| SQLite treated as separate data source | Should just be a cache, not a different tool |
| No send capability | Have to write throwaway Telethon scripts |
| No media support | Can't send images, files, or audio |

**Current behavior:**
- `searching-message-history` skill: SQLite queries
- `get-telegram-messages` skill: Live API calls
- Agents confused about which to use
- No way to send messages or media

**Desired outcome:**
- **One command for reading messages** — abstracts away storage/caching
- **One command for sending messages** — supports text and media
- SQLite is just a transparent cache for API results
- Agent doesn't need to know or care about the backend

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (confirm API-first approach)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Telethon session exists | `test -f /Users/valorengels/src/ai/data/valor_bridge.session` | API access |
| API credentials in .env | `grep -q TELEGRAM_API_ID .env` | Telegram authentication |

## Solution

### Key Elements

- **`valor-telegram read`**: Single command to get messages. Uses API, caches to SQLite transparently.
- **`valor-telegram send`**: Send text or media. Supports images, files, audio.
- **Unified skill**: One `telegram` skill doc explaining both commands
- **Cache-through architecture**: SQLite stores API results, serves as fallback when offline

### Flow

**Reading messages:**
```
valor-telegram read --chat "Dev: Valor" --limit 10
valor-telegram read --chat "Dev: Valor" --search "deployment"
valor-telegram read --chat "Tom" --since "1 hour ago"
```
→ Hits API first, caches result, returns messages

**Sending messages:**
```
valor-telegram send --chat "Dev: Valor" "Hello world"
valor-telegram send --chat "Dev: Valor" --file ./screenshot.png "Check this out"
valor-telegram send --chat "Dev: Valor" --image ./photo.jpg
valor-telegram send --chat "Dev: Valor" --audio ./recording.mp3
```

### Technical Approach

- Single CLI entry point: `valor-telegram` with subcommands
- API-first: Always try live API, fall back to cache if offline/rate-limited
- SQLite as cache: Store messages on read, serve from cache when appropriate
- Media handling: Use Telethon's `send_file()` with appropriate attributes for images/audio/files
- Session reuse: Same `valor_bridge.session` as the bridge

### Cache Strategy

```
READ request
    ↓
Try Telegram API
    ↓ (success)           ↓ (fail: offline/rate-limit)
Store in SQLite cache     Read from SQLite cache
    ↓                         ↓
Return results            Return cached results (with staleness warning)
```

## Rabbit Holes

- **Complex search syntax**: Don't build a query DSL — simple keyword search is enough
- **Message threading/replies**: Don't try to reconstruct thread trees — flat list is fine
- **Reaction support**: Out of scope for v1
- **Edit/delete messages**: Out of scope — send-only

## Risks

### Risk 1: API rate limits
**Impact:** Frequent reads could hit Telegram limits
**Mitigation:** Cache aggressively, add backoff logic, respect API limits

### Risk 2: Large media uploads
**Impact:** Sending big files could timeout
**Mitigation:** Use Telethon's chunked upload, add progress indicator for large files

## No-Gos (Out of Scope)

- Message editing or deletion
- Reaction adding/removing
- Scheduled/delayed messages
- Forwarding messages
- Complex search operators (just keyword matching)

## Update System

No update system changes required — local tools only.

## Agent Integration

- New `valor-telegram` CLI exposed via unified skill
- Skill doc at `.claude/skills/telegram/SKILL.md`
- Remove old skills: `searching-message-history`, `get-telegram-messages`
- No MCP server needed — CLI is sufficient

## Documentation

- [ ] Create `.claude/skills/telegram/SKILL.md` with unified interface
- [ ] Create `docs/features/telegram-messaging.md`
- [ ] Update `docs/features/README.md` index
- [ ] Remove stale references from `config/SOUL.md`

## Success Criteria

- [ ] `valor-telegram read --chat "Dev: Valor" --limit 5` returns messages
- [ ] `valor-telegram send --chat "Dev: Valor" "test"` sends successfully
- [ ] `valor-telegram send --chat "Dev: Valor" --file ./test.png "image"` sends media
- [ ] Old skill directories removed
- [ ] Unified skill doc exists at `.claude/skills/telegram/SKILL.md`
- [ ] SQLite transparently caches read results

## Team Orchestration

### Team Members

- **Builder (CLI)**
  - Name: cli-builder
  - Role: Implement valor-telegram CLI with read/send subcommands
  - Agent Type: builder
  - Resume: true

- **Builder (skill)**
  - Name: skill-builder
  - Role: Create unified skill, remove old skills
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: telegram-validator
  - Role: Verify CLI works, test media sending
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build valor-telegram CLI
- **Task ID**: build-cli
- **Depends On**: none
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/valor_telegram.py` with:
  - `read` subcommand: `--chat`, `--limit`, `--search`, `--since`
  - `send` subcommand: `--chat`, `--file`, `--image`, `--audio`, message text
  - API-first with SQLite cache fallback
  - Reuse existing Telethon session
- Create `scripts/valor-telegram` wrapper
- Add tests in `tests/test_valor_telegram.py`

### 2. Build unified skill and cleanup
- **Task ID**: build-skill
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/telegram/SKILL.md` with examples
- Delete `.claude/skills/searching-message-history/`
- Delete `.claude/skills/get-telegram-messages/`
- Clean up SOUL.md references

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-cli, build-skill
- **Assigned To**: telegram-validator
- **Agent Type**: validator
- **Parallel**: false
- Test read command with various options
- Test send command with text
- Test send command with image file
- Verify old skills removed
- Verify unified skill exists

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/telegram-messaging.md`
- Update `docs/features/README.md`

## Validation Commands

- `valor-telegram read --help` - Read subcommand works
- `valor-telegram send --help` - Send subcommand works
- `test -f .claude/skills/telegram/SKILL.md` - Unified skill exists
- `test ! -d .claude/skills/searching-message-history` - Old skill removed
- `test ! -d .claude/skills/get-telegram-messages` - Old skill removed

---

## Open Questions

1. **Cache TTL**: How long should cached messages be considered fresh before re-fetching? (Suggestion: 5 minutes for recent, indefinite for older)

2. **Offline mode**: Should we warn when serving stale cache, or just serve silently?
