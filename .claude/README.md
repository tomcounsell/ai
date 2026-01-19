# Claude Code Configuration

This directory contains Claude Code configuration for the Valor AI System.

## Core Philosophy

**Master the agent, master engineering.**

Everything reduces to the Core Four:
1. **Context** - What information the agent has access to
2. **Model** - The intelligence powering the agent
3. **Prompt** - The instructions driving behavior
4. **Tools** - The capabilities the agent can invoke

The system makes autonomous decisions about parallelization, validation loops, and orchestration. Valor is the human interface; internally, the system is self-sufficient.

## Directory Structure

```
.claude/
├── README.md              # This file
├── settings.local.json    # Local settings (not committed)
├── agents/                # Subagent definitions
│   ├── stripe.md          # Payment operations
│   ├── sentry.md          # Error monitoring
│   ├── render.md          # Infrastructure
│   ├── github.md          # Code collaboration
│   ├── notion.md          # Documentation
│   ├── linear.md          # Project management
│   └── support/           # Support specialists
└── commands/              # Skills (slash commands)
    ├── prime.md           # Codebase primer
    ├── pthread.md         # Parallel thread execution
    ├── sdlc.md            # AI Developer Workflow
    └── audit-next-tool.md # Tool quality audits
```

## Skills

Skills in `.claude/commands/` define reusable workflows:

| Skill | Purpose |
|-------|---------|
| `/prime` | Load codebase context and architecture |
| `/pthread` | Scale compute via parallel agent execution |
| `/sdlc` | Autonomous Plan→Build→Test→Ship workflow |
| `/audit-next-tool` | Quality audits for tools |

### /pthread - Parallel Threads

Spawn multiple agents for independent work. The system auto-parallelizes when:
- Multiple independent searches needed
- Exploring different approaches (fusion pattern)
- Reviewing separate modules

### /sdlc - AI Developer Workflow

Complete development lifecycle with validation loops:
```
Plan → Build → Test → Review → Ship
         ↑           │
         └───────────┘ (loop on failure)
```

The system does not stop until all quality gates pass.

## Agent Definitions

Agents in `.claude/agents/` are specialized subagents:

- **Domain agents**: stripe, sentry, render, github, notion, linear
- **Support agents**: debugging, testing, documentation, security, etc.

Each agent:
- Has focused context for its domain
- Uses appropriate model (haiku for simple, opus for complex)
- Can be invoked automatically based on task

## Thread-Based Engineering

The system thinks in threads:

| Thread Type | Description |
|-------------|-------------|
| **Base** | Single prompt → work → review |
| **P-Thread** | Multiple agents in parallel |
| **C-Thread** | Chained phases with checkpoints |
| **F-Thread** | Same prompt to multiple agents, aggregate best |
| **B-Thread** | Agents orchestrating other agents |
| **L-Thread** | Extended autonomous work |

## Validation Loops (Ralph Wiggum Pattern)

Agents verify their own work:
1. Agent attempts completion
2. Validation runs (tests, linting, checks)
3. If fail → continue with feedback
4. If pass → complete

This creates closed-loop systems that self-correct.

## Version Tracking

| Component | Version | Updated |
|-----------|---------|---------|
| Claude Code | Current | 2026-01-19 |
| Skills | 2.0 | 2026-01-19 |
| Agent definitions | 1.0 | 2025-11-19 |

## Key Insights

*"Scale your compute to scale your impact."*

*"First you want better agents, then you want more agents."*

*"Build the system that builds the system."*

See `config/SOUL.md` for Valor's full philosophy and `docs/CONSOLIDATED_DOCUMENTATION.md` for architecture.
