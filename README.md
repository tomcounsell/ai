# Valor AI System

A Claude Code-powered AI coworker that runs on its own machine.

## What Is This?

Valor is an AI coworker - not an assistant, not a tool, but a colleague with its own Mac, its own work, and its own agency. The supervisor assigns work and provides direction. Valor executes autonomously, reaching out via Telegram only when necessary.

## Architecture

This system uses [Clawdbot](https://github.com/clawdbot/clawdbot) as the messaging and gateway layer, with custom skills for business integrations.

```
Supervisor (Telegram) → Clawdbot Gateway → Claude Code → Skills
                                                           ├── Stripe
                                                           ├── Sentry
                                                           ├── GitHub
                                                           ├── Render
                                                           ├── Notion
                                                           └── Linear
```

## Quick Start

### 1. Install Clawdbot

```bash
npm install -g clawdbot@latest
clawdbot onboard --install-daemon
```

### 2. Configure

```bash
# Copy persona to Clawdbot workspace
cp config/SOUL.md ~/clawd/SOUL.md

# Configure Clawdbot
cp config/clawdbot/clawdbot.json ~/.clawdbot/clawdbot.json
# Edit with your API keys and Telegram credentials
```

### 3. Start

```bash
clawdbot start
```

## Commands

| Command | Description |
|---------|-------------|
| `clawdbot start` | Start the daemon |
| `clawdbot stop` | Stop the daemon |
| `clawdbot status` | Check status |
| `clawdbot logs` | View logs |

## Documentation

- **[CLAUDE.md](CLAUDE.md)** - Development guide
- **[docs/CONSOLIDATED_DOCUMENTATION.md](docs/CONSOLIDATED_DOCUMENTATION.md)** - Complete system documentation
- **[docs/CLAWDBOT_MIGRATION_PLAN.md](docs/CLAWDBOT_MIGRATION_PLAN.md)** - Migration details
- **[docs/SKILLS_MIGRATION.md](docs/SKILLS_MIGRATION.md)** - Skills implementation guide

## Skills

Custom Clawdbot skills for business integrations:

| Skill | Purpose |
|-------|---------|
| **Stripe** | Payment processing, subscriptions, billing |
| **Sentry** | Error monitoring, performance analysis |
| **GitHub** | Repository operations, PRs, issues |
| **Render** | Deployment, infrastructure management |
| **Notion** | Knowledge base, documentation |
| **Linear** | Project management, issue tracking |

## Contact

Valor Engels
