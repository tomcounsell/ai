# Valor AI System

A Claude Code-powered AI coworker that runs on its own machine.

## What Is This?

Valor is an AI coworker - not an assistant, not a tool, but a colleague with its own Mac, its own work, and its own agency. The supervisor assigns work and provides direction. Valor executes autonomously, reaching out via Telegram only when necessary.

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Telegram Integration | **Working** | User account via Telethon, responds to @valor mentions |
| Clawdbot Agent | **Working** | Handles AI processing with SOUL.md persona |
| Self-Management | **Working** | Can restart himself, survives reboots |
| Service (launchd) | **Installed** | Auto-starts on boot |
| Skills (MCP) | Planned | Stripe, Sentry, GitHub, Render, Notion, Linear |
| Daydream (Cron) | Planned | Daily autonomous maintenance |

## Valor's Capabilities

Valor has access to 15 tools that enable autonomous work:

### Research & Information
- **Web Search**: Find current information via Perplexity API
- **Knowledge Search**: Semantic search across local documents with embeddings
- **Link Analysis**: Extract, validate, and summarize URLs

### Vision & Media
- **Image Analysis**: Describe images, detect objects, extract text (OCR)
- **Image Tagging**: Categorize and tag images with AI
- **Image Generation**: Create images from text descriptions
- **Transcription**: Convert audio to text

### Development
- **Code Execution**: Run Python, JavaScript, or Bash in a sandbox
- **Documentation**: Generate docstrings, READMEs, API docs from code
- **Browser Automation**: Navigate, interact, screenshot web pages

### Testing & Quality
- **Test Judge**: AI evaluation of test results against criteria
- **Test Params**: Generate edge cases and parameter variations
- **Test Scheduler**: Queue and execute tests in background

### Communication
- **Telegram History**: Search conversation history with relevance scoring
- **Document Summary**: Summarize documents at configurable detail levels

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
┌─────────────────────────────────────────────────────────────────┐
│              clawdbot agent --local                              │
│                                                                  │
│  • Loads ~/clawd/SOUL.md (Valor persona)                         │
│  • Calls Claude API for reasoning                                │
│  • Returns response to bridge                                    │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Claude API                                 │
│                  (anthropic/claude-sonnet-4)                     │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Install Clawdbot
npm install -g clawdbot@latest

# 2. Set up workspace
mkdir -p ~/clawd
cp config/SOUL.md ~/clawd/SOUL.md

# 3. Install Python dependencies
pip install telethon python-dotenv

# 4. Install and start the service
./scripts/valor-service.sh install
```

See [docs/setup.md](docs/setup.md) for detailed setup instructions.

## Service Management

Valor can manage his own process:

| Command | Description |
|---------|-------------|
| `./scripts/valor-service.sh status` | Check if running |
| `./scripts/valor-service.sh restart` | Restart after code changes |
| `./scripts/valor-service.sh logs` | View logs |
| `./scripts/valor-service.sh health` | Health check |

The service auto-restarts on crash and on system boot.

## Repository Structure

```
ai/
├── bridge/                 # Telegram-Clawdbot bridge (Python)
│   └── telegram_bridge.py  # Main bridge script
├── tools/                  # Valor's capability tools
│   ├── search/             # Web search (Perplexity)
│   ├── image_analysis/     # Vision analysis
│   ├── code_execution/     # Sandboxed code runner
│   ├── test_judge/         # AI test evaluation
│   ├── knowledge_search/   # Semantic local search
│   ├── doc_summary/        # Document summarization
│   ├── documentation/      # Doc generation
│   ├── link_analysis/      # URL analysis
│   ├── telegram_history/   # Chat history search
│   ├── image_tagging/      # Image categorization
│   ├── test_params/        # Test parameter generation
│   ├── test_scheduler/     # Background test runner
│   ├── browser/            # Browser automation
│   ├── image_gen/          # Image generation
│   └── transcribe/         # Audio transcription
├── config/
│   ├── SOUL.md             # Valor persona definition
│   └── clawdbot/           # Clawdbot config templates
├── scripts/
│   ├── valor-service.sh    # Service management
│   └── start_bridge.sh     # Quick start script
├── docs/
│   ├── setup.md            # Setup guide
│   ├── CLAWDBOT_MIGRATION_PLAN.md
│   └── SKILLS_MIGRATION.md
├── logs/                   # Runtime logs
├── data/                   # Session files, state
├── CLAUDE.md               # Development guide
└── README.md               # This file
```

## Documentation

| Document | Purpose |
|----------|---------|
| [tools/README.md](tools/README.md) | Tools overview and usage |
| [tools/STANDARD.md](tools/STANDARD.md) | Tool development standard |
| [docs/setup.md](docs/setup.md) | Local setup guide |
| [docs/CLAWDBOT_MIGRATION_PLAN.md](docs/CLAWDBOT_MIGRATION_PLAN.md) | Migration status and plan |
| [docs/SKILLS_MIGRATION.md](docs/SKILLS_MIGRATION.md) | Skills implementation guide |
| [CLAUDE.md](CLAUDE.md) | Development principles |

## Planned: Skills

Business integration skills (not yet implemented):

| Skill | Purpose | Priority |
|-------|---------|----------|
| **Sentry** | Error monitoring, performance analysis | High |
| **GitHub** | Repository operations, PRs, issues | High |
| **Linear** | Project management, issue tracking | Medium |
| **Notion** | Knowledge base, documentation | Medium |
| **Stripe** | Payment processing, subscriptions | Low |
| **Render** | Deployment, infrastructure | Low |

## Planned: Daydream

Daily autonomous maintenance process (not yet implemented):

1. Clean up legacy code
2. Review previous day's logs
3. Check Sentry for errors
4. Clean up task management (Linear)
5. Update documentation
6. Produce daily report

## Contact

Valor Engels
