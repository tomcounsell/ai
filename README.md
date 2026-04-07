# Valor

An autonomous AI coworker. Not an assistant, not a chatbot — a colleague that owns its own machine and does real work.

## What Is This?

Valor wraps agent harnesses (like Claude Code) and bridges them to the comms channels humans actually use (Telegram, Email, LinkedIn, and more). The supervisor assigns work and provides direction. Valor executes autonomously on its own Mac, reaching out when necessary.

## How It Works

Three layers:

- **Comms layer** — bridges to the channels where work actually happens: Telegram, Email, LinkedIn, etc. Messages come in, replies go out, session context survives across conversations.
- **Harness layer** — wraps agent harnesses like Claude Code, giving Valor tools, memory, skills, and a full SDLC workflow.
- **Execution layer** — a standalone worker service runs sessions against the configured harness. Sessions come in three flavors: **PM** (orchestrates work), **Dev** (writes code), and **Teammate** (conversational).

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│   Comms  (Telegram · Email · LinkedIn · …)                   │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│   Bridge   (I/O only — enqueues sessions, routes output)     │
└──────────────────────────┬───────────────────────────────────┘
                           │    Redis queue
                           ▼
┌──────────────────────────────────────────────────────────────┐
│   Worker   (sole session execution engine)                   │
│     ├── ChatSession  (PM — orchestrates the SDLC pipeline)   │
│     │     └── DevSession  (Dev — writes, tests, ships code)  │
│     └── Teammate    (conversational)                         │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│   Agent Harness  (Claude Code / Agent SDK) + MCP tools       │
└──────────────────────────────────────────────────────────────┘
```

See [`docs/features/bridge-worker-architecture.md`](docs/features/bridge-worker-architecture.md) for the full design.

## The SDLC Pipeline

Valor ships real features through a structured pipeline, each stage a skill the agent invokes:

```
Plan → Critique → Build → Test → Patch → Review → Docs → Merge
```

ChatSession (PM persona) steers the pipeline and delegates coding work to DevSession (Dev persona). See [`.claude/skills/sdlc/SKILL.md`](.claude/skills/sdlc/SKILL.md) for the ground truth on stage definitions.

## Subsystems

| Subsystem | Purpose |
|-----------|---------|
| **Subconscious memory** | Long-term memory with bloom-filter recall, intentional saves, and post-session extraction |
| **Reflections** | Daily autonomous maintenance pipeline (log review, audits, cleanup, reporting) |
| **Autoexperiment** | Nightly prompt optimization for observer/summarizer targets |
| **Self-healing** | Watchdog service with crash tracking and automatic recovery |
| **Session steering** | Inject guidance into running sessions from any process |
| **Worktree isolation** | Per-feature git worktrees for parallel work without collisions |
| **Dashboard** | Web UI showing sessions, health, reflections, and machine state |

## Quick Start

```bash
# 1. Install dependencies
pip install -e .

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys and comms credentials

# 3. Start the bridge and worker
./scripts/start_bridge.sh
./scripts/valor-service.sh worker-start
```

## Service Management

```bash
./scripts/valor-service.sh status          # Bridge status
./scripts/valor-service.sh restart         # Restart bridge after code changes
./scripts/valor-service.sh worker-status   # Worker status
./scripts/valor-service.sh worker-restart  # Restart worker
tail -f logs/bridge.log                    # Stream logs
```

## Repository Layout

```
ai/
├── agent/               # Session queue, SDK client, output routing
├── bridge/              # Comms bridges (Telegram, etc.) and nudge loop
├── worker/              # Standalone worker service (python -m worker)
├── tools/               # Local Python tools
├── ui/                  # Dashboard web UI
├── monitoring/          # Watchdog, crash tracker, health checks
├── .claude/
│   ├── skills/          # SDLC and utility skills
│   ├── commands/        # Slash commands
│   └── agents/          # Subagent definitions
├── config/              # SOUL.md persona, projects.json
├── scripts/             # Service management, setup, deployment
├── docs/features/       # Feature-level documentation
└── tests/               # Unit, integration, e2e
```

## Development

```bash
pytest tests/unit/ -n auto     # Fast unit tests in parallel
pytest tests/                  # Full suite
python -m ruff format .        # Format
```

## See Also

| Resource | Purpose |
|----------|---------|
| [CLAUDE.md](CLAUDE.md) | Development principles and working guide |
| [docs/features/README.md](docs/features/README.md) | Feature index — how things work |
| [config/SOUL.md](config/SOUL.md) | Valor persona and philosophy |
| [tests/README.md](tests/README.md) | Test suite index and contribution guide |
