# Valor AI System

An AI coworker powered by the Claude Agent SDK that runs autonomously on its own Mac.

## What Is This?

Valor is an AI coworker - not an assistant, not a tool, but a colleague with its own Mac, its own work, and its own agency. The supervisor assigns work and provides direction. Valor executes autonomously, reaching out via Telegram when necessary.

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Claude Agent SDK | **Active** | Primary agent backend (v0.1.20) |
| Telegram Integration | **Working** | User account via Telethon, responds to @valor mentions |
| Clawdbot (Legacy) | **Available** | Fallback via `USE_CLAUDE_SDK=false` |
| Self-Management | **Working** | Can restart himself, survives reboots |
| MCP Skills | **Working** | Sentry, GitHub, Linear, Notion, Stripe, Render |
| Daydream (Cron) | **Working** | Daily autonomous maintenance at 6 AM Pacific |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Telegram                                 │
│                    (User sends message)                          │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Python Bridge                                 │
│              (bridge/telegram_bridge.py)                         │
│                                                                  │
│  • Telethon client (user account, not bot)                       │
│  • Listens for @valor mentions and DMs                           │
│  • Maintains session continuity per chat                         │
│  • USE_CLAUDE_SDK flag routes to appropriate backend             │
└─────────────────────────┬───────────────────────────────────────┘
                          │
            ┌─────────────┴─────────────┐
            │                           │
            ▼                           ▼
┌───────────────────────┐   ┌───────────────────────────┐
│  Claude Agent SDK     │   │  Clawdbot (Legacy)        │
│  (USE_CLAUDE_SDK=true)│   │  (USE_CLAUDE_SDK=false)   │
│                       │   │                           │
│  • agent/sdk_client.py│   │  • subprocess call        │
│  • Same tools as      │   │  • ~/clawd/skills/        │
│    Claude Code CLI    │   │                           │
└───────────┬───────────┘   └───────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Claude API                                 │
│                  (anthropic/claude-sonnet-4)                     │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Install dependencies
pip install -e .

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys and Telegram credentials

# 3. Enable Claude Agent SDK (recommended)
# In .env:
USE_CLAUDE_SDK=true

# 4. Start the bridge
./scripts/start_bridge.sh
```

See [docs/setup.md](docs/setup.md) for detailed setup instructions.

## Configuration

### Agent Backend Selection

The system supports two agent backends via the `USE_CLAUDE_SDK` environment variable:

| Setting | Backend | Description |
|---------|---------|-------------|
| `USE_CLAUDE_SDK=true` | **Claude Agent SDK** | Official SDK with same capabilities as Claude Code CLI |
| `USE_CLAUDE_SDK=false` | Clawdbot (Legacy) | Third-party tool, subprocess-based |

The Claude Agent SDK is now the **recommended default**.

### Key Files

| File | Purpose |
|------|---------|
| `agent/sdk_client.py` | Claude Agent SDK wrapper |
| `bridge/telegram_bridge.py` | Telegram ↔ Agent bridge |
| `config/SOUL.md` | Valor persona definition |
| `.env` | Environment variables and API keys |

## Service Management

```bash
./scripts/valor-service.sh status   # Check if running
./scripts/valor-service.sh restart  # Restart after code changes
./scripts/valor-service.sh logs     # View logs
./scripts/valor-service.sh health   # Health check
```

## Repository Structure

```
ai/
├── agent/                  # Claude Agent SDK integration
│   ├── __init__.py
│   └── sdk_client.py       # SDK wrapper (ValorAgent class)
├── bridge/                 # Telegram-Agent bridge
│   └── telegram_bridge.py  # Main bridge with routing logic
├── tools/                  # Local Python tools
│   ├── telegram_history/   # Chat history storage
│   └── link_analysis/      # URL analysis
├── config/
│   ├── SOUL.md             # Valor persona definition
│   └── projects.json       # Multi-project configuration
├── scripts/
│   ├── valor-service.sh    # Service management
│   └── start_bridge.sh     # Quick start script
├── docs/
│   ├── setup.md            # Setup guide
│   └── plans/              # Migration and planning docs
├── logs/                   # Runtime logs
├── data/                   # Session files, state
├── CLAUDE.md               # Development guide
└── README.md               # This file
```

## Documentation

| Document | Purpose |
|----------|---------|
| [CLAUDE.md](CLAUDE.md) | Development principles and architecture |
| [docs/setup.md](docs/setup.md) | Local setup guide |
| [docs/plans/claude-agent-sdk-migration.md](docs/plans/claude-agent-sdk-migration.md) | SDK migration plan and status |

## MCP Skills (via Clawdbot Legacy)

When using Clawdbot backend, these skills are available in `~/clawd/skills/`:

| Skill | Tools | Purpose |
|-------|-------|---------|
| Sentry | 8 | Error monitoring, performance analysis |
| GitHub | 10 | Repository operations, PRs, issues |
| Linear | 9 | Project management, issue tracking |
| Notion | 8 | Knowledge base, documentation |
| Stripe | 9 | Payment processing, subscriptions |
| Render | 9 | Deployment, infrastructure |
| Daydream | 6 steps | Daily autonomous maintenance |

**Note**: Phase 2 of the SDK migration will rebuild these as standalone MCP servers usable by both Claude Code and Valor.

## Daydream (Daily Maintenance)

Valor runs autonomous maintenance daily at 6 AM Pacific:

1. **clean_legacy** - Remove deprecated patterns
2. **review_logs** - Analyze yesterday's logs
3. **check_sentry** - Query for errors
4. **clean_tasks** - Update Linear issues
5. **update_docs** - Ensure docs match code
6. **daily_report** - Summary to supervisor

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Quality

```bash
black . && ruff check . && mypy . --strict
```

### Switching Agent Backends

To switch from SDK to Clawdbot (for debugging or rollback):

```bash
# In .env
USE_CLAUDE_SDK=false

# Restart bridge
./scripts/valor-service.sh restart
```

## Contact

Valor Engels
