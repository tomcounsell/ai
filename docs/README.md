# AI System Documentation

## Overview

Documentation for the Valor AI System - a unified conversational development environment that eliminates boundaries between natural conversation and code execution. Built on Claude Code, it creates a living codebase where users interact directly WITH the system through chat applications like Telegram.

**Primary Interface**: Telegram (real user account, not a bot)
**Core Engine**: Claude Code with rich tool ecosystem
**Model**: Valor provides tools, workflows, and skills; Claude Code orchestrates

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
| [Consolidated Documentation](CONSOLIDATED_DOCUMENTATION.md) | **Primary reference** - complete system documentation |
| [System Overview](architecture/system-overview.md) | High-level architecture and design principles |
| [MCP Integration](architecture/mcp-integration.md) | Model Context Protocol tool integration |
| [Codebase Context & RAG](architecture/codebase-context-rag.md) | Per-workspace indexing and retrieval strategy |

### Interface

| Document | Description |
|----------|-------------|
| [Telegram Integration](TELEGRAM.md) | Complete Telegram interface documentation |

### Operations

| Document | Description |
|----------|-------------|
| [Deployment](deployment.md) | Multi-instance deployment configuration |
| [Monitoring](operations/monitoring.md) | System monitoring and health checks |
| [Daydream System](operations/daydream-system.md) | Autonomous maintenance process |

### Quality & Testing

| Document | Description |
|----------|-------------|
| [Quality Standards](tools/quality-standards.md) | Tool quality standards and patterns |
| [Testing Strategy](testing/testing-strategy.md) | Real integration testing approach |
| [Tool Architecture](tools/tool-architecture.md) | Tool design patterns |
| [Tools Reference](tools-reference.md) | Complete tool documentation |

### Components

| Document | Description |
|----------|-------------|
| [Message Processing](components/message-processing.md) | Telegram message handling pipeline |
| [Resource Monitoring](components/resource-monitoring.md) | System resource management |

## Architecture Summary

```
User (Telegram)
    |
    v
Valor Agent - Conversational AI with full context
    |
    v
Claude Code - Orchestrates tools and subagents
    |
    v
Tool Ecosystem
    |-- MCP Servers (Stripe, Sentry, GitHub, etc.)
    |-- Development Tools (search, code, test)
    +-- Social Tools (Telegram integration)
```

## Key Principles

1. **Pure Agency**: System handles complexity without exposing intermediate steps
2. **No Legacy Code**: Complete elimination of deprecated patterns
3. **Context Management**: Maintain relevant context across interactions
4. **Tool Selection**: Dynamic filtering to avoid context pollution
5. **Real Integration Testing**: No mocks, use actual services

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

## Claude Code Configuration

The `.claude/` directory contains:
- `agents/` - Subagent definitions that Claude Code can invoke
- `commands/` - Slash commands for user-invokable skills
- `settings.local.json` - Local configuration

See [CLAUDE.md](/CLAUDE.md) for complete development guidelines.
