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

See [`docs-rebuild/`](docs-rebuild/) for complete system documentation.

## Contact

Valor Engels

---

*Clean slate. Zero legacy. 9.8/10 standard.*