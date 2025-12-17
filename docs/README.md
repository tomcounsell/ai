# AI System Documentation

## Overview

Documentation for the Valor AI System - a Claude Code-powered conversational AI assistant that operates through chat interfaces like Telegram, with full access to its host MacBook hardware and a rich ecosystem of tools.

**Primary Interface**: Telegram (real user account, not a bot)
**Core Engine**: Claude Code with specialized subagents
**Architecture**: Subagent-based with lazy-loading MCP tools

## Quick Start

```bash
# Start the system
./scripts/start.sh

# Start with Telegram interface
./scripts/start.sh --telegram

# View logs
./scripts/logs.sh
```

## Documentation Index

### Core Architecture

| Document | Description |
|----------|-------------|
| [System Overview](architecture/system-overview.md) | High-level architecture and design principles |
| [Subagent System](architecture/subagent-mcp-system.md) | Subagent-based architecture for context isolation |
| [MCP Integration](architecture/mcp-integration.md) | Model Context Protocol tool integration |

### Interface

| Document | Description |
|----------|-------------|
| [Telegram Integration](TELEGRAM.md) | Complete Telegram interface documentation |

### Subagent Specifications

| Document | Description |
|----------|-------------|
| [Subagents Overview](subagents/README.md) | Subagent architecture and PRD index |
| [Stripe Subagent](subagents/stripe-subagent-prd.md) | Payment processing |
| [Sentry Subagent](subagents/sentry-subagent-prd.md) | Error monitoring |
| [GitHub Subagent](subagents/github-subagent-prd.md) | Code repository |
| [Render Subagent](subagents/render-subagent-prd.md) | Infrastructure deployment |
| [Notion Subagent](subagents/notion-subagent-prd.md) | Knowledge management |
| [Linear Subagent](subagents/linear-subagent-prd.md) | Project management |

### Operations

| Document | Description |
|----------|-------------|
| [Monitoring](operations/monitoring.md) | System monitoring and health checks |
| [Daydream System](operations/daydream-system.md) | Autonomous analysis system |

### Quality & Testing

| Document | Description |
|----------|-------------|
| [Quality Standards](tools/quality-standards.md) | 9.8/10 gold standard implementation |
| [Testing Strategy](testing/testing-strategy.md) | Real integration testing approach |
| [Tool Architecture](tools/tool-architecture.md) | Tool design patterns |

### Security

| Document | Description |
|----------|-------------|
| [Security & Compliance](Security-Compliance-Requirements.md) | Security architecture and compliance |

### Reference

| Document | Description |
|----------|-------------|
| [Consolidated Documentation](CONSOLIDATED_DOCUMENTATION.md) | Complete merged documentation |
| [Consolidated PDF](CONSOLIDATED_DOCUMENTATION.pdf) | Printable PDF version |

## Architecture Summary

```
User (Telegram)
    |
    v
Main Agent (Valor) - Clean context, <10k tokens
    |
    v
Routing Layer
    |-- Task Analyzer
    |-- MCP Library (auth-aware)
    +-- Multi-Model Router
    |
    v
Specialized Subagents (lazy-loaded)
    |-- Stripe (payments)
    |-- Sentry (errors)
    |-- GitHub (code)
    |-- Render (infra)
    |-- Notion (docs)
    +-- Linear (projects)
```

## Key Principles

1. **Subagent Architecture**: Lazy-loaded specialized agents prevent context pollution
2. **No Legacy Code**: Complete elimination of deprecated patterns
3. **Critical Thinking**: Deep analysis over quick fixes
4. **Intelligence Over Patterns**: LLM understanding, not keyword matching
5. **Real Integration Testing**: No mocks, use actual services
6. **9.8/10 Quality Standard**: Maintained across all components

## Environment Setup

```bash
# Required
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890

# API Keys
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
PERPLEXITY_API_KEY=pplx-...
```

See [CLAUDE.md](/CLAUDE.md) for complete development guidelines.
