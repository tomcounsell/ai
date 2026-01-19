# Claude Code Configuration

This directory contains Claude Code configuration for the Valor AI System.

## Directory Structure

```
.claude/
├── README.md          # This file
├── settings.local.json # Local settings (not committed)
├── agents/            # Subagent definitions
│   ├── stripe.md      # Payment operations
│   ├── sentry.md      # Error monitoring
│   ├── render.md      # Infrastructure
│   ├── github.md      # Code collaboration
│   ├── notion.md      # Documentation
│   ├── linear.md      # Project management
│   └── support/       # Support specialists
└── commands/          # Slash commands
    ├── prime.md       # Development environment
    └── audit-next-tool.md
```

## Agent Definitions

The agents in `.claude/agents/` are Claude Code native subagents that Claude can invoke automatically based on conversation context. Each agent:

- Has a focused tool set for its domain
- Uses an appropriate model (haiku for simple, sonnet for complex)
- Operates with an isolated context window

## Commands

Commands in `.claude/commands/` are user-invokable skills triggered with `/command-name`.

## Version Tracking

| Component | Version | Updated |
|-----------|---------|---------|
| Claude Code | Current | 2026-01-19 |
| Agent definitions | 1.0 | 2025-11-19 |
| Commands | 1.0 | 2025-06-10 |

## Notes

- Claude Code orchestrates subagents automatically
- Valor provides tools, workflows, and skills
- See `docs/CONSOLIDATED_DOCUMENTATION.md` for full architecture
