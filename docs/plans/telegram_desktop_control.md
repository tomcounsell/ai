---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-02-12
tracking: https://github.com/tomcounsell/ai/issues/66
---

# Telegram Desktop Control

## Problem

We can only access Telegram messages programmatically through the bridge's Telethon session. This gives us raw data but no visibility into the **real user experience** - how messages actually render in Telegram Desktop (formatting, reactions, threading, layout).

**Current behavior:**
- Bridge queries return message text and metadata, but we can't verify visual presentation
- No way to screenshot actual conversations for review evidence
- Debugging UX issues (broken formatting, missing reactions) requires manual inspection
- The `/do-pr-review` skill can screenshot web apps via `agent-browser` but has no equivalent for native desktop apps

**Desired outcome:**
- Screenshot Telegram Desktop conversations as visual evidence during reviews
- Navigate to specific chats by name to verify message delivery and formatting

## Feasibility Research (Completed)

Hands-on testing on macOS revealed the following about Telegram Desktop:

| Capability | Status | Notes |
|-----------|--------|-------|
| **Accessibility tree** | Not available | Zero UI elements exposed - custom Qt/Metal rendering |
| **Keystrokes** | Works | Cmd+K search, typing, Enter, Escape all functional |
| **Menu bar** | Works | All menu items accessible and inspectable |
| **Window title** | Works | Shows active chat name (e.g., "Telegram @ Valor") |
| **Window bounds** | Works | Position/size available via AppleScript and Quartz |
| **Screenshots** | Works* | Quartz region capture works but needs Screen Recording permission |
| **Clipboard** | Works | pbcopy/pbpaste for text transfer |

**Key constraint**: Unlike `agent-browser` which provides semantic element refs (`@e1`, `@e2`), Telegram Desktop is a visual black box. No element targeting possible. Interaction is keyboard-driven only.

**Viable approach**: Build a CLI tool (`agent-desktop`) that wraps AppleScript + Quartz for keyboard automation and screenshots. Follow the same pattern as `agent-browser` but adapted for keyboard-driven interaction instead of element refs.

## Appetite

**Size:** Small

**Team:** Solo dev. Ship it.

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Telegram Desktop installed | `test -d "/Applications/Telegram.app"` | Target application |
| pyobjc-framework-Quartz | `python -c "import Quartz"` | Window enumeration and screenshots |
| Screen Recording permission | `python -c "import Quartz.CoreGraphics as CG; img = CG.CGWindowListCreateImage(CG.CGRectNull, CG.kCGWindowListOptionIncludingWindow, 1, 0); assert img is not None"` | Non-blank screenshots |
| Accessibility permission | `osascript -e 'tell application "System Events" to tell process "Telegram" to set frontmost to true'` | Keystroke injection |

## Solution

### Key Elements

- **`agent-desktop` CLI tool**: Python script in `tools/telegram_desktop/` invoked as `python -m tools.telegram_desktop <command>`
- **AppleScript engine**: Wraps `osascript` calls for keystroke injection, window management, and menu access
- **Quartz screenshot engine**: Uses `CGWindowListCreateImage` to capture Telegram's window region
- **Skill definition**: `.claude/skills/agent-desktop/SKILL.md` teaches Claude how to use the tool

### Flow

**Review verification:**
Activate Telegram → Navigate to chat (Cmd+K + type name + Enter) → Wait for load → Screenshot → Return image path

**Message verification:**
Activate Telegram → Navigate to chat → Screenshot → Claude reads screenshot visually → Confirms formatting/content

### Technical Approach

- **No element refs** - Unlike agent-browser, all interaction is keyboard-driven. Commands are action-oriented (`navigate`, `type`, `screenshot`) rather than ref-oriented (`click @e1`)
- **Window title as state** - The window title (e.g., "Telegram @ Valor") is the only reliable way to know which chat is active
- **Quartz for screenshots** - Full-screen capture cropped to Telegram's window bounds (per-window capture requires additional permissions Telegram doesn't expose)
- **AppleScript for control** - `osascript` subprocess calls for keystroke injection and window management
- **Single Python module** - No external dependencies beyond pyobjc-framework-Quartz (already installed)

### Command Surface (v1: screenshot + navigate)

```bash
# Window management
agent-desktop activate                    # Bring Telegram to front
agent-desktop status                      # Current chat name, window bounds
agent-desktop verify                      # Check permissions, take test screenshot

# Navigation
agent-desktop navigate "Chat Name"        # Cmd+K → type → Enter
agent-desktop back                        # Escape to chat list

# Screenshots
agent-desktop screenshot                  # Capture window → return path
agent-desktop screenshot --output path.png  # Capture to specific path
```

**Deferred to v2:** `type`, `send`, `menu` commands (text input and menu access)

## Rabbit Holes

- **OCR/text extraction from screenshots** - Tempting but unnecessary. Claude can read screenshots natively as a multimodal model. Don't build OCR.
- **Element targeting** - Telegram exposes zero accessibility elements. Don't try to build a ref system - it won't work.
- **Cross-platform support** - macOS only. Linux/Windows would need completely different automation backends. Separate project.
- **Message parsing from screenshots** - Don't try to structurally parse messages from images. Use the existing Telethon bridge for data; this tool is for visual verification only.

## Risks

### Risk 1: Screen Recording permission may not persist
**Impact:** Screenshots return blank gray images
**Mitigation:** Add a `verify` command that takes a test screenshot and checks it's not a solid color. Document permission setup in README.

