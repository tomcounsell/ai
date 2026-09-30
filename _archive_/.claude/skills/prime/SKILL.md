---
name: prime
description: "Use when onboarding to the Valor AI codebase, understanding the system architecture, or when the user asks 'how does this work'. Comprehensive codebase orientation guide."
disable-model-invocation: true
---

# Prime - Codebase Onboarding

Get oriented in the Valor AI system well enough to add features effectively.

## What This Is

This is **Valor** - an AI coworker that runs on its own Mac. Not an assistant, not a tool - a colleague with agency. The supervisor assigns work, Valor executes autonomously.

**You ARE this codebase.** When users say "you" or "your features," they mean this code.

## Current Architecture

```
Telegram → Bridge (Telethon) → Redis AgentSession queue (bridge is I/O only)
Standalone Worker (python -m worker) → sole session execution engine → headless session runner (`agent/session_runner/`, one `claude -p` subprocess per turn)
Reflection scheduler (python -m reflections) → own launchd subprocess; enqueues recurring work the worker executes
```

**Key components:**
- **Bridge** (`bridge/telegram_bridge.py`): Telegram user account via Telethon; enqueues sessions and routes output (nudge loop) — no SDLC awareness
- **Worker** (`worker/__main__.py`): executes AgentSessions via the headless session runner (`agent/session_runner/`, harness in `agent/sdk_client.py`)
- **MCP Servers** (`.mcp.json`): modular capabilities (memory, BYOB, Sentry, Notion); GitHub via `gh` CLI
- **Identity** (`config/identity.json` + `config/personas/segments/`): structured identity data and composable persona segments

## Directory Layout

```
ai/                          # This repo
├── .claude/skills/          # Project-only skills (/prime, /setup, /sdlc, /update, /do-deploy)
├── .claude/skills-global/   # Global skills — hardlinked to ~/.claude/skills/ on every machine by /update
├── agent/                   # Session queue, SDK client, output routing
├── bridge/                  # Telegram bridge
├── worker/                  # Standalone worker service (python -m worker)
├── reflections/             # Out-of-process reflection scheduler (python -m reflections)
├── tools/                   # Local Python tools (valor-* CLIs)
├── config/                  # identity.json, personas/segments/, reflections.yaml
├── scripts/valor-service.sh # Service management
├── docs/features/           # Feature index — how things actually work
└── CLAUDE.md                # Development guide (READ THIS)
```

## Read These Files

**In order:**
1. `CLAUDE.md` - development principles, commands, architecture
2. `config/personas/segments/` - Valor's identity, work patterns, and tools
3. `docs/features/README.md` - feature index, when you need how-something-works detail

## How to Add Features

Create `.claude/skills-global/<name>/SKILL.md` for skills every machine should know (global bodies stay repo-agnostic; repo specifics go in the `.claude/skill-context/` seam), or `.claude/skills/<name>/SKILL.md` for project-only skills. See "Global vs. Project-Only Skills" in `CLAUDE.md`.

New Python tools are invisible to the agent until wired into a CLI entry point (`pyproject.toml [project.scripts]`) or imported by the bridge directly.

## Service Management

```bash
./scripts/valor-service.sh status   # Check if running
./scripts/valor-service.sh restart  # Restart bridge, watchdog, and worker after changes
./scripts/valor-service.sh logs     # View logs
```

## Key Principles

1. **Always commit and push** - never leave work uncommitted
2. **No legacy code** - delete obsolete code completely
3. **Critical thinking** - question assumptions, validate decisions
4. **Self-improving** - Valor can modify his own code and restart
5. **Parallelize independent work** - spawn parallel subagents for independent tasks; never for sequential/dependent work

## Quick Actions

**Check system status:**
```bash
./scripts/valor-service.sh status
tail -20 logs/bridge.error.log
curl -s localhost:8500/dashboard.json   # full system state as JSON
```

**After making changes:**
```bash
git add . && git commit -m "Description" && git push
./scripts/valor-service.sh restart
```

---

*Run `/prime` at the start of any session to get oriented.*
