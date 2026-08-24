# AI System Documentation

Documentation for the Valor AI System — a unified conversational development environment
that erases the boundary between natural conversation and code execution. Users interact
with the system through chat (Telegram, email, or a local Claude Code session), and the
system executes real work in real repositories.

This file is the map. The territory is [`README.md`](../README.md) at the repo root
(install, run, service management) and the [feature index](features/README.md) (how any
individual thing works).

## Architecture in One Screen

The system runs as separate long-lived processes. The **bridge** is I/O only — it receives
messages, enqueues `AgentSession` records to Redis, runs the nudge loop, and registers
output callbacks. It has no SDLC awareness and never executes an agent turn. The
**worker** is the sole session-execution engine, spawning one `claude -p` subprocess per
turn via the headless session runner.

```
Telegram / Email / local Claude Code
            |
            v
   Bridge (I/O only)  ──enqueue AgentSession──>  Redis queue
   bridge/telegram_bridge.py                          |
            ^                                         v
            |                              Worker (python -m worker)
            └───────outbox / callbacks─────  the sole execution engine
                                                       |
                                                       v
                                     Headless session runner: one `claude -p`
                                     subprocess per turn (subscription auth)
                                                       |
                                                       v
                                     Tools, MCP servers, `gh`, skills, subagents
```

Read these in order:

| Document | What it covers |
|----------|----------------|
| [Bridge/Worker Architecture](features/bridge-worker-architecture.md) | The process split above, and why the bridge holds no execution logic |
| [Headless Session Runner](features/headless-session-runner.md) | How a turn actually runs: the `claude -p` subprocess, env handling, teardown |
| [Session Lifecycle](features/session-lifecycle.md) | The 14-state `AgentSession` lifecycle, from enqueue to completion |
| [Feature Index](features/README.md) | Every implemented feature, with a doc for each |

## Quick Start

Both the bridge and the worker must be running. Starting only one leaves messages queued
and never executed.

```bash
# Start the Telegram bridge (receives messages, enqueues sessions)
./scripts/start_bridge.sh

# Start the standalone worker (executes every agent session)
./scripts/valor-service.sh worker-start

# Status and restart — `restart` cycles bridge, watchdog, and worker together
./scripts/valor-service.sh status
./scripts/valor-service.sh worker-status
./scripts/valor-service.sh restart

# Logs
tail -f logs/bridge.log
```

Full install, optional services (email bridge, dashboard UI), and service management live
in the root [`README.md`](../README.md).

## Doc Placement

Each doc topic has exactly one canonical file. Place it by kind:

| Directory | Holds |
|-----------|-------|
| `docs/features/` | One doc per implemented feature — the default home for anything describing how the system works. Indexed by [`features/README.md`](features/README.md). |
| `docs/guides/` | Evergreen how-to and reference material (setup, CLI references, standards). |
| `docs/conventions/` | Cross-project conventions this repo expects other repos to adopt. |
| `docs/sdlc/` | Per-stage repo-specific addenda, read at runtime by the global SDLC skills. |
| `docs/runbooks/` | Step-by-step operational procedures for a recurring task. |
| `docs/infra/` | Infrastructure and third-party integration setup notes. |
| `docs/postmortems/` | Dated incident write-ups: what broke, why, what changed. |
| `docs/audits/` | Dated point-in-time audits of a subsystem. |
| `docs/research/` | Dated investigations, evaluations, and deep dives, each stamped with its date. |
| `docs/baselines/` | Captured measurement snapshots used as a comparison point. |
| `docs/plans/` | Feature plan documents produced by `/do-plan`. |
| `docs/diagrams/`, `docs/assets/` | Images and diagram sources referenced by the docs above. |

Two rules:

- **Feature docs go in `features/` and get an index row.** A feature doc that is not in
  the index is invisible; an index row pointing at a missing file is a dead link.
- **Nothing lives in two places.** When a doc stops describing the current system, move it
  to [`features/archived/`](features/archived/) and drop its index row rather than leaving
  a stale copy linked from an active page.

## Documentation Index

### Operations

| Document | Description |
|----------|-------------|
| [Deployment](features/deployment.md) | Multi-instance deployment configuration |
| [Reflections System](features/reflections.md) | Autonomous maintenance process |

### Quality & Testing

| Document | Description |
|----------|-------------|
| [Quality Standards](guides/quality-standards.md) | Tool quality standards and patterns |
| [Tools Reference](tools-reference.md) | Complete tool documentation |

### Postmortems

| Document | Description |
|----------|-------------|
| [2026-04-24: PM SDLC Bypass](postmortems/2026-04-24-pm-sdlc-bypass.md) | PM agent implemented code directly instead of routing through SDLC |

## Key Principles

1. **Pure Agency** — the system handles complexity without exposing intermediate steps.
2. **No Legacy Code** — deprecated patterns are deleted, not deprecated in place.
3. **Context Management** — relevant context is carried across interactions explicitly.
4. **Tool Selection** — dynamic filtering, because loading every tool pollutes context.
5. **Real Integration Testing** — no mocks, use actual services.

## Environment Setup

Secrets live in `~/Desktop/Valor/.env`; the repo `.env` is a symlink to it. See
[`guides/setup.md`](guides/setup.md) for the full walkthrough and `.env.example` for the
complete list.

**Agent auth is subscription-first.** Headless turns run on the Claude subscription (OAuth
via `claude` CLI login), not on an API key. The session runner strips `ANTHROPIC_API_KEY`
and the endpoint overrides from every spawned CLI's environment
(`agent/session_runner/harness/claude.py`, `agent/session_runner/role_driver.py`) so a
stray key can never silently move agent turns onto metered billing.

```bash
# Required — Telegram user account (from my.telegram.org)
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890

# Auxiliary — direct Anthropic API calls made OUTSIDE the agent harness
ANTHROPIC_API_KEY=sk-ant-...

# Other service keys
OPENAI_API_KEY=sk-...
PERPLEXITY_API_KEY=pplx-...
```

`ANTHROPIC_API_KEY` is still required, but for direct API calls that are not agent turns:

- `bridge/media.py` — image vision on inbound photos
- `reflections/docs_auditor.py` — the documentation audit reflection
- `reflections/utilities.py` — the shared LLM helper for reflections
- `reflections/pm_briefings/builder.py` — briefing generation

Without it those features degrade or skip; agent sessions are unaffected.

## Claude Code Configuration

The `.claude/` directory contains:

- `agents/` — subagent definitions Claude Code can invoke
- `skills-global/` — skills hardlinked to `~/.claude/skills/` on every machine by `/update`
- `skills/` — project-only skills coupled to this repo's infrastructure
- `commands/` — slash commands
- `hooks/` — validators and lifecycle hooks, registered from `hooks/manifest.toml`
- `settings.local.json` — local configuration

See [`CLAUDE.md`](../CLAUDE.md) for complete development guidelines.
