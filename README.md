# AI System - Clean Rebuild

## Status: 🏗️ Rebuilding

Complete system rebuild in progress. All legacy code removed.

## Quick Start

### One-Command Telegram Bot

```bash
# Run everything: auth (if needed) → start bot → tail logs
./scripts/telegram_run.sh
```

### Shell Alias Setup

Add this to your shell config (`~/.zshrc` or `~/.bash_profile`):

```bash
alias valor="cd /Users/valorengels/src/ai && ./scripts/telegram_run.sh"
```

Then just type `valor` from anywhere to start your AI system!

### Other Commands

```bash
# Start production server
./scripts/start.sh

# Start demo server (no API keys needed)
./scripts/start.sh --demo

# View logs
./scripts/logs.sh

# Stop all services
./scripts/stop.sh
```

## Documentation

See [`docs/`](docs/) for complete system documentation:

### Key Documents
- **[Architecture Overview](docs/architecture/system-overview.md)** - System design and components
- **[PRD](docs/PRD-AI-System-Rebuild.md)** - Product requirements and roadmap
- **[CLAUDE.md](CLAUDE.md)** - Development guide and commands
- **[System Status](docs/SYSTEM_STATUS.md)** - Current implementation status
- **[Subagents](docs/subagents/)** - Domain-specific agent PRDs

### Recent Architecture Decisions
- **[MCP Library & Session Management](docs/MCP-Library-Requirements.md)** - Intelligent MCP server selection
- **[Gemini CLI Integration](docs/architecture/gemini-cli-integration-analysis.md)** - Multi-model agent router
- **[Skills vs Subagents](docs/architecture/skills-vs-subagents-analysis.md)** - Claude Code subagent approach
- **[Agent-SOP Evaluation](docs/architecture/agent-sop-evaluation.md)** - Structured workflow framework

## Contact

Valor Engels

---

*Clean slate. Zero legacy. 9.8/10 standard.*