---
description: "Comprehensive onboarding guide for the Valor AI codebase, architecture, and development workflow. Use when onboarding, understanding the system, or asking 'how does this work'."
---

# Prime - Codebase Onboarding

Get up to speed on the Valor AI system to add new features effectively.

## What This Is

This is **Valor** - an AI coworker that runs on its own Mac. Not an assistant, not a tool - a colleague with agency. The supervisor assigns work, Valor executes autonomously.

**You ARE this codebase.** When users say "you" or "your features," they mean this code.

## Current Architecture

```
Telegram → Python Bridge (Telethon) → Claude Agent SDK → Claude API
```

**Key components:**
- **Python Bridge** (`bridge/telegram_bridge.py`): Handles Telegram user account via Telethon
- **Claude Agent SDK** (`agent/sdk_client.py`): AI orchestration, calls Claude API
- **MCP Servers** (`.mcp.json`): Modular capabilities (Sentry, Notion, etc.); GitHub via `gh` CLI
- **SOUL.md** (`config/SOUL.md`): Valor's persona and philosophy

## Directory Layout

```
ai/                          # This repo
├── .claude/commands/        # Claude Code skills (/prime, /pthread, /sdlc)
├── agent/                   # Claude Agent SDK integration
├── bridge/                  # Telegram bridge
├── config/SOUL.md           # Persona definition
├── scripts/valor-service.sh # Service management
├── docs/                    # Documentation
└── CLAUDE.md                # Development guide (READ THIS)
```

## Read These Files

**In order:**
1. `CLAUDE.md` - Development principles, commands, architecture
2. `config/SOUL.md` - Valor's persona and philosophy

## How to Add Features

### New Claude Code Skill

Create `.claude/commands/<name>.md` with instructions for Claude Code to follow.

### Permission Model

| Pattern | Behavior | Use For |
|---------|----------|---------|
| `accept` | Auto-approve | Read ops (list, get) |
| `prompt` | Ask user | Write ops (create, update) |
| `reject` | Block | Dangerous ops (delete) |

## Service Management

```bash
./scripts/valor-service.sh status   # Check if running
./scripts/valor-service.sh restart  # Restart after changes
./scripts/valor-service.sh logs     # View logs
```

## Key Principles

1. **Always commit and push** - Never leave work uncommitted
2. **No legacy code** - Delete obsolete code completely
3. **Critical thinking** - Question assumptions, validate decisions
4. **Self-improving** - Valor can modify his own code and restart

## Thread Types (for complex work)

| Type | Use Case |
|------|----------|
| Base | Single task |
| P-Thread | Parallel independent work |
| C-Thread | Chained phases with checkpoints |
| L-Thread | Extended autonomous work |

## Quick Actions

**Check system status:**
```bash
./scripts/valor-service.sh status
tail -20 logs/bridge.error.log
```

**After making changes:**
```bash
git add . && git commit -m "Description" && git push
./scripts/valor-service.sh restart
```

---

*Run `/prime` at the start of any session to get oriented.*
