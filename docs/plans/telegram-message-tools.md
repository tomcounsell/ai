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

We have three related Telegram capabilities scattered across confusingly-named skills and tools:

| Capability | Current Location | Data Source |
|------------|------------------|-------------|
| Search history | `searching-message-history` skill | SQLite (`~/.valor/telegram_history.db`) |
| Fetch live messages | `get-telegram-messages` skill | Live Telegram API via bridge IPC |
| Send messages | None (inline Telethon scripts) | Live Telegram API |

**Current behavior:**
- Agents don't know which skill to use for message lookup
- No way to send messages without writing throwaway Python scripts
- `valor-history` CLI documented in SOUL.md doesn't exist
- Naming doesn't convey SQLite vs API distinction

**Desired outcome:**
- Single unified skill with clear guidance on when to use each backend
- `telegram-send` CLI for sending messages from any Claude Code session
- Consistent naming that reflects the actual data sources
- Agent can reliably choose the right approach

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (confirm naming and skill structure)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Telethon session exists | `test -f /Users/valorengels/src/ai/data/valor_bridge.session` | Reuse bridge session for sending |
| SQLite history DB | `test -f ~/.valor/telegram_history.db` | Message history storage |

## Solution

### Key Elements

- **Unified skill**: Single `telegram` skill with subsections for read (SQLite), read (live), and send
- **Send CLI tool**: `tools/telegram_send.py` for sending messages
- **CLI wrapper**: `scripts/telegram-send` for ergonomic usage
- **Retire old skills**: Remove `searching-message-history` and `get-telegram-messages` directories

### Flow

**Agent needs message context** → Checks `telegram` skill → Sees decision tree → Uses SQLite for search/groups, live API for real-time DMs

**Agent needs to send message** → Uses `telegram-send` CLI → Specifies group/chat + message → Message delivered

### Technical Approach

- Reuse existing Telethon session at `data/valor_bridge.session`
- Send tool runs independently of bridge (Telethon supports concurrent connections)
- Unified skill doc references existing tools, adds send capability
- Cache dialog list to avoid repeated API calls for group name resolution

## Rabbit Holes

- **Rich message formatting**: Don't build markdown/HTML formatting support — plain text only
- **Media sending**: Don't support photo/file uploads — text messages only for now
- **Message editing/deletion**: Out of scope — send-only
- **Read receipts**: Don't track delivery status

## Risks

### Risk 1: Session conflicts
**Impact:** Bridge and send tool fighting over Telethon session
**Mitigation:** Telethon sessions support multiple concurrent connections; test concurrency explicitly

### Risk 2: Dialog resolution overhead
**Impact:** Slow sends while iterating dialogs to find group by name
**Mitigation:** Cache dialog list in SQLite (reuse existing `chats` table)

## No-Gos (Out of Scope)

- Media/file sending
- Message editing or deletion
- Scheduled messages
- Bot commands (we're a user account, not a bot)
- Reply-to threading (v2 maybe)

## Update System

No update system changes required — these are local tools that don't affect deployment.

## Agent Integration

- New `telegram-send` CLI must be added to allowed tools in the unified skill
- Skill doc in `.claude/skills/telegram/SKILL.md` replaces two existing skills
- No MCP server needed — CLI tools are sufficient
- Remove stale `valor-history` reference from SOUL.md (doesn't exist)

## Documentation

- [ ] Create `.claude/skills/telegram/SKILL.md` as unified skill doc
- [ ] Update `docs/features/README.md` index with telegram messaging entry
- [ ] Remove outdated `valor-history` reference from `config/SOUL.md`
- [ ] Delete old skill directories after migration

## Success Criteria

- [ ] `python tools/telegram_send.py --group "Dev: Valor" "test message"` sends successfully
- [ ] `scripts/telegram-send --help` shows usage
- [ ] Unified skill doc at `.claude/skills/telegram/SKILL.md` exists
- [ ] Old skill directories removed (`.claude/skills/searching-message-history/`, `.claude/skills/get-telegram-messages/`)
- [ ] SOUL.md updated (remove `valor-history` reference)
- [ ] Tests pass for send functionality

## Team Orchestration

### Team Members

- **Builder (send-tool)**
  - Name: send-builder
  - Role: Implement telegram_send.py and CLI wrapper
  - Agent Type: builder
  - Resume: true

- **Builder (skill-consolidation)**
  - Name: skill-builder
  - Role: Create unified skill, remove old skills, update SOUL.md
  - Agent Type: builder
  - Resume: true

- **Validator (send)**
  - Name: send-validator
  - Role: Verify send tool works, test concurrent with bridge
  - Agent Type: validator
  - Resume: true

- **Validator (skills)**
  - Name: skills-validator
  - Role: Verify skill structure, old skills removed
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build telegram_send.py
- **Task ID**: build-send-tool
- **Depends On**: none
- **Assigned To**: send-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/telegram_send.py` with:
  - `--group NAME` to send by group name
  - `--chat-id ID` to send by chat ID
  - `--reply-to MSG_ID` optional reply threading
  - Reuse `data/valor_bridge.session`
  - Resolve group names via `chats` table first, then API fallback
- Create `scripts/telegram-send` wrapper script
- Add basic tests in `tests/test_telegram_send.py`

### 2. Build unified skill and cleanup
- **Task ID**: build-skill-consolidation
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills/telegram/SKILL.md` with:
  - Decision tree for which backend to use
  - SQLite search examples (from `searching-message-history`)
  - Live fetch examples (from `get-telegram-messages`)
  - Send message examples (new)
- Delete `.claude/skills/searching-message-history/`
- Delete `.claude/skills/get-telegram-messages/`
- Update `config/SOUL.md` to remove `valor-history` reference

### 3. Validate send tool
- **Task ID**: validate-send
- **Depends On**: build-send-tool
- **Assigned To**: send-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `python tools/telegram_send.py --help` works
- Verify `scripts/telegram-send --help` works
- Test actual send to a test group (if safe)
- Verify no session conflicts with running bridge

### 4. Validate skill consolidation
- **Task ID**: validate-skills
- **Depends On**: build-skill-consolidation
- **Assigned To**: skills-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `.claude/skills/telegram/SKILL.md` exists and is complete
- Verify old skill directories are removed
- Verify SOUL.md no longer references `valor-history`
- Verify skill is discoverable

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-send, validate-skills
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/telegram-messaging.md`
- Add entry to `docs/features/README.md`

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: skills-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `black --check . && ruff check .`
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python tools/telegram_send.py --help` - Send tool works
- `test -f .claude/skills/telegram/SKILL.md` - Unified skill exists
- `test ! -d .claude/skills/searching-message-history` - Old skill removed
- `test ! -d .claude/skills/get-telegram-messages` - Old skill removed
- `grep -q "valor-history" config/SOUL.md && echo "FAIL" || echo "PASS"` - SOUL.md cleaned

---

## Open Questions

1. **Test sends**: Should we send a real test message to "Dev: Valor" during validation, or just verify the tool runs without errors?

2. **Reply threading**: The issue comment mentions `--reply-to`. Is this needed for v1, or can we defer to v2?

3. **CLI naming**: `telegram-send` or `valor-telegram-send` to match existing `valor-*` naming?
