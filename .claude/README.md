# Claude Code Configuration

This directory contains Claude Code configuration for Valor - agents, skills, hooks, and validators that work across ALL projects.

## Global Toolbox Architecture

**This repo is Valor's toolbox.** The contents of this `.claude/` directory are symlinked to `~/.claude/` so they're available when working in any repository.

```bash
# Symlinks (already configured)
~/.claude/agents   -> ~/src/ai/.claude/agents
~/.claude/commands -> ~/src/ai/.claude/commands
~/.claude/skills   -> ~/src/ai/.claude/skills
~/.claude/hooks    -> ~/src/ai/.claude/hooks
```

**Cross-repo usage:**
- Working in `~/src/popoto/`? These agents and skills are available.
- Working in `~/src/django-project-template/`? Same toolbox.
- Project-specific agents can be added to each repo's local `.claude/agents/`.

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
├── agents/                # Subagent definitions (flat structure)
│   ├── builder.md         # General implementation agent
│   ├── validator.md       # Read-only verification agent
│   ├── stripe.md          # Payment operations
│   ├── github.md          # Code collaboration
│   └── ...                # All agents at root level
├── commands/              # Skills (slash commands)
│   ├── prime.md           # Codebase primer
│   ├── build.md           # Plan execution with agent teams
│   └── ...
├── skills/                # Complex multi-step skills
│   └── make-plan/         # Shape Up planning methodology
└── hooks/                 # Validation hooks
    └── validators/        # File and content validators
```

## Skills

Skills in `.claude/commands/` define reusable workflows:

| Skill | Purpose |
|-------|---------|
| `/prime` | **Start here** - Codebase onboarding for new sessions |
| `/pthread` | Scale compute via parallel agent execution |
| `/sdlc` | Autonomous Plan→Build→Test→Ship workflow |
| `/audit-next-tool` | Quality audits for tools |

### /prime - Codebase Onboarding (NEW SESSIONS START HERE)

Run `/prime` at the start of any session to understand:
- Current architecture (Clawdbot + Python bridge)
- Where things live (this repo vs ~/clawd/skills/)
- How to add new features
- Key files to read

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

Agents in `.claude/agents/` are specialized subagents (flat structure, no subdirectories):

**Builder agents** - Create and modify code:
- `builder` - General implementation
- `tool-developer` - High-quality tool creation
- `database-architect` - Schema design, migrations
- `agent-architect` - Agent systems, context management
- `designer` - UI/UX implementation

**Validator agents** - Verify without modifying:
- `validator` - Read-only verification (no Write/Edit tools)
- `code-reviewer` - Code review, security checks
- `quality-auditor` - Standards compliance

**Domain agents** - External service operations:
- `stripe`, `sentry`, `render`, `github`, `notion`, `linear`

**Support agents** - Specialized expertise:
- `api-integration-specialist`, `security-reviewer`, `performance-optimizer`, etc.

### Builder + Validator Pairing

The standard workflow pairs builders with validators:

1. **Builder** implements a component
2. **Validator** verifies it (read-only, cannot fix issues)
3. If validation fails, builder receives feedback and iterates
4. Loop until validation passes

This pairing is a **workflow practice**, not enforced by directory structure. The `/build` skill orchestrates this pattern automatically.

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
