# Valor AI System

An AI coworker powered by the Claude Agent SDK that runs autonomously on its own Mac.

## What Is This?

Valor is an AI coworker - not an assistant, not a tool, but a colleague with its own Mac, its own work, and its own agency. The supervisor assigns work and provides direction. Valor executes autonomously, reaching out via Telegram when necessary.

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Claude Agent SDK | **Active** | Agent backend (v0.1.20) |
| Telegram Integration | **Working** | User account via Telethon, responds to @valor mentions |
| Self-Management | **Working** | Can restart himself, survives reboots |
| MCP Skills | **Working** | Sentry, Notion (MCP); GitHub via `gh` CLI |
| Reflections (Cron) | **Working** | Daily autonomous maintenance at 6 AM Pacific |

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
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌───────────────────────────────────────┐
│  Claude Agent SDK                     │
│                                       │
│  • agent/sdk_client.py                │
│  • Same tools as Claude Code CLI      │
└───────────────────┬───────────────────┘
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

# 3. Start the bridge
./scripts/start_bridge.sh
```

See [docs/setup.md](docs/setup.md) for detailed setup instructions.

## Configuration

### Agent Backend

The system uses the Claude Agent SDK as its agent backend, providing the same capabilities as Claude Code CLI.

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

## MCP Skills

Skills are available via MCP servers registered in `.mcp.json`:

| Skill | Purpose |
|-------|---------|
| Sentry | Error monitoring, performance analysis |
| GitHub | Repository operations, PRs, issues |
| Reflections | Daily autonomous maintenance |

## Reflections (Daily Maintenance)

Valor runs autonomous maintenance daily at 6 AM Pacific via a 16-unit pipeline with string-keyed state tracking:

**Independent units** (13): `legacy_code_scan`, `log_review`, `task_management`, `documentation_audit`, `skills_audit`, `hooks_audit`, `redis_ttl_cleanup`, `redis_data_quality`, `branch_plan_cleanup`, `feature_docs_audit`, `principal_staleness`, `disk_space_check`, `pr_review_audit`

**Merged pipelines** (3): `session_intelligence` (analysis → reflection → auto-fix), `behavioral_learning` (episode close → pattern crystallization), `daily_report_and_notify` (produce report → create GitHub issue)

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Code Quality

```bash
black . && ruff check . && mypy . --strict
```

## Contact

Valor Engels