### Risk 2: Telegram Desktop updates break keyboard shortcuts
**Impact:** Navigation commands fail silently
**Mitigation:** Keep commands simple (Cmd+K for search is a universal pattern). Test suite verifies core shortcuts work.

### Risk 3: Timing-dependent failures
**Impact:** Screenshot taken before chat loads, keystroke sent before search opens
**Mitigation:** Use configurable delays between actions. Start conservative (0.5s), let users tune down.

## No-Gos (Out of Scope)

- No Telegram Web automation (use `agent-browser` for that)
- No message sending / text input in v1 (deferred to v2 - the bridge handles message sending)
- No reading message content programmatically from the UI (use bridge query tools)
- No multi-window or multi-account support
- No automated login/authentication flow
- No menu access commands in v1

## Update System

Update script needs to install `pyobjc-framework-Quartz` dependency. Add to the Python dependencies section of the update process. No config file changes needed - the tool is self-contained.

- Add `pyobjc-framework-Quartz` to `pyproject.toml` dependencies
- Remote machines will need Screen Recording and Accessibility permissions granted manually (one-time setup, cannot be automated)
- Add permission setup steps to the `/setup` skill documentation

## Agent Integration

The agent-desktop tool will be exposed as a Claude Code skill (like agent-browser), not as an MCP server.

- Create `.claude/skills/agent-desktop/SKILL.md` with usage documentation and command reference
- Add `Bash(agent-desktop:*)` to `.claude/settings.local.json` permissions
- No MCP server needed - invoked via Bash tool directly
- No bridge changes needed - this is a Claude Code skill, not a bridge capability
- Integration test: skill invocation via Claude Code verifies the tool works end-to-end

## Documentation

- [ ] Create `docs/features/telegram-desktop-control.md` describing the feature and permission setup
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Create `tools/telegram_desktop/README.md` with command reference and troubleshooting
- [ ] Document macOS permission requirements (Screen Recording, Accessibility) with screenshots

## Success Criteria

- [ ] `agent-desktop activate` brings Telegram Desktop to front
- [ ] `agent-desktop navigate "Chat Name"` opens the specified chat
- [ ] `agent-desktop screenshot` captures a non-blank PNG of the Telegram window
- [ ] `agent-desktop status` reports the active chat name from window title
- [ ] `agent-desktop verify` checks permissions and reports readiness
- [ ] Tool follows the same pattern as `tools/browser/` (manifest.json, README.md, tests/)
- [ ] Skill definition in `.claude/skills/agent-desktop/` teaches Claude the workflow
- [ ] All tests pass: `pytest tools/telegram_desktop/tests/ -v`
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (core-tool)**
  - Name: desktop-builder
  - Role: Implement the agent-desktop CLI tool and all commands
  - Agent Type: builder
  - Resume: true

- **Builder (skill)**
  - Name: skill-builder
  - Role: Create skill definition and permission config
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: desktop-validator
  - Role: Verify all commands work against real Telegram Desktop
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs, tool README, permission guide
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add pyobjc dependency
- **Task ID**: build-deps
- **Depends On**: none
- **Assigned To**: desktop-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `pyobjc-framework-Quartz` to `pyproject.toml` dependencies
- Verify import works: `python -c "import Quartz"`

### 2. Build core agent-desktop tool
- **Task ID**: build-tool
- **Depends On**: build-deps
- **Assigned To**: desktop-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/telegram_desktop/` with manifest.json, `__init__.py`, `__main__.py`
- Implement commands: `activate`, `status`, `verify`, `navigate`, `back`, `screenshot`
- AppleScript wrapper for keystrokes via `subprocess.run(['osascript', '-e', ...])`
- Quartz screenshot capture with window bounds detection
- CLI argument parsing with subcommands
- Create integration tests in `tools/telegram_desktop/tests/`

### 3. Create skill definition
- **Task ID**: build-skill
- **Depends On**: build-tool
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/agent-desktop/SKILL.md` with frontmatter and usage docs
- Add `Bash(agent-desktop:*)` permission alias or document invocation pattern
- Include workflow examples mirroring agent-browser patterns

### 4. Validate all commands
- **Task ID**: validate-tool
- **Depends On**: build-skill
- **Assigned To**: desktop-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tools/telegram_desktop/tests/ -v`
- Manually verify: activate, navigate to a real chat, screenshot, check image is non-blank
- Verify skill definition is syntactically correct

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-tool
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/telegram-desktop-control.md`
- Create `tools/telegram_desktop/README.md`
- Add entry to `docs/features/README.md` index table

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: desktop-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Confirm documentation exists and is indexed

## Validation Commands

- `python -m tools.telegram_desktop activate` - Telegram comes to front
- `python -m tools.telegram_desktop status` - Reports chat name
- `python -m tools.telegram_desktop screenshot --output /tmp/test.png && file /tmp/test.png` - Valid PNG produced
- `pytest tools/telegram_desktop/tests/ -v` - All tests pass
- `test -f docs/features/telegram-desktop-control.md` - Feature doc exists
- `test -f tools/telegram_desktop/README.md` - Tool README exists

---

## Resolved Questions

1. **Screen Recording permission**: Add to `/setup` skill as a documented manual step (one-time per machine).
2. **Command name**: `agent-desktop` — extensible for future desktop app support.
3. **Scope**: Screenshot + navigate only in v1. Type/send/menu deferred to v2. Appetite reduced to Small.
